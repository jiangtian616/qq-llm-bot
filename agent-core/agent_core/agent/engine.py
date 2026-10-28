"""Agent 引擎：判断是否触发回复、处理指令、构造上下文、调用提供商，并把结果交给发件箱。

只认统一的消息类型，不关心消息来自哪个平台、由哪个模型生成。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from agent_core.agent.prompt import HELP_TEXT, CLEARED_TEXT, EMPTY_REPLY_TEXT
from agent_core.agent.providers import ProviderPool
from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms
from agent_core.core.models import InboundMessage, NeutralMessage, Scene
from agent_core.core.ports import ChannelAdapter
from agent_core.infra.db.database import Database
from agent_core.infra.db.repos import Ingested, OutboxChunk
from agent_core.pipeline.outbox import split_reply

log = logging.getLogger(__name__)


class AgentEngine:
    def __init__(self, *, db: Database, providers: ProviderPool, bus: EventBus, on_outbox: Callable[[], None]) -> None:
        self._db = db
        self._providers = providers
        self._bus = bus
        self._on_outbox = on_outbox

    async def handle(self, inbox: Any, msg: InboundMessage, channel: ChannelAdapter) -> None:
        """处理收件箱里的一条消息。inbox 是 inbox_events 的一行（已加租约）。"""
        db = self._db
        conv = await db.conversations.find(msg.account_id, msg.scene, msg.target_openid)
        conv_id = conv.id if conv else None

        async def setting(key: str) -> Any:
            return await db.settings.resolve(key, account_id=msg.account_id, conversation_id=conv_id)

        if msg.scene == Scene.GROUP:
            triggered = msg.is_mention or (await setting("group.trigger")) == "all"
            keep = triggered or bool(await setting("group.keep_untriggered"))
        else:
            triggered = bool(await setting("c2c.enabled"))
            keep = True
        if not keep:
            await db.inbox.finish(inbox.id, "ignored")
            return

        command = None
        prefix = await setting("command.prefix")
        if triggered and prefix and msg.content.startswith(prefix):
            name = msg.content[len(prefix) :].strip()
            if name in _COMMANDS:
                command = name

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
            max_turns=int(await setting("history.max_turns") or 0),
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
        await self._answer(inbox, msg, channel, ingested, profile, max_chunks)

    async def _profile(self, profile_id: Any) -> Any:
        if profile_id:
            profile = await self._db.profiles.get(int(profile_id))
            if profile is not None:
                return profile
        profiles = await self._db.profiles.list()
        return profiles[0] if profiles else None

    async def _run_command(self, command: str, inbox: Any, msg: InboundMessage, ingested: Ingested) -> None:
        if command in ("清空", "clear"):
            await self._db.conversations.clear_history(ingested.conversation_id)
            text = CLEARED_TEXT
        else:
            text = HELP_TEXT
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
                history: list[Any] = ctx.provider_history
                prefix: list[Any] = []
            else:
                # 这个提供商在当前 epoch 还没有原始历史（新会话、清空过、或刚换了提供商）：从中立历史重建，
                # 并把重建出来的前缀一起存下来，之后同一 epoch 里只追加。
                history = provider.build_context(ctx.neutral_before)
                prefix = list(history)
            lines = [*ctx.pending, NeutralMessage("user", msg.content or "（空消息）", speaker=ingested.sender_label or None)]
            text = "\n".join(f"[{m.speaker}] {m.content}" if m.speaker else m.content for m in lines)
            user_message = provider.build_context([NeutralMessage("user", text)])[0]

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
        provider_messages = [] if result.refused else [*prefix, user_message, result.raw_assistant]
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


_COMMANDS = {"清空", "clear", "帮助", "help"}
