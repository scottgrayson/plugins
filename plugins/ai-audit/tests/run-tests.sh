#!/usr/bin/env bash
# Fixture-based tests for ai-audit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PLUGIN="$ROOT/plugins/ai-audit"
BIN="$PLUGIN/bin/ai-audit"
FIX="$PLUGIN/fixtures"
PASS=0
FAIL=0

chmod +x "$BIN"
export PYTHONPATH="${PLUGIN}/lib${PYTHONPATH:+:$PYTHONPATH}"

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

assert_ge() {
  local name="$1" got="$2" want="$3"
  if [[ "$got" -ge "$want" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name (got $got < want $want)"
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
    echo "  hay: ${hay:0:500}"
    FAIL=$((FAIL + 1))
  fi
}

assert_not_contains() {
  local name="$1" hay="$2" needle="$3"
  if [[ "$hay" != *"$needle"* ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name (unexpected '$needle')"
    FAIL=$((FAIL + 1))
  fi
}

py_get() {
  # Usage: py_get <json-file> <python-expr-using-data>
  local file="$1"
  local expr="$2"
  python3 - "$file" "$expr" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(eval(sys.argv[2], {"data": data}))
PY
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== ai-audit detect =="
det="$("$BIN" detect --root "$FIX/claude" --json-only)"
assert_contains "detect claude host" "$det" '"host": "claude"'

det_c="$("$BIN" detect --host cursor --root "$FIX/cursor" --json-only)"
assert_contains "detect cursor host" "$det_c" '"host": "cursor"'

det_g="$("$BIN" detect --host grok --root "$FIX/grok" --json-only)"
assert_contains "detect grok host" "$det_g" '"host": "grok"'

echo "== context: claude =="
"$BIN" context --host claude --root "$FIX/claude" --json-only >"$TMP/claude-context.json"
assert_ge "claude inventory size" "$(py_get "$TMP/claude-context.json" 'len(data["inventory"])')" 8
assert_ge "claude always count" "$(py_get "$TMP/claude-context.json" 'data["counts"]["always"]')" 3
# always-style rule should be always
assert_eq "claude always rule classified" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("always-style.md"))')" \
  "always"
assert_eq "claude paths rule conditional" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("frontend.md"))')" \
  "conditional"
# ranking: big always rule or long skill description near top
assert_ge "claude wins >= 3" "$(py_get "$TMP/claude-context.json" 'len(data["wins"])')" 3
assert_ge "claude top saving > 0" "$(py_get "$TMP/claude-context.json" 'data["wins"][0]["saving_est_tokens"]')" 1
assert_eq "claude wins sorted" \
  "$(py_get "$TMP/claude-context.json" 'all(data["wins"][i]["saving_est_tokens"] >= data["wins"][i+1]["saving_est_tokens"] for i in range(len(data["wins"])-1))')" \
  "True"
# secrets not leaked
ctx_raw="$(cat "$TMP/claude-context.json")"
assert_not_contains "claude no API_KEY leak" "$ctx_raw" "SHOULD_NOT_APPEAR"

echo "== context: cursor =="
"$BIN" context --host cursor --root "$FIX/cursor" --json-only >"$TMP/cursor-context.json"
assert_eq "cursor alwaysApply rule" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("always.mdc"))')" \
  "always"
assert_eq "cursor globs conditional" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("globbed.mdc"))')" \
  "conditional"
assert_ge "cursor duplicate skill win present" \
  "$(py_get "$TMP/cursor-context.json" 'sum(1 for w in data["wins"] if "Deduplicate skill" in w["title"] and "shared-skill" in w["title"])')" \
  1
assert_ge "cursor wins >= 3" "$(py_get "$TMP/cursor-context.json" 'len(data["wins"])')" 3
assert_not_contains "cursor no DB password leak" "$(cat "$TMP/cursor-context.json")" "SHOULD_NOT_APPEAR"

echo "== context: grok =="
"$BIN" context --host grok --root "$FIX/grok" --json-only >"$TMP/grok-context.json"
assert_ge "grok inventory" "$(py_get "$TMP/grok-context.json" 'len(data["inventory"])')" 5
assert_eq "grok active bot always" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="bot_description" and i["meta"].get("agent_id")=="agent-active")')" \
  "always"
assert_eq "grok quiet bot on_demand" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="bot_description" and i["meta"].get("agent_id")=="agent-quiet")')" \
  "on_demand"
assert_ge "grok orphan memory win" \
  "$(py_get "$TMP/grok-context.json" 'sum(1 for w in data["wins"] if "orphan" in w["title"].lower())')" \
  1

echo "== token: cursor CSV =="
"$BIN" token --host cursor --root "$FIX/cursor" --csv "$FIX/cursor/usage.csv" --no-admin-api --json-only >"$TMP/cursor-token.json"
assert_eq "csv event count" "$(py_get "$TMP/cursor-token.json" 'data["data"]["event_count"]')" "5"
assert_eq "csv top model gpt-5" \
  "$(py_get "$TMP/cursor-token.json" 'max(data["data"]["by_model"].items(), key=lambda kv: kv[1]["total_tokens"])[0]')" \
  "gpt-5"
