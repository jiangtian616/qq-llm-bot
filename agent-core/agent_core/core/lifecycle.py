"""连接状态机和平稳停止。"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Awaitable, Callable

from agent_core.core.bus import BusEvent, EventBus

log = logging.getLogger(__name__)


class ConnectionState(enum.StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    ONLINE = "online"
    RECONNECTING = "reconnecting"
    ERROR = "error"


_ALLOWED: dict[ConnectionState, set[ConnectionState]] = {
    ConnectionState.STOPPED: {ConnectionState.CONNECTING},
    ConnectionState.CONNECTING: {ConnectionState.ONLINE, ConnectionState.ERROR, ConnectionState.STOPPED},
    ConnectionState.ONLINE: {ConnectionState.RECONNECTING, ConnectionState.ERROR, ConnectionState.STOPPED},
    ConnectionState.RECONNECTING: {ConnectionState.ONLINE, ConnectionState.ERROR, ConnectionState.STOPPED},
    ConnectionState.ERROR: {ConnectionState.CONNECTING, ConnectionState.STOPPED},
}


class InvalidTransition(Exception):
    pass


class ConnectionStateMachine:
    """一个机器人账号的 QQ 连接状态。每次变化都发到总线上，桌面端通过事件流看到。"""

    def __init__(self, account_id: int, bus: EventBus) -> None:
        self.account_id = account_id
        self._bus = bus
        self._state = ConnectionState.STOPPED
        self.last_error: str | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    def can_transition(self, target: ConnectionState) -> bool:
        return target in _ALLOWED[self._state]

    async def transition(self, target: ConnectionState, error: str | None = None) -> None:
        if target == self._state:
            return
        if not self.can_transition(target):
            raise InvalidTransition(f"{self._state} -> {target}")
        previous, self._state = self._state, target
        if target == ConnectionState.ERROR:
            self.last_error = error
        await self._bus.publish(
            BusEvent(
                "connection.state",
                {"account_id": self.account_id, "from": previous, "to": target, "error": error},
            )
        )


StopHook = Callable[[], Awaitable[None]]


class ServiceLifecycle:
    """核心服务进程的平稳停止：按注册的反序执行停止钩子，每个钩子有超时。"""

    def __init__(self, hook_timeout: float = 10.0) -> None:
        self._hooks: list[tuple[str, StopHook]] = []
        self._hook_timeout = hook_timeout
        self.shutdown_requested = asyncio.Event()
        self._stopped = False

    def on_stop(self, name: str, hook: StopHook) -> None:
        self._hooks.append((name, hook))

    def request_shutdown(self) -> None:
        self.shutdown_requested.set()

    async def run_stop_hooks(self) -> None:
        """只执行一次：/shutdown 和 Ctrl+C 两条路径都会调到这里。"""
        if self._stopped:
            return
        self._stopped = True
        for name, hook in reversed(self._hooks):
            try:
                await asyncio.wait_for(hook(), self._hook_timeout)
            except TimeoutError:
                log.warning("停止 %s 超时，跳过", name)
            except Exception:
                log.exception("停止 %s 出错", name)
