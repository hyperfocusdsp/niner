# DJ Filter + METAL + DICE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a DJ-style bipolar master filter with pre/post routing, an FM METAL knob on the TOP click voice, and a DICE randomize button with per-section lock toggles.

**Architecture:** Three independent features wired into the existing slammer signal chain. DJ Filter is a new DSP module inserted into the `plugin.rs` per-sample loop at one of two positions (pre- or post-comp). METAL adds ~10 lines of FM modulation inside `KickVoice::tick()`. DICE is a GUI-thread function that sets randomized normalized values via `ParamSetter`. All three share the same params/snapshot/preset plumbing.

**Tech Stack:** Rust, nih-plug, nih_plug_egui (egui), `rand` crate for DICE randomization.

---

## File Structure

**New files:**
- `src/dsp/dj_filter.rs` — `DjFilter` struct: stereo 2-pole SVF with bipolar cutoff mapping, process_sample, reset
- `src/ui/randomize.rs` — `pub fn randomize(setter, params, locked)`: sets random normalized values for unlocked sections

**Modified files:**
- `src/lib.rs` — register `dj_filter` in `mod dsp {}`, `randomize` in `mod ui` (via `src/ui/mod.rs`)
- `src/ui/mod.rs` — add `pub mod randomize;`
- `src/params.rs` — add 4 new params (`dj_filter_pos`, `dj_filter_res`, `dj_filter_pre`, `top_metal`), update `ParamSnapshot` + `capture` + `apply` + `collect_kick_params`
- `src/dsp/engine.rs` — add `metal_phase` to `KickVoice`, FM logic in `tick()`, `top_metal` to `KickParams`
- `src/plugin.rs` — instantiate `DjFilter`, call it at PRE or POST position in per-sample loop, reset in `Plugin::reset()`
- `src/export/render.rs` — add `DjFilter` to offline render chain + `dj_filter_pos/res/pre` to `MasterChainSnapshot`
- `src/ui/panels.rs` — METAL knob in TOP row, FILTER cluster + DICE button + lock LEDs in bottom-right
- `src/ui/editor.rs` — lock state `AtomicU8`, DICE click handler, pass lock state to panels
- `src/presets.rs` — add new fields with defaults to factory presets
- `Cargo.toml` — add `rand = "0.8"`

---

### Task 1: Create DjFilter DSP module

**Files:**
- Create: `src/dsp/dj_filter.rs`
- Modify: `src/lib.rs`

- [ ] **Step 1: Write the tests**

Add to the bottom of `src/dsp/dj_filter.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bypass_when_centered() {
        let mut f = DjFilter::new();
        f.set_sample_rate(48000.0);
        // Feed a signal through with pos=0 (center = off)
        let mut sum_diff = 0.0f32;
        for i in 0..512 {
            let input = (i as f32 * 0.1).sin();
            let (ol, or) = f.process_sample(input, input, 0.0, 0.0);
            sum_diff += (ol - input).abs() + (or - input).abs();
        }
        assert!(sum_diff < 1e-6, "center position must be bit-identical bypass, diff={sum_diff}");
    }

    fn fundamental_power(samples: &[f32], sr: f32, bin_freq: f32) -> f32 {
        let w = std::f32::consts::TAU * bin_freq / sr;
        let (mut re, mut im) = (0.0f32, 0.0f32);
        for (i, &x) in samples.iter().enumerate() {
            let p = w * i as f32;
            re += x * p.cos();
            im += x * p.sin();
        }
        re * re + im * im
    }

    #[test]
    fn hp_attenuates_low_frequencies() {
        let sr = 48000.0;
        let mut f = DjFilter::new();
        f.set_sample_rate(sr);
        // Feed 80 Hz sine through HP at full left (pos = -1.0, cutoff ~800 Hz)
        let freq = 80.0;
        let n = 4096;
        let mut out = vec![0.0f32; n];
        for i in 0..n {
            let input = (std::f32::consts::TAU * freq * i as f32 / sr).sin();
            let (ol, _) = f.process_sample(input, input, -1.0, 0.0);
            out[i] = ol;
        }
        // Skip first 256 samples (filter settling)
        let power_out = fundamental_power(&out[256..], sr, freq);
        // Reference: no filter
        let mut ref_buf = Vec::new();
        for i in 256..n {
            ref_buf.push((std::f32::consts::TAU * freq * i as f32 / sr).sin());
        }
        let power_ref = fundamental_power(&ref_buf, sr, freq);
        assert!(
            power_out < power_ref * 0.1,
            "HP should attenuate 80 Hz at 800 Hz cutoff: out={power_out} ref={power_ref}"
        );
    }

    #[test]
    fn lp_attenuates_high_frequencies() {
        let sr = 48000.0;
        let mut f = DjFilter::new();
        f.set_sample_rate(sr);
        // Feed 10 kHz sine through LP at full right (pos = 1.0, cutoff ~200 Hz)
        let freq = 10000.0;
        let n = 4096;
        let mut out = vec![0.0f32; n];
        for i in 0..n {
            let input = (std::f32::consts::TAU * freq * i as f32 / sr).sin();
            let (ol, _) = f.process_sample(input, input, 1.0, 0.0);
            out[i] = ol;
        }
        let power_out = fundamental_power(&out[256..], sr, freq);
        let mut ref_buf = Vec::new();
        for i in 256..n {
            ref_buf.push((std::f32::consts::TAU * freq * i as f32 / sr).sin());
        }
        let power_ref = fundamental_power(&ref_buf, sr, freq);
        assert!(
            power_out < power_ref * 0.1,
            "LP should attenuate 10 kHz at 200 Hz cutoff: out={power_out} ref={power_ref}"
        );
    }

    #[test]
    fn resonance_boosts_cutoff_region() {
        let sr = 48000.0;
        // LP at pos=0.5 with resonance=0 vs resonance=0.9
        // Feed white noise, measure power at cutoff frequency
        let pos = 0.5;
        let n = 8192;

        let mut rng: u32 = 0xCAFEBABE;
        let noise: Vec<f32> = (0..n)
            .map(|_| {
                rng = rng.wrapping_mul(1664525).wrapping_add(1013904223);
                (rng as f32 / u32::MAX as f32) * 2.0 - 1.0
            })
            .collect();

        // Compute cutoff for pos=0.5 (LP): 20000 * (200/20000)^0.5 = 20000 * 0.1 = 2000 Hz
        let cutoff_hz = 20000.0 * (200.0f32 / 20000.0).powf(0.5);

        let mut f_flat = DjFilter::new();
        f_flat.set_sample_rate(sr);
        let mut out_flat = vec![0.0f32; n];
        for i in 0..n {
            let (ol, _) = f_flat.process_sample(noise[i], noise[i], pos, 0.0);
            out_flat[i] = ol;
        }

        let mut f_reso = DjFilter::new();
        f_reso.set_sample_rate(sr);
        let mut out_reso = vec![0.0f32; n];
        for i in 0..n {
            let (ol, _) = f_reso.process_sample(noise[i], noise[i], pos, 0.9);
            out_reso[i] = ol;
        }

        let p_flat = fundamental_power(&out_flat[512..], sr, cutoff_hz);
        let p_reso = fundamental_power(&out_reso[512..], sr, cutoff_hz);
        assert!(
            p_reso > p_flat * 1.5,
            "resonance should boost power near cutoff: flat={p_flat} reso={p_reso}"
        );
    }

    #[test]
    fn reset_zeroes_state() {
        let mut f = DjFilter::new();
        f.set_sample_rate(48000.0);
        // Run some signal through
        for i in 0..256 {
            f.process_sample((i as f32 * 0.1).sin(), (i as f32 * 0.1).sin(), -0.5, 0.5);
        }
        f.reset();
        // After reset, state should be zeroed — bypass should be perfect
        let (ol, _) = f.process_sample(0.5, 0.5, 0.0, 0.0);
        assert!((ol - 0.5).abs() < 1e-6, "reset should zero filter state");
    }
}
```

