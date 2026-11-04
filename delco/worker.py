from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import __version__, store
from .bus import Bus
from .config import Settings
from .db import release
from .schedule import utcnow

log = logging.getLogger("delco.worker")

LOG_FLUSH_SECONDS = 0.4
LOG_FLUSH_LINES = 50
AUTOCLAIM_EVERY = 15.0


def mp_context() -> mp.context.BaseContext:
    method = os.environ.get("DELCO_START_METHOD") or (
        "forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn"
    )
    ctx = mp.get_context(method)
    if method == "forkserver":
        ctx.set_forkserver_preload(["delco.runner", "delco.loader", "delco.context"])
    return ctx


def default_worker_id() -> str:
    if os.environ.get("DELCO_WORKER_NAME"):
        return os.environ["DELCO_WORKER_NAME"][:48]
    host = (socket.gethostname().split(".")[0] or "worker")[:32]
    return f"{host}-{os.getpid()}"


class _Execution:
    def __init__(self, worker: Worker, claim: store.Claim) -> None:
        self.worker = worker
        self.claim = claim
        self.buffer: list[tuple[datetime, str, str]] = []
        self.lines = 0
        self.truncated = False
        self.progress: int | None = None
        self.progress_sent: int | None = None
        self.proc: mp.process.BaseProcess | None = None
        self.retry = True

    def add_log(self, level: str, message: str) -> None:
        limit = self.worker.settings.max_log_lines
        if self.lines >= limit:
            if not self.truncated:
                self.truncated = True
                self.buffer.append(
                    (utcnow(), "warning", f"Log truncado: limite de {limit} linhas por tentativa.")
                )
            return
        self.lines += 1
        self.buffer.append((utcnow(), level, message))

    def flush_logs(self) -> None:
        if not self.buffer:
            return
        lines, self.buffer = self.buffer, []
        try:
            rows = store.append_logs(self.claim.attempt_id, lines)
        except Exception as exc:
            log.warning("Não foi possível gravar logs da tentativa %s: %s", self.claim.attempt_id, exc)
            return
        self.worker.bus.publish(
            "log",
            attempt_id=self.claim.attempt_id,
            task_id=self.claim.task_id,
            run_id=self.claim.run_id,
            lines=[
                {
                    "id": r["id"],
                    "at": r["logged_at"].isoformat(),
                    "level": r["level"],
                    "message": r["message"],
                }
                for r in rows
            ],
        )

    def _kill(self) -> None:
        proc = self.proc
        if proc is None or not proc.is_alive():
            return
        try:
            if hasattr(os, "killpg") and proc.pid:
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            proc.terminate()
        proc.join(3)
        if proc.is_alive():
            try:
                if hasattr(os, "killpg") and proc.pid:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            proc.join(3)

    def run(self) -> tuple[str, object, str | None, str | None]:
        w, c = self.worker, self.claim
        ctx = w.mp
        recv_end, send_end = ctx.Pipe(duplex=False)
        payload = {
            "workflows_dir": str(w.workflows_dir),
            "source_file": c.source_file,
            "workflow": c.workflow,
            "task": c.task_name,
            "task_id": c.task_id,
            "run_id": c.run_id,
            "attempt": c.attempt,
            "max_attempts": c.max_attempts,
            "params": c.params,
            "upstream": c.upstream,
            "logical_date": c.logical_date.isoformat() if c.logical_date else None,
            "max_output_bytes": w.settings.max_output_bytes,
        }
        from .runner import child_main

        self.proc = ctx.Process(
            target=child_main, args=(send_end, payload), name=f"delco-{c.workflow}-{c.task_name}"
        )
        self.proc.start()
        send_end.close()

        started = time.monotonic()
        deadline = started + c.timeout if c.timeout else None
        next_beat = started + w.settings.heartbeat_seconds
        last_flush = started
        final: tuple | None = None
        outcome: str | None = None

        while True:
            try:
                if recv_end.poll(0.2):
                    for _ in range(1000):
                        msg = recv_end.recv()
                        kind = msg[0]
                        if kind == "log":
                            self.add_log(msg[1], msg[2])
                        elif kind == "progress":
                            self.progress = int(msg[1])
                        elif kind in ("result", "error", "skip"):
                            final = msg
                        if not recv_end.poll(0):
                            break
            except (EOFError, OSError):
                break
            now = time.monotonic()
            if now - last_flush >= LOG_FLUSH_SECONDS or len(self.buffer) >= LOG_FLUSH_LINES:
                self.flush_logs()
                last_flush = now
                if self.progress is not None and self.progress != self.progress_sent:
                    self.progress_sent = self.progress
                    w.bus.publish(
                        "task",
                        id=c.task_id,
                        run_id=c.run_id,
                        name=c.task_name,
                        status="running",
                        progress=self.progress,
                        attempt=c.attempt,
                    )
            if not self.proc.is_alive() and not recv_end.poll(0):
                break
            if now >= next_beat:
                next_beat = now + w.settings.heartbeat_seconds
                try:
                    mine, cancel = store.task_heartbeat(
                        c.task_id, c.attempt, w.settings.lease_seconds, self.progress
                    )
                except Exception as exc:
                    log.warning("Heartbeat falhou para a tarefa %s: %s", c.task_id, exc)
                    mine, cancel = True, False
                if not mine:
                    outcome = "lost"
                    self.add_log(
                        "warning", "A posse da tarefa foi perdida (heartbeat atrasado); tentativa encerrada."
                    )
                    break
                if cancel:
                    outcome = "cancelled"
                    self.add_log("warning", "Cancelamento pedido pelo operador; encerrando a tarefa.")
                    break
            if deadline and now >= deadline:
                outcome = "timed_out"
                self.add_log("error", f"Tempo limite de {c.timeout:g} s atingido; tarefa encerrada.")
                break
            if w.force_stop.is_set():
                outcome = "lost" if w.crashed else "interrupted"
                self.add_log("warning", "Worker desligado antes do fim da tarefa; ela será retomada.")
                break

        if outcome is None:
            self.proc.join(5)
        self._kill()
        try:
            recv_end.close()
        except OSError:
            pass
        if outcome == "timed_out":
            return outcome, None, f"Tempo limite de {c.timeout:g} s atingido.", None
        if outcome == "cancelled":
            return outcome, None, "Cancelada pelo operador.", None
        if outcome == "lost":
            return outcome, None, None, None
        if outcome == "interrupted":
            return "interrupted", None, "O worker foi desligado durante a tentativa.", None
        if final is None:
            code = self.proc.exitcode
            return "failed", None, f"O processo da tarefa terminou sem resultado (código {code}).", None
        if final[0] == "result":
            return "succeeded", final[1], None, None
        if final[0] == "skip":
            self.add_log("info", f"Tarefa pulada: {final[1]}")
            return "skipped", None, final[1], None
        exc_type, exc_msg, tb = final[1], final[2], final[3]
        self.retry = bool(final[4]) if len(final) > 4 else True
        self.add_log("error", f"{exc_type}: {exc_msg}")
        if not self.retry and c.attempt < c.max_attempts:
            self.add_log("warning", "Erro marcado como definitivo: a tarefa não será repetida.")
        return "failed", None, f"{exc_type}: {exc_msg}", tb


