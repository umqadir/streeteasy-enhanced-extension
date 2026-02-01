#!/usr/bin/env bash
set -euo pipefail

# Starts the local curation web UI in the background.
#
# Usage:
#   bash sample-collection/scripts/curate_web_start.sh [port]
#
# Outputs:
#   - PID file: sample-collection/.curate_web.pid
#   - Log file: sample-collection/.curate_web.log

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
port="${1:-7860}"

pid_file="$root_dir/sample-collection/.curate_web.pid"
log_file="$root_dir/sample-collection/.curate_web.log"
dataset="$root_dir/sample-collection/streeteasy_eval_dataset/listings.json"

if [[ -f "$pid_file" ]]; then
  old_pid="$(cat "$pid_file" || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" >/dev/null 2>&1; then
    echo "Already running (pid=$old_pid). Stop it first:"
    echo "  bash sample-collection/scripts/curate_web_stop.sh"
    exit 0
  fi
fi

mkdir -p "$(dirname -- "$pid_file")"

nohup python "$root_dir/sample-collection/scripts/curate_web.py" \
  --dataset "$dataset" \
  --port "$port" \
  >"$log_file" 2>&1 &

pid=$!
echo "$pid" >"$pid_file"

echo "Started (pid=$pid)"
echo "Open: http://127.0.0.1:${port}/"
echo "Logs: $log_file"

