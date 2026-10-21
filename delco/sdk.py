"""Definição de workflows como código.

Exemplo::

    from delco import Workflow

    wf = Workflow("relatorio", schedule="0 7 * * *", description="Relatório diário")

    @wf.task(retries=3, timeout=120)
    def extrair(ctx):
        return {"linhas": 120}

    @wf.task(depends_on=[extrair])
    def publicar(ctx):
        ctx.log(f"{ctx.upstream['extrair']['linhas']} linhas publicadas")
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .schedule import validate_cron, validate_timezone

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
TRIGGER_RULES = ("all_success", "all_done", "one_failed")

DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 5.0
DEFAULT_BACKOFF = 2.0
DEFAULT_MAX_RETRY_DELAY = 600.0
DEFAULT_TIMEOUT = 300.0


class WorkflowError(ValueError):
    """Definição inválida de workflow ou tarefa."""


@dataclass
class Task:
    name: str
    fn: Callable[..., Any]
    upstream: list[str] = field(default_factory=list)
    retries: int = DEFAULT_RETRIES
    retry_delay: float = DEFAULT_RETRY_DELAY
    retry_backoff: float = DEFAULT_BACKOFF
    max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY
    timeout: float | None = DEFAULT_TIMEOUT
    queue: str = "default"
    trigger_rule: str = "all_success"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        try:
            line = inspect.getsourcelines(self.fn)[1]
        except (OSError, TypeError):
            line = None
        return {
            "name": self.name,
            "upstream": sorted(self.upstream),
            "retries": self.retries,
            "retry_delay": self.retry_delay,
            "retry_backoff": self.retry_backoff,
            "max_retry_delay": self.max_retry_delay,
            "timeout": self.timeout,
            "queue": self.queue,
            "trigger_rule": self.trigger_rule,
            "description": self.description,
            "function": getattr(self.fn, "__qualname__", self.name),
            "line": line,
        }


TaskRef = str | Callable[..., Any] | Task


class Workflow:
    """Um grafo acíclico de tarefas com agendamento opcional."""

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        schedule: str | None = None,
        timezone: str = "America/Sao_Paulo",
        max_active_runs: int = 1,
        params: dict[str, Any] | None = None,
        tags: Iterable[str] = (),
        default_retries: int = DEFAULT_RETRIES,
        default_retry_delay: float = DEFAULT_RETRY_DELAY,
        default_timeout: float | None = DEFAULT_TIMEOUT,
        default_queue: str = "default",
    ) -> None:
        if not NAME_RE.match(name or ""):
            raise WorkflowError(
                f"Nome de workflow inválido: {name!r}. Use letras minúsculas, números e _ (até 63 caracteres)."
            )
        if schedule is not None:
            validate_cron(schedule)
        validate_timezone(timezone)
        if max_active_runs < 1:
            raise WorkflowError("max_active_runs precisa ser 1 ou mais.")
        if params is not None and not isinstance(params, dict):
            raise WorkflowError("params precisa ser um dicionário.")
        self.name = name
        self.description = (description or "").strip()
        self.schedule = schedule
        self.timezone = timezone
        self.max_active_runs = max_active_runs
        self.params = dict(params or {})
        self.tags = sorted({t.strip() for t in tags if t and t.strip()})
        self.default_retries = default_retries
        self.default_retry_delay = default_retry_delay
        self.default_timeout = default_timeout
        self.default_queue = default_queue
        self.tasks: dict[str, Task] = {}
        self.source_file: str | None = None

    # ------------------------------------------------------------------ montagem
    def task(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        depends_on: Iterable[TaskRef] = (),
        retries: int | None = None,
        retry_delay: float | None = None,
        backoff: float = DEFAULT_BACKOFF,
        max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY,
        timeout: float | None | object = ...,
        queue: str | None = None,
        trigger_rule: str = "all_success",
        description: str | None = None,
    ):
        """Decorador que registra uma função como tarefa do workflow."""

        def register(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_task(
                name or func.__name__,
                func,
                depends_on=depends_on,
                retries=retries,
                retry_delay=retry_delay,
                backoff=backoff,
                max_retry_delay=max_retry_delay,
                timeout=timeout,
                queue=queue,
                trigger_rule=trigger_rule,
                description=description,
            )
            return func

        return register(fn) if fn is not None else register

    def add_task(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        depends_on: Iterable[TaskRef] = (),
        retries: int | None = None,
        retry_delay: float | None = None,
        backoff: float = DEFAULT_BACKOFF,
        max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY,
        timeout: float | None | object = ...,
        queue: str | None = None,
        trigger_rule: str = "all_success",
        description: str | None = None,
    ) -> Task:
        """Registra uma tarefa sem decorador (útil para gerar tarefas em laço)."""
        if not NAME_RE.match(name or ""):
            raise WorkflowError(f"Nome de tarefa inválido em {self.name}: {name!r}.")
        if name in self.tasks:
            raise WorkflowError(f"A tarefa {name!r} foi definida duas vezes em {self.name}.")
        if not callable(fn):
            raise WorkflowError(f"A tarefa {name!r} precisa ser uma função.")
        if trigger_rule not in TRIGGER_RULES:
            raise WorkflowError(f"trigger_rule inválida em {name!r}: use {', '.join(TRIGGER_RULES)}.")
        retries = self.default_retries if retries is None else retries
        retry_delay = self.default_retry_delay if retry_delay is None else retry_delay
        timeout = self.default_timeout if timeout is ... else timeout
        if retries < 0 or retries > 100:
            raise WorkflowError(f"retries de {name!r} precisa estar entre 0 e 100.")
        if retry_delay < 0 or backoff < 1 or max_retry_delay < 0:
            raise WorkflowError(f"Espera entre tentativas inválida em {name!r}.")
        if timeout is not None and (not isinstance(timeout, int | float) or timeout <= 0):
            raise WorkflowError(f"timeout de {name!r} precisa ser maior que zero (ou None).")
        task = Task(
            name=name,
            fn=fn,
            upstream=[self._ref_name(d) for d in depends_on],
            retries=int(retries),
            retry_delay=float(retry_delay),
            retry_backoff=float(backoff),
            max_retry_delay=float(max_retry_delay),
            timeout=float(timeout) if timeout is not None else None,
            queue=(queue or self.default_queue).strip(),
            trigger_rule=trigger_rule,
            description=(description if description is not None else inspect.getdoc(fn) or "").strip(),
        )
        if len(set(task.upstream)) != len(task.upstream):
            raise WorkflowError(f"Dependência repetida em {name!r}.")
        self.tasks[name] = task
        try:
            fn.__delco_task__ = name  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass
        return task

    @staticmethod
    def _ref_name(ref: TaskRef) -> str:
        if isinstance(ref, str):
            return ref
        if isinstance(ref, Task):
            return ref.name
        name = getattr(ref, "__delco_task__", None)
        if not name:
            raise WorkflowError(f"{ref!r} não é uma tarefa registrada; passe o nome ou a função decorada.")
        return name

    # ---------------------------------------------------------------- validação
    def validate(self) -> None:
        if not self.tasks:
            raise WorkflowError(f"O workflow {self.name} não tem tarefas.")
        for task in self.tasks.values():
            for dep in task.upstream:
                if dep not in self.tasks:
                    raise WorkflowError(f"{self.name}.{task.name} depende de {dep!r}, que não existe.")
                if dep == task.name:
                    raise WorkflowError(f"{self.name}.{task.name} depende de si mesma.")
        self.topological_order()

    def topological_order(self) -> list[str]:
        indegree = {n: len(t.upstream) for n, t in self.tasks.items()}
        children: dict[str, list[str]] = {n: [] for n in self.tasks}
        for t in self.tasks.values():
            for dep in t.upstream:
                if dep in children:
                    children[dep].append(t.name)
        order: list[str] = []
        ready = sorted([n for n, d in indegree.items() if d == 0], key=list(self.tasks).index)
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.tasks):
            stuck = sorted(n for n in self.tasks if n not in order)
            raise WorkflowError(f"Ciclo de dependências em {self.name}: {', '.join(stuck)}.")
        return order

    def graph(self) -> dict[str, Any]:
        self.validate()
        return {
            "workflow": self.name,
            "tasks": [self.tasks[n].to_dict() for n in self.topological_order()],
        }

    def fingerprint(self) -> str:
        data = self.graph()
        data["tasks"] = [{k: v for k, v in t.items() if k != "line"} for t in data["tasks"]]
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "schedule": self.schedule,
            "timezone": self.timezone,
            "max_active_runs": self.max_active_runs,
            "params": self.params,
            "tags": self.tags,
            "source_file": self.source_file,
            "graph": self.graph(),
            "fingerprint": self.fingerprint(),
        }

    def __repr__(self) -> str:
        return f"<Workflow {self.name} ({len(self.tasks)} tarefas)>"


def descendants(graph: dict[str, Any], start: str) -> set[str]:
    """Tarefas que dependem (direta ou indiretamente) de ``start``."""
    children: dict[str, list[str]] = {}
    for t in graph["tasks"]:
        for dep in t["upstream"]:
            children.setdefault(dep, []).append(t["name"])
    seen: set[str] = set()
    stack = [start]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen
