"""控制接口的令牌鉴权：启动时生成随机令牌写入数据目录，只有本机当前用户能读到。"""

from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request, WebSocket, status

TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _bearer(header: str | None) -> str | None:
    if header and header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return None


def token_matches(expected: str, presented: str | None) -> bool:
    return presented is not None and hmac.compare_digest(expected.encode(), presented.encode())


def require_token(request: Request) -> None:
    """HTTP 路由的依赖：Authorization: Bearer <token>。"""
    if not token_matches(request.app.state.control.token, _bearer(request.headers.get("authorization"))):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token", headers={"WWW-Authenticate": "Bearer"})


def websocket_authorized(ws: WebSocket) -> bool:
    return token_matches(ws.app.state.control.token, _bearer(ws.headers.get("authorization")))
