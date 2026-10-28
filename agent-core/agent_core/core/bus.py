"""进程内事件总线：模块之间只通过它通信，互不直接调用。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[["BusEvent"], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class BusEvent:
    topic: str
    """如 connection.state、inbox.new、outbox.new、log。"""
    data: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_json(self) -> dict[str, Any]:
        return {"topic": self.topic, "ts": self.ts_ms, "data": self.data}


class EventBus:
    def __init__(self, stream_queue_size: int = 1000) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._streams: set[asyncio.Queue[BusEvent]] = set()
        self._stream_queue_size = stream_queue_size

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """订阅一个主题（"*" 表示全部），返回取消订阅的函数。"""
        self._handlers.setdefault(topic, []).append(handler)
        return lambda: self._handlers.get(topic, []).remove(handler)

    async def publish(self, event: BusEvent) -> None:
        for handler in [*self._handlers.get(event.topic, []), *self._handlers.get("*", [])]:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("事件处理出错：%s", event.topic)
        for queue in self._streams:
            if queue.full():
                # 慢订阅者丢最旧的事件，不拖住发布方。
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    @contextlib.asynccontextmanager
    async def stream(self) -> AsyncIterator[AsyncIterator[BusEvent]]:
        """给控制接口的事件流用：拿到一个异步迭代器，退出上下文时自动取消订阅。"""
        queue: asyncio.Queue[BusEvent] = asyncio.Queue(self._stream_queue_size)
        self._streams.add(queue)

        async def iterate() -> AsyncIterator[BusEvent]:
            while True:
                yield await queue.get()

        try:
            yield iterate()
        finally:
            self._streams.discard(queue)
