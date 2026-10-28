"""单实例锁：两个进程同时连 QQ 会把同一条消息回复两次。"""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock, Timeout


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self._lock = FileLock(path, timeout=0)

    def acquire(self) -> None:
        try:
            self._lock.acquire()
        except Timeout as e:
            raise AlreadyRunningError(f"核心服务已在运行（锁文件：{self._lock.lock_file}）") from e

    def release(self) -> None:
        self._lock.release()

    @property
    def is_locked(self) -> bool:
        return self._lock.is_locked

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
