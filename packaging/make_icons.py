"""从两张主图生成各平台的应用图标和托盘图标。换图标时替换主图，再运行一次这个脚本。

主图（都是 1024×1024、透明背景的 PNG）：
    assets/branding/icon.png        完整图标，图案铺满画布（四周不留边）
    assets/branding/icon_small.png  简化版，32 像素及以下用；小尺寸下完整图的细节会糊成一团

生成：
    windows/runner/resources/app_icon.ico                 exe、任务栏、窗口标题栏
    macos/Runner/Assets.xcassets/AppIcon.appiconset/*.png  按 Apple 规范缩到画布中央 824 像素并加投影
    assets/tray/tray_icon.ico、tray_icon.png               系统托盘（Windows 用 ico，macOS / Linux 用 png）
    assets/branding/app_icon_256.png                      Linux AppImage

用法（在仓库根目录；Pillow 只在运行时临时安装，不进项目依赖）：
    uv run --no-project --with pillow python packaging/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
BRANDING = ROOT / "assets" / "branding"
SMALL_MAX = 32  # 这个尺寸及以下用简化版

# Windows 小图标在 125% / 150% 缩放下是 20、24 像素，大图标视图用到 256。
WINDOWS_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
TRAY_SIZES = [16, 20, 24, 32, 40, 48, 64]
MACOS_SIZES = [16, 32, 64, 128, 256, 512, 1024]

# Apple 的 macOS 图标模板：1024 画布里，圆角方块占中央 824×824，下方有一层柔和投影。
MACOS_CONTENT = 824
MACOS_SHADOW_OFFSET = 12
MACOS_SHADOW_BLUR = 14
MACOS_SHADOW_OPACITY = 0.45


def load(name: str) -> Image.Image:
    im = Image.open(BRANDING / name).convert("RGBA")
    if im.size != (1024, 1024):
        raise SystemExit(f"{name} 要是 1024×1024，现在是 {im.width}×{im.height}")
    return im


def resize(im: Image.Image, size: int) -> Image.Image:
    # 每个尺寸都直接从 1024 的主图缩，不在小图上再缩小图。
    return im.resize((size, size), Image.LANCZOS)


def macos_style(im: Image.Image) -> Image.Image:
    inner = resize(im, MACOS_CONTENT)
    offset = (1024 - MACOS_CONTENT) // 2
    shadow = Image.new("RGBA", (1024, 1024))
    shadow_alpha = inner.getchannel("A").point(lambda v: int(v * MACOS_SHADOW_OPACITY))
    shadow.paste((0, 0, 0, 255), (offset, offset + MACOS_SHADOW_OFFSET), shadow_alpha)
    canvas = shadow.filter(ImageFilter.GaussianBlur(MACOS_SHADOW_BLUR))
    canvas.alpha_composite(inner, (offset, offset))
    return canvas


def write_ico(path: Path, frames: list[Image.Image]) -> None:
    # 每个尺寸单独缩好再交给 Pillow，避免它从一张图自己缩；最大的一帧作为主图。
    frames = sorted(frames, key=lambda f: f.width, reverse=True)
    frames[0].save(path, format="ICO", sizes=[f.size for f in frames], append_images=frames[1:])


def main() -> None:
    full, small = load("icon.png"), load("icon_small.png")

    def pick(size: int) -> Image.Image:
        return small if size <= SMALL_MAX else full

    write_ico(ROOT / "windows" / "runner" / "resources" / "app_icon.ico", [resize(pick(s), s) for s in WINDOWS_SIZES])

    appiconset = ROOT / "macos" / "Runner" / "Assets.xcassets" / "AppIcon.appiconset"
    mac_full, mac_small = macos_style(full), macos_style(small)
    for s in MACOS_SIZES:
        resize(mac_small if s <= SMALL_MAX else mac_full, s).save(appiconset / f"app_icon_{s}.png", optimize=True)

    tray = ROOT / "assets" / "tray"
    write_ico(tray / "tray_icon.ico", [resize(small, s) for s in TRAY_SIZES])
    resize(small, 64).save(tray / "tray_icon.png", optimize=True)

    resize(full, 256).save(BRANDING / "app_icon_256.png", optimize=True)
    print("已生成 Windows、macOS、托盘和 Linux 图标")


if __name__ == "__main__":
    main()
