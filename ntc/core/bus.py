"""Modüllerin birbirine doğrudan bağlanmadan konuşması için asenkron olay yolu."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Handler = Callable[[str, Any], Awaitable[None] | None]

# Sistem genelinde kullanılan konu adları.
TOPIC_FLOW = "flow"
TOPIC_FLOW_BATCH = "flow.batch"
TOPIC_DEVICE = "device.seen"
TOPIC_METRICS = "metrics.window"
TOPIC_ALERT = "alert"
TOPIC_ACTION = "optimizer.action"
TOPIC_AI_REPORT = "ai.report"


class EventBus:
    """Basit pub/sub. `*` konusu her şeyi dinler."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        if handler in self._subs.get(topic, []):
            self._subs[topic].remove(handler)

    async def publish(self, topic: str, payload: Any) -> None:
        handlers = [*self._subs.get(topic, []), *self._subs.get("*", [])]
        if not handlers:
            return
        results = await asyncio.gather(
            *(self._invoke(h, topic, payload) for h in handlers),
            return_exceptions=True,
        )
        for res in results:
            if isinstance(res, Exception):
                log.exception("Olay işleyicisi hata verdi (topic=%s)", topic, exc_info=res)

    @staticmethod
    async def _invoke(handler: Handler, topic: str, payload: Any) -> None:
        result = handler(topic, payload)
        if asyncio.iscoroutine(result):
            await result
