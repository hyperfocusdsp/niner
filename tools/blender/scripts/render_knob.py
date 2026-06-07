"""Render the photoreal Niner knob under the chassis studio rig.

Two outputs, both RGBA with film_transparent so only the relevant pixels
are opaque:

  assets/knob_cap.png    — neutral light-grey plastic knob, lit. At runtime
                           egui's `painter.image(tex, rect, uv, tint)`
                           multiplies it by each section's `core_color`, so
                           one bake serves every section colour with
                           identical, physically-correct matte shading
                           (rendered_px = albedo * lighting; multiply-tint
                           reconstructs section_color * lighting exactly for
                           a diffuse surface).
  assets/knob_shadow.png — soft contact/drop shadow only (knob made
                           camera-invisible, a shadow-catcher plane beneath
                           catches the area-light penumbra). Drawn UNtinted
                           under the cap so the shadow stays neutral.

Geometry matches the nano-banana reference (tools/blender/refs/ai_knobs):
a flat-top plateau raised slightly above a ledge ring with a soft outer
bevel — the recessed ledge reads as the knob's inner ring.

Invocation:
    blender --background --python render_knob.py -- --preset <preset.json>
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import bpy
import mathutils


# ---- CLI / preset -----------------------------------------------------------
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--preset", required=True)
parser.add_argument("--samples", type=int, default=None)
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

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

canvas_w = float(preset["canvas"]["width"])
canvas_h = float(preset["canvas"]["height"])


# ---- material: neutral plastic ---------------------------------------------
def make_plastic_material():
    mat = bpy.data.materials.new("knob_plastic")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    cfg = preset["material"]
    bsdf.inputs["Base Color"].default_value = (*cfg["base_color_linear"], 1.0)
    bsdf.inputs["Metallic"].default_value = float(cfg.get("metallic", 0.05))
    bsdf.inputs["Roughness"].default_value = float(cfg.get("roughness", 0.55))
    if "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = float(cfg.get("coat_weight", 0.0))
    if "Coat Roughness" in bsdf.inputs:
        bsdf.inputs["Coat Roughness"].default_value = float(cfg.get("coat_roughness", 0.0))
    return mat


plastic = make_plastic_material()


# ---- geometry: flat-top plateau + ledge ring (two cylinders) ---------------
cap = preset["cap"]
radius = float(cap["radius_px"])
rim_h = float(cap.get("rim_height_px", 9.0))
rim_bevel = float(cap.get("rim_bevel_px", 8.0))
plateau_r = float(cap.get("plateau_radius_px", 90.0))
plateau_rise = float(cap.get("plateau_rise_px", 3.0))
plateau_h = float(cap.get("plateau_height_px", 6.0))
plateau_bevel = float(cap.get("plateau_bevel_px", 7.0))
bevel_segs = int(cap.get("rim_bevel_segments", 12))


def add_cylinder(name, r, depth, z_center, bevel_w):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=96, radius=r, depth=depth, location=(0.0, 0.0, z_center)
    )
    obj = bpy.context.active_object
    obj.name = name
    obj.data.materials.append(plastic)
    if bevel_w > 0.0:
        bev = obj.modifiers.new("bevel", type="BEVEL")
        bev.width = bevel_w
        bev.segments = bevel_segs
        bev.limit_method = "ANGLE"
        bev.angle_limit = math.radians(30)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier="bevel")
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


# Rim body: base disk, top face at z = rim_h, beveled outer edge.
rim = add_cylinder("knob_rim", radius, rim_h, rim_h / 2.0, rim_bevel)
# Plateau: narrower disk poking up through the rim top by `plateau_rise`,
# leaving a flat ledge ring (plateau_r .. radius) = the inner ring.
plateau_top = rim_h + plateau_rise
plateau_z = plateau_top - plateau_h / 2.0
plateau = add_cylinder("knob_plateau", plateau_r, plateau_h, plateau_z, plateau_bevel)
knob_objs = [rim, plateau]


# ---- camera: orthographic top-down, 1 BU = 1 px ----------------------------
def add_camera():
    bpy.ops.object.camera_add(location=(0.0, 0.0, 1000.0), rotation=(0.0, 0.0, 0.0))
    cam = bpy.context.active_object
    cam.name = "knob_camera"
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = canvas_w
    cam.data.clip_start = 1.0
    cam.data.clip_end = 5000.0
    scene.camera = cam
    return cam


add_camera()


# ---- studio lights ----------------------------------------------------------
def add_studio_light(name, position, size_w, size_h, energy, color):
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


for name in ("key", "fill", "rim"):
    cfg = preset["lights"][name]
    add_studio_light(
        name.title(),
        position=tuple(cfg["position"]),
        size_w=float(cfg["size_w"]),
        size_h=float(cfg["size_h"]),
        energy=float(cfg["energy"]),
        color=tuple(cfg["color"]),
    )

# Dim world so the contact shadow has a soft non-black floor (negligible on
# the cap, which is lit by the key/fill/rim rig).
world = bpy.data.worlds.new("knob_world")
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg is not None:
    amb = float(preset.get("world_ambient", 0.03))
    bg.inputs[0].default_value = (amb, amb, amb, 1.0)
    bg.inputs[1].default_value = 1.0
scene.world = world


# ---- render settings --------------------------------------------------------
rcfg = preset["render"]
scale = int(rcfg.get("scale", 1))
samples = args.samples if args.samples is not None else int(rcfg.get("samples", 128))

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
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.render.film_transparent = True


def recompress(target: Path) -> None:
    if not shutil.which("convert"):
        return
    tmp = target.with_suffix(".tmp.png")
    subprocess.run(
        ["convert", str(target), "-strip",
         "-define", "png:compression-level=9",
         "-define", "png:compression-strategy=2",
         "-define", "png:exclude-chunks=all", str(tmp)],
        check=True,
    )
    if tmp.stat().st_size < target.stat().st_size:
        tmp.replace(target)
    else:
        tmp.unlink()


# ---- pass 1: the lit neutral cap (no plane) --------------------------------
cap_out = output_dir / "knob_cap.png"
scene.render.filepath = str(cap_out)
bpy.ops.render.render(write_still=True)
print(f"\n[render_knob] cap: {cap_out}  {scene.render.resolution_x}x{scene.render.resolution_y}")

# ---- pass 2: soft contact shadow only --------------------------------------
# Knob becomes invisible to the camera but still casts; a shadow-catcher
# plane beneath catches the area-light penumbra.
plane_size = float(preset.get("shadow_plane_px", 400.0))
bpy.ops.mesh.primitive_plane_add(size=plane_size, location=(0.0, 0.0, -0.5))
plane = bpy.context.active_object
plane.name = "shadow_catcher"
plane.is_shadow_catcher = True
# Lift the knob off the catcher so the shadow softens and spreads into a
# visible pool (a flush contact shadow barely separates from the dark plate).
gap = float(preset.get("shadow_gap_px", 0.0))
for obj in knob_objs:
    obj.visible_camera = False
    obj.location.z += gap
# Enlarge the area lights for the shadow pass only (cap already rendered) so
# the penumbra is much wider/softer — darkest near the knob, fading to nothing
# as it spreads. Bigger emitter = softer shadow without detaching it.
soften = float(preset.get("shadow_soften", 1.0))
if soften != 1.0:
    for lt in bpy.data.lights:
        lt.size *= soften
        lt.size_y *= soften

shadow_out = output_dir / "knob_shadow.png"
scene.render.filepath = str(shadow_out)
bpy.ops.render.render(write_still=True)
print(f"[render_knob] shadow: {shadow_out}")

# ---- copy to runtime assets (only for the production preset) ----------------
if preset_name == "knob_cap":
    for src, dst_name in ((cap_out, "knob_cap.png"), (shadow_out, "knob_shadow.png")):
        target = assets_dir / dst_name
        shutil.copy2(src, target)
        recompress(target)
        print(f"[render_knob] -> {target}")
