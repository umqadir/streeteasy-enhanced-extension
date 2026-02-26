#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv: MISSING"
  exit 1
fi

echo "uv: OK ($(uv --version))"

echo "python:"
uv run --project backend python - <<'PY'
import sys
print(sys.version)
if sys.version_info[:2] != (3, 11):
    print('WARNING: expected Python 3.11 for release profile')
PY

CACHE_ROOT="${CACHE_ROOT:-$HOME/.cache/cv_pipeline/models}"
DUST3R_CKPT="$CACHE_ROOT/checkpoints/dust3r/DUSt3R_ViTLarge_BaseDecoder_512_dpt.pth"
MOGE_DIR="$CACHE_ROOT/checkpoints/moge/Ruicheng__moge-2-vitl-normal"
VENDOR_DUST3R="$CACHE_ROOT/vendor/dust3r"
VENDOR_MOGE="$CACHE_ROOT/vendor/moge"

echo "asset checks:"
[[ -f "$DUST3R_CKPT" ]] && echo "  dust3r checkpoint: OK" || echo "  dust3r checkpoint: MISSING"
[[ -d "$MOGE_DIR" ]] && echo "  moge snapshot: OK" || echo "  moge snapshot: MISSING"
[[ -d "$VENDOR_DUST3R" ]] && echo "  vendor dust3r: OK" || echo "  vendor dust3r: MISSING"
[[ -d "$VENDOR_MOGE" ]] && echo "  vendor moge: OK" || echo "  vendor moge: MISSING"

echo "backend health (if running on localhost:8787):"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS http://127.0.0.1:8787/health >/tmp/sleepeasy_health.json 2>/dev/null; then
    echo "  running: YES"
    cat /tmp/sleepeasy_health.json
  else
    echo "  running: NO"
  fi
else
  echo "  curl unavailable"
fi
