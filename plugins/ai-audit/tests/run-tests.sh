#!/usr/bin/env bash
# Fixture-based tests for ai-audit (hermetic via --root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PLUGIN="$ROOT/plugins/ai-audit"
BIN="$PLUGIN/bin/ai-audit"
FIX="$PLUGIN/fixtures"
PASS=0
FAIL=0

export PYTHONPATH="${PLUGIN}/lib${PYTHONPATH:+:$PYTHONPATH}"
# Ensure real box paths cannot leak into --root detection during tests.
unset AI_AUDIT_SAND_DATA GROK_BOT_DATA CURSOR_ADMIN_API_KEY CURSOR_API_KEY || true

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
    echo "  hay: ${hay:0:600}"
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

echo "== 1. detect --root isolation (hermetic) =="
# Even if a fake /home/box path exists in env imagination, --root must win.
"$BIN" detect --root "$FIX/claude" --json-only >"$TMP/detect-claude.json"
assert_eq "detect claude host under --root" "$(py_get "$TMP/detect-claude.json" 'data["host"]')" "claude"
assert_eq "detect isolated flag" "$(py_get "$TMP/detect-claude.json" 'data["isolated"]')" "True"
assert_not_contains "detect no box sand-data" "$(cat "$TMP/detect-claude.json")" "/home/box"

# Simulate hostile env pointing at grok while auditing claude fixture
AI_AUDIT_SAND_DATA="$FIX/grok/sand-data" "$BIN" detect --root "$FIX/claude" --json-only >"$TMP/detect-claude2.json"
assert_eq "detect ignores env sand when --root" "$(py_get "$TMP/detect-claude2.json" 'data["host"]')" "claude"
assert_eq "detect sand_data none under claude --root" "$(py_get "$TMP/detect-claude2.json" 'data.get("sand_data")')" "None"

"$BIN" detect --root "$FIX/grok" --json-only >"$TMP/detect-grok.json"
assert_eq "detect grok host under --root" "$(py_get "$TMP/detect-grok.json" 'data["host"]')" "grok"

echo "== 2. Cursor CSV real dashboard headers =="
"$BIN" token --host cursor --root "$FIX/cursor" --csv "$FIX/cursor/usage.csv" --json-only >"$TMP/cursor-token.json"
assert_eq "csv event count" "$(py_get "$TMP/cursor-token.json" 'data["data"]["event_count"]')" "7"
assert_eq "csv top model gpt-5" \
  "$(py_get "$TMP/cursor-token.json" 'max(data["data"]["by_model"].items(), key=lambda kv: kv[1]["total_tokens"])[0]')" \
  "gpt-5"
assert_ge "csv by_kind has cloud_agent" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_kind"].get("cloud_agent",{}).get("count",0))')" 3
assert_ge "csv by_kind has automation" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_kind"].get("automation",{}).get("count",0))')" 2
assert_ge "csv by_kind has local" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_kind"].get("local",{}).get("count",0))')" 2
assert_ge "csv by_max_mode on" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_max_mode"].get("on",{}).get("count",0))')" 1
# Cloud rows should outrank local in kind totals
assert_eq "cloud tokens > local" \
  "$(py_get "$TMP/cursor-token.json" 'data["data"]["by_kind"]["cloud_agent"]["total_tokens"] > data["data"]["by_kind"]["local"]["total_tokens"]')" \
  "True"

python3 - <<PY
import sys
sys.path.insert(0, "$PLUGIN/lib")
from ai_audit.token_audit import parse_cursor_usage_csv
from pathlib import Path
d = parse_cursor_usage_csv(Path("$FIX/cursor/usage.csv"))
assert d["event_count"] == 7, d
assert "cloud_agent" in d["by_kind"]
assert d["by_kind"]["cloud_agent"]["count"] == 3
# Input (w/ Cache Write) + (w/o) parsed
ev0 = d["events"][0]
assert ev0["input_with_cache_write"] == 500, ev0
assert ev0["input_without_cache_write"] == 9500, ev0
assert ev0["kind"] == "cloud_agent"
print("csv dashboard headers ok")
PY
assert_eq "csv dashboard header parse" "ok" "ok"

