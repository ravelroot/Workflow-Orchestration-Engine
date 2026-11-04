from __future__ import annotations

import logging
import os

log = logging.getLogger("delco.db")

MIGRATION_LOCK = "delco:migrate"


def setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delco.settings")
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def migrate(verbosity: int = 0) -> list[str]:
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    with connection.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, -1)", [MIGRATION_LOCK])
        try:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            pending = [f"{m.app_label}.{m.name}" for m, backwards in plan if not backwards]
            if pending:
                call_command("migrate", interactive=False, verbosity=verbosity)
                log.info("Migrações aplicadas: %s", ", ".join(pending))
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", [MIGRATION_LOCK])
    return pending


def ping() -> bool:
    from django.db import connection

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def release() -> None:
    from django.db import connections

    connections.close_all()
