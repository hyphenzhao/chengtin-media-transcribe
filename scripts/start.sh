#!/usr/bin/env bash
set -euo pipefail
APP=/Volumes/Workspace/Servers/media-transcribe
for attempt in $(seq 1 60); do
  if mountpoint -q /Volumes/Workspace && test -x "$APP/.venv/bin/supervisord"; then break; fi
  sleep 2
done
mountpoint -q /Volumes/Workspace || exit 1
cd "$APP"
mkdir -p data logs
exec 9>data/start.lock
flock -n 9 || exit 0
if .venv/bin/supervisorctl -c scripts/supervisord.conf pid >/dev/null 2>&1; then exit 0; fi
# Do not pass the start lock into the daemon's descriptors.
.venv/bin/supervisord -c scripts/supervisord.conf 9>&-
