use crate::ui::theme;
use nih_plug_egui::egui;

/// Multiply an opaque colour toward black by factor `t` (0 = black, 1 = same).
/// Used to derive the cap's dark rim shade from its saturated section colour.
fn darken(c: egui::Color32, t: f32) -> egui::Color32 {
    egui::Color32::from_rgb(
        (c.r() as f32 * t) as u8,
        (c.g() as f32 * t) as u8,
        (c.b() as f32 * t) as u8,
    )
}

pub struct KnobResponse {
    pub changed: bool,
    pub reset: bool,
    /// Inner click-and-drag response of the knob rect itself (not the
    /// surrounding column). `None` only on the very first frame before
    /// allocation; populated for every subsequent frame. Callers attach
    /// `.context_menu()` to this when they want a right-click menu (e.g.
    /// MIDI Learn) anchored on the knob, not the label below.
    pub response: Option<egui::Response>,
}

/// Skeuomorphic matte-rubber knob: knurled rubber rim + saturated soft-sheen
/// section-colour cap + crisp white pointer (black-outlined) reaching the rim.
/// The rim and cap are rotationally symmetric, so only the pointer rotates.
///
/// Vertical drag changes value, shift for fine control, ctrl+click to reset.
#[allow(clippy::too_many_arguments)]
pub fn knob(
    ui: &mut egui::Ui,
    id: egui::Id,
    value: &mut f32,
    min: f32,
    max: f32,
    default: f32,
    label: &str,
    format_value: impl Fn(f32) -> String,
    diameter: f32,
    core_color: egui::Color32,
) -> KnobResponse {
    knob_inner(
        ui,
        id,
        value,
        min,
        max,
        default,
        label,
        "",
        format_value,
        diameter,
        core_color,
        false,
    )
}

/// Compact variant: tighter knob-to-label spacing for dense clusters
/// (e.g. the v0.6.0 SAT/CLIP stacked sub-rows). Visual rendering of the
/// knob itself is identical; only the surrounding box padding and the
/// gap before the label shrink — saves ~9 px of vertical room per knob.
///
/// `tooltip` adds a hover-text bubble explaining the abbreviated label
/// (e.g. label="CDRV" + tooltip="Voice clip drive — per-voice
/// waveshaper amount before amp envelope"). Pass an empty string to
/// suppress.
#[allow(clippy::too_many_arguments)]
pub fn knob_compact(
    ui: &mut egui::Ui,
    id: egui::Id,
    value: &mut f32,
    min: f32,
    max: f32,
    default: f32,
    label: &str,
    tooltip: &str,
    format_value: impl Fn(f32) -> String,
    diameter: f32,
    core_color: egui::Color32,
) -> KnobResponse {
    knob_inner(
        ui,
        id,
        value,
        min,
        max,
        default,
        label,
        tooltip,
        format_value,
        diameter,
        core_color,
        true,
    )
}

