//! Simple 16-step pattern sequencer.
//!
//! State is shared between the UI (click to toggle steps, read playhead for
//! highlight) and the audio thread (advance counter, read steps, fire
//! triggers) via plain atomics — no locks, RT-safe.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};
use std::sync::Arc;

use parking_lot::Mutex;

pub const STEPS: usize = 16;

/// Default pattern: four-on-the-floor (steps 0, 4, 8, 12).
pub const DEFAULT_STEP_BITS: u16 = 0x1111;
/// Default accent pattern: no accents — fully opt-in. v0.5.x sessions
/// without an accent bitmask deserialize to this value via `Default`.
pub const DEFAULT_ACCENT_BITS: u16 = 0x0000;
const DEFAULT_BPM: f32 = 120.0;
const MIN_BPM: f32 = 40.0;
const MAX_BPM: f32 = 240.0;

pub struct Sequencer {
    pub steps: [AtomicBool; STEPS],
    /// 909-style accent flags, parallel to `steps`. A step is "accented"
    /// iff `steps[i]` AND `accents[i]` are both true. Clearing a step also
    /// clears its accent (handled in `set_step`/`toggle_step`) so a
    /// previously-accented step reactivated later starts back at normal
    /// velocity, matching how the original 909 hardware behaves.
    pub accents: [AtomicBool; STEPS],
    /// User-controlled run flag (standalone only — ignored when `host_synced`).
    pub running: AtomicBool,
    /// Standalone BPM stored as milli-BPM so we can use an integer atomic.
    bpm_milli: AtomicU32,
    /// Last step played — audio thread writes, UI reads for playhead display.
    pub current_step: AtomicUsize,
    /// True once the audio thread has detected a real DAW transport (i.e.
    /// transport.playing has ever reported false). Set by the audio thread;
    /// read by the UI to disable standalone controls and hide the spacebar.
    pub host_synced: AtomicBool,
    /// BPM the UI should display: host tempo when synced, standalone BPM
    /// otherwise. Stored as milli-BPM.
    display_bpm_milli: AtomicU32,
    /// True when the sequencer is actively stepping (either user ran it in
    /// standalone mode, or host transport is playing in a DAW). UI reads
    /// this to render the PLAY/STOP button state and the playhead.
    pub running_effective: AtomicBool,
    /// Set by the audio thread on its first `process()` call — signals that
    /// `host_synced` now reflects reality. The editor uses this to defer
    /// standalone-only actions (e.g. restoring last session) until after
    /// the DAW/standalone decision has been made.
    pub transport_probed: AtomicBool,
    /// UI-thread mirror of the step bitmask for DAW/standalone state
    /// persistence. The audio thread never touches this — only the UI
    /// thread (via `toggle_step` / `set_step`) and `initialize()`.
    persist_mirror: Arc<Mutex<u16>>,
    /// UI-thread mirror of the accent bitmask, persisted alongside the
    /// step bitmask. v0.5.x sessions had no accents field; nih-plug
    /// deserialization falls back to `DEFAULT_ACCENT_BITS` (zero) so old
    /// patterns load unchanged with no accents marked.
    accent_persist_mirror: Arc<Mutex<u16>>,
}

impl Sequencer {
    pub fn new(persist_mirror: Arc<Mutex<u16>>, accent_persist_mirror: Arc<Mutex<u16>>) -> Self {
        let initial_bits = *persist_mirror.lock();
        let initial_accents = *accent_persist_mirror.lock();
        Self {
            steps: std::array::from_fn(|i| AtomicBool::new((initial_bits >> i) & 1 != 0)),
            accents: std::array::from_fn(|i| AtomicBool::new((initial_accents >> i) & 1 != 0)),
            running: AtomicBool::new(false),
            bpm_milli: AtomicU32::new((DEFAULT_BPM * 1000.0) as u32),
            current_step: AtomicUsize::new(0),
            host_synced: AtomicBool::new(false),
            display_bpm_milli: AtomicU32::new((DEFAULT_BPM * 1000.0) as u32),
            running_effective: AtomicBool::new(false),
            transport_probed: AtomicBool::new(false),
            persist_mirror,
            accent_persist_mirror,
        }
    }

