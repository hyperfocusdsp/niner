"""Render Niner's chassis as a photorealistic baked PNG.

Iteration progression:
  0 — flat plate emission (byte-equivalent to procedural BG_PANEL).
  1 — geometry (this iter): screws, rack ears, vents. Neutral PBR placeholder.
  2 — bezel insets (OUTPUT/COMP), section grooves, edge-band step.
  3 — three-light studio rig + hammertone material with Voronoi
       displacement+roughness modulation (per refs/reference_hammertone_finish.png).

Invocation:
    blender --background --python render_chassis.py -- --preset <preset.json> [--samples N]

Coordinate convention: 1 Blender unit = 1 logical pixel of the Niner canvas.
Plate centre at world origin. World +X = canvas right; world +Y = canvas top
(opposite of Rust pixel-Y, which grows downward — see `rust_to_world` below).
World +Z points toward the camera.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

import bpy
import mathutils


# ---------------------------------------------------------------------------
# CLI args (after the `--`)
# ---------------------------------------------------------------------------
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--preset", required=True)
parser.add_argument("--samples", type=int, default=None,
                    help="Override preset.render.samples (small N for fast iteration)")
args = parser.parse_args(argv)

preset_path = Path(args.preset).resolve()
preset = json.loads(preset_path.read_text())
preset_name = preset_path.stem

script_dir = Path(__file__).resolve().parent
blender_root = script_dir.parent
repo_root = blender_root.parent.parent
output_dir = blender_root / "output" / preset_name
output_dir.mkdir(parents=True, exist_ok=True)
assets_dir = repo_root / "assets"


# ---------------------------------------------------------------------------
# Scene reset
# ---------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------------------------------------------------------------------------
# Canvas + coord helper
# ---------------------------------------------------------------------------
canvas_w = float(preset["canvas"]["width"])
canvas_h = float(preset["canvas"]["height"])


def rust_to_world(x: float, y: float) -> tuple[float, float]:
    """Convert Rust pixel coord (top-left origin, +Y down) to Blender world
    coord (centre origin, +Y up). Z is provided by the caller."""
    return (x - canvas_w / 2.0, canvas_h / 2.0 - y)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
def srgb_to_linear(c: float) -> float:
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def make_pbr_material(name: str, base_color, metallic: float, roughness: float,
                      coat_weight: float = 0.0, coat_roughness: float = 0.0):
    """Plain Principled BSDF — used for screws and other simple parts."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    for w_name in ("Coat Weight", "Clearcoat"):
        if w_name in bsdf.inputs:
            bsdf.inputs[w_name].default_value = coat_weight
            break
    for r_name in ("Coat Roughness", "Clearcoat Roughness"):
        if r_name in bsdf.inputs:
            bsdf.inputs[r_name].default_value = coat_roughness
            break
    return mat


