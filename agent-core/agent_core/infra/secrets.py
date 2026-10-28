"""密钥存储：系统密钥库优先，没有可用后端时退回权限 0600 的文件。

密钥绝不写进数据库、日志和导出文件；数据库里只存键名（如 qq:{app_id}）。
"""

from __future__ import annotations

import enum
import json
import logging
import threading
from pathlib import Path

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

from agent_core.infra.fileperm import write_private_file
from agent_core.infra.logging_setup import redactor

log = logging.getLogger(__name__)

SERVICE_NAME = "qq-llm-bot"
_PROBE_KEY = "__probe__"


class SecretBackendKind(enum.StrEnum):
    KEYRING = "keyring"
    FILE = "file"


def _keyring_usable(backend: KeyringBackend) -> bool:
    # fail.Keyring 和 null.Keyring 的 priority 都小于 1；chainer 没有可用子后端时同理。
    try:
        if backend.priority < 1:  # type: ignore[operator]
            return False
    except Exception:
        return False
    # 有些后端（如锁住的 Secret Service）要到真正调用时才报错，先探一次。
    try:
        backend.get_password(SERVICE_NAME, _PROBE_KEY)
    except Exception as e:
        log.warning("系统密钥库不可用（%s），改用文件存储", type(e).__name__)
        return False
    return True


class SecretStore:
    def __init__(self, fallback_file: Path, backend: KeyringBackend | None = None) -> None:
        self._fallback_file = fallback_file
        self._lock = threading.Lock()
        candidate = backend if backend is not None else keyring.get_keyring()
        if _keyring_usable(candidate):
            self._backend: KeyringBackend | None = candidate
            self.kind = SecretBackendKind.KEYRING
        else:
            self._backend = None
            self.kind = SecretBackendKind.FILE
            log.warning("没有可用的系统密钥库，密钥将存放在 %s（权限 0600）", fallback_file)

    @property
    def backend_name(self) -> str:
        if self._backend is None:
            return "file"
        return f"{type(self._backend).__module__}.{type(self._backend).__name__}"

    def get_secret(self, key: str) -> str | None:
        if self._backend is not None:
            value = self._backend.get_password(SERVICE_NAME, key)
        else:
            value = self._read_file().get(key)
        redactor.register(value)
        return value

    def set_secret(self, key: str, value: str) -> None:
        if not value:
            raise ValueError("密钥不能为空")
        redactor.register(value)
        if self._backend is not None:
            self._backend.set_password(SERVICE_NAME, key, value)
            return
        with self._lock:
            data = self._read_file()
            data[key] = value
            write_private_file(self._fallback_file, json.dumps(data))

    def delete_secret(self, key: str) -> None:
        if self._backend is not None:
            try:
                self._backend.delete_password(SERVICE_NAME, key)
            except keyring.errors.PasswordDeleteError:
                pass
            return
        with self._lock:
            data = self._read_file()
            if data.pop(key, None) is not None:
                write_private_file(self._fallback_file, json.dumps(data))

    @staticmethod
    def mask(value: str | None) -> str:
        """界面上只显示末 4 位。"""
        if not value:
            return ""
        return "****" + value[-4:]

    def _read_file(self) -> dict[str, str]:
        if not self._fallback_file.exists():
            return {}
        return json.loads(self._fallback_file.read_text(encoding="utf-8") or "{}")

    def __repr__(self) -> str:
        return f"SecretStore(kind={self.kind}, backend={self.backend_name})"
