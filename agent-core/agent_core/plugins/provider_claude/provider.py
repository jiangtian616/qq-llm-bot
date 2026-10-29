"""ClaudeProvider：模型提供商插槽的 Claude 实现（官方 SDK anthropic 的 AsyncAnthropic）。

要点：
- 默认 claude-haiku-5-5，effort 设为 low（Haiku 5.5 默认是 medium，聊天用 low 更快更省）。
- 历史只追加、不改写：assistant 回复原样保存完整的 response.content（包括 thinking 块）。
- stop_reason == "refusal" 时没有正常内容，回一句固定话术；Haiku 没有服务端自动换模型的功能。
- system prompt 保持固定并开启 prompt caching；SDK 自带 2 次重试（429、5xx、网络错误）。
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from agent_core.core.models import GenerateResult, NeutralMessage, Usage
from agent_core.core.ports.provider import LLMProvider, ProviderCapabilities, ProviderContext

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT = "low"
DEFAULT_MAX_TOKENS = 1500
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_REFUSAL_REPLY = "抱歉，这个问题我没法回答。"

# 美元 / 百万 token（输入、输出）。缓存读取按输入价的 0.1 倍、5 分钟缓存写入按 1.25 倍计。
# Haiku 5.5 的提示超过 10 万 token 时是另一档价格（0.50 / 2.50）。
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
_HAIKU_LONG_PROMPT = (0.50, 2.50)


def estimate_cost(model: str, usage: Usage) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    if model == "claude-haiku-5-5" and prompt > 100_000:
        price = _HAIKU_LONG_PROMPT
    in_price, out_price = price
    return (
        usage.input_tokens * in_price
        + usage.cache_read_tokens * in_price * 0.1
        + usage.cache_write_tokens * in_price * 1.25
        + usage.output_tokens * out_price
    ) / 1_000_000


class MissingApiKeyError(RuntimeError):
    pass


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, ctx: ProviderContext, *, client: AsyncAnthropic | None = None) -> None:
        super().__init__(ctx)
        params = ctx.params
        self.model = ctx.model or DEFAULT_MODEL
        self.effort = params.get("effort", DEFAULT_EFFORT)
        self.max_tokens = int(params.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.refusal_reply = params.get("refusal_reply", DEFAULT_REFUSAL_REPLY)
        if client is None:
            api_key = ctx.secrets.get_secret(ctx.secret_ref)
            if not api_key:
                raise MissingApiKeyError(f"密钥库里没有 Claude API Key（{ctx.secret_ref}）")
            client = AsyncAnthropic(
                api_key=api_key,
                timeout=float(params.get("timeout_s", DEFAULT_TIMEOUT_S)),
                max_retries=2,
            )
        self._client = client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tools=True, vision=True)

    def build_context(self, history: Sequence[NeutralMessage]) -> list[dict[str, Any]]:
        # 群聊多人共用一份历史，user 消息前加上发言人标识，模型才分得清谁在说话。
        # 连续的同角色消息 API 会自动合并成一轮，不用手动拼接。
        # 图片放在文字前面（Claude 建议的顺序），每张前面加一个和文字里「[图片1]」对应的编号。
        messages: list[dict[str, Any]] = []
        for m in history:
            text = f"[{m.speaker}] {m.content}" if m.role == "user" and m.speaker else m.content
            if not m.images:
                messages.append({"role": m.role, "content": text})
                continue
            content: list[dict[str, Any]] = []
            for n, image in enumerate(m.images, 1):
                content.append({"type": "text", "text": f"[图片{n}]"})
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image.media_type,
                            "data": base64.b64encode(image.data).decode("ascii"),
                        },
                    }
                )
            content.append({"type": "text", "text": text})
            messages.append({"role": m.role, "content": content})
        return messages

    async def generate(self, messages: Sequence[Any]) -> GenerateResult:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": list(messages),
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if self.ctx.system_prompt:
            kwargs["system"] = self.ctx.system_prompt
        response: Message = await self._client.messages.create(**kwargs)

        # 原样保存完整 content，下一轮直接追加进历史，不改写。
        raw_assistant = {"role": "assistant", "content": [block.to_dict() for block in response.content]}
        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
        )
        cost = estimate_cost(self.model, usage)
        if response.stop_reason == "refusal":
            return GenerateResult(
                text=self.refusal_reply,
                stop_reason="refusal",
                usage=usage,
                model=response.model,
                refused=True,
                raw_assistant=raw_assistant,
                stop_details=response.stop_details.to_dict() if response.stop_details else None,
                cost_usd=cost,
            )
        text = "".join(block.text for block in response.content if block.type == "text")
        return GenerateResult(
            text=text,
            stop_reason=response.stop_reason,
            usage=usage,
            model=response.model,
            raw_assistant=raw_assistant,
            cost_usd=cost,
        )

    async def aclose(self) -> None:
        await self._client.close()
