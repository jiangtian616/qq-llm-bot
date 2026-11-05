"""SQLite 仓储。每个方法是一个事务；收发链路里「一步一个事务」的写入顺序见 pipeline/inbox.py。"""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, delete, exists, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import aliased

from agent_core.core.clock import now_ms
from agent_core.core.models import Attachment, InboundMessage, NeutralMessage, Quote, RawEvent, Scene, person_label
from agent_core.core.network import DIRECT, Proxy, ProxyMode, normalize_proxy_url
from agent_core.core.settings import DEFAULTS
from agent_core.infra.db.models import (
    ChannelAccount,
    Contact,
    Conversation,
    GatewaySession,
    InboxEvent,
    Message,
    Outbox,
    OutboxAttempt,
    ProviderMessage,
    ProviderProfile,
    Setting,
    SystemEvent,
    Turn,
    TurnStep,
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

    async def proxy(self, target: str) -> Proxy:
        """target 是 providers（模型接口）或 qq；这一类没勾选走代理时直连。"""
        values = {**DEFAULTS, **await self.list("global", 0)}
        if not values.get(f"network.proxy_for_{target}"):
            return DIRECT
        mode = ProxyMode(values.get("network.proxy_mode") or ProxyMode.NONE)
        if mode != ProxyMode.CUSTOM:
            return Proxy(mode)
        # 保存时已经校验过；万一库里的值不对（比如手动改过），按直连处理，不让整个回复失败。
        try:
            return Proxy(mode, normalize_proxy_url(values.get("network.proxy_url") or ""))
        except ValueError:
            return DIRECT


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

    async def get(self, inbox_id: int) -> InboxEvent | None:
        async with self._sessions() as s:
            return await s.get(InboxEvent, inbox_id)

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
    missing: list[NeutralMessage]
    """该提供商的原始历史之后、由别的提供商回复过的轮次（中间换过提供商又换回来），要补在原始历史后面。"""
    pending: list[NeutralMessage]
    """上一轮之后没触发回复的群消息（没 @ 机器人、模型判断不用回、被限频或预算挡下），拼进本轮 user 消息里，
    模型才知道期间聊了什么。"""


@dataclass(frozen=True, slots=True)
class OutboxChunk:
    content: str
    msg_type: int = 0
    image_url: str | None = None
    card: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        """发件箱 payload_json。早期的行没有 kind，读的时候按有没有 image_url 区分。"""
        if self.card:
            return {"kind": "card", "content": self.content, "card": self.card}
        if self.image_url:
            return {"kind": "image", "content": self.content, "image_url": self.image_url}
        return {"kind": "text", "content": self.content}


def _compress(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def contact_label(contact: Contact) -> str:
    """桌面端展示用的称呼：本地备注或平台昵称，加上 openid 末 6 位。"""
    return person_label(contact.display_name or contact.nickname, contact.openid)


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
        judging: bool = False,
    ) -> Ingested:
        """开始处理：更新或新建会话和发言人，插入入站消息；需要回复时插入问答（排队中）。重复调用是安全的。

        judging：要先让模型判断回不回复。消息先按没触发保存，问答从「判断中」开始，provider / model 是判断用的模型配置。
        """
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
            if msg.sender_name:
                contact.nickname = msg.sender_name

            existing = await s.scalar(
                select(Message).where(
                    Message.conversation_id == conv.id,
                    Message.direction == "in",
                    Message.platform_msg_id == msg.platform_msg_id,
                )
            )
            duplicate = existing is not None
            if existing is None:
                existing = Message(
                    conversation_id=conv.id,
                    history_epoch=conv.history_epoch,
                    direction="in",
                    sender_contact_id=contact.id,
                    platform_msg_id=msg.platform_msg_id,
                    quote_msg_id=msg.quote_msg_id,
                    content=msg.content,
                    display_content=msg.display_content,
                    attachments_json=json.dumps([_attachment_dict(a) for a in msg.attachments], ensure_ascii=False),
                    is_mention=int(msg.is_mention),
                    triggered=int(triggered),
                    platform_ts=msg.platform_ts,
                    sender_key=msg.sender_key,
                    quote_json=_quote_json(msg.quote),
                    created_at=now,
                )
                s.add(existing)
                await s.flush()

            turn = None
            if (triggered or judging) and provider and model:
                turn = await s.scalar(select(Turn).where(Turn.inbound_message_id == existing.id))
                if turn is None:
                    turn = Turn(
                        conversation_id=conv.id,
                        inbound_message_id=existing.id,
                        provider_profile_id=profile_id,
                        provider=provider,
                        model=model,
                        status="judging" if judging else "queued",
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

    async def quote_sender(self, conversation_id: int, sender_key: str) -> tuple[bool, str | None] | None:
        """按发送者标识认出被引用的消息是谁发的：(是不是机器人自己, 发言人标识)；认不出来返回 None。

        机器人发过的消息在发件箱里记着它的标识；别人的要在这个会话里收到过他的消息（带着标识）才认得出来。
        """
        async with self._sessions() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv is None:
                return None
            if await s.scalar(
                select(exists().where(Outbox.conversation_id == conversation_id, Outbox.sender_key == sender_key))
            ):
                return True, None
            contact = await s.scalar(
                select(Contact)
                .join(Message, Message.sender_contact_id == Contact.id)
                .where(Message.conversation_id == conversation_id, Message.sender_key == sender_key)
                .order_by(Message.id.desc())
                .limit(1)
            )
        if contact is None:
            return None
        return False, speaker_label(conv.scene, contact.openid, contact.display_name) or None

    async def start_judged_turn(self, turn_id: int, message_id: int, **turn_fields: Any) -> None:
        """模型判断要回复：消息改成触发了回复，问答换成回复用的模型配置、开始排队。"""
        async with self._sessions.begin() as s:
            await s.execute(update(Message).where(Message.id == message_id).values(triggered=1))
            await s.execute(update(Turn).where(Turn.id == turn_id).values(status="queued", **turn_fields))

    async def recent(
        self, *, conversation_id: int, history_epoch: int, before_id: int, limit: int
    ) -> list[NeutralMessage]:
        """当前这一段里最近的几条消息（不含系统回复和摘要），按时间正序；判断要不要回复时给模型看。"""
        sender = aliased(Contact)
        async with self._sessions() as s:
            conv = await s.get(Conversation, conversation_id)
            assert conv is not None
            rows = (
                await s.execute(
                    select(Message, sender)
                    .outerjoin(sender, Message.sender_contact_id == sender.id)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.history_epoch == history_epoch,
                        Message.id < before_id,
                        or_(Message.direction == "in", and_(Message.direction == "out", Message.turn_id.is_not(None))),
                    )
                    .order_by(Message.id.desc())
                    .limit(limit)
                )
            ).all()
        return [_neutral(m, contact, conv.scene) for m, contact in reversed(rows)]

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
        """会话历史，按时间正序；带发言人和问答信息。模型判断不回复的入站消息带上那次判断（skipped_turn_id）。"""
        skipped = aliased(Turn)
        stmt = (
            select(Message, Contact, Turn, skipped.id)
            .outerjoin(Contact, Message.sender_contact_id == Contact.id)
            .outerjoin(Turn, Message.turn_id == Turn.id)
            .outerjoin(skipped, and_(skipped.inbound_message_id == Message.id, skipped.status == "skipped"))
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).all()
        return [
            {
                "id": m.id,
                "direction": m.direction,
                "history_epoch": m.history_epoch,
                "content": m.display_content or m.content,
                "attachments": json.loads(m.attachments_json or "[]"),
                "is_mention": bool(m.is_mention),
                "triggered": bool(m.triggered),
                "quote": json.loads(m.quote_json) if m.quote_json else None,
                "skipped_turn_id": skipped_id,
                "created_at": m.created_at,
                "sender": None
                if contact is None
                else {
                    "contact_id": contact.id,
                    "openid": contact.openid,
                    "label": contact_label(contact),
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
                    "search_requests": turn.search_requests,
                    "tool_rounds": turn.tool_rounds,
                    "latency_ms": turn.latency_ms,
                    "stop_reason": turn.stop_reason,
                },
            }
            for m, contact, turn, skipped_id in reversed(rows)
        ]

    async def clear_history(self, conversation_id: int) -> int:
        """清空会话：epoch 加 1，旧历史保留但不再带入上下文。返回新的 epoch。"""
        async with self._sessions.begin() as s:
            conv = await s.get(Conversation, conversation_id)
            assert conv is not None
            conv.history_epoch += 1
            return conv.history_epoch

    async def start_epoch(
        self,
        *,
        conversation_id: int,
        from_epoch: int,
        provider: str,
        provider_messages: Sequence[Any],
        summary: str | None,
        turn_id: int,
        keep_turns: int,
        tools_fingerprint: str = "",
    ) -> int | None:
        """历史压缩：开始新一段，写入新一段的原始历史（摘要块 + 保留的最近几轮）和摘要文字。

        保留的最近 keep_turns 轮在中立历史里也挪到新一段，换了提供商从中立历史重建时才不会丢。
        轮数按回复数（不算拒答，拒答不进原始历史）从后往前数，和原始历史的切分一致。
        期间会话被清空过（epoch 已经变了）就什么也不做，返回 None。
        """
        now = now_ms()
        async with self._sessions.begin() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv is None or conv.history_epoch != from_epoch:
                return None
            conv.history_epoch += 1
            refused = select(Turn.id).where(Turn.conversation_id == conversation_id, Turn.status == "refused")
            replies = (
                await s.scalars(
                    select(Message.id)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.history_epoch == from_epoch,
                        Message.direction == "out",
                        Message.turn_id.is_not(None),
                        Message.turn_id.not_in(refused),
                    )
                    .order_by(Message.id.desc())
                    .limit(keep_turns + 1)
                )
            ).all()
            # 第 keep_turns + 1 条回复（从后往前）及以前的留在旧一段，之后的都挪过去。
            # 旧一段的摘要是那一段的开头，已经压进这次的摘要里了，不挪。
            boundary = replies[keep_turns] if len(replies) > keep_turns else 0
            await s.execute(
                update(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.history_epoch == from_epoch,
                    Message.id > boundary,
                    Message.direction != "summary",
                )
                .values(history_epoch=conv.history_epoch)
            )
            for i, raw in enumerate(provider_messages):
                s.add(
                    ProviderMessage(
                        conversation_id=conversation_id,
                        history_epoch=conv.history_epoch,
                        provider=provider,
                        seq_no=i + 1,
                        role=raw["role"],
                        raw_json=json.dumps(raw, ensure_ascii=False),
                        turn_id=turn_id,
                        tools_fingerprint=tools_fingerprint,
                        created_at=now,
                    )
                )
            if summary:
                # 中立历史里也留一份摘要：会话页能看到，换了提供商时从它重建上下文。
                # 它的 id 比挪过来的最近几轮大，读的时候按「摘要在前」排序。
                s.add(
                    Message(
                        conversation_id=conversation_id,
                        history_epoch=conv.history_epoch,
                        direction="summary",
                        content=summary,
                        attachments_json="[]",
                        is_mention=0,
                        triggered=0,
                        created_at=now,
                    )
                )
            return conv.history_epoch

    async def add_turn_cost(self, turn_id: int, cost_usd: float) -> None:
        """历史压缩的费用记在触发它的那一轮上，每日预算照样算进去。"""
        async with self._sessions.begin() as s:
            await s.execute(update(Turn).where(Turn.id == turn_id).values(cost_usd=Turn.cost_usd + cost_usd))

    async def load_context(
        self, *, conversation_id: int, history_epoch: int, provider: str, tools_fingerprint: str, current_message_id: int
    ) -> TurnContext:
        """原始历史只取工具指纹和本轮一致的行：工具清单变过的话，按换了提供商处理（重建，或者补上中间的轮次）。"""
        async with self._sessions() as s:
            all_rows = (
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
            raw_rows = [r for r in all_rows if (r.tools_fingerprint or "") == tools_fingerprint]
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
                    # 摘要是这一段的开头，但写入得比压缩时挪过来的最近几轮晚。
                    .order_by(case((Message.direction == "summary", 0), else_=1), Message.id)
                )
            ).all()

            refused = set(
                (
                    await s.scalars(
                        select(Turn.id).where(Turn.conversation_id == conversation_id, Turn.status == "refused")
                    )
                ).all()
            )

        neutral: list[NeutralMessage] = []
        kinds: list[tuple[str, int | None]] = []  # 和 neutral 一一对应：(direction, turn_id)
        last_answered = -1  # 最后一条已经进入过提供商历史的消息（回复，或触发了回复的提问）
        for m, contact in rows:
            if m.direction == "out" and m.turn_id is None:
                continue  # 系统回复（指令结果、限频和预算提醒）不进上下文
            kinds.append((m.direction, m.turn_id))
            neutral.append(_neutral(m, contact, conv.scene))
            if m.direction in ("summary", "out") or m.triggered:
                last_answered = len(neutral) - 1
        before = neutral[: last_answered + 1]
        missing = (
            _missing_turns(before, kinds, {r.turn_id for r in raw_rows}, refused) if raw_rows else []
        )
        return TurnContext(
            provider_history=[json.loads(r.raw_json) for r in raw_rows],
            # 序号在同一提供商、同一段里唯一（不分指纹），接着所有行往后排。
            next_seq=(all_rows[-1].seq_no + 1) if all_rows else 1,
            neutral_before=before,
            missing=missing,
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
        tools_fingerprint: str = "",
        chunks: Sequence[OutboxChunk],
        reply_to_msg_id: str | None,
        deadline: int | None,
        turn_fields: dict[str, Any] | None = None,
    ) -> None:
        """模型返回：原始消息、出站全文、发件箱（每段一行）和问答结果在同一事务里写入，收件箱同时标为已处理。

        turn_fields 里的 cost_usd 加在已有的费用上：判断要不要回复的费用之前已经记在这一轮上了。
        """
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
                        tools_fingerprint=tools_fingerprint,
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
                        payload_json=json.dumps(chunk.payload(), ensure_ascii=False),
                        status="pending",
                        deadline=deadline,
                        attempts=0,
                        next_attempt_at=now,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["account_id", "reply_to_msg_id", "msg_seq"])
                )
            if turn_id is not None and turn_fields:
                fields = dict(turn_fields)
                if "cost_usd" in fields:
                    fields["cost_usd"] = Turn.cost_usd + fields["cost_usd"]
                await s.execute(update(Turn).where(Turn.id == turn_id).values(**fields))
            await s.execute(
                update(InboxEvent)
                .where(InboxEvent.id == inbox_id)
                .values(status="done", locked_until=None, processed_at=now, conversation_id=conversation_id)
            )


