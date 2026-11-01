"""问答过程记录：一轮里组装上下文、每次请求模型、每次工具调用、回复编排、历史压缩各记一步，桌面端按时间线展示。

只记本轮新增的内容（本轮的 user 消息、模型和工具产生的消息），不重复存之前的历史和系统提示词。
图片的 base64、搜索结果里的加密内容、思考块的签名这类又大又看不懂的字段换成占位文字，太长的字符串截断。
步骤先攒在内存里，引擎在这一轮结束（包括失败、超时）时写进 turn_steps；提供商在 generate() 里往里加。
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agent_core.core.clock import now_ms

T = TypeVar("T")

MAX_STRING = 8000
"""data 里单个字符串的上限；detail 是给人看的摘要，另外限制。"""
MAX_DETAIL = 6000
_OPAQUE_KEYS = {"encrypted_content", "encrypted_index", "signature"}


@dataclass(slots=True)
class TraceStep:
    kind: str
    """context / request / search / tool / reply / compose / compaction / note / error"""
    title: str
    detail: str = ""
    data: Any = None
    error: str | None = None
    started_at: int = field(default_factory=now_ms)
    duration_ms: int | None = None


class Trace:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    def add(
        self,
        kind: str,
        title: str,
        *,
        detail: str = "",
        data: Any = None,
        error: str | None = None,
        started_at: int | None = None,
        duration_ms: int | None = None,
    ) -> TraceStep:
        step = TraceStep(
            kind,
            title,
            truncate(detail, MAX_DETAIL),
            sanitize(data),
            error,
            started_at if started_at is not None else now_ms(),
            duration_ms,
        )
        self.steps.append(step)
        return step

    def tool(self, name: str, arguments: Any, result: Any, watch: Stopwatch) -> None:
        """一次本地工具调用；result 是 ToolResult。"""
        args = arguments if isinstance(arguments, dict) else {}
        preview = next((v for v in args.values() if isinstance(v, str) and v), "")
        lines = [] if result.is_error else [result.content]
        if result.cost_usd:
            lines.append(f"费用 ${result.cost_usd:.4f}")
        self.add(
            "tool",
            f"工具 {name}" + (f"：{short(preview)}" if preview else ""),
            detail="\n".join(lines),
            data={"arguments": arguments, "links": list(result.links)},
            error=result.content if result.is_error else None,
            started_at=watch.started_at,
            duration_ms=watch.ms,
        )

    def reply(self, arguments: Any, problem: str | None, *, accepted: bool) -> None:
        """模型调用了回复工具：accepted 为假表示校验不过、交回模型重来。"""
        parts = arguments.get("parts") if isinstance(arguments, dict) else None
        n = len(parts) if isinstance(parts, list) else 0
        if not accepted:
            title = "回复工具被打回，交回模型重来一次"
        elif problem:
            title = f"回复工具：{n} 段（校验仍不通过，不合规的段由回复编排丢掉）"
        else:
            title = f"回复工具：{n} 段"
        self.add("reply", title, data={"parts": parts}, error=problem)

    def drain(self) -> list[TraceStep]:
        """取走已经攒下的步骤（写库后清空，同一轮后面再加的接着写）。"""
        steps, self.steps = self.steps, []
        return steps


class Stopwatch:
    """记下开始时间（毫秒时间戳）和耗时。"""

    def __init__(self) -> None:
        self.started_at = now_ms()
        self._t0 = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)


async def timed(awaitable: Awaitable[T]) -> tuple[T, Stopwatch]:
    watch = Stopwatch()
    return await awaitable, watch


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…（共 {len(text)} 字，已截断）"


def sanitize(value: Any) -> Any:
    """复制一份适合存下来看的 JSON：去掉图片数据、加密内容和签名，截断太长的字符串。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _OPAQUE_KEYS and isinstance(v, str):
                out[k] = f"<{len(v)} 字符，已省略>"
            elif k == "data" and isinstance(v, str) and len(v) > 200:
                out[k] = f"<{len(v)} 字符的数据，已省略>"
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(value, list | tuple):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        if value.startswith("data:") and len(value) > 200:
            return f"<{value[: value.find(',')]}，{len(value)} 字符，已省略>"
        return truncate(value, MAX_STRING)
    return value


def short(value: Any, limit: int = 60) -> str:
    """工具参数之类的单行预览。"""
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(raw.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
