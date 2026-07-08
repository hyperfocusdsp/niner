//! Factory presets and user preset I/O for Niner.
//!
//! A preset is a `(name, ParamSnapshot)` pair. The snapshot type itself lives
//! in `plugin.rs` next to `NinerParams` so there's a single source of truth
//! for every persisted parameter. This module is only responsible for:
//!
//! * Providing the built-in factory presets.
//! * Reading/writing user presets as JSON in a platform-appropriate data
//!   directory (see `util::paths::niner_preset_dir`).
//! * Exposing a merged factory+user list to the UI.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

use crate::params::ParamSnapshot;
use crate::util::paths;

/// Current on-disk preset schema version. Bump on breaking changes.
///
/// v2 introduces `ParamSnapshot` as the storage format and adds fields that
/// were missing from v1 (notably `mid_phase_offset`). v1 files are *not*
/// migrated — this is a pre-release refactor.
const PRESET_VERSION: u32 = 2;

/// Max length for sanitized preset filenames (without `.json` extension).
/// Safe on all mainstream filesystems (Windows limits path components to 255).
const MAX_FILENAME_LEN: usize = 120;

/// On-disk JSON wrapper.
///
/// `#[serde(default)]` on `params` means future field additions to
/// `ParamSnapshot` silently get type defaults rather than failing to load
/// older JSON.
#[derive(Serialize, Deserialize)]
struct PresetFile {
    name: String,
    version: u32,
    #[serde(default)]
    params: ParamSnapshot,
}

/// A preset entry returned from `PresetManager` — name + params + origin.
#[derive(Clone)]
pub struct PresetEntry {
    pub name: String,
    pub params: ParamSnapshot,
    pub is_factory: bool,
}

/// Factory presets — read-only, baked in. Each preset's full param state
/// lives as a JSON file under `assets/factory_presets/` (the same on-disk
/// schema as user presets). The order here is the order they appear in the
/// preset list; tests that index by position assume "Punchy Techno" stays
/// at index 1.
pub fn factory_presets() -> Vec<PresetEntry> {
    const FACTORIES: &[(&str, &str)] = &[
        (
            "Clean Sub",
            include_str!("../assets/factory_presets/Clean_Sub.json"),
        ),
        (
            "Punchy Techno",
            include_str!("../assets/factory_presets/Punchy_Techno.json"),
        ),
        (
            "909-ish",
            include_str!("../assets/factory_presets/909-ish.json"),
        ),
        ("808", include_str!("../assets/factory_presets/808.json")),
        ("909", include_str!("../assets/factory_presets/909.json")),
        ("hh", include_str!("../assets/factory_presets/hh.json")),
        ("Init", include_str!("../assets/factory_presets/Init.json")),
        (
            "overdose",
            include_str!("../assets/factory_presets/overdose.json"),
        ),
        ("psy", include_str!("../assets/factory_presets/psy.json")),
        ("sd1", include_str!("../assets/factory_presets/sd1.json")),
        ("sd2", include_str!("../assets/factory_presets/sd2.json")),
        (
            "tight",
            include_str!("../assets/factory_presets/tight.json"),
        ),
        ("clap", include_str!("../assets/factory_presets/clap.json")),
        (
            "niner",
            include_str!("../assets/factory_presets/niner.json"),
        ),
    ];
    FACTORIES
        .iter()
        .map(|(name, json)| {
            let pf: PresetFile = serde_json::from_str(json)
                .unwrap_or_else(|e| panic!("factory preset {name} malformed: {e}"));
            PresetEntry {
                name: (*name).to_string(),
                params: pf.params,
                is_factory: true,
            }
        })
        .collect()
}

/// Manages factory + user presets.
pub struct PresetManager {
    factory: Vec<PresetEntry>,
    user: Vec<PresetEntry>,
    dir: PathBuf,
}

impl PresetManager {
    pub fn new() -> Self {
        Self::load(
            paths::niner_preset_dir(),
            paths::niner_hidden_presets_file(),
        )
    }

    /// Build a manager from an explicit preset dir + hidden-list file.
    /// `new()` wires in the real platform paths; tests inject temp dirs.
    fn load(preset_dir: PathBuf, hidden_file: PathBuf) -> Self {
        // Auto-heal: older builds recorded "deleted" factory presets in a
        // hidden-names file and filtered them out of `list_all`. A few stray
        // DEL clicks could hide almost the whole factory bank with no way to
        // restore them (the P1 "only 1 preset shows" bug). Hiding factory
        // presets is no longer supported, so delete any stale file and never
        // honor it again — every affected install recovers on next launch.
        let _ = fs::remove_file(&hidden_file);
        let factory = factory_presets();
        let mut mgr = Self {
            factory,
            user: Vec::new(),
            dir: preset_dir,
        };
        mgr.refresh();
        tracing::info!(
            factories = mgr.factory.len(),
            user = mgr.user.len(),
            "preset library loaded"
        );
        mgr
    }

