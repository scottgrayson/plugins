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

mkdir -p "$TMP/app/.preview/steps"
cat >"$TMP/app/.preview.json" <<'JSON'
{
  "type": "static",
  "port": 4321,
  "idleSeconds": 30,
  "defaultSteps": ["seed"],
  "ignorePaths": ["/poll"],
  "root": "."
}
JSON
printf '#!/usr/bin/env bash\necho seed\n' >"$TMP/app/.preview/steps/seed.sh"
printf '#!/usr/bin/env bash\necho migrate\n' >"$TMP/app/.preview/steps/migrate.sh"
chmod +x "$TMP/app/.preview/steps/"*.sh

export PREVIEW_STATE_DIR="$TMP/state-config"
mkdir -p "$PREVIEW_STATE_DIR"
pushd "$TMP/app" >/dev/null
preview_load_config "$TMP/app/.preview.json"
assert_eq "config port" "$PREVIEW_PORT" "4321"
assert_eq "config idle" "$PREVIEW_IDLE_SECONDS" "30"
assert_eq "config type" "$PREVIEW_TYPE" "static"
assert_contains "config ignorePaths" "$PREVIEW_IGNORE_PATHS" "/poll"
assert_contains "config defaultSteps" "$PREVIEW_DEFAULT_STEPS" "seed"
popd >/dev/null

export PREVIEW_STATE_DIR="$TMP/state-defaults"
mkdir -p "$PREVIEW_STATE_DIR"
pushd "$TMP" >/dev/null
preview_load_config "$TMP/missing.json"
assert_eq "default idle" "$PREVIEW_IDLE_SECONDS" "900"
assert_eq "default cap" "$PREVIEW_TOTAL_CAP_SECONDS" "7200"
assert_eq "default type" "$PREVIEW_TYPE" "static"
popd >/dev/null

export PREVIEW_STATE_DIR="$TMP/state-steps"
mkdir -p "$PREVIEW_STATE_DIR"
pushd "$TMP/app" >/dev/null
preview_load_config "$TMP/app/.preview.json"
got="$(preview_resolve_steps "" "$TMP/app/.preview/steps" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
assert_eq "default steps when no labels" "$got" "seed"
got="$(preview_resolve_steps "preview:migrate,other" "$TMP/app/.preview/steps" | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
assert_eq "label selects migrate" "$got" "migrate"
assert_fail_contains "unknown step lists valid" "unknown step 'nope'" \
  bash -c "source '$LIB/config.sh'; export PREVIEW_STATE_DIR='$PREVIEW_STATE_DIR'; preview_load_config '$TMP/app/.preview.json'; preview_resolve_steps 'preview:nope' '$TMP/app/.preview/steps'"
out="$(bash -c "source '$LIB/config.sh'; export PREVIEW_STATE_DIR='$PREVIEW_STATE_DIR'; preview_load_config '$TMP/app/.preview.json'; preview_resolve_steps 'preview:nope' '$TMP/app/.preview/steps'" 2>&1 || true)"
assert_contains "unknown step mentions migrate" "$out" "migrate"
popd >/dev/null

python3 - "$PLUGIN/lib/proxy.py" <<'PY'
import importlib.util
import sys
import time

spec = importlib.util.spec_from_file_location("preview_proxy", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
Tracker = mod.CountTracker

t = Tracker(["/poll", "/healthz*"])
assert t.should_count("GET", "/poll") == (False, "ignorePaths")
assert t.should_count("GET", "/healthz/ready")[0] is False
assert t.should_count("GET", "/app.js") == (False, "static")
assert t.should_count("GET", "/ping") == (True, "user")
assert t.should_count("POST", "/form") == (True, "user")

poller = Tracker([])
path = "/livewire"
base = time.time()
with poller.lock:
    poller.history[path].clear()
    for j in range(3):
        poller.history[path].append(base + j * 29.0)
assert poller.looks_like_poll(path, base + 3 * 29.0) is True
assert poller.should_count("GET", path) == (False, "poll")

# Steady /ping is intentional keep-alive in the example; do not auto-ignore it.
pinger = Tracker([])
for j in range(6):
    assert pinger.should_count("GET", "/ping") == (True, "user")

human = Tracker([])
with human.lock:
    human.history["/dash"].extend([base, base + 5, base + 40])
assert human.looks_like_poll("/dash", base + 100) is False
assert human.should_count("GET", "/dash") == (True, "user")
print("proxy helpers ok")
PY
assert_eq "proxy ignore + polling helpers" "ok" "ok"

body=$'- [ ] Check to generate preview\n\n## Summary\nHi'
assert_eq "should start on tick" "$(preview_pr_should_start '- [x] Check to generate preview' && echo yes || echo no)" "yes"
assert_eq "should not start on idle" "$(preview_pr_should_start '- [ ] Check to generate preview' && echo yes || echo no)" "no"
assert_eq "should not start on url" "$(preview_pr_should_start '- [x] https://x.trycloudflare.com' && echo yes || echo no)" "no"
new="$(preview_pr_set_url "$body" 'https://x.trycloudflare.com')"
assert_eq "set url line1" "$(preview_pr_line1 "$new")" "- [x] https://x.trycloudflare.com"
assert_contains "preserves rest" "$new" "## Summary"
reset="$(preview_pr_reset_idle "$new")"
assert_eq "reset idle" "$(preview_pr_line1 "$reset")" "- [ ] Check to generate preview"
ensured="$(preview_pr_ensure_idle_checkbox 'hello')"
assert_eq "ensure inserts" "$(preview_pr_line1 "$ensured")" "- [ ] Check to generate preview"

echo
echo "passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
