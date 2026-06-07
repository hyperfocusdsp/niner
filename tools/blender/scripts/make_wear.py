#!/usr/bin/env python3
"""Generate a subtle, lighting-neutral knob WEAR overlay.

Layered so it reads at real GUI size (~40 px) without looking dirty:
  - organic low-frequency PATINA (uneven light/dark aging) — survives downscale
  - micro SCRATCHES (light hairlines + a few dark) — concentrated outward
  - a couple ARC scratches (rotational scuffing)
  - faint EDGE WEAR ring (handled rim)
  - low neutral GRAIN (matte micro-texture up close)

All neutral light/dark on a transparent circular disk (110/128 of the frame,
matching knob_cap.png). The runtime overlays this on each tinted cap rotated +
scaled + maybe mirrored by a per-knob hash, so one texture makes every knob
slightly unique without disturbing the baked lighting.

Output: assets/knob_wear.png (1024² RGBA).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent.parent.parent / "assets"
PREV = Path(__file__).resolve().parent.parent / "output" / "knob_cap"
OUT = ASSETS / "knob_wear.png"
S = 1024
SS = 2
W = S * SS
DISK = W / 2 * (110.0 / 128.0)
CX = CY = W / 2.0
rng = np.random.default_rng(7)
TAU = math.tau


def rand_pt(frac_lo=0.0, frac_hi=0.98):
    # bias outward (wear concentrates toward the grip/rim)
    r = DISK * math.sqrt(rng.uniform(frac_lo ** 2, frac_hi ** 2))
    a = rng.random() * TAU
    return CX + r * math.cos(a), CY + r * math.sin(a)


# --- scratches + arcs (PIL, supersampled) -----------------------------------
marks = Image.new("RGBA", (W, W), (0, 0, 0, 0))
d = ImageDraw.Draw(marks)
for _ in range(150):                                    # light hairlines
    x0, y0 = rand_pt(0.25, 1.0)
    ln = rng.uniform(10, 95) * SS
    ang = rng.random() * TAU
    x1, y1 = x0 + ln * math.cos(ang), y0 + ln * math.sin(ang)
    v = int(rng.uniform(210, 252))
    al = int(rng.uniform(7, 20))
    d.line([(x0, y0), (x1, y1)], fill=(v, v, v, al), width=1)
for _ in range(34):                                     # dark grime hairlines
    x0, y0 = rand_pt(0.2, 1.0)
    ln = rng.uniform(6, 55) * SS
    ang = rng.random() * TAU
    x1, y1 = x0 + ln * math.cos(ang), y0 + ln * math.sin(ang)
    v = int(rng.uniform(35, 85))
    al = int(rng.uniform(8, 18))
    d.line([(x0, y0), (x1, y1)], fill=(v, v, v, al), width=1)
for _ in range(6):                                      # arc scuffs (rotational)
    rad = rng.uniform(0.45, 0.95) * DISK
    a0 = rng.random() * TAU
    sweep = rng.uniform(0.2, 1.1)
    bb = [CX - rad, CY - rad, CX + rad, CY + rad]
    v = int(rng.uniform(200, 245))
    d.arc(bb, math.degrees(a0), math.degrees(a0 + sweep), fill=(v, v, v, int(rng.uniform(10, 22))), width=1)
marks = marks.filter(ImageFilter.GaussianBlur(0.4 * SS)).resize((S, S), Image.LANCZOS)

# --- numpy layers: patina, edge wear, grain ---------------------------------
yy, xx = np.mgrid[0:S, 0:S]
disk_s = S / 2 * (110.0 / 128.0)
rr = np.sqrt((xx - S / 2) ** 2 + (yy - S / 2) ** 2) / disk_s
inside = rr <= 1.0

rgba = np.zeros((S, S, 4), np.float32)


def over(v, a):
    """Alpha-over a neutral layer (value v 0..255, alpha a 0..255) onto rgba."""
    a = np.clip(a, 0, 255)
    src_a = a / 255.0
    dst_a = rgba[..., 3] / 255.0
    out_a = src_a + dst_a * (1 - src_a)
    safe = out_a > 1e-6
    for c in range(3):
        rgba[..., c] = np.where(
            safe,
            (v * src_a + rgba[..., c] * dst_a * (1 - src_a)) / np.where(safe, out_a, 1),
            rgba[..., c],
        )
    rgba[..., 3] = out_a * 255.0


# patina: FINER, subtler unevenness — two higher-frequency octaves, low alpha
low = rng.standard_normal((44, 44))
low = np.asarray(Image.fromarray((((low - low.min()) / np.ptp(low)) * 255).astype(np.uint8))
                 .resize((S, S), Image.BICUBIC)).astype(np.float32) / 255.0
dev = low - 0.5
over(np.where(dev > 0, 232.0, 78.0), np.abs(dev) * 22.0 * inside)
# finer octave for micro-mottling
mid = rng.standard_normal((110, 110))
mid = np.asarray(Image.fromarray((((mid - mid.min()) / np.ptp(mid)) * 255).astype(np.uint8))
                 .resize((S, S), Image.BICUBIC)).astype(np.float32) / 255.0
mdev = mid - 0.5
over(np.where(mdev > 0, 230.0, 80.0), np.abs(mdev) * 13.0 * inside)

# scratches/arcs from PIL
m = np.asarray(marks).astype(np.float32)
over(np.where(m[..., 3:4].squeeze(-1) > 0, (m[..., 0]), 0.0), m[..., 3])

# faint lighter edge wear (handled rim)
edge = np.exp(-((rr - 0.93) ** 2) / (2 * 0.045 ** 2)) * inside
over(238.0, edge * 16.0)

# subtle neutral grain
g = rng.standard_normal((S, S))
over(np.where(g > 0, 225.0, 95.0), np.clip(np.abs(g) * 3.2, 0, 9) * inside)

# edge fade so nothing hits the rim hard
fade = np.clip((1.0 - rr) / 0.05, 0.0, 1.0)
rgba[..., 3] *= fade

out = Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA")
out.save(OUT)
print(f"wrote {OUT} ({S}x{S}) mean_alpha={rgba[...,3].mean():.2f} max_alpha={rgba[...,3].max():.0f}")

# preview over a mid-tone disk
PREV.mkdir(parents=True, exist_ok=True)
prev = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(prev).ellipse(
    [S / 2 - disk_s, S / 2 - disk_s, S / 2 + disk_s, S / 2 + disk_s], fill=(120, 130, 145, 255)
)
Image.alpha_composite(prev, out).convert("RGB").save(PREV / "wear_preview.png")
print("wrote wear_preview.png")