def _attachment_dict(a: Any) -> dict[str, Any]:
    return {k: getattr(a, k) for k in ("content_type", "filename", "url", "width", "height", "size")}


def _quote_json(q: Quote | None) -> str | None:
    if q is None:
        return None
    data = {
        "content": q.content,
        "attachments": [_attachment_dict(a) for a in q.attachments],
        "by_bot": q.by_bot,
        "speaker": q.speaker,
    }
    return json.dumps(data, ensure_ascii=False)


def _quote(text: str | None) -> Quote | None:
    if not text:
        return None
    d = json.loads(text)
    return Quote(
        content=d.get("content") or "",
        attachments=tuple(Attachment(**a) for a in d.get("attachments") or []),
        by_bot=bool(d.get("by_bot")),
        speaker=d.get("speaker"),
    )


def _neutral(m: Message, contact: Contact | None, scene: str) -> NeutralMessage:
    """中立历史的一行；摘要写成开头带「[之前对话的摘要]」的 user 消息。"""
    if m.direction == "summary":
        return NeutralMessage("user", f"[之前对话的摘要]\n{m.content}")
    if m.direction == "out":
        return NeutralMessage("assistant", m.content)
    label = speaker_label(scene, contact.openid if contact else "", contact.display_name if contact else None)
    attachments = tuple(Attachment(**a) for a in json.loads(m.attachments_json or "[]"))
    return NeutralMessage("user", m.content, speaker=label or None, attachments=attachments, quote=_quote(m.quote_json))


