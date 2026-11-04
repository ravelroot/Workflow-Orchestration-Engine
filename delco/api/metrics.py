from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .. import __version__
from ..models import IntegrationDelivery, TaskInstance, Worker, Workflow, WorkflowRun

OPEN_TASKS = ("pending", "queued", "running", "retry_wait")


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def metric(self, name: str, kind: str, help_: str, samples: list[tuple[dict[str, str], float]]) -> None:
        self.lines.append(f"# HELP {name} {help_}")
        self.lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            label = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
            self.lines.append(f"{name}{{{label}}} {value:g}" if label else f"{name} {value:g}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def render(bus) -> str:
    now = timezone.now()
    day = now - timedelta(hours=24)
    w = _Writer()
    w.metric("delco_info", "gauge", "Versão do Delco.", [({"version": __version__}, 1)])
    workflows = list(Workflow.objects.filter(is_active=True).values_list("name", "is_paused"))
    w.metric("delco_workflows", "gauge", "Workflows carregados.", [({}, len(workflows))])
    w.metric(
        "delco_workflow_paused",
        "gauge",
        "1 quando a agenda do workflow está pausada.",
        [({"workflow": n}, 1 if p else 0) for n, p in workflows],
    )
    active = (
        WorkflowRun.objects.filter(status__in=("queued", "running"))
        .values("workflow__name", "status")
        .annotate(n=Count("id"))
    )
    w.metric(
        "delco_runs_active",
        "gauge",
        "Execuções na fila ou rodando.",
        [({"workflow": r["workflow__name"], "status": r["status"]}, r["n"]) for r in active],
    )
    finished = (
        WorkflowRun.objects.filter(finished_at__gt=day)
        .values("workflow__name", "status")
        .annotate(n=Count("id"))
    )
    w.metric(
        "delco_runs_finished_24h",
        "gauge",
        "Execuções terminadas nas últimas 24 horas.",
        [({"workflow": r["workflow__name"], "status": r["status"]}, r["n"]) for r in finished],
    )
    last = (
        WorkflowRun.objects.filter(status="succeeded")
        .values("workflow__name")
        .annotate(at=Max("finished_at"))
    )
    w.metric(
        "delco_workflow_last_success_timestamp_seconds",
        "gauge",
        "Momento (Unix) do último sucesso de cada workflow.",
        [({"workflow": r["workflow__name"]}, r["at"].timestamp()) for r in last if r["at"]],
    )
    tasks = TaskInstance.objects.filter(status__in=OPEN_TASKS).values("queue", "status").annotate(n=Count("id"))
    w.metric(
        "delco_tasks",
        "gauge",
        "Tarefas abertas por fila e estado.",
        [({"queue": t["queue"], "status": t["status"]}, t["n"]) for t in tasks],
    )
    workers = Worker.objects.values("status").annotate(n=Count("id"), slots=Coalesce(Sum("concurrency"), 0))
    w.metric(
        "delco_workers",
        "gauge",
        "Workers por estado.",
        [({"status": r["status"]}, r["n"]) for r in workers],
    )
    slots = sum(r["slots"] for r in workers if r["status"] == "online")
    busy = TaskInstance.objects.filter(status="running").count()
    w.metric("delco_worker_slots", "gauge", "Vagas dos workers online.", [({}, slots)])
    w.metric("delco_worker_slots_busy", "gauge", "Vagas ocupadas por tarefas rodando.", [({}, busy)])
    schedulers = bus.schedulers() if bus is not None else []
    w.metric("delco_schedulers", "gauge", "Agendadores com sinal recente.", [({}, len(schedulers))])
    deliveries = IntegrationDelivery.objects.filter(
        Q(status="pending") | Q(created_at__gt=day)
    ).values("status").annotate(n=Count("id"))
    w.metric(
        "delco_deliveries",
        "gauge",
        "Entregas de notificação pendentes e das últimas 24 horas.",
        [({"status": d["status"]}, d["n"]) for d in deliveries],
    )
    errors = bus.load_errors() if bus is not None else {}
    w.metric("delco_workflow_load_errors", "gauge", "Arquivos de workflow com erro.", [({}, len(errors))])
    return w.text()
