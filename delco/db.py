"""Banco de dados pelo Django: configuração do ORM, migrações e conexões.

O Delco usa o ORM do Django em todos os processos (API, agendador e workers).
Este módulo prepara o Django fora do ``manage.py`` e cuida das conexões nos
threads de longa duração do worker e do agendador.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("delco.db")

# Nome fixo da trava nomeada do MySQL (GET_LOCK): impede que dois processos migrem ao mesmo tempo.
MIGRATION_LOCK = "delco:migrate"


def setup() -> None:
    """Carrega o Django com ``delco.settings`` (idempotente)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delco.settings")
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()


def migrate(verbosity: int = 0) -> list[str]:
    """Aplica as migrações do Django com trava nomeada (API, agendador e CLI podem subir juntos)."""
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    with connection.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, -1)", [MIGRATION_LOCK])  # -1: espera o tempo que for preciso
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
    """Devolve ao pool as conexões do thread atual (chamado ao fim de cada unidade de trabalho)."""
    from django.db import connections

    connections.close_all()
