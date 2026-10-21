"""Agendador: sincroniza definições, cria execuções, libera tarefas e recupera falhas.

Pode haver mais de um agendador ao mesmo tempo: as linhas são travadas com
``FOR UPDATE SKIP LOCKED`` e as execuções agendadas têm chave única por data
lógica, então nada é criado ou despachado em dobro.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

from django.db import connection

from . import store
from .bus import Bus
from .config import Settings
from .db import release
from .loader import load_folder

log = logging.getLogger("delco.scheduler")

HOUSEKEEPING_LOCK = "delco:housekeeping"
HOUSEKEEPING_EVERY = 600.0


class Scheduler:
    def __init__(self, bus: Bus, settings: Settings, *, workflows_dir: Path | None = None) -> None:
        self.bus = bus
        self.settings = settings
        self.workflows_dir = Path(workflows_dir or settings.workflows_dir)
        self.stop_event = threading.Event()

    # ---------------------------------------------------------------- partes
    def sync(self) -> dict:
        result = load_folder(self.workflows_dir)
        summary = store.sync_definitions(result)
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
        for run_id in created:
            self.bus.publish("run", id=run_id, status="queued")
        for run_id in started:
            self.bus.publish("run", id=run_id, status="running")
        for task_id, run_id, name in result.skipped:
            self.bus.publish("task", id=task_id, run_id=run_id, name=name, status="skipped")
        for run_id, outcome in result.finished:
            self.bus.publish("run", id=run_id, status=outcome)
        if result.finished:
            # uma execução terminou: pode haver outra na fila esperando vaga
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
            except Exception as exc:  # o Redis caiu: a recuperação republica depois
                log.warning("Não foi possível publicar a tarefa %s na fila %s: %s", task_id, queue, exc)
                continue
            self.bus.publish("task", id=task_id, run_id=run_id, name=name, status="queued")

    def recover(self) -> dict:
        expired = store.recover_expired_leases()
        for t in expired:
            log.warning("Tarefa %s (execução %s) sem heartbeat: %s", t["task_name"], t["run_id"], t["status"])
            self.bus.publish("task", id=t["id"], run_id=t["run_id"], name=t["task_name"], status=t["status"])
        republished = 0
        for t in store.stale_queued(self.settings.redeliver_after_seconds):
            try:
                if self.bus.exists(t["queue"], t["queue_message_id"]):
                    continue  # a mensagem está na fila esperando um worker livre
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

    def housekeeping(self) -> dict | None:
        """Limpeza periódica; a trava nomeada (GET_LOCK) garante um agendador por vez."""
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

    # ----------------------------------------------------------------- laço
    def run_forever(self) -> None:  # pragma: no cover - laço do processo
        def handle(signum, _frame):
            log.info("Sinal %s recebido: parando o agendador.", signum)
            self.stop_event.set()
            self.bus.wake()

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)
        log.info("Agendador iniciado · pasta de workflows: %s", self.workflows_dir)
        self.sync()
        if self.settings.seed:
            from .seed import seed_history

            try:
                seed_history()
            except Exception:
                log.exception("Não foi possível criar o histórico de demonstração")
        last_sync = last_recovery = time.monotonic()
        last_housekeeping = 0.0
        while not self.stop_event.is_set():
            now = time.monotonic()
            try:
                if now - last_sync >= self.settings.sync_seconds:
                    last_sync = now
                    self.sync()
                self.tick()
                if now - last_recovery >= self.settings.recovery_seconds:
                    last_recovery = now
                    self.recover()
                if now - last_housekeeping >= HOUSEKEEPING_EVERY:
                    last_housekeeping = now
                    self.housekeeping()
            except Exception:
                log.exception("Erro no ciclo do agendador; tentando de novo em instantes")
                self.stop_event.wait(2)
            finally:
                release()  # devolve a conexão ao pool entre um ciclo e outro
            self.bus.wait_wake(self.settings.tick_seconds)
        log.info("Agendador parado.")
