"""读取网页：标题、摘要和网页里的图片（page_images 工具和卡片补全用）。

链接是模型给的，用 infra.http.fetch_public 下载（只访问公网）。只看 HTML 本身，不执行 JS：靠 JS 渲染出来的内容拿不到。
图片优先取 og:image / twitter:image（网页作者挑的封面），再取正文里标了尺寸、不太小的 <img>。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp

from agent_core.core.network import Proxy
from agent_core.infra.http import client_session, fetch_public

MAX_PAGE_BYTES = 2_000_000
PAGE_TIMEOUT_S = 10
MAX_IMAGES = 6
# 标了宽或高、又小于这个像素的 <img> 多半是图标、头像，不要。
MIN_IMAGE_SIDE = 120
_SKIP_IMAGE = re.compile(r"\.(svg|ico)(\?|$)|sprite|logo|icon|avatar|blank|spacer|pixel", re.IGNORECASE)
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PageImage:
    url: str
    alt: str = ""


@dataclass(slots=True)
class Page:
    url: str
    title: str = ""
    description: str = ""
    cover: str | None = None
    """og:image / twitter:image。"""
    images: list[PageImage] = field(default_factory=list)


async def fetch_page(url: str, proxy: Proxy) -> Page:
    """下载并解析一个网页；取不到时抛 infra.http.FetchError，超时抛 TimeoutError。"""
    async with client_session(proxy, timeout=aiohttp.ClientTimeout(total=PAGE_TIMEOUT_S)) as session:
        fetched = await fetch_public(session, url, max_bytes=MAX_PAGE_BYTES)
    parser = _PageParser()
    parser.feed(_decode(fetched.data, fetched.charset))
    page = Page(url=fetched.url)
    meta = parser.meta
    page.title = _clean(meta.get("og:title") or parser.title)
    page.description = _clean(meta.get("og:description") or meta.get("description") or "")
    seen: set[str] = set()
    cover_alt = _clean(meta.get("og:image:alt") or page.title)
    for raw in (meta.get("og:image:secure_url"), meta.get("og:image"), meta.get("twitter:image")):
        image = _absolute(fetched.url, raw)
        if image and image not in seen:
            page.cover = page.cover or image
            seen.add(image)
            page.images.append(PageImage(image, cover_alt))
    for raw, alt in parser.images:
        image = _absolute(fetched.url, raw)
        if image and image not in seen and not _SKIP_IMAGE.search(image):
            seen.add(image)
            page.images.append(PageImage(image, _clean(alt)))
        if len(page.images) >= MAX_IMAGES:
            break
    return page


def _decode(data: bytes, charset: str | None) -> str:
    if not charset:
        match = _META_CHARSET.search(data[:4096])
        charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
    if charset.lower() in ("gb2312", "gbk"):
        charset = "gb18030"
    try:
        return data.decode(charset, "replace")
    except LookupError:
        return data.decode("utf-8", "replace")


def _absolute(base: str, raw: str | None) -> str | None:
    if not raw:
        return None
    url = urljoin(base, raw.strip())
    return url if urlsplit(url).scheme in ("http", "https") else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.images: list[tuple[str, str]] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): v or "" for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"])
        elif tag == "img":
            src = a.get("data-src") or a.get("data-original") or a.get("src") or ""
            if not src or src.startswith("data:") or _too_small(a.get("width"), a.get("height")):
                return
            self.images.append((src, a.get("alt") or a.get("title") or ""))
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data


def _too_small(width: str | None, height: str | None) -> bool:
    for side in (width, height):
        digits = re.match(r"\d+", side or "")
        if digits and int(digits.group()) < MIN_IMAGE_SIDE:
            return True
    return False
