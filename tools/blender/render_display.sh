#!/usr/bin/env bash
# Bake the Niner display glass reflection to assets/display_reflection.png.
#
#   ./render_display.sh                              # default preset
#   ./render_display.sh presets/display_alt.json     # override
#
# Output flow:
#   tools/blender/output/<preset_name>/display_reflection.png  ← Blender writes here
#   assets/display_reflection.png                              ← Python script copies here
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PRESET="${1:-$HERE/presets/display.json}"

if [[ ! -f "$PRESET" ]]; then
    echo "preset not found: $PRESET" >&2
    exit 1
fi

if ! command -v blender >/dev/null 2>&1; then
    echo "blender not on PATH — install with: sudo pacman -S --needed blender" >&2
    exit 1
fi

blender --background --python "$HERE/scripts/render_display.py" -- --preset "$PRESET"

NAME="$(basename "$PRESET" .json)"
echo
echo "Output: $HERE/output/$NAME/"
ls -1 "$HERE/output/$NAME/" 2>/dev/null || true
