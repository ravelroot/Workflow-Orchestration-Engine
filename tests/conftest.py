"""Infraestrutura dos testes: Django com MySQL e Redis reais (veja o README para subir os dois).

Os workers dos testes rodam em threads e processos de verdade, então cada teste
grava no banco de fato (sem transação de teste). Antes de cada teste as tabelas
são esvaziadas com TRUNCATE e o Redis com FLUSHDB.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"

os.environ["DELCO_DATABASE_URL"] = os.environ.get(
    "DELCO_TEST_DATABASE_URL", "mysql://delco:delco@127.0.0.1:3306/delco_test"
)
os.environ["DELCO_REDIS_URL"] = os.environ.get("DELCO_TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DELCO_WORKFLOWS_DIR"] = str(FIXTURES)
os.environ["DELCO_SEED"] = "false"
os.environ["DELCO_HEARTBEAT_SECONDS"] = "0.3"
os.environ["DELCO_LEASE_SECONDS"] = "2"
os.environ["DELCO_REDELIVER_AFTER_SECONDS"] = "2"
os.environ["DELCO_WORKER_GRACE_SECONDS"] = "3"
os.environ["DELCO_ENVIRONMENT"] = "test"
os.environ["DJANGO_SETTINGS_MODULE"] = "delco.settings"

from delco.db import setup  # noqa: E402

setup()

from django.db import connection  # noqa: E402

from delco.bus import Bus  # noqa: E402
from delco.config import get_settings  # noqa: E402
from delco.db import migrate, release  # noqa: E402
from delco.models import TaskInstance, WorkflowRun  # noqa: E402
from delco.scheduler import Scheduler  # noqa: E402
from delco.worker import Worker  # noqa: E402

TABLES = (
    "task_logs",
    "task_attempts",
    "task_instances",
    "workflow_runs",
    "workflow_versions",
    "workflows",
    "workers",
    "api_tokens",
    "audit_log",
    "users",
)


class SQL:
    """Acesso direto ao MySQL para montar cenários (ex.: lease vencido) e conferir o estado."""

    def fetchall(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with connection.cursor() as cur:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            # O MySQL devolve DATETIME sem fuso; a sessão está em UTC (init_command), então marca UTC.
            return [
                {
                    c: v.replace(tzinfo=UTC) if isinstance(v, datetime) and v.tzinfo is None else v
                    for c, v in zip(cols, row, strict=True)
                }
                for row in cur.fetchall()
            ]

    def fetchone(self, sql: str, params: Any = None) -> dict[str, Any] | None:
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Any = None) -> int:
        with connection.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def db(settings):
    with connection.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("SHOW TABLES")
        for (table,) in cur.fetchall():
            cur.execute(f"DROP TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    migrate()
    yield SQL()
    release()


@pytest.fixture(scope="session")
def bus(settings):
    b = Bus(settings.redis_url)
    yield b
    b.close()


@pytest.fixture(autouse=True)
def clean(request):
    if "nodb" in request.keywords:
        yield
        return
    db = request.getfixturevalue("db")
    bus = request.getfixturevalue("bus")
    db.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in TABLES:
        db.execute(f"TRUNCATE TABLE {table}")
    db.execute("SET FOREIGN_KEY_CHECKS = 1")
    bus.redis.flushdb()
    bus._groups.clear()
    yield
    release()


@pytest.fixture
def scheduler(db, bus, settings):
    s = Scheduler(bus, settings, workflows_dir=FIXTURES)
    s.sync()
    return s


@pytest.fixture
def workers(db, bus, settings):
    started: list[Worker] = []

    def make(**kwargs) -> Worker:
        kwargs.setdefault("concurrency", 4)
        kwargs.setdefault("workflows_dir", FIXTURES)
        w = Worker(bus, settings, **kwargs)
        w.start()
        started.append(w)
        return w

    yield make
    for w in started:
        if not w.stopping.is_set():
            w.stop(grace=1)


def wait_for(
    scheduler: Scheduler, check: Callable[[], bool], timeout: float = 20, recover: bool = False
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scheduler.tick()
        if recover:
            scheduler.recover()
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("Tempo esgotado esperando a condição")


def run_status(db: SQL, run_id: int) -> str:
    return WorkflowRun.objects.values_list("status", flat=True).get(id=run_id)


def tasks_of(db: SQL, run_id: int) -> dict[str, dict]:
    return {t["task_name"]: t for t in TaskInstance.objects.filter(run_id=run_id).order_by("id").values()}


def finished(db: SQL, run_id: int) -> Callable[[], bool]:
    return lambda: run_status(db, run_id) in ("succeeded", "failed", "cancelled")
