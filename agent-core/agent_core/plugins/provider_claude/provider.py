"""ClaudeProvider：模型提供商插槽的 Claude 实现（官方 SDK anthropic 的 AsyncAnthropic）。

要点：
- 默认 claude-haiku-5-5，effort 设为 low（Haiku 5.5 默认是 medium，聊天用 low 更快更省）。模型目录见 catalog.py。
- 历史只追加、不改写：assistant 回复原样保存完整的 response.content（包括 thinking 块）。
  但发请求时去掉历史里的 thinking 块：图片换成了文字标记、系统提示词改过之后，思考块前面的内容就和生成时不一样了，
  新模型（Haiku 5.5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1）发回这样的思考块会返回 400。聊天不需要前几轮的思考过程。
- 不传 thinking：这几个模型默认就是自适应思考；Haiku 4.5 这类不支持 effort 的模型也不传 effort。
- stop_reason == "refusal" 时没有正常内容，回一句固定话术；Haiku 没有服务端自动换模型的功能。
- system prompt 保持固定并开启 prompt caching；SDK 自带 2 次重试（429、5xx、网络错误）。
- 代理按 ctx.proxy：跟随系统代理时用 SDK 的默认行为；直连和自定义时覆盖掉 SDK 挂上的系统代理（socks 要 socksio）。
- 历史压缩用官方的按需压缩（beta compact-2026-09-04）：服务端写摘要，返回带签名的 compaction 块，放在新一段历史最前面原样发回；
  之后历史里带着这个块的请求都要走 beta 接口、带同一个 beta 头。压缩请求要带上和平时一样的工具。
- 工具循环：联网搜索用服务端的 web_search（基础版 web_search_20250305，Anthropic 那边执行，按次计费），
  本地工具由这里执行后把结果交回去接着请求。模型调用回复工具（terminal）就结束，参数作为回复交给引擎，
  同时补上它的工具结果：每个工具调用都要有结果，历史才能接着用，压缩请求也拒绝以没有结果的工具调用结尾。
  服务端工具跑太久会返回 pause_turn，原样发回去就接着做。本轮循环里的思考块原样带回，之前各轮的照旧去掉。
- 没调用回复工具时，回复取最后一次响应里最后一个工具块之后的文字：搜索前那句「我查一下」不发出去。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Sequence
from functools import partial
from typing import Any

from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from httpx2 import AsyncHTTPTransport
from anthropic.types import Message

from agent_core.core.models import GenerateResult, NeutralMessage, Usage
from agent_core.core.network import Proxy, ProxyMode
from agent_core.core.ports.provider import (
    CompactResult,
    LLMProvider,
    ProviderCapabilities,
    ProviderContext,
    ProviderSpec,
)
from agent_core.core.ports.tool import Toolset, ToolResult
from agent_core.plugins.provider_claude.catalog import (
    COMPACTION_MODELS,
    DEFAULT_MODEL,
    HAIKU_LONG_PROMPT,
    PRICES,
    SPEC,
)

log = logging.getLogger(__name__)

DEFAULT_EFFORT = "low"
DEFAULT_MAX_TOKENS = 1500
# 高 effort 下思考时间更长，60 秒不够。
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_REFUSAL_REPLY = "抱歉，这个问题我没法回答。"
# 去掉思考块后 assistant 消息没有内容了（比如思考用完了 max_tokens），用它占位，空消息会让请求报错。
EMPTY_ASSISTANT_TEXT = "（没有回复）"

_THINKING_BLOCKS = {"thinking", "redacted_thinking"}

WEB_SEARCH_TOOL = "web_search_20250305"
# 联网搜索每 1000 次 10 美元，搜索结果另按输入 token 计费。
WEB_SEARCH_PRICE = 0.01
REPLY_ACK = "已发送给用户。"
SKIPPED_BY_REPLY = "没有执行：这一轮已经回复了。"
SKIPPED_BY_LIMIT = "没有执行：这一轮的工具调用次数到上限了。"

COMPACT_BETA = "compact-2026-09-04"
# 摘要请求的 max_tokens 包括摘要前的思考，官方建议留几千。
COMPACT_MAX_TOKENS = 4096
COMPACT_INSTRUCTIONS = (
    "把这段 QQ 聊天压缩成一份摘要，之后的对话会接着这份摘要继续。用中文写。"
    "群聊里每条用户消息前的方括号是发言人标识，摘要里要保留谁说了什么。"
    "保留：每个人提到的关键事实、偏好和请求；已经达成的结论和约定；还没回答完的问题。省略寒暄和重复的内容。"
    "不要调用工具，只输出摘要正文。"
)


def _has_compaction(messages: Sequence[Any]) -> bool:
    return any(
        isinstance(m, dict)
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "compaction" for b in m["content"])
        for m in messages
    )


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


def without_thinking(messages: Sequence[Any], *, keep_from: int | None = None) -> list[Any]:
    """去掉历史 assistant 消息里的 thinking / redacted_thinking 块，其余原样保留。

    keep_from 之后（本轮工具循环里）的消息原样保留：工具调用接着请求时，要带回同一轮的思考块。
    """
    out: list[Any] = []
    for i, m in enumerate(messages):
        if keep_from is not None and i >= keep_from:
            out.append(m)
            continue
        if not isinstance(m, dict) or m.get("role") != "assistant" or not isinstance(m.get("content"), list):
            out.append(m)
            continue
        kept = [b for b in m["content"] if not (isinstance(b, dict) and b.get("type") in _THINKING_BLOCKS)]
        out.append({**m, "content": kept or [{"type": "text", "text": EMPTY_ASSISTANT_TEXT}]})
    return out


def tool_definitions(tools: Toolset) -> list[dict[str, Any]]:
    """翻译成接口的 tools 参数：服务端工具在前、本地工具在后，顺序固定。"""
    defs: list[dict[str, Any]] = []
    for server in tools.server:
        if server.name != "web_search":
            log.warning("Claude 没有服务端工具 %s，跳过", server.name)
            continue
        definition: dict[str, Any] = {"type": WEB_SEARCH_TOOL, "name": "web_search"}
        if server.max_uses:
            definition["max_uses"] = server.max_uses
        defs.append(definition)
    for tool in tools.local:
        definition = {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        if tool.strict:
            definition["strict"] = True
        defs.append(definition)
    return defs


def _usage(response: Any) -> Usage:
    return Usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=response.usage.cache_read_input_tokens or 0,
        cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
    )


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
    )


def _web_searches(response: Any) -> int:
    server = getattr(response.usage, "server_tool_use", None)
    return int(getattr(server, "web_search_requests", 0) or 0)


def _final_text(content: Sequence[Any]) -> str:
    """最后一个工具相关的块之后的文字；前面那些（比如搜索前的「我查一下」）是过程，不算回复。"""
    texts: list[str] = []
    for block in content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type not in _THINKING_BLOCKS:
            texts = []
    return "".join(texts)


async def _run_tool(tools: Toolset, call: Any) -> ToolResult:
    tool = tools.find(call.name)
    if tool is None:
        return ToolResult(f"没有这个工具：{call.name}", is_error=True)
    try:
        return await tool.run(call.input if isinstance(call.input, dict) else {})
    except Exception as e:
        log.exception("工具 %s 执行失败", call.name)
        return ToolResult(f"工具执行失败：{type(e).__name__}: {e}", is_error=True)


def _tool_results(calls: Sequence[Any], results: dict[str, ToolResult], missing: str) -> dict[str, Any]:
    """一条 user 消息，给每个工具调用一个结果；results 里没有的用 missing 作为出错结果。"""
    blocks = []
    for call in calls:
        result = results.get(call.id) or ToolResult(missing, is_error=True)
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call.id, "content": result.content}
        if result.is_error:
            block["is_error"] = True
        blocks.append(block)
    return {"role": "user", "content": blocks}


def _http_client(proxy: Proxy) -> DefaultAsyncHttpxClient:
    # DefaultAsyncHttpxClient 保留 SDK 自己的超时、连接数和 keepalive 设置，但它不看 trust_env，
    # 总会把系统代理挂到 http:// 和 https:// 上；直连和自定义代理时用同样的键覆盖掉（值为 None 表示直连）。
    if proxy.mode == ProxyMode.SYSTEM:
        return DefaultAsyncHttpxClient()
    target = AsyncHTTPTransport(proxy=proxy.url) if proxy.mode == ProxyMode.CUSTOM and proxy.url else None
    return DefaultAsyncHttpxClient(mounts={"http://": target, "https://": target, "all://": target})


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
                http_client=_http_client(ctx.proxy),
            )
        self._client = client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=True,
            vision=True,
            compaction=self.model in COMPACTION_MODELS,
            server_tools=frozenset({"web_search"}),
        )

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

    async def generate(self, messages: Sequence[Any], tools: Toolset | None = None) -> GenerateResult:
        tools = tools or Toolset()
        convo = list(messages)
        # 最后一条是本轮 user 消息；它之后、本轮循环里的 assistant 消息，思考块要原样带回去。
        turn_start = len(convo) - 1
        produced: list[Any] = []
        usage, cost, searches, rounds = Usage(), 0.0, 0, 0
        while True:
            response = await self._create(convo, tools, keep_thinking_from=turn_start)
            # 原样保存完整 content，下一轮直接追加进历史，不改写。
            raw_assistant = {"role": "assistant", "content": [block.to_dict() for block in response.content]}
            convo.append(raw_assistant)
            produced.append(raw_assistant)
            step = _usage(response)
            usage = _add(usage, step)
            step_searches = _web_searches(response)
            searches += step_searches
            cost += estimate_cost(self.model, step) + step_searches * WEB_SEARCH_PRICE
            result = partial(
                GenerateResult,
                usage=usage,
                model=response.model,
                raw_messages=produced,
                cost_usd=cost,
                search_requests=searches,
                tool_rounds=rounds,
                context_tokens=step.input_tokens + step.cache_read_tokens + step.cache_write_tokens + step.output_tokens,
            )
            if response.stop_reason == "refusal":
                return result(
                    text=self.refusal_reply,
                    stop_reason="refusal",
                    refused=True,
                    stop_details=response.stop_details.to_dict() if response.stop_details else None,
                )
            calls = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason != "pause_turn" and not calls:
                return result(text=_final_text(response.content), stop_reason=response.stop_reason)

            terminal = next((c for c in calls if (t := tools.find(c.name)) is not None and t.terminal), None)
            if terminal is not None:
                # 回复工具：参数就是回复，不再请求模型。所有工具调用都要有结果，历史才能接着用（压缩也要求这样）。
                produced.append(_tool_results(calls, {terminal.id: ToolResult(REPLY_ACK)}, SKIPPED_BY_REPLY))
                parts = terminal.input.get("parts") if isinstance(terminal.input, dict) else None
                return result(
                    text=_final_text(response.content),
                    stop_reason=response.stop_reason,
                    reply_parts=parts if isinstance(parts, list) else [],
                )
            if rounds >= tools.max_rounds:
                log.warning("工具调用超过 %s 轮，结束这一轮", tools.max_rounds)
                if calls:
                    produced.append(_tool_results(calls, {}, SKIPPED_BY_LIMIT))
                return result(text=_final_text(response.content), stop_reason="tool_rounds_exceeded")
            rounds += 1
            if calls:
                # 服务端工具暂停（pause_turn）时没有本地工具调用，原样发回去它就接着做。
                outcomes = await asyncio.gather(*(_run_tool(tools, c) for c in calls))
                tool_message = _tool_results(calls, {c.id: r for c, r in zip(calls, outcomes, strict=True)}, "")
                convo.append(tool_message)
                produced.append(tool_message)

    async def _create(self, messages: Sequence[Any], tools: Toolset, *, keep_thinking_from: int) -> Message:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": without_thinking(messages, keep_from=keep_thinking_from),
            "cache_control": {"type": "ephemeral"},
        }
        if self.effort is not None:
            kwargs["output_config"] = {"effort": self.effort}
        if self.ctx.system_prompt:
            kwargs["system"] = self.ctx.system_prompt
        if tools:
            kwargs["tools"] = tool_definitions(tools)
        if _has_compaction(messages):
            return await self._client.beta.messages.create(**kwargs, betas=[COMPACT_BETA])
        return await self._client.messages.create(**kwargs)

    async def compact(self, messages: Sequence[Any], tools: Toolset | None = None) -> CompactResult | None:
        if self.model not in COMPACTION_MODELS:
            return None
        # 和平时的请求用同一个 system；摘要请求不生成回复，只返回一个 compaction 块。
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": COMPACT_MAX_TOKENS,
            "messages": without_thinking(messages),
            "compaction": {"type": "summarize", "instructions": COMPACT_INSTRUCTIONS},
            "betas": [COMPACT_BETA],
        }
        if self.ctx.system_prompt:
            kwargs["system"] = self.ctx.system_prompt
        if self.effort is not None:
            kwargs["output_config"] = {"effort": self.effort}
        if tools:
            kwargs["tools"] = tool_definitions(tools)
        response = await self._client.beta.messages.create(**kwargs)

        # 摘要调用的用量只记在 usage.iterations 里，顶层的 input_tokens / output_tokens 是 0。
        usage = Usage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
        for it in response.usage.iterations or []:
            usage = Usage(
                input_tokens=usage.input_tokens + (it.input_tokens or 0),
                output_tokens=usage.output_tokens + (it.output_tokens or 0),
                cache_read_tokens=usage.cache_read_tokens + (getattr(it, "cache_read_input_tokens", None) or 0),
                cache_write_tokens=usage.cache_write_tokens + (getattr(it, "cache_creation_input_tokens", None) or 0),
            )
        block = next((b for b in response.content if b.type == "compaction"), None)
        if response.stop_reason != "compaction" or block is None:
            # max_tokens、refusal 等情况拿不到摘要，照样计费。
            log.warning("历史压缩没有拿到摘要（stop_reason=%s）", response.stop_reason)
            return None
        raw = block.to_dict()
        return CompactResult(
            message={"role": "assistant", "content": [raw]},
            summary=raw.get("content") or "",
            usage=usage,
            cost_usd=estimate_cost(self.model, usage),
        )

    async def aclose(self) -> None:
        await self._client.close()
