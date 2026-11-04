from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENVIRONMENTS = ("development", "production", "test")
LOG_FORMATS = ("text", "json")
SMTP_SECURITY = ("starttls", "ssl", "none")
SECRET_FIELDS = ("secret_key", "database_url", "redis_url", "smtp_password", "metrics_token")


class ConfigError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Configuração inválida:\n" + "\n".join(f"  · {p}" for p in problems))
        self.problems = problems


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "sim", "on"}


class _Reader:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def number(
        self,
        name: str,
        default: str,
        cast: Callable[[str], Any],
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> Any:
        raw = _env(name, default)
        kind = "um número inteiro" if cast is int else "um número"
        try:
            value = cast(raw)
        except ValueError:
            self.problems.append(f"{name} precisa ser {kind} (recebido {raw!r}).")
            return cast(default)
        if minimum is not None and value < minimum:
            self.problems.append(f"{name} precisa ser no mínimo {minimum:g} (recebido {raw}).")
            return cast(default)
        if maximum is not None and value > maximum:
            self.problems.append(f"{name} precisa ser no máximo {maximum:g} (recebido {raw}).")
            return cast(default)
        return value

    def choice(self, name: str, default: str, options: tuple[str, ...]) -> str:
        value = _env(name, default).lower()
        if value not in options:
            self.problems.append(f"{name} precisa ser {', '.join(options)} (recebido {value!r}).")
            return default
        return value

    def url(self, name: str, default: str, schemes: tuple[str, ...]) -> str:
        value = _env(name, default)
        parsed = urlparse(value)
        if parsed.scheme not in schemes or not parsed.hostname:
            self.problems.append(f"{name} precisa ser uma URL {' ou '.join(s + '://' for s in schemes)}…")
        return value


@dataclass(frozen=True)
class Settings:
    environment: str
    secret_key: str
    allowed_hosts: list[str]
    database_url: str
    redis_url: str
    workflows_dir: Path
    web_dir: Path
    timezone: str
    seed: bool
    worker_concurrency: int
    worker_queues: tuple[str, ...]
    worker_grace_seconds: float
    lease_seconds: float
    heartbeat_seconds: float
    redeliver_after_seconds: float
    tick_seconds: float
    sync_seconds: float
    recovery_seconds: float
    retention_days: int
    max_output_bytes: int
    max_log_lines: int
    session_hours: int
    login_max_failures: int
    login_lock_minutes: int
    public_url: str
    webhook_timeout: float
    log_format: str = "text"
    log_level: str = "INFO"
    metrics_token: str = ""
    hook_rate_limit: int = 60
    login_rate_limit: int = 30
    max_backfill_runs: int = 366
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Delco <delco@localhost>"
    smtp_security: str = "starttls"
    telegram_api_url: str = "https://api.telegram.org"
    health_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    secret_key_from_env: bool = False

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host)

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if key in SECRET_FIELDS:
                out[key] = mask(key, value)
            elif isinstance(value, Path):
                out[key] = str(value)
            elif isinstance(value, tuple):
                out[key] = list(value)
            else:
                out[key] = value
        return out


def mask(key: str, value: Any) -> str:
    if not value:
        return ""
    if key in ("database_url", "redis_url"):
        p = urlparse(str(value))
        user = f"{p.username}:•••@" if p.username or p.password else ""
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{user}{p.hostname}{port}{p.path}"
    return "•••• definido"