    /// Copy the persist-mirror bitmasks into the step + accent atomics.
    /// Called once from `Plugin::initialize()` after nih-plug has
    /// deserialized the `#[persist]` fields, so DAW-restored patterns
    /// reach the audio thread before the first `process()` call.
    pub fn restore_from_persist(&self) {
        let bits = *self.persist_mirror.lock();
        let accent_bits = *self.accent_persist_mirror.lock();
        for i in 0..STEPS {
            self.steps[i].store((bits >> i) & 1 != 0, Ordering::Relaxed);
            self.accents[i].store((accent_bits >> i) & 1 != 0, Ordering::Relaxed);
        }
    }

    pub fn display_bpm(&self) -> f32 {
        self.display_bpm_milli.load(Ordering::Relaxed) as f32 / 1000.0
    }

    pub fn set_display_bpm(&self, bpm: f32) {
        self.display_bpm_milli
            .store((bpm * 1000.0) as u32, Ordering::Relaxed);
    }

    pub fn is_host_synced(&self) -> bool {
        self.host_synced.load(Ordering::Relaxed)
    }

    pub fn is_running_effective(&self) -> bool {
        self.running_effective.load(Ordering::Relaxed)
    }

    pub fn bpm(&self) -> f32 {
        self.bpm_milli.load(Ordering::Relaxed) as f32 / 1000.0
    }

    pub fn set_bpm(&self, bpm: f32) {
        let clamped = bpm.clamp(MIN_BPM, MAX_BPM);
        self.bpm_milli
            .store((clamped * 1000.0) as u32, Ordering::Relaxed);
    }

    pub fn is_step_on(&self, idx: usize) -> bool {
        self.steps[idx].load(Ordering::Relaxed)
    }

    pub fn is_step_accented(&self, idx: usize) -> bool {
        self.accents[idx].load(Ordering::Relaxed)
    }

    /// UI-thread only: flip a step on/off and mirror the change into the
    /// persist bitmask. Turning a step OFF also clears its accent so the
    /// accent state can't outlive the step it was attached to.
    pub fn toggle_step(&self, idx: usize) {
        let prev = self.steps[idx].fetch_xor(true, Ordering::Relaxed);
        let mut bits = self.persist_mirror.lock();
        *bits ^= 1u16 << idx;
        // Step is now `!prev`; if it just turned off, clear the accent.
        if prev {
            self.accents[idx].store(false, Ordering::Relaxed);
            let mut acc_bits = self.accent_persist_mirror.lock();
            *acc_bits &= !(1u16 << idx);
        }
    }

    /// UI-thread only: set a step to an explicit state. Used by the
    /// click-drag paint path so repeated writes as the pointer moves are
    /// idempotent (unlike `toggle_step`, which would oscillate). Setting
    /// a step OFF also clears any accent on it.
    pub fn set_step(&self, idx: usize, on: bool) {
        self.steps[idx].store(on, Ordering::Relaxed);
        let mut bits = self.persist_mirror.lock();
        if on {
            *bits |= 1u16 << idx;
        } else {
            *bits &= !(1u16 << idx);
            self.accents[idx].store(false, Ordering::Relaxed);
            let mut acc_bits = self.accent_persist_mirror.lock();
            *acc_bits &= !(1u16 << idx);
        }
    }

    /// UI-thread only: clear every step (and its accent). Used by the
    /// CLEAR button next to BOUNCE in the SAT/EQ row.
    pub fn clear_pattern(&self) {
        for idx in 0..STEPS {
            self.steps[idx].store(false, Ordering::Relaxed);
            self.accents[idx].store(false, Ordering::Relaxed);
        }
        *self.persist_mirror.lock() = 0;
        *self.accent_persist_mirror.lock() = 0;
    }

