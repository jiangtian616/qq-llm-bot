"""SQLite 仓储。每个方法是一个事务；收发链路里「一步一个事务」的写入顺序见 pipeline/inbox.py。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, delete, exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import aliased

from agent_core.core.clock import now_ms
from agent_core.core.models import Attachment, InboundMessage, NeutralMessage, RawEvent, Scene
from agent_core.core.settings import DEFAULTS
from agent_core.infra.db.models import (
    ChannelAccount,
    Contact,
    Conversation,
    GatewaySession,
    InboxEvent,
    Message,
    Outbox,
    ProviderMessage,
    ProviderProfile,
    Setting,
    SystemEvent,
    Turn,
)


class _Repo:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions


# ---------- 账号与连接 ----------


class AccountRepo(_Repo):
    async def list(self) -> list[ChannelAccount]:
        async with self._sessions() as s:
            return list((await s.scalars(select(ChannelAccount).order_by(ChannelAccount.id))).all())

    async def get(self, account_id: int) -> ChannelAccount | None:
        async with self._sessions() as s:
            return await s.get(ChannelAccount, account_id)

    async def create(
        self, *, platform: str, app_id: str, display_name: str, transport: str, intents: int
    ) -> ChannelAccount:
        now = now_ms()
        async with self._sessions.begin() as s:
            account = ChannelAccount(
                platform=platform,
                app_id=app_id,
                display_name=display_name,
                transport=transport,
                intents=intents,
                enabled=1,
                created_at=now,
                updated_at=now,
            )
            s.add(account)
            await s.flush()
            s.add(GatewaySession(account_id=account.id, state="stopped", shard_id=0, shard_count=1, updated_at=now))
        return account

    async def update(self, account_id: int, **fields: Any) -> ChannelAccount | None:
        async with self._sessions.begin() as s:
            account = await s.get(ChannelAccount, account_id)
            if account is None:
                return None
            for k, v in fields.items():
                setattr(account, k, v)
            account.updated_at = now_ms()
        return account

    async def delete(self, account_id: int) -> None:
        async with self._sessions.begin() as s:
            await s.execute(delete(ChannelAccount).where(ChannelAccount.id == account_id))

    async def gateway(self, account_id: int) -> GatewaySession | None:
        async with self._sessions() as s:
            return await s.get(GatewaySession, account_id)

    async def save_gateway(self, account_id: int, **fields: Any) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                update(GatewaySession)
                .where(GatewaySession.account_id == account_id)
                .values(**fields, updated_at=now_ms())
            )


# ---------- 配置 ----------


class SettingsRepo(_Repo):
    async def resolve(self, key: str, *, account_id: int | None = None, conversation_id: int | None = None) -> Any:
        """按会话 → 账号 → 全局的顺序找，都没有就用默认值。"""
        order = [("global", 0)]
        if account_id is not None:
            order.insert(0, ("account", account_id))
        if conversation_id is not None:
            order.insert(0, ("conversation", conversation_id))
        async with self._sessions() as s:
            rows = (
                await s.scalars(
                    select(Setting).where(
                        Setting.key == key,
                        or_(*(and_(Setting.scope == sc, Setting.scope_id == sid) for sc, sid in order)),
                    )
                )
            ).all()
        found = {(r.scope, r.scope_id): r for r in rows}
        for scope in order:
            if scope in found:
                return json.loads(found[scope].value_json)
        return DEFAULTS.get(key)

    async def list(self, scope: str, scope_id: int) -> dict[str, Any]:
        async with self._sessions() as s:
            rows = (await s.scalars(select(Setting).where(Setting.scope == scope, Setting.scope_id == scope_id))).all()
        return {r.key: json.loads(r.value_json) for r in rows}

    async def put(self, scope: str, scope_id: int, key: str, value: Any) -> None:
        now = now_ms()
        stmt = sqlite_insert(Setting).values(
            scope=scope, scope_id=scope_id, key=key, value_json=json.dumps(value, ensure_ascii=False), updated_at=now
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["scope", "scope_id", "key"],
            set_={"value_json": stmt.excluded.value_json, "updated_at": now},
        )
        async with self._sessions.begin() as s:
            await s.execute(stmt)

    async def remove(self, scope: str, scope_id: int, key: str) -> None:
        async with self._sessions.begin() as s:
            await s.execute(
                delete(Setting).where(Setting.scope == scope, Setting.scope_id == scope_id, Setting.key == key)
            )


# ---------- 模型配置 ----------


class ProfileRepo(_Repo):
    async def list(self) -> list[ProviderProfile]:
        async with self._sessions() as s:
            return list((await s.scalars(select(ProviderProfile).order_by(ProviderProfile.id))).all())

    async def get(self, profile_id: int) -> ProviderProfile | None:
        async with self._sessions() as s:
            return await s.get(ProviderProfile, profile_id)

    async def create(
        self, *, name: str, provider: str, model: str, system_prompt: str, params: dict[str, Any]
    ) -> ProviderProfile:
        now = now_ms()
        async with self._sessions.begin() as s:
            profile = ProviderProfile(
                name=name,
                provider=provider,
                model=model,
                system_prompt=system_prompt,
                params_json=json.dumps(params, ensure_ascii=False),
                secret_ref="",
                created_at=now,
                updated_at=now,
            )
            s.add(profile)
            await s.flush()
            # API Key 在密钥库里的键名，按配置区分，换 Key 不影响别的配置。
            profile.secret_ref = f"{provider}:profile:{profile.id}"
        return profile

    async def update(self, profile_id: int, **fields: Any) -> ProviderProfile | None:
        async with self._sessions.begin() as s:
            profile = await s.get(ProviderProfile, profile_id)
            if profile is None:
                return None
            if "params" in fields:
                fields["params_json"] = json.dumps(fields.pop("params"), ensure_ascii=False)
            for k, v in fields.items():
                setattr(profile, k, v)
            profile.updated_at = now_ms()
        return profile

    async def delete(self, profile_id: int) -> None:
        async with self._sessions.begin() as s:
            await s.execute(delete(ProviderProfile).where(ProviderProfile.id == profile_id))


# ---------- 运行事件 ----------


class EventRepo(_Repo):
    async def add(
        self,
        level: str,
        category: str,
        message: str,
        *,
        account_id: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessions.begin() as s:
            s.add(
                SystemEvent(
                    account_id=account_id,
                    ts=now_ms(),
                    level=level,
                    category=category,
                    message=message,
                    data_json=json.dumps(data, ensure_ascii=False) if data else None,
                )
            )

    async def list(self, limit: int = 100, *, level: str | None = None) -> list[SystemEvent]:
        stmt = select(SystemEvent).order_by(SystemEvent.id.desc()).limit(limit)
        if level:
            stmt = stmt.where(SystemEvent.level == level)
        async with self._sessions() as s:
            return list((await s.scalars(stmt)).all())


# ---------- 收件箱 ----------


class InboxRepo(_Repo):
    async def record(
        self, event: RawEvent, *, status: str, reply_deadline: int | None, session_id: str | None
    ) -> bool:
        """写入收件箱并更新 seq，同一事务。event_id 重复返回 False。"""
        now = now_ms()
        async with self._sessions.begin() as s:
            result = await s.execute(
                sqlite_insert(InboxEvent)
                .values(
                    account_id=event.account_id,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    gateway_seq=event.seq,
                    raw_json=json.dumps(event.payload, ensure_ascii=False),
                    received_at=event.received_at_ms,
                    reply_deadline=reply_deadline,
                    status=status,
                    attempts=0,
                )
                .on_conflict_do_nothing(index_elements=["account_id", "event_id"])
            )
            fields: dict[str, Any] = {"last_event_at": now, "updated_at": now}
            if event.seq is not None:
                fields["last_seq"] = event.seq
            if session_id:
                fields["session_id"] = session_id
            await s.execute(update(GatewaySession).where(GatewaySession.account_id == event.account_id).values(**fields))
        return result.rowcount == 1

    async def claim(self, limit: int, lease_ms: int) -> list[InboxEvent]:
        """取待处理的事件并加租约；租约过期的「处理中」（进程崩溃留下的）也会被重新取到。"""
        now = now_ms()
        async with self._sessions.begin() as s:
            rows = list(
                (
                    await s.scalars(
                        select(InboxEvent)
                        .where(
                            InboxEvent.status.in_(("pending", "processing")),
                            or_(InboxEvent.locked_until.is_(None), InboxEvent.locked_until < now),
                        )
                        .order_by(InboxEvent.id)
                        .limit(limit)
                    )
                ).all()
            )
            for row in rows:
                row.status = "processing"
                row.locked_until = now + lease_ms
                row.attempts += 1
        return rows

    async def finish(
        self, inbox_id: int, status: str, *, error: str | None = None, conversation_id: int | None = None
    ) -> None:
        values: dict[str, Any] = {"status": status, "locked_until": None, "processed_at": now_ms(), "last_error": error}
        if conversation_id is not None:
            values["conversation_id"] = conversation_id
        async with self._sessions.begin() as s:
            await s.execute(update(InboxEvent).where(InboxEvent.id == inbox_id).values(**values))

    async def list(self, *, status: str | None, limit: int) -> list[InboxEvent]:
        stmt = select(InboxEvent).order_by(InboxEvent.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(InboxEvent.status == status)
        async with self._sessions() as s:
            return list((await s.scalars(stmt)).all())

    async def retry(self, inbox_id: int) -> str | None:
        """失败的事件在回复窗口内可以重试；返回不能重试的原因，能重试返回 None。"""
        async with self._sessions.begin() as s:
            row = await s.get(InboxEvent, inbox_id)
            if row is None:
                return "事件不存在"
            if row.status != "failed":
                return "只有失败的事件可以重试"
            if row.reply_deadline and now_ms() > row.reply_deadline:
                return "已经超过被动回复时间，平台不再接受回复"
            row.status = "pending"
            row.locked_until = None
            row.last_error = None
        return None

    async def release_leases(self) -> int:
        """启动时调用：只有一个实例在运行，上次留下的租约都是崩溃遗留的，立刻放开。"""
        async with self._sessions.begin() as s:
            result = await s.execute(
                update(InboxEvent).where(InboxEvent.status == "processing").values(status="pending", locked_until=None)
            )
        return result.rowcount

    async def counts(self) -> dict[str, int]:
        async with self._sessions() as s:
            rows = (await s.execute(select(InboxEvent.status, func.count()).group_by(InboxEvent.status))).all()
        return {status: n for status, n in rows}


# ---------- 会话与问答 ----------


@dataclass(frozen=True, slots=True)
class Ingested:
    conversation_id: int
    history_epoch: int
    message_id: int
    sender_label: str
    turn_id: int | None
    turn_status: str | None
    duplicate: bool


@dataclass(frozen=True, slots=True)
class TurnContext:
    provider_history: list[Any]
    """该提供商在当前 epoch 的原始历史（只追加）；为空表示要从中立历史重建。"""
    next_seq: int
    neutral_before: list[NeutralMessage]
    """当前 epoch 里到上一轮为止的中立历史，重建上下文时用。"""
    pending: list[NeutralMessage]
    """上一轮之后没触发回复的群消息，拼进本轮 user 消息里，模型才知道期间聊了什么（比如先发图再 @ 机器人）。"""


@dataclass(frozen=True, slots=True)
class OutboxChunk:
    content: str
    msg_type: int = 0


def speaker_label(scene: str, openid: str, display_name: str | None) -> str:
    """群聊里给 user 消息加的发言人标识；有本地备注就用备注，否则用 openid 末 6 位。单聊不加。"""
    if scene != Scene.GROUP:
        return ""
    return display_name or f"用户{openid[-6:]}"


class ConversationRepo(_Repo):
    async def find(self, account_id: int, scene: str, target_openid: str) -> Conversation | None:
        async with self._sessions() as s:
            return await s.scalar(
                select(Conversation).where(
                    Conversation.account_id == account_id,
                    Conversation.scene == scene,
                    Conversation.target_openid == target_openid,
                )
            )

    async def ingest(
        self,
        msg: InboundMessage,
        *,
        triggered: bool,
        provider: str | None,
        model: str | None,
        profile_id: int | None,
        max_turns: int,
    ) -> Ingested:
        """开始处理：更新或新建会话和发言人，插入入站消息；需要回复时插入问答（排队中）。重复调用是安全的。"""
        now = now_ms()
        async with self._sessions.begin() as s:
            conv = await s.scalar(
                select(Conversation).where(
                    Conversation.account_id == msg.account_id,
                    Conversation.scene == msg.scene,
                    Conversation.target_openid == msg.target_openid,
                )
            )
            if conv is None:
                conv = Conversation(
                    account_id=msg.account_id,
                    scene=msg.scene,
                    target_openid=msg.target_openid,
                    history_epoch=0,
                    status="active",
                    created_at=now,
                )
                s.add(conv)
                await s.flush()
            conv.last_message_at = now

            kind = "member" if msg.scene == Scene.GROUP else "user"
            contact = await s.scalar(
                select(Contact).where(
                    Contact.account_id == msg.account_id, Contact.kind == kind, Contact.openid == msg.sender_openid
                )
            )
            if contact is None:
                contact = Contact(
                    account_id=msg.account_id, kind=kind, openid=msg.sender_openid, first_seen_at=now, last_seen_at=now
                )
                s.add(contact)
                await s.flush()
            contact.last_seen_at = now

            existing = await s.scalar(
                select(Message).where(
                    Message.conversation_id == conv.id,
                    Message.direction == "in",
                    Message.platform_msg_id == msg.platform_msg_id,
                )
            )
            duplicate = existing is not None
            if existing is None:
                if triggered and max_turns > 0:
                    # 当前历史超过上限就自动开始新一段；旧历史保留，但不再带入上下文。
                    turns_in_epoch = await s.scalar(
                        select(func.count())
                        .select_from(Message)
                        .where(
                            Message.conversation_id == conv.id,
                            Message.history_epoch == conv.history_epoch,
                            Message.direction == "out",
                            Message.turn_id.is_not(None),
                        )
                    )
                    if (turns_in_epoch or 0) >= max_turns:
                        conv.history_epoch += 1
                existing = Message(
                    conversation_id=conv.id,
                    history_epoch=conv.history_epoch,
                    direction="in",
                    sender_contact_id=contact.id,
                    platform_msg_id=msg.platform_msg_id,
                    quote_msg_id=msg.quote_msg_id,
                    content=msg.content,
                    attachments_json=json.dumps([_attachment_dict(a) for a in msg.attachments], ensure_ascii=False),
                    is_mention=int(msg.is_mention),
                    triggered=int(triggered),
                    platform_ts=msg.platform_ts,
                    created_at=now,
                )
                s.add(existing)
                await s.flush()

            turn = None
            if triggered and provider and model:
                turn = await s.scalar(select(Turn).where(Turn.inbound_message_id == existing.id))
                if turn is None:
                    turn = Turn(
                        conversation_id=conv.id,
                        inbound_message_id=existing.id,
                        provider_profile_id=profile_id,
                        provider=provider,
                        model=model,
                        status="queued",
                        input_tokens=0,
                        output_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        cost_usd=0.0,
                        attempts=0,
                    )
                    s.add(turn)
                    await s.flush()
            return Ingested(
                conversation_id=conv.id,
                history_epoch=existing.history_epoch,
                message_id=existing.id,
                sender_label=speaker_label(msg.scene, msg.sender_openid, contact.display_name),
                turn_id=turn.id if turn else None,
                turn_status=turn.status if turn else None,
                duplicate=duplicate,
            )

    async def get(self, conversation_id: int) -> Conversation | None:
        async with self._sessions() as s:
            return await s.get(Conversation, conversation_id)

    async def update(self, conversation_id: int, **fields: Any) -> Conversation | None:
        async with self._sessions.begin() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv is None:
                return None
            for k, v in fields.items():
                setattr(conv, k, v)
        return conv

    async def summaries(self, *, account_id: int | None, limit: int) -> list[tuple[Conversation, int, int]]:
        """会话列表：会话、消息数、未 @ 的群消息数（收到过就说明群里开了接收全部消息）。"""
        unmentioned = func.sum(case((and_(Message.direction == "in", Message.is_mention == 0), 1), else_=0))
        stmt = (
            select(Conversation, func.count(Message.id), func.coalesce(unmentioned, 0))
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .group_by(Conversation.id)
            .order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id.desc())
            .limit(limit)
        )
        if account_id is not None:
            stmt = stmt.where(Conversation.account_id == account_id)
        async with self._sessions() as s:
            return [(c, n, u) for c, n, u in (await s.execute(stmt)).all()]

    async def messages(self, conversation_id: int, *, limit: int, before_id: int | None = None) -> list[dict[str, Any]]:
        """会话历史，按时间正序；带发言人和问答信息。"""
        stmt = (
            select(Message, Contact, Turn)
            .outerjoin(Contact, Message.sender_contact_id == Contact.id)
            .outerjoin(Turn, Message.turn_id == Turn.id)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        async with self._sessions() as s:
            conv = await s.get(Conversation, conversation_id)
            rows = (await s.execute(stmt)).all()
        scene = conv.scene if conv else ""
        return [
            {
                "id": m.id,
                "direction": m.direction,
                "history_epoch": m.history_epoch,
                "content": m.content,
                "attachments": json.loads(m.attachments_json or "[]"),
                "is_mention": bool(m.is_mention),
                "triggered": bool(m.triggered),
                "created_at": m.created_at,
                "sender": None
                if contact is None
                else {
                    "contact_id": contact.id,
                    "openid": contact.openid,
                    "label": speaker_label(scene, contact.openid, contact.display_name) or contact.openid,
                    "display_name": contact.display_name,
                },
                "turn": None
                if turn is None
                else {
                    "id": turn.id,
                    "status": turn.status,
                    "model": turn.model,
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                    "cache_read_tokens": turn.cache_read_tokens,
                    "cost_usd": turn.cost_usd,
                    "latency_ms": turn.latency_ms,
                    "stop_reason": turn.stop_reason,
                },
            }
            for m, contact, turn in reversed(rows)
        ]

    async def clear_history(self, conversation_id: int) -> int:
        """清空会话：epoch 加 1，旧历史保留但不再带入上下文。返回新的 epoch。"""
        async with self._sessions.begin() as s:
            conv = await s.get(Conversation, conversation_id)
            assert conv is not None
            conv.history_epoch += 1
            return conv.history_epoch

    async def load_context(
        self, *, conversation_id: int, history_epoch: int, provider: str, current_message_id: int
    ) -> TurnContext:
        async with self._sessions() as s:
            raw_rows = (
                await s.scalars(
                    select(ProviderMessage)
                    .where(
                        ProviderMessage.conversation_id == conversation_id,
                        ProviderMessage.provider == provider,
                        ProviderMessage.history_epoch == history_epoch,
                    )
                    .order_by(ProviderMessage.seq_no)
                )
            ).all()
            conv = await s.get(Conversation, conversation_id)
            assert conv is not None
            sender = aliased(Contact)
            rows = (
                await s.execute(
                    select(Message, sender)
                    .outerjoin(sender, Message.sender_contact_id == sender.id)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.history_epoch == history_epoch,
                        Message.id < current_message_id,
                    )
                    .order_by(Message.id)
                )
            ).all()

        neutral: list[NeutralMessage] = []
        last_answered = -1  # 最后一条已经进入过提供商历史的消息（回复，或触发了回复的提问）
        for m, contact in rows:
            if m.direction == "out" and m.turn_id is None:
                continue  # 系统回复（指令结果、限频和预算提醒）不进上下文
            if m.direction == "out":
                neutral.append(NeutralMessage("assistant", m.content))
                last_answered = len(neutral) - 1
            else:
                label = speaker_label(conv.scene, contact.openid if contact else "", contact.display_name if contact else None)
                attachments = tuple(Attachment(**a) for a in json.loads(m.attachments_json or "[]"))
                neutral.append(NeutralMessage("user", m.content, speaker=label or None, attachments=attachments))
                if m.triggered:
                    last_answered = len(neutral) - 1
        return TurnContext(
            provider_history=[json.loads(r.raw_json) for r in raw_rows],
            next_seq=(raw_rows[-1].seq_no + 1) if raw_rows else 1,
            neutral_before=neutral[: last_answered + 1],
            pending=neutral[last_answered + 1 :],
        )

    async def mark_turn(self, turn_id: int, **fields: Any) -> None:
        async with self._sessions.begin() as s:
            await s.execute(update(Turn).where(Turn.id == turn_id).values(**fields))

    async def save_turn_result(
        self,
        *,
        inbox_id: int,
        account_id: int,
        conversation_id: int,
        history_epoch: int,
        turn_id: int | None,
        provider: str | None,
        provider_messages: Sequence[Any],
        next_seq: int,
        reply_text: str,
        chunks: Sequence[OutboxChunk],
        reply_to_msg_id: str | None,
        deadline: int | None,
        turn_fields: dict[str, Any] | None = None,
    ) -> None:
        """模型返回：原始消息、出站全文、发件箱（每段一行）和问答结果在同一事务里写入，收件箱同时标为已处理。"""
        now = now_ms()
        async with self._sessions.begin() as s:
            for i, raw in enumerate(provider_messages):
                s.add(
                    ProviderMessage(
                        conversation_id=conversation_id,
                        history_epoch=history_epoch,
                        provider=provider,
                        seq_no=next_seq + i,
                        role=raw["role"],
                        raw_json=json.dumps(raw, ensure_ascii=False),
                        turn_id=turn_id,
                        created_at=now,
                    )
                )
            s.add(
                Message(
                    conversation_id=conversation_id,
                    history_epoch=history_epoch,
                    direction="out",
                    content=reply_text,
                    attachments_json="[]",
                    is_mention=0,
                    triggered=0,
                    turn_id=turn_id,
                    created_at=now,
                )
            )
            # 同一 msg_id 下 msg_seq 从 1 递增；系统回复（如指令）也占用序号。
            used = await s.scalar(
                select(func.coalesce(func.max(Outbox.msg_seq), 0)).where(
                    Outbox.account_id == account_id, Outbox.reply_to_msg_id == reply_to_msg_id
                )
            )
            for i, chunk in enumerate(chunks):
                await s.execute(
                    sqlite_insert(Outbox)
                    .values(
                        account_id=account_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        reply_to_msg_id=reply_to_msg_id,
                        msg_seq=(used or 0) + i + 1,
                        chunk_index=i,
                        chunk_count=len(chunks),
                        msg_type=chunk.msg_type,
                        payload_json=json.dumps({"content": chunk.content}, ensure_ascii=False),
                        status="pending",
                        deadline=deadline,
                        attempts=0,
                        next_attempt_at=now,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["account_id", "reply_to_msg_id", "msg_seq"])
                )
            if turn_id is not None and turn_fields:
                await s.execute(update(Turn).where(Turn.id == turn_id).values(**turn_fields))
            await s.execute(
                update(InboxEvent)
                .where(InboxEvent.id == inbox_id)
                .values(status="done", locked_until=None, processed_at=now, conversation_id=conversation_id)
            )


def _attachment_dict(a: Any) -> dict[str, Any]:
    return {k: getattr(a, k) for k in ("content_type", "filename", "url", "width", "height", "size")}


# ---------- 用量 ----------


class UsageRepo(_Repo):
    async def cost_since(self, since_ms: int, *, account_id: int | None = None) -> float:
        stmt = (
            select(func.coalesce(func.sum(Turn.cost_usd), 0.0))
            .join(Conversation, Turn.conversation_id == Conversation.id)
            .where(Turn.started_at >= since_ms)
        )
        if account_id is not None:
            stmt = stmt.where(Conversation.account_id == account_id)
        async with self._sessions() as s:
            return float(await s.scalar(stmt) or 0.0)

    async def summary_since(self, since_ms: int) -> dict[str, Any]:
        async with self._sessions() as s:
            row = (
                await s.execute(
                    select(
                        func.count(Turn.id),
                        func.coalesce(func.sum(case((Turn.status == "succeeded", 1), else_=0)), 0),
                        func.coalesce(func.sum(case((Turn.status == "refused", 1), else_=0)), 0),
                        func.coalesce(func.sum(case((Turn.status == "failed", 1), else_=0)), 0),
                        func.coalesce(func.sum(Turn.input_tokens), 0),
                        func.coalesce(func.sum(Turn.output_tokens), 0),
                        func.coalesce(func.sum(Turn.cache_read_tokens), 0),
                        func.coalesce(func.sum(Turn.cost_usd), 0.0),
                    ).where(Turn.started_at >= since_ms)
                )
            ).one()
        keys = ("turns", "succeeded", "refused", "failed", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd")
        return dict(zip(keys, row, strict=True))


# ---------- 数据清理 ----------


class MaintenanceRepo(_Repo):
    async def purge_messages(self, account_id: int, cutoff_ms: int) -> dict[str, int]:
        """删除这个账号超期的聊天记录（messages、provider_messages、turns、已结束的 outbox），会话本身保留。

        如果删到了某个会话当前正在用的历史，把它的 epoch 加 1，避免带着残缺的历史调模型。
        """
        conv_ids = select(Conversation.id).where(Conversation.account_id == account_id).scalar_subquery()
        old_messages = select(Message.id).where(
            Message.conversation_id.in_(conv_ids), Message.created_at < cutoff_ms
        ).scalar_subquery()
        async with self._sessions.begin() as s:
            affected = (
                await s.scalars(
                    select(Message.conversation_id)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.account_id == account_id,
                        Message.created_at < cutoff_ms,
                        Message.history_epoch == Conversation.history_epoch,
                    )
                    .distinct()
                )
            ).all()
            if affected:
                await s.execute(
                    update(Conversation)
                    .where(Conversation.id.in_(affected))
                    .values(history_epoch=Conversation.history_epoch + 1)
                )
            outbox = await s.execute(
                delete(Outbox).where(
                    Outbox.conversation_id.in_(conv_ids),
                    Outbox.created_at < cutoff_ms,
                    Outbox.status.not_in(("pending", "sending")),
                )
            )
            provider_messages = await s.execute(
                delete(ProviderMessage).where(
                    ProviderMessage.conversation_id.in_(conv_ids), ProviderMessage.created_at < cutoff_ms
                )
            )
            turns = await s.execute(delete(Turn).where(Turn.inbound_message_id.in_(old_messages)))
            messages = await s.execute(
                delete(Message).where(Message.conversation_id.in_(conv_ids), Message.created_at < cutoff_ms)
            )
        return {
            "messages": messages.rowcount,
            "provider_messages": provider_messages.rowcount,
            "turns": turns.rowcount,
            "outbox": outbox.rowcount,
            "epoch_bumped": len(affected),
        }

    async def purge_raw_events(self, account_id: int, cutoff_ms: int) -> int:
        """删除已处理完的超期收件箱事件；还没处理完的不动。"""
        async with self._sessions.begin() as s:
            result = await s.execute(
                delete(InboxEvent).where(
                    InboxEvent.account_id == account_id,
                    InboxEvent.received_at < cutoff_ms,
                    InboxEvent.status.in_(("done", "ignored", "expired", "failed")),
                )
            )
        return result.rowcount


# ---------- 发件箱 ----------


class OutboxRepo(_Repo):
    async def claim(self, limit: int, lease_ms: int) -> list[tuple[Outbox, Conversation]]:
        """每个会话只取排在最前面、还没发完的那一段，保证同一会话按顺序发；不同会话可以并发。"""
        now = now_ms()
        earlier = aliased(Outbox)
        async with self._sessions.begin() as s:
            rows = (
                await s.execute(
                    select(Outbox, Conversation)
                    .join(Conversation, Outbox.conversation_id == Conversation.id)
                    .where(
                        Outbox.status.in_(("pending", "sending")),
                        or_(Outbox.locked_until.is_(None), Outbox.locked_until < now),
                        or_(Outbox.next_attempt_at.is_(None), Outbox.next_attempt_at <= now),
                        ~exists().where(
                            earlier.conversation_id == Outbox.conversation_id,
                            earlier.id < Outbox.id,
                            earlier.status.in_(("pending", "sending")),
                        ),
                    )
                    .order_by(Outbox.id)
                    .limit(limit)
                )
            ).all()
            for row, _ in rows:
                row.status = "sending"
                row.locked_until = now + lease_ms
                row.attempts += 1
        return [(o, c) for o, c in rows]

    async def list(self, *, status: str | None, limit: int) -> list[tuple[Outbox, Conversation]]:
        stmt = (
            select(Outbox, Conversation)
            .join(Conversation, Outbox.conversation_id == Conversation.id)
            .order_by(Outbox.id.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(Outbox.status == status)
        async with self._sessions() as s:
            return [(o, c) for o, c in (await s.execute(stmt)).all()]

    async def retry(self, outbox_id: int) -> str | None:
        """失败的分段在截止时间前可以重发；沿用同一个 msg_seq，平台会去重，不会发两次。"""
        async with self._sessions.begin() as s:
            row = await s.get(Outbox, outbox_id)
            if row is None:
                return "这条回复不存在"
            if row.status != "failed":
                return "只有发送失败的回复可以重试"
            if row.deadline and now_ms() > row.deadline:
                return "已经超过被动回复时间，平台不再接受回复"
            row.status = "pending"
            row.next_attempt_at = now_ms()
            row.locked_until = None
        return None

    async def cancel(self, outbox_id: int) -> str | None:
        async with self._sessions.begin() as s:
            row = await s.get(Outbox, outbox_id)
            if row is None:
                return "这条回复不存在"
            if row.status != "pending":
                return "只有待发送的回复可以取消"
            row.status = "cancelled"
            row.locked_until = None
        return None

    async def release_leases(self) -> int:
        async with self._sessions.begin() as s:
            result = await s.execute(
                update(Outbox).where(Outbox.status == "sending").values(status="pending", locked_until=None)
            )
        return result.rowcount

    async def mark(self, outbox_id: int, **fields: Any) -> None:
        fields.setdefault("locked_until", None)
        async with self._sessions.begin() as s:
            await s.execute(update(Outbox).where(Outbox.id == outbox_id).values(**fields))

    async def counts(self) -> dict[str, int]:
        async with self._sessions() as s:
            rows = (await s.execute(select(Outbox.status, func.count()).group_by(Outbox.status))).all()
        return {status: n for status, n in rows}
