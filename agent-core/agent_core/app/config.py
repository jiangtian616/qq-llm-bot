"""启动配置：只放进程级的东西（选哪些插件、监听端口）。机器人行为存在 settings 表里，由桌面端「配置」页修改。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class PluginsConfig(BaseModel):
    channel: str = "qq"
    transport: str = "websocket"
    provider: str = "claude"


class ControlConfig(BaseModel):
    port: int = Field(0, ge=0, le=65535)
    """0 表示随机端口；实际端口写在数据目录的 control.json 里。"""


class AppConfig(BaseModel):
    plugins: PluginsConfig = PluginsConfig()
    control: ControlConfig = ControlConfig()


def load_config(path: Path) -> AppConfig:
    """数据目录里的 agent-core.toml，可选；没有就用默认值。"""
    if not path.exists():
        return AppConfig()
    with path.open("rb") as f:
        return AppConfig.model_validate(tomllib.load(f))
