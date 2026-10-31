"""回复工具：模型用它交出结构化的最终回复，调用它这一轮就结束。

回复的规则写在工具描述里，不依赖用户自己写的 system prompt。参数开 strict，字段由接口保证。
三种段：文字、图片、图文卡片。图片和卡片的链接必须在这一轮出现过（用户消息、搜索结果、page_images 的结果），
不然 validate() 把原因交回模型让它重来一次；再不合规，回复编排时丢掉那一段。
改了定义，工具指纹会变，已有会话会从中立历史重建一次。
"""

from __future__ import annotations

from typing import Any

from agent_core.core.ports.tool import Tool

DESCRIPTION = (
    "把这一轮的最终回复发给用户。每一轮都必须以调用这个工具结束，回复的全部内容都放在 parts 里，"
    "不要在工具调用之外另写回复正文。parts 按顺序逐条发出，每一项是一条消息：\n"
    "- text：文字。text 填正文，不要写网址（QQ 不允许发送链接），也不要用 Markdown 格式。\n"
    "- image：图片。只在图片明显有助于回答时用（用户要看图，或者在介绍外观、地点、作品）。"
    "url 填图片链接，只能用 page_images 返回的或用户消息里的，不能编造；text 填一句图片说明。\n"
    "- card：图文卡片，用户需要原文出处或想点开看详情时，用它代替网址。url 填网页地址，只能用这一轮搜索结果里的；"
    "text 填卡片标题（50 字以内），description 填一句摘要（可以为 null，系统会用网页自己的摘要）。\n"
    "大多数回答只需要一条 text。"
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "description": "按顺序发出的回复，每一项是一条消息",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["text", "image", "card"]},
                    "text": {"type": "string", "description": "文字正文 / 图片说明 / 卡片标题"},
                    "url": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "图片链接或网页地址；text 段填 null",
                    },
                    "description": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "卡片摘要；其他段填 null",
                    },
                },
                "required": ["type", "text", "url", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["parts"],
    "additionalProperties": False,
}


class ReplyTool(Tool):
    name = "reply"
    description = DESCRIPTION
    input_schema = INPUT_SCHEMA
    strict = True
    terminal = True

    def validate(self, arguments: dict[str, Any], links: frozenset[str]) -> str | None:
        bad = [
            part.get("url")
            for part in arguments.get("parts") or []
            if isinstance(part, dict) and part.get("type") in ("image", "card") and part.get("url") not in links
        ]
        if not bad:
            return None
        return (
            f"这些链接没有在这一轮的用户消息、搜索结果或 page_images 结果里出现过，不能用：{'、'.join(map(str, bad))}。"
            "图片请先用 page_images 读取网页拿到真实的图片链接；卡片的链接要用搜索结果里的网页。"
            "拿不到的话就去掉这些段，重新调用 reply。"
        )
