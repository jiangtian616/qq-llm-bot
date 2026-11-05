"""用 Nuitka 把核心服务打成独立目录 agent-core/，自带 Python 运行时，用户不用装 Python。

第三方包按字节码打进去，编译成 C 的只有我们自己的代码和个别例外（见 third_party_packages()）。
本机 16 核首次构建约 5 分钟，有缓存时约 3 分钟；缓存在 Nuitka 的缓存目录里。

用法（在仓库根目录）：
    uv run --project agent-core --group build python packaging/build_core.py [--out build/core]

输出：<out>/agent-core/agent-core(.exe) 及其依赖。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from importlib.metadata import packages_distributions
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
    # 自定义 socks 代理时才导入
    "aiohttp_socks",
    "python_socks",
    "socksio",
]
INCLUDE_MODULES = [
    "sqlalchemy.dialects.sqlite.aiosqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
]
if sys.platform == "win32":
    INCLUDE_PACKAGES.append("win32ctypes")  # keyring 的 Windows 凭据管理器后端
elif sys.platform.startswith("linux"):
    INCLUDE_PACKAGES += ["secretstorage", "jeepney"]  # keyring 的 Secret Service 后端

# 不算第三方的发行包：我们自己的代码编译成 C（插件在打包后由 registry.BUILTIN_PLUGINS 兜底，不需要元数据），
# 打包工具不进包。
OWN_DISTRIBUTIONS = {"agent-core", "nuitka"}
# 仍然编译成 C 的第三方包。Nuitka 的 multiprocessing 插件会给 anyio.to_process 加一段导入后执行的代码，
# 这段代码跟着按字节码处理的话，第二次构建从缓存载入它时 Nuitka 4.3.1 会崩溃
# （'UncompiledPythonModule' object has no attribute 'getCompilationMode'）。
STILL_COMPILED = {"anyio"}


def third_party_packages() -> dict[str, list[str]]:
    """当前环境里装的第三方顶层包 → 它所属的发行包。除了 STILL_COMPILED，都按字节码打进去，不编译成 C。

    全部编译时约 3500 个模块要转成 C（约 2500 个来自 anthropic SDK：import anthropic 会加载全部类型定义），
    CI 上一次要 20~80 分钟；按字节码打包（标准库 Nuitka 默认就是这样）以后只剩 100 多个。
    这些包运行起来大多在等网络，编译成 C 也快不了多少。
    """
    result: dict[str, list[str]] = {}
    for top, dists in packages_distributions().items():
        owners = sorted({d for d in dists if re.sub(r"[-_.]+", "-", d).lower() not in OWN_DISTRIBUTIONS})
        if owners and top.isidentifier() and not top.startswith("__"):
            result[top] = owners
    return dict(sorted(result.items()))


def write_metadata_config(packages: dict[str, list[str]], path: Path) -> Path:
    """生成 Nuitka 的包配置：哪个顶层包打进来了，就带上它所属发行包的元数据。

    字节码模块到运行时才调用 importlib.metadata（httpx2 导入时就查自己的版本号，keyring 靠 entry points
    找密钥库后端），而 Nuitka 只给编译成 C 的模块补上它在编译期查到的元数据，所以要自己带上。
    不用 --include-distribution-metadata：对应的包没打进来时它直接报错，包配置只在包打进来时才生效。
    """
    lines = []
    for top, dists in packages.items():
        lines += [f"- module-name: '{top}'", "  data-files:", "    - include-metadata:"]
        lines += [f"        - '{d}'" for d in dists]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "build" / "core"))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    work = out / "nuitka"
    entry = Path(__file__).with_name("agent_core_entry.py")
    packages = third_party_packages()
    metadata_config = write_metadata_config(packages, out / "metadata.nuitka-package.config.yml")

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
        *(f"--noinclude-custom-mode={p}:bytecode" for p in packages if p not in STILL_COMPILED),
        f"--user-package-configuration-file={metadata_config}",
        # 测试辅助代码运行时用不到；alembic 整包引入时会把它们带进来。
        "--nofollow-import-to=*.tests",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=alembic.testing",
        "--nofollow-import-to=sqlalchemy.testing",
        str(entry),
    ]
    print(" ".join(cmd), flush=True)
    started = time.monotonic()
    subprocess.run(cmd, check=True, cwd=CORE)

    dist = work / f"{entry.stem}.dist"
    target = out / "agent-core"
    if target.exists():
        shutil.rmtree(target)
    shutil.move(dist, target)
    _ensure_migrations(target)
    print(f"核心服务已输出到 {target}（用时 {(time.monotonic() - started) / 60:.1f} 分钟）")
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
