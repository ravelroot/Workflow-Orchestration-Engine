from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

from django.db import connection

from . import __version__, integrations, store
from .bus import Bus
from .config import Settings
from .db import release
from .loader import load_folder
from .schedule import utcnow

log = logging.getLogger("delco.scheduler")

HOUSEKEEPING_LOCK = "delco:housekeeping"
HOUSEKEEPING_EVERY = 600.0
DELIVERY_EVERY = 2.0
BEAT_EVERY = 5.0


class Scheduler:
    def __init__(self, bus: Bus, settings: Settings, *, workflows_dir: Path | None = None) -> None:
        self.bus = bus
        self.settings = settings
        self.workflows_dir = Path(workflows_dir or settings.workflows_dir)
        self.stop_event = threading.Event()
        host = (socket.gethostname().split(".")[0] or "scheduler")[:32]
        self.id = f"{host}-{os.getpid()}"
        self.started_at = utcnow()
        self.last_sync: dict = {}
        self.last_error: str | None = None
        self.cycles = 0
        self.health_file = Path(settings.health_dir) / "delco-scheduler.health"

    def sync(self) -> dict:
        result = load_folder(self.workflows_dir)
        summary = store.sync_definitions(result)
        self.last_sync = {
            "at": utcnow().isoformat(),
            "workflows": len(summary["workflows"]),
            "errors": len(result.errors),
        }
        self.bus.set_load_errors(result.errors)
        for path, err in result.errors.items():
            log.error("Erro ao carregar %s: %s", path, err.strip().splitlines()[-1] if err.strip() else err)
        if summary["new_versions"] or summary["deactivated"]:
            log.info(
                "Workflows sincronizados: novas versões %s, desativados %s",
                summary["new_versions"],
                summary["deactivated"],
            )
            self.bus.publish("workflows", changed=summary["new_versions"] + summary["deactivated"])
        return summary

    def tick(self) -> dict:
        created = store.schedule_due()
        started = store.promote_runs()
        result = store.dispatch()
        self.enqueue(result.ready)
        names = store.run_workflow_names(created + started + [r for r, _ in result.finished])
        for run_id in created:
            self.bus.publish("run", id=run_id, status="queued", workflow=names.get(run_id))
        for run_id in started:
            self.bus.publish("run", id=run_id, status="running", workflow=names.get(run_id))
        for task_id, run_id, name in result.skipped:
            self.bus.publish("task", id=task_id, run_id=run_id, name=name, status="skipped")
        for run_id, outcome in result.finished:
            self.bus.publish("run", id=run_id, status=outcome, workflow=names.get(run_id))
        if result.finished:
            started_more = store.promote_runs()
            for run_id in started_more:
                self.bus.publish("run", id=run_id, status="running")
            if started_more:
                self.enqueue(store.dispatch().ready)
        return {
            "created": created,
            "started": started,
            "ready": len(result.ready),
            "finished": result.finished,
        }

    def enqueue(self, ready: list[tuple[int, str, int, str]]) -> None:
        for task_id, queue, run_id, name in ready:
            try:
                message_id = self.bus.enqueue(queue, task_id)
                store.set_message_id(task_id, message_id)
            except Exception as exc:
                log.warning("Não foi possível publicar a tarefa %s na fila %s: %s", task_id, queue, exc)
                continue
            self.bus.publish("task", id=task_id, run_id=run_id, name=name, status="queued")

    def recover(self) -> dict:
        for run in store.expire_runs():
            log.warning("Execução %s (%s) passou do tempo limite e foi encerrada", run["id"], run["workflow"])
            self.bus.publish("run", id=run["id"], status="failed", workflow=run["workflow"])
        expired = store.recover_expired_leases()
        for t in expired:
            log.warning("Tarefa %s (execução %s) sem heartbeat: %s", t["task_name"], t["run_id"], t["status"])
            self.bus.publish("task", id=t["id"], run_id=t["run_id"], name=t["task_name"], status=t["status"])
        republished = 0
        for t in store.stale_queued(self.settings.redeliver_after_seconds):
            try:
                if self.bus.exists(t["queue"], t["queue_message_id"]):
                    continue
                message_id = self.bus.enqueue(t["queue"], t["id"])
                store.touch_queued(t["id"], message_id)
                republished += 1
            except Exception as exc:
                log.warning("Falha ao republicar a tarefa %s: %s", t["id"], exc)
        if republished:
            log.warning("%s tarefa(s) republicada(s) na fila (mensagem perdida)", republished)
        silent = max(30.0, self.settings.heartbeat_seconds * 4)
        offline = store.mark_offline_workers(silent)
        for worker_id in offline:
            log.warning("Worker %s sem sinal: marcado como offline", worker_id)
            self.bus.publish("worker", id=worker_id, status="offline")
        if expired:
            self.bus.wake()
        return {"expired": len(expired), "republished": republished, "offline": offline}

    def beat(self) -> None:
        self.bus.scheduler_beat(
            {
                "id": self.id,
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "version": __version__,
                "started_at": self.started_at.isoformat(),
                "last_beat_at": utcnow().isoformat(),
                "workflows_dir": str(self.workflows_dir),
                "cycles": self.cycles,
                "last_sync": self.last_sync,
                "last_error": self.last_error,
            },
            ttl=int(max(15, BEAT_EVERY * 4)),
        )
        try:
            self.health_file.parent.mkdir(parents=True, exist_ok=True)
            self.health_file.write_text(f"{self.id} {time.time():.0f}\n")
        except OSError:
            pass

    def deliver(self) -> dict:
        try:
            return integrations.deliver_due()
        except Exception as exc:
            log.warning("Falha ao enviar notificações: %s", exc)
            return {"sent": 0, "failed": 0, "retry": 0}

    def housekeeping(self) -> dict | None:
        with connection.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0)", [HOUSEKEEPING_LOCK])
            if not cur.fetchone()[0]:
                return None
            try:
                removed = store.housekeeping(self.settings.retention_days)
            finally:
                cur.execute("SELECT RELEASE_LOCK(%s)", [HOUSEKEEPING_LOCK])
        if any(removed.values()):
            log.info("Limpeza: %s", removed)
        return removed

    def run_forever(self) -> None:
        def handle(signum, _frame):
            log.info("Sinal %s recebido: parando o agendador.", signum)
            self.stop_event.set()
            self.bus.wake()

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)
        log.info("Agendador %s iniciado · pasta de workflows: %s", self.id, self.workflows_dir)
        self.beat()
        self.sync()
        if self.settings.seed:
            from .seed import seed_history

            try:
                seed_history()
            except Exception:
                log.exception("Não foi possível criar o histórico de demonstração")
        last_sync = last_recovery = time.monotonic()
        last_housekeeping = last_delivery = last_beat = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            self.cycles += 1
            if now - last_beat >= BEAT_EVERY:
                last_beat = now
                self.beat()
            try:
                if now - last_sync >= self.settings.sync_seconds:
                    last_sync = now
                    self.sync()
                self.tick()
                if now - last_recovery >= self.settings.recovery_seconds:
                    last_recovery = now
                    self.recover()
                if now - last_delivery >= DELIVERY_EVERY:
                    last_delivery = now
                    self.deliver()
                if now - last_housekeeping >= HOUSEKEEPING_EVERY:
                    last_housekeeping = now
                    self.housekeeping()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.exception("Erro no ciclo do agendador; tentando de novo em instantes")
                self.stop_event.wait(2)
            finally:
                release()
            self.bus.wait_wake(self.settings.tick_seconds)
        self.bus.scheduler_gone(self.id)
        log.info("Agendador parado.")
