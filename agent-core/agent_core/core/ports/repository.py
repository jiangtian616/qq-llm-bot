from __future__ import annotations

from typing import Protocol


class Repository(Protocol):
    """仓储的公共约定。按会话、消息、配置等划分出具体仓储，实现在 infra/ 里。"""

    async def ping(self) -> bool: ...
