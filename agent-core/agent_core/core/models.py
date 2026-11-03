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
class Quote:
    """消息引用的那条消息。平台推送时带着它的内容和附件；是谁发的由引擎按 sender_key 在本会话里查。"""

    content: str = ""
    attachments: tuple[Attachment, ...] = ()
    sender_key: str | None = None
    """被引用消息的发送者标识（见 InboundMessage.sender_key），用来认出是谁发的，包括机器人自己。"""
    by_bot: bool = False
    speaker: str | None = None
    """被引用消息的发言人标识；by_bot 为真或者认不出来时为空。"""


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
    quote: Quote | None = None
    sender_key: str | None = None
    """平台给这条消息的引用索引里标识发送者的那部分；同一个人在同一个群里不变，用来认出被引用的消息是谁发的。"""


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
    image_url: str | None = None
    """要发的图片链接（msg_type=7）；渠道负责上传到平台再发出，content 可以为空。"""
    card: dict[str, Any] | None = None
    """图文卡片（msg_type=8）：title、description、url、pic_url。"""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    platform_message_id: str | None
    platform_ts: str | None = None
    ref_idx: str | None = None
    sender_key: str | None = None
    """ref_idx 里标识发送者（机器人自己）的部分，见 InboundMessage.sender_key。"""
    note: str | None = None
    """发送时的说明（比如卡片被拒、改发了文字），记进发送记录。"""


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
    quote: Quote | None = None
    """引用的消息；组装上下文时写成文字前面的「[引用……]」，它的图片和本条的图片一起交给模型。"""


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
    raw_messages: list[Any] = field(default_factory=list)
    """本轮在 user 消息之后产生的原始消息（assistant 回复、工具结果……），原样追加进该提供商的历史。
    以一轮的结尾收尾：最后一条要么是不带工具调用的 assistant 回复，要么是回复工具的结果。"""
    stop_details: dict[str, Any] | None = None
    cost_usd: float = 0.0
    """本轮所有请求的费用，包括服务端工具（如联网搜索）的费用。"""
    reply_parts: list[dict[str, Any]] | None = None
    """模型调用回复工具时交来的回复段；没调用回复工具时为 None，用 text。"""
    search_requests: int = 0
    tool_rounds: int = 0
    links: frozenset[str] = frozenset()
    """这一轮出现过的链接（用户消息、搜索结果、工具结果）；回复里的图片、卡片只能用这些。"""
    context_tokens: int = 0
    """最后一次请求的输入加输出 token 数，用来估算下一次请求有多大（决定要不要压缩历史）。"""
