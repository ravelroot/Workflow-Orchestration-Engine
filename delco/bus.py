"""Redis: filas (Streams com grupo de consumidores), eventos (pub/sub) e sinal de despertar.

O Redis é só transporte. Se uma mensagem se perder, o agendador percebe pelo
MySQL e a publica de novo; se chegar repetida, o worker a descarta porque
a troca de estado no banco é condicional.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import redis

log = logging.getLogger("delco.bus")

STREAM_PREFIX = "delco:queue:"
GROUP = "workers"
EVENTS_CHANNEL = "delco:events"
WAKE_KEY = "delco:wake"
LOAD_ERRORS_KEY = "delco:load-errors"
STREAM_MAXLEN = 200_000


def stream_key(queue: str) -> str:
    return STREAM_PREFIX + queue


class Bus:
    def __init__(self, url: str) -> None:
        self.url = url
        self.redis = redis.Redis.from_url(url, decode_responses=True, health_check_interval=30)
        self._groups: set[str] = set()

    # ------------------------------------------------------------------ filas
    def ensure_group(self, queue: str, *, force: bool = False) -> None:
        key = stream_key(queue)
        if key in self._groups and not force:
            return
        try:
            self.redis.xgroup_create(key, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups.add(key)

    def enqueue(self, queue: str, task_id: int) -> str:
        self.ensure_group(queue)
        return self.redis.xadd(
            stream_key(queue), {"task": str(task_id)}, maxlen=STREAM_MAXLEN, approximate=True
        )

    def read(self, consumer: str, queues: list[str], count: int, block_ms: int) -> list[tuple[str, str, int]]:
        streams = {stream_key(q): ">" for q in queues}
        try:
            reply = self.redis.xreadgroup(GROUP, consumer, streams, count=count, block=block_ms)
        except redis.ResponseError as exc:
            if "NOGROUP" in str(exc):  # o Redis foi limpo: recria os grupos
                for q in queues:
                    self.ensure_group(q, force=True)
                return []
            raise
        return self._parse(reply)

    def autoclaim(
        self, consumer: str, queue: str, min_idle_ms: int, count: int = 20
    ) -> list[tuple[str, str, int]]:
        """Assume mensagens entregues a um consumidor que morreu antes de confirmar."""
        try:
            reply = self.redis.xautoclaim(stream_key(queue), GROUP, consumer, min_idle_ms, "0-0", count=count)
        except redis.ResponseError as exc:
            if "NOGROUP" in str(exc):
                self.ensure_group(queue, force=True)
                return []
            raise
        messages = reply[1] if reply else []
        return [(queue, mid, int(fields["task"])) for mid, fields in messages if fields and "task" in fields]

    @staticmethod
    def _parse(reply: Any) -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        for key, messages in reply or []:
            queue = key[len(STREAM_PREFIX) :]
            for mid, fields in messages:
                if fields and "task" in fields:
                    out.append((queue, mid, int(fields["task"])))
        return out

    def ack(self, queue: str, message_id: str) -> None:
        key = stream_key(queue)
        pipe = self.redis.pipeline(transaction=False)
        pipe.xack(key, GROUP, message_id)
        pipe.xdel(key, message_id)
        pipe.execute()

    def exists(self, queue: str, message_id: str | None) -> bool:
        if not message_id:
            return False
        return bool(self.redis.xrange(stream_key(queue), message_id, message_id, count=1))

    def queue_stats(self) -> list[dict[str, Any]]:
        stats = []
        for key in sorted(self.redis.scan_iter(match=STREAM_PREFIX + "*", count=100)):
            pending = 0
            try:
                for group in self.redis.xinfo_groups(key):
                    if group["name"] == GROUP:
                        pending = int(group.get("pending", 0))
            except redis.ResponseError:
                pass
            stats.append(
                {"queue": key[len(STREAM_PREFIX) :], "length": self.redis.xlen(key), "pending": pending}
            )
        return stats

    # ---------------------------------------------------------------- eventos
    def publish(self, kind: str, **data: Any) -> None:
        try:
            self.redis.publish(EVENTS_CHANNEL, json.dumps({"type": kind, **data}, default=str))
        except redis.RedisError as exc:  # eventos são best-effort: nunca derrubam o fluxo
            log.warning("Falha ao publicar evento %s: %s", kind, exc)

    def wake(self) -> None:
        try:
            pipe = self.redis.pipeline(transaction=False)
            pipe.lpush(WAKE_KEY, "1")
            pipe.ltrim(WAKE_KEY, 0, 0)
            pipe.execute()
        except redis.RedisError:
            pass

    def wait_wake(self, timeout: float) -> bool:
        try:
            return self.redis.brpop([WAKE_KEY], timeout=max(0.05, timeout)) is not None
        except redis.RedisError:
            return False

    def set_load_errors(self, errors: dict[str, str]) -> None:
        try:
            self.redis.set(LOAD_ERRORS_KEY, json.dumps(errors))
        except redis.RedisError:
            pass

    def load_errors(self) -> dict[str, str]:
        try:
            raw = self.redis.get(LOAD_ERRORS_KEY)
            return json.loads(raw) if raw else {}
        except (redis.RedisError, ValueError):
            return {}

    def ping(self) -> bool:
        try:
            return bool(self.redis.ping())
        except redis.RedisError:
            return False

    def close(self) -> None:
        self.redis.close()


_bus: Bus | None = None
_bus_lock = threading.Lock()


def get_bus() -> Bus:
    """Conexão compartilhada do processo (usada pelas views da API)."""
    global _bus
    with _bus_lock:
        if _bus is None:
            from .config import get_settings

            _bus = Bus(get_settings().redis_url)
        return _bus
