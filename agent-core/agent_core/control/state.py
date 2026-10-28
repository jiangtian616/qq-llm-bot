from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_core.core.bus import EventBus
from agent_core.core.lifecycle import ServiceLifecycle


@dataclass(slots=True)
class ControlState:
    """控制接口需要的运行时对象，由 app/main.py 装配后放进 FastAPI 的 app.state。"""

    token: str
    bus: EventBus
    lifecycle: ServiceLifecycle
    data_dir: Path
    secret_backend: str
    plugins: dict[str, str]
    db_revision: str | None = None
    db: Any = None
    secrets: Any = None
    connections: Any = None
    providers: Any = None
    wake_inbox: Any = None
    wake_outbox: Any = None
    maintenance: Any = None
    platform: str = "qq"
    transport: str = "websocket"
    started_at: float = field(default_factory=time.time)
