#!/bin/sh
# One unattended research tick: idempotent, lock-protected, no secret output.
set -eu

ROOT="/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push"
# launchd cannot write to the external volume (macOS volume protection), so the
# schedule lock and tick log live on the internal disk and only the campaign
# state itself stays on the SSD.
CAMPAIGN="/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020"
SCHEDULE="$HOME/Library/Application Support/MOA-Research"
PY="$ROOT/.venv312/bin/python"
LOCK="$SCHEDULE/.tick.lock"

mkdir -p "$SCHEDULE"
chmod 700 "$SCHEDULE" 2>/dev/null || true

# mkdir is atomic on AFP/APFS; a stale lock older than two hours is cleared.
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null || true
  fi
  if ! mkdir "$LOCK" 2>/dev/null; then
    echo '{"status":"SKIPPED_ALREADY_RUNNING"}'
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

cd "$ROOT"
"$PY" -m moa.research_cli advance --root "$CAMPAIGN" 2>&1 | tail -40
