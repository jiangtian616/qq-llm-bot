"""回复编排：把模型交来的回复整理成发件箱里要发的几段。

两个来源：
- 模型调用了回复工具：parts 是结构化的回复段（文字、图片、卡片），逐段校验；
- 没调用回复工具（没开图文回复、提供商不支持工具、或者模型直接回了文字）：从文字里认出 Markdown 图片 ![说明](链接)，
  单独发成图片消息，和文字按原文顺序交替。

规则：
- 文字里的网址都去掉：QQ 的文字消息带网址会被拒（40054010）。Markdown 链接 [标题](网址) 只留标题。
- 回复工具交来的图片、卡片，链接必须在这一轮出现过（用户消息、搜索结果、工具结果），不然丢掉这一段。
- 卡片按 QQ 的要求补全：标题 1~50 字、摘要 1~200 字、网址和封面都要是 https。没写的摘要和封面从网页的 og 信息里取，
  拿不到封面就退回成一段文字（只有标题）。
- 段数超过平台的回复次数上限时，文字合在一起放前面，图片和卡片跟在后面，尽量保留，但至少给文字留一段。
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_core.infra.db.repos import OutboxChunk
from agent_core.pipeline.outbox import split_reply

log = logging.getLogger(__name__)

MEDIA_MSG_TYPE = 7
CARD_MSG_TYPE = 8
CARD_TITLE_MAX = 50
CARD_DESCRIPTION_MAX = 200
CARD_URL_MAX = 1024
ENRICH_TIMEOUT_S = 8
# 回复里的 Markdown 图片：![说明](链接 "标题")，只认 http(s) 链接。
_IMAGE = re.compile(r'!\[([^\]]*)\]\((https?://[^\s)]+)(?:\s+"[^"]*")?\)')
_LINK = re.compile(r"\[([^\]]*)\]\(\s*https?://[^\s)]*(?:\s+\"[^\"]*\")?\s*\)")
_URL = re.compile(r"https?://[^\s<>()（）\[\]【】\"'，。！？；]+")

# 一段回复：("text", 文字) / ("image", 链接, 说明) / ("card", 卡片字段, 标题)
Segment = tuple[Any, ...]
PageReader = Callable[[str], Awaitable[Any]]
"""读网页（agent.web.fetch_page），返回带 title、description、cover、images 的对象。"""


@dataclass(frozen=True, slots=True)
class ComposedReply:
    chunks: list[OutboxChunk]
    text: str
    """写进中立历史的文字：换了提供商时从它重建上下文，会话页也显示它。图片和卡片写成「[图片：说明]」「[卡片：标题]」。"""
    notes: list[str] = field(default_factory=list)
    """编排时做了什么决定（丢掉了哪段、卡片怎么补全的、去掉了几个网址……），记进问答过程。"""


def find_urls(text: str) -> list[str]:
    return _URL.findall(text)


def clean_text(text: str) -> str:
    """去掉文字里的网址：Markdown 链接只留标题，单独的网址删掉；删完多出来的空白也收拢。"""
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _URL.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def compose_text(text: str, max_chunks: int, *, fallback: str) -> ComposedReply:
    """没调用回复工具时：文字里的 Markdown 图片发成图片消息，其余是文字。中立历史存模型原文。"""
    pieces = _IMAGE.split(text)  # 文字、说明、链接、文字……
    segments: list[Segment] = []
    for i in range(0, len(pieces), 3):
        segments.append(("text", pieces[i]))
        if i + 2 < len(pieces):
            segments.append(("image", pieces[i + 2], pieces[i + 1]))
    notes = ["没有调用回复工具，按文字回复处理"]
    if images := len(pieces) // 3:
        notes.append(f"从文字里认出 {images} 张 Markdown 图片，单独发成图片消息")
    return ComposedReply(_pack(segments, max_chunks, fallback=fallback, notes=notes), text or fallback, notes)


async def compose_parts(
    parts: Sequence[Any], max_chunks: int, *, fallback: str, links: frozenset[str], read_page: PageReader
) -> ComposedReply:
    """模型调用了回复工具：逐段校验、补全卡片，不认识或不合规的段丢掉。"""
    pending: list[Any] = []  # 和 parts 对应：Segment，或者要补全的卡片（协程）
    notes: list[str] = []
    for n, part in enumerate(parts, 1):
        kind = part.get("type") if isinstance(part, dict) else None
        text = str(part.get("text") or "") if isinstance(part, dict) else ""
        url = part.get("url") if isinstance(part, dict) else None
        if kind == "text":
            pending.append(("text", text))
        elif kind == "image" and isinstance(url, str) and url in links and url.startswith(("https://", "http://")):
            pending.append(("image", url, clean_text(text)))
        elif kind == "card" and isinstance(url, str) and url in links:
            pending.append(_card(url, text, part.get("description"), read_page, notes))
        else:
            log.warning("丢掉不合规的回复段：%r", part)
            notes.append(f"丢掉第 {n} 段：{_drop_reason(kind, url, links)}")
    # 几张卡片同时去读网页。
    segments: list[Segment] = list(await asyncio.gather(*(i if asyncio.iscoroutine(i) else _ready(i) for i in pending)))
    packed = _pack(segments, max_chunks, fallback=fallback, notes=notes)
    return ComposedReply(packed, _history_text(segments) or fallback, notes)


def _drop_reason(kind: Any, url: Any, links: frozenset[str]) -> str:
    if kind not in ("text", "image", "card"):
        return f"不认识的类型 {kind!r}"
    if not isinstance(url, str) or not url:
        return f"{kind} 没有链接"
    if url not in links:
        return f"{kind} 的链接没在这一轮出现过：{url}"
    return f"{kind} 的链接不是 http(s)：{url}"


async def _ready(segment: Segment) -> Segment:
    return segment


async def _card(url: str, title: str, description: Any, read_page: PageReader, notes: list[str]) -> Segment:
    """补全一张卡片；拿不到封面（或网址不是 https）就退回成一段文字，只有标题。"""
    title = clean_text(title)
    description = clean_text(description) if isinstance(description, str) else ""
    cover = None
    if url.startswith("https://") and len(url) <= CARD_URL_MAX:
        try:
            page = await asyncio.wait_for(read_page(url), ENRICH_TIMEOUT_S)
        except Exception as e:
            log.info("补全卡片时读取网页失败（%s）：%s", url, e)
            notes.append(f"卡片 {url}：读取网页失败（{type(e).__name__}: {e}）")
        else:
            filled = [name for name, empty in (("标题", not title), ("摘要", not description)) if empty]
            title = title or clean_text(page.title)
            description = description or clean_text(page.description)
            candidates = [page.cover, *(i.url for i in page.images)]
            cover = next((c for c in candidates if c and c.startswith("https://") and len(c) <= CARD_URL_MAX), None)
            filled += ["封面"] if cover else []
            if filled:
                notes.append(f"卡片 {url}：从网页补上了{'、'.join(filled)}")
    else:
        notes.append(f"卡片 {url}：网址不是 https 或太长")
    title = _truncate(title, CARD_TITLE_MAX)
    if not title or cover is None:
        log.info("卡片缺标题或封面，改发文字：%s", url)
        notes.append(f"卡片 {url}：缺{'标题' if not title else '封面'}，改发文字")
        return ("text", title)
    card = {
        "title": title,
        "description": _truncate(description or title, CARD_DESCRIPTION_MAX),
        "url": url,
        "pic_url": cover,
    }
    return ("card", card, title)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _history_text(segments: Sequence[Segment]) -> str:
    lines: list[str] = []
    for seg in segments:
        if seg[0] == "text":
            if text := clean_text(seg[1]):
                lines.append(text)
        elif seg[0] == "image":
            lines.append(f"[图片：{seg[2]}]" if seg[2] else "[图片]")
        else:
            lines.append(f"[卡片：{seg[2]}]")
    return "\n".join(lines)


def _pack(segments: Sequence[Segment], max_chunks: int, *, fallback: str, notes: list[str]) -> list[OutboxChunk]:
    ordered: list[OutboxChunk] = []
    texts: list[str] = []
    media: list[OutboxChunk] = []
    urls = sum(len(_LINK.findall(seg[1])) + len(find_urls(_LINK.sub("", seg[1]))) for seg in segments if seg[0] == "text")
    if urls:
        notes.append(f"文字里去掉了 {urls} 个网址（QQ 拒收带网址的文字消息）")
    for seg in segments:
        if seg[0] == "text":
            if text := clean_text(seg[1]):
                texts.append(text)
                ordered.extend(OutboxChunk(c) for c in split_reply(text, sys.maxsize))
            continue
        chunk = OutboxChunk("", MEDIA_MSG_TYPE, image_url=seg[1]) if seg[0] == "image" else OutboxChunk(
            "", CARD_MSG_TYPE, card=seg[1]
        )
        media.append(chunk)
        ordered.append(chunk)
    if not ordered:
        notes.append("没有能发的内容，回复兜底文字")
        return [OutboxChunk(fallback)]
    if len(ordered) <= max_chunks:
        return ordered
    body = "\n".join(texts)
    n_media = min(len(media), max_chunks - (1 if body else 0))
    notes.append(
        f"共 {len(ordered)} 段，超过回复次数上限 {max_chunks}：文字合并放前面"
        + (f"，丢掉 {len(media) - n_media} 张图片或卡片" if n_media < len(media) else "")
    )
    chunks = [OutboxChunk(c) for c in split_reply(body, max_chunks - n_media)] if body else []
    return chunks + media[:n_media]
