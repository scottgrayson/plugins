#!/usr/bin/env bash
# Preview unit / contract tests.
# CI runs this via the `tests` job. Agent-mode e2e is a separate `e2e-agent` job
# (`dev/run-e2e-agent.sh`). Locally run both with:
#   bash dev/run-tests.sh --with-e2e
# or:
#   bash dev/run-tests.sh && bash dev/run-e2e-agent.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN="$ROOT/plugins/preview"
LIB="$PLUGIN/lib"
AI_AUDIT_TESTS="$ROOT/plugins/ai-audit/tests/run-tests.sh"
PASS=0
FAIL=0

WITH_E2E=0
for arg in "$@"; do
  case "$arg" in
    --with-e2e) WITH_E2E=1 ;;
    -h|--help)
      echo "Usage: $0 [--with-e2e]"
      echo "  --with-e2e  Also run dev/run-e2e-agent.sh (default CI runs e2e separately)."
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# ripgrep for brand-ban / workflow greps (not always on ubuntu-latest).
if ! command -v rg >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq ripgrep >/dev/null
  else
    RG_DIR="$ROOT/dev/.bin"
    mkdir -p "$RG_DIR"
    if [[ ! -x "$RG_DIR/rg" ]]; then
      curl -fsSL -o /tmp/rg.tar.gz         https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz
      tar -xzf /tmp/rg.tar.gz -C /tmp
      mv /tmp/ripgrep-14.1.1-x86_64-unknown-linux-musl/rg "$RG_DIR/rg"
      chmod +x "$RG_DIR/rg"
    fi
    export PATH="$RG_DIR:$PATH"
  fi
fi

# mikefarah yq (workflow YAML assertions). Install if missing or not mikefarah.
if ! command -v yq >/dev/null 2>&1 || ! yq --version 2>&1 | grep -qi mikefarah; then
  YQ_BIN="$ROOT/dev/.bin/yq"
  mkdir -p "$(dirname "$YQ_BIN")"
  if [[ ! -x "$YQ_BIN" ]]; then
    curl -fsSL -o "$YQ_BIN"       https://github.com/mikefarah/yq/releases/download/v4.44.3/yq_linux_amd64
    chmod +x "$YQ_BIN"
  fi
  export PATH="$(dirname "$YQ_BIN"):$PATH"
fi

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
unset PREVIEW_IDLE_SECONDS PREVIEW_TOTAL_CAP_SECONDS PREVIEW_PORT PREVIEW_TYPE PREVIEW_START PREVIEW_ROOT PREVIEW_DEFAULT_STEPS PREVIEW_IGNORE_PATHS || true
preview_load_config "$TMP/missing.json"
assert_eq "default idle 5m" "$PREVIEW_IDLE_SECONDS" "300"
assert_eq "default cap 15m" "$PREVIEW_TOTAL_CAP_SECONDS" "900"
assert_eq "default type" "$PREVIEW_TYPE" "static"
popd >/dev/null

# Pre-set env wins over config.json (config.env must not clobber).
export PREVIEW_STATE_DIR="$TMP/state-env-win"
mkdir -p "$PREVIEW_STATE_DIR"
export PREVIEW_TOTAL_CAP_SECONDS=42
pushd "$TMP/app" >/dev/null
preview_load_config "$TMP/app/.preview/config.json"
assert_eq "pre-set PREVIEW_TOTAL_CAP_SECONDS wins" "$PREVIEW_TOTAL_CAP_SECONDS" "42"
# Port still comes from config (not pre-set).
assert_eq "config port still loaded" "$PREVIEW_PORT" "4321"
popd >/dev/null
unset PREVIEW_TOTAL_CAP_SECONDS

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

# Agent mode: no timers — tiny idle + tiny cap must not stop the watchdog.
export PREVIEW_STATE_DIR="$TMP/state-agent"
mkdir -p "$PREVIEW_STATE_DIR"
preview_ensure_state
preview_write started_at "$(date +%s)"
preview_write ready_at "$(date +%s)"
preview_write status watching
: >"$PREVIEW_STATE_DIR/requests.log"
export PREVIEW_AGENT_MODE=1
export PREVIEW_IDLE_SECONDS=1
export PREVIEW_TOTAL_CAP_SECONDS=2
preview_watchdog &
agent_wd_pid=$!
sleep 4
if kill -0 "$agent_wd_pid" 2>/dev/null; then
  echo "PASS agent mode still alive past tiny cap"
  PASS=$((PASS + 1))
  preview_write reason "stop"
  preview_write stop "1"
  wait "$agent_wd_pid" 2>/dev/null || true
  reason="$(preview_read reason)"
  assert_eq "agent mode stop reason is stop not total-cap" "$reason" "stop"
