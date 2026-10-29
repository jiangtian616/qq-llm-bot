"""统一的数据结构。管线和 Agent 引擎只认这里的类型，不关心消息来自哪个平台、由哪个模型生成。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Scene(enum.StrEnum):
    GROUP = "group"
    C2C = "c2c"


class SendErrorClass(enum.StrEnum):
    """发送失败的分类，决定发件箱怎么处理这一行。"""

    RETRYABLE = "retryable"
    """网络错误、服务端错误、限流：逐次加长间隔重试，不超过截止时间。"""
    ALREADY_SENT = "already_sent"
    """去重错误：说明之前已经发出，直接标为已发送。"""
    FATAL = "fatal"
    """消息过长、被禁言等：直接标为失败。"""
    EXPIRED = "expired"
    """超过回复时间或回复次数：标为已过期。"""


class Direction(enum.StrEnum):
    IN = "in"
    OUT = "out"


@dataclass(frozen=True, slots=True)
class RawEvent:
    """接入方式产出的原始事件，原样交给渠道解析。"""

    account_id: int
    event_id: str
    event_type: str
    seq: int | None
    payload: dict[str, Any]
    received_at_ms: int


@dataclass(frozen=True, slots=True)
class Attachment:
    content_type: str | None = None
    filename: str | None = None
    url: str | None = None
    width: int | None = None
    height: int | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    account_id: int
    scene: Scene
    target_openid: str
    """群为 group_openid，单聊为 user_openid；和 account_id、scene 一起确定一个会话。"""
    sender_openid: str
    platform_msg_id: str
    content: str
    is_mention: bool
    platform_ts: str | None = None
    quote_msg_id: str | None = None
    attachments: tuple[Attachment, ...] = ()
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    account_id: int
    scene: Scene
    target_openid: str
    content: str
    reply_to_msg_id: str | None = None
    reply_to_event_id: str | None = None
    msg_seq: int = 1
    msg_type: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    platform_message_id: str | None
    platform_ts: str | None = None
    ref_idx: str | None = None


@dataclass(frozen=True, slots=True)
class ReplyWindow:
    """被动回复规则：收到消息后多久内可以回复、最多回复几次。"""

    seconds: int
    max_replies: int


@dataclass(frozen=True, slots=True)
class Session:
    """一个群或一个单聊用户就是一个会话。"""

    account_id: int
    scene: Scene
    target_openid: str
    conversation_id: int | None = None
    history_epoch: int = 0


@dataclass(frozen=True, slots=True)
class ImageData:
    """下载好的图片。只随当前这一轮发给模型，存进历史时只留文字标记。"""

    media_type: str  # image/jpeg、image/png、image/gif、image/webp
    data: bytes


@dataclass(frozen=True, slots=True)
class NeutralMessage:
    """中立历史里的一条消息，所有提供商共用。"""

    role: str  # user / assistant
    content: str
    speaker: str | None = None
    attachments: tuple[Attachment, ...] = ()
    images: tuple[ImageData, ...] = ()
    """要让模型看到的图片（提供商声明支持 vision 时才会有），放在文字前面。"""


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class GenerateResult:
    text: str
    stop_reason: str | None
    usage: Usage
    model: str
    refused: bool = False
    raw_assistant: Any = None
    """提供商的原始 assistant 消息（如 Claude 的完整 content 块），原样追加进该提供商的历史。"""
    stop_details: dict[str, Any] | None = None
    cost_usd: float = 0.0
