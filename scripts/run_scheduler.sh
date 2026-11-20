#!/usr/bin/env sh
set -eu

INTERVAL_SECONDS="${SCHEDULER_INTERVAL_SECONDS:-900}"
LOG_DIR="${LOG_DIR:-/app/logs}"

mkdir -p "$LOG_DIR"

while true; do
  python /app/scheduler.py >> "$LOG_DIR/cron.log" 2>&1
  sleep "$INTERVAL_SECONDS"
done