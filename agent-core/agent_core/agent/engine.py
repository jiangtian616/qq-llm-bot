"""Agent 引擎：取会话历史、调用提供商、把结果交给发件箱。只认统一的消息类型。"""

from __future__ import annotations

from agent_core.core.models import GenerateResult, InboundMessage
from agent_core.core.ports.provider import LLMProvider


class AgentEngine:
    """TODO(下一阶段)：接上 context_store 和发件箱，历史只在拿到完整回复后整轮写入。"""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def answer(self, msg: InboundMessage) -> GenerateResult:
        raise NotImplementedError
