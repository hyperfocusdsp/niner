#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy>=1.26",
#     "scipy>=1.11",
#     "soundfile>=0.12",
#     "librosa>=0.10",
# ]
# ///
"""Analyze a kick-drum sample and emit a Niner preset that approximates it.

Usage:
    uv run 909-fit.py <input.wav> [--name NAME] [--out PATH] [--force]

Defaults:
    --name <stem-of-input>-fit
    --out  ~/.config/niner/presets/<name>.json

The preset can be A/B'd by selecting it from Niner's preset bar after
the next plugin/standalone restart (or immediate, if Niner hot-reloads
the preset directory on focus).

What this fits and what it doesn't:
  - Pitch envelope (fstart, fend, sweep_ms, sweep_curve) — fitted from
    pitch tracking. This is the biggest perceptual lever.
  - Amplitude decay (decay_ms) — exponential fit to the RMS envelope.
  - Click presence (top_gain, top_freq) — from short-time HF energy in
    the first 5-15ms.
  - Saturation hint (sat_mode + sat_drive) — guessed from
    even-vs-odd harmonic ratio in the first 30ms.
  - Mid layer is conservative: stays close to the existing factory `909`
    preset values unless analysis surfaces a clear secondary partial.

What it can't fix (architectural gap, not analysis):
  - Niner uses a sine oscillator. A real 909 uses a sawtooth shaped
    through a soft-clipper. Even a perfect pitch+envelope fit will leave
    a harmonic-structure gap at the upper-mid range.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.optimize import curve_fit
from scipy.signal import hilbert

import librosa


# Niner's pitch-envelope formula:
#   f(t) = fend + (fstart - fend) * (1 - (t/T)^curve)
# Verified against src/dsp/envelope.rs.
def niner_pitch_curve(t: np.ndarray, fstart: float, fend: float,
                        sweep_s: float, curve: float) -> np.ndarray:
    """Niner pitch envelope. t in seconds, sweep_s in seconds."""
    progress = np.clip(t / max(sweep_s, 1e-6), 0.0, 1.0)
    return fend + (fstart - fend) * (1.0 - progress ** curve)


def trim_silence(y: np.ndarray, sr: int, threshold_db: float = -50.0
                 ) -> tuple[np.ndarray, int]:
    """Trim leading silence below `threshold_db` peak. Returns (y_trimmed, start_idx)."""
    abs_y = np.abs(y)
    peak = abs_y.max()
    if peak < 1e-6:
        return y, 0
    threshold = peak * 10 ** (threshold_db / 20.0)
    above = np.where(abs_y > threshold)[0]
    if len(above) == 0:
        return y, 0
    start = above[0]
    return y[start:], start


def fit_pitch_envelope(y: np.ndarray, sr: int) -> dict[str, float]:
    """Track pitch over time and fit Niner's power-law envelope.

    Returns the four pitch-envelope params plus a residual error for
    judging fit quality. Falls back to sensible defaults for portions
    that won't fit cleanly.
    """
    # YIN handles low-frequency monophonic content better than autocorrelation
    # for kick samples. We need to allow fmin down to ~30 Hz for the settled
    # tail of a real 909 kick (~50 Hz fundamental). librosa requires
    # frame_length ≥ 2 * sr / fmin to lock onto the lowest expected period;
    # at sr=44100 and fmin=30 that's 2940 samples, so 2048 throws a warning
    # and YIN silently widens its search. 4096 covers down to fmin ≈ 21.5 Hz
    # cleanly at 44.1 kHz, with negligible cost on a sample-length input.
    f0 = librosa.yin(
        y,
        fmin=30.0,
        fmax=400.0,
        sr=sr,
        frame_length=4096,
        hop_length=128,
    )
    # Time axis aligned to YIN frame centers.
    times = librosa.times_like(f0, sr=sr, hop_length=128)

    # Drop the first ~3ms (initial transient often confuses YIN).
    valid_start = np.searchsorted(times, 0.003)
    f0 = f0[valid_start:]
    times = times[valid_start:] - times[valid_start]

    # Drop NaN / unvoiced frames.
    voiced = ~np.isnan(f0) & (f0 > 30.0) & (f0 < 400.0)
    if voiced.sum() < 4:
        # Fallback: not enough pitched content to fit.
        return {
            "sub_fstart": 65.0, "sub_fend": 50.0,
            "sub_sweep_ms": 80.0, "sub_sweep_curve": 3.0,
            "_fit_residual": float("nan"),
            "_fit_note": "insufficient voiced frames",
        }

    f0_v = f0[voiced]
    t_v = times[voiced]

    # Initial guesses bracketing typical 909 ranges.
    fstart_guess = float(np.percentile(f0_v[:max(3, len(f0_v) // 5)], 90))
    fend_guess = float(np.percentile(f0_v[-max(3, len(f0_v) // 5):], 50))
    sweep_guess = max(0.04, t_v[-1] * 0.3)
    curve_guess = 3.0

    try:
        popt, _ = curve_fit(
            niner_pitch_curve,
            t_v, f0_v,
            p0=[fstart_guess, fend_guess, sweep_guess, curve_guess],
            bounds=(
                [40.0, 25.0, 0.005, 0.5],   # lower
                [220.0, 100.0, 0.500, 8.0],  # upper
            ),
            maxfev=5000,
        )
        residual = float(np.sqrt(np.mean(
            (niner_pitch_curve(t_v, *popt) - f0_v) ** 2)))
    except Exception as e:
        return {
            "sub_fstart": fstart_guess,
            "sub_fend": fend_guess,
            "sub_sweep_ms": sweep_guess * 1000.0,
            "sub_sweep_curve": curve_guess,
            "_fit_residual": float("nan"),
            "_fit_note": f"curve_fit failed: {e}",
        }

    fstart, fend, sweep_s, curve = popt
    return {
        "sub_fstart": float(fstart),
        "sub_fend": float(fend),
        "sub_sweep_ms": float(sweep_s * 1000.0),
        "sub_sweep_curve": float(curve),
        "_fit_residual": residual,
        "_fit_note": "ok",
    }


def fit_amplitude_decay(y: np.ndarray, sr: int) -> dict[str, float]:
    """Fit exponential decay to the RMS envelope. Returns decay_ms (full -60dB)."""
    # Hilbert envelope on a slightly LP'd signal — more stable than raw RMS
    # over short windows for transient material.
    env = np.abs(hilbert(y))
    # Smooth a touch to remove cycle-rate jitter.
    win = max(8, sr // 2000)  # 0.5ms boxcar
    if win > 1:
        env = np.convolve(env, np.ones(win) / win, mode="same")

    peak_idx = int(np.argmax(env))
    tail = env[peak_idx:]
    if len(tail) < 16:
        return {"decay_ms": 250.0, "_amp_note": "tail too short"}

    # Find time to fall by 60dB or end-of-signal, whichever first.
    target = env[peak_idx] * 10 ** (-60.0 / 20.0)
    below = np.where(tail < target)[0]
    if len(below) == 0:
        # Hit end of file before -60dB. Estimate via log-linear fit.
        t = np.arange(len(tail)) / sr
        valid = tail > tail.max() * 1e-3
        if valid.sum() < 4:
            return {"decay_ms": 250.0, "_amp_note": "no decay"}
        log_env = np.log(tail[valid])
        t_v = t[valid]
        slope, _ = np.polyfit(t_v, log_env, 1)  # log(env) = -t/tau + c
        if slope >= 0:
            return {"decay_ms": 250.0, "_amp_note": "non-decaying"}
        tau = -1.0 / slope
        decay_60 = tau * (60.0 / 20.0) * np.log(10.0)
        return {"decay_ms": float(decay_60 * 1000.0), "_amp_note": "extrapolated"}

    decay_samples = below[0]
    return {"decay_ms": float(decay_samples * 1000.0 / sr), "_amp_note": "ok"}


def estimate_click(y: np.ndarray, sr: int) -> dict[str, float]:
    """Detect a sharp HF click in the first 15ms.

    Sets top_gain proportional to HF/total energy ratio in that window.
    """
    n = min(int(sr * 0.015), len(y))
    head = y[:n]
    if len(head) < 32:
        return {"top_gain": 0.0, "top_freq": 3000.0,
                "top_decay_ms": 8.0, "top_bw": 2.0,
                "_click_note": "head too short"}

    # FFT and split into bands.
    spectrum = np.abs(np.fft.rfft(head * np.hanning(len(head))))
    freqs = np.fft.rfftfreq(len(head), 1.0 / sr)

    total_energy = float(np.sum(spectrum ** 2))
    hf_mask = (freqs >= 1000.0) & (freqs <= 8000.0)
    hf_energy = float(np.sum(spectrum[hf_mask] ** 2))
    if total_energy < 1e-12:
        return {"top_gain": 0.0, "top_freq": 3000.0,
                "top_decay_ms": 8.0, "top_bw": 2.0,
                "_click_note": "silent head"}

    hf_ratio = hf_energy / total_energy
    # Map ratio into [0, 0.5]. The factory `909` preset uses 0.0 (muted);
    # most real 909 samples will land between 0.05 and 0.30 here.
    top_gain = float(np.clip(hf_ratio * 1.5, 0.0, 0.5))

    # Centroid of the HF band → click frequency hint.
    if hf_energy > 0:
        top_freq = float(
            np.sum(freqs[hf_mask] * spectrum[hf_mask] ** 2) / hf_energy
        )
    else:
        top_freq = 3000.0

    return {
        "top_gain": top_gain,
        "top_freq": float(np.clip(top_freq, 1000.0, 6000.0)),
        "top_decay_ms": 8.0,
        "top_bw": 2.0,
        "_click_note": f"hf_ratio={hf_ratio:.3f}",
    }


def guess_saturation(y: np.ndarray, sr: int, fund: float
                     ) -> dict[str, float]:
    """Estimate sat_mode + sat_drive from harmonic structure near the peak.

    sat_mode floats:
        0 = Off, 1 = SoftClip, 2 = Diode, 3 = Tape
    Heuristic:
      - 2nd harmonic >> 3rd                  -> Diode (asymmetric, even-rich)
      - 3rd harmonic >> 2nd, balanced odds   -> SoftClip
      - HF roll-off vs broadband             -> Tape
      - Almost no harmonics                  -> Off
    """
    if not (40.0 < fund < 200.0):
        return {"sat_mode": 0.0, "sat_drive": 0.0,
                "sat_mix": 1.0, "_sat_note": "no usable fundamental"}

    # Early-attack window — start ~2 ms in (skip the click transient) and
    # span at least 4 fundamental cycles or 30 ms, whichever is longer.
    # The earlier "peak-amp + 30 ms" anchor landed mid-sweep where the
    # fundamental had already decayed 6-12 dB, suppressing `e1` and
    # inflating every harmonic ratio — the sat detector tripped on every
    # clean sample. Anchoring to the start gives the strongest fundamental
    # energy; sizing by `4 / fund` ensures the FFT bin width resolves the
    # fundamental's band (~55 Hz needs ≥ 73 ms; at 30 ms the bin width
    # would land between 33 Hz and 66 Hz and the fundamental's q=0.15
    # band would catch zero energy).
    skip_n = int(sr * 0.002)
    min_win_ms = max(30.0, 4000.0 / fund)
    win_n = min(int(sr * (min_win_ms / 1000.0)), len(y) - skip_n)
    if win_n < 256:
        return {"sat_mode": 0.0, "sat_drive": 0.0,
                "sat_mix": 1.0, "_sat_note": "early-attack window too short"}

    seg = y[skip_n:skip_n + win_n] * np.hanning(win_n)
    spec = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(win_n, 1.0 / sr)

    def band_energy(center: float, q: float = 0.15) -> float:
        lo = center * (1.0 - q)
        hi = center * (1.0 + q)
        mask = (freqs >= lo) & (freqs <= hi)
        return float(np.sum(spec[mask] ** 2))

    e1 = band_energy(fund)
    e2 = band_energy(fund * 2)
    e3 = band_energy(fund * 3)
    e4 = band_energy(fund * 4)
    e5 = band_energy(fund * 5)

    if e1 < 1e-12:
        return {"sat_mode": 0.0, "sat_drive": 0.0, "sat_mix": 1.0,
                "_sat_note": "no fundamental energy"}

    # Per-harmonic ratio vs fundamental.
    r2 = e2 / e1
    r3 = e3 / e1
    r4 = e4 / e1
    r5 = e5 / e1

    even = r2 + r4
    odd = r3 + r5
    total_harm = even + odd

    # HF rolloff: spectrum slope above 4×fund.
    hf_mask = (freqs > fund * 4) & (freqs < sr / 2 - 100)
    hf_slope_db = -10.0
    if hf_mask.sum() > 8:
        hf_freqs = np.log10(freqs[hf_mask] + 1e-6)
        hf_db = 20.0 * np.log10(spec[hf_mask] + 1e-12)
        hf_slope_db, _ = np.polyfit(hf_freqs, hf_db, 1)

    # Decision:
    if total_harm < 0.05:
        sat_mode = 0.0  # Off
    elif even > odd * 1.5:
        sat_mode = 2.0  # Diode (asymmetric / even-heavy)
    elif hf_slope_db < -25.0:
        sat_mode = 3.0  # Tape (HF roll-off)
    else:
        sat_mode = 1.0  # SoftClip

    # Drive scales with total harmonic content but capped — analysis can't
    # tell the difference between "loud sample with mild saturation" and
    # "quieter sample with heavy saturation" reliably.
    sat_drive = float(np.clip(total_harm * 0.7, 0.0, 0.45))

    return {
        "sat_mode": sat_mode,
        "sat_drive": sat_drive,
        "sat_mix": 0.7,
        "_sat_note": (
            f"r2={r2:.2f} r3={r3:.2f} r4={r4:.2f} hf_slope={hf_slope_db:.1f}dB/dec"
        ),
    }


# Niner's full ParamSnapshot defaults, mirrored from `src/params.rs:650`.
# Anything not produced by analysis stays at these. Conservative.
DEFAULT_PARAMS: dict[str, Any] = {
    "decay_ms": 400.0,
    "master_volume": None,
    "sub_gain": 0.95,
    "sub_fstart": 100.0,
    "sub_fend": 50.0,
    "sub_sweep_ms": 80.0,
    "sub_sweep_curve": 3.0,
    "sub_phase_offset": 90.0,
    "mid_gain": 0.5,
    "mid_fstart": 200.0,
    "mid_fend": 80.0,
    "mid_sweep_ms": 40.0,
    "mid_sweep_curve": 2.0,
    "mid_phase_offset": 90.0,
    "mid_decay_ms": 100.0,
    "mid_tone_gain": 0.8,
    "mid_noise_gain": 0.1,
    "mid_noise_color": 0.4,
    "top_gain": 0.0,
    "top_decay_ms": 8.0,
    "top_freq": 3000.0,
    "top_bw": 2.0,
    "top_metal": 0.0,
    "drift_amount": 0.1,
    "sat_mode": 0.0,
    "sat_drive": 0.0,
    "sat_mix": 1.0,
    "eq_tilt_db": 0.0,
    "eq_low_boost_db": 0.0,
    "eq_notch_freq": 250.0,
    "eq_notch_q": 0.0,
    "eq_notch_depth_db": 0.0,
    "comp_amount": 0.0,
    "comp_react": 0.5,
    "comp_drive": 0.0,
    "comp_limit_on": False,
    "comp_atk_ms": 1.0,
    "comp_rel_ms": 50.0,
    "comp_knee_db": 0.0,
    "clap_on": False,
    "clap_level": 0.0,
    "clap_freq": 1000.0,
    "clap_tail_ms": 50.0,
    "dj_filter_pos": 0.5,
    "dj_filter_res": 0.0,
    "dj_filter_pre": False,
}


def analyze(wav_path: Path) -> dict[str, Any]:
    """Run all extractors on a sample and merge into a parameter dict + diagnostics."""
    y, sr = sf.read(str(wav_path), always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = y.astype(np.float64)

    y, _ = trim_silence(y, sr)
    if len(y) < int(sr * 0.020):
        raise SystemExit(
            f"sample too short after silence-trim ({len(y)/sr*1000:.1f}ms)"
        )

    pitch = fit_pitch_envelope(y, sr)
    amp = fit_amplitude_decay(y, sr)
    click = estimate_click(y, sr)
    sat = guess_saturation(y, sr, fund=pitch.get("sub_fend", 60.0))

    # Merge into a full ParamSnapshot.
    params = dict(DEFAULT_PARAMS)
    params["decay_ms"] = amp["decay_ms"]
    params["sub_fstart"] = pitch["sub_fstart"]
    params["sub_fend"] = pitch["sub_fend"]
    params["sub_sweep_ms"] = pitch["sub_sweep_ms"]
    params["sub_sweep_curve"] = pitch["sub_sweep_curve"]
    params["top_gain"] = click["top_gain"]
    params["top_freq"] = click["top_freq"]
    params["top_decay_ms"] = click["top_decay_ms"]
    params["top_bw"] = click["top_bw"]
    params["sat_mode"] = sat["sat_mode"]
    params["sat_drive"] = sat["sat_drive"]
    params["sat_mix"] = sat["sat_mix"]

    # Mid layer: keep close to the existing factory `909`, but anchor mid_fend
    # near sub_fstart so it doesn't fight the sub.
    params["mid_fend"] = max(params["sub_fstart"], 80.0)
    params["mid_fstart"] = params["mid_fend"] * 2.5

    # Slight master EQ + comp boost matching the factory `909` preset.
    params["eq_low_boost_db"] = 1.5
    params["eq_tilt_db"] = 1.0
    params["drift_amount"] = 0.1

    diagnostics = {
        "input": str(wav_path),
        "sample_rate": sr,
        "duration_ms": float(len(y) * 1000.0 / sr),
        "pitch": pitch,
        "amplitude": amp,
        "click": click,
        "saturation": sat,
    }
    return {"params": params, "diagnostics": diagnostics}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", type=Path, help="path to a kick .wav")
    p.add_argument("--name", help="preset name (default: <stem>-fit)")
    p.add_argument("--out", type=Path,
                   help="output JSON path (default: ~/.config/niner/presets/<name>.json)")
    p.add_argument("--force", action="store_true",
                   help="overwrite output if it exists")
    p.add_argument("--print-only", action="store_true",
                   help="print diagnostics + JSON to stdout, don't write a file")
    args = p.parse_args()

    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 2

    name = args.name or f"{args.input.stem}-fit"
    if args.out is None:
        # Niner reads from XDG_DATA_HOME via `directories::ProjectDirs`
        # (src/util/paths.rs:30 — `niner_preset_dir()`). Earlier versions
        # versions used XDG_CONFIG_HOME; presets there are orphans now.
        data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        preset_dir = Path(data_home) / "niner" / "presets"
        out_path = preset_dir / f"{name}.json"
    else:
        out_path = args.out

    result = analyze(args.input)

    preset = {
        "name": name,
        "version": 1,
        "params": {"name": name, **result["params"]},
    }

    diag = result["diagnostics"]
    print(f"=== 909-fit: {args.input.name} → {name} ===")
    print(f"  duration: {diag['duration_ms']:.1f}ms @ {diag['sample_rate']}Hz")
    print(f"  pitch:    fstart={result['params']['sub_fstart']:.1f}Hz "
          f"fend={result['params']['sub_fend']:.1f}Hz "
          f"sweep={result['params']['sub_sweep_ms']:.1f}ms "
          f"curve={result['params']['sub_sweep_curve']:.2f}  "
          f"[{diag['pitch'].get('_fit_note', 'ok')}]")
    print(f"  amp:      decay={result['params']['decay_ms']:.1f}ms  "
          f"[{diag['amplitude'].get('_amp_note', 'ok')}]")
    print(f"  click:    top_gain={result['params']['top_gain']:.3f} "
          f"top_freq={result['params']['top_freq']:.0f}Hz  "
          f"[{diag['click'].get('_click_note', 'ok')}]")
    sat_names = {0.0: "Off", 1.0: "SoftClip", 2.0: "Diode", 3.0: "Tape"}
    print(f"  sat:      mode={sat_names.get(result['params']['sat_mode'], '?')} "
          f"drive={result['params']['sat_drive']:.3f}  "
          f"[{diag['saturation'].get('_sat_note', 'ok')}]")

    if args.print_only:
        print()
        print(json.dumps(preset, indent=2))
        return 0

    if out_path.exists() and not args.force:
        print(f"\nrefusing to overwrite {out_path} (pass --force to override)",
              file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(preset, f, indent=2)
    print(f"\nwrote {out_path}")
    print(f"open Niner and select preset '{name}' to A/B against the source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
