"""会话上下文：中立历史供所有提供商使用；各提供商的原始消息按提供商分开存，只追加不改写。

切换提供商时，新提供商从中立历史重建上下文，不读旧提供商的原始消息。历史太长就整段清空（epoch 加 1）。
"""

from __future__ import annotations

from typing import Any

from agent_core.core.models import NeutralMessage, Session


class ContextStore:
    """TODO(下一阶段)：基于 messages / provider_messages 表实现。"""

    async def provider_history(self, session: Session, provider: str) -> list[Any] | None:
        """该提供商在当前 epoch 的原始历史；没有时返回 None，由调用方用中立历史重建。"""
        raise NotImplementedError

    async def neutral_history(self, session: Session, max_turns: int) -> list[NeutralMessage]:
        raise NotImplementedError
