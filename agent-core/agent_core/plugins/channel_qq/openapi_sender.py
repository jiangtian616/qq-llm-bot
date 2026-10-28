"""调用 QQ OpenAPI：发群聊 / 单聊消息、取网关地址、查群内机器人状态。"""

from __future__ import annotations

from typing import Any

import aiohttp

from agent_core.plugins.channel_qq.constants import API_BASE
from agent_core.plugins.channel_qq.errors import QQApiError
from agent_core.plugins.channel_qq.token_manager import TokenManager


class OpenApiSender:
    def __init__(self, session: aiohttp.ClientSession, tokens: TokenManager, *, api_base: str = API_BASE) -> None:
        self._session = session
        self._tokens = tokens
        self._base = api_base.rstrip("/")

    async def request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            headers = {"Authorization": await self._tokens.authorization()}
            async with self._session.request(method, self._base + path, json=json, headers=headers) as resp:
                body = await resp.json(content_type=None) if resp.content_length != 0 else {}
                if resp.status == 401 and attempt == 1:
                    # 鉴权错误：立即刷新 token 并重试一次。
                    self._tokens.invalidate()
                    continue
                if not isinstance(body, dict):
                    body = {"data": body}
                if resp.status >= 400 or body.get("code") not in (None, 0):
                    raise QQApiError(
                        body.get("message", f"HTTP {resp.status}"),
                        code=body.get("code") or body.get("err_code"),
                        http_status=resp.status,
                        trace_id=resp.headers.get("X-Tps-trace-ID"),
                    )
                return body
        raise AssertionError("unreachable")

    async def get_gateway_url(self) -> str:
        return (await self.request("GET", "/gateway"))["url"]

    async def send_group_message(self, group_openid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/v2/groups/{group_openid}/messages", payload)

    async def send_c2c_message(self, user_openid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/v2/users/{user_openid}/messages", payload)

    async def get_group_bot_state(self, group_openid: str) -> dict[str, Any]:
        """消息接收设置等只读状态；11253 表示该接口只对白名单机器人开放。"""
        return await self.request("GET", f"/v2/groups/{group_openid}/bot_state")
