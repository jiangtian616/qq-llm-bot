"""Claude 的模型目录：模型 ID、支持的 effort 级别、默认 effort、输出上限和价格。

effort 级别按官方文档（build-with-claude/effort）：
- max：Fable 5.1 / 5、Opus 5.5 / 5 / 4.8 / 4.7 / 4.6、Sonnet 5.5 / 5 / 4.6、Haiku 5.5；
- xhigh：同上，但不含 Opus 4.6 和 Sonnet 4.6；
- Opus 5.5 和 Haiku 5.5 默认 medium，其余默认 high；Haiku 4.5 不接受 effort。
Claude Mythos 系列需要单独申请，不列出来。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.core.ports.provider import ModelSpec, ProviderSpec

_ALL = ("low", "medium", "high", "xhigh", "max")
_NO_XHIGH = ("low", "medium", "high", "max")


@dataclass(frozen=True, slots=True)
class Price:
    """美元 / 百万 token。缓存读取的倍率各模型不同（Opus 5.5 是 0.05 倍，Fable 5.1 是 0.025 倍），单独记。"""

    input: float
    output: float
    cache_read: float


def _model(
    model_id: str, label: str, price: Price, efforts: tuple[str, ...] = _ALL, default_effort: str | None = "high",
    *, max_output: int = 128_000, legacy: bool = False,
) -> tuple[ModelSpec, Price]:
    spec = ModelSpec(
        id=model_id,
        label=label,
        efforts=efforts,
        default_effort=default_effort if efforts else None,
        max_output_tokens=max_output,
        input_price=price.input,
        output_price=price.output,
        legacy=legacy,
    )
    return spec, price


_ENTRIES = [
    _model("claude-haiku-5-5", "Claude Haiku 5.5（最快最省，适合闲聊）", Price(0.10, 0.50, 0.01), default_effort="medium"),
    _model("claude-sonnet-5-5", "Claude Sonnet 5.5（推理更强）", Price(2.0, 10.0, 0.20)),
    _model("claude-opus-5-5", "Claude Opus 5.5（长文和复杂分析）", Price(4.0, 20.0, 0.20), default_effort="medium"),
    _model("claude-fable-5-1", "Claude Fable 5.1（能力最强，价格最高）", Price(10.0, 50.0, 0.25)),
    _model("claude-haiku-4-5", "Claude Haiku 4.5", Price(1.0, 5.0, 0.10), (), max_output=64_000, legacy=True),
    _model("claude-sonnet-5", "Claude Sonnet 5", Price(2.0, 10.0, 0.20), legacy=True),
    _model("claude-sonnet-4-6", "Claude Sonnet 4.6", Price(3.0, 15.0, 0.30), _NO_XHIGH, legacy=True),
    _model("claude-opus-5", "Claude Opus 5", Price(5.0, 25.0, 0.50), legacy=True),
    _model("claude-opus-4-8", "Claude Opus 4.8", Price(5.0, 25.0, 0.50), legacy=True),
    _model("claude-opus-4-7", "Claude Opus 4.7", Price(5.0, 25.0, 0.50), legacy=True),
    _model("claude-opus-4-6", "Claude Opus 4.6", Price(5.0, 25.0, 0.50), _NO_XHIGH, legacy=True),
    _model("claude-fable-5", "Claude Fable 5", Price(10.0, 50.0, 1.0), legacy=True),
]

PRICES: dict[str, Price] = {spec.id: price for spec, price in _ENTRIES}
# Haiku 5.5 的提示超过 10 万 token 时按另一档价格计。
HAIKU_LONG_PROMPT = Price(0.50, 2.50, 0.05)

DEFAULT_MODEL = "claude-haiku-5-5"

# 不用流式请求，max_tokens 压在 16000 以内，免得一次请求太久超时。
MAX_TOKENS_LIMIT = 16_000

SPEC = ProviderSpec(
    name="claude",
    display_name="Claude",
    api_key_label="Claude API Key",
    api_key_help="建议为本应用单独创建一个 Key，并在 Claude Console 给它所在的工作区设花费上限",
    models=tuple(spec for spec, _ in _ENTRIES),
    default_model=DEFAULT_MODEL,
    max_tokens_limit=MAX_TOKENS_LIMIT,
)
