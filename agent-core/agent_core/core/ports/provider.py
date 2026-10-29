from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_core.core.models import GenerateResult, NeutralMessage, Usage
from agent_core.core.ports.secrets import SecretReader


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    tools: bool = False
    vision: bool = False
    compaction: bool = False
    """能把较早的历史压缩成一条摘要消息（compact()）。"""


@dataclass(frozen=True, slots=True)
class CompactResult:
    message: Any
    """放在新一段历史最前面的摘要消息（提供商格式），之后原样发回。"""
    summary: str
    """摘要的文字，存进中立历史，换提供商时用。"""
    usage: Usage
    cost_usd: float


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    label: str
    efforts: tuple[str, ...] = ()
    """支持的 effort 级别，从低到高；空表示这个模型不接受 effort 参数。"""
    default_effort: str | None = None
    """不传 effort 时模型自己用的级别。"""
    max_output_tokens: int = 0
    input_price: float = 0.0
    output_price: float = 0.0
    """美元 / 百万 token，桌面端只用来展示。"""
    legacy: bool = False
    """上一代模型：仍然可用，界面上排在后面。"""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """提供商的说明：桌面端按它画模型配置界面，控制接口按它校验模型配置。"""

    name: str
    display_name: str
    api_key_label: str
    api_key_help: str
    models: tuple[ModelSpec, ...]
    default_model: str
    max_tokens_limit: int
    """这个实现允许的 max_tokens 上限（比如不用流式时要压低），和模型自己的上限取小的。"""

    def model(self, model_id: str) -> ModelSpec | None:
        return next((m for m in self.models if m.id == model_id), None)

    def check(self, model_id: str, params: dict[str, Any]) -> str | None:
        """模型配置不合法时返回原因。"""
        model = self.model(model_id)
        if model is None:
            return f"{self.display_name} 没有模型 {model_id}"
        effort = params.get("effort")
        if effort is not None and effort not in model.efforts:
            if not model.efforts:
                return f"{model.label} 不支持设置思考力度（effort）"
            return f"{model.label} 的思考力度只能是 {' / '.join(model.efforts)}"
        max_tokens = params.get("max_tokens")
        if max_tokens is not None:
            limit = min(model.max_output_tokens, self.max_tokens_limit)
            if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= limit:
                return f"最大输出 token 要是 1 到 {limit} 之间的整数"
        return None


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

    @classmethod
    @abc.abstractmethod
    def describe(cls) -> ProviderSpec:
        """不需要 API Key 就能拿到的说明：模型目录、各模型支持的参数。"""

    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abc.abstractmethod
    def build_context(self, history: Sequence[NeutralMessage]) -> list[Any]:
        """从中立历史构造该提供商的消息列表（只在没有该提供商原始历史时使用）。"""

    @abc.abstractmethod
    async def generate(self, messages: Sequence[Any]) -> GenerateResult:
        """messages 是该提供商格式的完整历史，最后一条是本轮 user 消息。"""

    async def compact(self, messages: Sequence[Any]) -> CompactResult | None:
        """把 messages（以 assistant 回复结尾）压缩成一条摘要消息；不支持或没拿到摘要时返回 None。"""
        return None

    async def aclose(self) -> None:
        return None
