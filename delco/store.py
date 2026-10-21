"""Transições de estado com o ORM do Django.

Regra de ouro: toda mudança de estado é condicional. Ou a linha é travada com
``select_for_update()`` (com ``skip_locked`` quando outro processo pode estar
cuidando dela) e conferida antes de mudar, ou o ``UPDATE`` leva o estado
esperado no filtro e devolve quantas linhas mudaram (compare-and-set). Se dois
processos tentarem a mesma transição, só um vence; o outro vê 0 linhas e segue
em frente. É isso que torna mensagens repetidas, quedas de worker e vários
agendadores ao mesmo tempo inofensivos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import (
    DateTimeField,
    DurationField,
    Exists,
    ExpressionWrapper,
    F,
    Max,
    OuterRef,
    Q,
    Value,
)
from django.db.models.functions import Coalesce, Now

from .loader import LoadResult
from .models import (
    ApiToken,
    AuditLog,
    TaskAttempt,
    TaskInstance,
    TaskLog,
    Worker,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from .schedule import next_fire, retry_delay, utcnow
from .sdk import descendants

log = logging.getLogger("delco.store")

OPEN_TASK = ("pending", "queued", "running", "retry_wait")
TERMINAL_TASK = ("succeeded", "failed", "skipped", "cancelled")
FAILED_LIKE = ("failed", "skipped", "cancelled")
SKIPPED_ERROR = "Pulada: dependência não terminou com sucesso."


class StoreError(Exception):
    """Operação impossível no estado atual (vira 409 na API)."""


def db_now_plus(seconds: float) -> ExpressionWrapper:
    """``now() + intervalo`` calculado no relógio do MySQL."""
    return ExpressionWrapper(
        Now() + Value(timedelta(seconds=seconds), output_field=DurationField()), output_field=DateTimeField()
    )


def db_ago(seconds: float) -> ExpressionWrapper:
    return db_now_plus(-seconds)


# ===================================================================== definições
def sync_definitions(result: LoadResult, now: datetime | None = None) -> dict[str, Any]:
    """Grava cada workflow carregado e cria uma nova versão quando o grafo muda."""
    now = now or utcnow()
    new_versions: list[str] = []
    for wf in result.workflows.values():
        d = wf.definition()
        with transaction.atomic():
            row = Workflow.objects.select_for_update().filter(name=wf.name).first()
            previous_schedule = row.schedule if row else None
            if row is None:
                created, _ = Workflow.objects.get_or_create(name=wf.name)
                row = Workflow.objects.select_for_update().get(pk=created.pk)
                previous_schedule = None
            version = WorkflowVersion.objects.filter(workflow=row, fingerprint=d["fingerprint"]).first()
            if version is None:
                last = WorkflowVersion.objects.filter(workflow=row).aggregate(n=Max("version"))["n"] or 0
                version = WorkflowVersion.objects.create(
                    workflow=row, version=last + 1, fingerprint=d["fingerprint"], graph=d["graph"]
                )
                new_versions.append(f"{wf.name} v{version.version}")
            next_run = row.next_run_at
            if not d["schedule"]:
                next_run = None
            elif d["schedule"] != previous_schedule or d["timezone"] != row.timezone or next_run is None:
                next_run = next_fire(d["schedule"], d["timezone"], now)
            Workflow.objects.filter(pk=row.pk).update(
                description=d["description"],
                schedule=d["schedule"],
                timezone=d["timezone"],
                max_active_runs=d["max_active_runs"],
                default_params=d["params"],
                tags=d["tags"],
                source_file=d["source_file"],
                current_version=version,
                next_run_at=next_run,
                is_active=True,
                updated_at=Now(),
            )
    loaded = list(result.workflows)
    broken_files = list(result.errors)
    stale = Workflow.objects.filter(is_active=True).exclude(name__in=loaded)
    if broken_files:  # arquivo com erro: mantém os workflows dele até ser corrigido
        stale = stale.exclude(source_file__in=broken_files)
    deactivated = sorted(stale.values_list("name", flat=True))
    if deactivated:
        Workflow.objects.filter(name__in=deactivated, is_active=True).update(
            is_active=False, updated_at=Now()
        )
    return {
        "workflows": loaded,
        "new_versions": new_versions,
        "deactivated": deactivated,
        "errors": result.errors,
    }


# ====================================================================== execuções
def create_run(
    workflow: Workflow,
    *,
    trigger: str,
    params: dict[str, Any] | None = None,
    triggered_by: int | None = None,
    logical_date: datetime | None = None,
    note: str | None = None,
) -> int | None:
    """Cria a execução e materializa todas as tarefas como ``pending``.

    ``workflow`` precisa vir com ``current_version`` carregada. Devolve ``None``
    se já existir execução agendada para a mesma data lógica.
    """
    merged = {**(workflow.default_params or {}), **(params or {})}
    try:
        with transaction.atomic():  # savepoint: a chave única por data lógica pode recusar
            run = WorkflowRun.objects.create(
                workflow_id=workflow.id,
                version_id=workflow.current_version_id,
                trigger=trigger,
                triggered_by_id=triggered_by,
                logical_date=logical_date,
                params=merged,
                note=note,
            )
    except IntegrityError as exc:
        if "workflow_runs_schedule_key" in str(exc):
            return None
        raise
    TaskInstance.objects.bulk_create(
        [
            TaskInstance(
                run=run,
                task_name=t["name"],
                upstream=t["upstream"],
                trigger_rule=t["trigger_rule"],
                queue=t["queue"],
                retries=t["retries"],
                max_attempts=t["retries"] + 1,
                retry_delay=t["retry_delay"],
                retry_backoff=t["retry_backoff"],
                max_retry_delay=t["max_retry_delay"],
                timeout_seconds=t["timeout"],
            )
            for t in workflow.current_version.graph["tasks"]
        ]
    )
    return run.id


def trigger_run(
    name: str,
    *,
    params: dict[str, Any] | None = None,
    trigger: str = "manual",
    user_id: int | None = None,
    note: str | None = None,
) -> int:
    with transaction.atomic():
        wf = (
            Workflow.objects.select_related("current_version")
            .filter(name=name, current_version__isnull=False)
            .first()
        )
        if wf is None:
            raise LookupError(name)
        if not wf.is_active:
            raise StoreError(f"O workflow {name} não está mais na pasta de workflows.")
        run_id = create_run(wf, trigger=trigger, params=params, triggered_by=user_id, note=note)
        audit(user_id, "run.trigger", f"run:{run_id}", {"workflow": name, "params": params or {}})
    assert run_id is not None
    return run_id


def schedule_due(now: datetime | None = None) -> list[int]:
    """Cria as execuções agendadas cujo horário chegou (sem recuperar horários perdidos)."""
    now = now or utcnow()
    created: list[int] = []
    with transaction.atomic():
        due = list(
            Workflow.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("current_version")
            .filter(
                is_active=True,
                is_paused=False,
                schedule__isnull=False,
                next_run_at__lte=now,
                current_version__isnull=False,
            )
            .order_by("next_run_at")[:50]
        )
        for wf in due:
            run_id = create_run(wf, trigger="schedule", logical_date=wf.next_run_at)
            if run_id:
                created.append(run_id)
            Workflow.objects.filter(pk=wf.pk).update(
                next_run_at=next_fire(wf.schedule, wf.timezone, max(now, wf.next_run_at)), updated_at=Now()
            )
    return created


def promote_runs() -> list[int]:
    """Coloca execuções da fila para rodar, respeitando ``max_active_runs`` de cada workflow."""
    started: list[int] = []
    with transaction.atomic():
        workflows = list(
            Workflow.objects.select_for_update(skip_locked=True)
            .filter(Exists(WorkflowRun.objects.filter(workflow=OuterRef("pk"), status="queued")))
            .order_by("id")
            .only("id", "max_active_runs")
        )
        for wf in workflows:
            running = WorkflowRun.objects.filter(workflow_id=wf.id, status="running").count()
            free = wf.max_active_runs - running
            if free <= 0:
                continue
            ids = list(
                WorkflowRun.objects.select_for_update()
                .filter(workflow_id=wf.id, status="queued")
                .order_by("id")
                .values_list("id", flat=True)[:free]
            )
            WorkflowRun.objects.filter(id__in=ids, status="queued").update(
                status="running", started_at=Coalesce("started_at", Now()), updated_at=Now()
            )
            started.extend(ids)
    return started


def decide(rule: str, upstream: list[str]) -> str:
    """Decide se uma tarefa pendente roda, espera ou é pulada: 'run' | 'wait' | 'skip'."""
    if any(s not in TERMINAL_TASK for s in upstream):
        return "wait"
    if rule == "all_done":
        return "run"
    if rule == "one_failed":
        return "run" if any(s == "failed" for s in upstream) else "skip"
    return "run" if all(s == "succeeded" for s in upstream) else "skip"


def run_outcome(statuses: list[str]) -> str:
    if "failed" in statuses:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    return "succeeded"


@dataclass
class DispatchResult:
    ready: list[tuple[int, str, int, str]]  # (task_id, queue, run_id, task_name)
    skipped: list[tuple[int, int, str]]
    finished: list[tuple[int, str]]


def dispatch(now: datetime | None = None) -> DispatchResult:
    """Avalia as dependências e marca como ``queued`` as tarefas prontas.

    A publicação no Redis acontece depois do COMMIT (veja ``Scheduler.enqueue``).
    """
    now = now or utcnow()
    result = DispatchResult([], [], [])
    has_ready = TaskInstance.objects.filter(run=OuterRef("pk")).filter(
        Q(status="pending") | Q(status="retry_wait", ready_at__lte=now)
    )
    has_open = TaskInstance.objects.filter(run=OuterRef("pk"), status__in=OPEN_TASK)
    candidates = list(
        WorkflowRun.objects.filter(status="running")
        .filter(Q(Exists(has_ready)) | ~Q(Exists(has_open)))
        .order_by("id")
        .values_list("id", flat=True)[:500]
    )
    for run_id in candidates:
        with transaction.atomic():
            locked = (
                WorkflowRun.objects.select_for_update(skip_locked=True)
                .filter(id=run_id, status="running")
                .values_list("id", flat=True)
                .first()
            )
            if locked is None:
                continue  # outro agendador está cuidando desta execução
            tasks = list(
                TaskInstance.objects.filter(run_id=run_id)
                .order_by("id")
                .values("id", "task_name", "status", "upstream", "trigger_rule", "queue", "ready_at")
            )
            status = {t["task_name"]: t["status"] for t in tasks}
            ready_ids: list[int] = []
            skip_ids: list[int] = []
            changed = True
            while changed:  # pular uma tarefa pode liberar (ou pular) as seguintes
                changed = False
                for t in tasks:
                    name = t["task_name"]
                    if status[name] == "retry_wait" and t["ready_at"] is not None and t["ready_at"] <= now:
                        status[name] = "queued"
                        ready_ids.append(t["id"])
                        result.ready.append((t["id"], t["queue"], run_id, name))
                    elif status[name] == "pending":
                        verdict = decide(t["trigger_rule"], [status[u] for u in t["upstream"]])
                        if verdict == "run":
                            status[name] = "queued"
                            ready_ids.append(t["id"])
                            result.ready.append((t["id"], t["queue"], run_id, name))
                        elif verdict == "skip":
                            status[name] = "skipped"
                            skip_ids.append(t["id"])
                            result.skipped.append((t["id"], run_id, name))
                            changed = True
            if ready_ids:
                TaskInstance.objects.filter(id__in=ready_ids, status__in=("pending", "retry_wait")).update(
                    status="queued", queued_at=Now(), ready_at=None, queue_message_id=None, updated_at=Now()
                )
            if skip_ids:
                TaskInstance.objects.filter(id__in=skip_ids, status="pending").update(
                    status="skipped", finished_at=Now(), error=SKIPPED_ERROR, updated_at=Now()
                )
            if all(s in TERMINAL_TASK for s in status.values()):
                outcome = run_outcome(list(status.values()))
                WorkflowRun.objects.filter(id=run_id, status="running").update(
                    status=outcome, finished_at=Now(), updated_at=Now()
                )
                result.finished.append((run_id, outcome))
    return result


def set_message_id(task_id: int, message_id: str) -> None:
    TaskInstance.objects.filter(id=task_id, status="queued").update(queue_message_id=message_id)


# ================================================================== recuperação
def recover_expired_leases() -> list[dict[str, Any]]:
    """Tarefas 'running' cujo worker parou de mandar heartbeat (queda, OOM, rede)."""
    recovered: list[dict[str, Any]] = []
    with transaction.atomic():
        rows = list(
            TaskInstance.objects.select_for_update(skip_locked=True)
            .filter(status="running", lease_expires_at__lt=Now())
            .order_by("id")[:200]
        )
        now = utcnow()
        for t in rows:
            reason = " ".join(f"O worker {t.worker_id or ''} parou de responder durante a tentativa.".split())
            attempt = (
                TaskAttempt.objects.filter(task_instance_id=t.id, number=t.attempts, status="running")
                .values("id", "started_at")
                .first()
            )
            if attempt:
                TaskAttempt.objects.filter(id=attempt["id"], status="running").update(
                    status="lost",
                    finished_at=now,
                    error=reason,
                    duration_ms=int((now - attempt["started_at"]).total_seconds() * 1000),
                )
            new_status, ready_at, finished = _after_failure(t, now=now)
            TaskInstance.objects.filter(id=t.id).update(
                status=new_status,
                ready_at=ready_at,
                finished_at=finished,
                error=reason,
                worker=None,
                lease_expires_at=None,
                updated_at=Now(),
            )
            recovered.append({"id": t.id, "run_id": t.run_id, "task_name": t.task_name, "status": new_status})
    return recovered


def _after_failure(t: TaskInstance, *, now: datetime) -> tuple[str, datetime | None, datetime | None]:
    if t.cancel_requested:
        return "cancelled", None, now
    if t.attempts < t.max_attempts:
        delay = retry_delay(t.attempts, t.retry_delay, t.retry_backoff, t.max_retry_delay)
        return "retry_wait", now + timedelta(seconds=delay), None
    return "failed", None, now


def stale_queued(older_than_seconds: float) -> list[dict[str, Any]]:
    return list(
        TaskInstance.objects.filter(status="queued", run__status="running")
        .filter(
            Q(queued_at__lt=db_ago(older_than_seconds))
            | Q(queue_message_id__isnull=True, queued_at__lt=db_ago(10))
        )
        .order_by("id")
        .values("id", "queue", "queue_message_id")[:500]
    )


def touch_queued(task_id: int, message_id: str) -> None:
    TaskInstance.objects.filter(id=task_id, status="queued").update(
        queue_message_id=message_id, queued_at=Now()
    )


def mark_offline_workers(silent_seconds: float) -> list[str]:
    ids = list(
        Worker.objects.exclude(status="offline")
        .filter(last_heartbeat_at__lt=db_ago(silent_seconds))
        .values_list("id", flat=True)
    )
    if ids:
        Worker.objects.filter(id__in=ids).exclude(status="offline").update(
            status="offline", stopped_at=Coalesce("stopped_at", Now())
        )
    return ids


def housekeeping(retention_days: int) -> dict[str, int]:
    """Apaga o que passou do prazo. As filhas somem pelo ON DELETE CASCADE do próprio banco."""
    day = 86_400
    with transaction.atomic():
        runs, _ = WorkflowRun.objects.filter(
            status__in=("succeeded", "failed", "cancelled"), finished_at__lt=db_ago(retention_days * day)
        ).delete()
        tokens, _ = ApiToken.objects.filter(
            Q(expires_at__lt=db_ago(day)) | Q(revoked_at__lt=db_ago(30 * day))
        ).delete()
        workers, _ = Worker.objects.filter(status="offline", last_heartbeat_at__lt=db_ago(7 * day)).delete()
        audit_rows, _ = AuditLog.objects.filter(created_at__lt=db_ago(180 * day)).delete()
    return {"runs": runs, "tokens": tokens, "workers": workers, "audit": audit_rows}


# ======================================================================= worker
def register_worker(
    worker_id: str, hostname: str, pid: int, queues: list[str], concurrency: int, version: str
) -> None:
    now = utcnow()
    Worker.objects.bulk_create(
        [
            Worker(
                id=worker_id,
                hostname=hostname,
                pid=pid,
                queues=queues,
                concurrency=concurrency,
                version=version,
                status="online",
                started_at=now,
                last_heartbeat_at=now,
                stopped_at=None,
            )
        ],
        update_conflicts=True,  # INSERT ... ON DUPLICATE KEY UPDATE pela chave primária
        update_fields=[
            "hostname",
            "pid",
            "queues",
            "concurrency",
            "version",
            "status",
            "started_at",
            "last_heartbeat_at",
            "stopped_at",
        ],
    )


def worker_heartbeat(worker_id: str, status: str = "online") -> None:
    Worker.objects.filter(id=worker_id).update(last_heartbeat_at=Now(), status=status)


def worker_stopped(worker_id: str) -> None:
    Worker.objects.filter(id=worker_id).update(status="offline", stopped_at=Now(), last_heartbeat_at=Now())


@dataclass
class Claim:
    task_id: int
    run_id: int
    task_name: str
    attempt: int
    attempt_id: int
    max_attempts: int
    timeout: float | None
    workflow: str
    source_file: str | None
    params: dict[str, Any]
    logical_date: datetime | None
    upstream: dict[str, Any]


def claim(task_id: int, worker_id: str, lease_seconds: float) -> Claim | None:
    """Assume a tarefa se ela ainda estiver na fila. Mensagem repetida -> None."""
    with transaction.atomic():
        # Trava a linha (leitura atual do InnoDB); se outro worker assumiu antes, o filtro já não bate e não volta nada.
        t = (
            TaskInstance.objects.select_for_update(of=("self",))
            .filter(id=task_id, status="queued", run__status="running")
            .first()
        )
        if t is None:
            return None
        attempt_no = t.attempts + 1
        TaskInstance.objects.filter(id=t.id).update(
            status="running",
            attempts=F("attempts") + 1,
            worker_id=worker_id,
            lease_expires_at=db_now_plus(lease_seconds),
            started_at=Coalesce("started_at", Now()),
            progress=None,
            error=None,
            updated_at=Now(),
        )
        attempt = TaskAttempt.objects.create(task_instance_id=t.id, number=attempt_no, worker_id=worker_id)
        run = WorkflowRun.objects.select_related("workflow").get(id=t.run_id)
        upstream = dict(
            TaskInstance.objects.filter(run_id=t.run_id, task_name__in=t.upstream).values_list(
                "task_name", "output"
            )
        )
    return Claim(
        task_id=t.id,
        run_id=t.run_id,
        task_name=t.task_name,
        attempt=attempt_no,
        attempt_id=attempt.id,
        max_attempts=t.max_attempts,
        timeout=t.timeout_seconds,
        workflow=run.workflow.name,
        source_file=run.workflow.source_file,
        params=run.params or {},
        logical_date=run.logical_date,
        upstream=upstream,
    )


def task_heartbeat(
    task_id: int, attempt: int, lease_seconds: float, progress: int | None = None
) -> tuple[bool, bool]:
    """Renova a posse da tarefa. Devolve (ainda_e_minha, cancelamento_pedido)."""
    changes: dict[str, Any] = {"lease_expires_at": db_now_plus(lease_seconds)}
    if progress is not None:
        changes["progress"] = progress
    mine = TaskInstance.objects.filter(id=task_id, status="running", attempts=attempt).update(**changes)
    if not mine:
        return False, False
    cancel = TaskInstance.objects.filter(id=task_id).values_list("cancel_requested", flat=True).first()
    return True, bool(cancel)


def finish(
    claim_: Claim,
    outcome: str,
    *,
    output: Any = None,
    error: str | None = None,
    traceback: str | None = None,
    duration_ms: int | None = None,
) -> str | None:
    """Fecha a tentativa e decide o próximo estado da tarefa. ``None`` = perdeu a posse."""
    now = utcnow()
    with transaction.atomic():
        t = (
            TaskInstance.objects.select_for_update()
            .filter(id=claim_.task_id, status="running", attempts=claim_.attempt)
            .first()
        )
        if t is None:
            return None
        attempt_status = "cancelled" if t.cancel_requested and outcome != "succeeded" else outcome
        TaskAttempt.objects.filter(id=claim_.attempt_id).update(
            status=attempt_status,
            finished_at=Now(),
            duration_ms=duration_ms,
            error=error,
            traceback=traceback,
        )
        if outcome == "succeeded" and not t.cancel_requested:
            new_status, ready_at, finished = "succeeded", None, now
        elif outcome == "cancelled":
            new_status, ready_at, finished = "cancelled", None, now
        else:
            new_status, ready_at, finished = _after_failure(t, now=now)
        succeeded = new_status == "succeeded"
        TaskInstance.objects.filter(id=t.id).update(
            status=new_status,
            ready_at=ready_at,
            finished_at=finished,
            output=output if succeeded else None,
            error=None if succeeded else error,
            progress=100 if succeeded else F("progress"),
            worker=None,
            lease_expires_at=None,
            updated_at=Now(),
        )
    return new_status


def append_logs(attempt_id: int, lines: list[tuple[datetime, str, str]]) -> list[dict[str, Any]]:
    if not lines:
        return []
    # Uma a uma: o MySQL não devolve os ids gerados num INSERT em lote, e o painel precisa deles.
    with transaction.atomic():
        rows = [
            TaskLog.objects.create(attempt_id=attempt_id, logged_at=ts, level=level, message=message[:8000])
            for ts, level, message in lines
        ]
    return [{"id": r.id, "logged_at": r.logged_at, "level": r.level, "message": r.message} for r in rows]


# ========================================================== ações do operador
def cancel_run(run_id: int, user_id: int | None = None) -> dict[str, Any]:
    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().filter(id=run_id).first()
        if run is None:
            raise LookupError(run_id)
        if run.status not in ("queued", "running"):
            raise StoreError("Só é possível cancelar execuções na fila ou em andamento.")
        TaskInstance.objects.filter(run_id=run_id, status__in=("pending", "queued", "retry_wait")).update(
            status="cancelled",
            finished_at=Now(),
            ready_at=None,
            error="Cancelada pelo operador.",
            updated_at=Now(),
        )
        running = TaskInstance.objects.filter(run_id=run_id, status="running").update(
            cancel_requested=True, updated_at=Now()
        )
        WorkflowRun.objects.filter(id=run_id).update(status="cancelled", finished_at=Now(), updated_at=Now())
        audit(user_id, "run.cancel", f"run:{run_id}", {"running_tasks": running})
    return {"id": run_id, "status": "cancelled", "stopping_tasks": running}


def retry_run(run_id: int, *, from_task: str | None = None, user_id: int | None = None) -> dict[str, Any]:
    """Retoma a execução: refaz só o que falhou (ou ``from_task`` e o que vem depois).

    O que já terminou com sucesso é mantido, inclusive o resultado, então a
    execução continua exatamente de onde parou.
    """
    with transaction.atomic():
        run = (
            WorkflowRun.objects.select_for_update(of=("self",))
            .select_related("version")
            .filter(id=run_id)
            .first()
        )
        if run is None:
            raise LookupError(run_id)
        if run.status in ("queued", "running"):
            raise StoreError("A execução ainda está em andamento. Cancele antes ou aguarde terminar.")
        tasks = dict(TaskInstance.objects.filter(run_id=run_id).values_list("task_name", "status"))
        graph = run.version.graph
        if from_task:
            if from_task not in tasks:
                raise LookupError(from_task)
            reset = {from_task} | descendants(graph, from_task)
        else:
            reset = {name for name, status in tasks.items() if status in FAILED_LIKE}
            if not reset:
                raise StoreError("Nenhuma tarefa falhou nesta execução.")
            for name in list(reset):
                reset |= descendants(graph, name)
        TaskInstance.objects.filter(run_id=run_id, task_name__in=sorted(reset)).update(
            status="pending",
            max_attempts=F("attempts") + F("retries") + 1,
            output=None,
            error=None,
            progress=None,
            ready_at=None,
            queued_at=None,
            started_at=None,
            finished_at=None,
            queue_message_id=None,
            cancel_requested=False,
            worker=None,
            lease_expires_at=None,
            updated_at=Now(),
        )
        WorkflowRun.objects.filter(id=run_id).update(status="queued", finished_at=None, updated_at=Now())
        audit(user_id, "run.retry", f"run:{run_id}", {"from_task": from_task, "tasks": sorted(reset)})
    return {"id": run_id, "status": "queued", "reset": sorted(reset)}


def set_paused(name: str, paused: bool, user_id: int | None = None) -> dict[str, Any]:
    with transaction.atomic():
        wf = Workflow.objects.select_for_update().filter(name=name).first()
        if wf is None:
            raise LookupError(name)
        changes: dict[str, Any] = {"is_paused": paused, "updated_at": Now()}
        if not paused and wf.schedule:
            # ao retomar, segue a agenda a partir de agora (não dispara horários perdidos)
            changes["next_run_at"] = next_fire(wf.schedule, wf.timezone, utcnow())
        Workflow.objects.filter(pk=wf.pk).update(**changes)
        audit(user_id, "workflow.pause" if paused else "workflow.resume", f"workflow:{name}", {})
    return {"name": name, "is_paused": paused}


def audit(user_id: int | None, action: str, target: str | None, details: dict[str, Any]) -> None:
    AuditLog.objects.create(user_id=user_id, action=action, target=target, details=details)
