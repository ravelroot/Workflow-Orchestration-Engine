from django.core.management.base import BaseCommand

from delco.bus import Bus
from delco.config import get_settings
from delco.db import migrate
from delco.worker import Worker


class Command(BaseCommand):
    help = "Sobe um worker: consome as filas do Redis e executa cada tentativa num processo filho."

    def add_arguments(self, parser):
        parser.add_argument("--queues", help="filas separadas por vírgula (padrão: DELCO_WORKER_QUEUES)")
        parser.add_argument(
            "--concurrency", type=int, help="tarefas simultâneas (padrão: DELCO_WORKER_CONCURRENCY)"
        )
        parser.add_argument("--name", help="nome do worker (padrão: host + pid)")

    def handle(self, *args, queues=None, concurrency=None, name=None, **options):
        settings = get_settings()
        migrate()  # com trava consultiva: seguro mesmo se API e agendador subirem juntos
        Worker(
            Bus(settings.redis_url),
            settings,
            worker_id=name,
            queues=[q.strip() for q in queues.split(",")] if queues else None,
            concurrency=concurrency,
        ).run_forever()
