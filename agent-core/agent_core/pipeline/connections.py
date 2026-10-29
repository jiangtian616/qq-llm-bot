"""连接管理：每个机器人账号一条 QQ 连接，由用户手动启停（或设置成启动后自动连接）。

接收只做三件事：解析事件；在一个事务里写入收件箱并更新 seq；提交后唤醒调度器。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms
from agent_core.core.lifecycle import ConnectionState, ConnectionStateMachine, InvalidTransition
from agent_core.core.ports import (
    ChannelAdapter,
    ChannelContext,
    InboundTransport,
    ResumeState,
    SecretReader,
    TransportContext,
)
from agent_core.infra.db.database import Database
from agent_core.pipeline.inbox import reply_deadline_ms

log = logging.getLogger(__name__)


class AccountNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class _Connection:
    state: ConnectionStateMachine
    transport: InboundTransport | None = None
    consumer: asyncio.Task[None] | None = None


class ConnectionManager:
    def __init__(
        self,
        *,
        db: Database,
        bus: EventBus,
        secrets: SecretReader,
        channel_cls: type[ChannelAdapter],
        transport_cls: type[InboundTransport],
        on_inbox: Callable[[], None],
    ) -> None:
        self._db = db
        self._bus = bus
        self._secrets = secrets
        self._channel_cls = channel_cls
        self._transport_cls = transport_cls
        self._on_inbox = on_inbox
        self._connections: dict[int, _Connection] = {}
        self._channels: dict[int, ChannelAdapter] = {}
        self._lock = asyncio.Lock()
        bus.subscribe("connection.state", self._on_state)

    # ---- 渠道（发消息用，不需要连着 WebSocket） ----

    async def channel(self, account_id: int) -> ChannelAdapter:
        adapter = self._channels.get(account_id)
        if adapter is None:
            account = await self._db.accounts.get(account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            adapter = self._channel_cls(
                ChannelContext(
                    account_id=account.id,
                    app_id=account.app_id,
                    secrets=self._secrets,
                )
            )
            self._channels[account_id] = adapter
        return adapter

    async def forget(self, account_id: int) -> None:
        """账号改了 AppSecret 或被删除时调用，下次用到时重新创建。"""
        await self.stop(account_id)
        adapter = self._channels.pop(account_id, None)
        if adapter is not None:
            await adapter.aclose()
        self._connections.pop(account_id, None)

    # ---- 连接 ----

    def state(self, account_id: int) -> ConnectionState:
        conn = self._connections.get(account_id)
        return conn.state.state if conn else ConnectionState.STOPPED

    def last_error(self, account_id: int) -> str | None:
        conn = self._connections.get(account_id)
        return conn.state.last_error if conn else None

    async def start(self, account_id: int) -> None:
        async with self._lock:
            account = await self._db.accounts.get(account_id)
            if account is None:
                raise AccountNotFoundError(account_id)
            conn = self._connections.get(account_id)
            if conn is None:
                conn = _Connection(state=ConnectionStateMachine(account_id, self._bus))
                self._connections[account_id] = conn
            if conn.transport is not None:
                return
            gateway = await self._db.accounts.gateway(account_id)
            transport = self._transport_cls(
                TransportContext(
                    account_id=account.id,
                    app_id=account.app_id,
                    secrets=self._secrets,
                    bus=self._bus,
                    state=conn.state,
                    intents=account.intents,
                    resume=ResumeState(gateway.session_id, gateway.last_seq) if gateway else ResumeState(),
                )
            )
            conn.transport = transport
            try:
                await transport.start()
            except Exception as e:
                conn.transport = None
                with contextlib.suppress(InvalidTransition):
                    await conn.state.transition(ConnectionState.ERROR, str(e))
                raise
            conn.consumer = asyncio.create_task(self._consume(account_id, transport), name=f"inbox-{account_id}")

    async def stop(self, account_id: int) -> None:
        async with self._lock:
            conn = self._connections.get(account_id)
            if conn is None or conn.transport is None:
                return
            transport = conn.transport
            # 先不再接新事件，再保存 session_id 和 seq，下次启动还能 Resume。
            await transport.stop()
            if conn.consumer is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(conn.consumer, 5)
            resume = transport.resume_state()
            await self._db.accounts.save_gateway(account_id, session_id=resume.session_id, last_seq=resume.seq)
            conn.transport = None
            conn.consumer = None

    async def stop_all(self) -> None:
        for account_id in list(self._connections):
            try:
                await self.stop(account_id)
            except Exception:
                log.exception("停止账号 %s 的连接出错", account_id)
        for adapter in self._channels.values():
            await adapter.aclose()
        self._channels.clear()

    async def start_auto_connect(self) -> None:
        for account in await self._db.accounts.list():
            if not account.enabled:
                continue
            if await self._db.settings.resolve("connection.auto_connect", account_id=account.id):
                try:
                    await self.start(account.id)
                except Exception as e:
                    log.warning("账号 %s 自动连接失败：%s", account.id, e)

    async def _consume(self, account_id: int, transport: InboundTransport) -> None:
        channel = await self.channel(account_id)
        async for event in transport.events():
            try:
                msg = channel.parse(event)
                if msg is None:
                    status, deadline = "ignored", None
                else:
                    status = "pending"
                    deadline = reply_deadline_ms(event.received_at_ms, channel.reply_window(msg.scene))
                inserted = await self._db.inbox.record(
                    event, status=status, reply_deadline=deadline, session_id=transport.resume_state().session_id
                )
                if inserted and status == "pending":
                    self._on_inbox()
                    await self._bus.publish(
                        BusEvent("inbox.new", {"account_id": account_id, "event_type": event.event_type})
                    )
            except Exception:
                log.exception("写入收件箱失败：%s", event.event_id)

    async def _on_state(self, event: BusEvent) -> None:
        account_id = event.data["account_id"]
        conn = self._connections.get(account_id)
        if conn is None:
            return
        target = event.data["to"]
        fields: dict[str, object] = {"state": str(target)}
        if target == ConnectionState.ONLINE:
            fields["last_ready_at"] = now_ms()
            if conn.transport is not None:
                resume = conn.transport.resume_state()
                fields["session_id"] = resume.session_id
                fields["last_seq"] = resume.seq
        if event.data.get("error"):
            fields["last_error"] = event.data["error"]
        await self._db.accounts.save_gateway(account_id, **fields)
        level = "error" if target == ConnectionState.ERROR else "info"
        if event.data.get("from") == ConnectionState.ONLINE and target == ConnectionState.RECONNECTING:
            level = "warning"
        await self._db.events.add(
            level,
            "connection",
            f"连接状态：{event.data.get('from')} → {target}",
            account_id=account_id,
            data={"error": event.data.get("error")} if event.data.get("error") else None,
        )