assert_eq "csv gpt-5 total tokens" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_model"]["gpt-5"]["total_tokens"])')" \
  "39400"
assert_ge "cursor token wins" "$(py_get "$TMP/cursor-token.json" 'len(data["wins"])')" 1
assert_eq "cursor not estimate" "$(py_get "$TMP/cursor-token.json" 'data["estimate"]')" "False"

echo "== token: claude JSONL =="
"$BIN" token --host claude --root "$FIX/claude" --json-only >"$TMP/claude-token.json"
assert_ge "claude sessions" "$(py_get "$TMP/claude-token.json" 'data["data"]["sessions_with_usage"]')" 1
assert_contains "claude has opus or sonnet" "$(py_get "$TMP/claude-token.json" '"," .join(data["data"]["by_model"].keys())')" "claude-"
assert_ge "claude opus tokens high" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_model"].get("claude-opus-4",{}).get("total_tokens",0))')" \
  6000
assert_ge "claude tool Bash counted" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_tool"].get("Bash",{}).get("count",0))')" \
  1

echo "== token: grok estimate =="
"$BIN" token --host grok --root "$FIX/grok" --json-only >"$TMP/grok-token.json"
assert_eq "grok estimate flag" "$(py_get "$TMP/grok-token.json" 'data["estimate"]')" "True"
assert_ge "grok active bot messages" \
  "$(py_get "$TMP/grok-token.json" 'int(data["data"]["by_bot"]["agent-active"]["message_count"])')" \
  40
assert_contains "grok routine win" "$(py_get "$TMP/grok-token.json" 'data["wins"][0]["title"] + " " + "".join(w["title"] for w in data["wins"])')" "routine"

echo "== facts: grok =="
"$BIN" facts --host grok --root "$FIX/grok" --stale-days 30 --json-only >"$TMP/grok-facts.json"
assert_ge "grok facts count" "$(py_get "$TMP/grok-facts.json" 'data["counts"]["facts"]')" 5
assert_ge "grok stale flags" "$(py_get "$TMP/grok-facts.json" 'data["counts"]["stale"]')" 1
assert_ge "grok duplicate flags" "$(py_get "$TMP/grok-facts.json" 'data["counts"]["duplicate"]')" 1
assert_ge "grok orphan flags" "$(py_get "$TMP/grok-facts.json" 'data["counts"]["orphan"]')" 1
# ranking order by saving
assert_eq "grok facts wins sorted" \
  "$(py_get "$TMP/grok-facts.json" 'all(data["wins"][i]["saving_est_tokens"] >= data["wins"][i+1]["saving_est_tokens"] for i in range(len(data["wins"])-1))')" \
  "True"

echo "== facts: claude =="
"$BIN" facts --host claude --root "$FIX/claude" --stale-days 30 --json-only >"$TMP/claude-facts.json"
assert_ge "claude stale topic" "$(py_get "$TMP/claude-facts.json" 'data["counts"]["stale"]')" 1
assert_ge "claude facts scanned" "$(py_get "$TMP/claude-facts.json" 'data["counts"]["facts"]')" 3

echo "== facts: cursor memories export =="
"$BIN" facts --host cursor --root "$FIX/cursor" --memories "$FIX/cursor/memories.mdc" --stale-days 30 --json-only >"$TMP/cursor-facts.json"
assert_ge "cursor facts from export" "$(py_get "$TMP/cursor-facts.json" 'data["counts"]["facts"]')" 3
assert_ge "cursor time-bound stale" "$(py_get "$TMP/cursor-facts.json" 'data["counts"]["stale"]')" 1
assert_contains "cursor notes server-side" "$(py_get "$TMP/cursor-facts.json" '" ".join(data["notes"])')" "server-side"

echo "== CSV unit parse =="
python3 - <<PY
import sys
sys.path.insert(0, "$PLUGIN/lib")
from ai_audit.token_audit import parse_cursor_usage_csv
from pathlib import Path
d = parse_cursor_usage_csv(Path("$FIX/cursor/usage.csv"))
assert d["event_count"] == 5, d
assert "gpt-5" in d["by_model"]
assert int(d["by_model"]["gpt-5"]["total_tokens"]) == 39400
assert int(d["by_model"]["claude-sonnet"]["total_tokens"]) == 7850
print("csv parse ok")
PY
assert_eq "csv parse helper" "ok" "ok"

echo "== markdown reports emit =="
md="$("$BIN" context --host claude --root "$FIX/claude" --md-only)"
assert_contains "md has Context audit" "$md" "# Context audit"
assert_contains "md has Biggest wins" "$md" "Biggest wins"

echo
echo "ai-audit passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
