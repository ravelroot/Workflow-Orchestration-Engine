"""WebSocket do painel (Django Channels): eventos ao vivo de execuções, tarefas, workers e logs."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .. import accounts
from .events import ALL, attempt_group, listener


class EventsConsumer(AsyncWebsocketConsumer):
    """Conexão autenticada pelo token em ``?token=``. Mensagens aceitas do navegador:

    ``{"action": "logs", "attempt_id": 7}`` passa a receber o log daquela tentativa;
    ``{"action": "unlogs"}`` para; ``{"action": "ping"}`` responde ``{"type": "pong"}``.
    """

    log_group: str | None = None

    async def connect(self) -> None:
        token = parse_qs(self.scope.get("query_string", b"").decode()).get("token", [""])[0]
        try:
            self.principal = await database_sync_to_async(accounts.authenticate)(token)
        except accounts.AuthError:
            await self.close(code=4401)
            return
        await listener.ensure()
        await self.channel_layer.group_add(ALL, self.channel_name)
        await self.accept()
        await self.send(text_data=json.dumps({"type": "hello"}))

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(ALL, self.channel_name)
        await self._leave_logs()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        try:
            msg = json.loads(text_data or "")
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        action = msg.get("action")
        if action == "logs" and isinstance(msg.get("attempt_id"), int):
            await self._leave_logs()
            self.log_group = attempt_group(msg["attempt_id"])
            await self.channel_layer.group_add(self.log_group, self.channel_name)
        elif action == "unlogs":
            await self._leave_logs()
        elif action == "ping":
            await self.send(text_data='{"type":"pong"}')

    async def _leave_logs(self) -> None:
        if self.log_group:
            await self.channel_layer.group_discard(self.log_group, self.channel_name)
            self.log_group = None

    async def delco_event(self, message: dict) -> None:
        await self.send(text_data=message["raw"])
