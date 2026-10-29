"""用 Nuitka 把核心服务打成独立目录 agent-core/，自带 Python 运行时，用户不用装 Python。

首次构建要编译约 3500 个模块（其中约 2500 个来自 anthropic SDK：import anthropic 时会加载全部类型定义，
没法裁掉），本机 16 核约 15 分钟。编译结果缓存在 Nuitka 的缓存目录里，之后再构建会快很多。

用法（在仓库根目录）：
    uv run --project agent-core --group build python packaging/build_core.py [--out build/core]

输出：<out>/agent-core/agent-core(.exe) 及其依赖。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "agent-core"

# 运行时按名字动态加载、Nuitka 静态分析看不到的模块。
INCLUDE_PACKAGES = [
    "agent_core",  # 插件通过 entry points 按配置加载
    "uvicorn",  # 协议、事件循环、lifespan 按配置导入
    "websockets",
    "keyring.backends",  # 密钥库后端通过 entry points 发现
    "aiosqlite",
    "alembic",
    "anyio",
]
INCLUDE_MODULES = [
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
]
# importlib.metadata 要用到的分发元数据：keyring 通过 entry points 发现密钥库后端。
# 我们自己的插件在打包后由 registry.BUILTIN_PLUGINS 兜底，不需要元数据。
INCLUDE_METADATA = ["keyring"]
if sys.platform == "win32":
    INCLUDE_PACKAGES.append("win32ctypes")  # keyring 的 Windows 凭据管理器后端
elif sys.platform.startswith("linux"):
    INCLUDE_PACKAGES += ["secretstorage", "jeepney"]  # keyring 的 Secret Service 后端


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "build" / "core"))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    work = out / "nuitka"
    entry = Path(__file__).with_name("agent_core_entry.py")

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        f"--output-dir={work}",
        "--output-filename=agent-core",
        # Alembic 从磁盘读迁移脚本（env.py、versions/*.py），按源文件原样带上。
        f"--include-data-dir={CORE / 'agent_core/infra/db/migrations'}=agent_core/infra/db/migrations",
        *(f"--include-package={p}" for p in INCLUDE_PACKAGES),
        *(f"--include-module={m}" for m in INCLUDE_MODULES),
        *(f"--include-distribution-metadata={d}" for d in INCLUDE_METADATA),
        # 测试辅助代码运行时用不到；alembic 整包引入时会把它们带进来。
        "--nofollow-import-to=*.tests",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=alembic.testing",
        "--nofollow-import-to=sqlalchemy.testing",
        str(entry),
    ]
    if sys.platform == "darwin":
        cmd.append("--macos-create-app-bundle=no")
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=CORE)

    dist = work / f"{entry.stem}.dist"
    target = out / "agent-core"
    if target.exists():
        shutil.rmtree(target)
    shutil.move(dist, target)
    _ensure_migrations(target)
    print(f"核心服务已输出到 {target}")
    return 0


def _ensure_migrations(target: Path) -> None:
    """Nuitka 默认不把 .py 当数据文件带上；缺了就直接复制迁移目录（Alembic 只读源文件，不 import 包）。"""
    src = CORE / "agent_core/infra/db/migrations"
    dst = target / "agent_core/infra/db/migrations"
    for path in src.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            dest = dst / path.relative_to(src)
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)


if __name__ == "__main__":
    sys.exit(main())
