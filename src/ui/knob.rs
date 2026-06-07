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

            // Photoreal path — soft contact shadow (untinted) + neutral
            // matte-plastic cap multiply-tinted by the section colour. Both
            // baked offline under one ortho studio rig (the knob disk fills
            // 110/128 of the frame) so they share a single dest rect; the
            // bake carries all the lighting, the inner ring, and the soft
            // edge. Matte shading scales linearly with albedo, so tinting the
            // neutral bake equals rendering that colour directly. Falls back
            // to the procedural draw below only if the bake failed to load.
            let baked =
                crate::ui::widgets::KNOB_CAP_BAKED.load(std::sync::atomic::Ordering::Relaxed);
            let cap_handle = if baked {
                crate::ui::widgets::knob_cap_handle(ui.ctx())
            } else {
                None
            };
            if let Some(cap) = cap_handle {
                let cap_w = diameter * (128.0 / 110.0);
                let dst = egui::Rect::from_center_size(center, egui::vec2(cap_w, cap_w));
                let uv = egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0));
                // Soft drop shadow — offset toward the LOWER-RIGHT to agree
                // with the cap's own lighting (key is upper-left, so the cap
                // highlight sits upper-left and the cast shadow falls
                // lower-right). Drawn ONCE: the bake is a soft contact shadow,
                // darkest near the knob and fading to nothing as it spreads, so
                // a single blit reads as a soft drop shadow (drawing it twice
                // hardened the core).
                if let Some(shadow) = crate::ui::widgets::knob_shadow_handle(ui.ctx()) {
                    let sh = egui::Rect::from_center_size(
                        center + egui::vec2(radius * 0.06, radius * 0.07),
                        egui::vec2(cap_w, cap_w),
                    );
                    painter.image(shadow.id(), sh, uv, egui::Color32::WHITE);
                }
                // Position-based lighting: lay the faceplate's soft macro
                // gradient over the bake's (identical) form lighting. The
                // chassis key is upper-left, so a knob's overall brightness
                // tracks where it sits on the plate — upper-left a touch
                // brighter, lower-right a touch dimmer. Not identical per knob,
                // but a gentle gradient like the plate. Derived analytically
                // (not sampled from chassis.png) so the dark display/vents
                // don't drag nearby knobs down.
                let sr = ui.ctx().screen_rect();
                let gx = ((center.x - sr.left()) / sr.width().max(1.0)).clamp(0.0, 1.0);
                let gy = ((center.y - sr.top()) / sr.height().max(1.0)).clamp(0.0, 1.0);
                let face = 1.0 + 0.12 * (0.5 - 0.5 * (gx + gy));
                let tint = egui::Color32::from_rgb(
                    (core_color.r() as f32 * face).min(255.0) as u8,
                    (core_color.g() as f32 * face).min(255.0) as u8,
                    (core_color.b() as f32 * face).min(255.0) as u8,
                );
                painter.image(cap.id(), dst, uv, tint);

                // Per-knob wear: overlay the neutral scratch/patina/edge-wear
                // texture rotated + scaled (+ maybe mirrored) by a hash of this
                // knob's id, so every knob looks slightly unique. The marks are
                // neutral light/dark, so rotating them does NOT spin the baked
                // directional lighting. Overall strength = the overlay alpha.
                if let Some(wear) = crate::ui::widgets::knob_wear_handle(ui.ctx()) {
                    use std::hash::{Hash, Hasher};
                    let mut hsh = std::collections::hash_map::DefaultHasher::new();
                    id.hash(&mut hsh);
                    let hv = hsh.finish();
                    let ang = (hv & 0xffff) as f32 / 65535.0 * std::f32::consts::TAU;
                    let scale = 0.94 + ((hv >> 16) & 0xff) as f32 / 255.0 * 0.06;
                    let mirror = (hv >> 24) & 1 == 1;
                    let (s, c) = ang.sin_cos();
                    let hwd = cap_w * 0.5 * scale;
                    let mut uvs = [
                        egui::pos2(0.0, 0.0),
                        egui::pos2(1.0, 0.0),
                        egui::pos2(1.0, 1.0),
                        egui::pos2(0.0, 1.0),
                    ];
                    if mirror {
                        uvs.swap(0, 1);
                        uvs.swap(2, 3);
                    }
                    let corners = [
                        egui::vec2(-hwd, -hwd),
                        egui::vec2(hwd, -hwd),
                        egui::vec2(hwd, hwd),
                        egui::vec2(-hwd, hwd),
                    ];
                    // Overall wear opacity — tune here (120/255 ≈ 0.47).
                    let col = egui::Color32::from_white_alpha(120);
                    let mut mesh = egui::Mesh::with_texture(wear.id());
                    for k in 0..4 {
                        let v = corners[k];
                        let rp = egui::vec2(v.x * c - v.y * s, v.x * s + v.y * c);
                        mesh.vertices.push(egui::epaint::Vertex {
                            pos: center + rp,
                            uv: uvs[k],
                            color: col,
                        });
                    }
                    mesh.indices.extend_from_slice(&[0, 1, 2, 0, 2, 3]);
                    painter.add(egui::Shape::mesh(mesh));
                }
            } else {
                // Cap occupies the inner 66% of the knob; the knurled rubber
                // rim is the annulus from there out to the edge.
                let cap_r = radius * 0.66;

                // 1. Drop shadow — seats the knob on the plate.
                painter.circle_filled(
                    center + egui::vec2(0.0, 1.5),
                    radius + 1.0,
                    theme::KNOB_DROP,
                );

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
                        mesh.colored_vertex(
                            center + egui::vec2(a.cos(), a.sin()) * cap_r,
                            cap_outer,
                        );
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
            }

            // 8. Indicator — filled bone-white pointer: rounded at the inner
            //    (centre) end, its outer corners riding the knob's own arc so
            //    the tip follows the knob curve, slightly tapered. Drawn as one
            //    convex polygon so egui anti-aliases the edges (a raw mesh
            //    would not). No outline. The ONLY element that rotates
            //    (135°→405°, 270°).
            let start_angle = std::f32::consts::PI * 0.75;
            let sweep_range = std::f32::consts::PI * 1.5;
            let angle = start_angle + sweep_range * norm;
            let dir = egui::vec2(angle.cos(), angle.sin());
            let perp = egui::vec2(-dir.y, dir.x);
            let r0 = radius * 0.30; // inner (rounded) end
            let r1 = radius * 0.93; // outer end, sitting on the knob arc
            let w_out = (radius * 0.17).max(2.4); // ~2× chunkier pointer
            let w_in = w_out * 0.62;
            let ic = center + dir * r0; // inner-cap centre
            let th1 = (w_out / r1).clamp(0.0, 0.4).asin(); // outer angular half-width
            let mut pts: Vec<egui::Pos2> = Vec::with_capacity(18);
            // inner-left tangent → left side up to the outer arc
            pts.push(ic + perp * w_in);
            // outer arc, left corner (+th1) sweeping to right corner (-th1): the
            // corners sit on radius r1 so the tip conforms to the knob curve.
            let arc_n = 6;
            for i in 0..=arc_n {
                let a = angle + th1 - 2.0 * th1 * (i as f32 / arc_n as f32);
                pts.push(center + egui::vec2(a.cos(), a.sin()) * r1);
            }
            // right side down to inner-right tangent
            pts.push(ic - perp * w_in);
            // rounded inner cap: right tangent (φ=−90°) through the centre-
            // facing tip (φ=0 → −dir) back toward the left tangent (φ=+90°).
            let cap_n = 8;
            for i in 1..cap_n {
                let phi =
                    std::f32::consts::PI * (i as f32 / cap_n as f32) - std::f32::consts::FRAC_PI_2;
                pts.push(ic + (-dir * phi.cos() + perp * phi.sin()) * w_in);
            }
            painter.add(egui::Shape::convex_polygon(
                pts,
                theme::WHITE,
                // Thin GREY border (not black) — defines the pointer on light
                // caps without the harsh drawn-on look a black outline gives.
                egui::Stroke::new(0.8, egui::Color32::from_gray(0x66)),
            ));

            // Bone-coloured tick dots around the FULL sweep (min -> max),
            // restored from the old GUI — 11 evenly-spaced markers on the plate
            // just outside the knob; the moving pointer kisses one when aligned.
            let dot_center_r = radius + 2.5;
            let dot_radius = (0.75 * (radius / 16.0).sqrt()).max(0.45);
            let dot_n = 10;
            for i in 0..=dot_n {
                let t = i as f32 / dot_n as f32; // min -> max, all the way around
                let a = start_angle + sweep_range * t;
                painter.circle_filled(
                    center + egui::vec2(a.cos(), a.sin()) * dot_center_r,
                    dot_radius,
                    theme::WHITE,
                );
            }

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
