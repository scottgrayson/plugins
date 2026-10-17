#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

preview_install_cloudflared() {
  if preview_have_cmd cloudflared; then
    command -v cloudflared
    return 0
  fi
  local bin_dir
  bin_dir="$(preview_state_dir)/bin"
  mkdir -p "$bin_dir"
  local dest="$bin_dir/cloudflared"
  if [[ -x "$dest" ]]; then
    echo "$dest"
    return 0
  fi
  local url arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) preview_die "unsupported arch for cloudflared: $arch" ;;
  esac
  url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}"
  preview_log "downloading cloudflared"
  curl -fsSL "$url" -o "$dest"
  chmod +x "$dest"
  echo "$dest"
}

preview_start_tunnel() {
  local target_url="$1"
  local cf
  cf="$(preview_install_cloudflared)"
  local log
  log="$(preview_state_dir)/tunnel.log"
  local url_file
  url_file="$(preview_state_dir)/url.raw"
  : >"$log"
  : >"$url_file"

  preview_log "starting quick tunnel to $target_url"
  (
    "$cf" tunnel --no-autoupdate --url "$target_url" 2>&1 | tee "$log" | while IFS= read -r line; do
      if [[ "$line" =~ https://[a-zA-Z0-9.-]+\.trycloudflare\.com ]]; then
        echo "${BASH_REMATCH[0]}" >"$url_file"
      fi
    done
  ) &
  local tunnel_wrap_pid=$!
  preview_write tunnel_wrap_pid "$tunnel_wrap_pid"

  local i url=""
  for i in $(seq 1 60); do
    if [[ -s "$url_file" ]]; then
      url="$(tr -d '[:space:]' <"$url_file")"
      break
    fi
    if ! kill -0 "$tunnel_wrap_pid" 2>/dev/null; then
      preview_die "cloudflared exited before publishing a URL; see $log"
    fi
    sleep 1
  done
  [[ -n "$url" ]] || preview_die "timed out waiting for trycloudflare.com URL; see $log"
  preview_write url "$url"
  echo "$url"
}
