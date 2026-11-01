"""打包便携版：核心服务（Nuitka 独立目录）+ 桌面端（flutter build --release）放在一起，打成一个包。

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
    # flutter 在 Windows 上是 .bat，要经过 shell 才能启动。
    subprocess.run(cmd, check=True, shell=sys.platform == "win32" and cmd[0] == "flutter", **kw)


def build_core(skip: bool) -> Path:
    core = CORE_OUT / "agent-core"
    if skip and core.exists():
        return core
    run([sys.executable, str(ROOT / "packaging" / "build_core.py"), "--out", str(CORE_OUT)])
    return core


def build_flutter(target: str, skip: bool) -> None:
    if not skip:
        run(["flutter", "build", target, "--release"], cwd=ROOT)


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
    core = build_core(args.skip_core)
    if sys.platform == "win32":
        build_flutter("windows", args.skip_flutter)
        out = package_windows(core, ver)
    elif sys.platform == "darwin":
        build_flutter("macos", args.skip_flutter)
        out = package_macos(core, ver)
    else:
        build_flutter("linux", args.skip_flutter)
        out = package_linux(core, ver)
    shutil.rmtree(DIST / "stage", ignore_errors=True)
    print(f"已生成：{out}（{out.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
