#!/usr/bin/env bash
# Unattended, forward catch-up sync of your BroodMinder history, for cron.
#
# Automatically resumes from the latest completed window in manifest.json,
# fetches only newly recorded readings and notes, and merges them into the
# analysis-ready NDJSON + CSV outputs without duplicating or dropping data.
#
# Install (e.g. daily at 04:00 UTC):
#   ( crontab -l 2>/dev/null; echo "0 4 * * * /ABS/PATH/TO/scripts/cron_sync.sh" ) | crontab -
#
set -uo pipefail

# Resolve the repo root relative to this script.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$DIR/.venv/bin/python"
LOG="$DIR/data/cron_sync.log"
cd "$DIR" || exit 1
[ -x "$PY" ] || PY="python3"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

mkdir -p "$DIR/data"
echo "=== $(ts) sync run start ===" >> "$LOG"

# Pull new forward windows since the latest recorded manifest window.
"$PY" scripts/extract_all.py --catchup >> "$LOG" 2>&1
echo "--- extract exit $? ---" >> "$LOG"

# Merge new raw windows with existing analysis outputs (no API calls).
"$PY" scripts/flatten.py --merge >> "$LOG" 2>&1
echo "=== $(ts) run done ===" >> "$LOG"
