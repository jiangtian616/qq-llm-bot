"""机器人行为配置项和默认值。存在 settings 表里，读取时按会话 → 账号 → 全局的顺序找，都没有就用这里的默认值。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    # at_only：只回复 @机器人 的消息；all：未 @ 的群消息也触发回复（平台推不推送由群里的消息接收设置决定）
    "group.trigger": "at_only",
    # 没触发回复的群消息是否存为上下文
    "group.keep_untriggered": True,
    "c2c.enabled": True,
    "command.prefix": "/",
    # 用哪个模型配置；为空时用第一个
    "provider_profile_id": None,
    # 一条回复最多拆几段；为空时用平台的回复次数上限（群 5、单聊 4）
    "reply.max_chunks": None,
    # 当前历史超过这么多轮就自动开始新一段（epoch 加 1）
    "history.max_turns": 20,
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
