//! Regression: the global last-used-preset file is standalone-only.
//!
//! ## The bug this guards against
//!
//! Niner remembers the last preset you picked in `<niner-data>/last_preset.txt`
//! so the *standalone* binary reopens on the same sound. That file is a single
//! global path with no notion of a project.
//!
//! In a DAW it must never be touched. The host restores every parameter from
//! the song before the editor opens, so reading that file and applying the
//! named preset destroys the user's per-project sound, and writing it makes
//! every song share one sound. Reported in Renoise, 2026-08-02: tweak a kick,
//! save the song, reload, and the tweaks are replaced by whatever preset was
//! selected last, in any project.
//!
//! The original guard inferred "am I in a DAW?" from the transport
//! (`Niner::host_ever_stopped`), which only becomes true after the host has
//! been seen playing *and then* stopped. A freshly loaded song sits stopped,
//! so the plugin concluded it was standalone and clobbered the restored state.
//! Host detection now uses `IS_STANDALONE`, which only `run_standalone()` sets.
//!
//! A test binary never calls `run_standalone()`, so it looks exactly like a
//! VST3/CLAP instance hosted in a DAW — which is precisely the case under test.

use std::sync::atomic::Ordering;

/// Sandbox the data dir so this test can never read or write the real
/// `~/.local/share/niner/last_preset.txt`.
fn sandbox() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("niner-lastpreset-test-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create sandbox data dir");
    std::env::set_var(niner::presets::DATA_DIR_ENV, &dir);
    dir
}

/// Both directions live in one `#[test]` on purpose: they share the process
/// globals (`NINER_DATA_DIR`, `IS_STANDALONE`), and cargo runs `#[test]`
/// functions in the same binary on parallel threads.
#[test]
fn last_preset_file_is_standalone_only() {
    let dir = sandbox();
    let file = dir.join("last_preset.txt");
    std::fs::write(&file, "Clean Sub").expect("seed last_preset.txt");

    // --- Plugin mode (IS_STANDALONE unset, i.e. hosted in a DAW) ---
    assert!(
        !niner::IS_STANDALONE.load(Ordering::Relaxed),
        "a test binary must look like a plugin instance; only run_standalone() sets this"
    );

    assert_eq!(
        niner::presets::load_last_preset_name(),
        None,
        "plugin mode must ignore last_preset.txt — applying it when the editor \
         opens overwrites the parameters the host restored from the song"
    );

    niner::presets::save_last_preset_name("Tweaked Kick");
    assert_eq!(
        std::fs::read_to_string(&file).expect("read last_preset.txt"),
        "Clean Sub",
        "plugin mode must not write last_preset.txt — it is one global file, so \
         selecting a preset in one song would repoint every other song's sound"
    );

    // --- Standalone mode keeps the convenience feature ---
    niner::IS_STANDALONE.store(true, Ordering::Relaxed);

    assert_eq!(
        niner::presets::load_last_preset_name().as_deref(),
        Some("Clean Sub"),
        "standalone must still reopen on the last-used preset"
    );

    niner::presets::save_last_preset_name("Tweaked Kick");
    assert_eq!(
        std::fs::read_to_string(&file).expect("read last_preset.txt"),
        "Tweaked Kick",
        "standalone must still record the last-used preset"
    );

    niner::IS_STANDALONE.store(false, Ordering::Relaxed);
    let _ = std::fs::remove_dir_all(&dir);
}