class Worker:
    def __init__(
        self,
        bus: Bus,
        settings: Settings,
        *,
        worker_id: str | None = None,
        queues: list[str] | None = None,
        concurrency: int | None = None,
        workflows_dir: Path | None = None,
    ) -> None:
        self.bus = bus
        self.settings = settings
        self.id = worker_id or default_worker_id()
        self.queues = list(queues or settings.worker_queues)
        self.concurrency = concurrency or settings.worker_concurrency
        self.workflows_dir = Path(workflows_dir or settings.workflows_dir).resolve()
        self.mp = mp_context()
        self.pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="delco-slot")
        self.active = 0
        self.slots = threading.Condition()
        self.stopping = threading.Event()
        self.force_stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.executed = 0
        self.crashed = False
        self.paused = threading.Event()
        self.health_file = Path(settings.health_dir) / "delco-worker.health"

    def start(self) -> None:
        store.register_worker(
            self.id, socket.gethostname(), os.getpid(), self.queues, self.concurrency, __version__
        )
        release()
        for q in self.queues:
            self.bus.ensure_group(q, force=True)
        self.check_pause()
        self.bus.publish("worker", id=self.id, status="paused" if self.paused.is_set() else "online")
        for target, name in ((self._consume, "consumer"), (self._heartbeat, "heartbeat")):
            t = threading.Thread(target=target, name=f"delco-{name}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Worker %s online · filas %s · %s vagas", self.id, ",".join(self.queues), self.concurrency)

    def stop(self, grace: float | None = None) -> None:
        if self.stopping.is_set():
            return
        grace = self.settings.worker_grace_seconds if grace is None else grace
        self.stopping.set()
        try:
            store.worker_heartbeat(self.id, "stopping")
            self.bus.publish("worker", id=self.id, status="stopping")
        except Exception:
            pass
        finally:
            release()
        deadline = time.monotonic() + grace
        with self.slots:
            while self.active > 0 and time.monotonic() < deadline:
                self.slots.wait(0.2)
        if self.active > 0:
            self.force_stop.set()
        self.pool.shutdown(wait=True)
        for t in self._threads:
            t.join(timeout=3)
        try:
            store.worker_stopped(self.id)
            self.bus.publish("worker", id=self.id, status="offline")
        except Exception:
            pass
        finally:
            release()
        log.info("Worker %s parado (%s tentativas executadas)", self.id, self.executed)

    def kill(self) -> None:
        self.crashed = True
        self.stopping.set()
        self.force_stop.set()
        self.pool.shutdown(wait=True)
        for t in self._threads:
            t.join(timeout=3)

    def run_forever(self) -> None:
        done = threading.Event()

        def handle(signum, _frame):
            log.info("Sinal %s recebido: terminando as tarefas em andamento…", signum)
            done.set()

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)
        self.start()
        while not done.wait(1):
            pass
        self.stop()

    def _heartbeat(self) -> None:
        self.touch_health()
        while not self.stopping.wait(self.settings.heartbeat_seconds) and not self.crashed:
            self.check_pause()
            try:
                store.worker_heartbeat(self.id, "paused" if self.paused.is_set() else "online")
                self.touch_health()
            except Exception as exc:
                log.warning("Heartbeat do worker falhou: %s", exc)
            finally:
                release()

    def check_pause(self) -> bool:
        try:
            wanted = self.bus.is_worker_paused(self.id)
        except Exception:
            return self.paused.is_set()
        if wanted and not self.paused.is_set():
            self.paused.set()
            log.info("Worker %s pausado: termina o que está rodando e não pega tarefas novas.", self.id)
            self.bus.publish("worker", id=self.id, status="paused")
        elif not wanted and self.paused.is_set():
            self.paused.clear()
            log.info("Worker %s retomado.", self.id)
            self.bus.publish("worker", id=self.id, status="online")
        return self.paused.is_set()

    def touch_health(self) -> None:
        try:
            self.health_file.parent.mkdir(parents=True, exist_ok=True)
            self.health_file.write_text(f"{self.id} {time.time():.0f}\n")
        except OSError:
            pass

    def _consume(self) -> None:
        last_autoclaim = 0.0
        while not self.stopping.is_set():
            if self.paused.is_set():
                self.stopping.wait(0.5)
                continue
            with self.slots:
                free = self.concurrency - self.active
                if free <= 0:
                    self.slots.wait(0.5)
                    continue
            try:
                messages = self.bus.read(self.id, self.queues, count=free, block_ms=1000)
                if time.monotonic() - last_autoclaim > AUTOCLAIM_EVERY:
                    last_autoclaim = time.monotonic()
                    idle_ms = int(self.settings.redeliver_after_seconds * 1000)
                    for q in self.queues:
                        messages += self.bus.autoclaim(self.id, q, idle_ms, count=free)
            except Exception as exc:
                log.warning("Falha ao ler a fila: %s", exc)
                self.stopping.wait(2)
                continue
            if messages and self.paused.is_set():
                self._give_back(messages)
                continue
            for queue, message_id, task_id in messages:
                if self.stopping.is_set():
                    break
                with self.slots:
                    self.active += 1
                self.pool.submit(self._slot, queue, message_id, task_id)

    def _give_back(self, messages: list[tuple[str, str, int]]) -> None:
        for queue, message_id, task_id in messages:
            try:
                new_id = self.bus.enqueue(queue, task_id)
                store.touch_queued(task_id, new_id)
                self.bus.ack(queue, message_id)
            except Exception as exc:
                log.warning("Não foi possível devolver a tarefa %s à fila: %s", task_id, exc)
            finally:
                release()

    def _slot(self, queue: str, message_id: str, task_id: int) -> None:
        try:
            self._process(queue, message_id, task_id)
        except Exception:
            log.exception("Erro inesperado ao processar a tarefa %s", task_id)
        finally:
            release()
            with self.slots:
                self.active -= 1
                self.slots.notify_all()

    def _process(self, queue: str, message_id: str, task_id: int) -> None:
        claim = store.claim(task_id, self.id, self.settings.lease_seconds)
        self.bus.ack(queue, message_id)
        if claim is None:
            return
        self.bus.publish(
            "task",
            id=claim.task_id,
            run_id=claim.run_id,
            name=claim.task_name,
            status="running",
            attempt=claim.attempt,
            worker=self.id,
        )
        execution = _Execution(self, claim)
        started = time.monotonic()
        execution.add_log("info", f"Tentativa {claim.attempt} de {claim.max_attempts} no worker {self.id}")
        outcome, output, error, tb = execution.run()
        duration = int((time.monotonic() - started) * 1000)
        if outcome == "succeeded":
            execution.add_log("info", f"Concluída em {duration / 1000:.2f} s")
        execution.flush_logs()
        self.executed += 1
        if outcome == "lost":
            return
        if outcome == "interrupted":
            outcome = "lost"
        status = store.finish(
            claim,
            outcome,
            output=output,
            error=error,
            traceback=tb,
            duration_ms=duration,
            retry=execution.retry,
        )
        if status is not None:
            self.bus.publish(
                "task",
                id=claim.task_id,
                run_id=claim.run_id,
                name=claim.task_name,
                status=status,
                attempt=claim.attempt,
            )
        self.bus.wake()
