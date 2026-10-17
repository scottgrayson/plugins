#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

preview_http_ok() {
  local url="$1"
  local code
  # Prefer QUIC/HTTP3 when curl supports it, then HTTP/2, then plain HTTPS.
  if curl --help 2>&1 | grep -q -- '--http3'; then
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 --http3 "$url" 2>/dev/null || true)"
    if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
      echo "http3:$code"
      return 0
    fi
  fi
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 --http2 "$url" 2>/dev/null || true)"
  if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
    echo "http2:$code"
    return 0
  fi
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$url" 2>/dev/null || true)"
  if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
    echo "http:$code"
    return 0
  fi
  return 1
}

preview_wait_ready() {
  local url="${1:-$(preview_read url)}"
  [[ -n "$url" ]] || preview_die "no preview URL"
  local i mode
  preview_log "waiting for public URL to answer: $url"
  for i in $(seq 1 90); do
    if mode="$(preview_http_ok "$url")"; then
      preview_write ready_at "$(date +%s)"
      preview_write status ready
      preview_write ready_via "$mode"
      preview_log "READY via $mode"
      echo "$url"
      return 0
    fi
    sleep 1
  done
  preview_die "public URL never answered HTTP: $url"
}
