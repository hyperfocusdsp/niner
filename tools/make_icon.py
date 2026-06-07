#!/usr/bin/env python3
"""Rebuild the Niner app icon: bone-white brand "9" on a dark-graphite
Apple-style squircle, with subtle depth (gradient + vignette + soft glyph
shadow + faint top highlight).

Extracts the existing brand "9" glyph (red) from assets/icon/niner-1024.png
as a soft alpha mask, recolours it bone, and composites it on a freshly
generated squircle. Writes all hicolor sizes + niner.ico.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icon"
# Read the brand "9" from a dedicated source (the original red glyph) so
# re-running this never clobbers its own input (the generated niner-*.png are
# overwritten each run).
SRC = ICON_DIR / "niner-source.png"
BONE = np.array([0xC2, 0xBE, 0xB4], np.float32)  # muted bone (not stark white)
GRAPHITE_TOP = np.array([0x3A, 0x3E, 0x44], np.float32)
GRAPHITE_BOT = np.array([0x22, 0x25, 0x29], np.float32)
SIZES = [16, 32, 48, 64, 128, 256, 512, 1024]
SQUIRCLE_N = 5.0          # superellipse exponent (iOS ≈ 5)
MARGIN = 0.985            # squircle fills ~98.5% of the canvas

src = Image.open(SRC).convert("RGBA")
N = src.size[0]
SS = 2
W = N * SS

# --- extract the brand "9" as a soft alpha mask (red-dominant pixels) -------
a = np.asarray(src).astype(np.float32)
R, G, B = a[..., 0], a[..., 1], a[..., 2]
redness = R - np.maximum(G, B)
mask = np.clip((redness - 16) / 55.0, 0, 1) * np.clip((R - 28) / 45.0, 0, 1)
glyph = Image.fromarray((mask * 255).astype(np.uint8)).resize((W, W), Image.LANCZOS)
ga = np.asarray(glyph).astype(np.float32) / 255.0     # 0..1 glyph coverage

# --- squircle background ----------------------------------------------------
yy, xx = np.mgrid[0:W, 0:W].astype(np.float32)
u = (xx - (W - 1) / 2) / ((W / 2) * MARGIN)
v = (yy - (W - 1) / 2) / ((W / 2) * MARGIN)
f = np.abs(u) ** SQUIRCLE_N + np.abs(v) ** SQUIRCLE_N
sq_alpha = np.clip((1.0 - f) * (W * 0.05), 0.0, 1.0)   # soft AA edge

t = (yy / (W - 1))[..., None]
grad = GRAPHITE_TOP * (1 - t) + GRAPHITE_BOT * t       # vertical gradient
rad = np.sqrt(u ** 2 + v ** 2)
vig = np.clip(1.0 - np.clip(rad - 0.55, 0, None) * 0.30, 0.80, 1.0)[..., None]
bg = grad * vig

# faint top-edge highlight (Apple-ish sheen)
hi = np.clip(1.0 - (v + 1.0) / 0.30, 0.0, 1.0) * (sq_alpha)   # near top edge
bg += (np.array([255, 255, 255], np.float32) - bg) * (hi[..., None] * 0.05)

out = bg.copy()

# --- soft drop shadow under the glyph, offset down --------------------------
sh = np.asarray(
    Image.fromarray((ga * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(W * 0.010))
).astype(np.float32) / 255.0
off = int(W * 0.012)
sh_off = np.zeros_like(sh)
sh_off[off:, :] = sh[:-off, :]
out *= (1.0 - sh_off[..., None] * 0.45)

# --- bone glyph over --------------------------------------------------------
for c in range(3):
    out[..., c] = out[..., c] * (1 - ga) + BONE[c] * ga

rgba = np.dstack([out, sq_alpha * 255.0])
final = Image.fromarray(np.clip(rgba, 0, 255).astype(np.uint8), "RGBA").resize(
    (N, N), Image.LANCZOS
)

for s in SIZES:
    final.resize((s, s), Image.LANCZOS).save(ICON_DIR / f"niner-{s}.png")
final.resize((256, 256), Image.LANCZOS).save(
    ICON_DIR / "niner.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)]
)
print(f"rebuilt icon ({N}px master) -> {ICON_DIR}")
