from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from agent_core.core.models import (
    InboundMessage,
    OutboundMessage,
    RawEvent,
    ReplyWindow,
    Scene,
    SendErrorClass,
    SendResult,
)
from agent_core.core.network import DIRECT, Proxy
from agent_core.core.ports.secrets import SecretReader


@dataclass(slots=True)
class ChannelContext:
    account_id: int
    app_id: str
    secrets: SecretReader
    proxy: Proxy = field(default=DIRECT)
    """调 QQ OpenAPI 用的代理（network.proxy_for_qq）。"""


class ChannelAdapter(abc.ABC):
    """渠道插槽：把平台事件转成统一消息、把统一回复发回平台。"""

    name: str = ""

    def __init__(self, ctx: ChannelContext) -> None:
        self.ctx = ctx

    @abc.abstractmethod
    def parse(self, event: RawEvent) -> InboundMessage | None:
        """不是消息的事件（如 READY、入群通知）返回 None。"""

    @abc.abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...

    @abc.abstractmethod
    def reply_window(self, scene: Scene) -> ReplyWindow: ...

    def classify_send_error(self, error: Exception) -> SendErrorClass:
        """按平台的错误码给发送失败分类；默认都当作可重试。"""
        return SendErrorClass.RETRYABLE

    async def get_conversation_state(self, scene: Scene, target_openid: str) -> dict[str, Any] | None:
        """可选能力：查询会话在平台上的状态（如 QQ 群里的消息接收设置）。不支持时返回 None。"""
        return None

    async def aclose(self) -> None:
        return None
