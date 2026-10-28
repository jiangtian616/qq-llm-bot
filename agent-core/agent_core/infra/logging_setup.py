"""日志：写文件 + 控制台，并把已知密钥替换成掩码。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path


class SecretRedactor(logging.Filter):
    """SecretStore 读写过的密钥会登记到这里；任何日志里出现它们都会被替换。"""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str | None) -> None:
        if value and len(value) >= 4:
            with self._lock:
                self._secrets.add(value)

    def redact(self, text: str) -> str:
        for s in self._secrets:
            if s in text:
                text = text.replace(s, "***" + s[-4:])
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            record.msg = self.redact(record.getMessage())
            record.args = None
        return True


redactor = SecretRedactor()


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "agent-core.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handlers: list[logging.Handler] = [file_handler]
    # 桌面端以分离模式拉起时没有控制台，sys.stderr 可能是 None。
    if sys.stderr is not None:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        handlers.append(logging.StreamHandler(sys.stderr))
    root = logging.getLogger()
    root.setLevel(level)
    # Claude SDK 每个请求都打一行 INFO，太吵；出错时 SDK 和我们自己都会另外记录。
    logging.getLogger("httpx2").setLevel(max(level, logging.WARNING))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(redactor)
        root.addHandler(h)
