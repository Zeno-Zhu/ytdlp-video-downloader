# -*- coding: utf-8 -*-
"""生成扩展图标（纯标准库手写 PNG，无需 Pillow）"""
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")
BG = (255, 0, 51)          # yt-dlp 红
FG = (255, 255, 255)       # 白色播放三角
RADIUS_RATIO = 0.22


def _chunk(tag, data):
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)


def _in_triangle(x, y):
    """归一化三角形：顶点 (0.42,0.30) (0.42,0.70) (0.68,0.50)"""
    def sign(ax, ay, bx, by):
        return (x - bx) * (ay - by) - (ax - bx) * (y - by)
    d1 = sign(0.42, 0.30, 0.42, 0.70)
    d2 = sign(0.42, 0.70, 0.68, 0.50)
    d3 = sign(0.68, 0.50, 0.42, 0.30)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def make_png(size):
    radius = size * RADIUS_RATIO
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            # 圆角矩形判定
            nx = min(px, size - 1 - px)
            ny = min(py, size - 1 - py)
            if nx >= radius or ny >= radius:
                inside = True
            else:
                dx = radius - nx
                dy = radius - ny
                inside = dx * dx + dy * dy <= radius * radius
            if inside and _in_triangle((px + 0.5) / size, (py + 0.5) / size):
                row.append(FG + (255,))
            elif inside:
                row.append(BG + (255,))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)

    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for size in (16, 32, 48, 128):
        path = os.path.join(OUT_DIR, f"icon{size}.png")
        with open(path, "wb") as f:
            f.write(make_png(size))
        print(f"生成 {path}")


if __name__ == "__main__":
    main()
