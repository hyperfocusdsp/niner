#!/usr/bin/env bash
# Render the photoreal knob cap (single neutral plastic dome under the
# chassis studio rig). Output goes to assets/knob_cap.png; the runtime
# blits this with `core_color` as a tint so all section colours inherit
# the same lighting in one bake.
#
#   ./render_knob.sh
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PRESET="${1:-$HERE/presets/knob_cap.json}"

if [[ ! -f "$PRESET" ]]; then
    echo "preset not found: $PRESET" >&2
    exit 1
fi

exec blender --background \
    --python "$HERE/scripts/render_knob.py" \
    -- --preset "$PRESET" \
    "$@"
