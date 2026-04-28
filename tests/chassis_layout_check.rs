//! Drift detector — fails if `tools/blender/presets/chassis.json` falls
//! out of sync with the canonical Rust constants in `src/ui/panels.rs` or
//! the canvas size in `src/params.rs`.
//!
//! The Blender bake hard-codes pixel positions for screws, vent slots,
//! bezel insets, and groove dividers. If anyone changes the Rust layout
//! without updating the JSON, the bake will misalign with the runtime
//! draws — usually invisible at first but breaks under specific UI scales
//! or DAW host quirks. This test makes that drift loud at `cargo test`.

use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn load_preset() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tools/blender/presets/chassis.json");
    let text = fs::read_to_string(&path).expect("chassis.json missing or unreadable");
    serde_json::from_str(&text).expect("chassis.json is not valid JSON")
}

fn f64_eq(a: f64, b: f64) {
    assert!(
        (a - b).abs() < 1e-9,
        "drift: expected {a}, got {b} ({})",
        (a - b).abs()
    );
}

#[test]
fn canvas_matches() {
    let p = load_preset();
    f64_eq(p["canvas"]["width"].as_f64().unwrap(), 680.0);
    f64_eq(p["canvas"]["height"].as_f64().unwrap(), 444.0);
}

#[test]
fn layout_constants_match() {
    let p = load_preset();
    let layout = &p["layout"];
    f64_eq(
        layout["rack_ear_w"].as_f64().unwrap(),
        niner::ui::panels::RACK_EAR_W as f64,
    );
    f64_eq(
        layout["content_left"].as_f64().unwrap(),
        niner::ui::panels::CONTENT_LEFT as f64,
    );
    f64_eq(
        layout["header_h"].as_f64().unwrap(),
        niner::ui::panels::HEADER_H as f64,
    );
    f64_eq(
        layout["knob_size"].as_f64().unwrap(),
        niner::ui::panels::KNOB_SIZE as f64,
    );
    f64_eq(
        layout["knob_spacing"].as_f64().unwrap(),
        niner::ui::panels::KNOB_SPACING as f64,
    );
}

#[test]
fn screw_positions_match() {
    let p = load_preset();
    let positions = p["screws"]["positions"].as_array().unwrap();
    assert_eq!(positions.len(), 4, "expected 4 corner screws");

    // Mirrors panels.rs draw_chrome screw positions:
    // (panel_left + 8, panel_top + 18) and three corners of (680 × 444).
    let expected = [
        (8.0, 18.0),
        (672.0, 18.0),  // right = 680 - 8
        (8.0, 426.0),   // bottom = 444 - 18
        (672.0, 426.0),
    ];
    for (i, (ex, ey)) in expected.iter().enumerate() {
        f64_eq(positions[i][0].as_f64().unwrap(), *ex);
        f64_eq(positions[i][1].as_f64().unwrap(), *ey);
    }
    f64_eq(p["screws"]["radius"].as_f64().unwrap(), 5.0);
}

#[test]
fn vent_slots_match() {
    let p = load_preset();
    let v = &p["vents"];
    // Mirrors widgets.rs draw_rack_ear:
    //   8 slots at (slot_y = ear_top + 35 + i*44), slot 8×22 px.
    assert_eq!(v["slots_per_ear"].as_i64().unwrap(), 8);
    f64_eq(v["slot_w"].as_f64().unwrap(), 8.0);
    f64_eq(v["slot_h"].as_f64().unwrap(), 22.0);
    f64_eq(v["first_slot_y_offset"].as_f64().unwrap(), 35.0);
    f64_eq(v["slot_spacing"].as_f64().unwrap(), 44.0);
}

#[test]
fn bezel_geometry_matches() {
    let p = load_preset();
    let b = &p["bezels"];
    // OUTPUT bezel — derived from editor.rs row layout:
    //   wf_left = panel.left + CONTENT_LEFT = 30
    //   master_y = panel.top + 36 + 6 = 42
    //   wf_width = 7 * KNOB_SPACING - 16 = 348
    //   wf_height = 56
    f64_eq(b["output"]["x"].as_f64().unwrap(), 30.0);
    f64_eq(b["output"]["y"].as_f64().unwrap(), 42.0);
    f64_eq(b["output"]["w"].as_f64().unwrap(), 348.0);
    f64_eq(b["output"]["h"].as_f64().unwrap(), 56.0);

    // COMP bezel — strip_x = wf_left + wf_width + 16 + 2 = 396; right edge
    // at panel.right - CONTENT_LEFT = 650; strip_w = 254; height = wf_height.
    f64_eq(b["comp"]["x"].as_f64().unwrap(), 396.0);
    f64_eq(b["comp"]["y"].as_f64().unwrap(), 42.0);
    f64_eq(b["comp"]["w"].as_f64().unwrap(), 254.0);
    f64_eq(b["comp"]["h"].as_f64().unwrap(), 56.0);
}

#[test]
fn groove_y_positions_match() {
    let p = load_preset();
    let ys = p["grooves"]["y_positions"].as_array().unwrap();
    // Computed from the row layout flow:
    //   above master row:    panel.top + 36 = 36
    //   above SUB/TOP row:   master_bottom_y(98) + 8 + 14 = 120
    //   above MID row:       sub_bottom(190)  + 14 = 204     (where 190 = 124 + 32 + 34)
    //   above SAT/EQ row:    mid_bottom(274)  + 14 = 288     (where 274 = 208 + 32 + 34)
    let expected = [36.0, 120.0, 204.0, 288.0];
    assert_eq!(ys.len(), expected.len());
    for (i, ey) in expected.iter().enumerate() {
        f64_eq(ys[i].as_f64().unwrap(), *ey);
    }
}

#[test]
fn edge_band_height_matches() {
    let p = load_preset();
    f64_eq(p["edge_band"]["height_px"].as_f64().unwrap(), 12.0);
}
