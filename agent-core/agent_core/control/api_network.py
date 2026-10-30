"""控制接口：网络代理的测试。代理本身是 network.* 配置项，读写走 /settings。"""

from __future__ import annotations

import asyncio
import os
import time
import urllib.request
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agent_core.control.auth import require_token
from agent_core.core.network import Proxy, ProxyMode, normalize_proxy_url
from agent_core.infra.http import client_session
from agent_core.plugins.channel_qq.constants import API_BASE as QQ_API_BASE
from agent_core.plugins.provider_deepseek.provider import BASE_URL_ENV as DEEPSEEK_BASE_URL_ENV
from agent_core.plugins.provider_deepseek.provider import DEFAULT_BASE_URL as DEEPSEEK_DEFAULT_BASE_URL

TEST_TIMEOUT_S = 8


class ProxyTest(BaseModel):
    """要测试的代理设置，可以是还没保存的。"""

    mode: str
    url: str = ""


def _targets() -> list[tuple[str, str]]:
    return [
        ("Claude", os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"),
        ("DeepSeek", os.environ.get(DEEPSEEK_BASE_URL_ENV) or DEEPSEEK_DEFAULT_BASE_URL),
        ("QQ", QQ_API_BASE),
    ]


async def _probe(proxy: Proxy, name: str, url: str) -> dict[str, Any]:
    """收到任何 HTTP 响应（包括 401、404）都算连得通，只看网络层能不能到。"""
    started = time.monotonic()
    result: dict[str, Any] = {"target": name, "url": url, "ok": False, "status": None, "error": None}
    try:
        timeout = aiohttp.ClientTimeout(total=TEST_TIMEOUT_S)
        async with client_session(proxy, timeout=timeout) as s, s.get(url, allow_redirects=False) as resp:
            result.update(ok=True, status=resp.status)
    except TimeoutError:
        result["error"] = f"{TEST_TIMEOUT_S} 秒内没有响应"
    except Exception as e:  # noqa: BLE001 连不通的原因原样给用户看
        result["error"] = f"{type(e).__name__}: {e}"
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


def build_network_router() -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    @r.post("/network/test")
    async def test_proxy(body: ProxyTest) -> dict[str, Any]:
        try:
            mode = ProxyMode(body.mode)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不认识的代理方式：{body.mode}") from e
        url = None
        if mode == ProxyMode.CUSTOM:
            try:
                url = normalize_proxy_url(body.url)
            except ValueError as e:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
        proxy = Proxy(mode, url)
        results = await asyncio.gather(*(_probe(proxy, name, target) for name, target in _targets()))
        # 系统代理：给用户看实际读到的是哪个，读不到时说明会直连。
        system = urllib.request.getproxies()
        return {"results": list(results), "system_proxy": system.get("https") or system.get("http")}

    return r