    /// Rescan the user preset directory.
    pub fn refresh(&mut self) {
        self.user.clear();
        if let Ok(entries) = fs::read_dir(&self.dir) {
            let mut paths: Vec<PathBuf> = entries
                .flatten()
                .filter(|e| e.path().extension().map(|x| x == "json").unwrap_or(false))
                .map(|e| e.path())
                .collect();
            paths.sort();
            for path in paths {
                if let Ok(data) = fs::read_to_string(&path) {
                    if let Ok(pf) = serde_json::from_str::<PresetFile>(&data) {
                        self.user.push(PresetEntry {
                            name: pf.name,
                            params: pf.params,
                            is_factory: false,
                        });
                    }
                }
            }
        }
    }

    /// All presets: factory first, then user. A user preset whose name
    /// matches a factory preset (case-insensitive) shadows the factory
    /// entry, so "overwriting" a factory means saving a user copy under the
    /// same name; deleting that user override restores the factory entry.
    /// Factory presets themselves are always listed — they can't be hidden
    /// or deleted.
    pub fn list_all(&self) -> Vec<PresetEntry> {
        let mut out: Vec<PresetEntry> = self
            .factory
            .iter()
            .filter(|f| {
                !self
                    .user
                    .iter()
                    .any(|u| u.name.eq_ignore_ascii_case(&f.name))
            })
            .cloned()
            .collect();
        out.extend(self.user.iter().cloned());
        out
    }

    /// Save a snapshot under `name`. Saving under a factory name writes a
    /// user copy that shadows the factory in `list_all` — the factory
    /// definition itself is never touched, so a `delete` restores it.
    pub fn save(&mut self, name: &str, params: ParamSnapshot) -> Result<(), String> {
        let pf = PresetFile {
            name: name.to_owned(),
            version: PRESET_VERSION,
            params,
        };
        let json = serde_json::to_string_pretty(&pf).map_err(|e| e.to_string())?;
        fs::create_dir_all(&self.dir).map_err(|e| e.to_string())?;
        let path = self.dir.join(format!("{}.json", sanitize_filename(name)));
        fs::write(&path, json).map_err(|e| e.to_string())?;
        self.refresh();
        Ok(())
    }

    /// Delete a preset by name.
    ///
    /// * If a user file exists for this name, remove it. If the name also
    ///   matches a factory preset, the factory entry reappears in `list_all`
    ///   on the next refresh.
    /// * A factory preset itself cannot be deleted — it's baked into the
    ///   binary, so deletion is refused (the caller surfaces the message).
    ///   This is deliberate: an earlier build "deleted" factories by hiding
    ///   them, which could make the whole bank vanish.
    pub fn delete(&mut self, name: &str) -> Result<(), String> {
        if self.user.iter().any(|e| e.name == name) {
            let path = self.dir.join(format!("{}.json", sanitize_filename(name)));
            fs::remove_file(&path).map_err(|e| e.to_string())?;
            self.refresh();
            return Ok(());
        }
        if self.factory.iter().any(|e| e.name == name) {
            return Err("Factory presets can't be deleted".to_string());
        }
        Err(format!("\"{name}\" not found"))
    }
}

/// Remember the name of the last-selected preset so the standalone reopens
/// with the same choice. Best-effort — IO errors are logged and swallowed.
pub fn save_last_preset_name(name: &str) {
    let path = paths::niner_last_preset_file();
    if let Some(parent) = path.parent() {
        if let Err(e) = fs::create_dir_all(parent) {
            tracing::warn!(?e, "failed to create last-preset dir");
            return;
        }
    }
    if let Err(e) = fs::write(&path, name) {
        tracing::warn!(?e, ?path, "failed to write last-preset file");
    }
}