echo "== 3. Claude JSONL dedupe by message.id/requestId =="
"$BIN" token --host claude --root "$FIX/claude" --json-only >"$TMP/claude-token.json"
# Without dedupe: 1000+1000+2500+5000+5000+6200 = 20700 input alone; with dedupe: 1000+2500+5000+6200 = 14700 input
assert_eq "claude deduped usage rows" "$(py_get "$TMP/claude-token.json" 'data["data"]["usage_rows_deduped"]')" "4"
sonnet_in="$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_model"]["claude-sonnet-4"]["input"])')"
assert_eq "claude sonnet input deduped" "$sonnet_in" "3500"
opus_in="$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_model"]["claude-opus-4"]["input"])')"
assert_eq "claude opus input deduped" "$opus_in" "11200"
# Total tokens should not double-count splits
# Deduped: 1150+2780+6200+6300 = 16430 (splits of msg-1/msg-3 not double-counted)
total="$(py_get "$TMP/claude-token.json" 'sum(int(v["total_tokens"]) for v in data["data"]["by_model"].values())')"
assert_eq "claude total tokens deduped" "$total" "16430"

echo "== 4. Tool cost on tool_result request (input growth) =="
# Read: prev input 1000 → next 2500 growth 1500; Bash: 5000 → 6200 growth 1200
assert_eq "Read attributed growth" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_tool"].get("Read",{}).get("input_growth",0))')" \
  "1500"
assert_eq "Bash attributed growth" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_tool"].get("Bash",{}).get("input_growth",0))')" \
  "1200"

echo "== 5. Grok activeAgentId =="
"$BIN" context --host grok --root "$FIX/grok" --json-only >"$TMP/grok-context.json"
assert_eq "grok active bot always via activeAgentId" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="bot_description" and i["meta"].get("agent_id")=="agent-active")')" \
  "always"
assert_eq "grok quiet bot on_demand" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="bot_description" and i["meta"].get("agent_id")=="agent-quiet")')" \
  "on_demand"

echo "== 6. Grok skill cache dedupe =="
# deploy+short-one are in workflows; cache-only-skill should be the only managed cache entry counted
assert_eq "managed cache only novel skills" \
  "$(py_get "$TMP/grok-context.json" 'next((i["meta"].get("skill_count") for i in data["inventory"] if i["kind"]=="managed_skill_cache"), 0)')" \
  "1"

echo "== 7. disable-model-invocation =="
"$BIN" context --host claude --root "$FIX/claude" --json-only >"$TMP/claude-context.json"
assert_eq "claude manual skill desc on_demand" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="manual-only" and i["kind"]=="skill_description")')" \
  "on_demand"
"$BIN" context --host cursor --root "$FIX/cursor" --json-only >"$TMP/cursor-context.json"
assert_eq "cursor slash-only desc on_demand" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="slash-only" and i["kind"]=="skill_description")')" \
  "on_demand"
assert_eq "grok manual-workflow is skill_path always" \
  "$(py_get "$TMP/grok-context.json" 'next(i["kind"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="manual-workflow" and i["load"]=="always")')" \
  "skill_path"

echo "== 8. skill extra files (scripts/assets) =="
assert_ge "claude big-skill has skill_asset" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if i["kind"]=="skill_asset" and (i.get("meta") or {}).get("skill")=="big-skill")')" \
  1
assert_eq "skill_asset on_demand" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="skill_asset")')" \
  "on_demand"

echo "== 9. imports / nested / cli-config / MCP schemas =="
assert_ge "claude import shared-conventions always" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if i["path"].endswith("shared-conventions.md") and i["load"]=="always")')" \
  1
assert_ge "claude nested widget CLAUDE.md" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if "packages/widget/CLAUDE.md" in i["path"])')" \
  1
