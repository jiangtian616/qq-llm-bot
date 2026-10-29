"""消息附件：下载图片交给模型，其它附件只在文字里留一个标记。

图片在本机下载后以 base64 发给模型，不直接把平台的链接交给模型服务（链接会过期，模型服务那边也不一定访问得到）。
只有当前这一轮带图片数据；存进历史时换成「[图片1]」这样的文字标记，数据库不会膨胀，后面每轮也不会重复计费。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import aiohttp

from agent_core.core.models import Attachment, ImageData, NeutralMessage

log = logging.getLogger(__name__)

MAX_IMAGES_PER_TURN = 4
# Claude 接口单张图片 base64 后不能超过 5 MB，原图按 3.75 MB 算。
MAX_IMAGE_BYTES = 3_750_000
DOWNLOAD_TIMEOUT_S = 15

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


def note_history(m: NeutralMessage) -> NeutralMessage:
    """历史消息只留附件的文字标记。"""
    if not m.attachments:
        return m
    return NeutralMessage(m.role, with_notes(m.content, [attachment_note(a) for a in m.attachments]), speaker=m.speaker)


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
    """每一行消息加上附件标记后的文字，和传入的消息一一对应。"""
    images: tuple[ImageData, ...]


class MediaFetcher:
    async def fetch(self, url: str) -> ImageData:
        """下载一张图片；超时、太大、格式不支持都抛 ValueError。"""
        if not url.startswith(("https://", "http://")):
            raise ValueError("不是 http 链接")
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as s, s.get(url) as resp:
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

    async def collect(self, lines: Sequence[NeutralMessage], *, vision: bool) -> TurnMedia:
        """给本轮的每行消息加上附件标记，并下载要交给模型的图片（按顺序编号，最多 MAX_IMAGES_PER_TURN 张）。"""
        wanted: list[tuple[int, int, Attachment]] = []  # (行号, 附件序号, 附件)
        if vision:
            for i, m in enumerate(lines):
                for j, a in enumerate(m.attachments):
                    if is_image(a) and a.url and (a.size or 0) <= MAX_IMAGE_BYTES:
                        wanted.append((i, j, a))
            # 图片太多时只取最后几张：离提问最近的图最可能是在问的那张。
            wanted = wanted[-MAX_IMAGES_PER_TURN:]
        results = await asyncio.gather(*(self.fetch(a.url or "") for _, _, a in wanted), return_exceptions=True)

        labels: dict[tuple[int, int], str] = {}
        images: list[ImageData] = []
        for (i, j, a), result in zip(wanted, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("图片下载失败（%s）：%s", a.filename or a.url, result)
                labels[(i, j)] = "[图片（没能读取）]"
            else:
                images.append(result)
                labels[(i, j)] = f"[图片{len(images)}]"
        texts = [
            with_notes(m.content, [labels.get((i, j)) or attachment_note(a) for j, a in enumerate(m.attachments)])
            for i, m in enumerate(lines)
        ]
        return TurnMedia(texts, tuple(images))
