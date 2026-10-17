#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

preview_kill_pid() {
  local pid="$1"
  [[ -n "$pid" ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
}

preview_cleanup() {
  local reason="${1:-$(preview_read reason)}"
  [[ -n "$reason" ]] || reason="cleanup"
  preview_write reason "$reason"
  preview_write status stopping
  preview_log "cleaning up ($reason)"

  local key pid
  for key in watchdog_pid proxy_pid app_pid tunnel_wrap_pid; do
    pid="$(preview_read "$key")"
    preview_kill_pid "$pid"
  done

  # Catch stray children recorded in pgid file if present.
  local pgid
  pgid="$(preview_read pgid)"
  if [[ -n "$pgid" ]]; then
    kill -TERM -"$pgid" 2>/dev/null || true
  fi

  preview_write status stopped
  preview_write stopped_at "$(date +%s)"
  preview_log "stopped: $reason"
}