else
  echo "FAIL agent mode died on timer (reason=$(preview_read reason))"
  FAIL=$((FAIL + 1))
  wait "$agent_wd_pid" 2>/dev/null || true
fi
unset PREVIEW_AGENT_MODE

# Human mode: total cap still enforced.
export PREVIEW_STATE_DIR="$TMP/state-human-cap"
mkdir -p "$PREVIEW_STATE_DIR"
preview_ensure_state
preview_write started_at "$(date +%s)"
preview_write ready_at "$(date +%s)"
preview_write status watching
: >"$PREVIEW_STATE_DIR/requests.log"
unset PREVIEW_AGENT_MODE || true
export PREVIEW_IDLE_SECONDS=600
export PREVIEW_TOTAL_CAP_SECONDS=3
preview_watchdog
reason="$(preview_read reason)"
assert_eq "human mode still stops on total-cap" "$reason" "total-cap"

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

# Agent CLI: --share / --detach documented; agent default is local-only
help_out="$("$PLUGIN/bin/preview" --help 2>&1 || true)"
assert_contains "help mentions --share" "$help_out" "--share"
assert_contains "help mentions --detach" "$help_out" "--detach"
assert_contains "help mentions share command" "$help_out" "share    Same as"
# Agent without --share must not start a tunnel (gate in bin/preview)
if grep -q 'use_tunnel=0' "$PLUGIN/bin/preview" \
  && grep -qE 'cmd_share|up --agent --share' "$PLUGIN/bin/preview"; then
  echo "PASS agent default skips tunnel; share opts in"
  PASS=$((PASS + 1))
else
  echo "FAIL agent local-only / share gate missing in bin/preview"
  FAIL=$((FAIL + 1))
fi

# Smoke: preview up --agent prints localhost, no trycloudflare, local HTTP works
SMOKE_DIR="$TMP/agent-local"
mkdir -p "$SMOKE_DIR/.preview"
cp "$ROOT/examples/preview/serve.py" "$ROOT/examples/preview/index.html" "$SMOKE_DIR/"
# Unique app port so parallel leftovers cannot collide
smoke_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
cat >"$SMOKE_DIR/.preview/config.json" <<JSON
{
  "type": "static",
  "start": "python3 serve.py",
  "port": $smoke_port,
  "idleSeconds": 300,
  "totalCapSeconds": 120
}
JSON
export PREVIEW_STATE_DIR="$SMOKE_DIR/.preview/run"
rm -rf "$PREVIEW_STATE_DIR"
("$PLUGIN/bin/preview" up --agent --config "$SMOKE_DIR/.preview/config.json" --workdir "$SMOKE_DIR" \
  >"$TMP/agent-local.stdout" 2>"$TMP/agent-local.stderr") &
smoke_pid=$!
url=""
for _ in $(seq 1 40); do
  if [[ -f "$PREVIEW_STATE_DIR/status" ]]; then
    st="$(cat "$PREVIEW_STATE_DIR/status" 2>/dev/null || true)"
    if [[ "$st" == "ready" || "$st" == "watching" ]]; then
      url="$(cat "$PREVIEW_STATE_DIR/url" 2>/dev/null || true)"
      [[ -n "$url" ]] && break
    fi
  fi
  if ! kill -0 "$smoke_pid" 2>/dev/null; then
    break
  fi
  sleep 0.25
