"""回复编排：把模型交来的回复整理成发件箱里要发的几段。

两个来源：
- 模型调用了回复工具：parts 是结构化的回复段，逐段校验；
- 没调用回复工具（没开图文回复、提供商不支持工具、或者模型直接回了文字）：从文字里认出 Markdown 图片 ![说明](链接)，
  单独发成图片消息，和文字按原文顺序交替。

不管哪个来源，文字里的网址都去掉：QQ 的文字消息带网址会被拒（40054010）。Markdown 链接 [标题](网址) 只留标题。
段数超过平台的回复次数上限时，文字合在一起放前面，图片跟在后面，尽量保留图片，但至少给文字留一段。
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent_core.infra.db.repos import OutboxChunk
from agent_core.pipeline.outbox import split_reply

log = logging.getLogger(__name__)

MEDIA_MSG_TYPE = 7
# 回复里的 Markdown 图片：![说明](链接 "标题")，只认 http(s) 链接。
_IMAGE = re.compile(r'!\[[^\]]*\]\((https?://[^\s)]+)(?:\s+"[^"]*")?\)')
_LINK = re.compile(r"\[([^\]]*)\]\(\s*https?://[^\s)]*(?:\s+\"[^\"]*\")?\s*\)")
_URL = re.compile(r"https?://[^\s<>()（）\[\]【】\"'，。！？；]+")


@dataclass(frozen=True, slots=True)
class ComposedReply:
    chunks: list[OutboxChunk]
    text: str
    """写进中立历史的文字：换了提供商时从它重建上下文，会话页也显示它。"""


def clean_text(text: str) -> str:
    """去掉文字里的网址：Markdown 链接只留标题，单独的网址删掉；删完多出来的空行也收拢。"""
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _URL.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def compose_text(text: str, max_chunks: int, *, fallback: str) -> ComposedReply:
    """没调用回复工具时：文字里的 Markdown 图片发成图片消息，其余是文字。"""
    parts = _IMAGE.split(text)  # 文字、链接、文字……交替
    segments = [("image", part) if i % 2 else ("text", part) for i, part in enumerate(parts)]
    return ComposedReply(_pack(segments, max_chunks, fallback=fallback), text or fallback)


def compose_parts(parts: Sequence[Any], max_chunks: int, *, fallback: str) -> ComposedReply:
    """模型调用了回复工具：逐段校验，不认识或不合规的段丢掉。"""
    segments: list[tuple[str, str]] = []
    for part in parts:
        kind = part.get("type") if isinstance(part, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        if kind == "text" and isinstance(text, str):
            segments.append(("text", text))
        else:
            log.warning("丢掉不合规的回复段：%r", part)
    texts = [clean_text(t) for k, t in segments if k == "text"]
    return ComposedReply(_pack(segments, max_chunks, fallback=fallback), "\n".join(t for t in texts if t) or fallback)


def _pack(segments: Sequence[tuple[str, str]], max_chunks: int, *, fallback: str) -> list[OutboxChunk]:
    """segments 是按原顺序排好的 ("text", 文字) / ("image", 链接)。"""
    ordered: list[OutboxChunk] = []
    for kind, value in segments:
        if kind == "image":
            ordered.append(OutboxChunk("", MEDIA_MSG_TYPE, image_url=value))
        elif text := clean_text(value):
            ordered.extend(OutboxChunk(c) for c in split_reply(text, sys.maxsize))
    if not ordered:
        return [OutboxChunk(fallback)]
    if len(ordered) <= max_chunks:
        return ordered
    texts = [t for t in (clean_text(v) for k, v in segments if k == "text") if t]
    images = [v for k, v in segments if k == "image"]
    body = "\n".join(texts)
    n_images = min(len(images), max_chunks - (1 if body else 0))
    chunks = [OutboxChunk(c) for c in split_reply(body, max_chunks - n_images)] if body else []
    return chunks + [OutboxChunk("", MEDIA_MSG_TYPE, image_url=url) for url in images[:n_images]]
