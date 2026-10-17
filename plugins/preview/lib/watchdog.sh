#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

preview_last_counted_ts() {
  local log
  log="$(preview_state_dir)/requests.log"
  if [[ ! -f "$log" ]]; then
    echo ""
    return 0
  fi
  python3 - "$log" <<'PY'
import sys
from datetime import datetime, timezone
path = sys.argv[1]
last = None
with open(path, encoding="utf-8") as f:
    for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        if parts[3] != "1":
            continue
        try:
            dt = datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            last = int(dt.timestamp())
        except ValueError:
            continue
if last is None:
    print("")
else:
    print(last)
PY
}

preview_watchdog() {
  # Agent mode disables idle; only the total cap applies as a safety net.
  local idle="${PREVIEW_IDLE_SECONDS:-300}"
  local cap="${PREVIEW_TOTAL_CAP_SECONDS:-900}"
  local started ready_at now last_active idle_age
  if [[ "${PREVIEW_AGENT_MODE:-}" == "1" || "${PREVIEW_AGENT_MODE:-}" == "true" ]]; then
    idle=0
  fi
  started="$(preview_read started_at)"
  [[ -n "$started" ]] || started="$(date +%s)"

  preview_write status watching
  preview_log "watchdog idle=${idle}s total_cap=${cap}s agent=${PREVIEW_AGENT_MODE:-0}"

  while true; do
    now="$(date +%s)"
    # Total cap always wins.
    if (( now - started >= cap )); then
      preview_write reason "total-cap"
      preview_log "total cap reached (${cap}s)"
      return 0
    fi

    ready_at="$(preview_read ready_at)"
    if [[ -z "$ready_at" ]]; then
      sleep 2
      continue
    fi

    # Single idle timer: starts at READY, resets on each counted request. idle=0 disables it.
    if (( idle > 0 )); then
      last_active="$(preview_last_counted_ts)"
      if [[ -z "$last_active" ]]; then
        last_active="$ready_at"
      fi
      idle_age=$((now - last_active))
      if (( idle_age >= idle )); then
        preview_write reason "idle"
        preview_log "idle timeout (${idle}s since last counted request)"
        return 0
      fi
    fi

    if [[ -f "$(preview_state_dir)/stop" ]]; then
      local why
      why="$(preview_read reason)"
      [[ -n "$why" ]] || preview_write reason "stop"
      return 0
    fi

    local app_pid tunnel_pid
    app_pid="$(preview_read app_pid)"
    tunnel_pid="$(preview_read tunnel_wrap_pid)"
    if [[ -n "$app_pid" ]] && ! kill -0 "$app_pid" 2>/dev/null; then
      preview_write reason "app-exit"
      preview_log "app process exited"
      return 0
    fi
    if [[ -n "$tunnel_pid" ]] && ! kill -0 "$tunnel_pid" 2>/dev/null; then
      preview_write reason "tunnel-exit"
      preview_log "tunnel process exited"
      return 0
    fi

    sleep 2
  done
}
