"""SQLite 表结构，共 13 张表，分四组：账号与连接、会话与历史、问答与发送、配置与运行。

约定：时间字段存 UTC 毫秒时间戳（INTEGER），平台原始时间另存文本；JSON 存 TEXT；布尔值存 0/1；
密钥不入库，只存系统密钥库里的键名。改了这里要用 Alembic 生成新的迁移。
"""

from __future__ import annotations

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------- 账号与连接 ----------


class ChannelAccount(Base):
    """机器人账号。AppSecret 存在系统密钥库里，键名为 qq:{app_id}。"""

    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("platform", "app_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(Text)
    app_id: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(Text, default="websocket")
    intents: Mapped[int] = mapped_column(Integer, default=1 << 25)
    is_sandbox: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)


class GatewaySession(Base):
    """每个账号一行：连接状态和 Resume 所需的 session_id、seq。"""

    __tablename__ = "gateway_sessions"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str | None] = mapped_column(Text)
    last_seq: Mapped[int | None] = mapped_column(Integer)
    shard_id: Mapped[int] = mapped_column(Integer, default=0)
    shard_count: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(Text, default="stopped")
    last_ready_at: Mapped[int | None] = mapped_column(Integer)
    last_event_at: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[int] = mapped_column(Integer)


class InboxEvent(Base):
    """收件箱：原始事件和处理状态。"""

    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint("account_id", "event_id"),
        Index(None, "status", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    gateway_seq: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[str | None] = mapped_column(Text)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    received_at: Mapped[int] = mapped_column(Integer)
    reply_deadline: Mapped[int | None] = mapped_column(Integer)
    # pending / processing / done / ignored / expired / failed
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[int | None] = mapped_column(Integer)


class AccessRule(Base):
    """白名单和黑名单。"""

    __tablename__ = "access_rules"
    __table_args__ = (UniqueConstraint("account_id", "target_kind", "openid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    target_kind: Mapped[str] = mapped_column(Text)  # group / member / user
    openid: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(Text)  # allow / deny
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)


# ---------- 会话与历史 ----------


class Conversation(Base):
    """一个群或一个单聊用户就是一个会话。平台只给 openid，显示名是本地备注。"""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("account_id", "scene", "target_openid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    scene: Mapped[str] = mapped_column(Text)  # group / c2c
    target_openid: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    history_epoch: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="active")  # active / removed / blocked
    # 群机器人状态接口（GET /v2/groups/{group_openid}/bot_state）的只读结果，为空表示没查到或无权限。
    bot_recv_msg_setting: Mapped[str | None] = mapped_column(Text)
    bot_member_role: Mapped[str | None] = mapped_column(Text)
    bot_allow_proactive_msg: Mapped[int | None] = mapped_column(Integer)
    bot_joined_at: Mapped[str | None] = mapped_column(Text)
    bot_state_checked_at: Mapped[int | None] = mapped_column(Integer)
    bot_state_error: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer)


class Contact(Base):
    """发言人。群里和单聊里的 openid 是否相同尚未确认，所以按 kind 分开存。"""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("account_id", "kind", "openid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)  # member / user
    openid: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[int] = mapped_column(Integer)
    last_seen_at: Mapped[int] = mapped_column(Integer)


class Message(Base):
    """中立历史，所有模型共用。"""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "direction", "platform_msg_id"),
        Index(None, "conversation_id", "history_epoch", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    history_epoch: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(Text)  # in / out
    sender_contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    platform_msg_id: Mapped[str | None] = mapped_column(Text)
    quote_msg_id: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")
    attachments_json: Mapped[str] = mapped_column(Text, default="[]")
    is_mention: Mapped[int] = mapped_column(Integer, default=0)
    triggered: Mapped[int] = mapped_column(Integer, default=0)
    platform_ts: Mapped[str | None] = mapped_column(Text)
    turn_id: Mapped[int | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    created_at: Mapped[int] = mapped_column(Integer)


class ProviderMessage(Base):
    """各提供商的原始消息，只插入不修改（Claude 存完整 content 块，包括 thinking）。"""

    __tablename__ = "provider_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "provider", "history_epoch", "seq_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    history_epoch: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(Text)
    seq_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text)  # user / assistant
    raw_json: Mapped[str] = mapped_column(Text)
    turn_id: Mapped[int] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"))
    created_at: Mapped[int] = mapped_column(Integer)


# ---------- 问答与发送 ----------


class ProviderProfile(Base):
    """模型配置。哪个会话用哪个配置由分层配置里的 provider_profile_id 决定。"""

    __tablename__ = "provider_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    secret_ref: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(Integer)


class Turn(Base):
    """一次问答：状态、用量、费用。"""

    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    inbound_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    provider_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_profiles.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    # queued / running / succeeded / failed / refused / expired
    status: Mapped[str] = mapped_column(Text, default="queued")
    stop_reason: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[int | None] = mapped_column(Integer)
    finished_at: Mapped[int | None] = mapped_column(Integer)


class Outbox(Base):
    """发件箱：待发的回复分段。QQ 用 msg_id + msg_seq 去重，正好作为每一行的唯一键。"""

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("account_id", "reply_to_msg_id", "msg_seq"),
        Index(None, "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("channel_accounts.id", ondelete="CASCADE"))
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    turn_id: Mapped[int | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    reply_to_msg_id: Mapped[str | None] = mapped_column(Text)
    reply_to_event_id: Mapped[str | None] = mapped_column(Text)
    msg_seq: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=1)
    msg_type: Mapped[int] = mapped_column(Integer, default=0)  # 0 文本、2 Markdown、7 富媒体
    payload_json: Mapped[str] = mapped_column(Text)
    # pending / sending / sent / failed / expired / cancelled
    status: Mapped[str] = mapped_column(Text, default="pending")
    deadline: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[int | None] = mapped_column(Integer)
    locked_until: Mapped[int | None] = mapped_column(Integer)
    last_error_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    platform_message_id: Mapped[str | None] = mapped_column(Text)
    platform_ts: Mapped[str | None] = mapped_column(Text)
    ref_idx: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer)


# ---------- 配置与运行 ----------


class Setting(Base):
    """分层配置。读取时按会话 → 账号 → 全局的顺序找。"""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("scope", "scope_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(Text)  # global / account / conversation
    scope_id: Mapped[int] = mapped_column(Integer, default=0)
    key: Mapped[str] = mapped_column(Text)
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[int] = mapped_column(Integer)


class SystemEvent(Base):
    """运行事件：界面上要展示的关键事件。详细日志写文件。"""

    __tablename__ = "system_events"
    __table_args__ = (Index(None, "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL")
    )
    ts: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(Text)  # info / warning / error
    category: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str | None] = mapped_column(Text)


EXPECTED_TABLES = frozenset(
    {
        "channel_accounts",
        "gateway_sessions",
        "inbox_events",
        "access_rules",
        "conversations",
        "contacts",
        "messages",
        "provider_messages",
        "provider_profiles",
        "turns",
        "outbox",
        "settings",
        "system_events",
    }
)