def _missing_turns(
    before: Sequence[NeutralMessage],
    kinds: Sequence[tuple[str, int | None]],
    raw_turns: set[int],
    refused: set[int],
) -> list[NeutralMessage]:
    """原始历史最后覆盖到的那条回复之后，别的提供商答过的轮次（提问 + 回复）。

    同一段里摘要总在最前面，有原始历史就说明已经包含了它。拒答的轮次和最后没有回复的提问不补，和原始历史的规则一样。
    """
    start = 0
    for i, (direction, turn_id) in enumerate(kinds[: len(before)]):
        if direction == "summary" or (direction == "out" and turn_id in raw_turns):
            start = i + 1
    missing: list[NeutralMessage] = []
    asked: list[NeutralMessage] = []
    for m, (direction, turn_id) in zip(before[start:], kinds[start : len(before)], strict=True):
        if direction != "out":
            asked.append(m)
            continue
        if turn_id not in refused:
            missing += [*asked, m]
        asked = []
    return missing


# ---------- 处理过程 ----------


class TraceRepo(_Repo):
    """问答过程（turn_steps）和发件箱的发送尝试（outbox_attempts）：只追加，跟着问答和发件箱一起清理。"""

    async def add_steps(self, turn_id: int, steps: Sequence[Any]) -> None:
        """steps 是 core.trace.TraceStep；序号接着这一轮已有的往后排（重试时同一轮会再处理一次）。"""
        if not steps:
            return
        async with self._sessions.begin() as s:
            last = await s.scalar(select(func.coalesce(func.max(TurnStep.seq), 0)).where(TurnStep.turn_id == turn_id))
            for i, step in enumerate(steps, start=(last or 0) + 1):
                s.add(
                    TurnStep(
                        turn_id=turn_id,
                        seq=i,
                        kind=step.kind,
                        title=step.title,
                        detail=step.detail,
                        data_json=None if step.data is None else json.dumps(step.data, ensure_ascii=False),
                        error=step.error,
                        started_at=step.started_at,
                        duration_ms=step.duration_ms,
                        request_z=None if step.request is None else _compress(step.request),
                    )
                )

    async def steps(self, turn_id: int) -> list[TurnStep]:
        async with self._sessions() as s:
            return list(
                (await s.scalars(select(TurnStep).where(TurnStep.turn_id == turn_id).order_by(TurnStep.seq))).all()
            )

    async def steps_with_request(self, turn_id: int) -> set[int]:
        """这一轮里还留着请求快照的步骤 id。"""
        async with self._sessions() as s:
            return set(
                (
                    await s.scalars(
                        select(TurnStep.id).where(TurnStep.turn_id == turn_id, TurnStep.request_z.is_not(None))
                    )
                ).all()
            )

    async def request_snapshots(self, turn_id: int) -> list[tuple[int, Any]]:
        """这一轮所有请求模型步骤的 (步骤 id, 请求快照)，按顺序；快照已清理的为 None。"""
        async with self._sessions() as s:
            rows = (
                await s.execute(
                    select(TurnStep.id, TurnStep.request_z)
                    .where(TurnStep.turn_id == turn_id, TurnStep.kind == "request")
                    .order_by(TurnStep.seq)
                )
            ).all()
        return [(step_id, None if raw is None else json.loads(zlib.decompress(raw))) for step_id, raw in rows]

    async def add_attempt(self, outbox_id: int, **fields: Any) -> None:
        async with self._sessions.begin() as s:
            s.add(OutboxAttempt(outbox_id=outbox_id, **fields))

    async def attempts(self, outbox_id: int) -> list[OutboxAttempt]:
        async with self._sessions() as s:
            return list(
                (
                    await s.scalars(
                        select(OutboxAttempt).where(OutboxAttempt.outbox_id == outbox_id).order_by(OutboxAttempt.id)
                    )
                ).all()
            )

    async def turns(self, *, status: str | None, limit: int) -> list[tuple[Turn, Conversation, Message]]:
        """问答列表，新的在前；带会话和触发它的那条消息。不按状态筛选时不列出判断后没回复的（skipped），免得刷屏。"""
        stmt = (
            select(Turn, Conversation, Message)
            .join(Conversation, Turn.conversation_id == Conversation.id)
            .join(Message, Turn.inbound_message_id == Message.id)
            .order_by(Turn.id.desc())
            .limit(limit)
        )
        stmt = stmt.where(Turn.status == status) if status else stmt.where(Turn.status != "skipped")
        async with self._sessions() as s:
            return [(t, c, m) for t, c, m in (await s.execute(stmt)).all()]

    async def turn(self, turn_id: int) -> tuple[Turn, Conversation, Message, Contact | None] | None:
        stmt = (
            select(Turn, Conversation, Message, Contact)
            .join(Conversation, Turn.conversation_id == Conversation.id)
            .join(Message, Turn.inbound_message_id == Message.id)
            .outerjoin(Contact, Message.sender_contact_id == Contact.id)
            .where(Turn.id == turn_id)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).first()
        return None if row is None else (row[0], row[1], row[2], row[3])

    async def turn_counts(self) -> dict[str, int]:
        async with self._sessions() as s:
            rows = (await s.execute(select(Turn.status, func.count()).group_by(Turn.status))).all()
        return {status: n for status, n in rows}

    async def turn_for_inbox(self, inbox_id: int) -> int | None:
        async with self._sessions() as s:
            return await s.scalar(select(Turn.id).where(Turn.inbox_id == inbox_id).order_by(Turn.id.desc()).limit(1))

    async def outbox_for_turn(self, turn_id: int) -> list[Outbox]:
        async with self._sessions() as s:
            return list((await s.scalars(select(Outbox).where(Outbox.turn_id == turn_id).order_by(Outbox.id))).all())


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
                        # 判断后没回复的不算一轮问答，费用照算
                        func.coalesce(func.sum(case((Turn.status.not_in(("judging", "skipped")), 1), else_=0)), 0),
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

    async def purge_request_snapshots(self, account_id: int, cutoff_ms: int) -> int:
        """清掉这个账号超期的请求快照；问答过程的其它内容跟着聊天记录一起清理。"""
        turn_ids = (
            select(Turn.id)
            .join(Conversation, Turn.conversation_id == Conversation.id)
            .where(Conversation.account_id == account_id)
            .scalar_subquery()
        )
        async with self._sessions.begin() as s:
            result = await s.execute(
                update(TurnStep)
                .where(TurnStep.turn_id.in_(turn_ids), TurnStep.started_at < cutoff_ms, TurnStep.request_z.is_not(None))
                .values(request_z=None)
            )
        return result.rowcount

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

    async def get(self, outbox_id: int) -> tuple[Outbox, Conversation] | None:
        stmt = (
            select(Outbox, Conversation)
            .join(Conversation, Outbox.conversation_id == Conversation.id)
            .where(Outbox.id == outbox_id)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).first()
        return None if row is None else (row[0], row[1])

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

    async def replied_to(self, rows: Sequence[tuple[Outbox, Conversation]]) -> dict[int, str]:
        """每条回复回的是谁（被回复的那条入站消息的发言人）：{发件箱 id: 称呼}；找不到的不在里面。"""
        wanted: dict[tuple[int, str], list[int]] = {}
        for o, _ in rows:
            if o.reply_to_msg_id:
                wanted.setdefault((o.conversation_id, o.reply_to_msg_id), []).append(o.id)
        if not wanted:
            return {}
        async with self._sessions() as s:
            found = (
                await s.execute(
                    select(Message.conversation_id, Message.platform_msg_id, Contact)
                    .join(Contact, Message.sender_contact_id == Contact.id)
                    .where(
                        Message.direction == "in",
                        Message.conversation_id.in_({conv for conv, _ in wanted}),
                        Message.platform_msg_id.in_({msg for _, msg in wanted}),
                    )
                )
            ).all()
        return {
            outbox_id: contact_label(contact)
            for conv, msg, contact in found
            for outbox_id in wanted.get((conv, msg), [])
        }

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
