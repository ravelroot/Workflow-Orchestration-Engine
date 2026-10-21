"""Configuração lida das variáveis de ambiente DELCO_*."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "sim", "on"}


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
    # Fila e workers
    worker_concurrency: int
    worker_queues: tuple[str, ...]
    worker_grace_seconds: float
    lease_seconds: float
    heartbeat_seconds: float
    redeliver_after_seconds: float
    # Agendador
    tick_seconds: float
    sync_seconds: float
    recovery_seconds: float
    retention_days: int
    # Limites
    max_output_bytes: int
    max_log_lines: int
    session_hours: int
    login_max_failures: int
    login_lock_minutes: int

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    root = Path(__file__).resolve().parent.parent
    return Settings(
        environment=_env("DELCO_ENVIRONMENT", "development"),
        # O Delco não assina cookies nem sessões com a chave; sem DELCO_SECRET_KEY, cada processo sorteia uma.
        secret_key=_env("DELCO_SECRET_KEY", secrets.token_urlsafe(50)),
        allowed_hosts=[h.strip() for h in _env("DELCO_ALLOWED_HOSTS", "*").split(",") if h.strip()],
        database_url=_env("DELCO_DATABASE_URL", "mysql://delco:delco@localhost:3306/delco"),
        redis_url=_env("DELCO_REDIS_URL", "redis://localhost:6379/0"),
        workflows_dir=Path(_env("DELCO_WORKFLOWS_DIR", str(root / "workflows"))),
        web_dir=Path(_env("DELCO_WEB_DIR", str(root / "web"))),
        timezone=_env("DELCO_TIMEZONE", "America/Sao_Paulo"),
        seed=_bool("DELCO_SEED", True),
        worker_concurrency=max(1, int(_env("DELCO_WORKER_CONCURRENCY", "4"))),
        worker_queues=tuple(
            q.strip() for q in _env("DELCO_WORKER_QUEUES", "default").split(",") if q.strip()
        ),
        worker_grace_seconds=float(_env("DELCO_WORKER_GRACE_SECONDS", "30")),
        lease_seconds=float(_env("DELCO_LEASE_SECONDS", "30")),
        heartbeat_seconds=float(_env("DELCO_HEARTBEAT_SECONDS", "5")),
        redeliver_after_seconds=float(_env("DELCO_REDELIVER_AFTER_SECONDS", "60")),
        tick_seconds=float(_env("DELCO_TICK_SECONDS", "1")),
        sync_seconds=float(_env("DELCO_SYNC_SECONDS", "30")),
        recovery_seconds=float(_env("DELCO_RECOVERY_SECONDS", "5")),
        retention_days=int(_env("DELCO_RETENTION_DAYS", "30")),
        max_output_bytes=int(_env("DELCO_MAX_OUTPUT_BYTES", str(1024 * 1024))),
        max_log_lines=int(_env("DELCO_MAX_LOG_LINES", "5000")),
        session_hours=int(_env("DELCO_SESSION_HOURS", "12")),
        login_max_failures=int(_env("DELCO_LOGIN_MAX_FAILURES", "5")),
        login_lock_minutes=int(_env("DELCO_LOGIN_LOCK_MINUTES", "5")),
    )
