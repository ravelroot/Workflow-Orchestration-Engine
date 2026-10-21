"""Utilitários prontos para usar dentro das tarefas."""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import Any

import httpx

from .context import TaskContext


class CommandError(RuntimeError):
    def __init__(self, command: str, code: int) -> None:
        super().__init__(f"O comando terminou com código {code}: {command}")
        self.code = code


def shell(
    ctx: TaskContext,
    command: str | list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    """Roda um comando e envia cada linha da saída para o log da tarefa."""
    args = shlex.split(command) if isinstance(command, str) else list(command)
    shown = command if isinstance(command, str) else shlex.join(args)
    ctx.log(f"$ {shown}")
    proc = subprocess.Popen(
        args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        ctx.log(line.rstrip("\n"))
    code = proc.wait()
    if check and code != 0:
        raise CommandError(shown, code)
    return code


def http_json(ctx: TaskContext, method: str, url: str, *, timeout: float = 30, **kwargs: Any) -> Any:
    """Faz uma chamada HTTP e devolve o JSON. Envia o ``ctx.idempotency_key`` no cabeçalho
    ``Idempotency-Key`` para que o serviço chamado possa ignorar repetições."""
    headers = {"Idempotency-Key": ctx.idempotency_key, **kwargs.pop("headers", {})}
    ctx.log(f"{method.upper()} {url}")
    started = time.monotonic()
    response = httpx.request(method, url, headers=headers, timeout=timeout, **kwargs)
    ctx.log(f"HTTP {response.status_code} em {time.monotonic() - started:.2f} s")
    response.raise_for_status()
    return response.json() if response.content else None
