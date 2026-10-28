from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_core.core.models import GenerateResult, NeutralMessage
from agent_core.core.ports.secrets import SecretReader


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    tools: bool = False
    vision: bool = False


@dataclass(slots=True)
class ProviderContext:
    secrets: SecretReader
    secret_ref: str
    """API Key 在密钥库里的键名（provider_profiles.secret_ref）。"""
    model: str | None = None
    system_prompt: str = ""
    params: dict[str, Any] = field(default_factory=dict)


class LLMProvider(abc.ABC):
    """模型提供商插槽。

    同一提供商的原始消息只追加、不改写；切换提供商时，新提供商用 build_context() 从中立历史重建上下文。
    """

    name: str = ""

    def __init__(self, ctx: ProviderContext) -> None:
        self.ctx = ctx

    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abc.abstractmethod
    def build_context(self, history: Sequence[NeutralMessage]) -> list[Any]:
        """从中立历史构造该提供商的消息列表（只在没有该提供商原始历史时使用）。"""

    @abc.abstractmethod
    async def generate(self, messages: Sequence[Any]) -> GenerateResult:
        """messages 是该提供商格式的完整历史，最后一条是本轮 user 消息。"""

    async def aclose(self) -> None:
        return None
