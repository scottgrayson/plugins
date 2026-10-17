#!/usr/bin/env bash
set -euo pipefail

IDLE_LINE='- [ ] Check to generate preview'
TICKED_LINE='- [x] Check to generate preview'

preview_pr_is_control_line() {
  local line="$1"
  [[ "$line" == "$IDLE_LINE" || "$line" == "$TICKED_LINE" || "$line" =~ ^-\ \[[xX]\]\ https:// ]]
}

preview_pr_control_line() {
  local body="$1"
  PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" python3 -c '
import os, sys
body = sys.stdin.read()
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
best = None
best_rank = -1
for line in body.splitlines():
    if line.startswith("- [x] https://") or line.startswith("- [X] https://"):
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
' <<<"$body"
}

preview_pr_line1() {
  local body="$1"
  local control
  control="$(preview_pr_control_line "$body")"
  if [[ -n "$control" ]]; then
    printf '%s' "$control"
    return 0
  fi
  printf '%s' "$body" | sed -n '1p'
}

preview_pr_set_control_line() {
  local body="$1"
  local new_line="$2"
  PREVIEW_NEW_LINE="$new_line" PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" python3 -c '
import os, sys
new_line = os.environ["PREVIEW_NEW_LINE"]
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
body = sys.stdin.read()
lines = body.splitlines()
idxs = [i for i, line in enumerate(lines)
        if line in (idle, ticked)
        or line.startswith("- [x] https://")
        or line.startswith("- [X] https://")]
if idxs:
    lines[idxs[0]] = new_line
    for i in reversed(idxs[1:]):
        del lines[i]
else:
    insert_at = 0
    for i, line in enumerate(lines):
        if "CURSOR_AGENT_PR_BODY_BEGIN" in line:
            insert_at = i
            break
    lines[insert_at:insert_at] = [new_line, ""]
sys.stdout.write("\n".join(lines))
if body.endswith("\n"):
    sys.stdout.write("\n")
' <<<"$body"
}

preview_pr_has_checkbox() {
  local line1
  line1="$(preview_pr_control_line "$1")"
  [[ -n "$line1" ]] && preview_pr_is_control_line "$line1"
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

preview_pr_is_running_marker() {
  local line="$1"
  [[ "$line" =~ ^-\ \[[xX]\]\ https:// ]]
}

preview_pr_set_line1() {
  preview_pr_set_control_line "$1" "$2"
}

preview_pr_ensure_idle_checkbox() {
  local body="$1"
  if preview_pr_has_checkbox "$body"; then
    local current
    current="$(preview_pr_control_line "$body")"
    preview_pr_set_control_line "$body" "$current"
    return 0
  fi
  preview_pr_set_control_line "$body" "$IDLE_LINE"
}

preview_pr_set_url() {
  local body="$1"
  local url="$2"
  preview_pr_set_control_line "$body" "- [x] $url"
}

preview_pr_reset_idle() {
  local body="$1"
  preview_pr_set_control_line "$body" "$IDLE_LINE"
}
