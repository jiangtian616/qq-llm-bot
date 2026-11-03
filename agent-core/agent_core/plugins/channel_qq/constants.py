from __future__ import annotations

import enum
import os

# 开发调试时可以用环境变量指向本地的模拟服务。
API_BASE = os.environ.get("QQ_LLM_BOT_QQ_API_BASE", "https://api.bot.qq.com").rstrip("/")
TOKEN_URL = f"{API_BASE}/app/getAppAccessToken"


def secret_key(app_id: str) -> str:
    """AppSecret 在密钥库里的键名。"""
    return f"qq:{app_id}"


class Intents(enum.IntFlag):
    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    GROUP_AND_C2C_EVENT = 1 << 25
    """群聊和单聊事件，需要单独申请。"""
    PUBLIC_GUILD_MESSAGES = 1 << 30


DEFAULT_INTENTS = int(Intents.GROUP_AND_C2C_EVENT)


class OpCode(enum.IntEnum):
    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    RESUME = 6
    RECONNECT = 7
    INVALID_SESSION = 9
    HELLO = 10
    HEARTBEAT_ACK = 11
    HTTP_CALLBACK_ACK = 12


class EventType(enum.StrEnum):
    READY = "READY"
    RESUMED = "RESUMED"
    GROUP_AT_MESSAGE_CREATE = "GROUP_AT_MESSAGE_CREATE"
    GROUP_MESSAGE_CREATE = "GROUP_MESSAGE_CREATE"
    C2C_MESSAGE_CREATE = "C2C_MESSAGE_CREATE"


# WebSocket 关闭码：这些情况重连也没用。
FATAL_CLOSE_CODES = {
    4914,  # 机器人已下架
    4915,  # 机器人已封禁
}
# 这些关闭码说明 session 已失效，不能 Resume，要重新 Identify。
RESET_SESSION_CLOSE_CODES = {4006, 4007, *range(4900, 4914)}
INVALID_TOKEN_CLOSE_CODE = 4004


class ErrorCode(enum.IntEnum):
    BOT_STATE_NO_PERMISSION = 11253
    IP_NOT_IN_WHITELIST = 11298
    MUTED = 40054002
    DUPLICATE_MSG_SEQ = 40054005
    MESSAGE_TOO_LONG = 40054007
    REPLY_EXPIRED_OR_LIMIT = 40034128
    UNSUPPORTED_FILE_FORMAT = 850019
    MEDIA_DOWNLOAD_FAILED = 850026
    FILE_TOO_LARGE = 850031
    RECALL_INVALID = 40061001
    RECALL_NO_PERMISSION = 40062003
    RECALL_EXPIRED = 40064004
    RECALL_FAILED = 50065001


# 群消息发出后 2 分钟内可以撤回（v2/groups/{group_openid}/messages/{message_id} DELETE）。
GROUP_RECALL_WINDOW_S = 120


class FileType(enum.IntEnum):
    """富媒体上传接口的 file_type。"""

    IMAGE = 1
    VIDEO = 2
    VOICE = 3
    FILE = 4