/// Read back the last-selected preset name, if any.
pub fn load_last_preset_name() -> Option<String> {
    let path = paths::niner_last_preset_file();
    let data = fs::read_to_string(&path).ok()?;
    let trimmed = data.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// Make a string safe to use as a filename component on Linux, macOS, and
/// Windows. Disallows path separators, shell-ish metachars, and caps length.
fn sanitize_filename(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '_' || c == ' ' {
                c
            } else {
                '_'
            }
        })
        .collect::<String>()
        .trim()
        .replace(' ', "_");

    // Cap length to stay well under filesystem limits (Windows: 255).
    // Truncating on a char boundary — safe because all retained chars are
    // single-byte ASCII after the filter above.
    if cleaned.len() > MAX_FILENAME_LEN {
        cleaned[..MAX_FILENAME_LEN].to_string()
    } else if cleaned.is_empty() {
        "untitled".to_string()
    } else {
        cleaned
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_strips_path_separators() {
        assert_eq!(sanitize_filename("../etc/passwd"), "___etc_passwd");
        assert_eq!(sanitize_filename("a\\b"), "a_b");
    }

    #[test]
    fn sanitize_replaces_spaces_with_underscore() {
        assert_eq!(sanitize_filename("my kick"), "my_kick");
    }

    #[test]
    fn sanitize_caps_length() {
        let long = "a".repeat(500);
        assert_eq!(sanitize_filename(&long).len(), MAX_FILENAME_LEN);
    }

    #[test]
    fn sanitize_empty_becomes_untitled() {
        assert_eq!(sanitize_filename(""), "untitled");
        assert_eq!(sanitize_filename("   "), "untitled");
    }

    #[test]
    fn factory_presets_load_cleanly() {
        // Every JSON in `assets/factory_presets/` parses, the list is
        // non-empty, and "Punchy Techno" stays at index 1 (round-trip test
        // depends on it).
        let presets = factory_presets();
        assert!(presets.len() >= 10);
        assert_eq!(presets[1].name, "Punchy Techno");
    }

    /// Round-trip: a snapshot serialized to JSON and read back equals itself.
    /// This is the poor-man's version of the full capture → apply → capture
    /// round-trip (which needs a real `ParamSetter` wired to `NinerParams`
    /// and is covered by in-host manual testing). The serde path is the only
    /// part that can break silently when fields are added.
    #[test]
    fn param_snapshot_json_round_trip() {
        let original = factory_presets().remove(1).params; // "Punchy Techno"
        let pf = PresetFile {
            name: "round-trip".into(),
            version: PRESET_VERSION,
            params: original.clone(),
        };
        let json = serde_json::to_string(&pf).unwrap();
        let decoded: PresetFile = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded.version, PRESET_VERSION);
        assert_eq!(decoded.params, original);
    }

    /// Older JSON missing a field (e.g. pre-v2 files without `mid_phase_offset`)
    /// should load with the field defaulted to 0.0, thanks to `#[serde(default)]`.
    #[test]
    fn param_snapshot_tolerates_missing_fields() {
        let json = r#"{
            "name": "old",
            "version": 1,
            "params": { "decay_ms": 250.0 }
        }"#;
        let pf: PresetFile = serde_json::from_str(json).unwrap();
        assert_eq!(pf.params.decay_ms, 250.0);
        assert_eq!(pf.params.mid_phase_offset, 0.0);
        assert_eq!(pf.params.sub_gain, 0.0);
    }

    /// Regression for the P1 "only 1 factory preset shows" bug: a
    /// `hidden_presets.txt` listing 13 of the 14 factory names must NOT
    /// reduce the factory list. Pre-fix this returned 1 (only "hh").
    #[test]
    fn hidden_file_is_ignored() {
        let tmp = std::env::temp_dir().join("niner_test_hidden_ignored");
        let _ = fs::remove_dir_all(&tmp);
        let preset_dir = tmp.join("presets");
        fs::create_dir_all(&preset_dir).unwrap();
        let hidden_file = tmp.join("hidden_presets.txt");
        let names: Vec<String> = factory_presets()
            .into_iter()
            .map(|p| p.name)
            .filter(|n| n != "hh")
            .collect();
        assert_eq!(names.len(), 13);
        fs::write(&hidden_file, names.join("\n")).unwrap();

        let mgr = PresetManager::load(preset_dir, hidden_file);
        assert_eq!(
            mgr.list_all().len(),
            14,
            "a stale hidden_presets.txt must not suppress factory presets"
        );
        let _ = fs::remove_dir_all(&tmp);
    }

    /// Factory presets can no longer be deleted/hidden — `delete` on a
    /// factory-only name is refused and the list is unchanged.
    #[test]
    fn delete_factory_is_refused() {
        let tmp = std::env::temp_dir().join("niner_test_delete_factory");
        let _ = fs::remove_dir_all(&tmp);
        let preset_dir = tmp.join("presets");
        fs::create_dir_all(&preset_dir).unwrap();
        let mut mgr = PresetManager::load(preset_dir, tmp.join("hidden_presets.txt"));
        let before = mgr.list_all().len();
        assert!(
            mgr.delete("hh").is_err(),
            "deleting a factory preset must be refused"
        );
        assert_eq!(
            mgr.list_all().len(),
            before,
            "a refused delete must not change the preset list"
        );
        let _ = fs::remove_dir_all(&tmp);
    }
}
