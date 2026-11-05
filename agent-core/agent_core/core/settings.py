"""机器人行为配置项和默认值。存在 settings 表里，读取时按会话 → 账号 → 全局的顺序找，都没有就用这里的默认值。"""

from __future__ import annotations

from typing import Any

# 模型判断要不要回复（group.reply_mode = auto）时的默认标准；用户可以按会话 / 账号 / 全局改写。
DEFAULT_JUDGE_PROMPT = (
    "你在帮一个 QQ 群里的机器人判断：群里刚发的这条消息，机器人要不要回复。\n"
    "应该回复：叫了机器人的名字或明显在跟它说话；向它提问、请它帮忙；接着它刚才的回复往下聊；"
    "大家在讨论一个它能给出有用信息的问题，而且没人在等别人回答。\n"
    "不用回复：群友之间的闲聊、寒暄、接话；只有表情、图片、「哈哈」「+1」这类没有实际内容的消息；"
    "明显是对别人说的话；机器人插话会显得多余或打扰的时候。\n"
    "拿不准时不回复。"
)

DEFAULTS: dict[str, Any] = {
    # 群里回复哪些消息。平台推哪些消息由 QQ 客户端的群设置决定（仅 @机器人 / 全部消息），这里决定推过来的消息里回复哪些：
    # mention 只回复 @ 机器人或引用它的消息；all 每条都回复；auto 由模型判断（@ 和引用机器人的消息照样回复，
    # 其余每条先用 group.judge.* 单独问一次模型）。没回复的消息照样存下来，下次回复时作为期间的群聊带给模型
    "group.reply_mode": "mention",
    # 判断要不要回复的标准（auto 模式），写在判断请求的 system 里；输出格式由程序补上，这里只写标准
    "group.judge.prompt": DEFAULT_JUDGE_PROMPT,
    # 判断用哪个模型配置；为空时和回复用同一个。判断每条消息都要请求一次，用便宜的模型就行
    "group.judge.profile_id": None,
    # 上一轮之后没回复的群消息拼进本轮时，最多带最新的多少条、多少字；更早的只留一行「省略了 N 条」。0 表示不限
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
    # 图文回复：给模型回复工具和 page_images，让它按需回复文字、图片、图文卡片（提供商支持工具时才生效）
    "reply.rich_media.enabled": True,
    # 联网搜索：Claude 用自带的服务端搜索（每 1000 次 10 美元），DeepSeek 用 Search 接口（每 1000 次 5 元），
    # 搜索结果另按 token 计费；每轮最多搜几次
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
    # 问答过程里每次请求模型的完整请求（查看完整上下文用）保留天数，比聊天记录占地方；0 表示跟着聊天记录一起清理
    "retention.request_snapshots_days": 7,
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

# group.reply_mode 的取值，含义见 DEFAULTS 里的注释。
GROUP_REPLY_MODES = ("mention", "all", "auto")

# 只能在全局设置的配置项。
GLOBAL_ONLY_PREFIXES = ("network.",)
