"""QQAdapter：渠道插槽的 QQ 实现。"""

from __future__ import annotations

from typing import Any

import aiohttp

from agent_core.core.models import (
    InboundMessage,
    OutboundMessage,
    RawEvent,
    ReplyWindow,
    Scene,
    SendErrorClass,
    SendResult,
)
from agent_core.core.ports.channel import ChannelAdapter, ChannelContext
from agent_core.plugins.channel_qq.constants import API_BASE, ErrorCode
from agent_core.plugins.channel_qq.errors import QQApiError
from agent_core.plugins.channel_qq.event_parser import EventParser
from agent_core.plugins.channel_qq.openapi_sender import OpenApiSender
from agent_core.plugins.channel_qq.token_manager import TokenManager

_ALREADY_SENT = {ErrorCode.DUPLICATE_MSG_SEQ}
_FATAL = {ErrorCode.MESSAGE_TOO_LONG, ErrorCode.MUTED}
_EXPIRED = {ErrorCode.REPLY_EXPIRED_OR_LIMIT}

# 被动回复：群聊 5 分钟内最多 5 次，单聊 60 分钟内最多 4 次。
_REPLY_WINDOWS = {
    Scene.GROUP: ReplyWindow(seconds=5 * 60, max_replies=5),
    Scene.C2C: ReplyWindow(seconds=60 * 60, max_replies=4),
}


class QQAdapter(ChannelAdapter):
    name = "qq"

    def __init__(self, ctx: ChannelContext, *, api_base: str = API_BASE) -> None:
        super().__init__(ctx)
        self._api_base = api_base
        self._parser = EventParser()
        self._session: aiohttp.ClientSession | None = None
        self._sender: OpenApiSender | None = None

    def parse(self, event: RawEvent) -> InboundMessage | None:
        return self._parser.parse(event)

    def reply_window(self, scene: Scene) -> ReplyWindow:
        return _REPLY_WINDOWS[scene]

    async def send(self, message: OutboundMessage) -> SendResult:
        payload: dict[str, Any] = {"content": message.content, "msg_type": message.msg_type, "msg_seq": message.msg_seq}
        if message.reply_to_msg_id:
            payload["msg_id"] = message.reply_to_msg_id
        elif message.reply_to_event_id:
            payload["event_id"] = message.reply_to_event_id
        payload.update(message.extra)
        sender = self._get_sender()
        if message.scene == Scene.GROUP:
            body = await sender.send_group_message(message.target_openid, payload)
        else:
            body = await sender.send_c2c_message(message.target_openid, payload)
        return SendResult(
            platform_message_id=body.get("id"),
            platform_ts=body.get("timestamp"),
            ref_idx=(body.get("ext_info") or {}).get("ref_idx"),
        )

    def classify_send_error(self, error: Exception) -> SendErrorClass:
        if not isinstance(error, QQApiError):
            # 网络错误、超时等。
            return SendErrorClass.RETRYABLE
        if error.code in _ALREADY_SENT:
            return SendErrorClass.ALREADY_SENT
        if error.code in _FATAL:
            return SendErrorClass.FATAL
        if error.code in _EXPIRED:
            return SendErrorClass.EXPIRED
        status = error.http_status or 0
        if 400 <= status < 500 and status != 429:
            return SendErrorClass.FATAL
        return SendErrorClass.RETRYABLE

    async def get_conversation_state(self, scene: Scene, target_openid: str) -> dict[str, Any] | None:
        if scene != Scene.GROUP:
            return None
        return await self._get_sender().get_group_bot_state(target_openid)

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
            self._sender = None

    def _get_sender(self) -> OpenApiSender:
        if self._sender is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
            tokens = TokenManager(self.ctx.app_id, self.ctx.secrets, self._session)
            self._sender = OpenApiSender(self._session, tokens, api_base=self._api_base)
        return self._sender
