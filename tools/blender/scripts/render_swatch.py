"""Render a small material swatch (flat lit square) for fast iteration.

Implements the research-recommended **Approach D** (combined Voronoi → Bump
+ roughness modulation) for hammertone paint. UV-driven 2D Voronoi feeds
both a Bump node (normal modulation) and a Map Range node (roughness
[0.37, 0.73] band). Optional UV warp via low-frequency Noise Texture for
organic cell irregularity.

Invocation:
    blender --background --python render_swatch.py -- \
        --variant <name> \
        --voronoi-scale <N>            (e.g., 50 / 70 / 90 / 120) \
        [--samples N]

Output: tools/blender/swatches/<variant>.png at 256×256.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy
import mathutils
from mathutils import Vector


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--variant", required=True)
parser.add_argument("--voronoi-scale", type=float, default=70.0,
                    help="2D Voronoi scale (cells/UV). 50=coarse, 70=med, 90=med-fine, 120=fine")
parser.add_argument("--bump-strength", type=float, default=0.55)
parser.add_argument("--metallic", type=float, default=0.55)
parser.add_argument("--rough-min", type=float, default=0.37)
parser.add_argument("--rough-max", type=float, default=0.73)
parser.add_argument("--base-color", type=str, default="0.12,0.10,0.08")
parser.add_argument("--samples", type=int, default=128)
parser.add_argument("--size", type=int, default=256)
parser.add_argument("--uv-warp", type=float, default=0.04, help="UV warp strength (0=off)")
args = parser.parse_args(argv)

base_color = tuple(float(c) for c in args.base_color.split(",")) + (1.0,)

script_dir = Path(__file__).resolve().parent
swatch_dir = script_dir.parent / "swatches"
swatch_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Scene reset
# ---------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------------------------------------------------------------------------
# Plate — 200×200 unit square. Slight thickness so the rim light can graze.
# ---------------------------------------------------------------------------
PLATE_SIZE = 200.0

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -1.0))
plate = bpy.context.active_object
plate.scale = (PLATE_SIZE, PLATE_SIZE, 2.0)
bpy.ops.object.transform_apply(scale=True)


# ---------------------------------------------------------------------------
# Material — Approach D: Voronoi → Bump + roughness modulation
# ---------------------------------------------------------------------------
def build_hammertone(mat, voronoi_scale: float, bump_strength: float,
                     base_color, metallic: float,
                     rough_min: float, rough_max: float, uv_warp_strength: float):
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

    bsdf.inputs["Base Color"].default_value = base_color
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = 0.55  # overridden by remap link

    L = nt.links

    # UV path — optional warp via low-frequency noise
    if uv_warp_strength > 0.0:
        warp_noise = nt.nodes.new("ShaderNodeTexNoise")
        warp_noise.inputs["Scale"].default_value = 8.0
        warp_noise.inputs["Detail"].default_value = 2.0
        warp_noise.inputs["Roughness"].default_value = 0.5
        if "Distortion" in warp_noise.inputs:
            warp_noise.inputs["Distortion"].default_value = 0.0
        mix_vec = nt.nodes.new("ShaderNodeMixRGB")
        mix_vec.blend_type = "ADD"
        mix_vec.inputs["Fac"].default_value = uv_warp_strength
        L.new(coord.outputs["UV"], warp_noise.inputs["Vector"])
        L.new(coord.outputs["UV"], mix_vec.inputs["Color1"])
        L.new(warp_noise.outputs["Color"], mix_vec.inputs["Color2"])
        L.new(mix_vec.outputs["Color"], voro.inputs["Vector"])
    else:
        L.new(coord.outputs["UV"], voro.inputs["Vector"])

    # Bump path
    L.new(voro.outputs["Distance"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    # Roughness path
    L.new(voro.outputs["Distance"], remap.inputs["Value"])
    L.new(remap.outputs["Result"], bsdf.inputs["Roughness"])
    # Output
    L.new(bsdf.outputs["BSDF"], out.inputs["Surface"])


mat = bpy.data.materials.new(args.variant)
build_hammertone(
    mat,
    voronoi_scale=args.voronoi_scale,
    bump_strength=args.bump_strength,
    base_color=base_color,
    metallic=args.metallic,
    rough_min=args.rough_min,
    rough_max=args.rough_max,
    uv_warp_strength=args.uv_warp,
)
plate.data.materials.append(mat)


# ---------------------------------------------------------------------------
# Studio rig — research-recommended energies (~1000× my prior preset)
# Scaled to the swatch plate: lights use chassis preset positions, but the
# swatch plate is 200/680 = ~0.29× the size, so we move lights closer to
# preserve the same irradiance density on the surface.
# ---------------------------------------------------------------------------
PLATE_SCALE = PLATE_SIZE / 680.0  # 0.294

def add_area_light(name, position, size, size_y, energy, color):
    data = bpy.data.lights.new(name, type="AREA")
    data.shape = "RECTANGLE"
    data.size = size
    data.size_y = size_y
    data.energy = energy
    data.color = color
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = Vector(position)
    direction = Vector((0, 0, 0)) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


# Key — upper-left, warm
add_area_light(
    "Key",
    position=(-520 * PLATE_SCALE, 460 * PLATE_SCALE, 780 * PLATE_SCALE),
    size=600 * PLATE_SCALE,
    size_y=400 * PLATE_SCALE,
    energy=850_000 * (PLATE_SCALE ** 2),  # area-scaled
    color=(1.0, 0.96, 0.88),
)
# Fill — lower-right, slight cool
add_area_light(
    "Fill",
    position=(480 * PLATE_SCALE, -420 * PLATE_SCALE, 500 * PLATE_SCALE),
    size=800 * PLATE_SCALE,
    size_y=500 * PLATE_SCALE,
    energy=160_000 * (PLATE_SCALE ** 2),
    color=(0.88, 0.92, 1.0),
)
# Rim — behind panel, narrow tall strip
add_area_light(
    "Rim",
    position=(420 * PLATE_SCALE, 300 * PLATE_SCALE, -350 * PLATE_SCALE),
    size=80 * PLATE_SCALE,
    size_y=600 * PLATE_SCALE,
    energy=220_000 * (PLATE_SCALE ** 2),
    color=(1.0, 1.0, 1.0),
)


# World — very low ambient so grain shadow stays visible
scene.world = bpy.data.worlds.new("studio_world")
scene.world.use_nodes = True
bg = scene.world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
bg.inputs["Strength"].default_value = 0.03


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
bpy.ops.object.camera_add(location=(0, 0, 1000), rotation=(0, 0, 0))
cam = bpy.context.active_object
cam.data.type = "ORTHO"
cam.data.ortho_scale = PLATE_SIZE
cam.data.clip_start = 1.0
cam.data.clip_end = 5000.0
scene.camera = cam


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
scene.render.engine = "CYCLES"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
try:
    scene.cycles.denoiser = "OPTIX"
except (TypeError, RuntimeError):
    scene.cycles.denoiser = "OPENIMAGEDENOISE"

scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

scene.render.resolution_x = args.size
scene.render.resolution_y = args.size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.render.image_settings.color_depth = "8"
scene.render.film_transparent = False

out_path = swatch_dir / f"{args.variant}.png"
scene.render.filepath = str(out_path)
bpy.ops.render.render(write_still=True)
print(f"\n[swatch:{args.variant}] {out_path}")
