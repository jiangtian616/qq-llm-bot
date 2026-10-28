"""access_token：有效期 7200 秒，离过期不足 60 秒时刷新。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import aiohttp

from agent_core.core.ports.secrets import SecretReader
from agent_core.plugins.channel_qq.constants import TOKEN_URL, ErrorCode, secret_key
from agent_core.plugins.channel_qq.errors import QQAuthError

log = logging.getLogger(__name__)

REFRESH_MARGIN_S = 60


class TokenManager:
    def __init__(
        self,
        app_id: str,
        secrets: SecretReader,
        session: aiohttp.ClientSession,
        *,
        token_url: str = TOKEN_URL,
        clock: Callable[[], float] = time.monotonic,
        refresh_margin: float = REFRESH_MARGIN_S,
    ) -> None:
        self._app_id = app_id
        self._secrets = secrets
        self._session = session
        self._token_url = token_url
        self._clock = clock
        self._margin = refresh_margin
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token is None or self._clock() >= self._expires_at - self._margin:
                await self._refresh()
            assert self._token is not None
            return self._token

    async def authorization(self) -> str:
        """OpenAPI 请求头：Authorization: QQBot {token}。"""
        return f"QQBot {await self.get_token()}"

    def invalidate(self) -> None:
        """收到鉴权错误时调用，下次 get_token() 会重新换取。"""
        self._token = None

    async def _refresh(self) -> None:
        client_secret = self._secrets.get_secret(secret_key(self._app_id))
        if not client_secret:
            raise QQAuthError(f"密钥库里没有 AppSecret（{secret_key(self._app_id)}）")
        async with self._session.post(
            self._token_url, json={"appId": self._app_id, "clientSecret": client_secret}
        ) as resp:
            body = await resp.json(content_type=None)
        if not isinstance(body, dict) or "access_token" not in body:
            body = body if isinstance(body, dict) else {}
            code = body.get("code")
            msg = body.get("message", "获取 access_token 失败")
            if code == ErrorCode.IP_NOT_IN_WHITELIST:
                msg += "：出口 IP 不在白名单，请到 QQ 开放平台控制台检查"
            raise QQAuthError(msg, code=code, http_status=resp.status)
        # 文档里 expires_in 有时是字符串，有时是数字。
        expires_in = int(body.get("expires_in", 7200))
        self._token = body["access_token"]
        self._expires_at = self._clock() + expires_in
        log.info("QQ access_token 已刷新，%s 秒后过期", expires_in)
