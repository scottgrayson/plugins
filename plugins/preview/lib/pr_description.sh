#!/usr/bin/env bash
set -euo pipefail

IDLE_LINE='- [ ] Check to generate preview'
TICKED_LINE='- [x] Check to generate preview'

preview_pr_format_duration() {
  local seconds="${1:-0}"
  if ! [[ "$seconds" =~ ^[0-9]+$ ]]; then
    seconds=0
  fi
  if (( seconds <= 0 )); then
    printf '1m'
    return 0
  fi
  if (( seconds < 60 )); then
    printf '%ss' "$seconds"
  elif (( seconds % 60 == 0 )); then
    printf '%sm' "$((seconds / 60))"
  else
    printf '%sm%ss' "$((seconds / 60))" "$((seconds % 60))"
  fi
}

preview_pr_parse_duration() {
  local text="$1"
  python3 -c '
import re, sys
t = sys.argv[1].strip()
m = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", t)
if not m or (m.group(1) is None and m.group(2) is None):
    sys.exit(1)
print(int(m.group(1) or 0) * 60 + int(m.group(2) or 0))
' "$text"
}

preview_pr_format_et() {
  local epoch="${1:-}"
  PREVIEW_EPOCH="$epoch" python3 - <<'PY'
import os
from datetime import datetime
from zoneinfo import ZoneInfo
raw = os.environ.get("PREVIEW_EPOCH", "").strip()
tz = ZoneInfo("America/New_York")
dt = datetime.fromtimestamp(int(raw), tz=tz) if raw else datetime.now(tz)
hour = dt.strftime("%I").lstrip("0") or "0"
print(f"{hour}:{dt.strftime('%M %p')} ET")
PY
}

preview_pr_loading_line() {
  local started_et="$1"
  local estimate_text="${2:-}"
  if [[ -n "$estimate_text" ]]; then
    printf 'Preview deploy started at %s (%s)' "$started_et" "$estimate_text"
  else
    printf 'Preview deploy started at %s' "$started_et"
  fi
}

preview_pr_running_line() {
  local url="$1"
  local created_et="$2"
  local idle_text="$3"
  printf '[Preview Website](%s) · created %s · expires after %s idle' "$url" "$created_et" "$idle_text"
}

preview_pr_get_last_deploy() {
  local body="$1"
  PREVIEW_BODY="$body" python3 - <<'PY'
import os, re
body = os.environ.get("PREVIEW_BODY", "")
m = re.search(r"<!--\s*preview:last-deploy=([0-9]+m(?:[0-9]+s)?|[0-9]+s)\s*-->", body)
if m:
    print(m.group(1), end="")
PY
}

preview_pr_set_last_deploy() {
  local body="$1"
  local seconds="$2"
  local compact
  compact="$(preview_pr_format_duration "$seconds")"
  PREVIEW_BODY="$body" PREVIEW_LAST="$compact" python3 - <<'PY'
import os, re
body = os.environ["PREVIEW_BODY"]
compact = os.environ["PREVIEW_LAST"]
comment = f"<!-- preview:last-deploy={compact} -->"
body2, n = re.subn(r"<!--\s*preview:last-deploy=[^>]*-->", comment, body, count=1)
if n:
    print(body2, end="" if body2.endswith("\n") else "\n")
else:
    lines = body.splitlines()
    insert_at = 1 if lines else 0
    if insert_at < len(lines) and lines[insert_at] == "":
        insert_at += 1
    lines[insert_at:insert_at] = [comment, ""]
    text = "\n".join(lines)
    print(text if text.endswith("\n") else text + "\n", end="")
PY
}

preview_pr_is_control_line() {
  local line="$1"
  PREVIEW_LINE="$line" PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" python3 - <<'PY'
import os, re, sys
line = os.environ["PREVIEW_LINE"]
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
if line in (idle, ticked):
    sys.exit(0)
patterns = [
    r"^Preview deploy started at ",
    r"^\[Preview Website\]\(",
    r"^- \[[xX]\] Link to preview will be available soon",
    r"^- \[[xX]\] \[Preview\]\(",
    r"^- \[[xX]\] https://",
    r"^- \[[xX]\] <a ",
]
sys.exit(0 if any(re.match(p, line) for p in patterns) else 1)
PY
}

