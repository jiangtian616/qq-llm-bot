"""提示词和固定话术：system prompt 保持固定（便于 prompt caching），动态内容只放在 messages 里。"""

from __future__ import annotations

DEFAULT_SYSTEM_PROMPT = (
    "你是一个 QQ 机器人助手。回答简洁、友好，用中文回复。"
    "群聊里每条用户消息前的方括号是发言人标识，用来区分不同的人，回复时不要复述这个标识。"
    "QQ 消息不支持 Markdown，请用纯文本回复，不要使用 Markdown 格式。"
)

CLEARED_TEXT = "已清空对话历史，接下来是新的对话。"
HELP_TEXT = "可用指令：\n/清空 —— 清空本会话的对话历史\n/帮助 —— 显示这条说明"
EMPTY_REPLY_TEXT = "抱歉，我没想好怎么回答，换个说法试试？"
RATE_LIMITED_TEXT = "你发得有点快，请过一分钟再来问我。"
BUDGET_EXCEEDED_TEXT = "今天的使用额度已经用完了，明天再来找我吧。"
