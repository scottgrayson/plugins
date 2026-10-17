#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN="$ROOT/plugins/preview"
LIB="$PLUGIN/lib"
PASS=0
FAIL=0

assert_eq() {
  local name="$1" got="$2" want="$3"
  if [[ "$got" == "$want" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name"
    echo "  got:  $got"
    echo "  want: $want"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local name="$1" hay="$2" needle="$3"
  if [[ "$hay" == *"$needle"* ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name (missing '$needle')"
    echo "  hay: $hay"
    FAIL=$((FAIL + 1))
  fi
}

assert_fail_contains() {
  local name="$1" needle="$2"
  shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if [[ $rc -ne 0 && "$out" == *"$needle"* ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name (rc=$rc)"
    echo "  out: $out"
    FAIL=$((FAIL + 1))
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# shellcheck source=/dev/null
source "$LIB/config.sh"
# shellcheck source=/dev/null
source "$LIB/pr_description.sh"
# shellcheck source=/dev/null
source "$LIB/watchdog.sh"

mkdir -p "$TMP/app/.preview/steps"
cat >"$TMP/app/.preview/config.json" <<'JSON'
{
  "type": "static",
  "port": 4321,
  "idleSeconds": 30,
  "defaultSteps": ["seed"],
  "ignorePaths": ["/healthz*"],
  "root": "."
}
JSON
printf '#!/usr/bin/env bash\necho seed\n' >"$TMP/app/.preview/steps/seed.sh"
printf '#!/usr/bin/env bash\necho migrate\n' >"$TMP/app/.preview/steps/migrate.sh"
chmod +x "$TMP/app/.preview/steps/"*.sh

export PREVIEW_STATE_DIR="$TMP/state-config"
mkdir -p "$PREVIEW_STATE_DIR"
pushd "$TMP/app" >/dev/null
preview_load_config "$TMP/app/.preview/config.json"
assert_eq "config port" "$PREVIEW_PORT" "4321"
assert_eq "config idle" "$PREVIEW_IDLE_SECONDS" "30"
assert_eq "config type" "$PREVIEW_TYPE" "static"
assert_contains "config ignorePaths" "$PREVIEW_IGNORE_PATHS" "/healthz*"
assert_contains "config defaultSteps" "$PREVIEW_DEFAULT_STEPS" "seed"
assert_eq "default config path" "$(preview_default_config_path)" "$TMP/app/.preview/config.json"
popd >/dev/null

export PREVIEW_STATE_DIR="$TMP/state-defaults"
mkdir -p "$PREVIEW_STATE_DIR"
pushd "$TMP" >/dev/null
preview_load_config "$TMP/missing.json"
assert_eq "default idle 5m" "$PREVIEW_IDLE_SECONDS" "300"
assert_eq "default cap 15m" "$PREVIEW_TOTAL_CAP_SECONDS" "900"
assert_eq "default type" "$PREVIEW_TYPE" "static"
popd >/dev/null

export PREVIEW_STATE_DIR="$TMP/state-steps"
mkdir -p "$PREVIEW_STATE_DIR"
pushd "$TMP/app" >/dev/null
preview_load_config "$TMP/app/.preview/config.json"
got="$(preview_resolve_steps "" "$TMP/app/.preview/steps" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
assert_eq "default steps when no labels" "$got" "seed"
got="$(preview_resolve_steps "preview:migrate,other" "$TMP/app/.preview/steps" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
assert_eq "label selects migrate" "$got" "migrate"
assert_fail_contains "unknown step lists valid" "unknown step 'nope'" \
  bash -c "source '$LIB/config.sh'; export PREVIEW_STATE_DIR='$PREVIEW_STATE_DIR'; preview_load_config '$TMP/app/.preview/config.json'; preview_resolve_steps 'preview:nope' '$TMP/app/.preview/steps'"
out="$(bash -c "source '$LIB/config.sh'; export PREVIEW_STATE_DIR='$PREVIEW_STATE_DIR'; preview_load_config '$TMP/app/.preview/config.json'; preview_resolve_steps 'preview:nope' '$TMP/app/.preview/steps'" 2>&1 || true)"
assert_contains "unknown step mentions migrate" "$out" "migrate"
popd >/dev/null

python3 - "$PLUGIN/lib/proxy.py" <<'PY'
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("preview_proxy", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Tracker = mod.CountTracker

# Built-in *poll* + repo ignorePaths; no Livewire/interval heuristic.
t = Tracker(["/healthz*"])
assert t.should_count("GET", "/poll") == (False, "ignorePaths"), t.should_count("GET", "/poll")
assert t.should_count("GET", "/api/poller") == (False, "ignorePaths")
assert t.should_count("GET", "/livewire/message") == (True, "user")  # not special-cased
assert t.should_count("GET", "/healthz/ready") == (False, "ignorePaths")
assert t.should_count("GET", "/app.js") == (False, "static")
assert t.should_count("GET", "/assets/x") == (False, "static")
assert t.should_count("GET", "/ping") == (True, "user")
assert t.should_count("POST", "/form") == (True, "user")

# Explicit ignorePaths can still name /poll; default already covers it.
explicit = Tracker(["/poll"])
assert explicit.should_count("GET", "/poll") == (False, "ignorePaths")
assert explicit.should_count("GET", "/ping") == (True, "user")

# /ping must always count (intentional keep-alive in the example app).
pinger = Tracker([])
for _ in range(6):
    assert pinger.should_count("GET", "/ping") == (True, "user")

print("proxy helpers ok")
PY
assert_eq "proxy generic ignore helpers" "ok" "ok"

# Agent mode: idle disabled (idle=0); total cap still applies.
export PREVIEW_STATE_DIR="$TMP/state-agent"
mkdir -p "$PREVIEW_STATE_DIR"
preview_ensure_state
preview_write started_at "$(date +%s)"
preview_write ready_at "$(date +%s)"
preview_write status watching
: >"$PREVIEW_STATE_DIR/requests.log"
# Simulate counted traffic so a non-agent idle would reset — agent must ignore idle.
python3 - <<'PY'
import time
from datetime import datetime, timezone
log = __import__("os").environ["PREVIEW_STATE_DIR"] + "/requests.log"
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
with open(log, "a", encoding="utf-8") as f:
    f.write(f"{ts}\tGET\t/\t1\tuser\n")
PY
export PREVIEW_AGENT_MODE=1
export PREVIEW_IDLE_SECONDS=2
export PREVIEW_TOTAL_CAP_SECONDS=5
# Foreground: past idle (2s) it must still be running until total-cap (5s).
preview_watchdog
reason="$(preview_read reason)"
assert_eq "agent mode stops on total-cap not idle" "$reason" "total-cap"
unset PREVIEW_AGENT_MODE

# Non-agent: idle fires when no counted requests after READY.
export PREVIEW_STATE_DIR="$TMP/state-idle"
mkdir -p "$PREVIEW_STATE_DIR"
preview_ensure_state
now="$(date +%s)"
preview_write started_at "$now"
preview_write ready_at "$((now - 5))"
preview_write status watching
: >"$PREVIEW_STATE_DIR/requests.log"
# Only ignored poll traffic:
python3 - <<'PY'
from datetime import datetime, timezone
import os
log = os.environ["PREVIEW_STATE_DIR"] + "/requests.log"
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
with open(log, "a", encoding="utf-8") as f:
    f.write(f"{ts}\tGET\t/poll\t0\tignorePaths\n")
PY
export PREVIEW_IDLE_SECONDS=2
export PREVIEW_TOTAL_CAP_SECONDS=600
preview_watchdog
reason="$(preview_read reason)"
assert_eq "idle timeout when only poll traffic" "$reason" "idle"

body=$'- [ ] Check to generate preview\n\n## Summary\nHi'
assert_eq "should start on tick" "$(preview_pr_should_start '- [x] Check to generate preview' && echo yes || echo no)" "yes"
assert_eq "should not start on idle" "$(preview_pr_should_start '- [ ] Check to generate preview' && echo yes || echo no)" "no"
assert_eq "should not start on running" "$(preview_pr_should_start '[Preview Website](https://x.trycloudflare.com) · created 1:00 PM ET · expires after 5m idle' && echo yes || echo no)" "no"
assert_eq "format duration seconds" "$(preview_pr_format_duration 21)" "21s"
assert_eq "format duration minute" "$(preview_pr_format_duration 60)" "1m"
assert_eq "format duration mixed" "$(preview_pr_format_duration 61)" "1m1s"
assert_eq "format duration 5m" "$(preview_pr_format_duration 300)" "5m"

loading_first="$(preview_pr_set_loading "$body" '11:52 AM ET' '')"
assert_eq "loading without history" "$(preview_pr_line1 "$loading_first")" "Preview deploy started at 11:52 AM ET"
loading_hist="$(preview_pr_set_loading "$body" '11:52 AM ET' '29s')"
assert_eq "loading with history" "$(preview_pr_line1 "$loading_hist")" "Preview deploy started at 11:52 AM ET (estimate 29s)"

new="$(preview_pr_set_running "$body" 'https://x.trycloudflare.com' '11:52 AM ET' 300)"
assert_eq "running line1 5m idle" "$(preview_pr_line1 "$new")" "[Preview Website](https://x.trycloudflare.com) · created 11:52 AM ET · expires after 5m idle"
assert_contains "preserves rest" "$new" "## Summary"
# last-deploy comment
with_deploy="$(preview_pr_set_last_deploy "$new" 29)"
assert_contains "writes last-deploy comment" "$with_deploy" "<!-- preview:last-deploy=29s -->"
assert_eq "reads last-deploy" "$(preview_pr_get_last_deploy "$with_deploy")" "29s"

dup=$'- [ ] Check to generate preview\n\n<!-- CURSOR_AGENT_PR_BODY_BEGIN -->\n- [x] Check to generate preview\n\nHi'
collapsed="$(preview_pr_set_loading "$dup" '11:52 AM ET' '21s')"
assert_eq "collapse to top" "$(preview_pr_line1 "$collapsed")" "Preview deploy started at 11:52 AM ET (estimate 21s)"
assert_eq "no ticked leftover" "$(grep -c 'Check to generate preview' <<<"$collapsed" || true)" "0"
reset="$(preview_pr_reset_idle "$new")"
assert_eq "reset idle" "$(preview_pr_line1 "$reset")" "- [ ] Check to generate preview"
ensured="$(preview_pr_ensure_idle_checkbox 'hello')"
assert_eq "ensure inserts" "$(preview_pr_line1 "$ensured")" "- [ ] Check to generate preview"

# Brand word ban (needle split so this file is clean)
needle="t""app"
if rg -n -i --glob '!.git/**' "\\b${needle}\\b" "$ROOT" >/dev/null 2>&1; then
  echo "FAIL forbidden brand whole-word present"
  FAIL=$((FAIL + 1))
else
  echo "PASS brand word ban"
  PASS=$((PASS + 1))
fi

# No onDemand / triggers list in plugin
if rg -n 'onDemand|triggers\s*:' "$PLUGIN" "$ROOT/.github/workflows/preview.yml" "$ROOT/.github/workflows/preview-reusable.yml" 2>/dev/null | rg -v 'Custom triggers|no `onDemand`|no triggers list|onDemand flag'; then
  echo "FAIL onDemand/triggers found"
  FAIL=$((FAIL + 1))
else
  echo "PASS no onDemand/triggers in shipped plugin"
  PASS=$((PASS + 1))
fi

# Shipped workflow is checkbox-only (no issue_comment)
if rg -n 'issue_comment' "$ROOT/.github/workflows/preview.yml" >/dev/null 2>&1; then
  echo "FAIL shipped preview.yml still has issue_comment"
  FAIL=$((FAIL + 1))
else
  echo "PASS shipped workflow checkbox-only"
  PASS=$((PASS + 1))
fi

# Security: never pull_request_target; same-repo gate on every reusable job
WF_PREVIEW="$ROOT/.github/workflows/preview.yml"
WF_REUSABLE="$ROOT/.github/workflows/preview-reusable.yml"
SAME_REPO='github.event.pull_request.head.repo.full_name == github.repository'
if rg -n 'pull_request_target' "$WF_PREVIEW" "$WF_REUSABLE" >/dev/null 2>&1; then
  echo "FAIL pull_request_target present"
  FAIL=$((FAIL + 1))
else
  echo "PASS no pull_request_target"
  PASS=$((PASS + 1))
fi
# preview.yml must use pull_request (not target)
if yq -e '.on.pull_request' "$WF_PREVIEW" >/dev/null 2>&1 && ! yq -e '.on.pull_request_target' "$WF_PREVIEW" >/dev/null 2>&1; then
  echo "PASS preview.yml on.pull_request only"
  PASS=$((PASS + 1))
else
  echo "FAIL preview.yml on: must be pull_request only"
  FAIL=$((FAIL + 1))
fi
jobs_missing=""
for job in ensure-checkbox mark-loading preview finalize; do
  ifc="$(yq -r ".jobs.\"$job\".if // \"\"" "$WF_REUSABLE")"
  if [[ "$ifc" != *"$SAME_REPO"* ]]; then
    jobs_missing+="$job "
  fi
done
if [[ -z "$jobs_missing" ]]; then
  echo "PASS reusable jobs gate fork PRs"
  PASS=$((PASS + 1))
else
  echo "FAIL reusable jobs missing same-repo if: $jobs_missing"
  FAIL=$((FAIL + 1))
fi

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
