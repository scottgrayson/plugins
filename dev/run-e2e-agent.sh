#!/usr/bin/env bash
# Deterministic agent-mode E2E for CI (examples/preview).
# Covers: detach+curl, agent ignores tiny totalCap, busy-port fail-fast, stop cleanup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$ROOT/examples/preview"
export PATH="$ROOT/plugins/preview/bin:$PATH"
export PREVIEW_STATE_DIR="${PREVIEW_STATE_DIR:-$EXAMPLE/.preview/run-e2e-ci}"

PASS=0
FAIL=0

pass() { echo "PASS $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL $1"; echo "  $2"; FAIL=$((FAIL + 1)); }

cleanup_preview() {
  preview stop >/dev/null 2>&1 || true
  if [[ -f "$PREVIEW_STATE_DIR/app_pid" ]]; then
    kill "$(cat "$PREVIEW_STATE_DIR/app_pid")" 2>/dev/null || true
  fi
  if [[ -f "$PREVIEW_STATE_DIR/proxy_pid" ]]; then
    kill "$(cat "$PREVIEW_STATE_DIR/proxy_pid")" 2>/dev/null || true
  fi
  rm -rf "$PREVIEW_STATE_DIR"
}

port_in_use() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.3)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
sys.exit(0)
PY
}

pids_gone() {
  local pid
  for pid in "$@"; do
    [[ -z "${pid:-}" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
  done
  return 0
}

cd "$EXAMPLE"
CONFIG_PORT="$(python3 -c 'import json; print(json.load(open(".preview/config.json"))["port"])')"
cleanup_preview
trap 'cleanup_preview' EXIT

echo "== (a) up --agent --detach + curl 200 =="
unset PREVIEW_AGENT_MODE PREVIEW_TOTAL_CAP_SECONDS PREVIEW_IDLE_SECONDS PREVIEW_PORT || true
mkdir -p "$PREVIEW_STATE_DIR"
set +e
DETACH_OUT="$(preview up --agent --detach 2>/tmp/preview-e2e-a.err)"
DETACH_RC=$?
set -e
URL="$(printf '%s\n' "$DETACH_OUT" | tail -n1 | tr -d '\r')"
if [[ "$DETACH_RC" -eq 0 && "$URL" == http://127.0.0.1:* ]]; then
  pass "detach exits 0 with localhost URL ($URL)"
else
  fail "detach URL/exit" "rc=$DETACH_RC out=[$DETACH_OUT] err=$(cat /tmp/preview-e2e-a.err)"
fi
CODE="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 5 "$URL" || echo fail)"
if [[ "$CODE" == "200" ]]; then
  pass "curl READY URL -> 200"
else
  fail "curl READY URL" "code=$CODE url=$URL"
fi
APP_A="$(cat "$PREVIEW_STATE_DIR/app_pid" 2>/dev/null || true)"
PROXY_A="$(cat "$PREVIEW_STATE_DIR/proxy_pid" 2>/dev/null || true)"
preview stop >/dev/null
sleep 0.5
if pids_gone "$APP_A" "$PROXY_A" && ! port_in_use "$CONFIG_PORT"; then
  pass "stop after detach: no leftover pids, port $CONFIG_PORT free"
else
  fail "stop cleanup after detach" "app=$APP_A proxy=$PROXY_A"
fi
rm -rf "$PREVIEW_STATE_DIR"

echo "== (b) agent ignores PREVIEW_TOTAL_CAP_SECONDS=2 =="
mkdir -p "$PREVIEW_STATE_DIR"
PREVIEW_TOTAL_CAP_SECONDS=2 PREVIEW_IDLE_SECONDS=1 \
  preview up --agent >/tmp/preview-e2e-b.out 2>/tmp/preview-e2e-b.err &
UP_PID=$!
for _ in $(seq 1 40); do
  if grep -q 'READY' /tmp/preview-e2e-b.err 2>/dev/null; then
    break
  fi
  if ! kill -0 "$UP_PID" 2>/dev/null; then
    break
  fi
  sleep 0.25
done
# Watchdog logs after READY; wait briefly so the line is flushed to the err file.
for _ in $(seq 1 40); do
  if grep -q 'watchdog idle=0s total_cap=0s agent=1' /tmp/preview-e2e-b.err 2>/dev/null; then
    break
  fi
  if ! kill -0 "$UP_PID" 2>/dev/null; then
    break
  fi
  sleep 0.25
done
if grep -q 'watchdog idle=0s total_cap=0s agent=1' /tmp/preview-e2e-b.err; then
  pass "agent watchdog idle=0 total_cap=0"
else
  fail "agent watchdog zeros" "$(grep watchdog /tmp/preview-e2e-b.err || cat /tmp/preview-e2e-b.err)"
fi
APP_B="$(cat "$PREVIEW_STATE_DIR/app_pid" 2>/dev/null || true)"
sleep 5
if kill -0 "$UP_PID" 2>/dev/null && [[ -n "$APP_B" ]] && kill -0 "$APP_B" 2>/dev/null; then
  pass "agent still alive ~5s past tiny cap"
else
  fail "agent died on tiny cap" "up_alive=$(kill -0 "$UP_PID" 2>/dev/null && echo yes || echo no) reason=$(cat "$PREVIEW_STATE_DIR/reason" 2>/dev/null || echo none)"
fi
preview stop >/dev/null 2>&1 || true
wait "$UP_PID" 2>/dev/null || true
sleep 0.3
rm -rf "$PREVIEW_STATE_DIR"

echo "== (c) busy configured port fails naming the port =="
python3 - "$CONFIG_PORT" <<'PY' &
import socket, sys, time
port = int(sys.argv[1])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(1)
time.sleep(60)
PY
OCCUPY_PID=$!
sleep 0.3
mkdir -p "$PREVIEW_STATE_DIR"
set +e
BUSY_OUT="$(preview up --agent --detach 2>&1)"
BUSY_RC=$?
set -e
kill "$OCCUPY_PID" 2>/dev/null || true
wait "$OCCUPY_PID" 2>/dev/null || true
sleep 0.2
if [[ "$BUSY_RC" -ne 0 && "$BUSY_OUT" == *"port $CONFIG_PORT is already in use"* ]]; then
  pass "busy port fails naming port $CONFIG_PORT"
else
  fail "busy port" "rc=$BUSY_RC out=[$BUSY_OUT]"
fi
rm -rf "$PREVIEW_STATE_DIR"

echo "== (d) leftover check =="
LEFTOVER="$(ps -eo pid=,args= | grep -E '[s]erve\.py|[p]roxy\.py' | grep -F "$ROOT" || true)"
if [[ -z "$LEFTOVER" ]] && ! port_in_use "$CONFIG_PORT"; then
  pass "no leftover serve/proxy; port $CONFIG_PORT free"
else
  fail "leftovers" "procs=[$LEFTOVER] port_in_use=$(port_in_use "$CONFIG_PORT" && echo yes || echo no)"
fi

echo
echo "e2e passed=$PASS failed=$FAIL"
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
