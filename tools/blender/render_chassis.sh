#!/usr/bin/env bash
# Render Niner's chassis (the dark plugin body) as a photorealistic baked PNG.
#
#   ./render_chassis.sh                                        # default preset
#   ./render_chassis.sh presets/chassis_marketing.json         # hero render
#
# Output flow:
#   tools/blender/output/<preset_name>/chassis.png   ← Blender writes here
#   assets/chassis.png                                ← Python script copies here
#                                                       (the runtime asset)
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PRESET="${1:-$HERE/presets/chassis.json}"

if [[ ! -f "$PRESET" ]]; then
    echo "preset not found: $PRESET" >&2
    exit 1
fi

if ! command -v blender >/dev/null 2>&1; then
    echo "blender not on PATH — install with: sudo pacman -S --needed blender" >&2
    exit 1
fi

blender --background --python "$HERE/scripts/render_chassis.py" -- --preset "$PRESET"

NAME="$(basename "$PRESET" .json)"
echo
echo "Output: $HERE/output/$NAME/"
ls -1 "$HERE/output/$NAME/" 2>/dev/null || true
