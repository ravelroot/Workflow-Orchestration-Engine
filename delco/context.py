from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

LEVELS = ("debug", "info", "warning", "error")


@dataclass
class TaskContext:
    workflow: str
    run_id: int
    task_name: str
    task_instance_id: int
    attempt: int
    max_attempts: int
    params: dict[str, Any] = field(default_factory=dict)
    upstream: dict[str, Any] = field(default_factory=dict)
    logical_date: datetime | None = None
    _send: Callable[[tuple], None] | None = field(default=None, repr=False)

    @property
    def idempotency_key(self) -> str:
        return f"delco:{self.workflow}:{self.run_id}:{self.task_name}"

    @property
    def is_retry(self) -> bool:
        return self.attempt > 1

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts

    def log(self, message: Any, level: str = "info") -> None:
        level = level if level in LEVELS else "info"
        text = str(message)
        if self._send:
            for line in text.splitlines() or [""]:
                self._send(("log", level, line))
        else:
            print(f"[{level}] {text}")

    def warning(self, message: Any) -> None:
        self.log(message, "warning")

    def error(self, message: Any) -> None:
        self.log(message, "error")

    def progress(self, percent: float, message: str | None = None) -> None:
        value = max(0, min(100, int(round(percent))))
        if self._send:
            self._send(("progress", value))
        if message:
            self.log(message)

    def skip(self, reason: str = "Pulada pela própria tarefa.") -> None:
        from .sdk import Skip

        raise Skip(reason)

    def fail(self, reason: str, *, retry: bool = False) -> None:
        from .sdk import Fail

        raise Fail(reason, retry=retry)
