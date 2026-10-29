"""调度器：从收件箱取活交给 Agent 引擎。同一会话串行，保证回复顺序和历史一致；不同会话并发，设并发上限。

有新活时立即唤醒；另每 5 秒轮询一次数据库兜底，重启后也能接上。
领取和处理完一条事件都发 queue.changed，桌面端的消息队列页据此刷新。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms
from agent_core.core.models import RawEvent
from agent_core.pipeline.inbox import LEASE_MS

log = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4
POLL_INTERVAL_S = 5.0


class Dispatcher:
    def __init__(self, *, db, connections, engine, bus: EventBus, concurrency: int = DEFAULT_CONCURRENCY) -> None:  # noqa: ANN001
        self._db = db
        self._bus = bus
        self._connections = connections
        self._engine = engine
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_inflight = concurrency * 4
        self._wakeup = asyncio.Event()
        self._locks: dict[tuple[int, str, str], asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        self._loop_task: asyncio.Task[None] | None = None

    def wake(self) -> None:
        self._wakeup.set()

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._run(), name="dispatcher")

    async def stop(self, timeout: float = 30.0) -> None:
        """不再取新活，等正在处理的回复入库（最多 timeout 秒）。没处理完的留在收件箱，下次启动接着做。"""
        self._stopping = True
        self.wake()
        if self._loop_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=timeout)
            for t in pending:
                t.cancel()

    async def _run(self) -> None:
        while not self._stopping:
            free = self._max_inflight - len(self._tasks)
            if free <= 0:
                await asyncio.wait(self._tasks, timeout=POLL_INTERVAL_S, return_when=asyncio.FIRST_COMPLETED)
                continue
            try:
                rows = await self._db.inbox.claim(free, LEASE_MS)
            except Exception:
                log.exception("读取收件箱出错")
                rows = []
            for row in rows:
                task = asyncio.create_task(self._process(row))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            if rows:
                await self._bus.publish(BusEvent("queue.changed", {"box": "inbox"}))
                continue
            self._wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), POLL_INTERVAL_S)

    async def _process(self, row) -> None:  # noqa: ANN001
        try:
            channel = await self._connections.channel(row.account_id)
            event = RawEvent(
                account_id=row.account_id,
                event_id=row.event_id,
                event_type=row.event_type,
                seq=row.gateway_seq,
                payload=json.loads(row.raw_json or "{}"),
                received_at_ms=row.received_at,
            )
            msg = channel.parse(event)
            if msg is None:
                await self._db.inbox.finish(row.id, "ignored")
                return
            # asyncio.Lock 按排队顺序唤醒，同一会话的消息按收件箱顺序处理。
            lock = self._locks.setdefault((msg.account_id, msg.scene, msg.target_openid), asyncio.Lock())
            async with lock, self._semaphore:
                if row.reply_deadline and now_ms() > row.reply_deadline:
                    await self._db.inbox.finish(row.id, "expired", error="超过被动回复时间")
                    return
                await self._engine.handle(row, msg, channel)
        except Exception as e:
            log.exception("处理收件箱事件 %s 出错", row.id)
            with contextlib.suppress(Exception):
                await self._db.inbox.finish(row.id, "failed", error=f"{type(e).__name__}: {e}"[:2000])
        finally:
            # 处理一条可能同时改了收件箱和发件箱（回复入库）。
            await self._bus.publish(BusEvent("queue.changed", {"box": "inbox"}))