- [ ] **Step 2: Write the DjFilter implementation**

Create `src/dsp/dj_filter.rs`:

```rust
use std::f32::consts::PI;

pub struct DjFilter {
    lp: [f32; 2],
    bp: [f32; 2],
    sr: f32,
}

impl DjFilter {
    pub fn new() -> Self {
        Self {
            lp: [0.0; 2],
            bp: [0.0; 2],
            sr: 44100.0,
        }
    }

    pub fn set_sample_rate(&mut self, sr: f32) {
        self.sr = sr;
        self.reset();
    }

    pub fn reset(&mut self) {
        self.lp = [0.0; 2];
        self.bp = [0.0; 2];
    }

    pub fn process_sample(&mut self, l: f32, r: f32, cutoff_pos: f32, resonance: f32) -> (f32, f32) {
        if cutoff_pos.abs() < 0.001 {
            return (l, r);
        }

        let t = cutoff_pos.abs();
        let is_hp = cutoff_pos < 0.0;

        let freq = if is_hp {
            20.0 * (800.0f32 / 20.0).powf(t)
        } else {
            20000.0 * (200.0f32 / 20000.0).powf(t)
        };

        let q = 0.707 + resonance * 14.3;
        let damp = 1.0 / q;
        let w = (PI * freq / self.sr).sin() * 2.0;

        let inputs = [l, r];
        let mut outputs = [0.0f32; 2];

        for ch in 0..2 {
            self.lp[ch] += w * self.bp[ch];
            let hp = inputs[ch] - self.lp[ch] - damp * self.bp[ch];
            self.bp[ch] += w * hp;
            outputs[ch] = if is_hp { hp } else { self.lp[ch] };
        }

        (outputs[0], outputs[1])
    }
}
```

- [ ] **Step 3: Register the module in lib.rs**

In `src/lib.rs`, add `pub mod dj_filter;` to the `mod dsp` block, after `pub mod click;`:

```rust
mod dsp {
    pub mod clap;
    pub mod click;
    pub mod dj_filter;
    pub mod drift;
    // ... rest unchanged
}
```

- [ ] **Step 4: Run tests**

Run: `cargo test dj_filter --release`
Expected: all 5 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/dsp/dj_filter.rs src/lib.rs
git commit -m "feat: add DjFilter DSP module (stereo 2-pole SVF, bipolar HP/LP)"
```

---

### Task 2: Add METAL FM to KickVoice

**Files:**
- Modify: `src/dsp/engine.rs`

- [ ] **Step 1: Write the tests**

Add to the `tests` module in `src/dsp/engine.rs`:

```rust
    #[test]
    fn metal_zero_is_identical_to_no_metal() {
        let params = KickParams::default();
        assert!(params.top_metal < 0.001);
        let mut e1 = KickEngine::new(48000.0);
        e1.trigger(&params);
        let mut l1 = vec![0.0f32; 1024];
        let mut r1 = vec![0.0f32; 1024];
        e1.process(&mut l1, &mut r1, &params);

        let mut e2 = KickEngine::new(48000.0);
        e2.trigger(&params);
        let mut l2 = vec![0.0f32; 1024];
        let mut r2 = vec![0.0f32; 1024];
        e2.process(&mut l2, &mut r2, &params);
        assert_eq!(l1, l2, "metal=0 must be deterministic / bit-identical");
    }

    #[test]
    fn metal_changes_top_output() {
        let params_no = KickParams {
            top_metal: 0.0,
            top_gain: 1.0,
            mid_gain: 0.0,
            sub_gain: 0.0,
            ..KickParams::default()
        };
        let params_yes = KickParams {
            top_metal: 0.8,
            ..params_no
        };

        let mut e1 = KickEngine::new(48000.0);
        e1.trigger(&params_no);
        let mut l1 = vec![0.0f32; 512];
        let mut r1 = vec![0.0f32; 512];
        e1.process(&mut l1, &mut r1, &params_no);

        let mut e2 = KickEngine::new(48000.0);
        e2.trigger(&params_yes);
        let mut l2 = vec![0.0f32; 512];
        let mut r2 = vec![0.0f32; 512];
        e2.process(&mut l2, &mut r2, &params_yes);

        let diff: f32 = l1.iter().zip(l2.iter()).map(|(a, b)| (a - b).abs()).sum();
        assert!(diff > 0.1, "metal should change the click character, diff={diff}");
    }
```

- [ ] **Step 2: Add `top_metal` to `KickParams`**

In `src/dsp/engine.rs`, add to the `KickParams` struct in the TOP section:

```rust
    // TOP
    pub top_gain: f32,
    pub top_decay_ms: f32,
    pub top_freq: f32,
    pub top_bw: f32,
    pub top_metal: f32,
```

And in `impl Default for KickParams`:

```rust
            top_bw: 1.5,
            top_metal: 0.0,
