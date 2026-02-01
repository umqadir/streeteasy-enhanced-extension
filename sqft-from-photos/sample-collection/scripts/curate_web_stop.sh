#!/usr/bin/env bash
set -euo pipefail

# Stops the local curation web UI started by curate_web_start.sh.

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
pid_file="$root_dir/sample-collection/.curate_web.pid"

if [[ ! -f "$pid_file" ]]; then
  echo "No pid file at: $pid_file"
  exit 0
fi

pid="$(cat "$pid_file" || true)"
if [[ -z "${pid:-}" ]]; then
  rm -f "$pid_file"
  echo "Empty pid file; removed."
  exit 0
fi

if kill -0 "$pid" >/dev/null 2>&1; then
  kill "$pid" || true
  echo "Stopped (pid=$pid)"
else
  echo "Not running (stale pid=$pid)"
fi

rm -f "$pid_file"

