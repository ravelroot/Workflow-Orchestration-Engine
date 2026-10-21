from django.core.management.base import BaseCommand

from delco.bus import Bus
from delco.config import get_settings
from delco.db import migrate
from delco.scheduler import Scheduler
from delco.seed import seed_history, seed_users


class Command(BaseCommand):
    help = "Cria as contas e uma semana de histórico de demonstração (só se o banco estiver vazio)."

    def handle(self, *args, **options):
        settings = get_settings()
        migrate()
        users = seed_users()
        Scheduler(Bus(settings.redis_url), settings).sync()
        runs = seed_history()
        self.stdout.write(
            f"Contas de demonstração: {'criadas' if users else 'já existiam'} · histórico: {runs} execuções"
        )
