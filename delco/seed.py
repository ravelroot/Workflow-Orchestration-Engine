"""Dados de demonstração: contas de teste e uma semana de histórico fictício.

Tudo aqui é inventado e existe só para o painel não abrir vazio. Desligue com
``DELCO_SEED=false`` antes do primeiro start em produção.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from croniter import croniter
from django.db import transaction
from django.db.models import Max

from .models import TaskAttempt, TaskInstance, TaskLog, User, Worker, Workflow, WorkflowRun
from .schedule import normalize_cron, utcnow
from .security import hash_password

log = logging.getLogger("delco.seed")

DEMO_PASSWORD = "Delco@2026"
DEMO_USERS = [
    ("admin@delco.dev", "Helena Prado", "admin"),
    ("operador@delco.dev", "Tiago Moura", "operator"),
    ("leitura@delco.dev", "Bruna Sales", "viewer"),
]
HISTORY_WORKERS = ["7f3a9c2e81b4-7", "c41e02d9a7f3-7"]
ERRORS = [
    ("ConnectionError", "O fornecedor não respondeu em 10 s (simulado)"),
    ("TimeoutError", "A consulta passou de 30 s"),
    ("ValueError", "Coluna 'valor' ausente no lote 3"),
    ("HTTPStatusError", "503 Service Unavailable em api.exemplo.local"),
]


def seed_users() -> bool:
    if User.objects.exists():
        return False
    pwd = hash_password(DEMO_PASSWORD)
    User.objects.bulk_create(
        [User(email=email, name=name, role=role, password_hash=pwd) for email, name, role in DEMO_USERS]
    )
    log.info("Contas de demonstração criadas (senha %s)", DEMO_PASSWORD)
    return True


def _fires(expr: str, start: datetime, end: datetime, limit: int) -> list[datetime]:
    it = croniter(normalize_cron(expr), start)
    out: list[datetime] = []
    while True:
        nxt = it.get_next(datetime)
        if nxt >= end:
            break
        out.append(nxt)
    return out[-limit:]


def _plan(graph: dict[str, Any], rng: random.Random, scenario: str) -> dict[str, dict[str, Any]]:
    """Decide status, tentativas e erro de cada tarefa de uma execução fictícia."""
    tasks = graph["tasks"]
    plan: dict[str, dict[str, Any]] = {t["name"]: {"status": "pending", "attempts": []} for t in tasks}
    victim = None
    if scenario in ("retry", "fail"):
        options = [t["name"] for t in tasks if t["trigger_rule"] == "all_success" and t["retries"] > 0]
        victim = rng.choice(options) if options else None
    for t in tasks:
        ups = [plan[u]["status"] for u in t["upstream"]]
        rule = t["trigger_rule"]
        if rule == "all_success" and any(s != "succeeded" for s in ups):
            plan[t["name"]]["status"] = "skipped"
            continue
        if rule == "one_failed" and "failed" not in ups:
            plan[t["name"]]["status"] = "skipped"
            continue
        error = rng.choice(ERRORS)
        if t["name"] == victim and scenario == "fail":
            plan[t["name"]] = {
                "status": "failed",
                "attempts": ["failed"] * (t["retries"] + 1),
                "error": error,
            }
        elif t["name"] == victim:
            plan[t["name"]] = {"status": "succeeded", "attempts": ["failed", "succeeded"], "error": error}
        else:
            plan[t["name"]] = {"status": "succeeded", "attempts": ["succeeded"]}
    return plan


def _duration(name: str, rng: random.Random) -> float:
    if name.startswith("passo_"):
        return rng.uniform(1.3, 2.1)
    if name.startswith(("agregar", "validar", "remover", "contar", "cumprimentar", "registrar", "alertar")):
        return rng.uniform(0.15, 0.9)
    return rng.uniform(1.2, 6.5)


def _insert_run(
    batch: _Batch,
    wf: Workflow,
    start: datetime,
    rng: random.Random,
    scenario: str,
    trigger: str,
    logical: datetime | None,
    user_id: int | None,
) -> None:
    """Monta uma execução fictícia completa (tarefas, tentativas e logs) para gravar em lote."""
    graph = wf.current_version.graph
    plan = _plan(graph, rng, scenario)
    run_start = start + timedelta(milliseconds=rng.randint(40, 400))
    ends: dict[str, datetime] = {}
    rows = []
    for t in graph["tasks"]:
        p = plan[t["name"]]
        ready = max([ends.get(u, run_start) for u in t["upstream"]] or [run_start])
        if p["status"] == "skipped":
            ends[t["name"]] = ready
            rows.append((t, p, None, ready, []))
            continue
        clock = ready + timedelta(milliseconds=rng.randint(20, 300))
        first = clock
        attempts = []
        for i, status in enumerate(p["attempts"], start=1):
            dur = _duration(t["name"], rng) * (0.4 if status == "failed" else 1)
            attempts.append((i, status, clock, clock + timedelta(seconds=dur)))
            clock += timedelta(seconds=dur)
            if status == "failed" and i < len(p["attempts"]):
                clock += timedelta(seconds=min(t["retry_delay"] * (t["retry_backoff"] ** (i - 1)), 120))
        ends[t["name"]] = clock
        rows.append((t, p, first, clock, attempts))
    statuses = [plan[t["name"]]["status"] for t in graph["tasks"]]
    run_end = max(ends.values())
    run = WorkflowRun(
        workflow_id=wf.id,
        version_id=wf.current_version_id,
        status="failed" if "failed" in statuses else "succeeded",
        trigger=trigger,
        triggered_by_id=user_id,
        logical_date=logical,
        params=wf.default_params,
        created_at=start,
        started_at=run_start,
        finished_at=run_end,
        updated_at=run_end,
    )
    tasks = []
    for t, p, first, end, attempts in rows:
        status = p["status"]
        err = p.get("error")
        error_text = f"{err[0]}: {err[1]}" if err and status == "failed" else None
        if status == "skipped":
            error_text = "Pulada: dependência não terminou com sucesso."
        instance = TaskInstance(
            task_name=t["name"],
            status=status,
            upstream=t["upstream"],
            trigger_rule=t["trigger_rule"],
            queue=t["queue"],
            retries=t["retries"],
            max_attempts=t["retries"] + 1,
            retry_delay=t["retry_delay"],
            retry_backoff=t["retry_backoff"],
            max_retry_delay=t["max_retry_delay"],
            timeout_seconds=t["timeout"],
            attempts=len(attempts),
            progress=100 if status == "succeeded" else None,
            output={"ok": True, "registros": rng.randint(10, 5000)} if status == "succeeded" else None,
            error=error_text,
            queued_at=first,
            started_at=first,
            finished_at=end,
            updated_at=end,
        )
        tries = []
        for number, a_status, a_start, a_end in attempts:
            worker = rng.choice(HISTORY_WORKERS)
            a_err = f"{err[0]}: {err[1]}" if a_status == "failed" and err else None
            attempt = TaskAttempt(
                number=number,
                worker_id=worker,
                status=a_status,
                started_at=a_start,
                finished_at=a_end,
                duration_ms=int((a_end - a_start).total_seconds() * 1000),
                error=a_err,
                traceback=f'Traceback (most recent call last):\n  File "workflows/…", line 42, in {t["name"]}\n{a_err}'
                if a_err
                else None,
            )
            lines = [(a_start, "info", f"Tentativa {number} de {t['retries'] + 1} no worker {worker}")]
            if a_status == "failed":
                lines.append((a_end, "error", a_err or "Falha"))
            else:
                lines.append((a_start + (a_end - a_start) / 2, "info", "Processando…"))
                lines.append((a_end, "info", f"Concluída em {(a_end - a_start).total_seconds():.2f} s"))
            tries.append(
                (
                    attempt,
                    [TaskLog(logged_at=at, level=level, message=message) for at, level, message in lines],
                )
            )
        tasks.append((instance, tries))
    batch.append((run, tasks))


def _with_ids(model, objs: list) -> list:
    """Numera os objetos a partir do maior id da tabela (o seed roda uma vez, em banco sem execuções)."""
    start = (model.objects.aggregate(n=Max("id"))["n"] or 0) + 1
    for i, obj in enumerate(objs):
        obj.id = start + i
    return objs


class _Batch(list):
    """Execuções montadas em memória e gravadas com ``bulk_create`` (rápido mesmo com milhares de linhas)."""

    def save(self) -> None:
        # O MySQL não devolve os ids de um INSERT em lote: os ids saem daqui, a partir do maior já gravado.
        runs = _with_ids(WorkflowRun, [run for run, _ in self])
        WorkflowRun.objects.bulk_create(runs)
        instances = []
        for run, (_, tasks) in zip(runs, self, strict=True):
            for instance, _ in tasks:
                instance.run_id = run.id
                instances.append(instance)
        TaskInstance.objects.bulk_create(_with_ids(TaskInstance, instances))
        attempts, logs = [], []
        for _, tasks in self:
            for instance, tries in tasks:
                for attempt, _ in tries:
                    attempt.task_instance_id = instance.id
                    attempts.append(attempt)
        TaskAttempt.objects.bulk_create(_with_ids(TaskAttempt, attempts), batch_size=2000)
        for _, tasks in self:
            for _, tries in tasks:
                for attempt, lines in tries:
                    for line in lines:
                        line.attempt_id = attempt.id
                        logs.append(line)
        TaskLog.objects.bulk_create(logs, batch_size=5000)


def seed_history(*, now: datetime | None = None, seed: int = 2026) -> int:
    """Cria uma semana de execuções fictícias se o banco ainda não tiver nenhuma."""
    if WorkflowRun.objects.exists():
        return 0
    now = now or utcnow()
    rng = random.Random(seed)
    workflows = list(
        Workflow.objects.select_related("current_version")
        .filter(is_active=True, current_version__isnull=False)
        .order_by("name")
    )
    if not workflows:
        return 0
    users = list(User.objects.exclude(role="viewer").order_by("id").values_list("id", flat=True))
    batch = _Batch()
    with transaction.atomic():
        Worker.objects.bulk_create(
            [
                Worker(
                    id=worker,
                    hostname=worker,
                    pid=1,
                    queues=["default", "critical"],
                    concurrency=4,
                    version="1.0.0",
                    status="offline",
                    started_at=now - timedelta(days=8),
                    last_heartbeat_at=now - timedelta(hours=26),
                    stopped_at=now - timedelta(hours=26),
                )
                for worker in HISTORY_WORKERS
            ],
            ignore_conflicts=True,
        )
        for wf in workflows:
            if wf.schedule:
                limit = 96 if wf.schedule.startswith("*/") else 7
                for fire in _fires(wf.schedule, now - timedelta(days=7), now - timedelta(minutes=20), limit):
                    roll = rng.random()
                    scenario = "fail" if roll < 0.05 else "retry" if roll < 0.17 else "ok"
                    _insert_run(batch, wf, fire, rng, scenario, "schedule", fire, None)
            else:
                count = 2 if len(wf.current_version.graph["tasks"]) > 20 else 4
                for i in range(count):
                    start = now - timedelta(days=rng.uniform(0.3, 6.5))
                    scenario = "fail" if i == 1 else "ok"
                    _insert_run(
                        batch,
                        wf,
                        start,
                        rng,
                        scenario,
                        rng.choice(["manual", "cli"]),
                        None,
                        rng.choice(users) if users else None,
                    )
        batch.save()
    log.info("Histórico de demonstração criado: %s execuções", len(batch))
    return len(batch)
