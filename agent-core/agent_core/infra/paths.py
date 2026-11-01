"""数据目录。平台差异只在 infra/ 里处理。

和桌面端的 platform/app_paths.dart 按同一套规则解析，从上往下用第一个命中的：
1. 命令行参数 --data-dir（桌面端拉起核心服务时总是带上），或环境变量 QQ_LLM_BOT_DATA_DIR；
2. 便携模式：程序旁边有 qq-llm-bot-data 文件夹，数据就放在里面（只对打包后的程序生效）；
3. 桌面端设置页选的自定义目录，路径记在系统默认目录的 data-location.json 里；
4. 系统默认目录。

便携模式不再随安装包默认开启：升级时常常是删掉整个旧程序文件夹再解压新包，数据放在程序旁边会跟着一起删掉。
"""

from __future__ import annotations

import enum
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "qq-llm-bot"
ENV_DATA_DIR = "QQ_LLM_BOT_DATA_DIR"
# 不叫 data：Flutter 的 Windows / Linux 产物里已经有一个 data 目录。
PORTABLE_DIR_NAME = "qq-llm-bot-data"
# 自定义数据目录的指针文件，放在系统默认目录里；格式 {"path": "..."}，由桌面端写入。
POINTER_FILE_NAME = "data-location.json"


class DataDirSource(enum.StrEnum):
    ARGUMENT = "argument"
    ENVIRONMENT = "environment"
    PORTABLE = "portable"
    CUSTOM = "custom"
    STANDARD = "standard"


class DataDirError(RuntimeError):
    pass


def default_data_dir() -> Path:
    # Windows: %APPDATA%\qq-llm-bot；macOS: ~/Library/Application Support/qq-llm-bot；Linux: ~/.local/share/qq-llm-bot
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False, roaming=True))


def custom_data_dir(standard: Path) -> Path | None:
    """读系统默认目录里的指针文件；没有或内容不对时返回 None。

    指针指向的目录不存在时直接报错，不悄悄换回默认目录：那样看起来就像数据全丢了。
    """
    pointer = standard / POINTER_FILE_NAME
    try:
        value = json.loads(pointer.read_text(encoding="utf-8")).get("path")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    if not path.is_dir():
        raise DataDirError(
            f"设置的数据目录不存在：{path}。如果它在移动硬盘或网络盘上，请先接好再启动；"
            f"不再使用这个目录的话，删除 {pointer} 即可改回默认目录。"
        )
    return path.resolve()


def is_packaged() -> bool:
    """Nuitka 编译后模块里有 __compiled__；PyInstaller 等会设置 sys.frozen。源码运行时两者都没有。"""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def program_anchor() -> Path | None:
    """「程序旁边」指的目录；源码运行时返回 None，不支持便携模式。

    核心服务随桌面端分发：Windows / Linux 在 <桌面端目录>/agent-core/，macOS 在 X.app/Contents/Helpers/agent-core/。
    AppImage 运行时可执行文件在临时挂载的目录里，用环境变量 APPIMAGE 找到 AppImage 文件本身。
    """
    if not is_packaged():
        return None
    appimage = os.environ.get("APPIMAGE")
    if sys.platform.startswith("linux") and appimage:
        return Path(appimage).parent
    core_dir = Path(sys.argv[0]).resolve().parent
    if sys.platform == "darwin":
        return core_dir.parents[3]  # Helpers → Contents → X.app → X.app 所在目录
    return core_dir.parent


def _ensure_writable(path: Path) -> None:
    probe = path / ".write-test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise DataDirError(
            f"便携模式的数据目录不可写：{path}（{e.strerror}）。"
            f"请把程序移到有写入权限的位置，或删除 {PORTABLE_DIR_NAME} 文件夹改用系统目录。"
        ) from e


def resolve_data_dir(argument: str | Path | None, anchor: Path | None = None) -> tuple[Path, DataDirSource]:
    if argument:
        return Path(argument).expanduser().resolve(), DataDirSource.ARGUMENT
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env).expanduser().resolve(), DataDirSource.ENVIRONMENT
    anchor = anchor if anchor is not None else program_anchor()
    if anchor is not None and (anchor / PORTABLE_DIR_NAME).is_dir():
        portable = anchor / PORTABLE_DIR_NAME
        # 便携文件夹在但写不进去时直接报错，不悄悄换到系统目录。
        _ensure_writable(portable)
        return portable, DataDirSource.PORTABLE
    standard = default_data_dir()
    custom = custom_data_dir(standard)
    if custom is not None:
        return custom, DataDirSource.CUSTOM
    return standard, DataDirSource.STANDARD


@dataclass(frozen=True, slots=True)
class DataPaths:
    root: Path
    source: DataDirSource = DataDirSource.STANDARD

    @classmethod
    def resolve(cls, data_dir: str | Path | None, anchor: Path | None = None) -> DataPaths:
        root, source = resolve_data_dir(data_dir, anchor)
        paths = cls(root, source)
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
