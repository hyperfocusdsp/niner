#!/usr/bin/env python3
"""Cut the 5 nano-banana knob renders out of the contact sheet.

Input : ~/Pictures/knobs_new.png  (9073x1669, 5 knobs on black, left->right
        blue / amber / green / red / orange)
Output: tools/blender/refs/ai_knobs/knob_<color>.png  (tight RGBA cutouts)

Also prints, per knob, the detected centre/radius and a radial luminance
profile (12 bins, 0..1.1r) so we can match the cap geometry in Blender —
the inner concentric ring and the soft edge bevel show up as dips in the
profile.

Pure reference/art-direction step: the shipped runtime asset is the
neutral Blender bake, not these cutouts.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

SRC = Path.home() / "Pictures" / "knobs_new.png"
OUT = Path(__file__).resolve().parent.parent / "refs" / "ai_knobs"
NAMES = ["blue", "amber", "green", "red", "orange"]
FG_THRESH = 18  # max(R,G,B) above this == foreground (bg is pure black)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC).convert("RGB")
    arr = np.asarray(img)
    h, w, _ = arr.shape
    maxc = arr.max(axis=2)
    fg = maxc > FG_THRESH

    # Label connected blobs; keep the 5 largest, ordered left->right.
    lbl, n = ndimage.label(fg)
    sizes = ndimage.sum(np.ones_like(lbl), lbl, index=range(1, n + 1))
    keep = np.argsort(sizes)[::-1][:5] + 1  # label ids of 5 biggest
    boxes = ndimage.find_objects(lbl)
    blobs = []
    for li in keep:
        sl = boxes[li - 1]
        cx = (sl[1].start + sl[1].stop) / 2.0
        blobs.append((cx, li, sl))
    blobs.sort(key=lambda b: b[0])  # left -> right

    lum = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2])

    for idx, (cx, li, sl) in enumerate(blobs):
        name = NAMES[idx] if idx < len(NAMES) else f"knob{idx}"
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        bw, bh = x1 - x0, y1 - y0
        radius = max(bw, bh) / 2.0
        ccx = (x0 + x1) / 2.0
        ccy = (y0 + y1) / 2.0

        # Alpha = this blob's mask, holes filled, feathered 1px for clean AA.
        mask = ndimage.binary_fill_holes(lbl == li)
        alpha = (mask.astype(np.uint8) * 255)
        rgba = np.dstack([arr, alpha]).astype(np.uint8)
        cut = Image.fromarray(rgba, "RGBA")
        # Feather the alpha edge slightly to kill jaggies on the silhouette.
        a = cut.getchannel("A").filter(ImageFilter.GaussianBlur(1.2))
        cut.putalpha(a)

        margin = int(radius * 0.08)
        crop = cut.crop((max(0, x0 - margin), max(0, y0 - margin),
                         min(w, x1 + margin), min(h, y1 + margin)))
        crop.save(OUT / f"knob_{name}.png")

        # Radial luminance profile inside the blob (12 bins to ~1.05r).
        yy, xx = np.mgrid[y0:y1, x0:x1]
        rr = np.sqrt((xx - ccx) ** 2 + (yy - ccy) ** 2) / radius
        sub_lum = lum[y0:y1, x0:x1]
        sub_msk = mask[y0:y1, x0:x1]
        bins = np.linspace(0.0, 1.05, 13)
        prof = []
        for b in range(12):
            sel = sub_msk & (rr >= bins[b]) & (rr < bins[b + 1])
            prof.append(float(sub_lum[sel].mean()) if sel.any() else 0.0)
        prof_s = " ".join(f"{p:5.0f}" for p in prof)
        print(f"{name:7s} centre=({ccx:6.0f},{ccy:6.0f}) r={radius:5.0f} "
              f"box={bw}x{bh}")
        print(f"        radial L (0->1.05r): {prof_s}")

    print(f"\nWrote {len(blobs)} cutouts to {OUT}")


if __name__ == "__main__":
    main()
