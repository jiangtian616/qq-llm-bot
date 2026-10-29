"""ClaudeProvider：模型提供商插槽的 Claude 实现（官方 SDK anthropic 的 AsyncAnthropic）。

要点：
- 默认 claude-haiku-5-5，effort 设为 low（Haiku 5.5 默认是 medium，聊天用 low 更快更省）。模型目录见 catalog.py。
- 历史只追加、不改写：assistant 回复原样保存完整的 response.content（包括 thinking 块）。
  但发请求时去掉历史里的 thinking 块：图片换成了文字标记、系统提示词改过之后，思考块前面的内容就和生成时不一样了，
  新模型（Haiku 5.5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1）发回这样的思考块会返回 400。聊天不需要前几轮的思考过程。
- 不传 thinking：这几个模型默认就是自适应思考；Haiku 4.5 这类不支持 effort 的模型也不传 effort。
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
from agent_core.core.ports.provider import LLMProvider, ProviderCapabilities, ProviderContext, ProviderSpec
from agent_core.plugins.provider_claude.catalog import DEFAULT_MODEL, HAIKU_LONG_PROMPT, PRICES, SPEC

DEFAULT_EFFORT = "low"
DEFAULT_MAX_TOKENS = 1500
# 高 effort 下思考时间更长，60 秒不够。
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_REFUSAL_REPLY = "抱歉，这个问题我没法回答。"
# 去掉思考块后 assistant 消息没有内容了（比如思考用完了 max_tokens），用它占位，空消息会让请求报错。
EMPTY_ASSISTANT_TEXT = "（没有回复）"

_THINKING_BLOCKS = {"thinking", "redacted_thinking"}


def estimate_cost(model: str, usage: Usage) -> float:
    """5 分钟缓存写入按输入价的 1.25 倍计，缓存读取用目录里各模型自己的价格。"""
    price = PRICES.get(model)
    if price is None:
        return 0.0
    prompt = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    if model == "claude-haiku-5-5" and prompt > 100_000:
        price = HAIKU_LONG_PROMPT
    return (
        usage.input_tokens * price.input
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.input * 1.25
        + usage.output_tokens * price.output
    ) / 1_000_000


def without_thinking(messages: Sequence[Any]) -> list[Any]:
    """去掉历史 assistant 消息里的 thinking / redacted_thinking 块，其余原样保留。"""
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            out.append(m)
            continue
        kept = [b for b in m["content"] if not (isinstance(b, dict) and b.get("type") in _THINKING_BLOCKS)]
        out.append({**m, "content": kept or [{"type": "text", "text": EMPTY_ASSISTANT_TEXT}]})
    return out


class MissingApiKeyError(RuntimeError):
    pass


class ClaudeProvider(LLMProvider):
    name = "claude"

    @classmethod
    def describe(cls) -> ProviderSpec:
        return SPEC

    def __init__(self, ctx: ProviderContext, *, client: AsyncAnthropic | None = None) -> None:
        super().__init__(ctx)
        params = ctx.params
        self.model = ctx.model or DEFAULT_MODEL
        # 目录里没有的模型照样调用，但不传 effort，免得模型不认这个参数。
        spec = SPEC.model(self.model)
        effort = params.get("effort", DEFAULT_EFFORT)
        self.effort: str | None = effort if spec is not None and effort in spec.efforts else None
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
            "messages": without_thinking(messages),
            "cache_control": {"type": "ephemeral"},
        }
        if self.effort is not None:
            kwargs["output_config"] = {"effort": self.effort}
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
