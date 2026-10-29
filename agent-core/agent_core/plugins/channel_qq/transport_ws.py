"""WebSocket 接入（当前实现）。

流程：取网关地址 → 收到 op 10 Hello → 发 op 2 Identify（或带 session_id + seq 的 op 6 Resume）→ READY / RESUMED
→ 按 heartbeat_interval 发 op 1 心跳。断线后逐次拉长间隔重连，优先 Resume 补收漏掉的事件。

读取循环只做解析和入队，不等待任何慢操作；心跳没有回应就判定为断线（电脑睡眠唤醒后也走这条路）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from agent_core.core.lifecycle import ConnectionState
from agent_core.core.models import RawEvent
from agent_core.core.ports.transport import InboundTransport, ResumeState, TransportContext
from agent_core.plugins.channel_qq.constants import (
    API_BASE,
    FATAL_CLOSE_CODES,
    INVALID_TOKEN_CLOSE_CODE,
    RESET_SESSION_CLOSE_CODES,
    EventType,
    OpCode,
)
from agent_core.plugins.channel_qq.errors import FatalGatewayError
from agent_core.plugins.channel_qq.openapi_sender import OpenApiSender
from agent_core.plugins.channel_qq.token_manager import TokenManager

log = logging.getLogger(__name__)

_STOP = object()
HELLO_TIMEOUT_S = 30
MAX_BACKOFF_S = 60


class WebSocketTransport(InboundTransport):
    name = "websocket"

    def __init__(
        self,
        ctx: TransportContext,
        *,
        session: aiohttp.ClientSession | None = None,
        tokens: TokenManager | None = None,
        api_base: str = API_BASE,
        gateway_url: str | None = None,
    ) -> None:
        super().__init__(ctx)
        self._session = session
        self._owns_session = session is None
        self._tokens = tokens
        self._api_base = api_base
        self._gateway_url = gateway_url
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._session_id = ctx.resume.session_id
        self._seq = ctx.resume.seq
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_ack = 0.0
        self._last_heartbeat = 0.0

    # ---- InboundTransport ----

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        if self._tokens is None:
            self._tokens = TokenManager(self.ctx.app_id, self.ctx.secrets, self._session)
        self._stopping = False
        await self.ctx.state.transition(ConnectionState.CONNECTING)
        self._task = asyncio.create_task(self._run(), name=f"qq-ws-{self.ctx.account_id}")

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            # 正常关闭，session 保留，下次启动还能 Resume。
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
            self._tokens = None
        self._queue.put_nowait(_STOP)
        await self.ctx.state.transition(ConnectionState.STOPPED)

    async def events(self) -> AsyncIterator[RawEvent]:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            yield item

    def resume_state(self) -> ResumeState:
        return ResumeState(self._session_id, self._seq)

    # ---- 连接循环 ----

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            reached_ready = False
            try:
                reached_ready = await self._connect_once()
            except FatalGatewayError as e:
                log.error("QQ 连接无法恢复：%s", e)
                await self.ctx.state.transition(ConnectionState.ERROR, str(e))
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("QQ 连接出错：%s: %s", type(e).__name__, e)
            if self._stopping:
                return
            if self.ctx.state.state == ConnectionState.ONLINE:
                await self.ctx.state.transition(ConnectionState.RECONNECTING)
            if reached_ready:
                backoff = 1.0
            delay = backoff + random.uniform(0, backoff / 2)
            log.info("%.1f 秒后重连 QQ", delay)
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def _connect_once(self) -> bool:
        """连接一次，直到断开。返回这次是否进入过 READY / RESUMED。"""
        assert self._session is not None and self._tokens is not None
        url = self._gateway_url or await OpenApiSender(
            self._session, self._tokens, api_base=self._api_base
        ).get_gateway_url()
        reached_ready = False
        async with self._session.ws_connect(url, autoping=True) as ws:
            self._ws = ws
            hello = await ws.receive_json(timeout=HELLO_TIMEOUT_S)
            if hello.get("op") != OpCode.HELLO:
                raise RuntimeError(f"期望 Hello，收到 op={hello.get('op')}")
            interval = hello["d"]["heartbeat_interval"] / 1000
            await self._identify_or_resume(ws)
            heartbeat = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if await self._handle(ws, json.loads(msg.data)):
                            reached_ready = True
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                heartbeat.cancel()
                self._ws = None
            self._on_close(ws.close_code)
        return reached_ready

    async def _identify_or_resume(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        assert self._tokens is not None
        token = await self._tokens.authorization()
        if self._session_id and self._seq is not None:
            log.info("Resume：session_id=%s seq=%s", self._session_id, self._seq)
            await ws.send_json(
                {"op": OpCode.RESUME, "d": {"token": token, "session_id": self._session_id, "seq": self._seq}}
            )
        else:
            await ws.send_json(
                {
                    "op": OpCode.IDENTIFY,
                    "d": {"token": token, "intents": self.ctx.intents, "shard": [0, 1], "properties": {}},
                }
            )
        self._last_ack = time.monotonic()

    async def _handle(self, ws: aiohttp.ClientWebSocketResponse, payload: dict[str, Any]) -> bool:
        op = payload.get("op")
        if op == OpCode.DISPATCH:
            return await self._dispatch(payload)
        if op == OpCode.HEARTBEAT_ACK:
            self._last_ack = time.monotonic()
        elif op == OpCode.HEARTBEAT:
            await ws.send_json({"op": OpCode.HEARTBEAT, "d": self._seq})
        elif op == OpCode.RECONNECT:
            log.info("平台要求重连")
            await ws.close()
        elif op == OpCode.INVALID_SESSION:
            log.warning("session 无效，改为重新 Identify；断线期间的消息可能收不回来")
            self._reset_session()
            await ws.close()
        return False

    async def _dispatch(self, payload: dict[str, Any]) -> bool:
        seq = payload.get("s")
        if isinstance(seq, int):
            self._seq = seq
        event_type = payload.get("t") or ""
        d = payload.get("d")
        if event_type == EventType.READY:
            self._session_id = d["session_id"]
            log.info("QQ 已连接：%s", (d.get("user") or {}).get("username"))
            await self.ctx.state.transition(ConnectionState.ONLINE)
            return True
        if event_type == EventType.RESUMED:
            log.info("Resume 成功")
            await self.ctx.state.transition(ConnectionState.ONLINE)
            return True
        # 交给 ConnectionManager：它在同一事务里写入 inbox_events 并更新 gateway_sessions.last_seq。
        self._queue.put_nowait(
            RawEvent(
                account_id=self.ctx.account_id,
                event_id=payload.get("id") or f"{self._session_id}:{seq}",
                event_type=event_type,
                seq=seq if isinstance(seq, int) else None,
                payload=d if isinstance(d, dict) else {"value": d},
                received_at_ms=int(time.time() * 1000),
            )
        )
        return False

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse, interval: float) -> None:
        while not ws.closed:
            await asyncio.sleep(interval)
            if self._last_heartbeat and self._last_ack < self._last_heartbeat:
                log.warning("心跳超时，判定为断线")
                await ws.close()
                return
            self._last_heartbeat = time.monotonic()
            await ws.send_json({"op": OpCode.HEARTBEAT, "d": self._seq})

    def _on_close(self, code: int | None) -> None:
        if code is None or self._stopping:
            return
        log.info("QQ 连接关闭，关闭码 %s", code)
        if code in FATAL_CLOSE_CODES:
            raise FatalGatewayError(f"关闭码 {code}：机器人已下架或被封禁")
        if code == INVALID_TOKEN_CLOSE_CODE and self._tokens is not None:
            self._tokens.invalidate()
        if code in RESET_SESSION_CLOSE_CODES:
            self._reset_session()

    def _reset_session(self) -> None:
        self._session_id = None
        self._seq = None
        self._last_heartbeat = 0.0
