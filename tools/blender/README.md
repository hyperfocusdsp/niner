# Niner — Chassis Bake Pipeline

Renders the Niner plugin's "dark body" (the static chassis surrounding the
procedural knobs and displays) as a photorealistic PNG, baked offline in
Blender / Cycles. The runtime plugin loads the PNG via `include_bytes!()`
and paints it across `panel_rect` instead of drawing the chrome procedurally.

## Why bake?

- **Photorealism that egui can't do procedurally:** soft area-light shadows,
  beveled edge highlights, real screw geometry, micro-roughness variation.
- **Marketing-render parity:** the same Cycles render that ships in the
  binary is what hyperfocusdsp.com hero shots use. No Photoshop layer.
- **Zero runtime cost:** one texture upload at editor startup, then a single
  `painter.image()` call per frame in place of ~15 `rect_filled` /
  `circle_filled` calls.

## Visual reference

`refs/reference_hammertone_finish.png` — user-provided 2026-04-28. Coarse
stipple grain (real micro-displacement, not just shader noise), slight
warm/bronze tint, broad soft specular highlights from an oblique key
light, edges dimmer from light falloff. Iter 3 material targets this look.

## What's baked vs. what stays procedural

**Baked into `assets/chassis.png`:**

- Front plate (matte powder-coated steel)
- Top/bottom edge bands
- Rack ears + 16 vent slots
- 4 corner Phillips screws
- OUTPUT + COMP display bezel insets
- Section-row groove dividers

**Stays procedural** (drawn in Rust on top of the bake):

- Knobs (locked direction — see `feedback_niner_blender_knob_experiment_reverted.md`)
- Display lit content (waveform, spectrum, GR meter, scan-lines, glow, 7-seg)
- Text labels, preset bar, sequencer cells
- LED + TEST button (animated)
- Logos (existing PNG overlays)

## Render

```bash
./render_chassis.sh                                  # production: 1360×888, 256 samples → assets/chassis.png
./render_chassis.sh presets/chassis_marketing.json   # hero: 3400×2220, 1024 samples → output/chassis_marketing/
```

The production preset auto-copies the result to `~/repos/niner/assets/chassis.png`
and re-encodes it with max PNG compression (saves ~24% over Cycles' default
writer; pixel-identical). `cargo build --release` then picks it up via
`include_bytes!()`.

The marketing preset stays in `output/chassis_marketing/` — does **not**
overwrite the runtime asset. Use it for hyperfocusdsp.com hero shots and
IG content. Render time on CPU is 1–2 hours at this resolution + sample
count; on a CUDA GPU it drops to 5–15 min.

## Drift detection

`cargo test --test chassis_layout_check` asserts that `presets/chassis.json`
matches the canonical Rust constants in `src/ui/panels.rs`. Any drift
between the JSON and Rust will fail the test. **Edit both sides whenever
the canvas size, screw positions, rack ear width, bezel rects, vent
slots, groove y-positions, or edge-band height changes.**

## Fast iteration

For layout-alignment work (where 64 samples is overkill), drop the
sample count via the script's `--samples` flag:

```bash
blender --background --python scripts/render_chassis.py -- \
    --preset presets/chassis.json --samples 16
```

A 16-sample render at 1360×888 completes in <30 s on CPU; quality renders
take 5–8 min CPU / <60 s on a CUDA GPU.

## Coordinate system

1 Blender unit = 1 logical Niner pixel. The canvas (680 × 444) maps to
world rect `(-340..+340, -222..+222)` with the camera centred at
`(0, 0, 1000)` orthographic, looking down -Z. Geometry positions in
`presets/chassis.json` are listed in **logical pixel coordinates** for
direct comparison with `src/ui/panels.rs` constants.

## Sync with Rust

`presets/chassis.json` mirrors the constants in `src/ui/panels.rs`. The
unit test `tests/chassis_layout_check.rs` enforces this — if the JSON
drifts from the Rust source, `cargo test` fails. **Edit the JSON whenever
you change the canvas size, screw positions, rack ear width, or bezel
geometry in the Rust code.**

## Troubleshooting

- **1-px halo at canvas edges:** `view_transform` must be `"Standard"` —
  Filmic/AgX crush deep blacks and break the edge-fallback match.
- **OPTIX denoiser error:** the script auto-falls-back to
  `OPENIMAGEDENOISE` (CPU). No action needed.
- **`Coat Weight` not found:** the script handles both Blender 4.x
  (`Coat Weight`) and older 3.x (`Clearcoat`) input names.
