"""ClaudeProvider：模型提供商插槽的 Claude 实现（官方 SDK anthropic 的 AsyncAnthropic）。

要点：
- 默认 claude-haiku-5-5，effort 设为 low（Haiku 5.5 默认是 medium，聊天用 low 更快更省）。
- 历史只追加、不改写：assistant 回复原样保存完整的 response.content（包括 thinking 块）。
- stop_reason == "refusal" 时没有正常内容，回一句固定话术；Haiku 没有服务端自动换模型的功能。
- system prompt 保持固定并开启 prompt caching；SDK 自带 2 次重试（429、5xx、网络错误）。
"""

from __future__ import annotations

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
        messages: list[dict[str, Any]] = []
        for m in history:
            text = f"[{m.speaker}] {m.content}" if m.role == "user" and m.speaker else m.content
            messages.append({"role": m.role, "content": text})
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
        if response.stop_reason == "refusal":
            return GenerateResult(
                text=self.refusal_reply,
                stop_reason="refusal",
                usage=usage,
                model=response.model,
                refused=True,
                raw_assistant=raw_assistant,
                stop_details=response.stop_details.to_dict() if response.stop_details else None,
            )
        text = "".join(block.text for block in response.content if block.type == "text")
        return GenerateResult(
            text=text,
            stop_reason=response.stop_reason,
            usage=usage,
            model=response.model,
            raw_assistant=raw_assistant,
        )

    async def aclose(self) -> None:
        await self._client.close()
