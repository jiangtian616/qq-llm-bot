"""回复工具：模型用它交出结构化的最终回复，调用它这一轮就结束。

回复的规则写在工具描述里，不依赖用户自己写的 system prompt。参数开 strict，字段由接口保证。
现在只开放文字段；图片、卡片段等能拿到可信的链接后再加（改了定义，工具指纹会变，已有会话会从中立历史重建一次）。
"""

from __future__ import annotations

from typing import Any

from agent_core.core.ports.tool import Tool

DESCRIPTION = (
    "把这一轮的最终回复发给用户。每一轮都必须以调用这个工具结束，回复的全部内容都放在 parts 里，"
    "不要在工具调用之外另写回复正文。parts 按顺序逐条发出，每一项是一条文字消息；大多数回答只需要一项，"
    "内容很长或者确实要分几条说时才拆开。文字里不要写网址（QQ 不允许发送链接），也不要用 Markdown 格式。"
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
                    "type": {"type": "string", "enum": ["text"]},
                    "text": {"type": "string", "description": "这条消息的文字"},
                },
                "required": ["type", "text"],
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