```

- [ ] **Step 3: Add `metal_phase` to `KickVoice` and wire FM**

Add `metal_phase: f32` field to `KickVoice`:

```rust
struct KickVoice {
    // ... existing fields ...
    top_amp_env: AmpEnvelope,
    metal_phase: f32,
    fadeout_gain: f32,
    // ...
}
```

In `KickVoice::new()`:

```rust
            top_amp_env: AmpEnvelope::new(sample_rate),
            metal_phase: 0.0,
            fadeout_gain: 1.0,
```

In `KickVoice::trigger()`, reset the phase after the TOP block:

```rust
        self.top_click.trigger();
        self.top_amp_env.trigger(params.top_decay_ms);
        self.metal_phase = 0.0;
```

In `KickVoice::tick()`, replace the TOP block:

```rust
        // TOP: click transient with its own amp envelope for anti-click
        let top_raw = self.top_click.tick();
        let top_amp = self.top_amp_env.tick();
        let top = if params.top_metal > 0.001 {
            let sr = params.top_freq / self.top_click.sample_rate_hint();
            // Actually we don't have sr on KickVoice. We need it.
            // Use the click's internal state or pass sr through params.
            // Simpler: store sr on KickVoice.
            let mod_freq = params.top_freq * 2.4142;
            let mod_out = (self.metal_phase * std::f32::consts::TAU).sin();
            self.metal_phase += mod_freq / self.sample_rate;
            if self.metal_phase >= 1.0 {
                self.metal_phase -= self.metal_phase.floor();
            }
            let fm_depth = params.top_metal * params.top_freq;
            let _ = fm_depth * mod_out; // The FM is applied to the click's pre-baked buffer
            // Since ClickGen uses a pre-baked buffer, FM can't modulate the click
            // oscillator frequency in real-time. Instead, multiply the raw click
            // sample by the modulator to create a ring-mod metallic effect, which
            // produces similar inharmonic sidebands.
            let ring = 1.0 + params.top_metal * mod_out;
            top_raw * ring * params.top_gain * top_amp
        } else {
            top_raw * params.top_gain * top_amp
        };
```

Wait — I need to reconsider. The click is a *pre-baked buffer* (see `click.rs`), not a live oscillator. FM modulation of the frequency isn't possible. But ring modulation produces the same inharmonic sideband spectrum and is actually the more authentic 909 approach (the 909's metallic sound comes from mixing multiple detuned oscillators, which is mathematically equivalent to ring mod). Let me adjust.

Also, `KickVoice` doesn't store `sample_rate`. I need to add it.

Updated approach: add `sample_rate: f32` to `KickVoice`, and use ring modulation (multiply click output by modulator sine) to create the metallic character. This produces sum and difference frequencies (`freq ± mod_freq`), creating the inharmonic spectrum.

In `KickVoice`:

```rust
struct KickVoice {
    // ...existing fields...
    top_amp_env: AmpEnvelope,
    metal_phase: f32,
    sample_rate: f32,
    fadeout_gain: f32,
    // ...
}
```

In `KickVoice::new()`:

```rust
            metal_phase: 0.0,
            sample_rate,
```

In `KickVoice::set_sample_rate()`:

```rust
    fn set_sample_rate(&mut self, sample_rate: f32) {
        *self = Self::new(sample_rate);
    }
```

(Already reconstructs via `Self::new(sample_rate)`, so `sample_rate` field is handled.)

In `KickVoice::tick()`, the TOP block becomes:

```rust
        // TOP: click transient with optional metallic ring modulation
        let top_raw = self.top_click.tick();
        let top_amp = self.top_amp_env.tick();
        let top = if params.top_metal > 0.001 {
            let mod_freq = params.top_freq * 2.4142;
            let mod_out = (self.metal_phase * std::f32::consts::TAU).sin();
            self.metal_phase += mod_freq / self.sample_rate;
            if self.metal_phase >= 1.0 {
                self.metal_phase -= self.metal_phase.floor();
            }
            let ring = 1.0 + params.top_metal * mod_out;
            top_raw * ring * params.top_gain * top_amp
        } else {
            top_raw * params.top_gain * top_amp
        };
```

- [ ] **Step 4: Run tests**

Run: `cargo test --release -- engine`
Expected: all engine tests pass including the two new metal tests

- [ ] **Step 5: Commit**

```bash
git add src/dsp/engine.rs
git commit -m "feat: add METAL ring-mod FM partial to TOP click voice"
```

---

### Task 3: Add new parameters to SlammerParams

**Files:**
- Modify: `src/params.rs`

- [ ] **Step 1: Add param fields to SlammerParams struct**

In `src/params.rs`, after `top_bw`:

```rust
    #[id = "top_metal"]
    pub top_metal: FloatParam,
```

After the `clap_tail_ms` field:

```rust
    // --- DJ Filter (master bus) ---
    #[id = "dj_filt_pos"]
    pub dj_filter_pos: FloatParam,

    #[id = "dj_filt_res"]
    pub dj_filter_res: FloatParam,

    #[id = "dj_filt_pre"]
    pub dj_filter_pre: BoolParam,
```

- [ ] **Step 2: Add param constructors to Default impl**

After the `top_bw` constructor:

```rust
            top_metal: pct_knob("Top Metal", 0.0)
                .with_smoother(SmoothingStyle::Linear(10.0)),
```

After the `clap_tail_ms` constructor:

```rust
            // --- DJ Filter ---
            dj_filter_pos: FloatParam::new(
                "DJ Filter",
                0.0,
                FloatRange::Linear { min: -1.0, max: 1.0 },
            )
            .with_smoother(SmoothingStyle::Linear(5.0))
            .with_value_to_string(Arc::new(|v| {
                if v.abs() < 0.001 {
                    "OFF".into()
                } else {
                    let t = v.abs();
                    let freq = if v < 0.0 {
                        20.0 * (800.0f32 / 20.0).powf(t)
                    } else {
                        20000.0 * (200.0f32 / 20000.0).powf(t)
                    };
                    let prefix = if v < 0.0 { "HP" } else { "LP" };
                    if freq >= 1000.0 {
                        format!("{prefix} {:.1}kHz", freq / 1000.0)
                    } else {
                        format!("{prefix} {freq:.0}Hz")
                    }
                }
            })),

            dj_filter_res: pct_knob("DJ Filter Res", 0.0)
                .with_smoother(SmoothingStyle::Linear(10.0)),

            dj_filter_pre: BoolParam::new("DJ Filter Pre", false),
```

- [ ] **Step 3: Update `collect_kick_params` to include `top_metal`**

Add after the `top_bw` line:

```rust
        top_metal: p.top_metal.value(),
```

- [ ] **Step 4: Update ParamSnapshot**

Add fields to the struct (after `top_bw`):

```rust
    pub top_metal: f32,