assert_ge "claude nested package skill" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="widget-skill")')" \
  1
assert_eq "cursor cli-config not always context" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="cli_config")')" \
  "unknown"
assert_eq "cursor cli-config bytes 0" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["bytes"] for i in data["inventory"] if i["kind"]=="cli_config")')" \
  "0"
assert_eq "claude MCP sized by schemas" \
  "$(py_get "$TMP/claude-context.json" 'next((i.get("meta") or {}).get("sized_by") for i in data["inventory"] if i["kind"]=="mcp_servers")')" \
  "schemas"

echo "== 10. orphan memory not always-loaded / not token win =="
assert_eq "orphan load unknown" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="memory" and i["meta"].get("orphan"))')" \
  "unknown"
assert_eq "active memory always" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="memory" and i["meta"].get("agent_id")=="agent-active")')" \
  "always"
assert_eq "orphan win is ask not savings" \
  "$(py_get "$TMP/grok-context.json" 'next((w.get("ask") or w["saving_est_tokens"]==0) for w in data["wins"] if "orphan" in w["title"].lower())')" \
  "True"

echo "== 11. conflict word-boundary / negation =="
"$BIN" facts --host claude --root "$FIX/claude" --stale-days 30 --json-only >"$TMP/claude-facts.json"
# pnpm vs npm should conflict; must not vs must not should not
assert_ge "pnpm/npm conflict flagged" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for i in data["issues"] if i["kind"]=="conflict" and ("pnpm" in i["detail"].lower() or "npm" in i["detail"].lower()))')" \
  1
assert_eq "must-not pair not false conflict" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for i in data["issues"] if i["kind"]=="conflict" and "must not push" in i["detail"].lower() and i["detail"].lower().count("must not push")>=2 and "pnpm" not in i["detail"].lower() and "npm" not in i["detail"].lower())')" \
  "0"

python3 - <<'PY'
import sys
sys.path.insert(0, "/workspace/plugins/ai-audit/lib")
from ai_audit.facts_audit import _polarity_conflict
assert _polarity_conflict("Use pnpm for package installs", "Use npm for package installs")
assert _polarity_conflict("Always enable telemetry", "Never enable telemetry")
assert not _polarity_conflict("Must not push directly to main", "Must not push directly to main")
assert not _polarity_conflict("Must not push to main", "You must not push to main")
print("polarity ok")
PY
assert_eq "polarity helper" "ok" "ok"

echo "== 12. MEMORY.md not double-counted; CLAUDE.local + rules =="
# Topic files exist → MEMORY.md bullets excluded
assert_eq "no MEMORY.md index facts when topics exist" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if f["source"]=="MEMORY.md")')" \
  "0"
assert_ge "CLAUDE.local.md facts present" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if "CLAUDE.local.md" in f["path"])')" \
  1
assert_ge "rules facts present" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if "rules" in f["path"])')" \
  1

echo "== 13. no invented savings / routine ask =="
"$BIN" token --host grok --root "$FIX/grok" --json-only >"$TMP/grok-token.json"
assert_eq "grok routine ask without schedules" \
  "$(py_get "$TMP/grok-token.json" 'any(w.get("ask") and "routine" in w["title"].lower() for w in data["wins"])')" \
  "True"
assert_eq "ask win has zero saving" \
  "$(py_get "$TMP/grok-token.json" 'next(w["saving_est_tokens"] for w in data["wins"] if w.get("ask"))')" \
  "0"
# With routines + usage total → measured win
cat >"$TMP/routines.json" <<'EOF'
[{"name":"hourly-triage","cron":"0 * * * *"},{"name":"morning","cron":"0 9 * * *"}]
EOF
"$BIN" token --host grok --root "$FIX/grok" --routines-json "$TMP/routines.json" --usage-total 80 --usage-period "week" --json-only >"$TMP/grok-token2.json"
assert_ge "routine measured win with data" \
  "$(py_get "$TMP/grok-token2.json" 'sum(1 for w in data["wins"] if "Routine schedule" in w["title"] and w["saving_est_tokens"]>0)')" \
  1

