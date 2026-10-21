"""Ponte Redis → Channels.

O agendador e os workers publicam cada evento no canal ``delco:events`` do Redis.
Cada processo da API mantém uma assinatura e repassa o evento ao grupo certo do
Channels: ``delco.eventos`` (todos os navegadores) ou ``delco.tentativa.<id>``
(só quem está com o log daquela tentativa aberto).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import redis.asyncio as aioredis
from channels.layers import get_channel_layer

from ..bus import EVENTS_CHANNEL
from ..config import get_settings

log = logging.getLogger("delco.events")

ALL = "delco.eventos"


def attempt_group(attempt_id: int) -> str:
    return f"delco.tentativa.{attempt_id}"


class _Listener:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.ready: asyncio.Event | None = None

    async def ensure(self, timeout: float = 5) -> None:
        loop = asyncio.get_running_loop()
        if self.task is None or self.task.done() or self.task.get_loop() is not loop:
            self.ready = asyncio.Event()
            self.task = loop.create_task(self._run(self.ready), name="delco-events")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.ready.wait(), timeout)

    async def _run(self, ready: asyncio.Event) -> None:
        layer = get_channel_layer()
        while True:
            conn = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            try:
                pubsub = conn.pubsub()
                await pubsub.subscribe(EVENTS_CHANNEL)
                ready.set()
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        await forward(layer, message["data"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # Redis fora do ar: tenta de novo
                log.warning("Assinatura de eventos caiu (%s); reconectando em 2 s", exc)
                await asyncio.sleep(2)
            finally:
                with contextlib.suppress(Exception):
                    await conn.aclose()


async def forward(layer, raw: str) -> None:
    try:
        event = json.loads(raw)
    except ValueError:
        return
    if event.get("type") == "log":
        if not isinstance(event.get("attempt_id"), int):
            return
        group = attempt_group(event["attempt_id"])
    else:
        group = ALL
    await layer.group_send(group, {"type": "delco.event", "raw": raw})


listener = _Listener()
