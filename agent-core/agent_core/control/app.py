"""本机控制接口（FastAPI）：只监听 127.0.0.1，所有接口都要带令牌。

桌面端只是这个接口的一个客户端；以后换 Web 界面或命令行工具也不用动核心服务。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import time

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, status

from agent_core import API_VERSION, __version__
from agent_core.control.api import build_router
from agent_core.control.api_views import build_views_router
from agent_core.control.auth import require_token, websocket_authorized
from agent_core.control.state import ControlState
from agent_core.core.ports.provider import ProviderSpec


def create_app(state: ControlState, providers: dict[str, ProviderSpec] | None = None) -> FastAPI:
    app = FastAPI(
        title="qq-llm-bot agent-core",
        version=__version__,
        docs_url=None,
        redoc_url=None,
    )
    app.state.control = state
    app.state.providers = providers or {}
    app.include_router(_router())
    app.include_router(build_router())
    app.include_router(build_views_router())
    app.add_api_websocket_route("/events", _events)
    return app


def _ctl(request: Request) -> ControlState:
    return request.app.state.control


def _router() -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    @r.get("/health")
    async def health(request: Request) -> dict:
        s = _ctl(request)
        return {
            "status": "ok",
            "version": __version__,
            "api_version": API_VERSION,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - s.started_at, 1),
            "data_dir": str(s.data_dir),
            "db_revision": s.db_revision,
            "secret_backend": s.secret_backend,
            "plugins": s.plugins,
            "shutting_down": s.lifecycle.shutdown_requested.is_set(),
        }

    @r.get("/version")
    async def version() -> dict:
        return {
            "version": __version__,
            "api_version": API_VERSION,
            "python": platform.python_version(),
            "platform": platform.platform(),
        }

    @r.post("/shutdown", status_code=status.HTTP_202_ACCEPTED)
    async def shutdown(request: Request) -> dict:
        # 平稳停止：先不再接新事件，等在途回复发完、保存 seq 后再退出进程。
        _ctl(request).lifecycle.request_shutdown()
        return {"status": "shutting_down"}

    return r


async def _events(ws: WebSocket) -> None:
    """事件流：推送连接状态、消息、日志等。消息格式 {"topic", "ts", "data"}。"""
    if not websocket_authorized(ws):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    state: ControlState = ws.app.state.control
    await ws.accept()
    await ws.send_json({"topic": "hello", "ts": int(time.time() * 1000), "data": {"api_version": API_VERSION}})

    async def wait_disconnect() -> None:
        # 客户端发来的内容忽略；收到断开就结束。
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await ws.receive_text()

    async def forward() -> None:
        async with state.bus.stream() as events:
            async for event in events:
                await ws.send_json(event.to_json())

    tasks = [asyncio.create_task(wait_disconnect()), asyncio.create_task(forward())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await ws.close()
