#!/usr/bin/env bash
# Unattended, resumable backfill of your full BroodMinder history, for cron.
#
# The API is capped at ~1000 calls/day, so a large account's "beginning of time"
# pull spans several days. This script is idempotent + budget-aware: safe to run
# repeatedly. Once every hive is back-filled to its start, runs become cheap
# no-ops (cached windows + stop-after-empty short-circuit). Logs to
# data/cron_backfill.log.
#
# Install (every 6h, to catch the daily quota reset whenever it falls):
#   ( crontab -l 2>/dev/null; echo "20 */6 * * * /ABS/PATH/TO/scripts/cron_backfill.sh" ) | crontab -
# Remove later with: crontab -e   (delete the broodminder line)
#
# Adjust --start to before your oldest data, and --end to where your normal
# (forward) extraction already begins, so the two don't overlap.
set -uo pipefail

# Resolve the repo root relative to this script (portable; no hardcoded paths).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$DIR/.venv/bin/python"
LOG="$DIR/data/cron_backfill.log"
LOCKFILE="$DIR/data/.sync.lock"
cd "$DIR" || exit 1
[[ -x "$PY" ]] || PY="python3"   # fall back to system python if no venv

ts() {
    date -u +%Y-%m-%dT%H:%M:%SZ
    return
}

mkdir -p "$DIR/data"

# Acquire exclusive lock to prevent overlapping sync or backfill runs
if ! command -v flock >/dev/null 2>&1; then
    echo "=== $(ts) backfill run failed: flock command not found ===" >> "$LOG"
    exit 1
fi
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    echo "=== $(ts) backfill run skipped: another sync or backfill process holds the lock ===" >> "$LOG"
    exit 0
fi

echo "=== $(ts) backfill run start ===" >> "$LOG"
# Walk newest->oldest, stopping each hive after 3 consecutive empty 6-month
# windows. Stays under the daily cap; on 429 it saves progress and exits 0.
"$PY" scripts/extract_all.py \
    --start 2016-01-01 --end 2025-06-01 \
    --reverse --stop-after-empty 3 --max-calls 950 \
    >> "$LOG" 2>&1
echo "--- extract exit $? ---" >> "$LOG"

# Rebuild analysis outputs (no API calls).
"$PY" scripts/flatten.py >> "$LOG" 2>&1
echo "=== $(ts) run done ===" >> "$LOG"
