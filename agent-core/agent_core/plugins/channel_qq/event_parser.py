"""把 QQ 事件转成统一的 InboundMessage。字段以 botpy message.py 的解析为准。

引用消息（message_type 103）：message_scene.ext 里 ref_msg_idx=… 是被引用消息的索引，msg_idx=… 是这条消息自己的；
msg_elements 里 msg_idx 等于 ref_msg_idx 的那一项就是被引用消息的内容和附件。被引用的消息即使没推给机器人（没 @ 它），
内容也在这里。

发送者标识（sender_key）：按实测，引用索引是 REFIDX_ 加上 96 字节的 base64，按 16 字节分块，第 1 块随消息变，
第 2、3 块随发送者变，后 3 块随群变；同一个人（包括机器人自己）在同一个群里，后 80 字节总是一样。
机器人发消息时接口返回的 ref_idx 和别人引用它时看到的不是同一个值，只有这后 80 字节对得上，所以拿它来认出被引用的消息是谁发的。
这不是官方文档写明的格式，解析不了就当认不出来。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from agent_core.core.models import Attachment, InboundMessage, Quote, RawEvent, Scene
from agent_core.plugins.channel_qq.constants import EventType

_REF_PREFIX = "REFIDX_"
_REF_BYTES = 96
# QQ 表情在文字里是 <faceType=6,faceId="0",ext="eyJ0ZXh0IjoiIn0=">，ext 是 base64 的 {"text": "…"}。
_FACE = re.compile(r'<faceType=\d+,faceId="[^"]*",ext="([^"]*)">')


def sender_key(ref_idx: str | None) -> str | None:
    """引用索引里标识发送者的部分（十六进制）；格式不对时返回 None。"""
    if not ref_idx or not ref_idx.startswith(_REF_PREFIX):
        return None
    try:
        raw = base64.b64decode(ref_idx[len(_REF_PREFIX) :], validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw[16:].hex() if len(raw) == _REF_BYTES else None


def clean_content(content: str | None) -> str:
    """去掉前导空格（群里 @ 机器人的消息，@ 部分已被平台去掉，只剩空格），表情换成它的文字或「[表情]」。"""
    return _FACE.sub(_face_text, content or "").strip()


def _face_text(match: re.Match[str]) -> str:
    try:
        text = json.loads(base64.b64decode(match.group(1))).get("text")
    except (binascii.Error, ValueError, AttributeError):
        text = None
    return text if isinstance(text, str) and text else "[表情]"


class EventParser:
    def parse(self, event: RawEvent) -> InboundMessage | None:
        d = event.payload
        author = d.get("author") or {}
        match event.event_type:
            case EventType.GROUP_AT_MESSAGE_CREATE | EventType.GROUP_MESSAGE_CREATE:
                return self._build(
                    event,
                    scene=Scene.GROUP,
                    target=d["group_openid"],
                    sender=author.get("member_openid", ""),
                    is_mention=event.event_type == EventType.GROUP_AT_MESSAGE_CREATE,
                )
            case EventType.C2C_MESSAGE_CREATE:
                openid = author.get("user_openid", "")
                return self._build(event, scene=Scene.C2C, target=openid, sender=openid, is_mention=True)
            case _:
                return None

    @staticmethod
    def _build(event: RawEvent, *, scene: Scene, target: str, sender: str, is_mention: bool) -> InboundMessage:
        d = event.payload
        ref = d.get("message_reference") or {}
        ext = _scene_ext(d)
        return InboundMessage(
            account_id=event.account_id,
            scene=scene,
            target_openid=target,
            sender_openid=sender,
            platform_msg_id=d["id"],
            content=clean_content(d.get("content")),
            is_mention=is_mention,
            platform_ts=d.get("timestamp"),
            quote_msg_id=ref.get("message_id"),
            attachments=tuple(_attachment(a) for a in d.get("attachments") or []),
            event_id=event.event_id,
            quote=_quote(d, ext.get("ref_msg_idx")),
            sender_key=sender_key(ext.get("msg_idx")),
        )


def _scene_ext(d: dict[str, Any]) -> dict[str, str]:
    """message_scene.ext 是「键=值」的字符串列表。"""
    out: dict[str, str] = {}
    for item in (d.get("message_scene") or {}).get("ext") or []:
        if isinstance(item, str) and "=" in item:
            key, _, value = item.partition("=")
            out[key] = value
    return out


def _quote(d: dict[str, Any], ref_idx: str | None) -> Quote | None:
    if not ref_idx:
        return None
    elements = [e for e in d.get("msg_elements") or [] if isinstance(e, dict)]
    element = next((e for e in elements if e.get("msg_idx") == ref_idx), elements[0] if elements else {})
    content = clean_content(element.get("content"))
    attachments = tuple(_attachment(a) for a in element.get("attachments") or [])
    if not content and not attachments:
        # 没有内容时用 parallel_message 里平台给的预览文字（比如「[动画表情]」）。
        nodes = (d.get("parallel_message") or {}).get("msg_nodes") or []
        content = clean_content(next((n.get("content") for n in nodes if isinstance(n, dict) and n.get("content")), ""))
    return Quote(content=content, attachments=attachments, sender_key=sender_key(ref_idx))


def _attachment(a: dict[str, Any]) -> Attachment:
    url = a.get("url")
    if url and url.startswith("//"):
        url = "https:" + url
    return Attachment(
        content_type=a.get("content_type"),
        filename=a.get("filename"),
        url=url,
        width=a.get("width"),
        height=a.get("height"),
        size=a.get("size"),
    )
