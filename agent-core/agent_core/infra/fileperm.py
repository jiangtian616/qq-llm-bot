"""只让当前用户读写的文件。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def write_private_file(path: Path, text: str) -> None:
    """原子写入；macOS / Linux 上权限为 0600。

    Windows 上数据目录在 %APPDATA% 下，默认 ACL 只允许当前用户和系统访问，不再单独设置。
    """
    tmp = path.with_name(path.name + ".tmp")
    if sys.platform == "win32":
        tmp.write_text(text, encoding="utf-8")
    else:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def is_private(path: Path) -> bool:
    if sys.platform == "win32":
        return True
    return (path.stat().st_mode & 0o077) == 0
