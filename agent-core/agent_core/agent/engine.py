"""Agent 引擎：判断是否触发回复、处理指令、构造上下文、调用提供商，并把结果交给发件箱。

只认统一的消息类型，不关心消息来自哪个平台、由哪个模型生成。

上下文的两道上限：
- 上一轮之后攒下的群消息只带最新的一部分（group.context_max_messages / group.context_max_chars）；
- 每轮结束后估算下一次请求的大小，超过 history.compact_at_tokens 或轮数超过 history.max_turns 时，
  把较早的部分交给提供商压缩成摘要，最近 history.keep_recent_turns 轮原文保留，开始新一段。
  提供商不支持压缩或没拿到摘要时，只保留最近几轮。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agent_core.agent.prompt import (
    BUDGET_EXCEEDED_TEXT,
    CLEARED_TEXT,
    EMPTY_REPLY_TEXT,
    HELP_TEXT,
    RATE_LIMITED_TEXT,
)
from agent_core.agent.media import MediaFetcher, note_history
from agent_core.agent.providers import ProviderPool
from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms, start_of_local_day_ms
from agent_core.core.models import GenerateResult, InboundMessage, NeutralMessage, Scene
from agent_core.core.ports import ChannelAdapter, LLMProvider
from agent_core.infra.db.database import Database
from agent_core.infra.db.repos import Ingested, OutboxChunk
from agent_core.pipeline.middlewares.rate_limit import RateLimiter
from agent_core.pipeline.outbox import split_reply

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """组装上下文和压缩历史用到的配置项（按会话 → 账号 → 全局取值）。"""

    pending_max_messages: int
    pending_max_chars: int
    compact_at_tokens: int
    max_turns: int
    keep_recent_turns: int


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

        if msg.scene == Scene.GROUP:
            # 平台推送哪些群消息由 QQ 客户端里的群设置决定（仅 @机器人 / 全部消息），推过来的都回复。
            triggered = True
        else:
            triggered = bool(await setting("c2c.enabled"))

        command = None
        prefix = await setting("command.prefix")
        if triggered and prefix and msg.content.startswith(prefix):
            name = msg.content[len(prefix) :].strip()
            if name in _COMMANDS:
                command = name

        # 预算用完或触发太频繁：这条消息只作为上下文保存，不调模型；回一句固定的提醒（限频每个窗口只提醒一次）。
        blocked, notice = False, None
        if triggered and command is None:
            blocked, notice = await self._check_limits(msg, setting)
            if blocked:
                triggered = False

        profile = None
        if triggered and command is None:
            profile = await self._profile(await setting("provider_profile_id"))
            if profile is None:
                await db.events.add("error", "provider", "没有模型配置，无法回复", account_id=msg.account_id)
                await db.inbox.finish(inbox.id, "failed", error="没有模型配置")
                return

        ingested = await db.conversations.ingest(
            msg,
            triggered=triggered,
            provider=profile.provider if profile else None,
            model=profile.model if profile else None,
            profile_id=profile.id if profile else None,
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
        await self._answer(inbox, msg, channel, ingested, profile, max_chunks, vision, limits)

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
    ) -> None:
        db = self._db
        turn_id = ingested.turn_id
        assert turn_id is not None
        await db.conversations.mark_turn(turn_id, status="running", started_at=now_ms())
        try:
            provider = await self._providers.get(profile)
            ctx = await db.conversations.load_context(
                conversation_id=ingested.conversation_id,
                history_epoch=ingested.history_epoch,
                provider=profile.provider,
                current_message_id=ingested.message_id,
            )
            if ctx.provider_history:
                # 中间换过提供商又换回来：别的提供商答过的那几轮补在后面，和本轮一起追加进原始历史。
                prefix: list[Any] = provider.build_context([note_history(m) for m in ctx.missing]) if ctx.missing else []
                history: list[Any] = [*ctx.provider_history, *prefix]
            else:
                # 这个提供商在当前 epoch 还没有原始历史（新会话、清空过、或刚换了提供商）：从中立历史重建，
                # 并把重建出来的前缀一起存下来，之后同一 epoch 里只追加。
                # 历史里的图片只留文字标记，不重新下载。
                history = provider.build_context([note_history(m) for m in ctx.neutral_before])
                prefix = list(history)
            current = NeutralMessage("user", msg.content, speaker=ingested.sender_label or None, attachments=msg.attachments)
            lines = [*trim_pending(ctx.pending, limits.pending_max_messages, limits.pending_max_chars), current]
            media = await self._media.collect(lines, vision=vision and provider.capabilities().vision)
            texts = [(t or "（空消息）") if m is current else t for m, t in zip(lines, media.texts, strict=True)]
            text = "\n".join(f"[{m.speaker}] {t}" if m.speaker else t for m, t in zip(lines, texts, strict=True))
            user_message = provider.build_context([NeutralMessage("user", text, images=media.images)])[0]
            # 存进历史的版本不带图片数据，只有文字里的「[图片1]」标记。
            stored_user = provider.build_context([NeutralMessage("user", text)])[0] if media.images else user_message

            started = time.monotonic()
            result = await provider.generate([*history, user_message])
            latency_ms = int((time.monotonic() - started) * 1000)
        except Exception as e:
            log.exception("模型调用失败（turn %s）", turn_id)
            error = f"{type(e).__name__}: {e}"
            await db.conversations.mark_turn(turn_id, status="failed", last_error=error[:2000], finished_at=now_ms())
            await db.inbox.finish(inbox.id, "failed", error=error[:2000], conversation_id=ingested.conversation_id)
            await db.events.add("error", "provider", f"模型调用失败：{error[:200]}", account_id=msg.account_id)
            await self._bus.publish(BusEvent("turn.failed", {"turn_id": turn_id, "error": error[:200]}))
            return

        window = channel.reply_window(msg.scene)
        limit = min(int(max_chunks or window.max_replies), window.max_replies)
        chunks = [OutboxChunk(c) for c in split_reply(result.text or EMPTY_REPLY_TEXT, limit)]
        # 拒答时没有正常内容，这一轮不进提供商历史（空的 assistant 消息会让下一次请求报错）。
        provider_messages = [] if result.refused else [*prefix, stored_user, result.raw_assistant]
        await db.conversations.save_turn_result(
            inbox_id=inbox.id,
            account_id=msg.account_id,
            conversation_id=ingested.conversation_id,
            history_epoch=ingested.history_epoch,
            turn_id=turn_id,
            provider=profile.provider,
            provider_messages=provider_messages,
            next_seq=ctx.next_seq,
            reply_text=result.text or EMPTY_REPLY_TEXT,
            chunks=chunks,
            reply_to_msg_id=msg.platform_msg_id,
            deadline=inbox.reply_deadline,
            turn_fields={
                "status": "refused" if result.refused else "succeeded",
                "model": result.model,
                "stop_reason": result.stop_reason,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cache_read_tokens": result.usage.cache_read_tokens,
                "cache_write_tokens": result.usage.cache_write_tokens,
                "cost_usd": result.cost_usd,
                "latency_ms": latency_ms,
                "finished_at": now_ms(),
            },
        )
        self._on_outbox()
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
                    provider, [*history, stored_user, result.raw_assistant], result, msg, ingested, profile, limits
                )
            except Exception:
                log.exception("历史压缩失败（会话 %s）", ingested.conversation_id)

    async def _maybe_compact(
        self,
        provider: LLMProvider,
        history: list[Any],
        result: GenerateResult,
        msg: InboundMessage,
        ingested: Ingested,
        profile: Any,
        limits: ContextLimits,
    ) -> None:
        """这一轮之后历史太长了就压缩：较早的部分换成摘要，最近几轮原文保留，开始新一段。"""
        usage = result.usage
        # 下一次请求会带上这次的输入和这次的回复。
        next_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens + usage.output_tokens
        starts = turn_starts(history)
        too_long = limits.compact_at_tokens > 0 and next_tokens >= limits.compact_at_tokens
        too_many = limits.max_turns > 0 and len(starts) >= limits.max_turns
        keep = max(limits.keep_recent_turns, 0)
        if not (too_long or too_many) or len(starts) <= keep:
            return
        cut = starts[-keep] if keep else len(history)
        older, recent = history[:cut], history[cut:]

        compacted = await provider.compact(older) if provider.capabilities().compaction else None
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
            provider_messages=new_history,
            summary=summary,
            turn_id=ingested.turn_id,
        )
        if compacted is not None:
            await db.conversations.add_turn_cost(ingested.turn_id, compacted.cost_usd)
        if epoch is None:
            return
        reason = f"预计 {next_tokens} token" if too_long else f"已有 {len(starts)} 轮"
        if compacted is not None:
            text = f"会话历史{reason}，较早的 {len(starts) - keep} 轮已压缩成摘要，保留最近 {keep} 轮原文"
            level = "info"
        else:
            text = f"会话历史{reason}，当前模型没有压缩出摘要，只保留最近 {keep} 轮"
            level = "warning"
        await db.events.add(level, "history", text, account_id=msg.account_id)
        await self._bus.publish(
            BusEvent(
                "history.compacted",
                {"conversation_id": ingested.conversation_id, "epoch": epoch, "summarized": compacted is not None},
            )
        )


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


def turn_starts(history: Sequence[Any]) -> list[int]:
    """每一轮开头那条 user 消息的下标：第一条 user 消息，以及每条紧跟在 assistant 回复后面的 user 消息。

    从这些位置切开，前后两半都是完整的轮次，压缩请求以 assistant 回复结尾，保留的部分以 user 消息开头。
    """
    starts: list[int] = []
    for i, m in enumerate(history):
        role = m.get("role") if isinstance(m, dict) else None
        if role == "user" and (not starts or history[i - 1].get("role") == "assistant"):
            starts.append(i)
    return starts


_COMMANDS = {"清空", "clear", "帮助", "help"}
