//! Small shared UI primitives used throughout the editor: rack chrome,
//! screws, grooves, LEDs, inset displays, and the `param_knob` helper that
//! wraps `knob::knob` with a `FloatParam` setter.

use nih_plug::prelude::*;
use nih_plug_egui::egui;

use crate::ui::knob;
use crate::ui::theme;

/// Rack chrome: the left/right steel "ears" with ventilation slots.
pub fn draw_rack_ear(painter: &egui::Painter, x: f32, y: f32, width: f32, height: f32) {
    painter.rect_filled(
        egui::Rect::from_min_size(egui::pos2(x, y), egui::vec2(width, height)),
        0.0,
        theme::BG_RACK_EAR,
    );
    for i in 0..8 {
        let slot_y = y + 35.0 + i as f32 * 44.0;
        if slot_y + 22.0 > y + height {
            break;
        }
        painter.rect_filled(
            egui::Rect::from_min_size(
                egui::pos2(x + (width - 8.0) / 2.0, slot_y),
                egui::vec2(8.0, 22.0),
            ),
            2.0,
            theme::BG_VENT,
        );
    }
}

/// A Phillips-head rack screw.
pub fn draw_screw(painter: &egui::Painter, cx: f32, cy: f32, radius: f32) {
    let center = egui::pos2(cx, cy);
    painter.circle_filled(center, radius, theme::SCREW_LIGHT);
    painter.circle_filled(center, radius * 0.85, theme::KNOB_METAL);
    painter.circle_filled(center, radius * 0.7, theme::SCREW_DARK);
    for i in 0..6 {
        let angle = (i as f32 / 6.0) * std::f32::consts::TAU - std::f32::consts::PI / 6.0;
        let p = center + egui::vec2(angle.cos(), angle.sin()) * radius * 0.4;
        painter.circle_filled(p, 1.0, theme::SCREW_HEX);
    }
}

/// Set once at editor startup when the baked chassis texture loads
/// successfully. When true, `draw_groove` no-ops because the grooves are
/// part of the bake. False = procedural fallback.
pub static CHASSIS_BAKED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Horizontal panel groove — used to separate rows of knobs. Skipped when
/// `CHASSIS_BAKED` is set (the bake includes real beveled groove cuts).
pub fn draw_groove(painter: &egui::Painter, left: f32, right: f32, y: f32) {
    if CHASSIS_BAKED.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    painter.line_segment(
        [egui::pos2(left, y), egui::pos2(right, y)],
        egui::Stroke::new(1.0, theme::GROOVE_DARK),
    );
    painter.line_segment(
        [egui::pos2(left, y + 1.0), egui::pos2(right, y + 1.0)],
        egui::Stroke::new(0.5, theme::GROOVE_LIGHT),
    );
}

/// Small status LED with optional halo glow.
pub fn draw_led(painter: &egui::Painter, cx: f32, cy: f32, on: bool) {
    let center = egui::pos2(cx, cy);
    painter.circle_filled(center, 4.0, egui::Color32::from_rgb(0x08, 0x08, 0x08));
    let color = if on {
        theme::RED_LED
    } else {
        egui::Color32::from_rgb(0x2a, 0x08, 0x08)
    };
    painter.circle_filled(center, 3.0, color);
    if on {
        painter.circle_filled(center, 8.0, theme::RED_GLOW);
    }
}

/// Inset LCD-style display frame with scan-lines and a red ambient glow.
/// Asymmetric padding from the bezel rect to the lit content rect.
///
/// The lit area sits inside the bezel with more dark margin on left/top/
/// bottom than right — the dark frame "extends further" around the lit
/// content. The right margin matches the bezel `frame` thickness so the
/// existing 7-seg readout placement (right-aligned to `wf_width`) stays
/// flush with the right bezel edge as it always has.
#[derive(Copy, Clone, Debug)]
pub struct DisplayInsets {
    pub frame: f32,
    pub content_left: f32,
    pub content_top: f32,
    pub content_bottom: f32,
    pub content_right: f32,
}

impl DisplayInsets {
    pub const DEFAULT: Self = Self {
        frame: 4.0,
        content_left: 8.0,
        content_top: 6.0,
        content_bottom: 6.0,
        content_right: 4.0,
    };

    /// Compute the lit rect from a bezel-inside rect (`x, y, w, h` — the
    /// area the original `draw_inset_display` painted as `BG_DISPLAY`).
    pub fn lit_rect(&self, x: f32, y: f32, w: f32, h: f32) -> egui::Rect {
        egui::Rect::from_min_size(
            egui::pos2(x + self.content_left, y + self.content_top),
            egui::vec2(
                w - self.content_left - self.content_right,
                h - self.content_top - self.content_bottom,
            ),
        )
    }
}

/// Draw the dark inset display backdrop with default insets. The lit rect
/// (where scan-lines + red glow are painted) is asymmetrically inset from
/// the bezel — see `DisplayInsets::DEFAULT`. Use [`lit_rect_default`] when
/// placing content inside it.
pub fn draw_inset_display(painter: &egui::Painter, x: f32, y: f32, w: f32, h: f32) {
    draw_inset_display_with(painter, x, y, w, h, DisplayInsets::DEFAULT);
}

