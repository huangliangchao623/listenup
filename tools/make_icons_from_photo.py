"""从一张照片生成全套图标。

## 为什么手工组装 ICO

环境里没有 Pillow（`ModuleNotFoundError: No module named 'PIL'`），
但 ffmpeg 有。所以分工：

  ffmpeg   负责裁切 + 缩放 + 编码 PNG
  本脚本    负责把多个尺寸的 PNG 拼成 .ico

ICO 从 Vista 起支持内嵌 PNG，格式很简单：
  6 字节头 + 每张图 16 字节目录项 + 各图 PNG 数据

## 裁切策略

原图是 1440×810 的横图，人物在中间偏上。
直接用整张图做图标，缩到 32px 会看不清脸，
所以裁一个 700×700 的方形，以人物面部为中心。
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "tools" / "bin" / "ffmpeg.exe"
SRC = Path(r"C:\Users\20476\Pictures\微信图片电脑壁纸.jpg")
WORK = ROOT / "data" / "icon-build"

# 裁切参数：700×700，从 (370, 0) 开始——人物面部大致在正中
CROP = "crop=700:700:370:0"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def png(size: int) -> Path:
    """用 ffmpeg 生成指定边长的 PNG（正方形，保持比例）。"""
    out = WORK / f"{size}.png"
    if out.exists():
        return out
    vf = f"{CROP},scale={size}:{size}:flags=lanczos"
    r = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(SRC), "-vf", vf, "-frames:v", "1", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        raise SystemExit(f"ffmpeg 失败 ({size}px): {r.stderr[-400:]}")
    return out


def build_ico(dst: Path) -> int:
    """把多尺寸 PNG 打包成 ICO。返回写入的尺寸数。"""
    blobs = []
    for s in ICO_SIZES:
        p = png(s)
        blobs.append((s, p.read_bytes()))

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    data = b""
    for s, blob in blobs:
        # 256 在 ICO 目录里记作 0
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        data += blob
        offset += len(blob)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(header + entries + data)
    return len(blobs)


def main() -> int:
    if not SRC.exists():
        print(f"  ✗ 找不到原图: {SRC}")
        return 1
    WORK.mkdir(parents=True, exist_ok=True)

    print(f"  原图: {SRC.name}  {SRC.stat().st_size:,} B")
    print()

    # 1) Windows 图标（应用 + 控制台共用同一套）
    n = build_ico(ROOT / "app.ico")
    print(f"  ✓ app.ico       {len(ICO_SIZES)} 个尺寸  "
          f"{(ROOT / 'app.ico').stat().st_size:,} B")
    shutil.copy2(ROOT / "app.ico", ROOT / "console.ico")
    print(f"  ✓ console.ico   同步")

    # 2) 网页图标
    web = ROOT / "app" / "web"
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (32, "favicon-32.png")):
        shutil.copy2(png(size), web / name)
        print(f"  ✓ web/{name:18} {size}px  {(web / name).stat().st_size:,} B")

    # 3) Android 启动图标
    res = ROOT / "构建产物" / "android" / "res" / "drawable"
    res.mkdir(parents=True, exist_ok=True)
    old_xml = res / "ic_launcher.xml"
    if old_xml.exists():
        old_xml.unlink()
        print("  · 已删除旧的矢量 ic_launcher.xml")
    for size, name in ((192, "ic_launcher.png"), (512, "ic_launcher_512.png")):
        shutil.copy2(png(size), res / name)
        print(f"  ✓ res/drawable/{name:20} {size}px")

    print()
    print("  完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
