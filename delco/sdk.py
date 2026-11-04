from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .errors import WorkflowError
from .params import Param, ParamError, infer, resolve
from .schedule import validate_cron, validate_timezone

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
TRIGGER_RULES = ("all_success", "all_done", "one_failed")

DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 5.0
DEFAULT_BACKOFF = 2.0
DEFAULT_MAX_RETRY_DELAY = 600.0
DEFAULT_TIMEOUT = 300.0


class Skip(Exception):
    def __init__(self, reason: str = "Pulada pela própria tarefa.") -> None:
        super().__init__(reason)
        self.reason = reason


class Fail(Exception):
    def __init__(self, reason: str, *, retry: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry = retry


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
    retry_on: tuple[type[BaseException], ...] | None = None

    def should_retry(self, exc: BaseException) -> bool:
        if isinstance(exc, Fail):
            return exc.retry
        if self.retry_on is None:
            return True
        return isinstance(exc, self.retry_on)

    def to_dict(self) -> dict[str, Any]:
        try:
            line = inspect.getsourcelines(self.fn)[1]
        except (OSError, TypeError):
            line = None
        out = {
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
        if self.retry_on is not None:
            out["retry_on"] = sorted(e.__name__ for e in self.retry_on)
        return out


TaskRef = str | Callable[..., Any] | Task


@dataclass
class LocalTaskResult:
    name: str
    status: str
    attempts: int = 0
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
    logs: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class LocalRun:
    workflow: str
    status: str
    params: dict[str, Any]
    tasks: dict[str, LocalTaskResult]
    duration_ms: int

    @property
    def outputs(self) -> dict[str, Any]:
        return {name: t.output for name, t in self.tasks.items() if t.status == "succeeded"}

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


def _retry_on(value: Any, name: str) -> tuple[type[BaseException], ...] | None:
    if value is None:
        return None
    items = value if isinstance(value, list | tuple | set) else (value,)
    out = []
    for item in items:
        if not (isinstance(item, type) and issubclass(item, BaseException)):
            raise WorkflowError(f"retry_on de {name!r} precisa listar classes de exceção, como (TimeoutError,).")
        out.append(item)
    if not out:
        raise WorkflowError(f"retry_on de {name!r} está vazio; use None para repetir qualquer erro.")
    return tuple(out)


class Workflow:
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
        run_timeout: float | None = None,
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
        if run_timeout is not None and (not isinstance(run_timeout, int | float) or run_timeout <= 0):
            raise WorkflowError("run_timeout precisa ser maior que zero (ou None).")
        self.name = name
        self.description = (description or "").strip()
        self.schedule = schedule
        self.timezone = timezone
        self.max_active_runs = max_active_runs
        self.run_timeout = float(run_timeout) if run_timeout is not None else None
        self.params: dict[str, Any] = {}
        self.params_schema: dict[str, dict[str, Any]] = {}
        for key, value in (params or {}).items():
            if not isinstance(key, str) or not key.strip():
                raise WorkflowError("Os nomes dos parâmetros precisam ser textos.")
            try:
                spec = infer(value)
            except ParamError as exc:
                raise WorkflowError(f"Parâmetro {key!r} de {name}: {exc}") from None
            self.params_schema[key] = spec
            if "default" in spec:
                self.params[key] = value.default if isinstance(value, Param) else value
            if schedule and spec.get("required") and spec.get("default") is None:
                raise WorkflowError(
                    f"O parâmetro {key!r} de {name} é obrigatório e não tem padrão, "
                    "mas o workflow é agendado: dê um valor padrão."
                )
        self.tags = sorted({t.strip() for t in tags if t and t.strip()})
        self.default_retries = default_retries
        self.default_retry_delay = default_retry_delay
        self.default_timeout = default_timeout
        self.default_queue = default_queue
        self.tasks: dict[str, Task] = {}
        self.source_file: str | None = None

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
        retry_on: Any = None,
    ):
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
                retry_on=retry_on,
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
        retry_on: Any = None,
    ) -> Task:
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
            retry_on=_retry_on(retry_on, name),
        )
        if len(set(task.upstream)) != len(task.upstream):
            raise WorkflowError(f"Dependência repetida em {name!r}.")
        self.tasks[name] = task
        try:
            fn.__delco_task__ = name
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
        out: dict[str, Any] = {
            "workflow": self.name,
            "tasks": [self.tasks[n].to_dict() for n in self.topological_order()],
        }
        if self.run_timeout is not None:
            out["run_timeout"] = self.run_timeout
        return out

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
            "params_schema": self.params_schema,
            "run_timeout": self.run_timeout,
            "tags": self.tags,
            "source_file": self.source_file,
            "graph": self.graph(),
            "fingerprint": self.fingerprint(),
        }

    def resolve_params(self, given: dict[str, Any] | None = None) -> dict[str, Any]:
        merged, errors = resolve(self.params_schema, self.params, given)
        if errors:
            raise WorkflowError("; ".join(f"{k.removeprefix('params.')}: {v}" for k, v in errors.items()))
        return merged

    def run_local(
        self,
        params: dict[str, Any] | None = None,
        *,
        logical_date: datetime | None = None,
        retries: bool = True,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> LocalRun:
        from .context import TaskContext
        from .runner import call_task

        self.validate()
        merged = self.resolve_params(params)
        emit = on_event or (lambda kind, data: None)
        results: dict[str, LocalTaskResult] = {}
        started_run = time.monotonic()
        status: dict[str, str] = {}
        for name in self.topological_order():
            task = self.tasks[name]
            verdict = _decide(task.trigger_rule, [status[u] for u in task.upstream])
            if verdict == "skip":
                status[name] = "skipped"
                results[name] = LocalTaskResult(name, "skipped", error="Dependência não terminou com sucesso.")
                emit("skipped", {"task": name})
                continue
            result = LocalTaskResult(name, "running")
            results[name] = result
            max_attempts = task.retries + 1 if retries else 1
            started = time.monotonic()
            emit("start", {"task": name})
            for attempt in range(1, max_attempts + 1):
                result.attempts = attempt

                def send(message: tuple, result: LocalTaskResult = result, name: str = name) -> None:
                    if message[0] == "log":
                        result.logs.append((message[1], message[2]))
                        emit("log", {"task": name, "level": message[1], "message": message[2]})
                    elif message[0] == "progress":
                        emit("progress", {"task": name, "percent": message[1]})

                ctx = TaskContext(
                    workflow=self.name,
                    run_id=0,
                    task_name=name,
                    task_instance_id=0,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    params=dict(merged),
                    upstream={u: results[u].output for u in task.upstream if u in results},
                    logical_date=logical_date,
                    _send=send,
                )
                try:
                    output = call_task(task.fn, ctx)
                    json.dumps(output, default=str)
                except Skip as exc:
                    result.status, result.error = "skipped", exc.reason
                    break
                except Exception as exc:
                    result.error = f"{type(exc).__name__}: {exc}"
                    emit("error", {"task": name, "attempt": attempt, "error": result.error})
                    if attempt < max_attempts and task.should_retry(exc):
                        continue
                    result.status = "failed"
                    break
                else:
                    result.status, result.output, result.error = "succeeded", output, None
                    break
            result.duration_ms = int((time.monotonic() - started) * 1000)
            status[name] = result.status
            emit("end", {"task": name, "status": result.status, "duration_ms": result.duration_ms, "error": result.error})
        outcome = "failed" if "failed" in status.values() else "succeeded"
        return LocalRun(self.name, outcome, merged, results, int((time.monotonic() - started_run) * 1000))

    def __repr__(self) -> str:
        return f"<Workflow {self.name} ({len(self.tasks)} tarefas)>"


def _decide(rule: str, upstream: list[str]) -> str:
    if rule == "all_done":
        return "run"
    if rule == "one_failed":
        return "run" if any(s == "failed" for s in upstream) else "skip"
    return "run" if all(s == "succeeded" for s in upstream) else "skip"


def descendants(graph: dict[str, Any], start: str) -> set[str]:
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


def diff_graphs(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    old_tasks = {t["name"]: t for t in (old or {}).get("tasks", [])}
    new_tasks = {t["name"]: t for t in new.get("tasks", [])}
    ignore = {"line", "function"}
    changed = []
    for name in new_tasks:
        if name in old_tasks:
            fields = sorted(
                k
                for k in set(old_tasks[name]) | set(new_tasks[name])
                if k not in ignore and old_tasks[name].get(k) != new_tasks[name].get(k)
            )
            if fields:
                changed.append({"task": name, "fields": fields})
    out = {
        "added": [n for n in new_tasks if n not in old_tasks],
        "removed": [n for n in old_tasks if n not in new_tasks],
        "changed": changed,
    }
    if (old or {}).get("run_timeout") != new.get("run_timeout"):
        out["run_timeout"] = {"before": (old or {}).get("run_timeout"), "after": new.get("run_timeout")}
    return out
