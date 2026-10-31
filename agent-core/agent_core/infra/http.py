"""按代理配置创建 aiohttp 会话。QQ、DeepSeek 和图片下载都用 aiohttp，统一从这里建会话。

fetch_public() 下载模型给出的链接（网页、图片）：链接可能被群友诱导指向内网，所以每一跳都检查目标地址，只访问公网。
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp_socks import ProxyConnector, ProxyType

from agent_core.core.network import Proxy, ProxyMode

MAX_REDIRECTS = 5
# Clash、Surge 等代理的 TUN 模式会把域名解析到这个网段（fake-ip），实际流量由代理转发到公网。
_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
# 只给开发调试用：设为 1 时允许访问本机和内网地址，验证时让网页、图片指向本地模拟服务。
ALLOW_PRIVATE_ENV = "QQ_LLM_BOT_ALLOW_PRIVATE_FETCH"


class FetchError(Exception):
    """链接取不到或不能用（内网地址、打不开、太大……），重试也没用。"""


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    """跟随跳转之后的最终地址。"""
    data: bytes
    content_type: str
    charset: str | None


async def ensure_public(url: str) -> None:
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise FetchError("不是 http 链接")
    if os.environ.get(ALLOW_PRIVATE_ENV) == "1":
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port)
    except OSError as e:
        raise FetchError(f"域名解析失败：{host}") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global and address not in _FAKE_IP:
            raise FetchError(f"链接指向内网地址：{host}")


async def fetch_public(session: aiohttp.ClientSession, url: str, *, max_bytes: int) -> Fetched:
    """GET 一个公网链接；每一跳跳转都检查目标地址，超过 max_bytes 就放弃。"""
    for _ in range(MAX_REDIRECTS + 1):
        await ensure_public(url)
        async with session.get(url, allow_redirects=False) as resp:
            location = resp.headers.get("Location")
            if resp.status in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                continue
            if resp.status >= 400:
                raise FetchError(f"链接打不开（HTTP {resp.status}）")
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > max_bytes:
                    raise FetchError("内容太大")
            return Fetched(url, bytes(data), resp.content_type or "", resp.charset)
    raise FetchError("跳转次数太多")


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
