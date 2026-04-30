# DJ Filter + METAL + DICE Design

## Overview

Three features shipping together: a performance DJ-style master filter, an FM
"METAL" partial on the TOP click voice, and a DICE randomize button with
per-section lock toggles. All three target techno production workflows.

## 1. DJ FILTER

### Signal path

A stereo 2-pole state-variable filter (12 dB/oct) operating in either HP or LP
mode depending on knob position. A PRE/POST toggle selects one of two insertion
points in the `plugin.rs` per-sample chain:

```
voices → sat → EQ → [PRE slot] → comp → drive → limiter → tube warmth → [POST slot] → master vol → out
```

- **PRE** (dj_filter_pre = true): filter sits before the comp/drive/limiter call.
  The filtered signal feeds into the compressor — HP sweeps thin the input and
  the comp reacts to the reduced energy, producing the classic "filter build"
  pump.
- **POST** (dj_filter_pre = false, default): filter sits after tube warmth,
  before master volume. Clean surgical cut on the final signal.

### Bipolar knob mapping

`dj_filter_pos` is a FloatParam ranging -1.0 to +1.0, center = 0.0 (off).

- **Left half (-1.0 to -0.001):** HP mode. Cutoff sweeps exponentially from
  20 Hz (near center) to 800 Hz (full left).
- **Center (-0.001 to +0.001):** Bypassed. Bit-identical passthrough — no
  filter processing, no state update.
- **Right half (+0.001 to +1.0):** LP mode. Cutoff sweeps exponentially from
  20 kHz (near center) to 200 Hz (full right).

The exponential mapping uses `20.0 * (800.0/20.0).powf(t)` for HP and
`20000.0 * (200.0/20000.0).powf(t)` for LP, where `t = abs(pos)` normalized
0..1 within each half.

### Resonance

`dj_filter_res` (0.0..1.0, default 0.0) maps to SVF damping:
- 0.0 → Q = 0.707 (Butterworth, flat passband, no peak)
- 1.0 → Q ≈ 15 (aggressive resonant peak, but clamped below self-oscillation)

Formula: `Q = 0.707 + res * 14.3`, `damp = 1.0 / Q`.

### DSP struct

New file `src/dsp/dj_filter.rs`:

```rust
pub struct DjFilter {
    lp: [f32; 2],   // low-pass state, per channel
    bp: [f32; 2],   // band-pass state, per channel
    sr: f32,
}
```

- `new() -> Self` — zeroed state, sr = 44100.
- `set_sample_rate(sr)` — stores sr, resets state.
- `process_sample(l, r, cutoff_pos, resonance) -> (f32, f32)` — computes SVF
  coefficients from cutoff_pos + resonance each sample (cheap: two `sin` calls
  amortized via the smoother), runs the filter, returns filtered pair. When
  `cutoff_pos.abs() < 0.001`, returns input unchanged without updating state.
- `reset()` — zeroes lp/bp arrays. Called from `Plugin::reset()`.

SVF update (per channel):
```
w = 2.0 * sin(PI * freq / sr)
lp += w * bp
hp = input - lp - damp * bp
bp += w * hp
output = if HP mode { hp } else { lp }
```

### Parameters

| ID | Type | Range | Default | Smoother | Display |
|----|------|-------|---------|----------|---------|
| `dj_filter_pos` | FloatParam | -1.0..1.0 | 0.0 | Linear 5ms | "OFF" at center, else computed cutoff in Hz (e.g. "HP 340Hz", "LP 1.2kHz") |
| `dj_filter_res` | FloatParam | 0.0..1.0 | 0.0 | Linear 10ms | 0–100% |
| `dj_filter_pre` | BoolParam | — | false (POST) | — | PRE/POST |

### UI

Bottom-right corner, below the CLAP cluster. A bipolar knob (visual
center-detent), a small RES knob, and a PRE/POST LED toggle. Section label
"FILTER".

### ParamSnapshot

Add `dj_filter_pos`, `dj_filter_res`, `dj_filter_pre` with `#[serde(default)]`.
Old presets load with filter off (pos=0.0).

## 2. METAL (TOP click FM partial)

### DSP

FM synthesis on the TOP voice's click oscillator. A modulator sine at
`click_freq * 2.4142` (the 909 hi-hat ratio, `1 + sqrt(2)`) frequency-modulates
the click's phase, producing inharmonic metallic sidebands.

In `KickVoice::tick()`, when `params.top_metal > 0.001`:

```rust
// Modulator runs at the inharmonic ratio
let mod_freq = params.top_freq * 2.4142;
let mod_out = (self.metal_phase * TAU).sin();
self.metal_phase += mod_freq / sr;
if self.metal_phase >= 1.0 { self.metal_phase -= 1.0; }

// FM: offset the click frequency by modulator * depth
let fm_depth = params.top_metal * params.top_freq; // modulation index scales with carrier
let click_freq_modulated = params.top_freq + fm_depth * mod_out;
// Use click_freq_modulated instead of params.top_freq for the SVF input
```

The modulator is a free-running sine — it doesn't need its own envelope because
the FM effect is applied to the carrier, which already decays under the click's
amplitude envelope. The metallic character fades naturally as the click dies.

