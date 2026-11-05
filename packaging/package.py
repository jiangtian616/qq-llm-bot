r"""打包便携版：核心服务（Nuitka 独立目录）和桌面端（flutter build --release）同时构建，放在一起打成一个包。

    Windows  qq-llm-bot-<版本>-windows-x64.zip       解压即用；数据在 %APPDATA%\qq-llm-bot
    macOS    qq-llm-bot-<版本>-macos-<arm64|x64>.zip  解压出 .app；数据在 ~/Library/Application Support/qq-llm-bot
    Linux    qq-llm-bot-<版本>-linux-x86_64.AppImage  单文件运行；数据在 ~/.local/share/qq-llm-bot

包里不带 qq-llm-bot-data 文件夹：升级时常常是删掉整个旧程序文件夹再解压新包，数据放在程序旁边会跟着一起删掉。
需要便携模式时由用户自己在程序旁边新建这个文件夹，规则见 agent_core/infra/paths.py。

用法（在仓库根目录，每个平台在自己的系统上构建）：
    uv run --project agent-core --group build python packaging/package.py
    可选：--skip-core（复用 build/core 里已有的核心服务）、--skip-flutter（复用已有的桌面端构建）
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
CORE_OUT = BUILD / "core"
APP = "qq-llm-bot"
FLUTTER_BINARY = "qq_llm_bot"


def version() -> str:
    text = (ROOT / "pubspec.yaml").read_text(encoding="utf-8")
    return re.search(r"^version:\s*([^+\s]+)", text, re.M).group(1)


def machine() -> str:
    m = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(m, m)


def run(cmd: list[str], **kw) -> None:  # noqa: ANN003
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def build_core(skip: bool) -> Path:
    core = CORE_OUT / "agent-core"
    if skip and core.exists():
        return core
    run([sys.executable, str(ROOT / "packaging" / "build_core.py"), "--out", str(CORE_OUT)])
    return core


class FlutterBuild:
    """在后台构建桌面端，和核心服务同时进行。

    两边互不依赖；Nuitka 分析代码时只用得上一个核，空着的核正好给 Flutter。
    输出先写进日志文件，结束时一次打印出来，免得和核心服务的输出搅在一起。
    """

    def __init__(self, target: str) -> None:
        self.cmd = ["flutter", "build", target, "--release"]
        self.log = BUILD / f"flutter-{target}.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        print("+", " ".join(self.cmd), f"（后台运行，输出写到 {self.log}）", flush=True)
        started = time.monotonic()
        with self.log.open("wb") as out:
            # flutter 在 Windows 上是 .bat，要经过 shell 才能启动。
            proc = subprocess.Popen(
                self.cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, shell=sys.platform == "win32"
            )
        self.code = 0
        self.minutes = 0.0

        def watch() -> None:
            # 结束时就记下用时，不算等核心服务的时间。
            self.code = proc.wait()
            self.minutes = (time.monotonic() - started) / 60

        self.watcher = threading.Thread(target=watch, daemon=True)
        self.watcher.start()

    def wait(self) -> None:
        self.watcher.join()
        # 原样输出：Windows 上 Flutter 和 MSBuild 按系统代码页输出，按 UTF-8 解码会把中文变成乱码。
        sys.stdout.flush()
        sys.stdout.buffer.write(self.log.read_bytes())
        sys.stdout.buffer.flush()
        if self.code:
            raise subprocess.CalledProcessError(self.code, self.cmd)
        print(f"桌面端构建完成（用时 {self.minutes:.1f} 分钟）", flush=True)


def fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def package_windows(core: Path, ver: str) -> Path:
    release = BUILD / "windows" / "x64" / "runner" / "Release"
    stage = fresh(DIST / "stage") / APP
    shutil.copytree(release, stage)
    shutil.copytree(core, stage / "agent-core")
    out = DIST / f"{APP}-{ver}-windows-{machine()}"
    return Path(shutil.make_archive(str(out), "zip", root_dir=stage.parent, base_dir=APP))


def package_macos(core: Path, ver: str) -> Path:
    built = BUILD / "macos" / "Build" / "Products" / "Release" / f"{FLUTTER_BINARY}.app"
    stage = fresh(DIST / "stage")
    app = stage / f"{APP}.app"
    shutil.copytree(built, app, symlinks=True)
    # 核心服务放进 .app 的 Contents/Helpers，和 .app 一起签名；桌面端按这个相对路径找它。
    shutil.copytree(core, app / "Contents" / "Helpers" / "agent-core", symlinks=True)
    # 没有 Apple 开发者账号时用临时签名（Apple 芯片上未签名的程序不能运行）；首次打开要在系统设置里手动允许。
    run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    out = DIST / f"{APP}-{ver}-macos-{machine()}.zip"
    out.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(out)])
    return out


def package_linux(core: Path, ver: str) -> Path:
    arch_dir = "arm64" if machine() == "arm64" else "x64"
    bundle = BUILD / "linux" / arch_dir / "release" / "bundle"
    appdir = fresh(DIST / "stage") / f"{APP}.AppDir"
    shutil.copytree(bundle, appdir, dirs_exist_ok=True)
    shutil.copytree(core, appdir / "agent-core")
    apprun = appdir / "AppRun"
    apprun.write_text(f'#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/{FLUTTER_BINARY}" "$@"\n')
    apprun.chmod(0o755)
    (appdir / f"{APP}.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP}\n"
        "Comment=QQ 官方机器人 + 大模型对话 Agent\n"
        f"Exec={FLUTTER_BINARY}\n"
        f"Icon={APP}\n"
        "Categories=Network;Chat;\n"
        "Terminal=false\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "assets" / "branding" / "app_icon_256.png", appdir / f"{APP}.png")
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool:
        raise SystemExit("找不到 appimagetool：请安装，或用环境变量 APPIMAGETOOL 指定路径")
    arch = "aarch64" if machine() == "arm64" else "x86_64"
    out = DIST / f"{APP}-{ver}-linux-{arch}.AppImage"
    run([tool, str(appdir), str(out)], env={**os.environ, "ARCH": arch})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 qq-llm-bot 便携版")
    parser.add_argument("--skip-core", action="store_true", help="复用 build/core 里已有的核心服务")
    parser.add_argument("--skip-flutter", action="store_true", help="复用已有的桌面端 release 构建")
    args = parser.parse_args()

    ver = version()
    DIST.mkdir(exist_ok=True)
    target = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    flutter = None if args.skip_flutter else FlutterBuild(target)
    try:
        core = build_core(args.skip_core)
    finally:
        # 核心服务失败时也等桌面端结束，不留下没人管的后台进程。
        if flutter:
            flutter.wait()
    package = {"windows": package_windows, "macos": package_macos, "linux": package_linux}[target]
    out = package(core, ver)
    shutil.rmtree(DIST / "stage", ignore_errors=True)
    print(f"已生成：{out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
