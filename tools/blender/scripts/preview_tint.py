#!/usr/bin/env python3
"""Simulate the runtime knob composite to judge the bake before wiring Rust.

For each section colour: dark plate -> soft shadow (untinted) -> cap
(multiply-tinted) -> bone-white indicator line. Outputs a big row and a
true-GUI-size row (46 px) so we can see how it reads at real scale.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "output" / "knob_preview"
OUT = SRC
PLATE = (0x13, 0x13, 0x13)
BONE = (0xF4, 0xF1, 0xEA)
# (name, rgb) — the 6 theme.rs section colours.
COLORS = [
    ("master", (0x3A, 0x78, 0xC8)),
    ("sub", (0x85, 0x90, 0xA0)),
    ("top", (0xD4, 0x95, 0x26)),
    ("mid", (0x4E, 0x9A, 0x52)),
    ("sat", (0xC5, 0x2E, 0x2E)),
    ("eq", (0x3D, 0x44, 0x4B)),
]


def tint(img: Image.Image, rgb) -> Image.Image:
    r, g, b = img.split()[:3]
    a = img.split()[3]
    solid = Image.new("RGB", img.size, rgb)
    sr, sg, sb = solid.split()
    out = Image.merge("RGB", (
        ImageChops.multiply(r, sr),
        ImageChops.multiply(g, sg),
        ImageChops.multiply(b, sb),
    ))
    out.putalpha(a)
    return out


def compose(cap, shadow, rgb, size):
    cell = Image.new("RGBA", (size, size), (*PLATE, 255))
    cap_s = cap.resize((size, size), Image.LANCZOS)
    sh_s = shadow.resize((size, size), Image.LANCZOS)
    cell.alpha_composite(sh_s)
    cell.alpha_composite(tint(cap_s, rgb))
    # Bone indicator: pointing up (12 o'clock). Knob disk fills 110/128 of
    # half; centre at cell centre, tip near disk edge.
    d = ImageDraw.Draw(cell)
    c = size / 2.0
    disk_r = size / 2.0 * (110.0 / 128.0)
    r_out, r_in = disk_r * 0.92, disk_r * 0.28
    w = max(1.0, disk_r * 0.08)
    ang = -math.pi / 2.0  # up
    dx, dy = math.cos(ang), math.sin(ang)
    px, py = -dy, dx
    pts = [
        (c + dx * r_out + px * w, c + dy * r_out + py * w),
        (c + dx * r_out - px * w, c + dy * r_out - py * w),
        (c + dx * r_in - px * w, c + dy * r_in - py * w),
        (c + dx * r_in + px * w, c + dy * r_in + py * w),
    ]
    d.polygon(pts, fill=(*BONE, 255))
    return cell


def main():
    cap = Image.open(SRC / "knob_cap.png").convert("RGBA")
    shadow = Image.open(SRC / "knob_shadow.png").convert("RGBA")
    for size, tag in ((220, "big"), (46, "gui")):
        gap = max(8, size // 12)
        row = Image.new("RGBA", (len(COLORS) * (size + gap) + gap, size + 2 * gap),
                        (*PLATE, 255))
        for i, (_, rgb) in enumerate(COLORS):
            cell = compose(cap, shadow, rgb, size)
            row.alpha_composite(cell, (gap + i * (size + gap), gap))
        row.convert("RGB").save(OUT / f"preview_tinted_{tag}.png")
        print(f"wrote preview_tinted_{tag}.png  ({row.size[0]}x{row.size[1]})")


if __name__ == "__main__":
    main()
