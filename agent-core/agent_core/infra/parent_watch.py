"""监视拉起核心服务的桌面端：桌面端退出（包括被强制结束）后，核心服务跟着平稳停止。"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from collections.abc import Callable

_POLL_S = 1.0


def watch_parent(pid: int, on_exit: Callable[[], None]) -> None:
    """在后台线程里等 pid 对应的进程退出，退出后调用一次 on_exit（在后台线程里调用）。"""
    target = _wait_windows if sys.platform == "win32" else _wait_posix
    threading.Thread(target=lambda: (target(pid), on_exit()), name="parent-watch", daemon=True).start()


def _wait_windows(pid: int) -> None:
    # 一开始就拿住进程句柄再等，不会因为 PID 被复用而认错进程。
    synchronize, infinite = 0x00100000, 0xFFFFFFFF
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return  # 打不开说明已经退出了
    try:
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), infinite)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _wait_posix(pid: int) -> None:
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass  # 进程还在，只是没权限发信号
        time.sleep(_POLL_S)
