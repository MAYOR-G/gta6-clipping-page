#!/usr/bin/env bash
# Step 3: Contact-sheet generator (sheet.sh)
# Builds a timestamped grid of frames (default: 1 frame every 2 seconds) from any video
# so you can visually scout/verify candidates fast without trusting titles.
# Usage: ./scripts/sheet.sh <input_video> [output_png] [interval_sec] [cols]

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <input_video> [output_png] [interval_sec=2] [cols=4]" >&2
  exit 1
fi

INPUT="$1"
OUT="${2:-${INPUT%.*}_sheet.png}"
INTERVAL="${3:-2}"
COLS="${4:-4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 - "$SCRIPT_DIR" "$INPUT" "$OUT" "$INTERVAL" "$COLS" << 'EOF'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from clip import make_contact_sheet

out = make_contact_sheet(Path(sys.argv[2]), Path(sys.argv[3]), interval=float(sys.argv[4]), cols=int(sys.argv[5]))
print(f"Contact sheet saved: {out}")
EOF
