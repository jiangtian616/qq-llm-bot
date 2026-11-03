"""DeepSeekProvider：模型提供商插槽的 DeepSeek 实现，走 Chat Completions 接口（POST /chat/completions）。

为什么用 Chat Completions 而不是 Responses：
- DeepSeek 的 Responses 接口也是无状态的，不支持 previous_response_id / store / truncation / context_management，
  每轮一样要回传完整历史，没有服务端压缩，比 Chat Completions 多不出聊天要用的能力（多出来的 web_search 只有 Pro 能用）；
- Chat Completions 是 DeepSeek 的主接口，用量里直接给出缓存命中 / 未命中的 token 数（prompt_cache_hit_tokens / miss），正好对应计费。
直接用 aiohttp 发请求，不引入 openai SDK：thinking、reasoning_content、缓存用量这些 DeepSeek 自己的字段 SDK 里本来也没有类型，
还能少一个要 Nuitka 编译的大依赖。

要点：
- 默认 deepseek-flash，effort 设为 low。effort 为 none 时传 thinking.type=disabled（不思考），其余传 thinking.type=enabled 和 reasoning_effort。
- 历史只追加、不改写：assistant 回复原样保存接口返回的 message（包括 reasoning_content、tool_calls）。
  不带 tools 的请求去掉 reasoning_content（官方说不用回传）；带 tools 的请求必须回传历史里全部的 reasoning_content，
  否则返回 400（guides/thinking_mode）。
- 工具调用（function calling）：本地工具在这里执行，结果作为 tool 消息交回去接着请求；模型调用回复工具就结束，
  同时补上它的 tool 消息。参数用 strict 模式，要走 /beta 地址，/beta 请求失败就退回普通地址。
  联网搜索没有服务端工具，search_tool() 给出本地的 web_search，背后是 Search 接口（/v1/tools/search，按次计费）。
- 中立历史里可能有连续的同角色消息（比如摘要后面紧跟提问），Chat Completions 不会自动合并，发请求前合并成一条。
- deepseek-v4-pro 不能看图，目录里标为 vision=False，引擎只给它「[图片]」文字标记。
- 上下文缓存自动开启，按前缀匹配；system prompt 保持固定，作为第一条 system 消息发送。
- 没有官方的历史压缩，compaction 为 False，compact() 用基类的默认实现返回 None，引擎只保留最近几轮。
- 错误按官方错误码分类（quick_start/error_codes）：429 / 500 / 503 和网络错误自动重试 2 次；401、402 这类直接失败，报中文原因。
- 判断群消息要不要回复（judge()）：单独一次小请求，JSON 输出（response_format=json_object），关掉思考模式。
- Base URL 可以用环境变量 DEEPSEEK_BASE_URL 覆盖（官方示例里用的就是这个名字），验证时指向本地模拟服务。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections.abc import Sequence
from functools import partial
from typing import Any

import aiohttp

from agent_core.core.models import GenerateResult, NeutralMessage, Usage
from agent_core.core.ports.provider import (
    JUDGE_SCHEMA,
    JudgeResult,
    LLMProvider,
    ProviderCapabilities,
    ProviderContext,
    ProviderSpec,
    parse_judge,
)
from agent_core.core.ports.tool import Tool, Toolset, ToolResult
from agent_core.core.trace import Stopwatch, Trace, short, timed
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
# 判断要不要回复：输出只有一个小 JSON。
JUDGE_MAX_TOKENS = 512
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
REPLY_ACK = "已发送给用户。"
SKIPPED_BY_REPLY = "没有执行：这一轮已经回复了。"
SKIPPED_BY_LIMIT = "没有执行：这一轮的工具调用次数到上限了。"
SEARCH_QUERY_MAX = 128
SEARCH_COUNT = 5
SEARCH_SUMMARY_MAX = 600
# Search 接口每 1000 次成功请求 5 元，按 1 美元约 7.2 元折算。
SEARCH_PRICE_USD = 0.005 / 7.2
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


def to_wire(system_prompt: str, messages: Sequence[Any], *, with_tools: bool = False) -> list[dict[str, Any]]:
    """把历史变成请求里的 messages：加上 system，合并连续的同角色消息。

    不带 tools 的请求去掉 reasoning_content（官方说不用回传）；带 tools 的请求必须原样回传历史里全部的 reasoning_content，
    否则返回 400。带工具调用的 assistant 消息、tool 消息原样保留，不合并（合并会丢掉 tool_calls）。
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}] if system_prompt else []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": m.get("content") or ""})
            continue
        content = m.get("content")
        calls = m.get("tool_calls") if role == "assistant" else None
        if role == "assistant" and not content and not calls:
            content = EMPTY_ASSISTANT_TEXT
        wire: dict[str, Any] = {"role": role, "content": content}
        if calls:
            wire["tool_calls"] = calls
        if with_tools and role == "assistant" and m.get("reasoning_content") is not None:
            wire["reasoning_content"] = m["reasoning_content"]
        prev = out[-1] if out else None
        mergeable = prev is not None and prev["role"] == role and (
            role == "user" or (role == "assistant" and not calls and not prev.get("tool_calls") and not with_tools)
        )
        if mergeable:
            assert prev is not None
            if isinstance(prev["content"], str) and isinstance(content, str):
                merged: Any = f"{prev['content']}\n{content}"
            else:
                merged = [*_parts(prev["content"]), *_parts(content)]
            out[-1] = {**prev, "content": merged}
        else:
            out.append(wire)
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
        self._strict = True
        """工具参数用 strict 模式（走 /beta）；/beta 请求失败过一次就不再用。"""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(tools=True, vision=self.spec is not None and self.spec.vision, compaction=False)

    def search_tool(self, max_uses: int) -> Tool | None:
        return DeepSeekSearchTool(self, max_uses)

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

    def _body(self, messages: Sequence[Any], tools: Toolset, *, strict: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": to_wire(self.ctx.system_prompt, messages, with_tools=bool(tools.local)),
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if tools.local:
            body["tools"] = [_function(t, strict=strict) for t in tools.local]
        if self.effort == "none":
            body["thinking"] = {"type": "disabled"}
        elif self.effort is not None:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = self.effort
        return body

    async def generate(
        self, messages: Sequence[Any], tools: Toolset | None = None, trace: Trace | None = None
    ) -> GenerateResult:
        tools = tools or Toolset()
        trace = trace or Trace()
        convo = list(messages)
        produced: list[Any] = []
        usage, cost, searches, rounds = Usage(), 0.0, 0, 0
        links = set(tools.seed_links)
        rejected = False  # 回复校验不过已经交回模型重来过一次
        while True:
            watch = Stopwatch()
            try:
                data, step = await self._chat(convo, tools)
            except Exception as e:
                trace.add(
                    "request",
                    "请求失败",
                    detail=f"历史 {len(convo)} 条消息 · 工具 {_tool_names(tools)}",
                    error=f"{type(e).__name__}: {e}",
                    started_at=watch.started_at,
                    duration_ms=watch.ms,
                )
                raise
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            finish = choice.get("finish_reason")
            step_cost = estimate_cost(self.model, step)
            trace.add(
                "request",
                f"请求模型 · {finish}",
                detail=self._response_detail(data, message, step, step_cost, len(convo), tools),
                data={"response": message},
                started_at=watch.started_at,
                duration_ms=watch.ms,
            )
            # 原样保存接口返回的 message（包括 reasoning_content、tool_calls），之后追加进历史，不改写。
            raw_assistant = {**message, "role": "assistant"}
            convo.append(raw_assistant)
            produced.append(raw_assistant)
            usage = _add(usage, step)
            cost += step_cost
            result = partial(
                GenerateResult,
                usage=usage,
                model=data.get("model") or self.model,
                raw_messages=produced,
                cost_usd=cost,
                search_requests=searches,
                tool_rounds=rounds,
                links=frozenset(links),
                context_tokens=step.input_tokens + step.cache_read_tokens + step.cache_write_tokens + step.output_tokens,
            )
            if finish == "content_filter":
                # 输出被内容过滤截掉了，没有能用的正文。
                trace.add("note", "输出被内容过滤截掉，回复固定话术")
                return result(text=self.refusal_reply, stop_reason=finish, refused=True)
            calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict)]
            if not calls:
                return result(text=message.get("content") or "", stop_reason=finish)

            terminal = next((c for c in calls if (t := tools.find(_call_name(c))) is not None and t.terminal), None)
            if terminal is not None:
                arguments = _call_arguments(terminal)
                tool = tools.find(_call_name(terminal))
                problem = tool.validate(arguments, frozenset(links)) if tool is not None else None
                accepted = problem is None or rejected or rounds >= tools.max_rounds
                trace.reply(arguments, problem, accepted=accepted)
                if accepted:
                    # 回复工具：参数就是回复，不再请求模型；每个工具调用都要有对应的 tool 消息，历史才能接着用。
                    produced += _tool_messages(calls, {terminal["id"]: ToolResult(REPLY_ACK)}, SKIPPED_BY_REPLY)
                    parts = arguments.get("parts")
                    return result(
                        text=message.get("content") or "",
                        stop_reason=finish,
                        reply_parts=parts if isinstance(parts, list) else [],
                    )
                rejected = True
                rounds += 1
                replies = _tool_messages(calls, {terminal["id"]: ToolResult(problem, is_error=True)}, SKIPPED_BY_REPLY)
                convo += replies
                produced += replies
                continue
            if rounds >= tools.max_rounds:
                log.warning("工具调用超过 %s 轮，结束这一轮", tools.max_rounds)
                trace.add("note", f"工具调用到上限（{tools.max_rounds} 轮），结束这一轮")
                produced += _tool_messages(calls, {}, SKIPPED_BY_LIMIT)
                return result(text=message.get("content") or "", stop_reason="tool_rounds_exceeded")
            rounds += 1
            seen = frozenset(links)
            runs = await asyncio.gather(*(timed(_run_tool(tools, c, seen)) for c in calls))
            outcomes = [outcome for outcome, _ in runs]
            for call, (outcome, run_watch) in zip(calls, runs, strict=True):
                trace.tool(_call_name(call), _call_arguments(call), outcome, run_watch)
            for outcome in outcomes:
                links.update(outcome.links)
                cost += outcome.cost_usd
                searches += outcome.search_requests
            replies = _tool_messages(calls, {c["id"]: r for c, r in zip(calls, outcomes, strict=True)}, "")
            convo += replies
            produced += replies

    def _response_detail(
        self, data: dict[str, Any], message: dict[str, Any], step: Usage, cost: float, history: int, tools: Toolset
    ) -> str:
        """一次请求的摘要：参数、用量，以及模型这次输出的思考、文字和工具调用。"""
        lines = [
            f"模型 {data.get('model') or self.model} · effort {self.effort or '默认'} · 历史 {history} 条消息"
            f" · 工具 {_tool_names(tools)}" + ("" if self._strict else "（非 strict）"),
            f"输入 {step.input_tokens + step.cache_read_tokens} token（缓存命中 {step.cache_read_tokens}）"
            f" · 输出 {step.output_tokens} token · ${cost:.4f}",
        ]
        if message.get("reasoning_content"):
            lines.append(f"[思考] {message['reasoning_content']}")
        if message.get("content"):
            lines.append(f"[文字] {message['content']}")
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                lines.append(f"[调用 {_call_name(call)}] {short(_call_arguments(call), 200)}")
        return "\n".join(lines)

    async def _chat(self, messages: Sequence[Any], tools: Toolset) -> tuple[dict[str, Any], Usage]:
        """strict 模式要走 /beta 地址（Beta 功能）；/beta 请求失败（不是限流、服务器故障）就关掉 strict 改走普通地址，之后都不再用。"""
        strict = self._strict and any(t.strict for t in tools.local)
        if not strict:
            return await self._complete(self._body(messages, tools, strict=False))
        try:
            return await self._complete(self._body(messages, tools, strict=True), beta=True)
        except DeepSeekError as e:
            if e.retryable or e.status in (401, 402):
                raise
            log.warning("DeepSeek strict 模式（/beta）请求失败，改用普通地址：%s", e)
            self._strict = False
            return await self._complete(self._body(messages, tools, strict=False))

    async def judge(self, instructions: str, text: str) -> JudgeResult | None:
        # json_object 模式要求提示词里出现「json」并给出格式样例，这里补在判断标准后面。
        system = f"{instructions}\n\n输出 json，格式：{json.dumps(JUDGE_SCHEMA['properties'], ensure_ascii=False)}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "max_tokens": JUDGE_MAX_TOKENS,
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        data, usage = await self._complete(body)
        cost = estimate_cost(self.model, usage)
        choice = (data.get("choices") or [{}])[0]
        output = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        reply, reason = parse_judge(output) or (False, f"没拿到判断结果（finish_reason={finish}），按不回复处理")
        detail = (
            f"模型 {data.get('model') or self.model} · 不思考 · finish_reason {finish}\n"
            f"输入 {usage.input_tokens + usage.cache_read_tokens} token（缓存命中 {usage.cache_read_tokens}）"
            f" · 输出 {usage.output_tokens} token · ${cost:.4f}\n[输出] {output}"
        )
        return JudgeResult(
            reply=reply, reason=reason, usage=usage, cost_usd=cost, model=data.get("model") or self.model, detail=detail
        )

    async def search(self, query: str) -> list[dict[str, Any]]:
        """调 DeepSeek 的 Search 接口（POST /v1/tools/search）。"""
        session = self._ensure_session()
        body = {"query": query[:SEARCH_QUERY_MAX], "count": SEARCH_COUNT}
        async with session.post(f"{self.base_url}/v1/tools/search", json=body) as resp:
            raw = await resp.read()
            if resp.status != 200:
                raise DeepSeekError(
                    f"{_STATUS_TEXT.get(resp.status, 'DeepSeek 搜索出错')}（HTTP {resp.status}）：{_error_message(raw)}",
                    status=resp.status,
                    retryable=resp.status in _RETRYABLE_STATUS,
                )
        results = json.loads(raw).get("results")
        return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []

    async def _complete(self, body: dict[str, Any], *, beta: bool = False) -> tuple[dict[str, Any], Usage]:
        """发请求并按错误分类重试；返回响应和所有尝试累计的用量。"""
        carried = Usage()
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            delay = RETRY_BACKOFF_S * 2**attempt
            try:
                data = await self._post(body, beta=beta)
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
                message = choice.get("message") or {}
                interrupted = choice.get("finish_reason") in _INTERRUPTED and not (
                    message.get("content") or message.get("tool_calls")
                )
                if not interrupted or last:
                    return data, usage
                carried = usage
                log.warning("DeepSeek 生成被打断（%s），%.1f 秒后重试", choice.get("finish_reason"), delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = client_session(
                self.ctx.proxy,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=aiohttp.ClientTimeout(total=self.timeout_s),
            )
        return self._session

    async def _post(self, body: dict[str, Any], *, beta: bool = False) -> dict[str, Any]:
        url = f"{self.base_url}/beta/chat/completions" if beta else f"{self.base_url}/chat/completions"
        async with self._ensure_session().post(url, json=body) as resp:
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


def _tool_names(tools: Toolset) -> str:
    return "、".join(t.name for t in tools.local) or "无"


def _function(tool: Tool, *, strict: bool) -> dict[str, Any]:
    function: dict[str, Any] = {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}
    if strict and tool.strict:
        function["strict"] = True
    return {"type": "function", "function": function}


def _call_name(call: dict[str, Any]) -> str:
    return str((call.get("function") or {}).get("name") or "")


def _call_arguments(call: dict[str, Any]) -> dict[str, Any]:
    """参数是 JSON 字符串；解析不了按空参数处理，工具自己报错。"""
    raw = (call.get("function") or {}).get("arguments")
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return arguments if isinstance(arguments, dict) else {}


async def _run_tool(tools: Toolset, call: dict[str, Any], links: frozenset[str]) -> ToolResult:
    tool = tools.find(_call_name(call))
    if tool is None:
        return ToolResult(f"没有这个工具：{_call_name(call)}", is_error=True)
    try:
        return await tool.run(_call_arguments(call), links)
    except Exception as e:
        log.exception("工具 %s 执行失败", tool.name)
        return ToolResult(f"工具执行失败：{type(e).__name__}: {e}", is_error=True)


def _tool_messages(calls: Sequence[dict[str, Any]], results: dict[str, ToolResult], missing: str) -> list[dict[str, Any]]:
    """每个工具调用一条 tool 消息；results 里没有的用 missing 作为出错结果。"""
    out = []
    for call in calls:
        result = results.get(call.get("id", "")) or ToolResult(missing, is_error=True)
        content = f"[出错] {result.content}" if result.is_error else result.content
        out.append({"role": "tool", "tool_call_id": call.get("id"), "content": content})
    return out


class DeepSeekSearchTool(Tool):
    """本地搜索工具，背后是 DeepSeek 的 Search 接口（按成功请求计费，每 1000 次 5 元）。每轮最多用 max_uses 次。"""

    name = "web_search"
    description = (
        "联网搜索网页，返回标题、网址、发布时间和摘要。问题涉及最近的事件、实时数据、具体的产品或人物等可能变化的信息时使用；"
        "常识、闲聊、计算不需要搜索。query 用简洁的关键词。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    strict = True

    def __init__(self, provider: DeepSeekProvider, max_uses: int) -> None:
        self._provider = provider
        self._max_uses = max_uses
        self._used = 0

    async def run(self, arguments: dict[str, Any], links: frozenset[str]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult("搜索关键词不能为空。", is_error=True)
        if self._max_uses and self._used >= self._max_uses:
            return ToolResult(f"这一轮的搜索次数到上限了（{self._max_uses} 次），请根据已有的结果回答。", is_error=True)
        self._used += 1
        try:
            results = await self._provider.search(query)
        except (DeepSeekError, aiohttp.ClientError, TimeoutError, ValueError) as e:
            log.warning("DeepSeek 搜索失败：%s", e)
            return ToolResult(f"搜索失败：{e}", is_error=True, search_requests=1)
        if not results:
            return ToolResult("没有搜到相关结果。", search_requests=1)
        lines = []
        for r in results:
            date = f"（{r['date'][:10]}）" if r.get("date") else ""
            summary = str(r.get("summary") or r.get("snippet") or "")[:SEARCH_SUMMARY_MAX]
            lines.append(f"- {r.get('title') or ''}{date}\n  {r.get('url') or ''}\n  {summary}")
        return ToolResult(
            "\n".join(lines),
            links=tuple(str(r["url"]) for r in results if r.get("url")),
            cost_usd=SEARCH_PRICE_USD,
            search_requests=1,
        )


def _error_message(raw: bytes) -> str:
    """错误响应是 {"error": {"message": ...}}，拿不到就截一段原文。"""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return str(message or raw[:200].decode("utf-8", "replace") or "没有错误信息")[:500]