done
if [[ "$url" == http://127.0.0.1:* ]] && [[ "$url" != *trycloudflare* ]]; then
  echo "PASS agent --agent URL is localhost (no tunnel)"
  PASS=$((PASS + 1))
else
  echo "FAIL agent --agent URL should be localhost, got: $url"
  echo "  stderr: $(tail -5 "$TMP/agent-local.stderr" 2>/dev/null || true)"
  FAIL=$((FAIL + 1))
fi
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 5 "$url/" 2>/dev/null || echo fail)"
if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then
  echo "PASS agent localhost HTTP $code"
  PASS=$((PASS + 1))
else
  echo "FAIL agent localhost HTTP (code=$code url=$url)"
  FAIL=$((FAIL + 1))
fi
# No trycloudflare anywhere in this run's state/logs
if grep -R -n 'trycloudflare' "$PREVIEW_STATE_DIR" "$TMP/agent-local.stdout" "$TMP/agent-local.stderr" >/dev/null 2>&1; then
  echo "FAIL agent --agent leaked trycloudflare URL"
  FAIL=$((FAIL + 1))
else
  echo "PASS agent --agent has no trycloudflare URL"
  PASS=$((PASS + 1))
fi
"$PLUGIN/bin/preview" stop smoke-test >/dev/null 2>&1 || true
wait "$smoke_pid" 2>/dev/null || true
unset PREVIEW_STATE_DIR

# Busy-port fail-fast: leftover listener on config port must not READY.
BUSY_DIR="$TMP/busy-port"
mkdir -p "$BUSY_DIR/.preview"
busy_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
# Hold the port with a decoy listener.
python3 - "$busy_port" <<'PY' &
import socket, sys, time
port = int(sys.argv[1])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(1)
time.sleep(60)
PY
busy_holder=$!
# Give the decoy a moment to bind.
for _ in $(seq 1 20); do
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.2); s.connect(('127.0.0.1', int(sys.argv[1]))); s.close()" "$busy_port" 2>/dev/null && break
  sleep 0.1
done
cat >"$BUSY_DIR/.preview/config.json" <<JSON
{
  "type": "static",
  "start": "python3 -c 'import time; time.sleep(30)'",
  "port": $busy_port,
  "totalCapSeconds": 60
}
JSON
export PREVIEW_STATE_DIR="$BUSY_DIR/.preview/run"
rm -rf "$PREVIEW_STATE_DIR"
busy_out="$("$PLUGIN/bin/preview" up --agent --detach --config "$BUSY_DIR/.preview/config.json" --workdir "$BUSY_DIR" 2>&1)" && busy_rc=0 || busy_rc=$?
if [[ $busy_rc -ne 0 && "$busy_out" == *"port $busy_port is already in use"* ]]; then
  echo "PASS busy-port fail-fast"
  PASS=$((PASS + 1))
else
  echo "FAIL busy-port fail-fast (rc=$busy_rc)"
  echo "  out: $busy_out"
  FAIL=$((FAIL + 1))
fi
kill "$busy_holder" 2>/dev/null || true
wait "$busy_holder" 2>/dev/null || true
unset PREVIEW_STATE_DIR

# App exits before READY: clear error naming the port.
DIE_DIR="$TMP/app-exit"
mkdir -p "$DIE_DIR/.preview"
die_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
cat >"$DIE_DIR/.preview/config.json" <<JSON
{
  "type": "static",
  "start": "python3 -c 'import sys; sys.exit(1)'",
  "port": $die_port,
  "totalCapSeconds": 60
}
JSON
export PREVIEW_STATE_DIR="$DIE_DIR/.preview/run"
rm -rf "$PREVIEW_STATE_DIR"
die_out="$("$PLUGIN/bin/preview" up --agent --detach --config "$DIE_DIR/.preview/config.json" --workdir "$DIE_DIR" 2>&1)" && die_rc=0 || die_rc=$?
if [[ $die_rc -ne 0 && "$die_out" == *"app exited before READY on port $die_port"* ]]; then
  echo "PASS app-exit-before-ready"
  PASS=$((PASS + 1))
else
  echo "FAIL app-exit-before-ready (rc=$die_rc)"
  echo "  out: $die_out"
  FAIL=$((FAIL + 1))
fi
unset PREVIEW_STATE_DIR