```

After `clap_tail_ms`:

```rust
    pub dj_filter_pos: f32,
    pub dj_filter_res: f32,
    pub dj_filter_pre: bool,
```

Add to `capture()` (after `top_bw`):

```rust
            top_metal: p.top_metal.value(),
```

After `clap_tail_ms`:

```rust
            dj_filter_pos: p.dj_filter_pos.value(),
            dj_filter_res: p.dj_filter_res.value(),
            dj_filter_pre: p.dj_filter_pre.value(),
```

Add to `apply()` (after `top_bw`):

```rust
        set!(p.top_metal, self.top_metal);
```

After `clap_tail_ms`:

```rust
        set!(p.dj_filter_pos, self.dj_filter_pos);
        set!(p.dj_filter_res, self.dj_filter_res);
        setter.begin_set_parameter(&p.dj_filter_pre);
        setter.set_parameter(&p.dj_filter_pre, self.dj_filter_pre);
        setter.end_set_parameter(&p.dj_filter_pre);
```

- [ ] **Step 5: Add param default tests**

Add a new test to the existing `clap_param_tests` module (or rename it to something broader):

```rust
    #[test]
    fn dj_filter_and_metal_defaults() {
        let p = SlammerParams::default();
        assert!((p.dj_filter_pos.value()).abs() < 1e-4);
        assert!((p.dj_filter_res.value()).abs() < 1e-4);
        assert!(!p.dj_filter_pre.value());
        assert!((p.top_metal.value()).abs() < 1e-4);
    }

    #[test]
    fn param_snapshot_roundtrip_dj_filter() {
        let snap = ParamSnapshot {
            dj_filter_pos: -0.6,
            dj_filter_res: 0.4,
            dj_filter_pre: true,
            top_metal: 0.5,
            ..ParamSnapshot::default()
        };
        let json = serde_json::to_string(&snap).unwrap();
        let back: ParamSnapshot = serde_json::from_str(&json).unwrap();
        assert_eq!(back, snap);
    }

    #[test]
    fn old_preset_loads_with_filter_defaults() {
        let json = r#"{ "decay_ms": 120.0 }"#;
        let snap: ParamSnapshot = serde_json::from_str(json).unwrap();
        assert!((snap.dj_filter_pos).abs() < 1e-6);
        assert!(!snap.dj_filter_pre);
        assert!((snap.top_metal).abs() < 1e-6);
    }
```

- [ ] **Step 6: Run tests**

Run: `cargo test --release`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/params.rs
git commit -m "feat: add dj_filter_pos/res/pre + top_metal params and snapshot fields"
```

---

### Task 4: Wire DjFilter into plugin.rs per-sample loop

**Files:**
- Modify: `src/plugin.rs`

- [ ] **Step 1: Add DjFilter field to Slammer struct**

Add the import at the top:

```rust
use crate::dsp::dj_filter::DjFilter;
```

Add the field to the `Slammer` struct:

```rust
    dj_filter: DjFilter,
```

Initialize in `Default`:

```rust
            dj_filter: DjFilter::new(),
```

- [ ] **Step 2: Set sample rate in `initialize()` and reset in `reset()`**

In `initialize()`, after `self.master_bus.prepare(self.sample_rate);`:

```rust
        self.dj_filter.set_sample_rate(self.sample_rate);
```

In `reset()`, after `self.master_bus.prepare(self.sample_rate);`:

```rust
        self.dj_filter.reset();
```

- [ ] **Step 3: Add filter processing to the per-sample loop**

In the `process()` method, inside the per-sample loop, pull the smoothed filter params alongside the existing smoothed params:

After `let master_gain = self.params.master_volume.smoothed.next();`:

```rust
                let filt_pos = self.params.dj_filter_pos.smoothed.next();
                let filt_res = self.params.dj_filter_res.smoothed.next();
                let filt_pre = self.params.dj_filter_pre.value();
```

Insert the PRE filter call. After the bypass check `let (cl, cr) = if amount > 0.0001 ...` but **before** the `master_bus.process_sample` call, add:

Actually, looking at the current code structure, the bypass check wraps the entire comp call. The PRE filter needs to go before the comp. Let me restructure carefully.

Current flow in the per-sample loop:
1. Pull smoothed params
2. Compute threshold/ratio from amount
3. `self.master_bus.set_times(...)`
4. Bypass check → `master_bus.process_sample` → `(cl, cr)`
5. Tube warmth → `(wl, wr)`
6. Master volume → `(ol, or_)`

New flow:
1. Pull smoothed params (+ filter params)
2. PRE filter (if `filt_pre && filt_pos.abs() > 0.001`)
3. Comp bypass check → `master_bus.process_sample` → `(cl, cr)`
4. Tube warmth → `(wl, wr)`
5. POST filter (if `!filt_pre && filt_pos.abs() > 0.001`)
6. Master volume → `(ol, or_)`

Replace the section from the bypass check through master volume with:

```rust
                // DJ Filter PRE: before comp
                let (pre_l, pre_r) = if filt_pre {
                    self.dj_filter.process_sample(*l, *r, filt_pos, filt_res)
                } else {
                    (*l, *r)
                };

                let (cl, cr) = if amount > 0.0001 || drive > 0.001 || limiter_on {
                    self.master_bus.process_sample(
                        pre_l,
                        pre_r,
                        threshold_db,
                        ratio,
                        knee_db,
                        drive,
                        limiter_on,
                    )
                } else {
                    (pre_l, pre_r)
                };

                const UNITY_TO_PLUS_6DB: f32 = 1.995_262_3 - 1.0;
                let warmth_amount =
                    ((master_gain - 1.0) / UNITY_TO_PLUS_6DB).clamp(0.0, 1.0);
                let (wl, wr) = self.tube_warmth.process_sample(cl, cr, warmth_amount);

                // DJ Filter POST: after warmth, before master volume
                let (fl, fr) = if !filt_pre {
                    self.dj_filter.process_sample(wl, wr, filt_pos, filt_res)
                } else {
                    (wl, wr)
                };

                let ol = fl * master_gain;
                let or_ = fr * master_gain;
```

- [ ] **Step 4: Run tests**

