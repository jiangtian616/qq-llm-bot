"""按代理配置创建 aiohttp 会话。QQ、DeepSeek 和图片下载都用 aiohttp，统一从这里建会话。"""

from __future__ import annotations

import urllib.request
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType

from agent_core.core.network import Proxy, ProxyMode


def client_session(proxy: Proxy, **kwargs: Any) -> aiohttp.ClientSession:
    """kwargs 原样交给 ClientSession（timeout、headers 等）。"""
    if proxy.mode == ProxyMode.SYSTEM:
        # trust_env 读 HTTP_PROXY 等环境变量，以及 Windows 注册表、macOS 网络设置里的 HTTP 代理。
        return aiohttp.ClientSession(trust_env=True, **kwargs)
    if proxy.mode == ProxyMode.CUSTOM and proxy.url:
        if proxy.is_socks:
            parts = urlsplit(proxy.url)
            connector = ProxyConnector(
                proxy_type=ProxyType.SOCKS5,
                host=parts.hostname or "",
                port=parts.port or 1080,
                rdns=parts.scheme == "socks5h",
            )
            return aiohttp.ClientSession(connector=connector, **kwargs)
        # WebSocket 连接也走会话的默认代理。
        return aiohttp.ClientSession(proxy=proxy.url, **kwargs)
    return aiohttp.ClientSession(**kwargs)


def websocket_proxy(proxy: Proxy, url: str) -> str | None:
    """连 WebSocket 时要额外指定的代理；None 表示用会话的默认设置。

    跟随系统代理时，aiohttp 的 trust_env 只认 WS_PROXY / WSS_PROXY，而系统代理一般只设了 http / https，
    所以 ws:// 按 http 代理、wss:// 按 https 代理走。
    """
    if proxy.mode != ProxyMode.SYSTEM:
        return None
    parts = urlsplit(url)
    if parts.hostname and urllib.request.proxy_bypass(parts.hostname):
        return None
    proxies = urllib.request.getproxies()
    found = proxies.get(parts.scheme) or proxies.get("https" if parts.scheme == "wss" else "http")
    return found if found and found.startswith(("http://", "https://")) else None