When `top_metal <= 0.001`, the modulator is skipped entirely (bit-identical
bypass to current behavior).

### New fields on KickVoice

- `metal_phase: f32` — modulator oscillator phase, reset to 0.0 on trigger.

No new file. ~10 lines added to `KickVoice::tick()` inside the existing TOP
block.

### Parameters

| ID | Type | Range | Default | Smoother | Display |
|----|------|-------|---------|----------|---------|
| `top_metal` | FloatParam | 0.0..1.0 | 0.0 | Linear 10ms | 0–100% |

### KickParams

Add `pub top_metal: f32`. Default 0.0.

### UI

One knob labeled "METAL" appended after BW in the TOP row. Color:
`theme::SECTION_TOP` (green). TOP row: GAIN / DECAY / FREQ / BW / METAL
(5 knobs).

### ParamSnapshot

Add `top_metal: f32` with `#[serde(default)]`. Old presets load with metal=0.

## 3. DICE (Randomize with Section Locks)

### Logic

A `randomize()` function on the GUI thread that sets every unlocked param to a
random value within its valid range. Called from the DICE button click handler.

```rust
pub fn randomize(setter: &ParamSetter, params: &SlammerParams, locked: u8)
```

`locked` is a 6-bit mask: bits 0–5 = SUB, MID, TOP, SAT, EQ, COMP.

### Section → param mapping

| Bit | Section | Params |
|-----|---------|--------|
| 0 | SUB | sub_gain, sub_fstart, sub_fend, sub_sweep_ms, sub_sweep_curve, sub_phase_offset, decay_ms, drift_amount |
| 1 | MID | mid_gain, mid_fstart, mid_fend, mid_sweep_ms, mid_sweep_curve, mid_phase_offset, mid_decay_ms, mid_tone_gain, mid_noise_gain, mid_noise_color, clap_on, clap_level, clap_freq, clap_tail_ms |
| 2 | TOP | top_gain, top_decay_ms, top_freq, top_bw, top_metal |
| 3 | SAT | sat_mode, sat_drive, sat_mix |
| 4 | EQ | eq_tilt_db, eq_low_boost_db, eq_notch_freq, eq_notch_q, eq_notch_depth_db |
| 5 | COMP | comp_amount, comp_react, comp_drive, comp_limit_on, comp_atk_ms, comp_rel_ms, comp_knee_db, dj_filter_pos, dj_filter_res, dj_filter_pre |

**Always excluded (never randomized):** master_volume, sequencer pattern,
sequencer BPM/running state.

### Randomization strategy

Each param is randomized by generating a random normalized value (0.0..1.0) and
calling `setter.set_parameter_normalized()`. This automatically respects the
param's range, skew, and stepping — frequency params naturally cluster in
musically useful ranges because the normalized-to-plain mapping applies the
param's `FloatRange::Skewed` or `Logarithmic` curve.

Special cases:
- **BoolParams** (clap_on, comp_limit_on, dj_filter_pre): 30% chance true,
  70% false. Keeps the randomized sound from being overwhelmed by effects.
- **sat_mode**: Uniform random across discrete steps (already handled by
  normalized random on a stepped param).
- **Gains** (sub/mid/top_gain, clap_level, sat_drive, sat_mix): bias toward
  moderate values by using `rng.gen::<f32>().powf(0.7)` — slightly favors the
  upper range without hard clamping, avoiding silent voices.

### Lock state

Stored as `Arc<AtomicU8>` in the editor closure, shared across frames. Not
persisted as a plugin param — locks are UI-only state. Initial value: 0 (all
unlocked).

### Dependency

Add `rand = "0.8"` to Cargo.toml.

### New file

`src/ui/randomize.rs` — contains `pub fn randomize(...)`. Registered in the
existing `mod ui { ... }` block in `lib.rs`.

### UI

Bottom-right corner, below or beside the DJ FILTER cluster. A DICE button
(same 3D button style as TEST/BOUNCE) plus six small LED toggles in a tight
horizontal row, labeled **S M T X E C** (SUB, MID, TOP, SAT, EQ, COMP).
Clicking a letter toggles its lock. Locked = lit (red), unlocked = dim.

Layout (below FILTER cluster):
```
[DICE]  S  M  T  X  E  C
```

## Files summary

**New:**
- `src/dsp/dj_filter.rs` — DjFilter struct + process
- `src/ui/randomize.rs` — randomize() function

**Modify:**
- `src/lib.rs` — add `pub mod dj_filter;` to dsp block, `pub mod randomize;` to ui block
- `src/params.rs` — add dj_filter_pos/res/pre + top_metal params; update ParamSnapshot + collect_kick_params
- `src/dsp/engine.rs` — add metal_phase to KickVoice, FM logic in tick(), top_metal to KickParams
- `src/plugin.rs` — instantiate DjFilter, call it in per-sample loop at PRE or POST position, pull smoothed params, reset DjFilter in `Plugin::reset()`
- `src/ui/panels.rs` — METAL knob in TOP row, FILTER cluster + DICE button + lock LEDs in bottom-right
- `src/ui/editor.rs` — lock state AtomicU8, DICE click handler calls randomize()
- `src/presets.rs` — add new fields to factory presets with defaults (all off/zero)
- `Cargo.toml` — add `rand = "0.8"`