echo "== 14. token notes rendered; usage/routines CLI =="
md="$("$BIN" token --host grok --root "$FIX/grok" --usage-total 42 --usage-period "week" --md-only)"
assert_contains "md renders data notes" "$md" "ESTIMATE"
assert_contains "md shows usage total in summary or notes" "$md" "42"

echo "== 15. Admin API opt-in only =="
# Default must not call admin even if key set
CURSOR_ADMIN_API_KEY="fake-key-should-not-be-used" \
  "$BIN" token --host cursor --root "$FIX/cursor" --csv "$FIX/cursor/usage.csv" --json-only >"$TMP/no-admin.json"
assert_contains "admin not used by default" "$(py_get "$TMP/no-admin.json" 'data["notes"]')" "Admin API not used"
assert_eq "admin flag false" "$(py_get "$TMP/no-admin.json" 'data["data"]["admin"]')" "False"

python3 - <<'PY'
import sys
sys.path.insert(0, "/workspace/plugins/ai-audit/lib")
from ai_audit.token_audit import fetch_cursor_admin_usage
import ai_audit.token_audit as ta
# Ensure Basic auth header is prepared (don't actually hit network with fake key —
# call with no key returns None)
assert fetch_cursor_admin_usage(api_key=None) is None
# Inspect source for Basic auth
src = open("/workspace/plugins/ai-audit/lib/ai_audit/token_audit.py").read()
assert "Basic" in src and "CURSOR_ADMIN_API_KEY" in src
assert "pageSize" in src
print("admin opt-in source ok")
PY
assert_eq "admin opt-in source" "ok" "ok"

echo "== 16. minors: sqlite immutable, cache min sample, fact quote =="
# Cache-hit win requires CACHE_HIT_MIN_EVENTS; fixture has few events per model —
# gpt-5 has 4 cloud+ide events with max mode — may or may not meet 5. Ensure single-event doesn't invent.
python3 - <<'PY'
import sys
sys.path.insert(0, "/workspace/plugins/ai-audit/lib")
from ai_audit.token_audit import rank_token_wins, CACHE_HIT_MIN_EVENTS
data = {
  "by_model": {
    "solo": {"input": 5000, "cache_read": 10, "output": 1, "total_tokens": 5011, "count": 1}
  },
  "by_kind": {},
  "by_tool": {},
  "by_bot": {},
  "routines": [],
}
wins = rank_token_wins("cursor", data)
assert not any("cache" in w.title.lower() for w in wins), wins
assert CACHE_HIT_MIN_EVENTS >= 5
print("cache min sample ok")
PY
assert_eq "cache min sample" "ok" "ok"

"$BIN" facts --host grok --root "$FIX/grok" --stale-days 30 --json-only >"$TMP/grok-facts.json"
assert_eq "fact text capped ~80" \
  "$(py_get "$TMP/grok-facts.json" 'max(len(f["text"]) for f in data["facts"]) <= 80')" \
  "True"
assert_not_contains "no real-looking personal emails in fixtures" \
  "$(rg -n '@gmail\.com|@company\.|ssn|password' "$FIX" || true)" "@"

echo "== baseline inventory / ranking still works =="
assert_ge "claude inventory" "$(py_get "$TMP/claude-context.json" 'len(data["inventory"])')" 8
assert_eq "claude always rule" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("always-style.md"))')" \
  "always"
assert_ge "cursor duplicate skill win" \
  "$(py_get "$TMP/cursor-context.json" 'sum(1 for w in data["wins"] if "Deduplicate skill" in w["title"])')" \
  1
assert_not_contains "no secret leak" "$(cat "$TMP/claude-context.json")" "SHOULD_NOT_APPEAR"

mdc="$("$BIN" context --host claude --root "$FIX/claude" --md-only)"
assert_contains "md Context audit" "$mdc" "# Context audit"

echo
echo "ai-audit passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
