"""Rotas WebSocket (Django Channels)."""

from django.urls import path

from .consumers import EventsConsumer

websocket_urlpatterns = [path("api/ws", EventsConsumer.as_asgi())]
