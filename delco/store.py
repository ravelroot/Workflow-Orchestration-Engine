from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
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

from . import integrations
from .loader import LoadResult
from .models import (
    ApiToken,
    AuditLog,
    IntegrationDelivery,
    TaskAttempt,
    TaskInstance,
    TaskLog,
    Worker,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from .params import resolve
from .schedule import next_fire, retry_delay, utcnow
from .sdk import descendants, diff_graphs

log = logging.getLogger("delco.store")

OPEN_TASK = ("pending", "queued", "running", "retry_wait")
TERMINAL_TASK = ("succeeded", "failed", "skipped", "cancelled")
FAILED_LIKE = ("failed", "skipped", "cancelled")
SKIPPED_ERROR = "Pulada: dependência não terminou com sucesso."


class StoreError(Exception):
    pass


class StoreValidationError(Exception):
    def __init__(self, fields: dict[str, str], message: str = "Revise os campos destacados.") -> None:
        super().__init__(message)
        self.fields = fields
        self.message = message


def db_now_plus(seconds: float) -> ExpressionWrapper:
    return ExpressionWrapper(
        Now() + Value(timedelta(seconds=seconds), output_field=DurationField()), output_field=DateTimeField()
    )


def db_ago(seconds: float) -> ExpressionWrapper:
    return db_now_plus(-seconds)


def sync_definitions(result: LoadResult, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    new_versions: list[str] = []
    for wf in result.workflows.values():
        d = wf.definition()
        source = result.sources.get(wf.source_file or "")
        source_hash = hashlib.sha256(source.encode()).hexdigest() if source is not None else None
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
                    workflow=row,
                    version=last + 1,
                    fingerprint=d["fingerprint"],
                    graph=d["graph"],
                    source_code=source,
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
                params_schema=d["params_schema"],
                tags=d["tags"],
                source_file=d["source_file"],
                **({"source_code": source, "source_hash": source_hash} if source is not None else {}),
                current_version=version,
                next_run_at=next_run,
                is_active=True,
                updated_at=Now(),
            )
    loaded = list(result.workflows)
    broken_files = list(result.errors)
    stale = Workflow.objects.filter(is_active=True).exclude(name__in=loaded)
    if broken_files:
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


def create_run(
    workflow: Workflow,
    *,
    trigger: str,
    params: dict[str, Any] | None = None,
    triggered_by: int | None = None,
    logical_date: datetime | None = None,
    note: str | None = None,
) -> int | None:
    merged = {**(workflow.default_params or {}), **(params or {})}
    try:
        with transaction.atomic():
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
        merged = validate_params(wf, params)
        run_id = create_run(wf, trigger=trigger, params=merged, triggered_by=user_id, note=note)
        audit(
            user_id,
            "run.trigger",
            f"run:{run_id}",
            {"workflow": name, "params": mask_params(wf.params_schema, params or {}), "source": trigger},
        )
    assert run_id is not None
    return run_id


def validate_params(wf: Workflow, params: dict[str, Any] | None) -> dict[str, Any]:
    merged, errors = resolve(wf.params_schema or {}, wf.default_params or {}, params)
    if errors:
        raise StoreValidationError(errors, "Confira os parâmetros destacados.")
    return merged


def mask_params(schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return {k: ("••••" if (schema or {}).get(k, {}).get("secret") else v) for k, v in (params or {}).items()}


def schedule_due(now: datetime | None = None) -> list[int]:
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
        integrations.enqueue(started, "run.started")
    return started


def decide(rule: str, upstream: list[str]) -> str:
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
    ready: list[tuple[int, str, int, str]]
    skipped: list[tuple[int, int, str]]
    finished: list[tuple[int, str]]


def dispatch(now: datetime | None = None) -> DispatchResult:
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
                continue
            tasks = list(
                TaskInstance.objects.filter(run_id=run_id)
                .order_by("id")
                .values("id", "task_name", "status", "upstream", "trigger_rule", "queue", "ready_at")
            )
            status = {t["task_name"]: t["status"] for t in tasks}
            ready_ids: list[int] = []
            skip_ids: list[int] = []
            changed = True
            while changed:
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
                changed_rows = WorkflowRun.objects.filter(id=run_id, status="running").update(
                    status=outcome, finished_at=Now(), updated_at=Now()
                )
                if changed_rows:
                    integrations.enqueue([run_id], f"run.{outcome}")
                result.finished.append((run_id, outcome))
    return result


def set_message_id(task_id: int, message_id: str) -> None:
    TaskInstance.objects.filter(id=task_id, status="queued").update(queue_message_id=message_id)


def recover_expired_leases() -> list[dict[str, Any]]:
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


def _after_failure(
    t: TaskInstance, *, now: datetime, retry: bool = True
) -> tuple[str, datetime | None, datetime | None]:
    if t.cancel_requested:
        return "cancelled", None, now
    if retry and t.attempts < t.max_attempts:
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
        deliveries, _ = IntegrationDelivery.objects.filter(
            run__isnull=True, created_at__lt=db_ago(retention_days * day)
        ).delete()
    return {"runs": runs, "tokens": tokens, "workers": workers, "audit": audit_rows, "deliveries": deliveries}


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
        update_conflicts=True,
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
    with transaction.atomic():
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
    retry: bool = True,
) -> str | None:
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
        elif outcome == "skipped" and not t.cancel_requested:
            new_status, ready_at, finished = "skipped", None, now
        else:
            new_status, ready_at, finished = _after_failure(t, now=now, retry=retry)
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
    with transaction.atomic():
        rows = [
            TaskLog.objects.create(attempt_id=attempt_id, logged_at=ts, level=level, message=message[:8000])
            for ts, level, message in lines
        ]
    return [{"id": r.id, "logged_at": r.logged_at, "level": r.level, "message": r.message} for r in rows]


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
        integrations.enqueue([run_id], "run.cancelled")
        audit(user_id, "run.cancel", f"run:{run_id}", {"running_tasks": running})
    return {"id": run_id, "status": "cancelled", "stopping_tasks": running}


def retry_run(run_id: int, *, from_task: str | None = None, user_id: int | None = None) -> dict[str, Any]:
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
            changes["next_run_at"] = next_fire(wf.schedule, wf.timezone, utcnow())
        Workflow.objects.filter(pk=wf.pk).update(**changes)
        audit(user_id, "workflow.pause" if paused else "workflow.resume", f"workflow:{name}", {})
    return {"name": name, "is_paused": paused}


def audit(user_id: int | None, action: str, target: str | None, details: dict[str, Any]) -> None:
    AuditLog.objects.create(user_id=user_id, action=action, target=target, details=details)


TASK_STATUS_PT = {"succeeded": "sucesso", "failed": "falha", "skipped": "pulada"}


def run_workflow_names(run_ids: list[int]) -> dict[int, str]:
    if not run_ids:
        return {}
    return dict(WorkflowRun.objects.filter(id__in=run_ids).values_list("id", "workflow__name"))


def _fmt_seconds(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def expire_runs(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    expired: list[dict[str, Any]] = []
    candidates = (
        WorkflowRun.objects.filter(status="running", started_at__isnull=False)
        .select_related("version", "workflow")
        .only("id", "started_at", "version__graph", "workflow__name")
        .order_by("id")[:500]
    )
    for run in candidates:
        limit = (run.version.graph or {}).get("run_timeout")
        if not limit or run.started_at + timedelta(seconds=float(limit)) > now:
            continue
        reason = f"Tempo limite da execução ({_fmt_seconds(limit)}) atingido."
        with transaction.atomic():
            locked = (
                WorkflowRun.objects.select_for_update(skip_locked=True)
                .filter(id=run.id, status="running")
                .values_list("id", flat=True)
                .first()
            )
            if locked is None:
                continue
            TaskInstance.objects.filter(run_id=run.id, status__in=("pending", "queued", "retry_wait")).update(
                status="cancelled", finished_at=Now(), ready_at=None, error=reason, updated_at=Now()
            )
            stopping = TaskInstance.objects.filter(run_id=run.id, status="running").update(
                cancel_requested=True, updated_at=Now()
            )
            WorkflowRun.objects.filter(id=run.id).update(
                status="failed", finished_at=Now(), error=reason, updated_at=Now()
            )
            integrations.enqueue([run.id], "run.failed")
            audit(None, "run.timeout", f"run:{run.id}", {"limit_seconds": limit, "stopping_tasks": stopping})
        expired.append({"id": run.id, "workflow": run.workflow.name, "reason": reason})
    return expired


def schedule_slots(expr: str, timezone: str, start: datetime, end: datetime, limit: int) -> list[datetime]:
    out: list[datetime] = []
    current = next_fire(expr, timezone, start - timedelta(microseconds=1))
    while current <= end and len(out) <= limit:
        out.append(current)
        current = next_fire(expr, timezone, current)
    return out


def backfill(
    name: str,
    *,
    start: date,
    end: date,
    params: dict[str, Any] | None = None,
    note: str | None = None,
    user_id: int | None = None,
    dry_run: bool = False,
    skip_existing: bool = True,
    limit: int = 366,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    if end < start:
        raise StoreValidationError({"end": "A data final precisa ser igual ou depois da inicial."})
    wf = Workflow.objects.select_related("current_version").filter(name=name, current_version__isnull=False).first()
    if wf is None:
        raise LookupError(name)
    if not wf.is_active:
        raise StoreError(f"O workflow {name} não está mais na pasta de workflows.")
    if not wf.schedule:
        raise StoreError("Só workflows com agenda podem ser reprocessados por período.")
    tz = ZoneInfo(wf.timezone)
    begin = datetime.combine(start, dtime.min, tz)
    finish_at = min(datetime.combine(end, dtime.max, tz), utcnow())
    if finish_at < begin:
        raise StoreValidationError({"start": "O período escolhido ainda não começou."})
    slots = schedule_slots(wf.schedule, wf.timezone, begin, finish_at, limit)
    if len(slots) > limit:
        raise StoreValidationError(
            {"end": f"O período tem mais de {limit} horários agendados. Escolha um intervalo menor."}
        )
    merged = validate_params(wf, params)
    existing: set[datetime] = set()
    if skip_existing and slots:
        existing = set(
            WorkflowRun.objects.filter(workflow=wf, logical_date__in=slots)
            .exclude(status__in=("failed", "cancelled"))
            .values_list("logical_date", flat=True)
        )
    todo = [s for s in slots if s not in existing]
    skipped = [s for s in slots if s in existing]
    created: list[int] = []
    if not dry_run and todo:
        label = note or f"Reprocessamento de {start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')}"
        with transaction.atomic():
            for slot in todo:
                run_id = create_run(
                    wf, trigger="backfill", params=merged, triggered_by=user_id, logical_date=slot, note=label
                )
                if run_id:
                    created.append(run_id)
            audit(
                user_id,
                "run.backfill",
                f"workflow:{name}",
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "runs": len(created),
                    "params": mask_params(wf.params_schema, params or {}),
                },
            )
    return {
        "workflow": name,
        "dry_run": dry_run,
        "slots": [s.isoformat() for s in todo],
        "skipped": [s.isoformat() for s in skipped],
        "created": created,
    }


def mark_task(run_id: int, name: str, status: str, *, user_id: int | None = None) -> dict[str, Any]:
    if status not in TASK_STATUS_PT:
        raise StoreValidationError({"status": "Use succeeded, failed ou skipped."})
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
            raise StoreError("A execução ainda está em andamento. Aguarde terminar ou cancele antes.")
        task = TaskInstance.objects.select_for_update().filter(run_id=run_id, task_name=name).first()
        if task is None:
            raise LookupError(name)
        if task.status == status:
            raise StoreError(f"A tarefa já está como {TASK_STATUS_PT[status]}.")
        message = None if status == "succeeded" else f"Marcada como {TASK_STATUS_PT[status]} pelo operador."
        TaskInstance.objects.filter(id=task.id).update(
            status=status,
            error=message,
            progress=100 if status == "succeeded" else F("progress"),
            finished_at=Coalesce("finished_at", Now()),
            ready_at=None,
            cancel_requested=False,
            updated_at=Now(),
        )
        statuses = dict(TaskInstance.objects.filter(run_id=run_id).values_list("task_name", "status"))
        reset: set[str] = set()
        if status in ("succeeded", "skipped"):
            below = descendants(run.version.graph, name)
            reset = {n for n, st in statuses.items() if n in below and st in ("skipped", "cancelled")}
        if reset:
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
            run_status = "queued"
            WorkflowRun.objects.filter(id=run_id).update(
                status="queued", finished_at=None, error=None, updated_at=Now()
            )
        else:
            run_status = run_outcome(list(statuses.values()))
            WorkflowRun.objects.filter(id=run_id).update(
                status=run_status, error=None if run_status == "succeeded" else F("error"), updated_at=Now()
            )
        audit(user_id, "run.mark", f"run:{run_id}", {"task": name, "status": status, "reset": sorted(reset)})
    return {"id": run_id, "task": name, "status": status, "reset": sorted(reset), "run_status": run_status}


def delete_run(run_id: int, *, user_id: int | None = None) -> None:
    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().select_related("workflow").filter(id=run_id).first()
        if run is None:
            raise LookupError(run_id)
        if run.status in ("queued", "running"):
            raise StoreError("Cancele a execução antes de excluir.")
        audit(user_id, "run.delete", f"run:{run_id}", {"workflow": run.workflow.name, "status": run.status})
        run.delete()


BULK_RUN_ACTIONS = ("cancel", "retry", "delete")


def bulk_runs(action: str, ids: list[int], *, user_id: int | None = None) -> dict[str, Any]:
    if action not in BULK_RUN_ACTIONS:
        raise StoreValidationError({"action": "Use cancel, retry ou delete."})
    done: list[int] = []
    errors: dict[str, str] = {}
    for run_id in sorted(set(ids)):
        try:
            if action == "cancel":
                cancel_run(run_id, user_id)
            elif action == "retry":
                retry_run(run_id, user_id=user_id)
            else:
                delete_run(run_id, user_id=user_id)
            done.append(run_id)
        except LookupError:
            errors[str(run_id)] = "Execução não encontrada."
        except StoreError as exc:
            errors[str(run_id)] = str(exc)
    return {"action": action, "done": done, "errors": errors}


def bulk_workflows(action: str, names: list[str] | None, *, user_id: int | None = None) -> dict[str, Any]:
    if action not in ("pause", "resume"):
        raise StoreValidationError({"action": "Use pause ou resume."})
    paused = action == "pause"
    qs = Workflow.objects.filter(is_active=True, schedule__isnull=False, is_paused=not paused)
    if names is not None:
        qs = qs.filter(name__in=names)
    changed = []
    for name in qs.order_by("name").values_list("name", flat=True):
        set_paused(name, paused, user_id)
        changed.append(name)
    return {"action": action, "changed": changed}


def workflow_versions(name: str, limit: int = 50) -> list[dict[str, Any]] | None:
    wf = Workflow.objects.filter(name=name).only("id", "current_version_id").first()
    if wf is None:
        return None
    from django.db.models import Count

    versions = list(
        WorkflowVersion.objects.filter(workflow=wf)
        .annotate(runs_count=Count("runs"))
        .order_by("-version")
        .values("id", "version", "fingerprint", "graph", "created_at", "runs_count")[: limit + 1]
    )
    out = []
    for i, v in enumerate(versions[:limit]):
        previous = versions[i + 1]["graph"] if i + 1 < len(versions) else None
        out.append(
            {
                "version": v["version"],
                "fingerprint": v["fingerprint"],
                "created_at": v["created_at"],
                "tasks": len(v["graph"]["tasks"]),
                "runs": v["runs_count"],
                "current": v["id"] == wf.current_version_id,
                "changes": diff_graphs(previous, v["graph"]) if previous is not None else None,
            }
        )
    return out


def set_note(run_id: int, note: str | None, *, user_id: int | None = None) -> bool:
    note = (note or "").strip() or None
    with transaction.atomic():
        changed = WorkflowRun.objects.filter(id=run_id).update(note=note, updated_at=Now())
        if changed:
            audit(user_id, "run.note", f"run:{run_id}", {"note": note})
    return bool(changed)
