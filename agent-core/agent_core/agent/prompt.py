"""提示词：system prompt 保持固定（便于 prompt caching），动态内容只放在 messages 里。"""

from __future__ import annotations

DEFAULT_SYSTEM_PROMPT = (
    "你是一个 QQ 机器人助手。回答简洁、友好，用中文回复。"
    "群聊里每条用户消息前的方括号是发言人标识，用来区分不同的人，回复时不要复述这个标识。"
)


def speaker_label(scene: str, sender_openid: str, display_name: str | None = None) -> str:
    """群聊里给 user 消息加的发言人标识；有本地备注就用备注，否则用 openid 末 6 位。"""
    if display_name:
        return display_name
    return f"用户{sender_openid[-6:]}" if scene == "group" else ""
