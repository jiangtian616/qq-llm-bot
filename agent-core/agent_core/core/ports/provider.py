from __future__ import annotations

import abc
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_core.core.models import GenerateResult, NeutralMessage, Usage
from agent_core.core.network import DIRECT, Proxy
from agent_core.core.ports.secrets import SecretReader
from agent_core.core.ports.tool import Tool, Toolset
from agent_core.core.trace import Trace


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    tools: bool = False
    vision: bool = False
    compaction: bool = False
    """能把较早的历史压缩成一条摘要消息（compact()）。"""
    server_tools: frozenset[str] = frozenset()
    """提供商自带、在它那边执行的工具（如 web_search）。"""


@dataclass(frozen=True, slots=True)
class CompactResult:
    message: Any
    """放在新一段历史最前面的摘要消息（提供商格式），之后原样发回。"""
    summary: str
    """摘要的文字，存进中立历史，换提供商时用。"""
    usage: Usage
    cost_usd: float


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """判断群消息要不要回复的结果。"""

    reply: bool
    reason: str
    usage: Usage
    cost_usd: float
    model: str
    detail: str = ""
    """请求的摘要（参数、用量、原始输出），记进处理过程。"""


# 判断结果的格式：提供商用结构化输出（Claude 的 json_schema、DeepSeek 的 json_object）让模型按它输出。
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"reply": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["reply", "reason"],
    "additionalProperties": False,
}


def parse_judge(text: str) -> tuple[bool, str] | None:
    """解析模型输出的 {"reply": …, "reason": …}；格式不对返回 None。"""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("reply"), bool):
        return None
    return data["reply"], str(data.get("reason") or "")


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
    vision: bool = True
    """能看图片；不能时引擎只把「[图片]」这样的文字标记交给模型。"""


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
    proxy: Proxy = DIRECT
    """调模型接口用的代理（network.proxy_for_providers）。"""


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
    async def generate(
        self, messages: Sequence[Any], tools: Toolset | None = None, trace: Trace | None = None
    ) -> GenerateResult:
        """messages 是该提供商格式的完整历史，最后一条是本轮 user 消息。

        有工具时在这里跑工具循环：模型调用本地工具就执行、把结果交回去接着请求，直到模型不再调用工具，
        或者调用了 terminal 的工具（回复工具）。只有 capabilities().tools 为真的提供商会收到本地工具。
        trace 记下处理过程：每次请求（request）、服务端搜索（search）、本地工具（tool）、回复工具（reply）各一步。
        """

    def search_tool(self, max_uses: int) -> Tool | None:
        """没有服务端搜索、但能提供本地搜索工具的提供商（如 DeepSeek 的 Search 接口）在这里返回它。"""
        return None

    async def compact(self, messages: Sequence[Any], tools: Toolset | None = None) -> CompactResult | None:
        """把 messages（以一轮的结尾收尾）压缩成一条摘要消息；不支持或没拿到摘要时返回 None。

        tools 和平时请求用的一样：历史里有工具调用时，压缩请求也要带上同样的工具。
        """
        return None

    async def judge(self, instructions: str, text: str) -> JudgeResult | None:
        """判断一条群消息要不要回复：instructions 是判断标准和输出格式（作为 system），text 是最近的聊天和这条消息。

        单独一次小请求，不带工具、不进历史、思考力度用最低的；不支持时返回 None（引擎就不回复）。
        """
        return None

    async def aclose(self) -> None:
        return None
