"""数据库入口：一个异步引擎，按领域分出各个仓储。"""

from __future__ import annotations

from pathlib import Path

from agent_core.infra.db.engine import create_engine_for, session_factory
from agent_core.infra.db.repos import (
    AccountRepo,
    ConversationRepo,
    EventRepo,
    InboxRepo,
    MaintenanceRepo,
    OutboxRepo,
    ProfileRepo,
    SettingsRepo,
    TraceRepo,
    UsageRepo,
)


class Database:
    def __init__(self, path: Path) -> None:
        self.engine = create_engine_for(path)
        sessions = session_factory(self.engine)
        self.accounts = AccountRepo(sessions)
        self.settings = SettingsRepo(sessions)
        self.profiles = ProfileRepo(sessions)
        self.events = EventRepo(sessions)
        self.inbox = InboxRepo(sessions)
        self.conversations = ConversationRepo(sessions)
        self.outbox = OutboxRepo(sessions)
        self.traces = TraceRepo(sessions)
        self.usage = UsageRepo(sessions)
        self.maintenance = MaintenanceRepo(sessions)

    async def close(self) -> None:
        await self.engine.dispose()
