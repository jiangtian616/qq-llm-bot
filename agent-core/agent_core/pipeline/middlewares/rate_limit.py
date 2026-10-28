"""按用户限频：滑动窗口，进程内计数（重启后清零即可，不需要持久化）。"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Hashable


class RateLimiter:
    def __init__(self, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._window = window_s
        self._clock = clock
        self._hits: dict[Hashable, deque[float]] = {}
        self._blocked: set[Hashable] = set()

    def hit(self, key: Hashable, limit: int) -> tuple[bool, bool]:
        """记一次触发。返回 (是否放行, 是否这个窗口里第一次被拦)；第一次被拦时给用户回一句提醒，之后不再回。"""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if not hits:
            self._blocked.discard(key)
        if len(hits) < limit:
            hits.append(now)
            return True, False
        first = key not in self._blocked
        self._blocked.add(key)
        return False, first
