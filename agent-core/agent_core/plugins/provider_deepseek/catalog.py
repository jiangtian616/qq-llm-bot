"""DeepSeek 的模型目录：模型 ID、思考力度、输出上限、价格、能不能看图。

按官方文档（quick_start/pricing、api/list-models、guides/thinking_mode、guides/vision）：
- 两个模型：deepseek-flash（DeepSeek-V4.1-Flash，能看图）和 deepseek-v4-pro（不能看图）；上下文 1M，输出最多 384K。
- 思考模式默认打开，思考力度 low / high / max，默认 high；none 表示关闭思考模式（Chat Completions 里传 thinking.type=disabled）。
- 价格分高峰和空闲两档，空闲时段半价。高峰时段是 UTC 周一到周五 01:00-04:00、06:00-10:00（北京时间 9-12 点、14-18 点），
  中国法定节假日全天算空闲；节假日这里不判断，按高峰价算，宁可多算。目录里给桌面端展示的是高峰价。
- 上下文缓存自动开启，没有缓存写入费，命中的部分按缓存价计。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from agent_core.core.ports.provider import ModelSpec, ProviderSpec

EFFORTS = ("none", "low", "high", "max")


@dataclass(frozen=True, slots=True)
class Price:
    """高峰时段的价格，美元 / 百万 token；空闲时段按一半算。"""

    input: float
    output: float
    cache_read: float


def _model(model_id: str, label: str, price: Price, *, vision: bool) -> tuple[ModelSpec, Price]:
    spec = ModelSpec(
        id=model_id,
        label=label,
        efforts=EFFORTS,
        default_effort="high",
        max_output_tokens=393_216,
        input_price=price.input,
        output_price=price.output,
        vision=vision,
    )
    return spec, price


_ENTRIES = [
    _model("deepseek-flash", "DeepSeek V4.1 Flash（快且便宜，能看图）", Price(0.30, 1.20, 0.006), vision=True),
    _model("deepseek-v4-pro", "DeepSeek V4 Pro（推理更强，不能看图）", Price(1.32, 3.96, 0.044), vision=False),
]

PRICES: dict[str, Price] = {spec.id: price for spec, price in _ENTRIES}

DEFAULT_MODEL = "deepseek-flash"

# 高峰时段（UTC 小时，左闭右开），只在周一到周五。
_PEAK_HOURS_UTC = ((1, 4), (6, 10))


def is_peak(at: datetime | None = None) -> bool:
    at = (at or datetime.now(UTC)).astimezone(UTC)
    return at.weekday() < 5 and any(start <= at.hour < end for start, end in _PEAK_HOURS_UTC)


# 不用流式请求，max_tokens 压在 16000 以内，免得一次请求太久超时。
MAX_TOKENS_LIMIT = 16_000

SPEC = ProviderSpec(
    name="deepseek",
    display_name="DeepSeek",
    api_key_label="DeepSeek API Key",
    api_key_help="在 DeepSeek 开放平台创建；按量从账户余额扣费，余额不足时请求会失败。价格按高峰时段显示，空闲时段半价",
    models=tuple(spec for spec, _ in _ENTRIES),
    default_model=DEFAULT_MODEL,
    max_tokens_limit=MAX_TOKENS_LIMIT,
)
