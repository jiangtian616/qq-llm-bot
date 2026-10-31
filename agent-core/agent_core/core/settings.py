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
    # 图文回复：给模型回复工具，让它用结构化的回复段收尾（提供商支持工具时才生效）；关掉时请求里不带任何本地工具
    "reply.rich_media.enabled": True,
    # 联网搜索：用提供商自带的服务端搜索（Claude 每 1000 次 10 美元，另按搜索结果的 token 计费）；每轮最多搜几次
    "tools.web_search.enabled": False,
    "tools.web_search.max_uses": 3,
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
    # 网络代理，只有全局级。proxy_mode：none 直连、system 跟随系统代理、custom 用 proxy_url（http://、socks5:// 等）。
    # 模型接口和 QQ 分别决定走不走代理；QQ 在国内，一般不用。默认跟随系统代理、只用于模型接口，和 Claude SDK 原来的行为一致
    "network.proxy_mode": "system",
    "network.proxy_url": "",
    "network.proxy_for_providers": True,
    "network.proxy_for_qq": False,
}

SCOPES = ("global", "account", "conversation")

# 只能在全局设置的配置项。
GLOBAL_ONLY_PREFIXES = ("network.",)
