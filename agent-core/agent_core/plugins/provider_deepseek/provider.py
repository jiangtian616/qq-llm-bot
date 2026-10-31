"""DeepSeekProvider：模型提供商插槽的 DeepSeek 实现，走 Chat Completions 接口（POST /chat/completions）。

为什么用 Chat Completions 而不是 Responses：
- DeepSeek 的 Responses 接口也是无状态的，不支持 previous_response_id / store / truncation / context_management，
  每轮一样要回传完整历史，没有服务端压缩，比 Chat Completions 多不出聊天要用的能力（多出来的 web_search 只有 Pro 能用）；
- Chat Completions 是 DeepSeek 的主接口，用量里直接给出缓存命中 / 未命中的 token 数（prompt_cache_hit_tokens / miss），正好对应计费。
直接用 aiohttp 发请求，不引入 openai SDK：thinking、reasoning_content、缓存用量这些 DeepSeek 自己的字段 SDK 里本来也没有类型，
还能少一个要 Nuitka 编译的大依赖。

要点：
- 默认 deepseek-flash，effort 设为 low。effort 为 none 时传 thinking.type=disabled（不思考），其余传 thinking.type=enabled 和 reasoning_effort。
- 历史只追加、不改写：assistant 回复原样保存接口返回的 message（包括 reasoning_content）。
  发请求时去掉 reasoning_content：按官方文档（guides/thinking_mode），不带 tools 的请求不需要回传思维链，传了也会被忽略。
- 中立历史里可能有连续的同角色消息（比如摘要后面紧跟提问），Chat Completions 不会自动合并，发请求前合并成一条。
- deepseek-v4-pro 不能看图，目录里标为 vision=False，引擎只给它「[图片]」文字标记。
- 上下文缓存自动开启，按前缀匹配；system prompt 保持固定，作为第一条 system 消息发送。
- 没有官方的历史压缩，compaction 为 False，compact() 用基类的默认实现返回 None，引擎只保留最近几轮。
- 错误按官方错误码分类（quick_start/error_codes）：429 / 500 / 503 和网络错误自动重试 2 次；401、402 这类直接失败，报中文原因。
- Base URL 可以用环境变量 DEEPSEEK_BASE_URL 覆盖（官方示例里用的就是这个名字），验证时指向本地模拟服务。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections.abc import Sequence
from typing import Any

import aiohttp

from agent_core.core.models import GenerateResult, NeutralMessage, Usage
from agent_core.core.ports.provider import LLMProvider, ProviderCapabilities, ProviderContext, ProviderSpec
from agent_core.core.ports.tool import Toolset
from agent_core.infra.http import client_session
from agent_core.plugins.provider_deepseek.catalog import DEFAULT_MODEL, PRICES, SPEC, is_peak

log = logging.getLogger(__name__)

BASE_URL_ENV = "DEEPSEEK_BASE_URL"
DEFAULT_BASE_URL = "https://api.deepseek.com"

DEFAULT_EFFORT = "low"
DEFAULT_MAX_TOKENS = 1500
# 思考模式下 max 力度想得更久，60 秒不够。
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_REFUSAL_REPLY = "抱歉，这个问题我没法回答。"
# assistant 消息没有正文时（比如思考用完了 max_tokens）用它占位，空消息可能让请求报错。
EMPTY_ASSISTANT_TEXT = "（没有回复）"

MAX_RETRIES = 2
RETRY_BACKOFF_S = 1.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_STATUS_TEXT = {
    400: "请求格式错误",
    401: "API Key 错误，认证失败",
    402: "账户余额不足，请到 DeepSeek 开放平台充值",
    422: "请求参数错误",
    429: "请求速率或并发达到上限",
    500: "DeepSeek 服务器故障",
    503: "DeepSeek 服务器繁忙",
}
# 推理资源不足、生成被中断：没有正文时重试一次，用量照样累计。
_INTERRUPTED = {"insufficient_system_resource", "aborted"}


class MissingApiKeyError(RuntimeError):
    pass


class DeepSeekError(RuntimeError):
    """DeepSeek 接口调用失败；retryable 表示稍后重试可能成功。"""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after: float | None = None


def estimate_cost(model: str, usage: Usage, *, peak: bool | None = None) -> float:
    """缓存命中的输入按缓存价，未命中的按输入价；空闲时段半价。没有缓存写入费。"""
    price = PRICES.get(model)
    if price is None:
        return 0.0
    cost = (
        usage.input_tokens * price.input
        + usage.cache_read_tokens * price.cache_read
        + usage.output_tokens * price.output
    ) / 1_000_000
    return cost if (is_peak() if peak is None else peak) else cost / 2


def parse_usage(raw: dict[str, Any] | None) -> Usage:
    """prompt_tokens = 命中 + 未命中；completion_tokens 已经包括思维链。"""
    raw = raw or {}
    hit = raw.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = (raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    miss = raw.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max((raw.get("prompt_tokens") or 0) - hit, 0)
    return Usage(input_tokens=miss, output_tokens=raw.get("completion_tokens") or 0, cache_read_tokens=hit)


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
    )


def _parts(content: Any) -> list[dict[str, Any]]:
    return content if isinstance(content, list) else [{"type": "text", "text": content or ""}]


def to_wire(system_prompt: str, messages: Sequence[Any]) -> list[dict[str, Any]]:
    """把历史变成请求里的 messages：加上 system，去掉 reasoning_content，合并连续的同角色消息。"""
    out: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}] if system_prompt else []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "assistant" and not content:
            content = EMPTY_ASSISTANT_TEXT
        if out and out[-1]["role"] == role and role in ("user", "assistant"):
            prev = out[-1]["content"]
            if isinstance(prev, str) and isinstance(content, str):
                merged: Any = f"{prev}\n{content}"
            else:
                merged = [*_parts(prev), *_parts(content)]
            out[-1] = {"role": role, "content": merged}
        else:
            out.append({"role": role, "content": content})
    return out


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    @classmethod
    def describe(cls) -> ProviderSpec:
        return SPEC

    def __init__(self, ctx: ProviderContext, *, session: aiohttp.ClientSession | None = None) -> None:
        super().__init__(ctx)
        params = ctx.params
        self.model = ctx.model or DEFAULT_MODEL
        self.spec = SPEC.model(self.model)
        # 目录里没有的模型照样调用，但不传思考参数，用模型自己的默认行为。
        effort = params.get("effort", DEFAULT_EFFORT)
        self.effort: str | None = effort if self.spec is not None and effort in self.spec.efforts else None
        self.max_tokens = int(params.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.refusal_reply = params.get("refusal_reply", DEFAULT_REFUSAL_REPLY)
        self.timeout_s = float(params.get("timeout_s", DEFAULT_TIMEOUT_S))
        self.base_url = (os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = ""
        if session is None:
            self._api_key = ctx.secrets.get_secret(ctx.secret_ref) or ""
            if not self._api_key:
                raise MissingApiKeyError(f"密钥库里没有 DeepSeek API Key（{ctx.secret_ref}）")
        self._session = session

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tools=False, vision=self.spec is not None and self.spec.vision, compaction=False)

    def build_context(self, history: Sequence[NeutralMessage]) -> list[dict[str, Any]]:
        # 群聊多人共用一份历史，user 消息前加上发言人标识，模型才分得清谁在说话。
        # 图片放在文字前面，每张前面加一个和文字里「[图片1]」对应的编号；图片只能放在 user 消息里。
        messages: list[dict[str, Any]] = []
        for m in history:
            text = f"[{m.speaker}] {m.content}" if m.role == "user" and m.speaker else m.content
            if not m.images:
                messages.append({"role": m.role, "content": text})
                continue
            content: list[dict[str, Any]] = []
            for n, image in enumerate(m.images, 1):
                data = base64.b64encode(image.data).decode("ascii")
                content.append({"type": "text", "text": f"[图片{n}]"})
                content.append({"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{data}"}})
            content.append({"type": "text", "text": text})
            messages.append({"role": m.role, "content": content})
        return messages

    def _body(self, messages: Sequence[Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_wire(self.ctx.system_prompt, messages),
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.effort == "none":
            body["thinking"] = {"type": "disabled"}
        elif self.effort is not None:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = self.effort
        return body

    async def generate(self, messages: Sequence[Any], tools: Toolset | None = None) -> GenerateResult:
        # 还没接 function calling（capabilities().tools 为假），引擎不会给本地工具，也没有服务端工具。
        data, usage = await self._complete(self._body(messages))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        finish = choice.get("finish_reason")
        # 原样保存接口返回的 message，下一轮直接追加进历史，不改写。
        raw_assistant = {**message, "role": "assistant"}
        cost = estimate_cost(self.model, usage)
        model = data.get("model") or self.model
        context_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens + usage.output_tokens
        if finish == "content_filter":
            # 输出被内容过滤截掉了，没有能用的正文。
            return GenerateResult(
                text=self.refusal_reply,
                stop_reason=finish,
                usage=usage,
                model=model,
                refused=True,
                raw_messages=[raw_assistant],
                cost_usd=cost,
                context_tokens=context_tokens,
            )
        return GenerateResult(
            text=message.get("content") or "",
            stop_reason=finish,
            usage=usage,
            model=model,
            raw_messages=[raw_assistant],
            cost_usd=cost,
            context_tokens=context_tokens,
        )

    async def _complete(self, body: dict[str, Any]) -> tuple[dict[str, Any], Usage]:
        """发请求并按错误分类重试；返回响应和所有尝试累计的用量。"""
        carried = Usage()
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            delay = RETRY_BACKOFF_S * 2**attempt
            try:
                data = await self._post(body)
            except DeepSeekError as e:
                if not e.retryable or last:
                    raise
                delay = e.retry_after or delay
                log.warning("DeepSeek 请求失败，%.1f 秒后重试：%s", delay, e)
            except (aiohttp.ClientError, TimeoutError) as e:
                error = DeepSeekError(f"连不上 DeepSeek（{type(e).__name__}: {e}）", retryable=True)
                if last:
                    raise error from e
                log.warning("DeepSeek 请求失败，%.1f 秒后重试：%s", delay, error)
            else:
                usage = _add(carried, parse_usage(data.get("usage")))
                choice = (data.get("choices") or [{}])[0]
                interrupted = choice.get("finish_reason") in _INTERRUPTED and not (choice.get("message") or {}).get("content")
                if not interrupted or last:
                    return data, usage
                carried = usage
                log.warning("DeepSeek 生成被打断（%s），%.1f 秒后重试", choice.get("finish_reason"), delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            self._session = client_session(
                self.ctx.proxy,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=aiohttp.ClientTimeout(total=self.timeout_s),
            )
        async with self._session.post(f"{self.base_url}/chat/completions", json=body) as resp:
            # 非流式请求在开始推理前会先返回一些空行保活，json.loads 会跳过开头的空白。
            raw = await resp.read()
            if resp.status == 200:
                try:
                    return json.loads(raw)
                except ValueError as e:
                    raise DeepSeekError(f"DeepSeek 返回的不是 JSON：{raw[:200]!r}", status=200, retryable=True) from e
            error = DeepSeekError(
                f"{_STATUS_TEXT.get(resp.status, 'DeepSeek 接口出错')}（HTTP {resp.status}）：{_error_message(raw)}",
                status=resp.status,
                retryable=resp.status in _RETRYABLE_STATUS,
            )
            retry_after = resp.headers.get("Retry-After", "")
            if retry_after.isdigit():
                error.retry_after = min(float(retry_after), 30.0)
            raise error

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()


def _error_message(raw: bytes) -> str:
    """错误响应是 {"error": {"message": ...}}，拿不到就截一段原文。"""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return str(message or raw[:200].decode("utf-8", "replace") or "没有错误信息")[:500]
