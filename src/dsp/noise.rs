/// White noise generator using a fast LCG (no heap, no rand crate on audio thread).
/// Followed by a 1-pole low-pass filter controlled by `color`:
///   color = 0.0 → dark (heavily filtered, sub rumble)
///   color = 1.0 → white (full spectrum / bright)
pub struct NoiseGen {
    state: u32,
    lp_z: f32,
    sample_rate: f32,
}

/// Fixed LCG seed used at construction time AND re-applied on every
/// `trigger()`. Resetting the RNG state per trigger makes the noise burst
/// bit-identical across hits — otherwise the LCG state keeps advancing as
/// samples are consumed, so each trigger starts at a different point in the
/// pseudo-random sequence. That variation is audible as a "ghost attack" on
/// every retrigger because the noise content drives the filter state
/// transient differently each time.
const NOISE_SEED: u32 = 0x12345678;

impl NoiseGen {
    pub fn new(sample_rate: f32) -> Self {
        Self {
            state: NOISE_SEED,
            lp_z: 0.0,
            sample_rate,
        }
    }

    pub fn trigger(&mut self) {
        self.state = NOISE_SEED;
        self.lp_z = 0.0;
    }

    /// Generate one noise sample filtered by `color` (0=dark, 1=bright/white).
    pub fn tick(&mut self, color: f32) -> f32 {
        // LCG: fast, deterministic, no allocation
        self.state = self.state.wrapping_mul(1664525).wrapping_add(1013904223);
        // Scale to [-1, 1]
        let white = (self.state as f32 / u32::MAX as f32) * 2.0 - 1.0;

        // 1-pole LP: cutoff mapped from color
        // color=0 → cutoff=20Hz (dark/sub rumble), color=1 → cutoff=20kHz (bright/white)
        let cutoff_hz = 20.0 * (2.0f32).powf(color * 10.0);
        let cutoff_hz = cutoff_hz.min(self.sample_rate * 0.49);
        let rc = 1.0 / (std::f32::consts::TAU * cutoff_hz);
        let dt = 1.0 / self.sample_rate;
        let alpha = dt / (rc + dt);

        self.lp_z += alpha * (white - self.lp_z);
        // Flush subnormal floats to prevent CPU spikes and noise artifacts
        if self.lp_z.is_subnormal() {
            self.lp_z = 0.0;
        }
        self.lp_z
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_bounded() {
        let mut ng = NoiseGen::new(44100.0);
        ng.trigger();
        for _ in 0..10000 {
            let s = ng.tick(0.5);
            assert!((-2.0..=2.0).contains(&s), "out of range: {}", s);
        }
    }

    /// At `color=1.0` the LP cutoff is well above audio range, so the
    /// output approximates white noise — energy in a high-frequency band
    /// (e.g. 4–8 kHz) should be comparable to energy in a low-frequency
    /// band (250–500 Hz). At `color=0.0` the cutoff drops to 20 Hz, so
    /// the high band should be sharply attenuated.
    #[test]
    fn dark_attenuates_high_band_relative_to_white() {
        use std::f32::consts::TAU;

        fn band_power(samples: &[f32], sr: f32, freqs: &[f32]) -> f32 {
            let mut total = 0.0;
            for &f in freqs {
                let w = TAU * f / sr;
                let (mut re, mut im) = (0.0f32, 0.0f32);
                for (i, &x) in samples.iter().enumerate() {
                    let p = w * i as f32;
                    re += x * p.cos();
                    im += x * p.sin();
                }
                total += re * re + im * im;
            }
            total
        }

        let sr = 48_000.0;
        let n = 24_000;
        let mut ng_white = NoiseGen::new(sr);
        let mut ng_dark = NoiseGen::new(sr);
        ng_white.trigger();
        ng_dark.trigger();
        let white: Vec<f32> = (0..n).map(|_| ng_white.tick(1.0)).collect();
        let dark: Vec<f32> = (0..n).map(|_| ng_dark.tick(0.0)).collect();

        let high_bins = [4000.0, 5000.0, 6000.0, 7000.0, 8000.0];
        let low_bins = [250.0, 350.0, 500.0];

        let p_white_high = band_power(&white, sr, &high_bins);
        let p_dark_high = band_power(&dark, sr, &high_bins);
        let p_white_low = band_power(&white, sr, &low_bins);
        let p_dark_low = band_power(&dark, sr, &low_bins);

        // White: high vs low ratios should be within ~10x (broadly flat).
        let white_ratio = p_white_high / p_white_low;
        assert!(
            white_ratio > 0.05 && white_ratio < 20.0,
            "white-noise high/low ratio = {white_ratio:.2}, expected near 1.0"
        );

        // Dark: high band should be heavily attenuated relative to white.
        // 100x is a generous lower bound — a properly-cut LP at 20 Hz
        // should knock the 4-8 kHz band ~50+ dB below white.
        let high_attenuation = p_white_high / p_dark_high;
        assert!(
            high_attenuation > 100.0,
            "dark vs white high-band attenuation = {high_attenuation:.2}x, expected > 100x"
        );
    }

    #[test]
    fn white_has_more_energy_than_dark() {
        let mut ng_white = NoiseGen::new(44100.0);
        let mut ng_dark = NoiseGen::new(44100.0);
        ng_white.trigger();
        ng_dark.trigger();

        let mut rms_white = 0.0f64;
        let mut rms_dark = 0.0f64;
        let n = 10000;
        for _ in 0..n {
            let w = ng_white.tick(1.0) as f64;
            let d = ng_dark.tick(0.0) as f64;
            rms_white += w * w;
            rms_dark += d * d;
        }
        rms_white = (rms_white / n as f64).sqrt();
        rms_dark = (rms_dark / n as f64).sqrt();
        assert!(
            rms_white > rms_dark,
            "white {} should have more energy than dark {}",
            rms_white,
            rms_dark
        );
    }
}
