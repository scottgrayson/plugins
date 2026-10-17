#!/usr/bin/env bash
set -euo pipefail

IDLE_LINE='- [ ] Check to generate preview'
TICKED_LINE='- [x] Check to generate preview'
LOADING_PREFIX='- [x] Link to preview will be available soon (avg deploy time '
RUNNING_RE='^- \[[xX]\] \[Preview\]\(.+\)$'
LOADING_RE='^- \[[xX]\] Link to preview will be available soon \(avg deploy time .+\)'
LEGACY_URL_RE='^- \[[xX]\] https://'

preview_pr_format_avg() {
  local seconds="${1:-61}"
  if ! [[ "$seconds" =~ ^[0-9]+$ ]]; then
    seconds=61
  fi
  if (( seconds < 60 )); then
    printf '%ss' "$seconds"
  else
    printf '%sm%ss' "$((seconds / 60))" "$((seconds % 60))"
  fi
}

preview_pr_loading_line() {
  local avg_seconds="${1:-61}"
  printf '%s%s)' "$LOADING_PREFIX" "$(preview_pr_format_avg "$avg_seconds")"
}

preview_pr_running_line() {
  local url="$1"
  printf -- '- [x] [Preview](%s)' "$url"
}

preview_pr_is_control_line() {
  local line="$1"
  [[ "$line" == "$IDLE_LINE" || "$line" == "$TICKED_LINE" ]] && return 0
  [[ "$line" =~ $LOADING_RE ]] && return 0
  [[ "$line" =~ $RUNNING_RE ]] && return 0
  [[ "$line" =~ $LEGACY_URL_RE ]] && return 0
  # HTML running forms we may try (GitHub usually sanitizes these).
  [[ "$line" =~ ^-\ \[[xX]\]\ \<a\  ]] && return 0
  return 1
}

preview_pr_control_rank() {
  local line="$1"
  if [[ "$line" =~ $RUNNING_RE || "$line" =~ $LEGACY_URL_RE || "$line" =~ ^-\ \[[xX]\]\ \<a\  ]]; then
    echo 4
  elif [[ "$line" =~ $LOADING_RE ]]; then
    echo 3
  elif [[ "$line" == "$TICKED_LINE" ]]; then
    echo 2
  elif [[ "$line" == "$IDLE_LINE" ]]; then
    echo 1
  else
    echo 0
  fi
}

preview_pr_control_line() {
  local body="$1"
  PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" PREVIEW_BODY="$body" python3 - <<'PY'
import os, re
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
body = os.environ["PREVIEW_BODY"]
loading_re = re.compile(r"^- \[[xX]\] Link to preview will be available soon \(avg deploy time .+\)$")
running_re = re.compile(r"^- \[[xX]\] \[Preview\]\(.+\)$")
legacy_re = re.compile(r"^- \[[xX]\] https://")
html_re = re.compile(r"^- \[[xX]\] <a ")
best, best_rank = None, -1
for line in body.splitlines():
    if running_re.match(line) or legacy_re.match(line) or html_re.match(line):
        rank = 4
    elif loading_re.match(line):
        rank = 3
    elif line == ticked:
        rank = 2
    elif line == idle:
        rank = 1
    else:
        continue
    if rank > best_rank:
        best, best_rank = line, rank
if best:
    print(best, end="")
PY
}

preview_pr_line1() {
  local body="$1"
  printf '%s' "$body" | sed -n '1p'
}

# Always keep exactly one control line as physical line 1; strip every other match.
preview_pr_set_control_line() {
  local body="$1"
  local new_line="$2"
  PREVIEW_NEW_LINE="$new_line" PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" PREVIEW_BODY="$body" python3 - <<'PY'
import os, re, sys
new_line = os.environ["PREVIEW_NEW_LINE"]
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
body = os.environ["PREVIEW_BODY"]
loading_re = re.compile(r"^- \[[xX]\] Link to preview will be available soon \(avg deploy time .+\)$")
running_re = re.compile(r"^- \[[xX]\] \[Preview\]\(.+\)$")
legacy_re = re.compile(r"^- \[[xX]\] https://")
html_re = re.compile(r"^- \[[xX]\] <a ")

def is_control(line: str) -> bool:
    return (
        line in (idle, ticked)
        or bool(loading_re.match(line))
        or bool(running_re.match(line))
        or bool(legacy_re.match(line))
        or bool(html_re.match(line))
    )

lines = body.splitlines()
kept = [ln for ln in lines if not is_control(ln)]
while kept and kept[0] == "":
    kept.pop(0)
out = [new_line, ""] + kept if kept else [new_line]
sys.stdout.write("\n".join(out))
if body.endswith("\n") or not body:
    sys.stdout.write("\n")
PY
}

preview_pr_has_checkbox() {
  local line
  line="$(preview_pr_control_line "$1")"
  [[ -n "$line" ]] && preview_pr_is_control_line "$line"
}

preview_pr_should_start() {
  local arg="$1"
  local line
  if [[ "$arg" == *$'\n'* ]]; then
    line="$(preview_pr_control_line "$arg")"
  else
    line="$arg"
  fi
  [[ "$line" == "$TICKED_LINE" ]]
}

preview_pr_is_loading() {
  local line="$1"
  [[ "$line" =~ $LOADING_RE ]]
}

preview_pr_is_running_marker() {
  local line="$1"
  [[ "$line" =~ $RUNNING_RE || "$line" =~ $LEGACY_URL_RE ]]
}

preview_pr_set_line1() {
  preview_pr_set_control_line "$1" "$2"
}

preview_pr_ensure_idle_checkbox() {
  local body="$1"
  local current
  current="$(preview_pr_control_line "$body")"
  if [[ -n "$current" ]]; then
    # Normalize to a single top control line; keep non-idle states as-is.
    preview_pr_set_control_line "$body" "$current"
    return 0
  fi
  preview_pr_set_control_line "$body" "$IDLE_LINE"
}

preview_pr_set_loading() {
  local body="$1"
  local avg_seconds="${2:-61}"
  preview_pr_set_control_line "$body" "$(preview_pr_loading_line "$avg_seconds")"
}

preview_pr_set_url() {
  local body="$1"
  local url="$2"
  preview_pr_set_control_line "$body" "$(preview_pr_running_line "$url")"
}

preview_pr_reset_idle() {
  local body="$1"
  preview_pr_set_control_line "$body" "$IDLE_LINE"
}

preview_pr_extract_url() {
  local line="$1"
  local re
  re='\[Preview\]\((https://[^)]+)\)'
  if [[ "$line" =~ $re ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  re='^- \[[xX]\] (https://[^[:space:]]+)'
  if [[ "$line" =~ $re ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}
