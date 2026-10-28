"""调度器：同一会话串行，保证回复顺序和历史一致；不同会话并发，设并发上限。

有新活时用 asyncio.Event 立即唤醒；另每 5 秒轮询一次数据库兜底，重启后也能接上。
"""

from __future__ import annotations

import asyncio

DEFAULT_CONCURRENCY = 4
POLL_INTERVAL_S = 5.0


class Dispatcher:
    """TODO(下一阶段)：从收件箱取活，经中间件链交给 Agent 引擎。"""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._wakeup = asyncio.Event()

    def wake(self) -> None:
        self._wakeup.set()

    async def run(self) -> None:
        raise NotImplementedError
