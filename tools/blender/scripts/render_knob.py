"""Render a single neutral plastic knob cap under the chassis studio rig.

The bake is RGBA with film_transparent=True so only the cap's silhouette is
opaque. At runtime, egui's `painter.image(tex, rect, uv, tint_color)`
multiplies the texture by `tint_color`, so painting this one bake with
each section's `core_color` produces a coloured cap with realistic
lighting (key upper-left + warm, fill lower-right + cool, rim grazing top
edge) that matches the same studio rig the chassis was baked under.

Output: assets/knob_cap.png (~256×256 RGBA).

Invocation:
    blender --background --python render_knob.py -- --preset <preset.json>
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


# CLI args (after the `--`)
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


# Material — neutral plastic. Runtime tints via painter color multiply.
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


# Geometry — a slightly convex disk (cylinder + top bevel for soft dome).
cap_cfg = preset["cap"]
radius = float(cap_cfg["radius_px"])
height = float(cap_cfg["height_px"])
bevel_w = float(cap_cfg.get("rim_bevel_px", 4.0))
bevel_segs = int(cap_cfg.get("rim_bevel_segments", 6))

# Build a low cylinder centred at world origin with z = 0..height.
bpy.ops.mesh.primitive_cylinder_add(
    vertices=64,
    radius=radius,
    depth=height,
    location=(0.0, 0.0, height / 2.0),
)
cap = bpy.context.active_object
cap.name = "knob_cap"
cap.data.materials.append(plastic)

# Top edge bevel — softens the rim so the key light produces a smooth
# falloff rather than a hard ring of specular at the corner.
bevel = cap.modifiers.new("rim_bevel", type="BEVEL")
bevel.width = bevel_w
bevel.segments = bevel_segs
bevel.limit_method = "ANGLE"
bevel.angle_limit = math.radians(30)
bpy.ops.object.modifier_apply(modifier="rim_bevel")

# Smooth shade the cap so the bevel reads as a continuous dome curve.
for poly in cap.data.polygons:
    poly.use_smooth = True


# Camera — orthographic, 1 BU = 1 px. Looking down +Z onto the cap.
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


# Studio lights — same directions as chassis bake, scaled energies.
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


# Render settings
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

out_path = output_dir / "knob_cap.png"
scene.render.filepath = str(out_path)


bpy.ops.render.render(write_still=True)
print(f"\n[render_knob] Rendered: {out_path}")
print(f"[render_knob]   resolution: {scene.render.resolution_x}x{scene.render.resolution_y}")
print(f"[render_knob]   engine: {scene.render.engine}, samples: {samples}")

if preset_name == "knob_cap":
    target = assets_dir / "knob_cap.png"
    shutil.copy2(out_path, target)
    # Recompress if convert is available
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
        if tmp.stat().st_size < target.stat().st_size:
            tmp.replace(target)
        else:
            tmp.unlink()
    print(f"[render_knob] Copied to: {target}")
