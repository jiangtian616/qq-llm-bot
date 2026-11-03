"""消息附件：下载图片交给模型，其它附件只在文字里留一个标记。

图片在本机下载后以 base64 发给模型，不直接把平台的链接交给模型服务（链接会过期，模型服务那边也不一定访问得到）。
只有当前这一轮带图片数据；存进历史时换成「[图片1]」这样的文字标记，数据库不会膨胀，后面每轮也不会重复计费。

引用的消息写成文字前面的「[引用 某人 的消息：…]」，它的附件标记写在括号里面；引用的图片和本条的图片一起编号、一起交给模型。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import aiohttp

from agent_core.core.models import Attachment, ImageData, NeutralMessage, Quote
from agent_core.core.network import DIRECT, Proxy
from agent_core.infra.http import client_session

log = logging.getLogger(__name__)

MAX_IMAGES_PER_TURN = 4
# Claude 接口单张图片 base64 后不能超过 5 MB，原图按 3.75 MB 算。
MAX_IMAGE_BYTES = 3_750_000
DOWNLOAD_TIMEOUT_S = 15
# 引用的消息只带开头这么多字：完整内容要么在历史里（引用的是机器人或之前的发言），要么只是个由头。
QUOTE_MAX_CHARS = 200

_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def is_image(a: Attachment) -> bool:
    return (a.content_type or "").startswith("image/")


def attachment_note(a: Attachment) -> str:
    """附件在文字里的标记（历史消息、没下载的附件用）。"""
    kind = (a.content_type or "").split("/")[0]
    match kind:
        case "image":
            return "[图片]"
        case "audio" | "voice":
            return "[语音]"
        case "video":
            return "[视频]"
        case _:
            return f"[文件 {a.filename}]" if a.filename else "[文件]"


def with_notes(content: str, notes: Sequence[str]) -> str:
    return " ".join(x for x in (content, *notes) if x)


def quote_text(q: Quote, notes: Sequence[str]) -> str:
    """引用写成「[引用 某人 的消息：内容 [图片1]]」；引用的是机器人自己的回复时写成「[引用你之前的回复：…]」。"""
    content = q.content if len(q.content) <= QUOTE_MAX_CHARS else q.content[:QUOTE_MAX_CHARS] + "…"
    who = "你之前的回复" if q.by_bot else (f" {q.speaker} 的消息" if q.speaker else "的消息")
    return f"[引用{who}：{with_notes(content, notes) or '（空）'}]"


def render(m: NeutralMessage, own_notes: Sequence[str], quote_notes: Sequence[str] = ()) -> str:
    text = with_notes(m.content, own_notes)
    if m.quote is None:
        return text
    return with_notes(quote_text(m.quote, quote_notes), [text])


def note_history(m: NeutralMessage) -> NeutralMessage:
    """历史消息只留附件的文字标记，引用写进文字里。"""
    if not m.attachments and m.quote is None:
        return m
    notes = [attachment_note(a) for a in m.attachments]
    quote_notes = [attachment_note(a) for a in m.quote.attachments] if m.quote else []
    return NeutralMessage(m.role, render(m, notes, quote_notes), speaker=m.speaker)


def sniff_media_type(data: bytes) -> str | None:
    """按文件头判断格式，不信平台给的 content_type；Claude 只支持这四种。"""
    for magic, media_type in _MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True, slots=True)
class TurnMedia:
    texts: list[str]
    """每一行消息加上引用和附件标记后的文字，和传入的消息一一对应。"""
    images: tuple[ImageData, ...]
    failed: tuple[str, ...] = ()
    """没能下载的图片：「文件名或链接：原因」。"""


class MediaFetcher:
    async def fetch(self, url: str, proxy: Proxy = DIRECT) -> ImageData:
        """下载一张图片；超时、太大、格式不支持都抛 ValueError。图片在 QQ 的服务器上，代理跟 QQ 走。"""
        if not url.startswith(("https://", "http://")):
            raise ValueError("不是 http 链接")
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_S)
        async with client_session(proxy, timeout=timeout) as s, s.get(url) as resp:
            resp.raise_for_status()
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("图片太大")
        media_type = sniff_media_type(bytes(data))
        if media_type is None:
            raise ValueError("不支持的图片格式")
        return ImageData(media_type, bytes(data))

    async def collect(self, lines: Sequence[NeutralMessage], *, vision: bool, proxy: Proxy = DIRECT) -> TurnMedia:
        """给本轮的每行消息加上引用和附件标记，并下载要交给模型的图片（按顺序编号，最多 MAX_IMAGES_PER_TURN 张）。"""
        # (行号, 附件序号, 附件)；引用里的附件序号用负数（-1、-2……），排在本条的附件前面。
        wanted: list[tuple[int, int, Attachment]] = []
        if vision:
            for i, m in enumerate(lines):
                for j, a in _numbered(m):
                    if is_image(a) and a.url and (a.size or 0) <= MAX_IMAGE_BYTES:
                        wanted.append((i, j, a))
            # 图片太多时只取最后几张：离提问最近的图最可能是在问的那张。
            wanted = wanted[-MAX_IMAGES_PER_TURN:]
        results = await asyncio.gather(*(self.fetch(a.url or "", proxy) for _, _, a in wanted), return_exceptions=True)

        labels: dict[tuple[int, int], str] = {}
        images: list[ImageData] = []
        failed: list[str] = []
        for (i, j, a), result in zip(wanted, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("图片下载失败（%s）：%s", a.filename or a.url, result)
                labels[(i, j)] = "[图片（没能读取）]"
                failed.append(f"{a.filename or a.url}：{type(result).__name__}: {result}")
            else:
                images.append(result)
                labels[(i, j)] = f"[图片{len(images)}]"
        def notes(i: int, m: NeutralMessage, quoted: bool) -> list[str]:
            return [labels.get((i, j)) or attachment_note(a) for j, a in _numbered(m) if (j < 0) == quoted]

        texts = [render(m, notes(i, m, False), notes(i, m, True)) for i, m in enumerate(lines)]
        return TurnMedia(texts, tuple(images), tuple(failed))


def _numbered(m: NeutralMessage) -> list[tuple[int, Attachment]]:
    quoted = [(-1 - j, a) for j, a in enumerate(m.quote.attachments)] if m.quote else []
    return [*quoted, *enumerate(m.attachments)]