/// Lit rect for the default insets — convenience for content placement.
pub fn lit_rect_default(x: f32, y: f32, w: f32, h: f32) -> egui::Rect {
    DisplayInsets::DEFAULT.lit_rect(x, y, w, h)
}

/// Draw the dark inset display backdrop with explicit insets.
pub fn draw_inset_display_with(
    painter: &egui::Painter,
    x: f32,
    y: f32,
    w: f32,
    h: f32,
    insets: DisplayInsets,
) {
    // Outer bezel frame — skipped when the chassis is baked, since the bake
    // contains a real beveled depression at the same coords.
    if !CHASSIS_BAKED.load(std::sync::atomic::Ordering::Relaxed) {
        painter.rect_filled(
            egui::Rect::from_min_size(
                egui::pos2(x - insets.frame, y - insets.frame),
                egui::vec2(w + insets.frame * 2.0, h + insets.frame * 2.0),
            ),
            4.0,
            theme::BG_DISPLAY_FRAME,
        );
    }
    let lit = insets.lit_rect(x, y, w, h);
    // Inner lit area — uniform dark backdrop. Covers the hammertone
    // texture inside the baked depression so scan-lines and glow have a
    // clean surface to render against.
    painter.rect_filled(lit, 0.0, theme::BG_DISPLAY);
    // Scan-lines, confined to lit rect.
    let mut sy = lit.top();
    while sy < lit.bottom() {
        painter.line_segment(
            [egui::pos2(lit.left(), sy), egui::pos2(lit.right(), sy)],
            egui::Stroke::new(1.0, egui::Color32::from_rgba_premultiplied(0, 0, 0, 20)),
        );
        sy += 2.0;
    }
    // Red ambient glow, confined to lit rect.
    let glow_inset = lit.width() * 0.2;
    painter.rect_filled(
        egui::Rect::from_min_size(
            egui::pos2(lit.left() + glow_inset, lit.top() + lit.height() * 0.2),
            egui::vec2(lit.width() - glow_inset * 2.0, lit.height() * 0.6),
        ),
        0.0,
        theme::RED_AMBIENT,
    );
}

/// Small arrow button used in the preset bar (`◂` / `▸`).
pub fn preset_arrow_btn(
    painter: &egui::Painter,
    rect: egui::Rect,
    glyph: &str,
    color: egui::Color32,
) {
    painter.rect_filled(rect, 2.0, theme::BTN_DARK);
    painter.rect_filled(
        egui::Rect::from_min_size(rect.min, egui::vec2(rect.width(), rect.height() * 0.4)),
        2.0,
        theme::BTN_LIGHT,
    );
    painter.text(
        rect.center(),
        egui::Align2::CENTER_CENTER,
        glyph,
        egui::FontId::new(10.0, egui::FontFamily::Monospace),
        color,
    );
}

/// Render a knob wired to a `FloatParam`. Returns `true` if the value changed.
#[allow(clippy::too_many_arguments)]
pub fn param_knob(
    ui: &mut egui::Ui,
    setter: &ParamSetter,
    id: &str,
    label: &str,
    param: &FloatParam,
    min: f32,
    max: f32,
    default: f32,
    format_value: impl Fn(f32) -> String,
    diameter: f32,
    core_color: egui::Color32,
) -> bool {
    let mut val = param.value();
    let changed = knob::knob(
        ui,
        egui::Id::new(id),
        &mut val,
        min,
        max,
        default,
        label,
        format_value,
        diameter,
        core_color,
    )
    .changed;
    if changed {
        setter.begin_set_parameter(param);
        setter.set_parameter(param, val);
        setter.end_set_parameter(param);
    }
    changed
}

/// Compact-layout variant of `param_knob` for dense clusters (e.g. the
/// stacked sub-rows in the v0.6.0 SAT/CLIP cluster). Identical
/// param-binding behaviour; visually the knob renders with tighter
/// surrounding padding and the label sits flush against the knob box.
///
/// `label` is the abbreviation rendered under the knob (≤ 4 chars
/// recommended so it fits on one line at 9.5 pt mono in the compact
/// column). `tooltip` is the long-form description shown on hover.
#[allow(clippy::too_many_arguments)]
pub fn param_knob_compact(
    ui: &mut egui::Ui,
    setter: &ParamSetter,
    id: &str,
    label: &str,
    tooltip: &str,
    param: &FloatParam,
    min: f32,
    max: f32,
    default: f32,
    format_value: impl Fn(f32) -> String,
    diameter: f32,
    core_color: egui::Color32,
) -> bool {
    let mut val = param.value();
    let changed = knob::knob_compact(
        ui,
        egui::Id::new(id),
        &mut val,
        min,
        max,
        default,
        label,
        tooltip,
        format_value,
        diameter,
        core_color,
    )
    .changed;
    if changed {
        setter.begin_set_parameter(param);
        setter.set_parameter(param, val);
        setter.end_set_parameter(param);
    }
    changed
}
