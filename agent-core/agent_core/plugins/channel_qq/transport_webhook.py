"""Webhook 接入（预留）：需要公网 HTTPS 地址、Ed25519 验签，并在 3 秒内回 op 12 ACK。

本项目只做本地部署，当前不实现；接口留在这里，以后新增实现、改配置即可切换。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_core.core.models import RawEvent
from agent_core.core.ports.transport import InboundTransport, ResumeState

_NOT_IMPLEMENTED = "Webhook 接入方式尚未实现，请使用 websocket"


class WebhookTransport(InboundTransport):
    name = "webhook"

    async def start(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def stop(self) -> None:
        return None

    def events(self) -> AsyncIterator[RawEvent]:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def resume_state(self) -> ResumeState:
        # Webhook 由平台推送，没有 Resume 的概念。
        return ResumeState()
