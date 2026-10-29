from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agent_core.core.bus import EventBus
from agent_core.core.lifecycle import ConnectionStateMachine
from agent_core.core.models import RawEvent
from agent_core.core.ports.secrets import SecretReader


@dataclass(frozen=True, slots=True)
class ResumeState:
    """断线后补收事件所需的状态，和收件箱在同一事务里落库。"""

    session_id: str | None = None
    seq: int | None = None


@dataclass(slots=True)
class TransportContext:
    account_id: int
    app_id: str
    secrets: SecretReader
    bus: EventBus
    state: ConnectionStateMachine
    intents: int
    resume: ResumeState = field(default_factory=ResumeState)


class InboundTransport(abc.ABC):
    """接入方式插槽：WebSocket（当前）或 Webhook（预留）。"""

    def __init__(self, ctx: TransportContext) -> None:
        self.ctx = ctx

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """平稳停止：不再接新事件，返回前保证 resume_state() 是最新的。"""

    @abc.abstractmethod
    def events(self) -> AsyncIterator[RawEvent]: ...

    @abc.abstractmethod
    def resume_state(self) -> ResumeState: ...
