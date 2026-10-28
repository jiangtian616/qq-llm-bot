from __future__ import annotations

from typing import Any, Protocol


class Tool(Protocol):
    """Agent 可调用的工具（以后接联网搜索、MCP 等）。"""

    name: str
    description: str
    input_schema: dict[str, Any]

    async def run(self, arguments: dict[str, Any]) -> str: ...
