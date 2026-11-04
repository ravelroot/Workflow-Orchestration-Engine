from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .context import TaskContext
from .sdk import Skip


class OutputTooLarge(ValueError):
    pass


def _pump_fd(fd: int, level: str, send) -> threading.Thread:
    read_end, write_end = os.pipe()
    os.dup2(write_end, fd)
    os.close(write_end)

    def pump() -> None:
        with os.fdopen(read_end, "rb") as stream:
            for raw in stream:
                send(("log", level, raw.decode("utf-8", errors="replace").rstrip("\r\n")))

    thread = threading.Thread(target=pump, name=f"delco-pump-{fd}", daemon=True)
    thread.start()
    return thread


class _PipeHandler(logging.Handler):
    MAP = {logging.DEBUG: "debug", logging.INFO: "info", logging.WARNING: "warning"}

    def __init__(self, send) -> None:
        super().__init__(logging.DEBUG)
        self.send = send

    def emit(self, record: logging.LogRecord) -> None:
        level = "error" if record.levelno >= logging.ERROR else self.MAP.get(record.levelno, "info")
        try:
            self.send(("log", level, self.format(record)))
        except Exception:
            pass


def call_task(fn, ctx: TaskContext) -> Any:
    try:
        wants_ctx = len(inspect.signature(fn).parameters) > 0
    except (TypeError, ValueError):
        wants_ctx = True
    result = fn(ctx) if wants_ctx else fn()
    if inspect.isawaitable(result):
        result = asyncio.run(_await(result))
    return result


async def _await(awaitable):
    return await awaitable


def child_main(conn: Connection, payload: dict[str, Any]) -> None:
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    lock = threading.Lock()

    def send(message: tuple) -> None:
        with lock:
            try:
                conn.send(message)
            except (BrokenPipeError, OSError):
                pass

    pumps = []
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        pumps = [_pump_fd(1, "info", send), _pump_fd(2, "warning", send)]
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (OSError, AttributeError, ValueError):
        pumps = []
    root = logging.getLogger()
    root.handlers[:] = [_PipeHandler(send)]
    root.setLevel(logging.INFO)

    message: tuple
    task = None
    try:
        from .loader import find_task

        task = find_task(
            Path(payload["workflows_dir"]), payload["source_file"], payload["workflow"], payload["task"]
        )
        logical = payload.get("logical_date")
        ctx = TaskContext(
            workflow=payload["workflow"],
            run_id=payload["run_id"],
            task_name=payload["task"],
            task_instance_id=payload["task_id"],
            attempt=payload["attempt"],
            max_attempts=payload["max_attempts"],
            params=payload.get("params") or {},
            upstream=payload.get("upstream") or {},
            logical_date=datetime.fromisoformat(logical) if logical else None,
            _send=send,
        )
        result = call_task(task.fn, ctx)
        encoded = json.dumps(result, default=str, ensure_ascii=False)
        if len(encoded.encode()) > payload["max_output_bytes"]:
            raise OutputTooLarge(
                f"O resultado tem {len(encoded.encode())} bytes; o limite é {payload['max_output_bytes']}. "
                "Grave dados grandes em arquivo ou banco e devolva só a referência."
            )
        message = ("result", json.loads(encoded))
    except BaseException as exc:
        if isinstance(exc, Skip):
            message = ("skip", exc.reason)
        else:
            retryable = not isinstance(exc, OutputTooLarge) and (task.should_retry(exc) if task else True)
            message = (
                "error",
                type(exc).__name__,
                str(exc) or type(exc).__name__,
                traceback.format_exc(),
                retryable,
            )
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
    if pumps:
        try:
            os.close(1)
            os.close(2)
        except OSError:
            pass
        for p in pumps:
            p.join(timeout=1)
    send(message)
    conn.close()
