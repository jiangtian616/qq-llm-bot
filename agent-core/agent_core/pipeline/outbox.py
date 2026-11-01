"""发件箱：回复和历史在同一事务里写入（AgentEngine）；这里的发送器逐段发出，按错误分类重试，沿用同一个 msg_seq。

同一会话按顺序发（OutboxRepo.claim 每个会话只取最前面的一段），不同会话并发。
每次尝试（成功、重试、失败、过期）都记一行 outbox_attempts，消息队列页的详情里能看到每次的错误码和耗时。
领取和发完一批都发 queue.changed，桌面端的消息队列页据此刷新。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random

from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms
from agent_core.core.models import OutboundMessage, Scene, SendErrorClass
from agent_core.core.trace import Stopwatch

log = logging.getLogger(__name__)

LEASE_MS = 60_000
POLL_INTERVAL_S = 5.0
MAX_CHUNK_CHARS = 1500


def backoff_delay_s(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """1、2、4 秒……逐次加长并加随机抖动。"""
    delay = min(cap, base * 2 ** max(0, attempt - 1))
    return delay + random.uniform(0, delay / 2)


def split_reply(text: str, max_chunks: int, max_chunk_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """长回答切成几段（优先在换行处切），每段带递增的 msg_seq；段数不超过平台的回复次数上限。"""
    text = text.strip()
    chunks: list[str] = []
    while text:
        if len(text) <= max_chunk_chars:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_chunk_chars)
        if cut < max_chunk_chars // 2:
            cut = max_chunk_chars
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks = chunks or [""]
    if len(chunks) > max_chunks:
        chunks = chunks[:max_chunks]
        chunks[-1] = chunks[-1][: max_chunk_chars - 1] + "…"
    return chunks


class OutboxWorker:
    def __init__(self, *, db, connections, bus: EventBus, concurrency: int = 4) -> None:  # noqa: ANN001
        self._db = db
        self._connections = connections
        self._bus = bus
        self._concurrency = concurrency
        self._wakeup = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    def wake(self) -> None:
        self._wakeup.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="outbox")

    async def stop(self) -> None:
        self._stopping = True
        self.wake()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while not self._stopping:
            try:
                rows = await self._db.outbox.claim(self._concurrency, LEASE_MS)
                if rows:
                    await self._bus.publish(BusEvent("queue.changed", {"box": "outbox"}))
                    await asyncio.gather(*(self._send(row, conv) for row, conv in rows))
                    await self._bus.publish(BusEvent("queue.changed", {"box": "outbox"}))
                    continue
            except Exception:
                log.exception("发件箱出错")
            self._wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), POLL_INTERVAL_S)

    async def _attempt(self, row, watch: Stopwatch, result: str, **fields) -> None:  # noqa: ANN001, ANN003
        try:
            await self._db.traces.add_attempt(
                row.id, attempt=row.attempts, result=result, started_at=watch.started_at, duration_ms=watch.ms, **fields
            )
        except Exception:
            log.exception("记录发送尝试失败（outbox %s）", row.id)

    async def _send(self, row, conv) -> None:  # noqa: ANN001
        watch = Stopwatch()
        if row.deadline and watch.started_at > row.deadline:
            await self._db.outbox.mark(row.id, status="expired", last_error="超过被动回复时间")
            await self._attempt(row, watch, "expired", error="超过被动回复时间，没有发送")
            await self._bus.publish(BusEvent("outbox.expired", {"outbox_id": row.id}))
            return
        channel = None
        try:
            channel = await self._connections.channel(row.account_id)
            payload = json.loads(row.payload_json)
            result = await channel.send(
                OutboundMessage(
                    account_id=row.account_id,
                    scene=Scene(conv.scene),
                    target_openid=conv.target_openid,
                    content=payload.get("content", ""),
                    reply_to_msg_id=row.reply_to_msg_id,
                    reply_to_event_id=row.reply_to_event_id,
                    msg_seq=row.msg_seq,
                    msg_type=row.msg_type,
                    image_url=payload.get("image_url"),
                    card=payload.get("card"),
                )
            )
        except Exception as e:
            await self._on_error(row, channel, e, watch)
            return
        await self._attempt(row, watch, "sent", note=result.note)
        await self._db.outbox.mark(
            row.id,
            status="sent",
            sent_at=now_ms(),
            platform_message_id=result.platform_message_id,
            platform_ts=result.platform_ts,
            ref_idx=result.ref_idx,
            last_error=None,
        )
        await self._bus.publish(BusEvent("outbox.sent", {"outbox_id": row.id, "conversation_id": conv.id}))

    async def _on_error(self, row, channel, error: Exception, watch: Stopwatch) -> None:  # noqa: ANN001
        kind = channel.classify_send_error(error) if channel is not None else SendErrorClass.RETRYABLE
        code = getattr(error, "code", None)
        message = f"{type(error).__name__}: {error}"[:2000]
        fields = {"last_error_code": code if isinstance(code, int) else None, "last_error": message}
        attempt = {"error_code": fields["last_error_code"], "error": message}
        if kind == SendErrorClass.ALREADY_SENT:
            await self._attempt(row, watch, "already_sent", note="平台说之前已经发出过（去重），按已发送处理", **attempt)
            await self._db.outbox.mark(row.id, status="sent", sent_at=now_ms(), **fields)
            return
        if kind in (SendErrorClass.FATAL, SendErrorClass.EXPIRED):
            status = "failed" if kind == SendErrorClass.FATAL else "expired"
            await self._attempt(row, watch, status, **attempt)
            await self._db.outbox.mark(row.id, status=status, **fields)
            await self._db.events.add("error", "send", f"回复发送失败：{message[:200]}", account_id=row.account_id)
            await self._bus.publish(BusEvent(f"outbox.{status}", {"outbox_id": row.id, "error": message[:200]}))
            return
        next_at = now_ms() + int(backoff_delay_s(row.attempts) * 1000)
        if row.deadline and next_at > row.deadline:
            await self._attempt(row, watch, "expired", note="下次重试会超过回复截止时间，不再重试", **attempt)
            await self._db.outbox.mark(row.id, status="expired", **fields)
            await self._db.events.add("warning", "send", f"重试来不及，回复已过期：{message[:200]}", account_id=row.account_id)
            return
        log.warning("发送失败，稍后重试（outbox %s）：%s", row.id, message)
        await self._attempt(row, watch, "retry", note=f"{(next_at - now_ms()) / 1000:.1f} 秒后重试", **attempt)
        await self._db.outbox.mark(row.id, status="pending", next_attempt_at=next_at, **fields)
