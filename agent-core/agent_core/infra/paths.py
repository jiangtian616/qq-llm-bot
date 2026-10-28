"""数据目录。平台差异只在 infra/ 里处理。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "qq-llm-bot"


def default_data_dir() -> Path:
    # Windows: %APPDATA%\qq-llm-bot；macOS: ~/Library/Application Support/qq-llm-bot；Linux: ~/.local/share/qq-llm-bot
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=True))


@dataclass(frozen=True, slots=True)
class DataPaths:
    root: Path

    @classmethod
    def resolve(cls, data_dir: str | Path | None) -> DataPaths:
        root = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir()
        paths = cls(root)
        for d in (paths.root, paths.logs_dir, paths.backups_dir):
            d.mkdir(parents=True, exist_ok=True)
        return paths

    @property
    def database(self) -> Path:
        return self.root / "agent-core.db"

    @property
    def lock_file(self) -> Path:
        return self.root / "agent-core.lock"

    @property
    def control_file(self) -> Path:
        """控制接口的端口和随机令牌，只有当前用户能读。"""
        return self.root / "control.json"

    @property
    def secrets_file(self) -> Path:
        """没有系统密钥库时的退路文件。"""
        return self.root / "secrets.json"

    @property
    def config_file(self) -> Path:
        return self.root / "agent-core.toml"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"