    /// UI-thread only: toggle the accent flag on a step. No-op when the
    /// step itself is off — accents only have meaning on a fired step.
    pub fn toggle_accent(&self, idx: usize) {
        if !self.steps[idx].load(Ordering::Relaxed) {
            return;
        }
        self.accents[idx].fetch_xor(true, Ordering::Relaxed);
        let mut acc_bits = self.accent_persist_mirror.lock();
        *acc_bits ^= 1u16 << idx;
    }

    pub fn current(&self) -> usize {
        self.current_step.load(Ordering::Relaxed)
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::Relaxed)
    }

    pub fn toggle_running(&self) {
        let prev = self.running.load(Ordering::Relaxed);
        self.running.store(!prev, Ordering::Relaxed);
    }
}

impl Default for Sequencer {
    fn default() -> Self {
        Self::new(
            Arc::new(Mutex::new(DEFAULT_STEP_BITS)),
            Arc::new(Mutex::new(DEFAULT_ACCENT_BITS)),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make() -> Sequencer {
        Sequencer::default()
    }

    #[test]
    fn default_pattern_is_four_on_the_floor() {
        let seq = make();
        for i in 0..STEPS {
            let want = matches!(i, 0 | 4 | 8 | 12);
            assert_eq!(seq.is_step_on(i), want, "step {i} default");
        }
    }

    #[test]
    fn set_step_writes_atomically_and_to_mirror() {
        let persist = Arc::new(Mutex::new(0u16));
        let accent = Arc::new(Mutex::new(0u16));
        let seq = Sequencer::new(persist.clone(), accent);
        seq.set_step(5, true);
        assert!(seq.is_step_on(5));
        assert_eq!(*persist.lock(), 1u16 << 5);
        seq.set_step(5, false);
        assert!(!seq.is_step_on(5));
        assert_eq!(*persist.lock(), 0);
    }

    #[test]
    fn toggle_accent_no_op_when_step_off() {
        // Accents only have meaning on a fired step. toggle_accent on an
        // off step must NOT flip the bit — otherwise we'd accumulate
        // accent state with no visible step, then surprise the user when
        // they re-enable that step later.
        let persist = Arc::new(Mutex::new(0u16));
        let accent = Arc::new(Mutex::new(0u16));
        let seq = Sequencer::new(persist, accent.clone());
        assert!(!seq.is_step_on(3));
        seq.toggle_accent(3);
        assert!(!seq.is_step_accented(3));
        assert_eq!(*accent.lock(), 0);
    }

    #[test]
    fn toggle_accent_flips_when_step_on() {
        let seq = make();
        seq.set_step(7, true);
        assert!(!seq.is_step_accented(7));
        seq.toggle_accent(7);
        assert!(seq.is_step_accented(7));
        seq.toggle_accent(7);
        assert!(!seq.is_step_accented(7));
    }

    #[test]
    fn bpm_is_clamped_to_supported_range() {
        let seq = make();
        seq.set_bpm(0.0);
        assert!((seq.bpm() - MIN_BPM).abs() < 1e-3);
        seq.set_bpm(1000.0);
        assert!((seq.bpm() - MAX_BPM).abs() < 1e-3);
        seq.set_bpm(140.5);
        assert!((seq.bpm() - 140.5).abs() < 1e-3);
    }

    #[test]
    fn toggle_running_flips_flag() {
        let seq = make();
        assert!(!seq.is_running());
        seq.toggle_running();
        assert!(seq.is_running());
        seq.toggle_running();
        assert!(!seq.is_running());
    }

    #[test]
    fn display_bpm_is_independent_of_internal_bpm() {
        // host_synced mode writes display_bpm to the host tempo while
        // standalone mode writes to internal bpm — the two paths must
        // not stomp each other.
        let seq = make();
        seq.set_bpm(100.0);
        seq.set_display_bpm(160.0);
        assert!((seq.bpm() - 100.0).abs() < 1e-3);
        assert!((seq.display_bpm() - 160.0).abs() < 1e-3);
    }
}
