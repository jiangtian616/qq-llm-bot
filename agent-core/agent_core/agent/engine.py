"""Agent 引擎：判断是否触发回复、处理指令、构造上下文、调用提供商，并把结果交给发件箱。

只认统一的消息类型，不关心消息来自哪个平台、由哪个模型生成。

群里回复哪些消息（group.reply_mode）：平台推哪些消息由 QQ 客户端的群设置决定，推过来的消息里，
mention 只回复 @ 机器人或引用了它的消息，all 每条都回复，auto 让模型判断（@ 和引用机器人的照样回复，其余每条先单独问一次）。
判断用 group.judge.profile_id 指定的模型配置（为空时和回复同一个），标准是 group.judge.prompt，
判断的费用和处理过程记在这条消息的问答上：要回复就接着排队回复，不回复就标为 skipped。
没回复的消息照样存进中立历史，下次回复时作为期间的群聊拼进 user 消息，模型才知道中间聊了什么。

引用的消息：平台推送时带着被引用消息的内容和附件；按发送者标识在本会话里认出是谁发的（机器人自己也认得出来），
写成文字前面的「[引用……]」，引用的图片和本条的图片一起交给模型。

上下文的两道上限：
- 上一轮之后攒下的群消息只带最新的一部分（group.context_max_messages / group.context_max_chars）；
- 每轮结束后估算下一次请求的大小，超过 history.compact_at_tokens 或轮数超过 history.max_turns 时，
  把较早的部分交给提供商压缩成摘要，最近 history.keep_recent_turns 轮原文保留，开始新一段。
  提供商不支持压缩或没拿到摘要时，只保留最近几轮。

工具：开了图文回复（reply.rich_media.enabled）且提供商支持工具时，给模型回复工具，让它用结构化的回复段收尾；
开了联网搜索（tools.web_search.enabled）且提供商自带搜索时，声明服务端搜索。工具清单变了，原始历史就和新请求对不上，
按工具指纹从中立历史重建上下文，和换了提供商一样。整轮（包括所有工具调用）限时 TURN_TIMEOUT_S。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from agent_core.agent.prompt import (
    BUDGET_EXCEEDED_TEXT,
    CLEARED_TEXT,
    EMPTY_REPLY_TEXT,
    HELP_TEXT,
    JUDGE_FORMAT,
    RATE_LIMITED_TEXT,
)
from agent_core.agent.media import MediaFetcher, note_history
from agent_core.agent.providers import ProviderPool
from agent_core.agent.reply import compose_parts, compose_text, find_urls
from agent_core.agent.tools import PageImagesTool, ReplyTool
from agent_core.agent.web import fetch_page
from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms, start_of_local_day_ms
from agent_core.core.models import GenerateResult, InboundMessage, NeutralMessage, Scene
from agent_core.core.network import Proxy
from agent_core.core.ports import ChannelAdapter, LLMProvider, ServerTool, Tool, Toolset
from agent_core.core.trace import Stopwatch, Trace
from agent_core.infra.db.database import Database
from agent_core.infra.db.repos import Ingested, OutboxChunk
from agent_core.pipeline.middlewares.rate_limit import RateLimiter

log = logging.getLogger(__name__)

# 一轮最多几次工具调用（服务端搜索暂停后接着请求也算），整轮最长多少秒；群里被动回复的有效期是 5 分钟，要给发送留出时间。
MAX_TOOL_ROUNDS = 4
TURN_TIMEOUT_S = 150
# 判断要不要回复：给模型看最近多少条消息（每条最多多少字、一共最多多少字）、机器人设定的前多少字，最长等多少秒。
JUDGE_CONTEXT_MESSAGES = 12
JUDGE_LINE_CHARS = 300
JUDGE_CONTEXT_CHARS = 3000
JUDGE_PERSONA_CHARS = 800
JUDGE_TIMEOUT_S = 30


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """组装上下文和压缩历史用到的配置项（按会话 → 账号 → 全局取值）。"""

    pending_max_messages: int
    pending_max_chars: int
    compact_at_tokens: int
    max_turns: int
    keep_recent_turns: int


@dataclass(frozen=True, slots=True)
class ToolOptions:
    """这一轮给模型哪些工具（按会话 → 账号 → 全局取值）。"""

    rich_media: bool
    web_search: bool
    web_search_max_uses: int
    web_proxy: Proxy
    """读网页用的代理（和模型接口一样走 network.proxy_for_providers）。"""

    def toolset(self, provider: LLMProvider) -> Toolset:
        """顺序固定：服务端搜索、本地搜索、page_images、回复工具。

        搜索优先用提供商自带的服务端搜索；没有的话用提供商给的本地搜索工具（如 DeepSeek 的 Search 接口）。
        """
        caps = provider.capabilities()
        server: tuple[ServerTool, ...] = ()
        local: list[Tool] = []
        if self.web_search and "web_search" in caps.server_tools:
            server = (ServerTool("web_search", self.web_search_max_uses),)
        elif self.web_search and caps.tools and (search := provider.search_tool(self.web_search_max_uses)):
            local.append(search)
        if self.rich_media and caps.tools:
            local += [PageImagesTool(self.web_proxy), ReplyTool()]
        return Toolset(server=server, local=tuple(local), max_rounds=MAX_TOOL_ROUNDS)


class AgentEngine:
    def __init__(
        self,
        *,
        db: Database,
        providers: ProviderPool,
        bus: EventBus,
        on_outbox: Callable[[], None],
        media: MediaFetcher | None = None,
    ) -> None:
        self._db = db
        self._media = media or MediaFetcher()
        self._providers = providers
        self._bus = bus
        self._on_outbox = on_outbox
        self._limiter = RateLimiter()
        self._budget_warned: set[tuple[str, int]] = set()

    async def handle(self, inbox: Any, msg: InboundMessage, channel: ChannelAdapter) -> None:
        """处理收件箱里的一条消息。inbox 是 inbox_events 的一行（已加租约）。"""
        db = self._db
        conv = await db.conversations.find(msg.account_id, msg.scene, msg.target_openid)
        conv_id = conv.id if conv else None

        async def setting(key: str) -> Any:
            return await db.settings.resolve(key, account_id=msg.account_id, conversation_id=conv_id)

        msg = await self._resolve_quote(msg, conv_id)
        judging = False
        if msg.scene == Scene.GROUP:
            # 平台推哪些群消息由 QQ 客户端的群设置决定（仅 @机器人 / 全部消息）；推过来的里面回复哪些看 group.reply_mode。
            mode = await setting("group.reply_mode")
            addressed = msg.is_mention or (msg.quote is not None and msg.quote.by_bot)
            triggered = mode == "all" or addressed
            judging = mode == "auto" and not triggered
        else:
            triggered = bool(await setting("c2c.enabled"))

        command = None
        prefix = await setting("command.prefix")
        if (triggered or judging) and prefix and msg.content.startswith(prefix):
            name = msg.content[len(prefix) :].strip()
            if name in _COMMANDS:
                command = name
                triggered, judging = True, False

        # 预算用完或触发太频繁：这条消息只作为上下文保存，不调模型；回一句固定的提醒（限频每个窗口只提醒一次）。
        blocked, notice = False, None
        if triggered and command is None:
            blocked, notice = await self._check_limits(msg, setting)
            if blocked:
                triggered = False
        # 要判断的消息没人叫机器人：预算用完了就不判断，也不提醒。限频等判断要回复以后再算。
        if judging and await self._budget_exceeded(msg.account_id) is not None:
            judging = False

        profile = None
        if triggered and command is None:
            profile = await self._profile(await setting("provider_profile_id"))
            if profile is None:
                await db.events.add("error", "provider", "没有模型配置，无法回复", account_id=msg.account_id)
                await db.inbox.finish(inbox.id, "failed", error="没有模型配置")
                return
        judge_profile = None
        if judging:
            judge_profile = await self._profile(
                await setting("group.judge.profile_id") or await setting("provider_profile_id")
            )
            judging = judge_profile is not None

        turn_profile = profile or judge_profile
        ingested = await db.conversations.ingest(
            msg,
            triggered=triggered,
            provider=turn_profile.provider if turn_profile else None,
            model=turn_profile.model if turn_profile else None,
            profile_id=turn_profile.id if turn_profile else None,
            judging=judging,
        )
        if not ingested.duplicate:
            await self._bus.publish(
                BusEvent(
                    "message.in",
                    {
                        "account_id": msg.account_id,
                        "conversation_id": ingested.conversation_id,
                        "scene": msg.scene,
                        "triggered": triggered,
                    },
                )
            )
        if blocked and notice and not ingested.duplicate:
            await self._system_reply(notice, inbox, msg, ingested)
            return
        if judging:
            if ingested.turn_status == "judging":
                profile = await self._judge(inbox, msg, ingested, judge_profile, setting)
                if profile is None:
                    return
            elif ingested.turn_status == "skipped":
                # 崩溃重启后重新处理到这里：已经判断过不回复。
                await db.inbox.finish(inbox.id, "done", conversation_id=ingested.conversation_id)
                return
            else:
                # 之前已经判断要回复，问答换成了回复用的模型配置。
                profile = await self._profile(await setting("provider_profile_id"))
                if profile is None:
                    await db.inbox.finish(inbox.id, "failed", error="没有模型配置")
                    return
            triggered = True
        if not triggered:
            await db.inbox.finish(inbox.id, "done", conversation_id=ingested.conversation_id)
            return
        if command is not None:
            await self._run_command(command, inbox, msg, ingested)
            return
        if ingested.turn_status in ("succeeded", "refused"):
            # 崩溃重启后重新处理到这里：这一轮已经答过了。
            await db.inbox.finish(inbox.id, "done", conversation_id=ingested.conversation_id)
            return
        max_chunks = await setting("reply.max_chunks")
        vision = bool(await setting("vision.enabled"))
        limits = ContextLimits(
            pending_max_messages=int(await setting("group.context_max_messages") or 0),
            pending_max_chars=int(await setting("group.context_max_chars") or 0),
            compact_at_tokens=int(await setting("history.compact_at_tokens") or 0),
            max_turns=int(await setting("history.max_turns") or 0),
            keep_recent_turns=int(await setting("history.keep_recent_turns") or 0),
        )
        options = ToolOptions(
            rich_media=bool(await setting("reply.rich_media.enabled")),
            web_search=bool(await setting("tools.web_search.enabled")),
            web_search_max_uses=int(await setting("tools.web_search.max_uses") or 0),
            web_proxy=await self._db.settings.proxy("providers"),
        )
        await self._answer(inbox, msg, channel, ingested, profile, max_chunks, vision, limits, options)

    async def _resolve_quote(self, msg: InboundMessage, conv_id: int | None) -> InboundMessage:
        """认出被引用的消息是谁发的：机器人自己，或者这个会话里发过言的人。"""
        q = msg.quote
        if q is None or not q.sender_key or conv_id is None:
            return msg
        found = await self._db.conversations.quote_sender(conv_id, q.sender_key)
        if found is None:
            return msg
        by_bot, speaker = found
        return replace(msg, quote=replace(q, by_bot=by_bot, speaker=speaker))

    async def _judge(
        self, inbox: Any, msg: InboundMessage, ingested: Ingested, judge_profile: Any, setting: Callable[[str], Any]
    ) -> Any:
        """问模型这条群消息要不要回复。要回复时返回回复用的模型配置（问答开始排队）；不回复时收尾并返回 None。

        判断出错、模型不支持、要回复但触发太频繁，都按不回复处理（不提醒：没人叫机器人）。
        """
        db = self._db
        turn_id = ingested.turn_id
        assert turn_id is not None
        await db.conversations.mark_turn(turn_id, started_at=now_ms(), inbox_id=inbox.id)
        trace = Trace()
        watch = Stopwatch()
        reply, reason, error, result, text = False, "", None, None, ""
        reply_profile = await self._profile(await setting("provider_profile_id"))
        try:
            provider = await self._providers.get(judge_profile, await db.settings.proxy("providers"))
            recent = await db.conversations.recent(
                conversation_id=ingested.conversation_id,
                history_epoch=ingested.history_epoch,
                before_id=ingested.message_id,
                limit=JUDGE_CONTEXT_MESSAGES,
            )
            current = NeutralMessage(
                "user", msg.content, speaker=ingested.sender_label or None, attachments=msg.attachments, quote=msg.quote
            )
            persona = (reply_profile.system_prompt if reply_profile else "") or ""
            text = judge_text(recent, current, persona)
            instructions = f"{(await setting('group.judge.prompt') or '').strip()}\n\n{JUDGE_FORMAT}".strip()
            result = await asyncio.wait_for(provider.judge(instructions, text), JUDGE_TIMEOUT_S)
            if result is None:
                reason = f"模型配置「{judge_profile.name}」不支持判断要不要回复，按不回复处理"
            else:
                reply, reason = result.reply, result.reason
        except Exception as e:
            log.exception("判断要不要回复失败（turn %s）", turn_id)
            error = f"{type(e).__name__}: {e}"
            if isinstance(e, TimeoutError) and not str(e):
                error = f"TimeoutError: 超过 {JUDGE_TIMEOUT_S} 秒，已放弃"
            reason = "判断出错，按不回复处理"
        if reply and reply_profile is None:
            reply, reason = False, f"{reason}（但没有回复用的模型配置）"
        if reply:
            blocked, _ = await self._check_limits(msg, setting)
            if blocked:
                reply, reason = False, f"{reason}（但触发太频繁或预算已用完，不回复）"
        detail = [f"模型配置「{judge_profile.name}」：{judge_profile.provider} / {judge_profile.model}", f"理由：{reason}"]
        if result is not None and result.detail:
            detail.append(result.detail)
        trace.add(
            "judge",
            "判断：要回复" if reply else "判断：不回复",
            detail="\n".join(detail),
            data={"text": text} if text else None,
            error=error,
            started_at=watch.started_at,
            duration_ms=watch.ms,
        )
        cost = result.cost_usd if result is not None else 0.0
        if reply:
            await db.conversations.start_judged_turn(
                turn_id,
                ingested.message_id,
                provider=reply_profile.provider,
                model=reply_profile.model,
                provider_profile_id=reply_profile.id,
                cost_usd=cost,
            )
            await self._save_trace(turn_id, trace)
            return reply_profile
        usage = result.usage if result is not None else None
        await db.conversations.mark_turn(
            turn_id,
            status="skipped",
            model=result.model if result is not None else judge_profile.model,
            stop_reason="judged_skip",
            last_error=error[:2000] if error else None,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            cache_read_tokens=usage.cache_read_tokens if usage else 0,
            cache_write_tokens=usage.cache_write_tokens if usage else 0,
            cost_usd=cost,
            latency_ms=watch.ms,
            finished_at=now_ms(),
        )
        await self._save_trace(turn_id, trace)
        await db.inbox.finish(inbox.id, "done", conversation_id=ingested.conversation_id)
        await self._bus.publish(
            BusEvent("turn.skipped", {"turn_id": turn_id, "conversation_id": ingested.conversation_id, "reason": reason})
        )
        return None

    async def _profile(self, profile_id: Any) -> Any:
        if profile_id:
            profile = await self._db.profiles.get(int(profile_id))
            if profile is not None:
                return profile
        profiles = await self._db.profiles.list()
        return profiles[0] if profiles else None

    async def _check_limits(self, msg: InboundMessage, setting: Callable[[str], Any]) -> tuple[bool, str | None]:
        """返回 (是否拦下, 给用户的提醒)。"""
        exceeded = await self._budget_exceeded(msg.account_id)
        if exceeded is not None:
            return True, BUDGET_EXCEEDED_TEXT
        limit = int(await setting("rate_limit.per_user_per_min") or 0)
        if limit > 0:
            allowed, first = self._limiter.hit((msg.account_id, msg.scene, msg.sender_openid), limit)
            if not allowed:
                if first:
                    await self._db.events.add(
                        "warning",
                        "rate_limit",
                        f"用户 {msg.sender_openid[-6:]} 每分钟触发超过 {limit} 次，暂不回复",
                        account_id=msg.account_id,
                    )
                return True, RATE_LIMITED_TEXT if first else None
        return False, None

    async def _budget_exceeded(self, account_id: int) -> str | None:
        """全局预算按所有账号合计，账号预算只算这个账号；任何一个用完都暂停。返回用完的是哪一个。"""
        db = self._db
        today = start_of_local_day_ms()
        checks = [
            ("account", (await db.settings.list("account", account_id)).get("budget.daily_usd"), account_id),
            ("global", (await db.settings.list("global", 0)).get("budget.daily_usd"), None),
        ]
        for scope, budget, scope_account in checks:
            if budget is None or float(budget) <= 0:
                continue
            spent = await db.usage.cost_since(today, account_id=scope_account)
            if spent < float(budget):
                continue
            key = (f"{scope}:{scope_account}", today)
            if key not in self._budget_warned:
                self._budget_warned.add(key)
                label = "这个账号的每日预算" if scope == "account" else "全局每日预算"
                await db.events.add(
                    "warning",
                    "budget",
                    f"{label}已用完（已花 ${spent:.4f}，上限 ${float(budget):g}），今天暂停回复",
                    account_id=account_id,
                    data={"scope": scope, "spent_usd": round(spent, 6), "budget_usd": float(budget)},
                )
                await self._bus.publish(BusEvent("budget.exceeded", {"scope": scope, "account_id": account_id}))
            return scope
        return None

    async def _run_command(self, command: str, inbox: Any, msg: InboundMessage, ingested: Ingested) -> None:
        if command in ("清空", "clear"):
            await self._db.conversations.clear_history(ingested.conversation_id)
            text = CLEARED_TEXT
        else:
            text = HELP_TEXT
        await self._system_reply(text, inbox, msg, ingested)

    async def _system_reply(self, text: str, inbox: Any, msg: InboundMessage, ingested: Ingested) -> None:
        """不经过模型的固定回复（指令结果、提醒）；不进模型上下文。"""
        await self._db.conversations.save_turn_result(
            inbox_id=inbox.id,
            account_id=msg.account_id,
            conversation_id=ingested.conversation_id,
            history_epoch=ingested.history_epoch,
            turn_id=None,
            provider=None,
            provider_messages=[],
            next_seq=1,
            reply_text=text,
            chunks=[OutboxChunk(text)],
            reply_to_msg_id=msg.platform_msg_id,
            deadline=inbox.reply_deadline,
        )
        self._on_outbox()

    async def _answer(
        self,
        inbox: Any,
        msg: InboundMessage,
        channel: ChannelAdapter,
        ingested: Ingested,
        profile: Any,
        max_chunks: Any,
        vision: bool,
        limits: ContextLimits,
        options: ToolOptions,
    ) -> None:
        db = self._db
        turn_id = ingested.turn_id
        assert turn_id is not None
        await db.conversations.mark_turn(turn_id, status="running", started_at=now_ms(), inbox_id=inbox.id)
        trace = Trace()
        try:
            provider = await self._providers.get(profile, await db.settings.proxy("providers"))
            tools = options.toolset(provider)
            ctx = await db.conversations.load_context(
                conversation_id=ingested.conversation_id,
                history_epoch=ingested.history_epoch,
                provider=profile.provider,
                tools_fingerprint=tools.fingerprint(),
                current_message_id=ingested.message_id,
            )
            if ctx.provider_history:
                # 中间换过提供商（或工具清单）又换回来：别的提供商答过的那几轮补在后面，和本轮一起追加进原始历史。
                prefix: list[Any] = provider.build_context([note_history(m) for m in ctx.missing]) if ctx.missing else []
                history: list[Any] = [*ctx.provider_history, *prefix]
            else:
                # 这个提供商在当前 epoch 还没有原始历史（新会话、清空过、刚换了提供商或工具清单）：从中立历史重建，
                # 并把重建出来的前缀一起存下来，之后同一 epoch 里只追加。
                # 历史里的图片只留文字标记，不重新下载。
                history = provider.build_context([note_history(m) for m in ctx.neutral_before])
                prefix = list(history)
            current = NeutralMessage(
                "user", msg.content, speaker=ingested.sender_label or None, attachments=msg.attachments, quote=msg.quote
            )
            pending = trim_pending(ctx.pending, limits.pending_max_messages, limits.pending_max_chars)
            lines = [*pending, current]
            media = await self._media.collect(
                lines, vision=vision and provider.capabilities().vision, proxy=await db.settings.proxy("qq")
            )
            texts = [(t or "（空消息）") if m is current else t for m, t in zip(lines, media.texts, strict=True)]
            text = "\n".join(f"[{m.speaker}] {t}" if m.speaker else t for m, t in zip(lines, texts, strict=True))
            user_message = provider.build_context([NeutralMessage("user", text, images=media.images)])[0]
            # 存进历史的版本不带图片数据，只有文字里的「[图片1]」标记。
            stored_user = provider.build_context([NeutralMessage("user", text)])[0] if media.images else user_message
            # 用户自己发的链接和图片可以直接用（读网页、发图）。
            seed = {u for line in lines for u in find_urls(line.content)}
            seed |= {a.url for line in lines for a in line.attachments if a.url}
            seed |= {a.url for line in lines if line.quote for a in line.quote.attachments if a.url}
            tools = replace(tools, seed_links=frozenset(seed))

            kept = sum(1 for m in pending if any(m is p for p in ctx.pending))
            notes = [f"第 {inbox.attempts} 次处理（之前失败过，或重启后接着处理）"] if inbox.attempts > 1 else []
            notes.append(f"模型配置「{profile.name}」：{profile.provider} / {profile.model}")
            if ctx.provider_history:
                notes.append(
                    f"历史：接着这个提供商的原始历史 {len(ctx.provider_history)} 条"
                    + (f"，补上别的提供商答过的 {len(ctx.missing)} 条" if ctx.missing else "")
                )
            else:
                notes.append(f"历史：从中立历史重建 {len(ctx.neutral_before)} 条（新会话、清空过，或换了提供商、工具清单）")
            notes.append(f"这次请求共 {len(history) + 1} 条消息，历史第 {ingested.history_epoch} 段")
            names = [t.name for t in tools.server] + [t.name for t in tools.local]
            notes.append(f"工具：{'、'.join(names)}（指纹 {tools.fingerprint()}）" if names else "工具：无")
            if ctx.pending:
                omitted = len(ctx.pending) - kept
                notes.append(f"带上之前没回复的群消息 {kept} 条" + (f"，省略更早的 {omitted} 条" if omitted else ""))
            if media.images or media.failed:
                notes.append(
                    f"图片：交给模型 {len(media.images)} 张"
                    + (f"，没能读取 {len(media.failed)} 张：{'；'.join(media.failed)}" if media.failed else "")
                )
            if seed:
                notes.append(f"用户消息里的链接 {len(seed)} 个（可以直接读网页、发图）")
            notes.append(f"系统提示词 {len(profile.system_prompt or '')} 字")
            trace.add("context", "组装上下文", detail="\n".join(notes), data={"user_message": user_message})

            started = time.monotonic()
            result = await asyncio.wait_for(provider.generate([*history, user_message], tools, trace), TURN_TIMEOUT_S)
            latency_ms = int((time.monotonic() - started) * 1000)
        except Exception as e:
            log.exception("模型调用失败（turn %s）", turn_id)
            error = f"{type(e).__name__}: {e}"
            if isinstance(e, TimeoutError) and not str(e):
                error = f"TimeoutError: 整轮超过 {TURN_TIMEOUT_S} 秒，已放弃"
            trace.add("error", "这一轮失败", error=error)
            await self._save_trace(turn_id, trace)
            await db.conversations.mark_turn(turn_id, status="failed", last_error=error[:2000], finished_at=now_ms())
            await db.inbox.finish(inbox.id, "failed", error=error[:2000], conversation_id=ingested.conversation_id)
            await db.events.add("error", "provider", f"模型调用失败：{error[:200]}", account_id=msg.account_id)
            await self._bus.publish(BusEvent("turn.failed", {"turn_id": turn_id, "error": error[:200]}))
            return

        window = channel.reply_window(msg.scene)
        limit = min(int(max_chunks or window.max_replies), window.max_replies)
        watch = Stopwatch()
        if result.reply_parts is not None:
            reply = await compose_parts(
                result.reply_parts,
                limit,
                fallback=EMPTY_REPLY_TEXT,
                links=result.links | tools.seed_links,
                read_page=lambda url: fetch_page(url, options.web_proxy),
            )
        else:
            reply = compose_text(result.text, limit, fallback=EMPTY_REPLY_TEXT)
        trace.add(
            "compose",
            f"回复编排：{len(reply.chunks)} 段",
            detail="\n".join([*reply.notes, *(_chunk_line(i, c) for i, c in enumerate(reply.chunks, 1))]),
            started_at=watch.started_at,
            duration_ms=watch.ms,
        )
        # 拒答时没有正常内容，这一轮不进提供商历史（空的 assistant 消息会让下一次请求报错）。
        provider_messages = [] if result.refused else [*prefix, stored_user, *result.raw_messages]
        await db.conversations.save_turn_result(
            inbox_id=inbox.id,
            account_id=msg.account_id,
            conversation_id=ingested.conversation_id,
            history_epoch=ingested.history_epoch,
            turn_id=turn_id,
            provider=profile.provider,
            provider_messages=provider_messages,
            tools_fingerprint=tools.fingerprint(),
            next_seq=ctx.next_seq,
            reply_text=reply.text,
            chunks=reply.chunks,
            reply_to_msg_id=msg.platform_msg_id,
            deadline=inbox.reply_deadline,
            turn_fields={
                "status": "refused" if result.refused else "succeeded",
                "last_error": None,
                "model": result.model,
                "stop_reason": result.stop_reason,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cache_read_tokens": result.usage.cache_read_tokens,
                "cache_write_tokens": result.usage.cache_write_tokens,
                # 加在已有的费用上（判断要不要回复的费用已经记在这一轮上）
                "cost_usd": result.cost_usd,
                "search_requests": result.search_requests,
                "tool_rounds": result.tool_rounds,
                "latency_ms": latency_ms,
                "finished_at": now_ms(),
            },
        )
        self._on_outbox()
        await self._save_trace(turn_id, trace)
        await self._bus.publish(
            BusEvent(
                "turn.done",
                {
                    "turn_id": turn_id,
                    "conversation_id": ingested.conversation_id,
                    "refused": result.refused,
                    "latency_ms": latency_ms,
                    "cost_usd": round(result.cost_usd, 6),
                },
            )
        )
        if not result.refused:
            # 回复已经交给发件箱在发了；压缩在这之后做，只会让这个会话的下一条消息多等一会儿。
            try:
                await self._maybe_compact(
                    provider,
                    [*history, stored_user, *result.raw_messages],
                    result,
                    msg,
                    ingested,
                    profile,
                    limits,
                    tools,
                    trace,
                )
            except Exception as e:
                log.exception("历史压缩失败（会话 %s）", ingested.conversation_id)
                trace.add("error", "历史压缩失败", error=f"{type(e).__name__}: {e}")
            await self._save_trace(turn_id, trace)

    async def _save_trace(self, turn_id: int, trace: Trace) -> None:
        """处理过程只是给人看的，写不进去不影响回复。"""
        try:
            await self._db.traces.add_steps(turn_id, trace.drain())
        except Exception:
            log.exception("保存问答过程失败（turn %s）", turn_id)

    async def _maybe_compact(
        self,
        provider: LLMProvider,
        history: list[Any],
        result: GenerateResult,
        msg: InboundMessage,
        ingested: Ingested,
        profile: Any,
        limits: ContextLimits,
        tools: Toolset,
        trace: Trace,
    ) -> None:
        """这一轮之后历史太长了就压缩：较早的部分换成摘要，最近几轮原文保留，开始新一段。"""
        # 下一次请求会带上最后一次请求的输入和它的回复；工具循环里前面几次请求的用量不能加起来算。
        next_tokens = result.context_tokens
        starts = turn_starts(history)
        too_long = limits.compact_at_tokens > 0 and next_tokens >= limits.compact_at_tokens
        too_many = limits.max_turns > 0 and len(starts) >= limits.max_turns
        keep = max(limits.keep_recent_turns, 0)
        if not (too_long or too_many) or len(starts) <= keep:
            return
        cut = starts[-keep] if keep else len(history)
        older, recent = history[:cut], history[cut:]

        watch = Stopwatch()
        compacted = await provider.compact(older, tools) if provider.capabilities().compaction else None
        if compacted is not None:
            new_history, summary = [compacted.message, *recent], compacted.summary
        else:
            new_history, summary = recent, None
        db = self._db
        assert ingested.turn_id is not None
        epoch = await db.conversations.start_epoch(
            conversation_id=ingested.conversation_id,
            from_epoch=ingested.history_epoch,
            provider=profile.provider,
            tools_fingerprint=tools.fingerprint(),
            provider_messages=new_history,
            summary=summary,
            turn_id=ingested.turn_id,
            keep_turns=keep,
        )
        if compacted is not None:
            await db.conversations.add_turn_cost(ingested.turn_id, compacted.cost_usd)
        reason = f"预计 {next_tokens} token" if too_long else f"已有 {len(starts)} 轮"
        if compacted is not None:
            text = f"会话历史{reason}，较早的 {len(starts) - keep} 轮已压缩成摘要，保留最近 {keep} 轮原文"
            level = "info"
        else:
            text = f"会话历史{reason}，当前模型没有压缩出摘要，只保留最近 {keep} 轮"
            level = "warning"
        detail = [text]
        if compacted is not None:
            u = compacted.usage
            detail.append(
                f"输入 {u.input_tokens + u.cache_read_tokens} token · 输出 {u.output_tokens} token · ${compacted.cost_usd:.4f}"
            )
        if epoch is None:
            detail.append("会话在这期间被清空或已经换了一段，没有开始新的一段")
        trace.add(
            "compaction",
            f"压缩历史：{reason}",
            detail="\n".join(detail),
            data={"summary": summary} if summary else None,
            started_at=watch.started_at,
            duration_ms=watch.ms,
        )
        if epoch is None:
            return
        await db.events.add(level, "history", text, account_id=msg.account_id)
        await self._bus.publish(
            BusEvent(
                "history.compacted",
                {"conversation_id": ingested.conversation_id, "epoch": epoch, "summarized": compacted is not None},
            )
        )


def judge_text(recent: Sequence[NeutralMessage], current: NeutralMessage, persona: str) -> str:
    """判断请求的 user 消息：机器人设定的开头、最近的聊天（太长时只留最新的）、要判断的那条。"""

    def line(m: NeutralMessage) -> str:
        m = note_history(m)
        content = m.content if len(m.content) <= JUDGE_LINE_CHARS else m.content[:JUDGE_LINE_CHARS] + "…"
        who = "你" if m.role == "assistant" else m.speaker
        return f"[{who}] {content}" if who else content

    lines: list[str] = []
    chars = 0
    for m in reversed(recent):
        text = line(m)
        if chars + len(text) > JUDGE_CONTEXT_CHARS:
            break
        lines.append(text)
        chars += len(text)
    lines.reverse()
    parts = []
    if persona.strip():
        head = persona.strip()
        parts.append(f"机器人的设定（节选）：\n{head[:JUDGE_PERSONA_CHARS]}" + ("…" if len(head) > JUDGE_PERSONA_CHARS else ""))
    parts.append("群里最近的聊天：\n" + ("\n".join(lines) or "（没有）"))
    parts.append(f"要判断的新消息：\n{line(current) or '（空消息）'}")
    return "\n\n".join(parts)


def trim_pending(pending: Sequence[NeutralMessage], max_messages: int, max_chars: int) -> list[NeutralMessage]:
    """攒下的群消息只留最新的、不超过条数和字数上限的那部分；更早的换成一行说明。0 表示不限。"""
    kept: list[NeutralMessage] = []
    chars = 0
    for m in reversed(pending):
        if max_messages > 0 and len(kept) >= max_messages:
            break
        if max_chars > 0 and chars + len(m.content) > max_chars:
            break
        kept.append(m)
        chars += len(m.content)
    kept.reverse()
    omitted = len(pending) - len(kept)
    if omitted:
        kept.insert(0, NeutralMessage("user", f"（省略了更早的 {omitted} 条群消息）"))
    return kept


def _chunk_line(n: int, chunk: OutboxChunk) -> str:
    if chunk.card:
        return f"{n}. 卡片：{chunk.card.get('title')}（{chunk.card.get('url')}）"
    if chunk.image_url:
        return f"{n}. 图片：{chunk.image_url}"
    return f"{n}. 文字：{chunk.content}"


def turn_starts(history: Sequence[Any]) -> list[int]:
    """每一轮开头那条 user 消息的下标：第一条 user 消息，以及每条紧跟在一轮结尾后面的 user 消息。

    一轮的结尾是 assistant 回复，或者回复工具的结果（Claude 是只有 tool_result 的 user 消息，DeepSeek 是 tool 消息）；
    工具循环中间的工具结果后面跟的是 assistant，不会被当成开头。
    从这些位置切开，前后两半都是完整的轮次：压缩的部分以一轮的结尾收尾，保留的部分以 user 消息开头。
    """
    starts: list[int] = []
    for i, m in enumerate(history):
        if _role(m) != "user" or _only_tool_results(m):
            continue
        prev = history[i - 1] if i else None
        if not starts or _role(prev) in ("assistant", "tool") or _only_tool_results(prev):
            starts.append(i)
    return starts


def _role(m: Any) -> str | None:
    return m.get("role") if isinstance(m, dict) else None


def _only_tool_results(m: Any) -> bool:
    content = m.get("content") if _role(m) == "user" else None
    return isinstance(content, list) and bool(content) and all(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


_COMMANDS = {"清空", "clear", "帮助", "help"}
