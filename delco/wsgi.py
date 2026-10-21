"""Ponto de entrada WSGI (só HTTP, sem WebSocket). Útil para testes e servidores WSGI."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delco.settings")
application = get_wsgi_application()
