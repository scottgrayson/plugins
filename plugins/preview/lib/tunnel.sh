#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

# Pin cloudflared to a release + sha256. Bump version and checksums together
# (copy from the GitHub release body "SHA256 Checksums" section).
# Override with PREVIEW_CLOUDFLARED_VERSION only for local experiments — CI/Actions
# should use the values below.
PREVIEW_CLOUDFLARED_VERSION="${PREVIEW_CLOUDFLARED_VERSION:-2026.10.1}"

preview_cloudflared_sha256() {
  # Official checksums from:
  # https://github.com/cloudflare/cloudflared/releases/tag/2026.10.1
  case "$1" in
    amd64) echo "3bfc539f45d2620c71debb7a86be0efa737ed5ccbad9eb85c787116900e5c586" ;;
    arm64) echo "2c65dfabf616c40efc7545d107d7a57ad5e21377e2964b7e88af19a238074a46" ;;
    *) return 1 ;;
  esac
}

preview_install_cloudflared() {
  # Always install the pinned binary into state/bin (do not trust PATH).
  local bin_dir dest arch url expect got
  bin_dir="$(preview_state_dir)/bin"
  mkdir -p "$bin_dir"
  dest="$bin_dir/cloudflared"

  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) preview_die "unsupported arch for cloudflared: $arch" ;;
  esac

  expect="$(preview_cloudflared_sha256 "$arch")" \
    || preview_die "no pinned sha256 for cloudflared arch=$arch"
  url="https://github.com/cloudflare/cloudflared/releases/download/${PREVIEW_CLOUDFLARED_VERSION}/cloudflared-linux-${arch}"

  if [[ -x "$dest" ]]; then
    got="$(sha256sum "$dest" | awk '{print $1}')"
    if [[ "$got" == "$expect" ]]; then
      echo "$dest"
      return 0
    fi
    preview_log "cloudflared checksum mismatch on cache; re-downloading"
    rm -f "$dest"
  fi

  preview_log "downloading cloudflared ${PREVIEW_CLOUDFLARED_VERSION} (${arch})"
  curl -fsSL "$url" -o "$dest"
  got="$(sha256sum "$dest" | awk '{print $1}')"
  if [[ "$got" != "$expect" ]]; then
    rm -f "$dest"
    preview_die "cloudflared sha256 mismatch: got ${got} want ${expect} (${url})"
  fi
  chmod +x "$dest"
  preview_log "cloudflared ${PREVIEW_CLOUDFLARED_VERSION} verified sha256=${got}"
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
  # Redirect wrap stdio so callers using $(preview_start_tunnel) are not blocked
  # by the long-lived cloudflared pipe holding the command-substitution FD open.
  (
    "$cf" tunnel --no-autoupdate --url "$target_url" 2>&1 | tee "$log" | while IFS= read -r line; do
      if [[ "$line" =~ https://[a-zA-Z0-9.-]+\.trycloudflare\.com ]]; then
        echo "${BASH_REMATCH[0]}" >"$url_file"
      fi
    done
  ) </dev/null >"$(preview_state_dir)/tunnel.wrap.log" 2>&1 &
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
