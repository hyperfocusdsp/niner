"""Bake a transparent glass-reflection PNG for the Niner display.

Replaces the procedural 32-step quadratic sheen + 1-px specular line with a
Cycles-rendered overlay that reads as real glass reflecting studio lights.

Output: assets/display_reflection.png — RGBA, transparent background, just
the highlight hotspots. Composited in egui via painter.image() over the lit
display content.

Design:
  • Camera: orthographic, top-down, ortho_scale = lit width.
  • Surface: thin plane at z=0 covering the lit rect, Principled BSDF with
    near-black base color + low roughness, so only the studio-light
    reflections are visible.
  • Lights: two-light rig tuned for the display rect (NOT the chassis rig —
    chassis lights are positioned for the whole 680×444 panel, their
    projections miss the small 348×56 display strip). A broad warm key from
    upper-left + a softer cool fill from upper-right gives the classic
    rack-gear glass look.
  • World: pure black, so anything not the lights/plane reads as transparent.
  • film_transparent = True — alpha shows where the surface is.

Invocation:
    blender --background --python render_display.py -- --preset <preset.json> [--samples N]
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


# ---------------------------------------------------------------------------
# Display rect (1 BU = 1 logical pixel of the lit area)
# ---------------------------------------------------------------------------
lit = preset["lit_rect"]
LIT_W = float(lit["w"])
LIT_H = float(lit["h"])
# World origin = centre of lit rect. +X right, +Y up, +Z toward camera.


# ---------------------------------------------------------------------------
# Glass plane — Principled BSDF, very dark base + low roughness so the
# render shows essentially only the studio-light reflections.
# ---------------------------------------------------------------------------
def make_glass_material(roughness: float, tint: tuple[float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new("display_glass")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = roughness
    # Specular tint — slightly warm so reflections pick up the key colour.
    if "Specular Tint" in bsdf.inputs:
        # Blender 4.x uses a colour socket for Specular Tint
        bsdf.inputs["Specular Tint"].default_value = (*tint, 1.0)
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.6

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def add_glass_plane() -> bpy.types.Object:
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = "display_glass"
    obj.scale = (LIT_W, LIT_H, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    mat_cfg = preset.get("material", {})
    mat = make_glass_material(
        roughness=float(mat_cfg.get("roughness", 0.18)),
        tint=tuple(mat_cfg.get("specular_tint", [1.0, 0.96, 0.90])),
    )
    obj.data.materials.append(mat)
    return obj


add_glass_plane()


# ---------------------------------------------------------------------------
# Studio lighting — two-light rig sized to the display rect (NOT the
# chassis rig). Positions are in world coords with origin at lit centre.
# ---------------------------------------------------------------------------
def add_area_light(name: str, position, size_w: float, size_h: float,
                   energy: float, color, target=(0.0, 0.0, 0.0)):
    data = bpy.data.lights.new(name, type="AREA")
    data.shape = "RECTANGLE"
    data.size = size_w
    data.size_y = size_h
    data.energy = energy
    data.color = color
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = mathutils.Vector(position)
    direction = mathutils.Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


for light in preset["lights"]:
    add_area_light(
        name=light["name"],
        position=tuple(light["position"]),
        size_w=float(light["size_w"]),
        size_h=float(light["size_h"]),
        energy=float(light["energy"]),
        color=tuple(light["color"]),
        target=tuple(light.get("target", [0.0, 0.0, 0.0])),
    )


# Pure black world — anything not lit by the rig stays transparent in the
# composite (film_transparent=True) or pure black on the surface.
world = bpy.data.worlds.new("display_world")
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
bg.inputs["Strength"].default_value = 0.0
scene.world = world


# ---------------------------------------------------------------------------
# Camera — orthographic, top-down, framed exactly on the lit rect.
# ---------------------------------------------------------------------------
def add_camera() -> bpy.types.Object:
    bpy.ops.object.camera_add(location=(0.0, 0.0, 600.0), rotation=(0.0, 0.0, 0.0))
    cam = bpy.context.active_object
    cam.name = "display_camera"
    cam.data.type = "ORTHO"
    # ortho_scale fits the longer dimension; render aspect carries the rest.
    cam.data.ortho_scale = max(LIT_W, LIT_H)
    cam.data.clip_start = 1.0
    cam.data.clip_end = 5000.0
    scene.camera = cam
    return cam


add_camera()


# ---------------------------------------------------------------------------
# Render settings — RGBA, transparent film, 2× scale to match chassis bake.
# ---------------------------------------------------------------------------
rcfg = preset["render"]
scale = int(rcfg.get("scale", 2))
samples = args.samples if args.samples is not None else int(rcfg.get("samples", 256))

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

scene.render.resolution_x = int(LIT_W * scale)
scene.render.resolution_y = int(LIT_H * scale)
scene.render.resolution_percentage = 100

scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.render.film_transparent = True

out_path = output_dir / "display_reflection.png"
scene.render.filepath = str(out_path)


bpy.ops.render.render(write_still=True)
print(f"\n[render_display] Rendered: {out_path}")
print(f"[render_display]   resolution: {scene.render.resolution_x}x{scene.render.resolution_y}")
print(f"[render_display]   engine: {scene.render.engine}, samples: {samples}")

if preset_name == "display":
    target = assets_dir / "display_reflection.png"
    shutil.copy2(out_path, target)

    import subprocess
    if shutil.which("convert"):
        # Map luminance → alpha so the PNG composites as an additive overlay:
        # dark pixels go transparent (don't obscure lit content underneath),
        # bright highlight pixels stay opaque. Uses max(R,G,B) which keeps
        # saturated warm highlights from getting alpha-suppressed vs a flat
        # luminance formula.
        subprocess.run(
            [
                "convert", str(target),
                "(", "+clone", "-channel", "RGB", "-separate",
                "-evaluate-sequence", "Max", ")",
                "-compose", "CopyOpacity", "-composite",
                str(target),
            ],
            check=True,
        )
        # Then compress.
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
            print(f"[render_display] Recompressed: {orig_size} → {new_size} bytes "
                  f"({(orig_size - new_size) * 100 // orig_size}% saved)")
        else:
            tmp.unlink()
    print(f"[render_display] Copied to: {target}")
else:
    print(f"[render_display] Non-production preset '{preset_name}' — "
          f"output stays in {out_path} (assets/display_reflection.png not touched)")
