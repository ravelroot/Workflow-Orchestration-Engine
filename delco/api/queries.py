"""Consultas de leitura usadas pela API e pelo painel (ORM do Django)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce, RowNumber, TruncHour
from django.utils import timezone

from ..models import AuditLog, TaskAttempt, TaskInstance, TaskLog, Worker, Workflow, WorkflowRun
from ..schedule import describe

TERMINAL = ("succeeded", "failed", "skipped", "cancelled")
FAILED_ATTEMPT = ("failed", "timed_out", "lost")


def duration_ms(
    started: datetime | None, finished: datetime | None, now: datetime | None = None
) -> int | None:
    """Duração em milissegundos; enquanto não termina, conta até agora."""
    if started is None:
        return None
    end = finished or now or timezone.now()
    return int((end - started).total_seconds() * 1000)


# ------------------------------------------------------------------ workflows
def list_workflows() -> list[dict[str, Any]]:
    week = timezone.now() - timedelta(days=7)
    stats = {
        r["workflow_id"]: r
        for r in WorkflowRun.objects.values("workflow_id").annotate(
            active=Count("id", filter=Q(status__in=("queued", "running"))),
            ok=Count("id", filter=Q(status="succeeded", created_at__gt=week)),
            done=Count("id", filter=Q(status__in=("succeeded", "failed"), created_at__gt=week)),
        )
    }
    recent: dict[int, list[dict[str, Any]]] = {}
    last_runs = (
        WorkflowRun.objects.annotate(
            n=Window(RowNumber(), partition_by=F("workflow_id"), order_by=F("id").desc())
        )
        .filter(n__lte=14)
        .order_by("id")
        .values("workflow_id", "id", "status", "created_at")
    )
    for r in last_runs:
        recent.setdefault(r.pop("workflow_id"), []).append(r)
    rows = []
    for w in Workflow.objects.select_related("current_version").order_by("-is_active", "name"):
        s = stats.get(w.id, {})
        version = w.current_version
        rows.append(
            {
                "name": w.name,
                "description": w.description,
                "schedule": w.schedule,
                "timezone": w.timezone,
                "is_paused": w.is_paused,
                "is_active": w.is_active,
                "next_run_at": w.next_run_at,
                "tags": w.tags,
                "max_active_runs": w.max_active_runs,
                "source_file": w.source_file,
                "version": version.version if version else None,
                "task_count": len(version.graph["tasks"]) if version else None,
                "active_runs": s.get("active", 0),
                "recent": recent.get(w.id, []),
                "success_rate": round(100 * s["ok"] / s["done"]) if s.get("done") else None,
                "schedule_text": describe(w.schedule),
            }
        )
    return rows


def get_workflow(name: str) -> dict[str, Any] | None:
    w = Workflow.objects.select_related("current_version").filter(name=name).first()
    if w is None:
        return None
    v = w.current_version
    month = timezone.now() - timedelta(days=30)
    stats = WorkflowRun.objects.filter(workflow=w, created_at__gt=month).aggregate(
        succeeded=Count("id", filter=Q(status="succeeded")),
        failed=Count("id", filter=Q(status="failed")),
        cancelled=Count("id", filter=Q(status="cancelled")),
        active=Count("id", filter=Q(status__in=("queued", "running"))),
        avg=Avg(F("finished_at") - F("started_at"), filter=Q(status="succeeded")),
    )
    avg = stats.pop("avg")
    stats["avg_ms"] = int(avg.total_seconds() * 1000) if avg is not None else None
    return {
        "name": w.name,
        "description": w.description,
        "schedule": w.schedule,
        "timezone": w.timezone,
        "is_paused": w.is_paused,
        "is_active": w.is_active,
        "next_run_at": w.next_run_at,
        "tags": w.tags,
        "max_active_runs": w.max_active_runs,
        "default_params": w.default_params,
        "source_file": w.source_file,
        "created_at": w.created_at,
        "updated_at": w.updated_at,
        "version": v.version if v else None,
        "fingerprint": v.fingerprint if v else None,
        "graph": v.graph if v else None,
        "version_created_at": v.created_at if v else None,
        "versions": w.versions.count(),
        "schedule_text": describe(w.schedule),
        "stats_30d": stats,
    }


def workflow_grid(name: str, limit: int) -> dict[str, Any] | None:
    w = (
        Workflow.objects.select_related("current_version")
        .filter(name=name, current_version__isnull=False)
        .first()
    )
    if w is None:
        return None
    now = timezone.now()
    runs = list(
        WorkflowRun.objects.filter(workflow=w)
        .order_by("-id")
        .values("id", "status", "trigger", "created_at", "logical_date", "started_at", "finished_at")[:limit]
    )
    runs.reverse()
    cells: dict[int, dict[str, Any]] = {r["id"]: {} for r in runs}
    for t in TaskInstance.objects.filter(run_id__in=list(cells)).values(
        "run_id", "task_name", "status", "attempts", "started_at", "finished_at"
    ):
        cells[t["run_id"]][t["task_name"]] = {
            "status": t["status"],
            "attempts": t["attempts"],
            "duration_ms": duration_ms(t["started_at"], t["finished_at"], now),
        }
    out = []
    for r in runs:
        started, finished = r.pop("started_at"), r.pop("finished_at")
        out.append({**r, "duration_ms": duration_ms(started, finished, now), "cells": cells[r["id"]]})
    return {"tasks": [t["name"] for t in w.current_version.graph["tasks"]], "runs": out}


# --------------------------------------------------------------------- runs
def _runs():
    return WorkflowRun.objects.select_related("workflow", "version", "triggered_by").annotate(
        tasks_total=Count("tasks"),
        tasks_succeeded=Count("tasks", filter=Q(tasks__status="succeeded")),
        tasks_failed=Count("tasks", filter=Q(tasks__status="failed")),
        tasks_running=Count("tasks", filter=Q(tasks__status="running")),
        tasks_finished=Count("tasks", filter=Q(tasks__status__in=TERMINAL)),
    )


def _run_row(r: WorkflowRun, now: datetime) -> dict[str, Any]:
    return {
        "id": r.id,
        "workflow": r.workflow.name,
        "status": r.status,
        "trigger": r.trigger,
        "triggered_by": r.triggered_by.name if r.triggered_by else None,
        "logical_date": r.logical_date,
        "note": r.note,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "version": r.version.version,
        "duration_ms": duration_ms(r.started_at, r.finished_at, now),
        "tasks_total": r.tasks_total,
        "tasks_succeeded": r.tasks_succeeded,
        "tasks_failed": r.tasks_failed,
        "tasks_running": r.tasks_running,
        "tasks_finished": r.tasks_finished,
    }


def _run_filters(
    *, workflow: str | None, status: str | None, trigger: str | None = None, since: int | None = None,
    q: str | None = None,
) -> Q:
    filters = Q()
    if workflow:
        filters &= Q(workflow__name=workflow)
    if status:
        filters &= Q(status=status)
    if trigger:
        filters &= Q(trigger=trigger)
    if since:
        filters &= Q(created_at__gte=timezone.now() - timedelta(days=since))
    if q:
        term = q.strip().lstrip("#")
        filters &= Q(id=int(term)) if term.isdigit() else Q(note__icontains=term) | Q(workflow__name__icontains=term)
    return filters


def list_runs(
    *, workflow: str | None, status: str | None, page: int, size: int, trigger: str | None = None,
    since: int | None = None, q: str | None = None,
) -> dict[str, Any]:
    filters = _run_filters(workflow=workflow, status=status, trigger=trigger, since=since, q=q)
    total = WorkflowRun.objects.filter(filters).count()
    now = timezone.now()
    offset = (page - 1) * size
    items = [_run_row(r, now) for r in _runs().filter(filters).order_by("-id")[offset : offset + size]]
    return {"items": items, "total": total, "page": page, "size": size}


EXPORT_COLUMNS = [
    "id", "workflow", "estado", "origem", "disparada_por", "criada_em", "inicio", "fim", "duracao_ms",
    "tarefas_total", "tarefas_sucesso", "tarefas_falha", "observacao",
]


def export_runs(*, limit: int = 10000, **filters: Any) -> list[list[Any]]:
    """Linhas do CSV de execuções (mesmos filtros da listagem), da mais recente para a mais antiga."""
    now = timezone.now()
    rows = []
    for r in _runs().filter(_run_filters(**filters)).order_by("-id")[:limit]:
        d = _run_row(r, now)
        iso = lambda v: v.isoformat() if v else ""  # noqa: E731
        rows.append([
            d["id"], d["workflow"], d["status"], d["trigger"], d["triggered_by"] or "", iso(d["created_at"]),
            iso(d["started_at"]), iso(d["finished_at"]), d["duration_ms"] if d["duration_ms"] is not None else "",
            d["tasks_total"], d["tasks_succeeded"], d["tasks_failed"], d["note"] or "",
        ])
    return rows


def get_run(run_id: int) -> dict[str, Any] | None:
    r = _runs().filter(id=run_id).first()
    if r is None:
        return None
    now = timezone.now()
    run = {**_run_row(r, now), "params": r.params, "graph": r.version.graph}
    tasks = (
        TaskInstance.objects.filter(run_id=run_id)
        .order_by("id")
        .values(
            "id",
            "status",
            "attempts",
            "max_attempts",
            "queue",
            "progress",
            "upstream",
            "trigger_rule",
            "worker_id",
            "cancel_requested",
            "error",
            "ready_at",
            "queued_at",
            "started_at",
            "finished_at",
            "timeout_seconds",
            name=F("task_name"),
        )
    )
    run["tasks"] = [{**t, "duration_ms": duration_ms(t["started_at"], t["finished_at"], now)} for t in tasks]
    return run


def get_task(run_id: int, name: str) -> dict[str, Any] | None:
    t = TaskInstance.objects.select_related("run__workflow").filter(run_id=run_id, task_name=name).first()
    if t is None:
        return None
    task = {
        "id": t.id,
        "run_id": t.run_id,
        "name": t.task_name,
        "status": t.status,
        "attempts": t.attempts,
        "max_attempts": t.max_attempts,
        "retries": t.retries,
        "queue": t.queue,
        "progress": t.progress,
        "upstream": t.upstream,
        "trigger_rule": t.trigger_rule,
        "worker_id": t.worker_id,
        "cancel_requested": t.cancel_requested,
        "output": t.output,
        "error": t.error,
        "retry_delay": t.retry_delay,
        "retry_backoff": t.retry_backoff,
        "timeout_seconds": t.timeout_seconds,
        "ready_at": t.ready_at,
        "queued_at": t.queued_at,
        "started_at": t.started_at,
        "finished_at": t.finished_at,
        "duration_ms": duration_ms(t.started_at, t.finished_at),
        "workflow": t.run.workflow.name,
    }
    task["attempts_list"] = list(
        TaskAttempt.objects.filter(task_instance_id=t.id)
        .annotate(log_lines=Count("logs"))
        .order_by("-number")
        .values(
            "id",
            "number",
            "status",
            "worker_id",
            "started_at",
            "finished_at",
            "duration_ms",
            "error",
            "traceback",
            "log_lines",
        )
    )
    return task


def attempt_logs(attempt_id: int, after: int, limit: int) -> dict[str, Any] | None:
    if not TaskAttempt.objects.filter(id=attempt_id).exists():
        return None
    rows = list(
        TaskLog.objects.filter(attempt_id=attempt_id, id__gt=after)
        .order_by("id")
        .values("id", "level", "message", at=F("logged_at"))[: limit + 1]
    )
    return {"lines": rows[:limit], "has_more": len(rows) > limit}


# --------------------------------------------------------------- monitoramento
def overview() -> dict[str, Any]:
    now = timezone.now()
    day = now - timedelta(hours=24)
    runs = WorkflowRun.objects.aggregate(
        active_runs=Count("id", filter=Q(status="running")),
        queued_runs=Count("id", filter=Q(status="queued")),
        runs_succeeded_24h=Count("id", filter=Q(status="succeeded", finished_at__gt=day)),
        runs_failed_24h=Count("id", filter=Q(status="failed", finished_at__gt=day)),
    )
    tasks = TaskInstance.objects.filter(status__in=("queued", "retry_wait", "running")).aggregate(
        queued_tasks=Count("id", filter=Q(status="queued")),
        retry_tasks=Count("id", filter=Q(status="retry_wait")),
        running_tasks=Count("id", filter=Q(status="running")),
    )
    workers = Worker.objects.filter(status="online").aggregate(
        slots_total=Coalesce(Sum("concurrency"), 0), workers_online=Count("id")
    )
    attempts = TaskAttempt.objects.filter(finished_at__gt=day).aggregate(
        failed_attempts_24h=Count("id", filter=Q(status__in=FAILED_ATTEMPT)),
        avg=Avg("duration_ms", filter=Q(status="succeeded")),
    )
    avg = attempts.pop("avg")
    kpis = {
        "active_runs": runs["active_runs"],
        "queued_runs": runs["queued_runs"],
        "queued_tasks": tasks["queued_tasks"],
        "retry_tasks": tasks["retry_tasks"],
        "running_tasks": tasks["running_tasks"],
        "slots_total": workers["slots_total"],
        "workers_online": workers["workers_online"],
        "workflows": Workflow.objects.filter(is_active=True).count(),
        "runs_succeeded_24h": runs["runs_succeeded_24h"],
        "runs_failed_24h": runs["runs_failed_24h"],
        "failed_attempts_24h": attempts["failed_attempts_24h"],
        "avg_task_ms_24h": round(avg) if avg is not None else None,
    }
    # vazão por hora nas últimas 24 h (horas sem tentativa aparecem com zero)
    first = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    per_hour = {
        r["hour"]: r
        for r in TaskAttempt.objects.filter(finished_at__gte=first)
        .annotate(hour=TruncHour("finished_at"))
        .values("hour")
        .annotate(
            succeeded=Count("id", filter=Q(status="succeeded")),
            failed=Count("id", filter=Q(status__in=FAILED_ATTEMPT)),
        )
    }
    throughput = []
    for i in range(24):
        hour = first + timedelta(hours=i)
        r = per_hour.get(hour, {})
        throughput.append({"hour": hour, "succeeded": r.get("succeeded", 0), "failed": r.get("failed", 0)})
    recent = [_run_row(r, now) for r in _runs().order_by("-id")[:8]]
    return {"kpis": kpis, "throughput": throughput, "recent_runs": recent}


def list_workers() -> list[dict[str, Any]]:
    day = timezone.now() - timedelta(hours=24)
    workers = list(
        Worker.objects.filter(~Q(status="offline") | Q(last_heartbeat_at__gt=day))
        .annotate(
            executed_24h=Count("attempts", filter=Q(attempts__finished_at__gt=day)),
            failed_24h=Count(
                "attempts",
                filter=Q(attempts__finished_at__gt=day) & ~Q(attempts__status__in=("succeeded", "running")),
            ),
            offline_last=Case(
                When(status="offline", then=Value(1)), default=Value(0), output_field=IntegerField()
            ),
        )
        .order_by("offline_last", "started_at")
        .values(
            "id",
            "hostname",
            "pid",
            "queues",
            "concurrency",
            "version",
            "status",
            "started_at",
            "last_heartbeat_at",
            "stopped_at",
            "executed_24h",
            "failed_24h",
        )
    )
    attempt_start = TaskAttempt.objects.filter(
        task_instance=OuterRef("pk"), number=OuterRef("attempts")
    ).values("started_at")[:1]
    running = (
        TaskInstance.objects.filter(status="running")
        .annotate(attempt_started=Subquery(attempt_start))
        .order_by("attempt_started")
        .values(
            "worker_id",
            "run_id",
            "task_name",
            "progress",
            "attempts",
            "attempt_started",
            task_id=F("id"),
            workflow=F("run__workflow__name"),
        )
    )
    by_worker: dict[str | None, list] = {}
    for r in running:
        r["started_at"] = r.pop("attempt_started")
        by_worker.setdefault(r.pop("worker_id"), []).append(r)
    for w in workers:
        w["running"] = by_worker.get(w["id"], [])
    return workers


def queue_counts() -> list[dict[str, Any]]:
    return list(
        TaskInstance.objects.filter(status__in=("queued", "running", "retry_wait"))
        .values("queue")
        .annotate(
            waiting=Count("id", filter=Q(status="queued")),
            running=Count("id", filter=Q(status="running")),
            retry_wait=Count("id", filter=Q(status="retry_wait")),
        )
        .order_by("queue")
    )


def audit_page(page: int, size: int) -> dict[str, Any]:
    total = AuditLog.objects.count()
    offset = (page - 1) * size
    items = [
        {
            "id": a.id,
            "action": a.action,
            "target": a.target,
            "details": a.details,
            "created_at": a.created_at,
            "user_name": a.user.name if a.user else None,
            "user_email": a.user.email if a.user else None,
        }
        for a in AuditLog.objects.select_related("user").order_by("-id")[offset : offset + size]
    ]
    return {"items": items, "total": total, "page": page, "size": size}


# ----------------------------------------------------------------- desempenho
def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return int(values[k])


def insights(days: int) -> dict[str, Any]:
    """Saúde de cada workflow no período: volume, taxa de sucesso, p50/p95 e tarefas problemáticas."""
    now = timezone.now()
    since = now - timedelta(days=days)
    first_day = timezone.localtime(now).date() - timedelta(days=days - 1)
    runs = list(
        WorkflowRun.objects.filter(finished_at__gte=since, status__in=("succeeded", "failed", "cancelled"))
        .values_list("workflow__name", "status", "started_at", "finished_at")[:50000]
    )
    per: dict[str, dict[str, Any]] = {}
    for name, status, started, finished in runs:
        w = per.setdefault(name, {"workflow": name, "runs": 0, "succeeded": 0, "failed": 0, "cancelled": 0,
                                  "durations": [], "daily": {}})
        w["runs"] += 1
        w[status] += 1
        if started and finished:
            w["durations"].append(int((finished - started).total_seconds() * 1000))
        day = timezone.localtime(finished).date()
        cell = w["daily"].setdefault(day, [0, 0])
        cell[0 if status == "succeeded" else 1] += 1
    for wf in Workflow.objects.filter(is_active=True).values_list("name", flat=True):
        per.setdefault(wf, {"workflow": wf, "runs": 0, "succeeded": 0, "failed": 0, "cancelled": 0,
                            "durations": [], "daily": {}})
    workflows = []
    for w in per.values():
        days_list = [first_day + timedelta(days=i) for i in range(days)]
        decided = w["succeeded"] + w["failed"]
        workflows.append({
            "workflow": w["workflow"],
            "runs": w["runs"],
            "succeeded": w["succeeded"],
            "failed": w["failed"],
            "cancelled": w["cancelled"],
            "success_rate": round(100 * w["succeeded"] / decided, 1) if decided else None,
            "p50_ms": _pct(w["durations"], 50),
            "p95_ms": _pct(w["durations"], 95),
            "daily": [{"day": d.isoformat(), "succeeded": w["daily"].get(d, [0, 0])[0],
                       "failed": w["daily"].get(d, [0, 0])[1]} for d in days_list],
        })
    workflows.sort(key=lambda w: (-w["runs"], w["workflow"]))

    attempts = TaskAttempt.objects.filter(finished_at__gte=since)
    failing = list(
        attempts.filter(status__in=FAILED_ATTEMPT)
        .values(workflow=F("task_instance__run__workflow__name"), task=F("task_instance__task_name"))
        .annotate(failures=Count("id"))
        .order_by("-failures")[:8]
    )
    for f in failing:
        last = (
            attempts.filter(status__in=FAILED_ATTEMPT, task_instance__task_name=f["task"],
                            task_instance__run__workflow__name=f["workflow"])
            .order_by("-id").values("error", "task_instance__run_id").first()
        ) or {}
        f["last_error"] = (last.get("error") or "")[:200]
        f["last_run_id"] = last.get("task_instance__run_id")
    durations: dict[tuple[str, str], list[int]] = {}
    for wf, task, ms in attempts.filter(status="succeeded", duration_ms__isnull=False).values_list(
        "task_instance__run__workflow__name", "task_instance__task_name", "duration_ms"
    )[:100000]:
        durations.setdefault((wf, task), []).append(ms)
    slowest = sorted(
        ({"workflow": wf, "task": task, "count": len(v), "p95_ms": _pct(v, 95), "avg_ms": round(sum(v) / len(v))}
         for (wf, task), v in durations.items()),
        key=lambda x: -(x["p95_ms"] or 0),
    )[:8]
    totals_attempts = attempts.aggregate(
        failed=Count("id", filter=Q(status__in=FAILED_ATTEMPT)),
        processing=Coalesce(Sum("duration_ms"), 0),
    )
    succeeded = sum(w["succeeded"] for w in workflows)
    failed = sum(w["failed"] for w in workflows)
    return {
        "days": days,
        "totals": {
            "runs": sum(w["runs"] for w in workflows),
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": round(100 * succeeded / (succeeded + failed), 1) if succeeded + failed else None,
            "failed_attempts": totals_attempts["failed"],
            "processing_ms": totals_attempts["processing"],
        },
        "workflows": workflows,
        "failing_tasks": failing,
        "slowest_tasks": slowest,
    }


# --------------------------------------------------------- próximas execuções
def upcoming(hours: int, limit: int = 60) -> list[dict[str, Any]]:
    """Horários agendados nas próximas ``hours`` horas, de todos os workflows ativos."""
    from ..schedule import next_fire

    now = timezone.now()
    end = now + timedelta(hours=hours)
    out: list[dict[str, Any]] = []
    for wf in Workflow.objects.filter(is_active=True, is_paused=False, schedule__isnull=False).only(
        "name", "schedule", "timezone", "next_run_at", "current_version"
    ):
        at = wf.next_run_at or next_fire(wf.schedule, wf.timezone, now)
        count = 0
        while at and at <= end and count < 48:
            out.append({"workflow": wf.name, "at": at, "schedule_text": describe(wf.schedule)})
            at = next_fire(wf.schedule, wf.timezone, at)
            count += 1
    out.sort(key=lambda x: (x["at"], x["workflow"]))
    return out[:limit]
