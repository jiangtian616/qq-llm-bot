"""工具：模型可以调用的能力。

两类：
- 本地工具（Tool）：我们自己实现，各提供商通用；提供商插件把它翻译成自己的工具格式，模型调用后由工具循环执行 run()。
  terminal 的工具（回复工具）不执行，模型一调用它这一轮就结束，参数交给引擎。
- 服务端工具（ServerTool）：提供商自带、在提供商那边执行的工具（如 Claude 的 web_search），这里只声明要不要开、怎么限制。

同一会话里工具清单变了（开关搜索、改了工具定义），原始历史就和新请求对不上了；fingerprint() 用来发现这种变化，
不一致时从中立历史重建上下文，和换了提供商一样。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False


class Tool:
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    strict: bool = False
    """要求模型的参数严格符合 input_schema（Claude 的 strict tool use）。"""
    terminal: bool = False
    """调用它就结束这一轮，参数交给引擎，不执行 run()。"""

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError(f"{self.name} 不在本地执行")

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": self.strict,
            "terminal": self.terminal,
        }


@dataclass(frozen=True, slots=True)
class ServerTool:
    name: str
    """提供商能力里 server_tools 列出的名字，如 web_search。"""
    max_uses: int = 0
    """一次请求里最多调用几次；0 表示不限。"""


@dataclass(frozen=True, slots=True)
class Toolset:
    """这一轮交给模型的工具，顺序固定（工具定义在请求最前面，顺序一变提示缓存就失效）。"""

    server: tuple[ServerTool, ...] = ()
    local: tuple[Tool, ...] = ()
    max_rounds: int = 4
    """最多执行几轮工具调用（服务端工具暂停后接着请求也算一轮）。"""

    def __bool__(self) -> bool:
        return bool(self.server or self.local)

    def fingerprint(self) -> str:
        """工具清单的指纹；没有工具时是空串，和升级前存下的原始历史一致。

        只看有哪些工具、本地工具怎么定义；max_uses 这类限制不影响历史能不能接着用，不算进去。
        """
        if not self:
            return ""
        spec = {"server": [t.name for t in self.server], "local": [t.definition() for t in self.local]}
        raw = json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def find(self, name: str) -> Tool | None:
        return next((t for t in self.local if t.name == name), None)
