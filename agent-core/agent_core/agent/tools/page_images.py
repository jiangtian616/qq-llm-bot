"""page_images：读取一个网页，返回里面真实可用的图片链接。

模型想发图时，图片链接只能来自这里（或用户消息）：搜索结果里没有图片链接，模型凭记忆写的链接大多打不开。
只允许读这一轮已经出现过的网页（搜索结果、用户消息），和 Claude 的 web_fetch 一样，防止模型被诱导去访问别的地址。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_core.agent.web import fetch_page
from agent_core.core.network import Proxy
from agent_core.core.ports.tool import Tool, ToolResult
from agent_core.infra.http import FetchError

log = logging.getLogger(__name__)


class PageImagesTool(Tool):
    name = "page_images"
    description = (
        "读取一个网页，返回网页里真实可用的图片链接和说明，以及网页标题。想给用户发图片时，先用它拿到图片链接，"
        "回复里的图片只能用它返回的链接。url 只能是这一轮搜索结果或用户消息里出现过的网页。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "网页地址"}},
        "required": ["url"],
        "additionalProperties": False,
    }
    strict = True

    def __init__(self, proxy: Proxy) -> None:
        self._proxy = proxy

    async def run(self, arguments: dict[str, Any], links: frozenset[str]) -> ToolResult:
        url = str(arguments.get("url") or "")
        if url not in links:
            return ToolResult("这个网址没有在这一轮的搜索结果或用户消息里出现过，不能读取。", is_error=True)
        try:
            page = await fetch_page(url, self._proxy)
        except (FetchError, TimeoutError, OSError) as e:
            log.info("读取网页失败（%s）：%s", url, e)
            return ToolResult(f"读取网页失败：{e or type(e).__name__}", is_error=True)
        if not page.images:
            return ToolResult(json.dumps({"title": page.title, "images": []}, ensure_ascii=False) + "\n这个网页里没有找到图片。")
        images = [{"url": i.url, "alt": i.alt} for i in page.images]
        return ToolResult(
            json.dumps({"title": page.title, "images": images}, ensure_ascii=False),
            links=tuple(i.url for i in page.images),
        )
