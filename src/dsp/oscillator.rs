use std::f32::consts::TAU;

/// Phase-accumulating sine oscillator.
///
/// At trigger time, phase is set to `phase_offset` (default π/2 = cosine start
/// for maximum punch — first sample at peak amplitude).
pub struct SineOsc {
    phase: f32,
    sample_rate: f32,
}

impl SineOsc {
    pub fn new(sample_rate: f32) -> Self {
        Self {
            phase: 0.0,
            sample_rate,
        }
    }

    /// Reset phase to the given offset. π/2 = cosine start = max amplitude.
    pub fn trigger(&mut self, phase_offset: f32) {
        self.phase = phase_offset;
    }

    /// Generate one sample at the given frequency (Hz).
    pub fn tick(&mut self, freq: f32) -> f32 {
        let out = self.phase.sin();
        self.phase += freq / self.sample_rate * TAU;
        // Keep phase in [0, TAU) to avoid precision loss over time
        if self.phase >= TAU {
            self.phase -= TAU;
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cosine_start_gives_peak() {
        let mut osc = SineOsc::new(44100.0);
        osc.trigger(std::f32::consts::FRAC_PI_2);
        let sample = osc.tick(100.0);
        assert!(
            (sample - 1.0).abs() < 0.001,
            "expected ~1.0, got {}",
            sample
        );
    }

    #[test]
    fn phase_continuous_across_buffers() {
        let mut osc = SineOsc::new(44100.0);
        osc.trigger(0.0);
        let mut prev = osc.tick(440.0);
        for _ in 0..1000 {
            let curr = osc.tick(440.0);
            // At 440Hz / 44100Hz, phase increment is small, so consecutive
            // samples should differ by a bounded amount
            let diff = (curr - prev).abs();
            assert!(
                diff < 0.1,
                "discontinuity: {} -> {} (diff {})",
                prev,
                curr,
                diff
            );
            prev = curr;
        }
    }

    #[test]
    fn output_bounded() {
        let mut osc = SineOsc::new(44100.0);
        osc.trigger(0.0);
        for _ in 0..10000 {
            let s = osc.tick(440.0);
            assert!((-1.0..=1.0).contains(&s), "out of range: {}", s);
        }
    }

    /// Goertzel single-bin power at frequency `bin_freq`. Returns
    /// (real² + imag²) so it can be compared between bins ratiometrically
    /// without taking sqrt. See `plugindev` skill notes on why this beats
    /// wideband RMS for verifying oscillator pitch.
    fn fundamental_power(samples: &[f32], sr: f32, bin_freq: f32) -> f32 {
        let w = TAU * bin_freq / sr;
        let (mut re, mut im) = (0.0f32, 0.0f32);
        for (i, &x) in samples.iter().enumerate() {
            let p = w * i as f32;
            re += x * p.cos();
            im += x * p.sin();
        }
        re * re + im * im
    }

    /// Verify the oscillator actually produces the requested frequency:
    /// 100 Hz output should have ≥30 dB more energy at 100 Hz than at 110 Hz
    /// or any other neighbouring bin.
    #[test]
    fn frequency_accuracy_at_100hz() {
        let sr = 48_000.0;
        let mut osc = SineOsc::new(sr);
        osc.trigger(0.0);
        let n = 24_000; // 0.5 s
        let samples: Vec<f32> = (0..n).map(|_| osc.tick(100.0)).collect();
        let on_bin = fundamental_power(&samples, sr, 100.0);
        let off_bin = fundamental_power(&samples, sr, 110.0);
        // Power ratio: 30 dB == 1000x.
        assert!(
            on_bin > 1000.0 * off_bin,
            "oscillator at 100 Hz: power(100)={on_bin:.2} not >> power(110)={off_bin:.2}"
        );
    }

    /// Same sanity-check at 1 kHz so the test catches a frequency-scaling
    /// regression in only one octave (e.g. an off-by-2× sample-rate bug).
    #[test]
    fn frequency_accuracy_at_1khz() {
        let sr = 48_000.0;
        let mut osc = SineOsc::new(sr);
        osc.trigger(0.0);
        let n = 24_000;
        let samples: Vec<f32> = (0..n).map(|_| osc.tick(1000.0)).collect();
        let on_bin = fundamental_power(&samples, sr, 1000.0);
        let off_bin = fundamental_power(&samples, sr, 1100.0);
        assert!(
            on_bin > 1000.0 * off_bin,
            "oscillator at 1 kHz: power(1000)={on_bin:.2} not >> power(1100)={off_bin:.2}"
        );
    }
}
