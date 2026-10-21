"""Expressões cron, fuso horário, espera entre tentativas e descrição em português."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

DIAS = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"]
ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


def _err(message: str) -> ValueError:
    from .sdk import WorkflowError

    return WorkflowError(message)


def normalize_cron(expr: str) -> str:
    expr = " ".join((expr or "").split())
    return ALIASES.get(expr.lower(), expr)


def validate_cron(expr: str) -> str:
    normalized = normalize_cron(expr)
    if len(normalized.split()) != 5 or not croniter.is_valid(normalized):
        raise _err(f"Agendamento inválido: {expr!r}. Use cron de 5 campos, como '0 7 * * *'.")
    return normalized


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _err(f"Fuso horário desconhecido: {name!r}.") from exc


def next_fire(expr: str, timezone: str, after: datetime) -> datetime:
    """Próximo horário (com fuso) estritamente depois de ``after``."""
    tz = ZoneInfo(timezone)
    base = after.astimezone(tz)
    return croniter(normalize_cron(expr), base).get_next(datetime)


def retry_delay(attempt: int, base: float, backoff: float, maximum: float, *, jitter: float = 0.1) -> float:
    """Espera exponencial com variação aleatória: base * backoff^(tentativa-1)."""
    delay = min(maximum, base * (backoff ** max(0, attempt - 1)))
    if jitter and delay > 0:
        delay *= 1 + random.uniform(-jitter, jitter)
    return round(max(0.0, delay), 3)


def _hhmm(h: str, m: str) -> str:
    return f"{int(h):02d}:{int(m):02d}"


def describe(expr: str | None) -> str:
    """Texto curto em português para as expressões mais comuns."""
    if not expr:
        return "Somente manual"
    cron = normalize_cron(expr)
    parts = cron.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    every = lambda field: field.startswith("*/") and field[2:].isdigit()  # noqa: E731
    try:
        if cron == "* * * * *":
            return "A cada minuto"
        if every(minute) and hour == dom == month == dow == "*":
            n = int(minute[2:])
            return "A cada minuto" if n == 1 else f"A cada {n} minutos"
        if minute.isdigit() and hour == "*" and dom == month == dow == "*":
            return "De hora em hora" if minute == "0" else f"De hora em hora, no minuto {int(minute)}"
        if minute.isdigit() and every(hour) and dom == month == dow == "*":
            return f"A cada {int(hour[2:])} horas"
        if minute.isdigit() and hour.isdigit() and month == "*":
            at = _hhmm(hour, minute)
            if dom == "*" and dow == "*":
                return f"Todo dia às {at}"
            if dom == "*" and dow == "1-5":
                return f"Dias úteis às {at}"
            if dom == "*" and dow.isdigit():
                dia = DIAS[int(dow) % 7]
                prefixo = "Todo" if dia in ("domingo", "sábado") else "Toda"
                return f"{prefixo} {dia}{'-feira' if prefixo == 'Toda' else ''} às {at}"
            if dom.isdigit() and dow == "*":
                return f"Todo dia {int(dom)} do mês às {at}"
    except (ValueError, IndexError):
        pass
    return cron


def utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def seconds(n: float) -> timedelta:
    return timedelta(seconds=n)