preview_pr_control_line() {
  local body="$1"
  PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" PREVIEW_BODY="$body" python3 - <<'PY'
import os, re
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
body = os.environ["PREVIEW_BODY"]
ranked = [
    (4, lambda s: bool(re.match(r"^\[Preview Website\]\(", s))),
    (4, lambda s: bool(re.match(r"^- \[[xX]\] \[Preview\]\(", s))),
    (4, lambda s: bool(re.match(r"^- \[[xX]\] https://", s))),
    (3, lambda s: s.startswith("Preview deploy started at ")),
    (3, lambda s: bool(re.match(r"^- \[[xX]\] Link to preview will be available soon", s))),
    (2, lambda s: s == ticked),
    (1, lambda s: s == idle),
]
best, best_rank = None, -1
for line in body.splitlines():
    for rank, fn in ranked:
        if fn(line) and rank > best_rank:
            best, best_rank = line, rank
            break
if best:
    print(best, end="")
PY
}

preview_pr_line1() {
  local body="$1"
  printf '%s' "$body" | sed -n '1p'
}

preview_pr_set_control_line() {
  local body="$1"
  local new_line="$2"
  PREVIEW_NEW_LINE="$new_line" PREVIEW_IDLE="$IDLE_LINE" PREVIEW_TICKED="$TICKED_LINE" PREVIEW_BODY="$body" python3 - <<'PY'
import os, re, sys
new_line = os.environ["PREVIEW_NEW_LINE"]
idle = os.environ["PREVIEW_IDLE"]
ticked = os.environ["PREVIEW_TICKED"]
body = os.environ["PREVIEW_BODY"]
control_res = [
    re.compile(r"^\[Preview Website\]\("),
    re.compile(r"^Preview deploy started at "),
    re.compile(r"^- \[[xX]\] Check to generate preview$"),
    re.compile(r"^- \[ \] Check to generate preview$"),
    re.compile(r"^- \[[xX]\] Link to preview will be available soon"),
    re.compile(r"^- \[[xX]\] \[Preview\]\("),
    re.compile(r"^- \[[xX]\] https://"),
    re.compile(r"^- \[[xX]\] <a "),
]

def is_control(line: str) -> bool:
    if line in (idle, ticked):
        return True
    return any(rx.match(line) for rx in control_res)

lines = body.splitlines()
kept = [ln for ln in lines if not is_control(ln)]
while kept and kept[0] == "":
    kept.pop(0)
out_lines = [new_line, ""] + kept if kept else [new_line]
text = "\n".join(out_lines)
sys.stdout.write(text)
if (body.endswith("\n") or not body) and not text.endswith("\n"):
    sys.stdout.write("\n")
PY
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

preview_pr_set_line1() {
  preview_pr_set_control_line "$1" "$2"
}

preview_pr_ensure_idle_checkbox() {
  local body="$1"
  local current
  current="$(preview_pr_control_line "$body")"
  if [[ -n "$current" ]]; then
    preview_pr_set_control_line "$body" "$current"
    return 0
  fi
  preview_pr_set_control_line "$body" "$IDLE_LINE"
}

preview_pr_set_loading() {
  local body="$1"
  local started_et="$2"
  local last_deploy_compact="${3:-}"
  local estimate=""
  if [[ -n "$last_deploy_compact" ]]; then
    estimate="estimate $last_deploy_compact"
  fi
  preview_pr_set_control_line "$body" "$(preview_pr_loading_line "$started_et" "$estimate")"
}

preview_pr_set_running() {
  local body="$1"
  local url="$2"
  local created_et="$3"
  local idle_seconds="$4"
  preview_pr_set_control_line "$body" "$(preview_pr_running_line "$url" "$created_et" "$(preview_pr_format_duration "$idle_seconds")")"
}

preview_pr_set_url() {
  preview_pr_set_running "$1" "$2" "${3:-$(preview_pr_format_et)}" "${4:-300}"
}

preview_pr_reset_idle() {
  preview_pr_set_control_line "$1" "$IDLE_LINE"
}

preview_pr_extract_url() {
  local line="$1"
  local re='\[Preview Website\]\((https://[^)]+)\)'
  if [[ "$line" =~ $re ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  re='\[Preview\]\((https://[^)]+)\)'
  if [[ "$line" =~ $re ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

preview_pr_format_avg() {
  preview_pr_format_duration "$@"
}