def load_settings() -> Settings:
    root = Path(__file__).resolve().parent.parent
    r = _Reader()
    environment = r.choice("DELCO_ENVIRONMENT", "development", ENVIRONMENTS)
    timezone = _env("DELCO_TIMEZONE", "America/Sao_Paulo")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        r.problems.append(f"DELCO_TIMEZONE não é um fuso conhecido (recebido {timezone!r}).")
        timezone = "America/Sao_Paulo"
    queues = tuple(q.strip() for q in _env("DELCO_WORKER_QUEUES", "default").split(",") if q.strip())
    if not queues:
        r.problems.append("DELCO_WORKER_QUEUES precisa ter pelo menos uma fila.")
        queues = ("default",)
    heartbeat = r.number("DELCO_HEARTBEAT_SECONDS", "5", float, minimum=0.1, maximum=300)
    lease = r.number("DELCO_LEASE_SECONDS", "30", float, minimum=1, maximum=3600)
    if lease < heartbeat * 2:
        r.problems.append(
            f"DELCO_LEASE_SECONDS ({lease:g}) precisa ser pelo menos o dobro de DELCO_HEARTBEAT_SECONDS "
            f"({heartbeat:g}); senão tarefas saudáveis perdem a posse."
        )
    public_url = r.url("DELCO_PUBLIC_URL", "http://localhost:8080", ("http", "https"))
    secret_env = _env("DELCO_SECRET_KEY", "")
    settings = Settings(
        environment=environment,
        secret_key=secret_env or secrets.token_urlsafe(50),
        secret_key_from_env=bool(secret_env),
        allowed_hosts=[h.strip() for h in _env("DELCO_ALLOWED_HOSTS", "*").split(",") if h.strip()],
        database_url=r.url("DELCO_DATABASE_URL", "mysql://delco:delco@localhost:3306/delco", ("mysql",)),
        redis_url=r.url("DELCO_REDIS_URL", "redis://localhost:6379/0", ("redis", "rediss", "unix")),
        workflows_dir=Path(_env("DELCO_WORKFLOWS_DIR", str(root / "workflows"))),
        web_dir=Path(_env("DELCO_WEB_DIR", str(root / "web"))),
        timezone=timezone,
        seed=_bool("DELCO_SEED", True),
        worker_concurrency=r.number("DELCO_WORKER_CONCURRENCY", "4", int, minimum=1, maximum=256),
        worker_queues=queues,
        worker_grace_seconds=r.number("DELCO_WORKER_GRACE_SECONDS", "30", float, minimum=0),
        lease_seconds=lease,
        heartbeat_seconds=heartbeat,
        redeliver_after_seconds=r.number("DELCO_REDELIVER_AFTER_SECONDS", "60", float, minimum=1),
        tick_seconds=r.number("DELCO_TICK_SECONDS", "1", float, minimum=0.05, maximum=60),
        sync_seconds=r.number("DELCO_SYNC_SECONDS", "30", float, minimum=1),
        recovery_seconds=r.number("DELCO_RECOVERY_SECONDS", "5", float, minimum=0.5),
        retention_days=r.number("DELCO_RETENTION_DAYS", "30", int, minimum=1, maximum=3650),
        max_output_bytes=r.number("DELCO_MAX_OUTPUT_BYTES", str(1024 * 1024), int, minimum=1024),
        max_log_lines=r.number("DELCO_MAX_LOG_LINES", "5000", int, minimum=10),
        session_hours=r.number("DELCO_SESSION_HOURS", "12", int, minimum=1, maximum=720),
        login_max_failures=r.number("DELCO_LOGIN_MAX_FAILURES", "5", int, minimum=1),
        login_lock_minutes=r.number("DELCO_LOGIN_LOCK_MINUTES", "5", int, minimum=1),
        public_url=public_url,
        webhook_timeout=r.number("DELCO_WEBHOOK_TIMEOUT", "10", float, minimum=1, maximum=120),
        log_format=r.choice("DELCO_LOG_FORMAT", "text", LOG_FORMATS),
        log_level=r.choice("DELCO_LOG_LEVEL", "info", ("debug", "info", "warning", "error")).upper(),
        metrics_token=_env("DELCO_METRICS_TOKEN", ""),
        hook_rate_limit=r.number("DELCO_HOOK_RATE_LIMIT", "60", int, minimum=0),
        login_rate_limit=r.number("DELCO_LOGIN_RATE_LIMIT", "30", int, minimum=0),
        max_backfill_runs=r.number("DELCO_MAX_BACKFILL_RUNS", "366", int, minimum=1, maximum=5000),
        smtp_host=_env("DELCO_SMTP_HOST", ""),
        smtp_port=r.number("DELCO_SMTP_PORT", "587", int, minimum=1, maximum=65535),
        smtp_user=_env("DELCO_SMTP_USER", ""),
        smtp_password=_env("DELCO_SMTP_PASSWORD", ""),
        smtp_from=_env("DELCO_SMTP_FROM", "Delco <delco@localhost>"),
        smtp_security=r.choice("DELCO_SMTP_SECURITY", "starttls", SMTP_SECURITY),
        telegram_api_url=r.url("DELCO_TELEGRAM_API_URL", "https://api.telegram.org", ("http", "https")),
        health_dir=Path(_env("DELCO_HEALTH_DIR", tempfile.gettempdir())),
    )
    if r.problems:
        raise ConfigError(r.problems)
    return settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()
