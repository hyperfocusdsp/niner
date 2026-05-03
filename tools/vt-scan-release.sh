#!/usr/bin/env bash
# Upload a Niner GitHub Release's bundles to VirusTotal and print the
# four deterministic permalinks for niner.mdx.
#
# Usage:  VT_API_KEY=<key> ./tools/vt-scan-release.sh <tag>
# Example: VT_API_KEY=$(pass show vt) ./tools/vt-scan-release.sh v0.7.3
#
# Free-tier API: 4 req/min, 500/day, 32 MB max upload — comfortably
# within Niner's release-bundle sizes (~14-18 MB each).

set -euo pipefail

TAG="${1:?usage: $0 <tag>}"
: "${VT_API_KEY:?VT_API_KEY env var required (https://virustotal.com → API Key)}"

REPO="hyperfocusdsp/niner"
WORKDIR="$(mktemp -d -t vt-niner-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Downloading $TAG bundles into $WORKDIR..."
gh release download "$TAG" --repo "$REPO" \
  --pattern '*.tar.gz' --pattern '*.zip' \
  --dir "$WORKDIR"

cd "$WORKDIR"
declare -a IDS=()
declare -a NAMES=()
for f in niner-linux-x86_64.tar.gz \
         niner-macos-arm64.tar.gz \
         niner-macos-x86_64.tar.gz \
         niner-windows-x86_64.zip; do
  echo ">>> uploading $f"
  resp=$(curl -s -X POST -H "x-apikey: $VT_API_KEY" \
    -F "file=@$f" "https://www.virustotal.com/api/v3/files")
  aid=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
  IDS+=("$aid")
  NAMES+=("$f")
  sha=$(sha256sum "$f" | awk '{print $1}')
  echo "  analysis_id: $aid"
  echo "  sha256:      $sha"
  sleep 16  # respect 4/min rate limit
done

echo
echo "Polling for completion..."
for i in "${!IDS[@]}"; do
  aid="${IDS[$i]}"
  fname="${NAMES[$i]}"
  while :; do
    resp=$(curl -s -H "x-apikey: $VT_API_KEY" \
      "https://www.virustotal.com/api/v3/analyses/$aid")
    st=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['attributes']['status'])")
    if [[ "$st" == "completed" ]]; then
      stats=$(echo "$resp" | python3 -c "import sys,json; s=json.load(sys.stdin)['data']['attributes']['stats']; print(f\"malicious={s['malicious']}/suspicious={s['suspicious']}/undetected={s['undetected']}\")")
      echo "  $fname: completed  $stats"
      break
    fi
    echo "  $fname: $st (waiting 15s)"
    sleep 15
  done
done

echo
echo "=== VirusTotal permalinks ==="
for f in "${NAMES[@]}"; do
  sha=$(sha256sum "$f" | awk '{print $1}')
  echo "[$f](https://www.virustotal.com/gui/file/$sha)"
done
