"""生成应用图标（.ico），不依赖 Pillow。

为什么自己画：
  这台机器的 Python 环境里没有 Pillow，而图标对"像个软件"很重要——
  exe、桌面快捷方式都要用它。PNG 和 ICO 的格式都不复杂，直接按规范写字节就行：
    · PNG：文件头 + IHDR + IDAT(zlib) + IEND
    · ICO：目录项 + 内嵌 PNG（Vista 之后支持 PNG 直接作为 ICO 内容）

图形：蓝色圆角方块 + 白色耳机（半圆弧 + 两个耳罩）。
用简单的几何判定逐像素着色，再做 4x 超采样抗锯齿。
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BG = (10, 132, 255)      # #0A84FF
FG = (255, 255, 255)
SS = 4                   # 超采样倍数


def rounded_rect(x: float, y: float, w: float, h: float, r: float) -> bool:
    """点是否在圆角矩形内（坐标按 0..1 归一化后乘以实际尺寸传入）"""
    if x < 0 or y < 0 or x > w or y > h:
        return False
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def headphone(x: float, y: float, s: float) -> bool:
    """耳机形状：s 是画布边长，坐标单位与 s 相同"""
    cx, cy = s * 0.5, s * 0.52
    # 头梁：半径 r 的圆弧，只取上半部分，粗细 t
    r = s * 0.235
    t = s * 0.052
    d = math.hypot(x - cx, y - cy)
    if abs(d - r) <= t and y <= cy + s * 0.02:
        return True
    # 左右耳罩：小圆角矩形
    cup_w, cup_h = s * 0.105, s * 0.175
    if rounded_rect(x - (cx - r - cup_w * 0.42), y - (cy - s * 0.01),
                    cup_w, cup_h, cup_w * 0.42):
        return True
    if rounded_rect(x - (cx + r - cup_w * 0.58), y - (cy - s * 0.01),
                    cup_w, cup_h, cup_w * 0.42):
        return True
    return False


def render(size: int) -> bytes:
    """返回 size×size 的 RGBA 像素数据（行优先）"""
    px = bytearray()
    for py in range(size):
        for pxx in range(size):
            # 超采样求覆盖率
            bg_hit = 0
            fg_hit = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = pxx + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if rounded_rect(x, y, size, size, size * 0.22):
                        bg_hit += 1
                        if headphone(x, y, size):
                            fg_hit += 1
            n = SS * SS
            if bg_hit == 0:
                px += bytes((0, 0, 0, 0))
                continue
            a = int(255 * bg_hit / n)
            f = fg_hit / n
            b = bg_hit / n
            # 前景按覆盖率混到背景上
            mix = f / b if b else 0
            r = int(BG[0] * (1 - mix) + FG[0] * mix)
            g = int(BG[1] * (1 - mix) + FG[1] * mix)
            bl = int(BG[2] * (1 - mix) + FG[2] * mix)
            px += bytes((r, g, bl, a))
    return bytes(px)


def png_bytes(size: int) -> bytes:
    raw = render(size)
    # 每行前加 filter 字节 0
    stride = size * 4
    filtered = bytearray()
    for y in range(size):
        filtered.append(0)
        filtered += raw[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", ihdr) +
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
                            0 if s >= 256 else s,
                            0 if s >= 256 else s,
                            0, 0, 1, 32, len(img), offset)
        offset += len(img)
    return header + dirs + b"".join(images)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "app.ico"
    sizes = [16, 24, 32, 48, 64, 128, 256]
    data = ico_bytes(sizes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"  已生成 {out}  ({len(data):,} B, {len(sizes)} 个尺寸)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
