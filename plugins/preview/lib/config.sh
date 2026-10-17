#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

preview_default_config_path() {
  echo "${PREVIEW_CONFIG:-$PWD/.preview/config.json}"
}

preview_load_config() {
  local config_path="${1:-$(preview_default_config_path)}"
  local out_dir
  out_dir="$(preview_state_dir)"
  preview_ensure_state

  python3 - "$config_path" "$out_dir/config.env" "$out_dir/config.json" <<'PY'
import json, os, sys

path, env_path, json_path = sys.argv[1], sys.argv[2], sys.argv[3]
defaults = {
    "type": "static",
    "start": "",
    "port": 4173,
    "idleSeconds": 300,
    "totalCapSeconds": 900,
    "defaultSteps": [],
    "ignorePaths": [],
    "root": ".",
}
data = dict(defaults)
if os.path.isfile(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise SystemExit(f"config must be a JSON object: {path}")
    for k, v in raw.items():
        if k not in defaults:
            raise SystemExit(f"unknown config key: {k}")
        data[k] = v
for key in ("idleSeconds", "totalCapSeconds", "port"):
    data[key] = int(data[key])
if not isinstance(data["defaultSteps"], list) or not all(isinstance(x, str) for x in data["defaultSteps"]):
    raise SystemExit("defaultSteps must be an array of strings")
if not isinstance(data["ignorePaths"], list) or not all(isinstance(x, str) for x in data["ignorePaths"]):
    raise SystemExit("ignorePaths must be an array of strings")
if data["start"] is None:
    data["start"] = ""
data["start"] = str(data["start"])
data["type"] = str(data["type"])
data["root"] = str(data["root"])

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"

lines = [
    f"PREVIEW_TYPE={sh_quote(data['type'])}",
    f"PREVIEW_START={sh_quote(data['start'])}",
    f"PREVIEW_PORT={data['port']}",
    f"PREVIEW_IDLE_SECONDS={data['idleSeconds']}",
    f"PREVIEW_TOTAL_CAP_SECONDS={data['totalCapSeconds']}",
    f"PREVIEW_ROOT={sh_quote(data['root'])}",
    f"PREVIEW_DEFAULT_STEPS={sh_quote(json.dumps(data['defaultSteps']))}",
    f"PREVIEW_IGNORE_PATHS={sh_quote(json.dumps(data['ignorePaths']))}",
]
with open(env_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
PY

  # shellcheck disable=SC1090
  source "$out_dir/config.env"
}

preview_list_step_names() {
  local steps_dir="${1:-$PWD/.preview/steps}"
  if [[ ! -d "$steps_dir" ]]; then
    return 0
  fi
  find "$steps_dir" -maxdepth 1 -type f -name '*.sh' -printf '%f\n' 2>/dev/null \
    | sed 's/\.sh$//' | sort
}

preview_resolve_steps() {
  local labels_csv="${1:-}"
  local steps_dir="${2:-$PWD/.preview/steps}"
  local defaults_json="${PREVIEW_DEFAULT_STEPS:-[]}"
  local -a selected=()
  local -a available=()
  local name

  mapfile -t available < <(preview_list_step_names "$steps_dir")

  if [[ -n "$labels_csv" ]]; then
    local IFS=','
    local -a labels=()
    read -r -a labels <<<"$labels_csv"
    local label step
    for label in "${labels[@]}"; do
      label="$(echo "$label" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      [[ -z "$label" ]] && continue
      case "$label" in
        preview:*)
          step="${label#preview:}"
          if [[ -z "$step" ]]; then
            preview_die "empty preview step label"
          fi
          if [[ ! -f "$steps_dir/$step.sh" ]]; then
            local valid
            valid="$(printf '%s\n' "${available[@]:-}" | paste -sd', ' -)"
            [[ -z "$valid" ]] && valid="(none)"
            preview_die "unknown step '$step'. valid steps: $valid"
          fi
          selected+=("$step")
          ;;
      esac
    done
  fi

  if [[ ${#selected[@]} -eq 0 ]]; then
    mapfile -t selected < <(python3 -c 'import json,sys; print("\n".join(json.loads(sys.argv[1])))' "$defaults_json")
  fi

  printf '%s\n' "${selected[@]:-}"
}

preview_run_steps() {
  local labels_csv="${1:-}"
  local steps_dir="${2:-$PWD/.preview/steps}"
  local step
  local -a steps=()
  mapfile -t steps < <(preview_resolve_steps "$labels_csv" "$steps_dir")
  for step in "${steps[@]:-}"; do
    [[ -z "$step" ]] && continue
    local script="$steps_dir/$step.sh"
    [[ -f "$script" ]] || preview_die "missing step script: $script"
    preview_log "running step: $step"
    chmod +x "$script" || true
    # shellcheck disable=SC1090
    bash "$script"
  done
}
