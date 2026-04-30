#!/usr/bin/env bash
# Verifies that a default release build excludes the layout-editor surface
# while keeping the always-on JSON load path intact.
#
# Used by CI (.github/workflows/ci.yml) and by P5 sign-off.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> building default release (no layout_editor feature)"
cargo build --release --no-default-features --quiet

LIB="target/release/libniner.so"
if [ ! -f "$LIB" ]; then
    # macOS produces .dylib; Windows produces .dll. Adjust if/when CI runs there.
    if [ -f "target/release/libniner.dylib" ]; then
        LIB="target/release/libniner.dylib"
    else
        echo "FAIL: niner library not found at target/release/libniner.{so,dylib}"
        exit 1
    fi
fi

# Editor UI strings should NOT appear in a default release. Each of these
# is a string literal inside a `#[cfg(feature = "layout_editor")]` block
# that paints the bulk-adjust panel.
EDITOR_STRINGS=(
    "Layout editor"
    "Drag = move"
    "Save layout"
    "Bulk overrides"
)

# Dump strings to a temp file once. We avoid `strings | grep -q` directly
# because `grep -q` short-circuits and SIGPIPEs `strings`, which
# `set -o pipefail` surfaces as a pipeline failure.
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
strings "$LIB" > "$TMP"

leaked=0
for s in "${EDITOR_STRINGS[@]}"; do
    if grep -qF -- "$s" "$TMP"; then
        echo "  LEAK: \"$s\" found in $LIB"
        leaked=1
    fi
done

if [ "$leaked" -ne 0 ]; then
    echo "FAIL: layout-editor surface leaked into default release build"
    exit 1
fi

# Always-on path: the binary must contain the baked JSON's bulk fields,
# proving include_bytes! survived linking. This catches "the file got
# stripped entirely" regressions.
if ! grep -qF -- '"chrome_height_scale"' "$TMP"; then
    echo "FAIL: baked layout JSON appears stripped from $LIB"
    exit 1
fi

SIZE_DEFAULT=$(stat -c %s "$LIB" 2>/dev/null || stat -f %z "$LIB")
echo "==> default release size: ${SIZE_DEFAULT} bytes"

# Optional: build the feature-on variant for a size-delta sanity check.
# Skipped in CI gate to save build time; enable with CHECK_DELTA=1.
if [ "${CHECK_DELTA:-0}" = "1" ]; then
    echo "==> building --features layout_editor for size-delta comparison"
    cargo build --release --features layout_editor --quiet
    SIZE_EDITOR=$(stat -c %s "$LIB" 2>/dev/null || stat -f %z "$LIB")
    DELTA=$((SIZE_EDITOR - SIZE_DEFAULT))
    echo "==> editor-on release size:  ${SIZE_EDITOR} bytes"
    echo "==> editor surface costs:    ${DELTA} bytes"
    if [ "$DELTA" -lt 50000 ]; then
        echo "WARN: editor surface delta is unexpectedly small ($DELTA bytes < 50 KB)"
        echo "      — verify cfg gates are doing real work, not just stripping symbols"
    fi
fi

echo "OK: layout-editor excluded from default release; baked JSON intact"
