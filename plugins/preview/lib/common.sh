#!/usr/bin/env bash
set -euo pipefail

PREVIEW_PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC2034
PREVIEW_LIB="$PREVIEW_PLUGIN_ROOT/lib"

preview_state_dir() {
  echo "${PREVIEW_STATE_DIR:-$PWD/.preview/run}"
}

preview_ensure_state() {
  mkdir -p "$(preview_state_dir)"
}

preview_log() {
  printf '[preview] %s\n' "$*" >&2
}

preview_die() {
  printf '[preview] error: %s\n' "$*" >&2
  exit 1
}

preview_write() {
  local key="$1"
  local value="$2"
  preview_ensure_state
  printf '%s' "$value" >"$(preview_state_dir)/$key"
}

preview_read() {
  local key="$1"
  local f
  f="$(preview_state_dir)/$key"
  if [[ -f "$f" ]]; then
    cat "$f"
  fi
}

preview_json_get() {
  local file="$1"
  local key="$2"
  local default="${3:-}"
  if [[ ! -f "$file" ]]; then
    printf '%s' "$default"
    return 0
  fi
  python3 - "$file" "$key" "$default" <<'PY'
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
val = data.get(key, default)
if val is None:
    val = default
if isinstance(val, bool):
    print("true" if val else "false")
elif isinstance(val, (list, dict)):
    print(json.dumps(val))
else:
    print(val)
PY
}

preview_have_cmd() {
  command -v "$1" >/dev/null 2>&1
}
