"""发件箱：回复和历史在同一事务里写入；发送器按错误码分类重试，沿用同一个 msg_seq。"""

from __future__ import annotations

import enum
import random

from agent_core.core.models import ReplyWindow


class SendErrorClass(enum.StrEnum):
    RETRYABLE = "retryable"
    """网络错误、服务端错误、限流：逐次加长间隔重试，不超过截止时间。"""
    ALREADY_SENT = "already_sent"
    """去重错误：说明之前已经发出，直接标为已发送。"""
    FATAL = "fatal"
    """消息过长、被禁言等：直接标为失败。"""
    EXPIRED = "expired"
    """超过回复时间或回复次数：标为已过期。"""


_ALREADY_SENT = {40054005}
_FATAL = {40054007, 40054002}
_EXPIRED = {40034128}


def classify_send_error(code: int | None, http_status: int | None = None) -> SendErrorClass:
    if code in _ALREADY_SENT:
        return SendErrorClass.ALREADY_SENT
    if code in _FATAL:
        return SendErrorClass.FATAL
    if code in _EXPIRED:
        return SendErrorClass.EXPIRED
    if http_status is not None and 400 <= http_status < 500 and http_status != 429:
        return SendErrorClass.FATAL
    return SendErrorClass.RETRYABLE


def backoff_delay_s(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """1、2、4 秒……逐次加长并加随机抖动。"""
    delay = min(cap, base * 2 ** max(0, attempt - 1))
    return delay + random.uniform(0, delay / 2)


def split_reply(text: str, window: ReplyWindow, max_chunk_chars: int = 1500) -> list[str]:
    """长回答切成几段，每段带递增的 msg_seq；段数不超过平台的回复次数上限。"""
    chunks = [text[i : i + max_chunk_chars] for i in range(0, len(text), max_chunk_chars)] or [""]
    if len(chunks) > window.max_replies:
        chunks = chunks[: window.max_replies]
        chunks[-1] = chunks[-1][: max_chunk_chars - 1] + "…"
    return chunks


class OutboxWorker:
    """TODO(下一阶段)：同一会话按顺序发、不同会话并发；发送前检查截止时间和剩余回复次数。"""

    async def run(self) -> None:
        raise NotImplementedError
