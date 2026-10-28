"""中间件链：白名单、限频、指令、过期检查等（下一阶段实现具体中间件）。"""

from __future__ import annotations

from collections.abc import Sequence

from agent_core.core.models import InboundMessage
from agent_core.core.ports.middleware import Middleware, NextHandler


def build_chain(middlewares: Sequence[Middleware], final: NextHandler) -> NextHandler:
    """把中间件按顺序串起来，最后交给 final。"""
    handler = final
    for mw in reversed(middlewares):
        handler = _bind(mw, handler)
    return handler


def _bind(mw: Middleware, next_handler: NextHandler) -> NextHandler:
    async def call(msg: InboundMessage) -> None:
        await mw.handle(msg, next_handler)

    return call
