from __future__ import annotations

from typing import Protocol


class SecretReader(Protocol):
    """插件读取密钥的唯一入口。实现见 infra/secrets.py；插件只拿到这个接口。"""

    def get_secret(self, key: str) -> str | None: ...
