"""生成「控制台」用的图标：三条推子（滑块），一眼能和应用图标区分开。

复用 make_icon.py 里的 PNG/ICO 编码逻辑，只换绘制内容——
不引第三方库，纯手写 PNG 和 ICO 字节。
"""
from __future__ import annotations

import importlib.util
import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("mk", ROOT / "tools" / "make_icon.py")
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)          # type: ignore[union-attr]

BG_TOP = (14, 165, 233)      # 天蓝
BG_BOT = (16, 185, 129)      # 青绿
FG = (255, 255, 255)
SS = 4


def rrect(x, y, w, h, r) -> bool:
    if x < 0 or y < 0 or x > w or y > h:
        return False
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def slider(y: float, x0: float, x1: float, knob_x: float,
           thick: float, knob_r: float, gap: float,
           x: float, yy: float) -> bool:
    """一条横向轨道 + 一个圆形滑块。

    滑块和轨道都是白色，如果直接叠在一起会糊成一根横线（看着像汉堡菜单）。
    所以在滑块周围留一圈空隙，把轨道切断，滑块才看得出来。
    """
    d2 = (x - knob_x) ** 2 + (yy - y) ** 2
    if d2 <= knob_r ** 2:
        return True                       # 滑块本体
    if d2 <= (knob_r + gap) ** 2:
        return False                      # 滑块周围的空隙
    return abs(yy - y) <= thick / 2 and x0 <= x <= x1


def console_glyph(x: float, y: float, s: float) -> bool:
    track_x0, track_x1 = s * 0.22, s * 0.78
    thick = s * 0.038
    knob_r = s * 0.072
    gap = s * 0.030
    rows = [
        (s * 0.35, 0.68),
        (s * 0.52, 0.34),
        (s * 0.69, 0.60),
    ]
    for ry, kx in rows:
        if slider(ry, track_x0, track_x1, s * kx, thick, knob_r, gap, x, y):
            return True
    return False


def render(size: int) -> bytes:
    px = bytearray()
    for py in range(size):
        for pxx in range(size):
            bg_hit = fg_hit = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = pxx + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if rrect(x, y, size, size, size * 0.22):
                        bg_hit += 1
                        if console_glyph(x, y, size):
                            fg_hit += 1
            n = SS * SS
            if bg_hit == 0:
                px += bytes((0, 0, 0, 0))
                continue
            a = int(255 * bg_hit / n)
            mix = (fg_hit / bg_hit) if bg_hit else 0
            t = py / max(1, size - 1)
            top = (int(BG_TOP[0] * (1 - t) + BG_BOT[0] * t),
                   int(BG_TOP[1] * (1 - t) + BG_BOT[1] * t),
                   int(BG_TOP[2] * (1 - t) + BG_BOT[2] * t))
            r = int(top[0] * (1 - mix) + FG[0] * mix)
            g = int(top[1] * (1 - mix) + FG[1] * mix)
            b = int(top[2] * (1 - mix) + FG[2] * mix)
            px += bytes((r, g, b, a))
    return bytes(px)


def png_bytes(size: int) -> bytes:
    raw = render(size)
    stride = size * 4
    filtered = bytearray()
    for y in range(size):
        filtered.append(0)
        filtered += raw[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(filtered), 9)) +
            chunk(b"IEND", b""))


def ico_bytes(sizes: list[int]) -> bytes:
    images = [png_bytes(s) for s in sizes]
    n = len(images)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    dirs = b""
    for s, img in zip(sizes, images):
        dirs += struct.pack("<BBBBHHII",
                            0 if s >= 256 else s, 0 if s >= 256 else s,
                            0, 0, 1, 32, len(img), offset)
        offset += len(img)
    return header + dirs + b"".join(images)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "console.ico"
    data = ico_bytes([16, 24, 32, 48, 64, 128, 256])
    out.write_bytes(data)
    print(f"  已生成 {out}  ({len(data):,} B)")
    # 顺便导出 256 的 PNG 方便查看
    png = png_bytes(256)
    (out.parent / "console-icon-preview.png").write_bytes(png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