#[allow(clippy::too_many_arguments)]
fn knob_inner(
    ui: &mut egui::Ui,
    id: egui::Id,
    value: &mut f32,
    min: f32,
    max: f32,
    default: f32,
    label: &str,
    tooltip: &str,
    format_value: impl Fn(f32) -> String,
    diameter: f32,
    core_color: egui::Color32,
    compact: bool,
) -> KnobResponse {
    let mut result = KnobResponse {
        changed: false,
        reset: false,
        response: None,
    };

    // Compact knobs need 8 px of padding (was 4) so the new tick-dot zone
    // at `radius + 2.5` stays inside `painter_at(rect)`'s clip rect for
    // the 18 px small knobs (rect = diameter+8 = 26 → centre-to-edge 13,
    // dots at radius+2.5 = 11.5 → safe). column_w stays >= 30 so labels
    // still don't wrap.
    let box_pad = if compact { 8.0 } else { 12.0 };
    let label_gap = if compact { 0.0 } else { 3.0 };
    let total = diameter + box_pad;
    // Compact mode keeps the knob box visually tight (≈ diameter + 4) but
    // widens the surrounding column to ≥ 30 px so 3- and 4-character
    // labels render on a single line without wrapping. The knob is then
    // centred horizontally inside the wider column.
    let column_w = if compact {
        (diameter + 12.0).max(total)
    } else {
        total
    };

    ui.vertical(|ui| {
        ui.set_width(column_w);
        if compact {
            // Without this, the parent's `item_spacing.y` (typically 4 px in
            // a stacked sub-row cluster) leaks into this inner vertical and
            // adds an unwanted gap between the knob box and the label.
            ui.spacing_mut().item_spacing.y = 0.0;
        }

        let size = egui::vec2(total, total);
        let knob_alloc = ui
            .allocate_ui_with_layout(
                egui::vec2(column_w, total),
                egui::Layout::top_down(egui::Align::Center),
                |ui| ui.allocate_exact_size(size, egui::Sense::click_and_drag()),
            )
            .inner;
        let (rect, response) = knob_alloc;
        let mut response = response.on_hover_cursor(egui::CursorIcon::ResizeVertical);
        if !tooltip.is_empty() {
            response = response.on_hover_text(tooltip);
        }
        // Hand the inner knob rect's response back to the caller so they
        // can attach `.context_menu()` (used by MIDI Learn). Cloned out of
        // the `ui.vertical` closure scope so the outer caller can use it
        // after the closure returns.
        result.response = Some(response.clone());

        // Ctrl+click or double-click to reset.
        // Note: response.double_clicked() is unreliable under baseview (raw
        // mouse events, no synthesised egui double-click). We track the last
        // click time ourselves using per-widget temp storage keyed by `id`.
        let ctrl_click = response.clicked() && ui.input(|i| i.modifiers.ctrl);
        let is_double = if response.clicked() {
            let now: f64 = ui.input(|i| i.time);
            let last: f64 = ui
                .ctx()
                .data(|d| d.get_temp(id).unwrap_or(f64::NEG_INFINITY));
            ui.ctx().data_mut(|d| d.insert_temp(id, now));
            (now - last) < 0.35
        } else {
            false
        };
        if ctrl_click || is_double {
            *value = default;
            result.changed = true;
            result.reset = true;
        }

        // Vertical drag
        if response.dragged() {
            let delta = -response.drag_delta().y;
            let speed = if ui.input(|i| i.modifiers.shift) {
                0.001
            } else {
                0.005
            };
            *value = (*value + delta * speed * (max - min)).clamp(min, max);
            result.changed = true;
        }

        // Paint
        if ui.is_rect_visible(rect) {
            let painter = ui.painter_at(rect);
            let center = rect.center();
            let radius = diameter / 2.0;
            let norm = ((*value - min) / (max - min)).clamp(0.0, 1.0);

            // Cap occupies the inner 66% of the knob; the knurled rubber
            // rim is the annulus from there out to the edge.
            let cap_r = radius * 0.66;

            // 1. Drop shadow — seats the knob on the plate.
            painter.circle_filled(center + egui::vec2(0.0, 1.5), radius + 1.0, theme::KNOB_DROP);

            // 2. Round body silhouette (fills the inter-tooth gaps so the
            //    outer edge reads as a clean circle, not a 40-gon).
            painter.circle_filled(center, radius, theme::KNOB_BODY);

            // 3. Knurled rubber rim: 20 even teeth (40 alternating wedges).
            //    40 segments = 9° each → seamless at the 0/360 wrap, so
            //    there's no doubled-width dark tooth at 12 o'clock. Built as
            //    one mesh of colour-per-vertex quads (cheap, single draw).
            {
                let segs = 40usize;
                let r_in = cap_r * 0.92;
                let mut mesh = egui::Mesh::default();
                for i in 0..segs {
                    let a0 = std::f32::consts::TAU * (i as f32 / segs as f32);
                    let a1 = std::f32::consts::TAU * ((i + 1) as f32 / segs as f32);
                    let col = if i % 2 == 0 {
                        theme::KNURL_DARK
                    } else {
                        theme::KNURL_LIGHT
                    };
                    let b = mesh.vertices.len() as u32;
                    mesh.colored_vertex(center + egui::vec2(a0.cos(), a0.sin()) * r_in, col);
                    mesh.colored_vertex(center + egui::vec2(a0.cos(), a0.sin()) * radius, col);
                    mesh.colored_vertex(center + egui::vec2(a1.cos(), a1.sin()) * radius, col);
                    mesh.colored_vertex(center + egui::vec2(a1.cos(), a1.sin()) * r_in, col);
                    mesh.add_triangle(b, b + 1, b + 2);
                    mesh.add_triangle(b, b + 2, b + 3);
                }
                painter.add(egui::Shape::mesh(mesh));
            }

            // 4. Crisp dark outer rim line.
            painter.circle_stroke(
                center,
                radius,
                egui::Stroke::new(1.0, egui::Color32::from_rgba_premultiplied(0, 0, 0, 0xb0)),
            );

            // 5. Cap — saturated radial gradient. Triangle fan: apex (the
            //    highlight point, nudged up) is the pure section colour;
            //    the rim ring is a darkened shade. The GPU interpolates a
            //    smooth gradient between them, so the colour stays punchy
            //    in the middle and falls off to a moulded edge.
            let cap_outer = darken(core_color, 0.5);
            {
                let n = 48usize;
                let apex = center - egui::vec2(0.0, cap_r * 0.25);
                let mut mesh = egui::Mesh::default();
                mesh.colored_vertex(apex, core_color);
                for i in 0..=n {
                    let a = std::f32::consts::TAU * (i as f32 / n as f32);
                    mesh.colored_vertex(center + egui::vec2(a.cos(), a.sin()) * cap_r, cap_outer);
                }
                for i in 0..n as u32 {
                    mesh.add_triangle(0, 1 + i, 2 + i);
                }
                painter.add(egui::Shape::mesh(mesh));
            }

            // 6. Soft glossy sheen near the top of the cap — premultiplied
            //    white fading to transparent. Gives the rubber life without
            //    a wet/glossy plastic look.
            {
                let n = 32usize;
                let sheen_r = cap_r * 0.72;
                let sheen_c = center - egui::vec2(0.0, cap_r * 0.34);
                let sheen = egui::Color32::from_rgba_premultiplied(0x60, 0x60, 0x60, 0x60);
                let mut mesh = egui::Mesh::default();
                mesh.colored_vertex(sheen_c, sheen);
                for i in 0..=n {
                    let a = std::f32::consts::TAU * (i as f32 / n as f32);
                    mesh.colored_vertex(
                        sheen_c + egui::vec2(a.cos(), a.sin()) * sheen_r,
                        egui::Color32::TRANSPARENT,
                    );
                }
                for i in 0..n as u32 {
                    mesh.add_triangle(0, 1 + i, 2 + i);
                }
                painter.add(egui::Shape::mesh(mesh));
            }

            // 7. 1-px dark ring at the cap edge — seats the cap in the rim.
            painter.circle_stroke(
                center,
                cap_r,
                egui::Stroke::new(1.0, egui::Color32::from_rgba_premultiplied(0, 0, 0, 0x80)),
            );

            // 8. Indicator — pure white bar with a black outline, tip
            //    reaching the outer edge of the knurl. The ONLY element that
            //    rotates with the value (same 135°→405°, 270° sweep as before).
            let start_angle = std::f32::consts::PI * 0.75;
            let sweep_range = std::f32::consts::PI * 1.5;
            let angle = start_angle + sweep_range * norm;
            let dir = egui::vec2(angle.cos(), angle.sin());
            let perp = egui::vec2(-dir.y, dir.x);
            let r_out = radius * 0.96;
            let r_in = radius * 0.28;
            let hw = (radius * 0.08).max(1.0);
            let bar = |ro: f32, ri: f32, w: f32| {
                vec![
                    center + dir * ro + perp * w,
                    center + dir * ro - perp * w,
                    center + dir * ri - perp * w,
                    center + dir * ri + perp * w,
                ]
            };
            // Black outline (slightly longer + wider) underneath…
            painter.add(egui::Shape::convex_polygon(
                bar(r_out + 1.0, r_in - 1.0, hw + 1.0),
                egui::Color32::BLACK,
                egui::Stroke::NONE,
            ));
            // …pure-white core on top.
            painter.add(egui::Shape::convex_polygon(
                bar(r_out, r_in, hw),
                egui::Color32::WHITE,
                egui::Stroke::NONE,
            ));

            // 8. Write value to display when hovered/dragged. The expiry
            // timestamp lets the OUTPUT display linger on the most-recent
            // readout for ~500 ms after the user stops interacting, so
            // tweaking a knob and releasing doesn't blink the value off
            // immediately. Reader side (panels.rs) checks the expiry
            // before rendering and schedules a repaint when it lapses.
            if response.hovered() || response.dragged() {
                let display_text = format!("{} {}", label, format_value(*value));
                let expires_at = std::time::Instant::now() + std::time::Duration::from_millis(500);
                ui.ctx().data_mut(|d| {
                    d.insert_temp(egui::Id::new("knob_display"), display_text);
                    d.insert_temp::<std::time::Instant>(
                        egui::Id::new("knob_display_expires"),
                        expires_at,
                    );
                });
            }
        }

        // Label below — allocate the space with an invisible galley so the
        // knob-row geometry is unchanged, then paint it with a dark outline
        // (legible over the distressed plate's light paint-chips).
        ui.add_space(label_gap);
        ui.with_layout(egui::Layout::top_down(egui::Align::Center), |ui| {
            let font = egui::FontId::new(9.5, egui::FontFamily::Monospace);
            let resp = ui.label(
                egui::RichText::new(label)
                    .font(font.clone())
                    .color(egui::Color32::TRANSPARENT),
            );
            crate::ui::widgets::outlined_text(
                ui.painter(),
                resp.rect.center_top(),
                egui::Align2::CENTER_TOP,
                label,
                font,
                theme::WHITE,
            );
        });
    });

    result
}