# --detach: exits 0 with URL; stop cleans up all pids.
DETACH_DIR="$TMP/detach"
mkdir -p "$DETACH_DIR/.preview"
cp "$ROOT/examples/preview/serve.py" "$ROOT/examples/preview/index.html" "$DETACH_DIR/"
detach_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
cat >"$DETACH_DIR/.preview/config.json" <<JSON
{
  "type": "static",
  "start": "python3 serve.py",
  "port": $detach_port,
  "idleSeconds": 300,
  "totalCapSeconds": 5
}
JSON
export PREVIEW_STATE_DIR="$DETACH_DIR/.preview/run"
rm -rf "$PREVIEW_STATE_DIR"
detach_out="$("$PLUGIN/bin/preview" up --agent --detach --config "$DETACH_DIR/.preview/config.json" --workdir "$DETACH_DIR" 2>"$TMP/detach.stderr")" && detach_rc=0 || detach_rc=$?
detach_url="$(echo "$detach_out" | tail -1)"
if [[ $detach_rc -eq 0 && "$detach_url" == http://127.0.0.1:* ]]; then
  echo "PASS --detach returns 0 with URL"
  PASS=$((PASS + 1))
else
  echo "FAIL --detach (rc=$detach_rc url=$detach_url)"
  echo "  stderr: $(tail -5 "$TMP/detach.stderr" 2>/dev/null || true)"
  FAIL=$((FAIL + 1))
fi
app_pid="$(cat "$PREVIEW_STATE_DIR/app_pid" 2>/dev/null || true)"
proxy_pid="$(cat "$PREVIEW_STATE_DIR/proxy_pid" 2>/dev/null || true)"
if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null \
  && [[ -n "$proxy_pid" ]] && kill -0 "$proxy_pid" 2>/dev/null; then
  echo "PASS --detach left app+proxy running"
  PASS=$((PASS + 1))
else
  echo "FAIL --detach processes not running (app=$app_pid proxy=$proxy_pid)"
  FAIL=$((FAIL + 1))
fi
# Tiny totalCapSeconds must not kill detached agent preview.
sleep 6
if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
  echo "PASS detached agent still alive past config totalCapSeconds"
  PASS=$((PASS + 1))
else
  echo "FAIL detached agent died on cap"
  FAIL=$((FAIL + 1))
fi
"$PLUGIN/bin/preview" stop detach-test >/dev/null 2>&1 || true
sleep 0.5
leftover=0
for pid in "$app_pid" "$proxy_pid"; do
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    leftover=1
  fi
done
if [[ $leftover -eq 0 ]]; then
  echo "PASS --detach stop cleans up pids"
  PASS=$((PASS + 1))
else
  echo "FAIL --detach stop left processes (app=$app_pid proxy=$proxy_pid)"
  FAIL=$((FAIL + 1))
  kill "$app_pid" "$proxy_pid" 2>/dev/null || true
fi
unset PREVIEW_STATE_DIR

# Release pinning: consumer docs @v1; Actions SHA-pinned; cloudflared version+sha256
if grep -n 'preview-reusable.yml@main' "$ROOT/README.md" "$PLUGIN/README.md" >/dev/null 2>&1; then
  echo "FAIL consumer README still pins @main"
  FAIL=$((FAIL + 1))
else
  echo "PASS consumer README pins @v1 (not @main)"
  PASS=$((PASS + 1))
fi
if grep -n 'preview-reusable.yml@v1' "$ROOT/README.md" "$PLUGIN/README.md" >/dev/null 2>&1; then
  echo "PASS consumer README shows @v1"
  PASS=$((PASS + 1))
else
  echo "FAIL consumer README missing @v1"
  FAIL=$((FAIL + 1))
fi
# This repo dogfoods local path
if grep -q 'uses: ./.github/workflows/preview-reusable.yml' "$ROOT/.github/workflows/preview.yml"; then
  echo "PASS dogfood preview.yml uses local ./"
  PASS=$((PASS + 1))
else
  echo "FAIL dogfood preview.yml must use local ./"
  FAIL=$((FAIL + 1))
fi
# No floating action tags like @v4 (must be full SHA)
if grep -R -nE 'uses:[[:space:]]*actions/[a-zA-Z0-9_-]+@v[0-9]' "$ROOT/.github/workflows" >/dev/null 2>&1; then
  echo "FAIL floating actions/*@vN tag in workflows"
  FAIL=$((FAIL + 1))
else
  echo "PASS no floating actions/*@vN tags"
  PASS=$((PASS + 1))
fi
pin_re='uses: actions/checkout@[0-9a-f]{40} # v[0-9]'
unpinned=$(grep -hE 'uses: actions/checkout@' "$ROOT/.github/workflows/ci.yml" "$ROOT/.github/workflows/preview-reusable.yml" | grep -vE "$pin_re" || true)
if [ -z "$unpinned" ] \
  && grep -qE "$pin_re" "$ROOT/.github/workflows/ci.yml" \
  && grep -qE "$pin_re" "$ROOT/.github/workflows/preview-reusable.yml"; then
  echo "PASS actions/checkout pinned to a full SHA"
  PASS=$((PASS + 1))
else
  echo "FAIL actions/checkout not SHA-pinned"
  FAIL=$((FAIL + 1))
fi
if grep -q 'PREVIEW_CLOUDFLARED_VERSION=.*2026.10.1' "$PLUGIN/lib/tunnel.sh" \
  && grep -q '3bfc539f45d2620c71debb7a86be0efa737ed5ccbad9eb85c787116900e5c586' "$PLUGIN/lib/tunnel.sh" \
  && grep -q '2c65dfabf616c40efc7545d107d7a57ad5e21377e2964b7e88af19a238074a46' "$PLUGIN/lib/tunnel.sh" \
  && grep -q 'sha256 mismatch' "$PLUGIN/lib/tunnel.sh"; then
  echo "PASS cloudflared pinned version + sha256 verify"
  PASS=$((PASS + 1))
else
  echo "FAIL cloudflared pin/verify missing"
  FAIL=$((FAIL + 1))
fi
if [[ -f "$ROOT/.github/dependabot.yml" ]] && grep -q 'package-ecosystem: github-actions' "$ROOT/.github/dependabot.yml"; then
  echo "PASS dependabot github-actions config"
  PASS=$((PASS + 1))
else
  echo "FAIL missing dependabot github-actions config"
  FAIL=$((FAIL + 1))
fi

if [[ "$WITH_E2E" -eq 1 ]]; then
  # Optional local full suite (CI runs e2e in the separate e2e-agent job).
  echo
  echo "== agent-mode e2e =="
  E2E_OUT="$(bash "$ROOT/dev/run-e2e-agent.sh" 2>&1)" || true
  printf '%s\n' "$E2E_OUT"
  E2E_PASS="$(printf '%s\n' "$E2E_OUT" | grep -cE '^PASS ' || true)"
  E2E_FAIL="$(printf '%s\n' "$E2E_OUT" | grep -cE '^FAIL ' || true)"
  PASS=$((PASS + E2E_PASS))
  FAIL=$((FAIL + E2E_FAIL))
  if [[ "$E2E_FAIL" -eq 0 && "$E2E_PASS" -gt 0 ]]; then
    echo "PASS agent-mode e2e suite ($E2E_PASS checks)"
    PASS=$((PASS + 1))
  else
    echo "FAIL agent-mode e2e suite (pass=$E2E_PASS fail=$E2E_FAIL)"
    FAIL=$((FAIL + 1))
  fi
fi

# ai-audit fixture suite (stage 1: detect + context)
echo
echo "== ai-audit =="
AI_RC=0
AI_OUT="$(bash "$AI_AUDIT_TESTS" 2>&1)" || AI_RC=$?
printf '%s\n' "$AI_OUT"
AI_PASS="$(printf '%s\n' "$AI_OUT" | grep -cE '^PASS ' || true)"
AI_FAIL="$(printf '%s\n' "$AI_OUT" | grep -cE '^FAIL ' || true)"
PASS=$((PASS + AI_PASS))
FAIL=$((FAIL + AI_FAIL))
# Fail on non-zero exit even when no "FAIL " lines were printed (e.g. crash mid-suite).
if [[ "$AI_RC" -ne 0 ]]; then
  echo "FAIL ai-audit suite exited non-zero (rc=$AI_RC pass=$AI_PASS fail=$AI_FAIL)"
  FAIL=$((FAIL + 1))
elif [[ "$AI_FAIL" -eq 0 && "$AI_PASS" -gt 0 ]]; then
  echo "PASS ai-audit suite ($AI_PASS checks)"
  PASS=$((PASS + 1))
else
  echo "FAIL ai-audit suite (pass=$AI_PASS fail=$AI_FAIL)"
  FAIL=$((FAIL + 1))
fi

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
