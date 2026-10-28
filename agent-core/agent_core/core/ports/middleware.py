from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable

from agent_core.core.models import InboundMessage

NextHandler = Callable[[InboundMessage], Awaitable[None]]


class Middleware(abc.ABC):
    """处理入站消息的中间件：白名单、限频、指令、过期检查等。不调用 next 就表示拦下这条消息。"""

    @abc.abstractmethod
    async def handle(self, msg: InboundMessage, next: NextHandler) -> None: ...