def make_hammertone_material(name: str, voronoi_scale: float = 70.0,
                              bump_strength: float = 0.55,
                              base_color=(0.12, 0.10, 0.08),
                              metallic: float = 0.55,
                              rough_min: float = 0.37, rough_max: float = 0.73,
                              uv_warp: float = 0.04):
    """Approach D from the research: 2D Voronoi (UV-driven) feeds both a
    Bump node (normal modulation) and a Map Range node (roughness band).
    Optional UV warp via low-frequency Noise Texture for organic cell
    irregularity. Produces the hammertone / textured-spray finish from
    refs/reference_hammertone_finish.png.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    coord = nt.nodes.new("ShaderNodeTexCoord")
    voro = nt.nodes.new("ShaderNodeTexVoronoi")
    bump = nt.nodes.new("ShaderNodeBump")
    remap = nt.nodes.new("ShaderNodeMapRange")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = nt.nodes.new("ShaderNodeOutputMaterial")

    voro.voronoi_dimensions = "2D"
    voro.feature = "F1"
    voro.inputs["Scale"].default_value = voronoi_scale
    voro.inputs["Randomness"].default_value = 1.0

    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = 1.0

    remap.inputs["From Min"].default_value = 0.0
    remap.inputs["From Max"].default_value = 1.0
    remap.inputs["To Min"].default_value = rough_min
    remap.inputs["To Max"].default_value = rough_max

    bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = 0.55  # overridden by remap link

    L = nt.links

    # UV path with optional noise warp
    if uv_warp > 0.0:
        warp = nt.nodes.new("ShaderNodeTexNoise")
        warp.inputs["Scale"].default_value = 8.0
        warp.inputs["Detail"].default_value = 2.0
        warp.inputs["Roughness"].default_value = 0.5
        if "Distortion" in warp.inputs:
            warp.inputs["Distortion"].default_value = 0.0
        mix = nt.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "ADD"
        mix.inputs["Fac"].default_value = uv_warp
        L.new(coord.outputs["UV"], warp.inputs["Vector"])
        L.new(coord.outputs["UV"], mix.inputs["Color1"])
        L.new(warp.outputs["Color"], mix.inputs["Color2"])
        L.new(mix.outputs["Color"], voro.inputs["Vector"])
    else:
        L.new(coord.outputs["UV"], voro.inputs["Vector"])

    # Bump path
    L.new(voro.outputs["Distance"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    # Roughness path — same Voronoi feeds both for physical correlation.
    L.new(voro.outputs["Distance"], remap.inputs["Value"])
    L.new(remap.outputs["Result"], bsdf.inputs["Roughness"])
    # Output
    L.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


# Production hammertone material — user-confirmed variant B (scale 70).
# Base color is dark warm near-black (slight bronze tint vs the original
# neutral #131313 procedural BG) per the user's reference image.
chassis_mat = make_hammertone_material(
    "chassis_hammertone",
    voronoi_scale=70.0,
    bump_strength=0.55,
    base_color=(0.12, 0.10, 0.08),
    metallic=0.55,
    rough_min=0.37,
    rough_max=0.73,
    uv_warp=0.04,
)

# Screws — brushed steel, slight cool tint to contrast the warm chassis.
screw_mat = make_pbr_material(
    "screw_steel",
    base_color=(0.18, 0.18, 0.20),
    metallic=0.85,
    roughness=0.30,
)

# World — very low ambient so grain shadow stays visible (research rec).
scene.world = bpy.data.worlds.new("studio_world")
scene.world.use_nodes = True
bg_node = scene.world.node_tree.nodes["Background"]
bg_node.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
bg_node.inputs["Strength"].default_value = 0.03


# ---------------------------------------------------------------------------
# Front plate — thick cube so boolean depressions work cleanly
# ---------------------------------------------------------------------------
PLATE_THICKNESS = 4.0   # 4 BU = ~4 logical px deep behind the front face

def add_chassis_plate() -> bpy.types.Object:
    """Front plate sized to the canvas, with PLATE_THICKNESS depth so we can
    boolean-subtract bezel depressions and groove cuts into the front face.
    The front face sits at z=0; the back is at z=-PLATE_THICKNESS."""
    bpy.ops.mesh.primitive_cube_add(
        size=1,
        location=(0, 0, -PLATE_THICKNESS / 2.0),
    )
    plate = bpy.context.active_object
    plate.name = "chassis_plate"
    plate.scale = (canvas_w, canvas_h, PLATE_THICKNESS)
    bpy.ops.object.transform_apply(scale=True)
    plate.data.materials.append(chassis_mat)
    return plate


def boolean_subtract(target: bpy.types.Object, cutter: bpy.types.Object,
                     name: str | None = None):
    """Apply a boolean DIFFERENCE modifier and remove the cutter."""
    bpy.context.view_layer.objects.active = target
    mod_name = name or f"diff_{cutter.name}"
    mod = target.modifiers.new(name=mod_name, type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=mod_name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def make_box_cutter(rust_x: float, rust_y: float, rust_w: float, rust_h: float,
                    z_centre: float, z_thickness: float, name: str) -> bpy.types.Object:
    """Create an axis-aligned box cutter at a given Rust pixel rect, centred
    at world Z = z_centre, thickness = z_thickness."""
    centre_rust_x = rust_x + rust_w / 2.0
    centre_rust_y = rust_y + rust_h / 2.0
    cx = centre_rust_x - canvas_w / 2.0
    cy = canvas_h / 2.0 - centre_rust_y
    bpy.ops.mesh.primitive_cube_add(size=1, location=(cx, cy, z_centre))
    cutter = bpy.context.active_object
    cutter.name = name
    cutter.scale = (rust_w, rust_h, z_thickness)
    bpy.ops.object.transform_apply(scale=True)
    return cutter


plate = add_chassis_plate()


# ---------------------------------------------------------------------------
# Bezel insets (OUTPUT + COMP displays) — boolean depressions in plate
# ---------------------------------------------------------------------------
def add_bezel_inset(bezel_cfg: dict, frame_padding: float, depth: float,
                    label: str):
    """Cut a rectangular depression into the front plate so the lit display
    sits inside a real bevel-walled frame."""
    lit_x = float(bezel_cfg["x"])
    lit_y = float(bezel_cfg["y"])
    lit_w = float(bezel_cfg["w"])
    lit_h = float(bezel_cfg["h"])
    # Outer bezel rect = lit rect expanded by frame_padding on all sides.
    outer_x = lit_x - frame_padding
    outer_y = lit_y - frame_padding
    outer_w = lit_w + frame_padding * 2.0
    outer_h = lit_h + frame_padding * 2.0
    # Cutter: thin slab centred at z = -depth/2, thickness depth*2.5 so it
    # cleanly punches the front face but doesn't clip the back.
    cutter = make_box_cutter(
        outer_x, outer_y, outer_w, outer_h,
        z_centre=-depth / 2.0,
        z_thickness=depth * 2.5,
        name=f"bezel_cutter_{label}",
    )
    boolean_subtract(plate, cutter, name=f"bezel_inset_{label}")


bezels = preset["bezels"]
frame_padding = float(bezels.get("frame_padding", 4.0))
extrusion_depth = float(bezels.get("extrusion_depth", 0.8))
add_bezel_inset(bezels["output"], frame_padding, extrusion_depth, "output")
add_bezel_inset(bezels["comp"], frame_padding, extrusion_depth, "comp")


# ---------------------------------------------------------------------------
# Section-row grooves — thin shallow cuts across the panel width
# ---------------------------------------------------------------------------
def add_groove(rust_y: float, depth: float, thickness: float):
    rack_ear_w = float(preset["layout"]["rack_ear_w"])
    # Span the full width between rack ears, plus a small overlap so the
    # groove visually carries through the ear edges.
    groove_x = rack_ear_w
    groove_w = canvas_w - rack_ear_w * 2.0
    cutter = make_box_cutter(
        groove_x,
        rust_y - thickness / 2.0,
        groove_w,
        thickness,
        z_centre=-depth / 2.0,
        z_thickness=depth * 2.5,
        name=f"groove_cutter_{int(rust_y)}",
    )
    boolean_subtract(plate, cutter, name=f"groove_{int(rust_y)}")


grooves = preset.get("grooves", {})
for groove_y in grooves.get("y_positions", []):
    add_groove(
        float(groove_y),
        float(grooves.get("depth_px", 0.4)),
        float(grooves.get("thickness_px", 1.5)),
    )


# ---------------------------------------------------------------------------
# Edge-band step — top + bottom 12 px bands recessed for seam shadow
# ---------------------------------------------------------------------------
edge = preset.get("edge_band", {})
edge_h = float(edge.get("height_px", 12.0))
edge_step = float(edge.get("step_px", 0.4))
if edge_step > 0.0:
    # Recess top band
    cutter_top = make_box_cutter(
        0.0, 0.0, canvas_w, edge_h,
        z_centre=-edge_step / 2.0,
        z_thickness=edge_step * 2.5,
        name="edge_band_top_cutter",
    )
    boolean_subtract(plate, cutter_top, name="edge_band_top")
    # Recess bottom band
    cutter_bot = make_box_cutter(
        0.0, canvas_h - edge_h, canvas_w, edge_h,
        z_centre=-edge_step / 2.0,
        z_thickness=edge_step * 2.5,
        name="edge_band_bot_cutter",
    )
    boolean_subtract(plate, cutter_bot, name="edge_band_bot")


# Soften all the new boolean edges with a small bevel.
bpy.context.view_layer.objects.active = plate
plate_bevel = plate.modifiers.new(name="plate_bevel", type="BEVEL")
plate_bevel.width = 0.25
plate_bevel.segments = 2
plate_bevel.limit_method = "ANGLE"
plate_bevel.angle_limit = math.radians(30)
bpy.ops.object.modifier_apply(modifier="plate_bevel")


# ---------------------------------------------------------------------------
# Rack ears + vent cutouts (boolean difference)
# ---------------------------------------------------------------------------
def add_rack_ear(side: str) -> bpy.types.Object:
    """A slightly raised rack-ear plate on the left or right edge of the
    chassis with 8 vent slots cut through. `side` is 'left' or 'right'.

    Mirrors `widgets.rs::draw_rack_ear`: ear is `RACK_EAR_W` wide, full
    canvas height, 8 vertical slots of 8×22 px starting at y=ear_top+35
    with 44 px spacing, centred horizontally in the ear.
    """
    layout = preset["layout"]
    vents = preset["vents"]
    ear_w = float(layout["rack_ear_w"])
    n_slots = int(vents["slots_per_ear"])
    slot_w = float(vents["slot_w"])
    slot_h = float(vents["slot_h"])
    first_y_off = float(vents["first_slot_y_offset"])
    spacing = float(vents["slot_spacing"])

    # Rust rect of the ear
    if side == "left":
        ear_rust_x = 0.0
    else:
        ear_rust_x = canvas_w - ear_w
    ear_rust_y = 0.0
    ear_rust_h = canvas_h

    # World rect (centre + size)
    ear_cx = ear_rust_x + ear_w / 2.0 - canvas_w / 2.0
    ear_cy = canvas_h / 2.0 - (ear_rust_y + ear_rust_h / 2.0)
    ear_z = 0.4  # 0.4 BU forward of the front plate (=0.4 px in screen-units)

    # Build the ear plate with a small thickness so it casts shadow.
    bpy.ops.mesh.primitive_cube_add(size=1, location=(ear_cx, ear_cy, ear_z / 2.0))
    ear = bpy.context.active_object
    ear.name = f"rack_ear_{side}"
    ear.scale = (ear_w, ear_rust_h, ear_z)
    bpy.ops.object.transform_apply(scale=True)
    ear.data.materials.append(chassis_mat)

    # Vent cutters — boolean DIFFERENCE.
    cutters = []
    slot_centre_rust_x = ear_rust_x + ear_w / 2.0
    for i in range(n_slots):
        slot_rust_y = first_y_off + i * spacing + slot_h / 2.0
        if slot_rust_y + slot_h / 2.0 > ear_rust_h:
            break
        slot_world_x = slot_centre_rust_x - canvas_w / 2.0
        slot_world_y = canvas_h / 2.0 - slot_rust_y
        bpy.ops.mesh.primitive_cube_add(
            size=1,
            location=(slot_world_x, slot_world_y, ear_z / 2.0),
        )
        cutter = bpy.context.active_object
        cutter.name = f"vent_cutter_{side}_{i}"
        cutter.scale = (slot_w, slot_h, ear_z * 4.0)  # thicker than ear → clean cut
        bpy.ops.object.transform_apply(scale=True)
        cutters.append(cutter)

    # Apply boolean difference for each cutter.
    bpy.context.view_layer.objects.active = ear
    for cutter in cutters:
        mod = ear.modifiers.new(name=cutter.name, type="BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Clean up the cutter helpers.
    for cutter in cutters:
        bpy.data.objects.remove(cutter, do_unlink=True)

    # Edge bevel on the ear — softens the silhouette so it casts a real
    # shadow rather than a hard line.
    bpy.context.view_layer.objects.active = ear
    bevel = ear.modifiers.new(name="ear_bevel", type="BEVEL")
    bevel.width = 0.4
    bevel.segments = 2
    bevel.limit_method = "ANGLE"
    bevel.angle_limit = math.radians(30)
    bpy.ops.object.modifier_apply(modifier="ear_bevel")

    return ear


add_rack_ear("left")
add_rack_ear("right")


# ---------------------------------------------------------------------------
# Phillips screws — 4 corners
# ---------------------------------------------------------------------------
def add_phillips_screw(rust_x: float, rust_y: float, radius: float) -> bpy.types.Object:
    """Phillips-style screw head: cylinder + beveled top edge + cross slot.

    Iter 1 uses a simple disc with a beveled top — detailed cross-slot
    geometry can land in iter 2. The current draw_screw procedural version
    fakes a hex socket with 6 dots, so a clean disc + bevel is already an
    upgrade in fidelity. Cross slot can come if/when needed.
    """
    cx, cy = rust_to_world(rust_x, rust_y)
    height = 0.8  # 0.8 BU = 0.8 px proud of the plate; small but real

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48,
        radius=radius,
        depth=height,
        location=(cx, cy, height / 2.0),
    )
    screw = bpy.context.active_object
    screw.name = f"screw_{int(rust_x)}_{int(rust_y)}"

    # Bevel the top rim — 30° angle limit catches just the top circular edge.
    bevel = screw.modifiers.new(name="rim_bevel", type="BEVEL")
    bevel.width = radius * 0.18
    bevel.segments = 3
    bevel.limit_method = "ANGLE"
    bevel.angle_limit = math.radians(30)
    bpy.ops.object.modifier_apply(modifier="rim_bevel")

    # Cross slot — two thin cuboids subtracted from the top.
    slot_depth = height * 0.5
    slot_w = radius * 0.18
    slot_l = radius * 1.5

    cutters = []
    for axis in ("x", "y"):
        bpy.ops.mesh.primitive_cube_add(
            size=1,
            location=(cx, cy, height - slot_depth / 2.0),
        )
        cutter = bpy.context.active_object
        if axis == "x":
            cutter.scale = (slot_l, slot_w, slot_depth * 2.0)
        else:
            cutter.scale = (slot_w, slot_l, slot_depth * 2.0)
        bpy.ops.object.transform_apply(scale=True)
        cutters.append(cutter)

    bpy.context.view_layer.objects.active = screw
    for cutter in cutters:
        mod = screw.modifiers.new(name=f"slot_{cutter.name}", type="BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        bpy.ops.object.modifier_apply(modifier=mod.name)
    for cutter in cutters:
        bpy.data.objects.remove(cutter, do_unlink=True)

    screw.data.materials.append(screw_mat)
    return screw


screws_cfg = preset["screws"]
screw_radius = float(screws_cfg["radius"])
for sx, sy in screws_cfg["positions"]:
    add_phillips_screw(float(sx), float(sy), screw_radius)


# ---------------------------------------------------------------------------
# Camera — orthographic, 1 BU per logical pixel
# ---------------------------------------------------------------------------
def add_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0, 0, 1000), rotation=(0, 0, 0))
    cam = bpy.context.active_object
    cam.name = "chassis_camera"
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = canvas_w
    cam.data.clip_start = 1.0
    cam.data.clip_end = 5000.0
    scene.camera = cam
    return cam


add_camera()


# ---------------------------------------------------------------------------
# Lighting — three-light studio rig (research-recommended energies).
# Key/fill/rim with proper Watts (~1000× the original preset). The dark
# hammertone material at base 0.10 absorbs ~90% of incident light, so the
# huge energy values land at a normal product-shot brightness on the bake.
# ---------------------------------------------------------------------------
def add_studio_light(name: str, position, size_w: float, size_h: float,
                     energy: float, color):
    data = bpy.data.lights.new(name, type="AREA")
    data.shape = "RECTANGLE"
    data.size = size_w
    data.size_y = size_h
    data.energy = energy
    data.color = color
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = mathutils.Vector(position)
    direction = mathutils.Vector((0, 0, 0)) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


# Key — upper-left, 35° elevation / 45° azimuth, large area for soft
# specular highlight, slightly warm (1.0, 0.96, 0.88).
add_studio_light(
    "Key",
    position=(-520.0, 460.0, 780.0),
    size_w=600.0, size_h=400.0,
    energy=850_000.0,
    color=(1.0, 0.96, 0.88),
)
# Fill — lower-right, shallow angle, large + dim, slightly cool to
# complement key.
add_studio_light(
    "Fill",
    position=(480.0, -420.0, 500.0),
    size_w=800.0, size_h=500.0,
    energy=160_000.0,
    color=(0.88, 0.92, 1.0),
)
# Rim — narrow strip behind the panel (negative Z), grazing top-right
# edge to define the chassis silhouette.
add_studio_light(
    "Rim",
    position=(420.0, 300.0, -350.0),
    size_w=80.0, size_h=600.0,
    energy=220_000.0,
    color=(1.0, 1.0, 1.0),
)


# ---------------------------------------------------------------------------
# Render settings
# ---------------------------------------------------------------------------
rcfg = preset["render"]
scale = int(rcfg.get("scale", 2))
samples = args.samples if args.samples is not None else int(rcfg.get("samples", 64))

scene.render.engine = rcfg.get("engine", "CYCLES")
if scene.render.engine == "CYCLES":
    scene.cycles.samples = samples
    scene.cycles.use_denoising = bool(rcfg.get("denoise", True))
    try:
        scene.cycles.denoiser = "OPTIX"
    except (TypeError, RuntimeError):
        scene.cycles.denoiser = "OPENIMAGEDENOISE"

scene.view_settings.view_transform = rcfg.get("view_transform", "Standard")
scene.view_settings.look = "None"

scene.render.resolution_x = int(canvas_w * scale)
scene.render.resolution_y = int(canvas_h * scale)
scene.render.resolution_percentage = 100

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.color_depth = "8"
scene.render.film_transparent = False

out_path = output_dir / "chassis.png"
scene.render.filepath = str(out_path)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
bpy.ops.render.render(write_still=True)
print(f"\n[render_chassis] Rendered: {out_path}")
print(f"[render_chassis]   resolution: {scene.render.resolution_x}x{scene.render.resolution_y}")
print(f"[render_chassis]   engine: {scene.render.engine}, samples: {samples}")

# Production preset (`chassis.json`) overwrites the bundled asset. Any
# other preset (e.g. `chassis_marketing.json`) leaves assets/ alone and
# stays in tools/blender/output/<preset>/ for hero shots / IG content.
if preset_name == "chassis":
    target = assets_dir / "chassis.png"
    shutil.copy2(out_path, target)

    # Re-encode with max PNG compression — Cycles' default writer leaves
    # ~24% on the table. Pixel-identical; reduces binary bloat. Skips if
    # ImageMagick `convert` isn't available.
    import subprocess
    if shutil.which("convert"):
        tmp = target.with_suffix(".tmp.png")
        subprocess.run(
            [
                "convert", str(target),
                "-strip",
                "-define", "png:compression-level=9",
                "-define", "png:compression-strategy=2",
                "-define", "png:exclude-chunks=all",
                str(tmp),
            ],
            check=True,
        )
        orig_size = target.stat().st_size
        new_size = tmp.stat().st_size
        if new_size < orig_size:
            tmp.replace(target)
            print(f"[render_chassis] Recompressed: {orig_size} → {new_size} bytes "
                  f"({(orig_size - new_size) * 100 // orig_size}% saved)")
        else:
            tmp.unlink()

    print(f"[render_chassis] Copied to: {target}")
else:
    print(f"[render_chassis] Non-production preset '{preset_name}' — "
          f"output stays in {out_path} (assets/chassis.png not touched)")