Run: `cargo test --release`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/plugin.rs
git commit -m "feat: wire DjFilter into plugin per-sample loop with PRE/POST routing"
```

---

### Task 5: Wire DjFilter into offline render

**Files:**
- Modify: `src/export/render.rs`

- [ ] **Step 1: Add filter fields to MasterChainSnapshot**

```rust
pub struct MasterChainSnapshot {
    // ... existing fields ...
    pub comp_knee_db: f32,
    pub dj_filter_pos: f32,
    pub dj_filter_res: f32,
    pub dj_filter_pre: bool,
    pub master_volume: f32,
}
```

- [ ] **Step 2: Add DjFilter to the render function**

In `render_oneshot()`, after `let mut tube_warmth = TubeWarmth::new();`:

```rust
    let mut dj_filter = DjFilter::new();
    dj_filter.set_sample_rate(EXPORT_SR);
```

Add the import at the top of the file:

```rust
use crate::dsp::dj_filter::DjFilter;
```

Extract filter params:

```rust
    let filt_pos = master_chain.dj_filter_pos;
    let filt_res = master_chain.dj_filter_res;
    let filt_pre = master_chain.dj_filter_pre;
```

In the per-sample loop inside the block processing, mirror the plugin.rs chain. Replace the existing per-sample body:

```rust
        for (l, r) in block_l.iter_mut().zip(block_r.iter_mut()) {
            // PRE filter
            let (pre_l, pre_r) = if filt_pre {
                dj_filter.process_sample(*l, *r, filt_pos, filt_res)
            } else {
                (*l, *r)
            };

            let (cl, cr) = if comp_active {
                master_bus.process_sample(
                    pre_l,
                    pre_r,
                    threshold_db,
                    ratio,
                    knee_db,
                    drive,
                    limiter_on,
                )
            } else {
                (pre_l, pre_r)
            };
            let (wl, wr) = tube_warmth.process_sample(cl, cr, warmth_amount);

            // POST filter
            let (fl, fr) = if !filt_pre {
                dj_filter.process_sample(wl, wr, filt_pos, filt_res)
            } else {
                (wl, wr)
            };

            let ol = fl * master_gain;
            let or_ = fr * master_gain;
```

- [ ] **Step 3: Update the MasterChainSnapshot construction in export/mod.rs**

Find where `MasterChainSnapshot` is constructed (in `src/export/mod.rs` or wherever `export_one_shot` builds it) and add the new fields:

```rust
            dj_filter_pos: params.dj_filter_pos.value(),
            dj_filter_res: params.dj_filter_res.value(),
            dj_filter_pre: params.dj_filter_pre.value(),
```

- [ ] **Step 4: Run tests**

Run: `cargo test --release`
Expected: all tests pass (render tests included)

- [ ] **Step 5: Commit**

```bash
git add src/export/render.rs src/export/mod.rs
git commit -m "feat: add DjFilter to offline bounce render chain"
```

---

### Task 6: Add METAL knob to TOP row UI

**Files:**
- Modify: `src/ui/panels.rs`

- [ ] **Step 1: Widen TOP knob rect and add METAL knob**

In `draw_sub_top_row()`, change the TOP knob rect width from `KNOB_SPACING * 4.0` to `KNOB_SPACING * 5.0`:

```rust
    let top_knob_rect = egui::Rect::from_min_size(
        egui::pos2(
            panel_rect.left() + CONTENT_LEFT + KNOB_SPACING * 6.0,
            row_knob_y,
        ),
        egui::vec2(KNOB_SPACING * 5.0, KNOB_SIZE + 30.0),
    );
```

Add the METAL knob after the BW knob inside the `ui.horizontal` closure:

```rust
            param_knob(
                ui,
                setter,
                "t_mt",
                "METAL",
                &params.top_metal,
                0.0,
                1.0,
                0.0,
                |v| format!("{:.0}%", v * 100.0),
                KNOB_SIZE,
                theme::SECTION_TOP,
            );
```

- [ ] **Step 2: Run and verify**

Run: `cargo build --release`
Expected: compiles without error

- [ ] **Step 3: Commit**

```bash
git add src/ui/panels.rs
git commit -m "feat: add METAL knob to TOP row"
```

---

### Task 7: Add FILTER cluster UI to bottom-right

**Files:**
- Modify: `src/ui/panels.rs`

- [ ] **Step 1: Add a `draw_filter_cluster` function**

Add after `draw_bounce_button`:

```rust
/// DJ Filter cluster: bipolar FILTER knob + RES knob + PRE/POST LED.
/// Positioned below the CLAP cluster in the bottom-right corner.
fn draw_filter_cluster(
    ui: &mut egui::Ui,
    setter: &ParamSetter,
    params: &SlammerParams,
    panel_rect: egui::Rect,
    top_y: f32,
) {
    let col_x = panel_rect.right() - CONTENT_LEFT - 96.0 + 4.0;
    let small_knob = 18.0f32;

    // "FILTER" label
    ui.painter().text(
        egui::pos2(col_x, top_y),
        egui::Align2::LEFT_TOP,
        "FILTER",
        egui::FontId::new(9.0, egui::FontFamily::Monospace),
        theme::TEXT_DIM,
    );

    // PRE/POST LED toggle — right of the label
    let led_x = col_x + 52.0;
    let led_y = top_y + 2.0;
    let pre_on = params.dj_filter_pre.value();
    let led_label = if pre_on { "PRE" } else { "POST" };
    let led_color = if pre_on {
        theme::RED_WAVEFORM
    } else {
        theme::TEXT_DIM
    };
    let led_rect = egui::Rect::from_min_size(
        egui::pos2(led_x, led_y),
        egui::vec2(32.0, 10.0),
    );
    let led_resp = ui.interact(
        led_rect,
        egui::Id::new("dj_filter_pre_led"),
        egui::Sense::click(),
    );
    if led_resp.clicked() {
        setter.begin_set_parameter(&params.dj_filter_pre);
        setter.set_parameter(&params.dj_filter_pre, !pre_on);
        setter.end_set_parameter(&params.dj_filter_pre);
    }
    draw_led(ui.painter(), led_x + 2.0, led_y + 4.0, pre_on);
    ui.painter().text(
        egui::pos2(led_x + 12.0, led_y),
        egui::Align2::LEFT_TOP,
        led_label,
        egui::FontId::new(8.0, egui::FontFamily::Monospace),
        led_color,
    );

    // Two small knobs: FILTER (bipolar) and RES
    let knob_y = top_y + 14.0;
    let knob_cell_w = small_knob + 10.0;
    let row_w = knob_cell_w * 2.0 + 6.0;
    let knob_rect = egui::Rect::from_min_size(
        egui::pos2(col_x, knob_y),
        egui::vec2(row_w, small_knob + 22.0),
    );
    ui.allocate_new_ui(egui::UiBuilder::new().max_rect(knob_rect), |ui| {
        ui.spacing_mut().item_spacing.x = 2.0;
        ui.horizontal(|ui| {
            // FILTER knob (bipolar -1..+1)
            let mut filt_val = params.dj_filter_pos.value();
            let resp = knob::knob(
                ui,
                egui::Id::new("dj_filter_pos"),
                &mut filt_val,
                -1.0,
                1.0,
                0.0,
                "FILT",
                |v| {
                    if v.abs() < 0.001 {
                        "OFF".into()
                    } else {
                        let t = v.abs();
                        let freq = if v < 0.0 {
                            20.0 * (800.0f32 / 20.0).powf(t)
                        } else {
                            20000.0 * (200.0f32 / 20000.0).powf(t)
                        };
                        let prefix = if v < 0.0 { "HP" } else { "LP" };
                        if freq >= 1000.0 {
                            format!("{prefix}{:.1}k", freq / 1000.0)
                        } else {
                            format!("{prefix}{freq:.0}")
                        }
                    }
                },
                small_knob,
                theme::KNOB_METAL,
            );
            if resp.changed {
                setter.begin_set_parameter(&params.dj_filter_pos);
                setter.set_parameter(&params.dj_filter_pos, filt_val);
                setter.end_set_parameter(&params.dj_filter_pos);
            }

            param_knob(
                ui,
                setter,
                "dj_filt_res",
                "RES",
                &params.dj_filter_res,
                0.0,
                1.0,
                0.0,
                |v| format!("{:.0}%", v * 100.0),
                small_knob,
                theme::KNOB_METAL,
            );
        });
    });
}
```

- [ ] **Step 2: Call `draw_filter_cluster` from `draw_sat_eq_row`**

At the end of `draw_sat_eq_row`, before the return, add the filter cluster. The BOUNCE button sits at `row_knob_y + (KNOB_SIZE - 22) * 0.5 + 4`. The filter cluster should go below it.

After `let bounce_clicked = draw_bounce_button(ui, panel_rect, row_knob_y);`:

```rust
    // DJ Filter cluster below the BOUNCE button
    let filter_top = row_knob_y + KNOB_SIZE + 2.0;
    draw_filter_cluster(ui, setter, params, panel_rect, filter_top);
```

Note: This will need `draw_sat_eq_row` to accept `setter` and `params` — check if it already does. Looking at the signature: `pub fn draw_sat_eq_row(ui, setter, params, panel_rect, mid_bottom_y)` — yes it does.

- [ ] **Step 3: Run and verify**

Run: `cargo build --release`
Expected: compiles without error

- [ ] **Step 4: Commit**

```bash
git add src/ui/panels.rs
git commit -m "feat: add FILTER cluster UI (bipolar knob + RES + PRE/POST LED)"
```

---

### Task 8: Create randomize module

**Files:**
- Create: `src/ui/randomize.rs`
- Modify: `src/ui/mod.rs`

- [ ] **Step 1: Write the randomize function**

Create `src/ui/randomize.rs`:

```rust
use nih_plug::prelude::*;
use rand::Rng;

use crate::params::SlammerParams;

pub const LOCK_SUB: u8 = 1 << 0;
pub const LOCK_MID: u8 = 1 << 1;
pub const LOCK_TOP: u8 = 1 << 2;
pub const LOCK_SAT: u8 = 1 << 3;
pub const LOCK_EQ: u8 = 1 << 4;
pub const LOCK_COMP: u8 = 1 << 5;

pub fn randomize(setter: &ParamSetter, params: &SlammerParams, locked: u8) {
    let mut rng = rand::thread_rng();

    macro_rules! rand_float {
        ($param:expr) => {
            setter.begin_set_parameter(&$param);
            setter.set_parameter_normalized(&$param, rng.gen::<f32>());
            setter.end_set_parameter(&$param);
        };
    }

    macro_rules! rand_float_biased {
        ($param:expr) => {
            setter.begin_set_parameter(&$param);
            setter.set_parameter_normalized(&$param, rng.gen::<f32>().powf(0.7));
            setter.end_set_parameter(&$param);
        };
    }

    macro_rules! rand_bool {
        ($param:expr, $chance:expr) => {
            setter.begin_set_parameter(&$param);
            setter.set_parameter(&$param, rng.gen::<f32>() < $chance);
            setter.end_set_parameter(&$param);
        };
    }

    if locked & LOCK_SUB == 0 {
        rand_float_biased!(params.sub_gain);
        rand_float!(params.sub_fstart);
        rand_float!(params.sub_fend);
        rand_float!(params.sub_sweep_ms);
        rand_float!(params.sub_sweep_curve);
        rand_float!(params.sub_phase_offset);
        rand_float!(params.decay_ms);
        rand_float!(params.drift_amount);
    }

    if locked & LOCK_MID == 0 {
        rand_float_biased!(params.mid_gain);
        rand_float!(params.mid_fstart);
        rand_float!(params.mid_fend);
        rand_float!(params.mid_sweep_ms);
        rand_float!(params.mid_sweep_curve);
        rand_float!(params.mid_phase_offset);
        rand_float!(params.mid_decay_ms);
        rand_float_biased!(params.mid_tone_gain);
        rand_float_biased!(params.mid_noise_gain);
        rand_float!(params.mid_noise_color);
        rand_bool!(params.clap_on, 0.3);
        rand_float_biased!(params.clap_level);
        rand_float!(params.clap_freq);
        rand_float!(params.clap_tail_ms);
    }

    if locked & LOCK_TOP == 0 {
        rand_float_biased!(params.top_gain);
        rand_float!(params.top_decay_ms);
        rand_float!(params.top_freq);
        rand_float!(params.top_bw);
        rand_float!(params.top_metal);
    }

    if locked & LOCK_SAT == 0 {
        rand_float!(params.sat_mode);
        rand_float_biased!(params.sat_drive);
        rand_float_biased!(params.sat_mix);
    }

    if locked & LOCK_EQ == 0 {
        rand_float!(params.eq_tilt_db);
        rand_float!(params.eq_low_boost_db);
        rand_float!(params.eq_notch_freq);
        rand_float!(params.eq_notch_q);
        rand_float!(params.eq_notch_depth_db);
    }

    if locked & LOCK_COMP == 0 {
        rand_float!(params.comp_amount);
        rand_float!(params.comp_react);
        rand_float_biased!(params.comp_drive);
        rand_bool!(params.comp_limit_on, 0.3);
        rand_float!(params.comp_atk_ms);
        rand_float!(params.comp_rel_ms);
        rand_float!(params.comp_knee_db);
        rand_float!(params.dj_filter_pos);
        rand_float!(params.dj_filter_res);
        rand_bool!(params.dj_filter_pre, 0.3);
    }
}
```

- [ ] **Step 2: Register in ui/mod.rs**

Add to `src/ui/mod.rs`:

```rust
pub mod randomize;
```

- [ ] **Step 3: Add rand dependency to Cargo.toml**

Add under `[dependencies]`:

```toml
rand = "0.8"
```

- [ ] **Step 4: Run tests**

Run: `cargo build --release`
Expected: compiles without error

- [ ] **Step 5: Commit**

```bash
git add src/ui/randomize.rs src/ui/mod.rs Cargo.toml
git commit -m "feat: add randomize module with per-section lock support"
```

---

### Task 9: Add DICE button + lock LEDs to UI

**Files:**
- Modify: `src/ui/panels.rs`
- Modify: `src/ui/editor.rs`

- [ ] **Step 1: Add lock state and DICE UI wiring to editor.rs**

In `src/ui/editor.rs`, add the import:

```rust
use std::sync::atomic::{AtomicU8, Ordering};
```

In the `create()` function, add a lock state before `create_egui_editor`:

```rust
    let dice_locks: Arc<AtomicU8> = Arc::new(AtomicU8::new(0));
```

- [ ] **Step 2: Add DICE button + lock LEDs drawing function to panels.rs**

Add to `src/ui/panels.rs`:

```rust
/// DICE randomize button + 6 section lock LEDs.
/// Returns true on the frame the DICE button is clicked.
pub fn draw_dice_row(
    ui: &mut egui::Ui,
    panel_rect: egui::Rect,
    top_y: f32,
    locks: &std::sync::atomic::AtomicU8,
) -> bool {
    let col_x = panel_rect.right() - CONTENT_LEFT - 96.0 + 4.0;

    // DICE button
    let btn_w = 32.0;
    let btn_h = 16.0;
    let btn_rect = egui::Rect::from_min_size(
        egui::pos2(col_x, top_y),
        egui::vec2(btn_w, btn_h),
    );
    let resp = ui.interact(btn_rect, egui::Id::new("dice_btn"), egui::Sense::click());
    let pressed = resp.is_pointer_button_down_on();
    {
        let painter = ui.painter();
        let top_color = if pressed { theme::BTN_DARK } else { theme::BTN_LIGHT };
        let bot_color = if pressed { theme::BTN_LIGHT } else { theme::BTN_DARK };
        painter.rect_filled(btn_rect, 2.0, bot_color);
        painter.rect_filled(
            egui::Rect::from_min_size(btn_rect.min, egui::vec2(btn_w, btn_h * 0.5)),
            2.0,
            top_color,
        );
        painter.text(
            btn_rect.center(),
            egui::Align2::CENTER_CENTER,
            "DICE",
            egui::FontId::new(8.0, egui::FontFamily::Monospace),
            theme::WHITE,
        );
    }
    let dice_clicked = resp.clicked();

    // 6 lock LEDs: S M T X E C
    let labels = ["S", "M", "T", "X", "E", "C"];
    let current_locks = locks.load(std::sync::atomic::Ordering::Relaxed);
    let led_start_x = col_x + btn_w + 6.0;
    let led_spacing = 10.0;

    for (i, label) in labels.iter().enumerate() {
        let bit = 1u8 << i;
        let is_locked = current_locks & bit != 0;
        let lx = led_start_x + i as f32 * led_spacing;
        let ly = top_y + 2.0;

        let led_rect = egui::Rect::from_min_size(
            egui::pos2(lx - 1.0, ly - 1.0),
            egui::vec2(led_spacing, btn_h),
        );
        let led_resp = ui.interact(
            led_rect,
            egui::Id::new(("dice_lock", i)),
            egui::Sense::click(),
        );
        if led_resp.clicked() {
            locks.fetch_xor(bit, std::sync::atomic::Ordering::Relaxed);
        }

        // LED dot
        let dot_color = if is_locked {
            theme::RED_WAVEFORM
        } else {
            egui::Color32::from_rgb(0x33, 0x22, 0x22)
        };
        ui.painter().circle_filled(
            egui::pos2(lx + 3.0, ly + 2.0),
            2.5,
            dot_color,
        );

        // Letter label below the dot
        ui.painter().text(
            egui::pos2(lx + 3.0, ly + 7.0),
            egui::Align2::CENTER_TOP,
            *label,
            egui::FontId::new(6.0, egui::FontFamily::Monospace),
            if is_locked { theme::WHITE } else { theme::TEXT_DIM },
        );
    }

    dice_clicked
}
```

- [ ] **Step 3: Call draw_dice_row and wire DICE click to randomize**

In `src/ui/panels.rs`, update `draw_sat_eq_row` to accept and return the dice row positioning. Actually, it's cleaner to add the DICE row in `draw_sat_eq_row` alongside the filter cluster.

After the filter cluster call in `draw_sat_eq_row`:

```rust
    // DICE button + lock LEDs below the filter cluster
    let dice_top = filter_top + 46.0;
```

But we need the lock state here. Update `SatEqRowResult` to include `dice_clicked`:

```rust
pub struct SatEqRowResult {
    pub next_y: f32,
    pub bounce_clicked: bool,
    pub dice_clicked: bool,
}
```

Actually, a cleaner approach: draw the DICE row in `editor.rs` directly after the SAT/EQ row, since it needs access to the `dice_locks` AtomicU8 which is owned by the editor closure. Let me put both FILTER cluster and DICE row calls in `editor.rs`.

Revised approach: keep `draw_filter_cluster` and `draw_dice_row` as public functions in `panels.rs`, but call them from `editor.rs` where we have access to all the state.

In `editor.rs`, after the `draw_sat_eq_row` block and its BOUNCE handling, add:

```rust
                    // ===== Filter cluster + DICE (bottom-right) =====
                    {
                        let filter_top = sat_eq_result.next_y - KNOB_SIZE - 28.0;
                        panels::draw_filter_cluster(
                            ui, setter, &params, panel_rect, filter_top,
                        );
                        let dice_top = filter_top + 46.0;
                        let dice_clicked = panels::draw_dice_row(
                            ui, panel_rect, dice_top, &dice_locks,
                        );
                        if dice_clicked {
                            let locked = dice_locks.load(std::sync::atomic::Ordering::Relaxed);
                            crate::ui::randomize::randomize(setter, &params, locked);
                        }
                    }
```

Wait — `draw_filter_cluster` is currently called from inside `draw_sat_eq_row`. Let me keep it simpler: move both calls to `editor.rs` and remove the inline call from `draw_sat_eq_row`.

- [ ] **Step 4: Adjust approach — call filter + dice from editor.rs**

Remove the `draw_filter_cluster` call from inside `draw_sat_eq_row` (from Task 7 Step 2). Instead, make `draw_filter_cluster` a `pub fn` and call it from `editor.rs`.

In `editor.rs`, after the BOUNCE handling and before the sequencer row:

```rust
                    // ===== Filter cluster + DICE (bottom-right) =====
                    {
                        let sat_eq_y = sat_eq_result.next_y;
                        let filter_top = sat_eq_y - KNOB_SIZE - 26.0;
                        panels::draw_filter_cluster(
                            ui, setter, &params, panel_rect, filter_top,
                        );
                        let dice_top = filter_top + 46.0;
                        let dice_clicked = panels::draw_dice_row(
                            ui, panel_rect, dice_top, &dice_locks,
                        );
                        if dice_clicked {
                            let locked = dice_locks.load(std::sync::atomic::Ordering::Relaxed);
                            crate::ui::randomize::randomize(setter, &params, locked);
                        }
                    }
```

The exact `filter_top` y-offset will need visual tuning at build-test time to land below the BOUNCE button and above the STEP row. Use the SAT/EQ row's `next_y` as an anchor.

- [ ] **Step 5: Run and verify**

Run: `cargo build --release`
Expected: compiles without error

- [ ] **Step 6: Commit**

```bash
git add src/ui/panels.rs src/ui/editor.rs
git commit -m "feat: add DICE button + section lock LEDs, wire to randomize()"
```

---

### Task 10: Update factory presets

**Files:**
- Modify: `src/presets.rs`

- [ ] **Step 1: Add new fields to factory presets**

All three factory presets already use `..Default::default()` to close their `ParamSnapshot` literals, so the new fields (`top_metal`, `dj_filter_pos`, `dj_filter_res`, `dj_filter_pre`) will automatically default to 0/false. No changes needed to the factory presets themselves — `#[serde(default)]` on ParamSnapshot handles old user presets, and `..Default::default()` handles the factory literals.

Verify this is true by checking that `ParamSnapshot::default()` returns 0.0 for `top_metal` and `dj_filter_pos/res` and `false` for `dj_filter_pre`. The `#[derive(Default)]` on `ParamSnapshot` gives us `f32::default() = 0.0` and `bool::default() = false`, which are the correct "off" values for all four new fields.

- [ ] **Step 2: Run the existing preset tests**

Run: `cargo test --release -- presets`
Expected: all preset tests pass (JSON round-trip, missing fields, etc.)

- [ ] **Step 3: Commit**

No commit needed — no file changes. The existing `..Default::default()` pattern handles this automatically.

---

### Task 11: Build, test, install

**Files:** None (build/deploy only)

- [ ] **Step 1: Full test suite**

Run: `cargo test --release`
Expected: all tests pass

- [ ] **Step 2: Clippy**

Run: `cargo clippy --release -- -D warnings`
Expected: no warnings

- [ ] **Step 3: Bundle**

Run: `cargo xtask bundle slammer --release`
Expected: CLAP and VST3 bundles created

- [ ] **Step 4: Install**

```bash
rm -rf ~/.vst3/slammer.vst3
cp -r target/bundled/slammer.vst3 ~/.vst3/
cp -f target/bundled/slammer.clap ~/.clap/
cargo build --release --bin slammer-standalone
```

- [ ] **Step 5: Commit any fixes**

If clippy or tests required changes, commit them:

```bash
git add -A
git commit -m "fix: address clippy/test issues from DJ Filter + METAL + DICE"
```

---

### Task 12: Update project memory

**Files:**
- Modify: `~/.claude/projects/-home-natalia-repos-slammer/memory/project_slammer_state.md`
- Modify: `~/Documents/Obsidian/vault13/Slammer Bugs & Features.md`

- [ ] **Step 1: Update project_slammer_state.md**

Add to "Last shipped" section:
- DJ Filter: bipolar master HP/LP SVF (12 dB/oct) with RES + PRE/POST toggle. `src/dsp/dj_filter.rs`. Wired into `plugin.rs` per-sample loop and `export/render.rs`. PRE = before comp, POST = after warmth. Params: `dj_filter_pos` (-1..1, center=off), `dj_filter_res` (0..1), `dj_filter_pre` (bool).
- METAL: ring-mod FM partial on TOP click voice. Modulator at `freq * 2.4142` (909 hat ratio). Added `metal_phase` to `KickVoice`, `top_metal` to `KickParams`. UI: 5th knob in TOP row.
- DICE: randomize button with 6 section locks (S/M/T/X/E/C). `src/ui/randomize.rs`. Lock state = `AtomicU8` in editor closure (UI-only, not persisted). Always excludes master_volume, sequencer, BPM.

Update signal chain diagram to include DJ Filter PRE/POST slots.

Update UI layout to reflect new widgets (METAL in TOP row, FILTER + DICE in bottom-right).

Mark "Master HP/LP filter" as done in the planned-next list.

- [ ] **Step 2: Update Obsidian vault note**

Mark the HP/LP filter feature request as done in `Slammer Bugs & Features.md`.

- [ ] **Step 3: Run memory-index**

```bash
memory-index
```

---

## Verification (standalone + Renoise — no Bitwig)

1. Launch `slammer-launch`. System audio stays alive (PW stability fix).
2. METAL knob at 0: trigger kick, output unchanged from before.
3. METAL at 50–100%: audible metallic ring on the click transient. Sweep FREQ and hear the metallic character track.
4. DJ FILTER center: trigger kick, output unchanged (bypass).
5. Sweep FILTER left (HP): low end thins, high end stays.
6. Sweep FILTER right (LP): high end rolls off.
7. RES at 80%: resonant peak near cutoff.
8. Toggle PRE/POST: PRE makes the comp react to the filtered signal; POST is a clean cut after compression. Audible difference with comp engaged.
9. DICE with all unlocked: click, get a random sound. Repeat — different each time.
10. Lock S (SUB): DICE again — sub body stays, everything else changes.
11. Lock all sections: DICE does nothing.
12. Load a pre-update preset (saved before these features): loads fine with filter off, metal 0, no crash.
13. Save a preset with filter + metal engaged, reload — values restore correctly.
14. BOUNCE with filter engaged: exported file reflects the filter.
15. Open in Renoise, load as VST3. Automate `dj_filter_pos`. Save project, reload — filter position restores.
