from django.core.management.base import BaseCommand

from delco.bus import Bus
from delco.config import get_settings
from delco.db import migrate
from delco.scheduler import Scheduler


class Command(BaseCommand):
    help = "Sobe o agendador: lê a pasta de workflows, cria execuções, libera tarefas e recupera falhas."

    def handle(self, *args, **options):
        settings = get_settings()
        migrate()
        Scheduler(Bus(settings.redis_url), settings).run_forever()
