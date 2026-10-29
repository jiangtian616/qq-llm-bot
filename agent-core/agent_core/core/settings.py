"""机器人行为配置项和默认值。存在 settings 表里，读取时按会话 → 账号 → 全局的顺序找，都没有就用这里的默认值。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    # 上一轮之后没触发回复（被限频或预算挡下）的群消息拼进本轮时，最多带最新的多少条、多少字；更早的只留一行「省略了 N 条」。0 表示不限
    "group.context_max_messages": 20,
    "group.context_max_chars": 2000,
    "c2c.enabled": True,
    # 把用户发的图片交给模型看（每张图大约多花 1000~1600 个输入 token）；关掉时模型只看到「[图片]」
    "vision.enabled": True,
    "command.prefix": "/",
    # 用哪个模型配置；为空时用第一个
    "provider_profile_id": None,
    # 一条回复最多拆几段；为空时用平台的回复次数上限（群 5、单聊 4）
    "reply.max_chunks": None,
    # 历史压缩：一轮结束后，下一次请求预计超过这么多 token，或当前这一段超过这么多轮，就把较早的部分压成摘要，
    # 最近几轮原文保留，开始新一段（epoch 加 1）。模型不支持压缩时只保留最近几轮。0 表示不按这一项压缩
    "history.compact_at_tokens": 20000,
    "history.max_turns": 20,
    "history.keep_recent_turns": 4,
    # 每个用户每分钟最多触发几次回复；0 表示不限
    "rate_limit.per_user_per_min": 10,
    # 每日费用上限（美元），超过就暂停回复并提醒；为空表示不限。全局和账号各算各的
    "budget.daily_usd": None,
    # 聊天记录、收件箱原始事件的保留天数；0 表示永久
    "retention.messages_days": 30,
    "retention.raw_events_days": 30,
    # 核心服务启动后是否自动连接（账号级）
    "connection.auto_connect": False,
}

SCOPES = ("global", "account", "conversation")
