"""把 QQ 事件转成统一的 InboundMessage。字段以 botpy message.py 的解析为准。"""

from __future__ import annotations

from typing import Any

from agent_core.core.models import Attachment, InboundMessage, RawEvent, Scene
from agent_core.plugins.channel_qq.constants import EventType


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
        return InboundMessage(
            account_id=event.account_id,
            scene=scene,
            target_openid=target,
            sender_openid=sender,
            platform_msg_id=d["id"],
            # 群里 @ 机器人的消息，@ 部分已被平台去掉，只剩前导空格。
            content=(d.get("content") or "").strip(),
            is_mention=is_mention,
            platform_ts=d.get("timestamp"),
            quote_msg_id=ref.get("message_id"),
            attachments=tuple(_attachment(a) for a in d.get("attachments") or []),
            event_id=event.event_id,
        )


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
