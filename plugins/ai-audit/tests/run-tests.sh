#!/usr/bin/env bash
# Fixture-based tests for ai-audit (hermetic via --root).
# All paths are derived from this script's location — never hard-code /workspace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PLUGIN="$ROOT/plugins/ai-audit"
LIB="$PLUGIN/lib"
BIN="$PLUGIN/bin/ai-audit"
FIX="$PLUGIN/fixtures"
PASS=0
FAIL=0

export PYTHONPATH="${LIB}${PYTHONPATH:+:$PYTHONPATH}"
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

# Run an inline python check that must exit 0; count as one PASS/FAIL.
# Usage: py_check "name" <<'PY' ... PY
py_check() {
  local name="$1"
  local script out rc=0
  # Read heredoc before spawning a subshell (command substitution would otherwise
  # leave python3 without the script on some shells).
  script="$(cat)"
  out="$(python3 -c "$script" 2>&1)" || rc=$?
  if [[ $rc -eq 0 ]]; then
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

echo "== 1. detect --root isolation =="
"$BIN" detect --root "$FIX/claude" --json-only >"$TMP/detect-claude.json"
assert_eq "detect claude host under --root" "$(py_get "$TMP/detect-claude.json" 'data["host"]')" "claude"
assert_eq "detect isolated flag" "$(py_get "$TMP/detect-claude.json" 'data["isolated"]')" "True"
assert_not_contains "detect no box sand-data" "$(cat "$TMP/detect-claude.json")" "/home/box"
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
assert_ge "csv by_kind cloud_agent" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_kind"].get("cloud_agent",{}).get("count",0))')" 3
assert_ge "csv by_kind automation" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["by_kind"].get("automation",{}).get("count",0))')" 2
assert_eq "cloud tokens > local" \
  "$(py_get "$TMP/cursor-token.json" 'data["data"]["by_kind"]["cloud_agent"]["total_tokens"] > data["data"]["by_kind"]["local"]["total_tokens"]')" \
  "True"
# Savings are estimates, not raw totals
assert_eq "kind win saving is half of volume" \
  "$(py_get "$TMP/cursor-token.json" 'any(w["title"].startswith("Run cloud") and w["saving_est_tokens"] == int(data["data"]["by_kind"]["cloud_agent"]["total_tokens"])//2 for w in data["wins"])')" \
  "True"
assert_contains "kind win has method" \
  "$(py_get "$TMP/cursor-token.json" 'next(w.get("method","") for w in data["wins"] if "cloud" in w["title"].lower())')" \
  "50%"

py_check "csv dashboard header parse" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.token_audit import parse_cursor_usage_csv
from pathlib import Path
d = parse_cursor_usage_csv(Path("$FIX/cursor/usage.csv"))
assert d["event_count"] == 7, d
assert d["by_kind"]["cloud_agent"]["count"] == 3
ev0 = d["events"][0]
assert ev0["input_with_cache_write"] == 500, ev0
assert ev0["input_without_cache_write"] == 9500, ev0
assert ev0["kind"] == "cloud_agent"
PY

echo "== 3. Claude JSONL usage dedupe =="
"$BIN" token --host claude --root "$FIX/claude" --json-only >"$TMP/claude-token.json"
assert_eq "claude deduped usage rows" "$(py_get "$TMP/claude-token.json" 'data["data"]["usage_rows_deduped"]')" "4"
assert_eq "claude sonnet input deduped" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_model"]["claude-sonnet-4"]["input"])')" "3500"
assert_eq "claude opus input deduped" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_model"]["claude-opus-4"]["input"])')" "11200"

echo "== 4. tool_use collected from split lines + cache in growth =="
# Read on 2nd split line; growth = (2500+50+200) - (1000+0+100) = 1650
# Bash on 2nd split line; growth = (6200+0+100) - (5000+200+0) = 1100
assert_eq "Read growth with cache" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_tool"].get("Read",{}).get("input_growth",0))')" \
  "1650"
assert_eq "Bash growth with cache" \
  "$(py_get "$TMP/claude-token.json" 'int(data["data"]["by_tool"].get("Bash",{}).get("input_growth",0))')" \
  "1100"

echo "== 5. Grok activeAgentId =="
"$BIN" context --host grok --root "$FIX/grok" --json-only >"$TMP/grok-context.json"
assert_eq "grok active bot always" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="bot_description" and (i.get("meta") or {}).get("agent_id")=="agent-active")')" \
  "always"

echo "== 6. Grok skill cache dedupe =="
assert_eq "managed cache only novel skills" \
  "$(py_get "$TMP/grok-context.json" 'next(((i.get("meta") or {}).get("skill_count") for i in data["inventory"] if i["kind"]=="managed_skill_cache"), 0)')" \
  "1"

echo "== 7. disable-model-invocation =="
"$BIN" context --host claude --root "$FIX/claude" --json-only >"$TMP/claude-context.json"
assert_eq "claude manual skill on_demand" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="manual-only" and i["kind"]=="skill_description")')" \
  "on_demand"
"$BIN" context --host cursor --root "$FIX/cursor" --json-only >"$TMP/cursor-context.json"
assert_eq "cursor slash-only on_demand" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="slash-only" and i["kind"]=="skill_description")')" \
  "on_demand"

echo "== 8. skill assets =="
assert_ge "big-skill skill_asset" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if i["kind"]=="skill_asset" and (i.get("meta") or {}).get("skill")=="big-skill")')" \
  1

echo "== 9. imports / nested / cli-config / MCP =="
assert_ge "import always" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if i["path"].endswith("shared-conventions.md") and i["load"]=="always")')" 1
assert_eq "cli-config not context" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="cli_config")')" "unknown"
assert_eq "MCP sized by schemas" \
  "$(py_get "$TMP/claude-context.json" 'next((i.get("meta") or {}).get("sized_by") for i in data["inventory"] if i["kind"]=="mcp_servers")')" \
  "schemas"

echo "== 10. orphan not always-loaded / cleanup-only =="
assert_eq "orphan load unknown" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="memory" and (i.get("meta") or {}).get("orphan"))')" \
  "unknown"
assert_eq "orphan context win ask" \
  "$(py_get "$TMP/grok-context.json" 'next(bool(w.get("ask")) and w["saving_est_tokens"]==0 for w in data["wins"] if "orphan" in w["title"].lower())')" \
  "True"

echo "== 11. conflict word-boundary + non-bullet prose =="
"$BIN" facts --host claude --root "$FIX/claude" --stale-days 30 --json-only >"$TMP/claude-facts.json"
assert_ge "pnpm/npm conflict from prose" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for i in data["issues"] if i["kind"]=="conflict" and "pnpm" in i["detail"].lower() and "npm" in i["detail"].lower())')" \
  1
assert_ge "non-bullet fact from package-policy" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if "package-policy" in f["path"] and "pnpm" in f["text"].lower())')" \
  1
assert_eq "short prose line Use pnpm, not npm." \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if f["text"].strip()=="Use pnpm, not npm.")')" \
  "1"

py_check "polarity word-boundary" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.facts_audit import _polarity_conflict
assert _polarity_conflict("Use pnpm for package installs", "Use npm for package installs")
assert _polarity_conflict("Always enable telemetry", "Never enable telemetry")
assert not _polarity_conflict("Must not push directly to main", "Must not push directly to main")
assert not _polarity_conflict("Must not push to main", "You must not push to main")
PY

echo "== 12. MEMORY.md / CLAUDE.local / rules =="
assert_eq "no MEMORY.md index facts" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if f["source"]=="MEMORY.md")')" "0"
assert_ge "CLAUDE.local facts" \
  "$(py_get "$TMP/claude-facts.json" 'sum(1 for f in data["facts"] if "CLAUDE.local.md" in f["path"])')" 1

echo "== 13. no invented savings / routine ask =="
"$BIN" token --host grok --root "$FIX/grok" --json-only >"$TMP/grok-token.json"
assert_eq "routine ask without schedules" \
  "$(py_get "$TMP/grok-token.json" 'any(w.get("ask") and "routine" in w["title"].lower() for w in data["wins"])')" \
  "True"
cat >"$TMP/routines.json" <<'EOF'
[{"name":"hourly-triage","cron":"0 * * * *"},{"name":"morning","cron":"0 9 * * *"}]
EOF
"$BIN" token --host grok --root "$FIX/grok" --routines-json "$TMP/routines.json" --usage-total 80 --usage-period "week" --json-only >"$TMP/grok-token2.json"
assert_ge "routine measured win" \
  "$(py_get "$TMP/grok-token2.json" 'sum(1 for w in data["wins"] if "routine" in w["title"].lower() and w["saving_est_tokens"]>0)')" \
  1
# With usage 80, 50% estimate → 40
assert_eq "routine saving is half usage" \
  "$(py_get "$TMP/grok-token2.json" 'next(w["saving_est_tokens"] for w in data["wins"] if "routine" in w["title"].lower() and w["saving_est_tokens"]>0)')" \
  "40"

echo "== 14. token notes rendered =="
md="$("$BIN" token --host grok --root "$FIX/grok" --usage-total 42 --usage-period "week" --md-only)"
assert_contains "md renders notes" "$md" "ESTIMATE"
assert_contains "md shows usage total" "$md" "42"

echo "== 15. Admin API opt-in; no CURSOR_API_KEY fallback =="
CURSOR_ADMIN_API_KEY="fake-key" \
  "$BIN" token --host cursor --root "$FIX/cursor" --csv "$FIX/cursor/usage.csv" --json-only >"$TMP/no-admin.json"
assert_contains "admin not used by default" "$(py_get "$TMP/no-admin.json" 'str(data["notes"])')" "Admin API not used"
assert_eq "admin flag false" "$(py_get "$TMP/no-admin.json" 'data["data"]["admin"]')" "False"

py_check "admin key var only (no CURSOR_API_KEY)" <<PY
import sys, os
sys.path.insert(0, "$LIB")
from ai_audit.token_audit import fetch_cursor_admin_usage
assert fetch_cursor_admin_usage(api_key=None) is None
os.environ["CURSOR_API_KEY"] = "should-not-be-used"
os.environ.pop("CURSOR_ADMIN_API_KEY", None)
assert fetch_cursor_admin_usage(api_key=None) is None, "must not use CURSOR_API_KEY"
src = open("$LIB/ai_audit/token_audit.py", encoding="utf-8").read()
assert "CURSOR_ADMIN_API_KEY" in src
assert "Basic" in src and "pageSize" in src
# Ensure CURSOR_API_KEY is not used as fallback in fetch
import re
fn = src[src.index("def fetch_cursor_admin_usage"):src.index("def _usage_dedupe_key")]
assert "CURSOR_API_KEY" not in fn
PY

echo "== 16. cache min sample / fact quote / sqlite wal copy =="
py_check "cache min sample blocks single event" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.token_audit import rank_token_wins, CACHE_HIT_MIN_EVENTS
data = {
  "by_model": {"solo": {"input": 5000, "cache_read": 10, "output": 1, "total_tokens": 5011, "count": 1}},
  "by_kind": {}, "by_tool": {}, "by_bot": {}, "routines": [],
}
wins = rank_token_wins("cursor", data)
assert not any("cache" in w.title.lower() for w in wins), wins
assert CACHE_HIT_MIN_EVENTS >= 5
PY

"$BIN" facts --host grok --root "$FIX/grok" --stale-days 30 --json-only >"$TMP/grok-facts.json"
assert_eq "fact text capped ~80" \
  "$(py_get "$TMP/grok-facts.json" 'max(len(f["text"]) for f in data["facts"]) <= 80')" "True"
assert_eq "orphan facts win is cleanup-only" \
  "$(py_get "$TMP/grok-facts.json" 'next(w["saving_est_tokens"]==0 and w.get("ask") for w in data["wins"] if "orphan" in w["title"].lower())')" \
  "True"

py_check "sqlite opens copied db+wal+shm" <<PY
import sys
sys.path.insert(0, "$LIB")
src = open("$LIB/ai_audit/token_audit.py", encoding="utf-8").read()
assert "search-index.db-wal" in src or '"-wal"' in src
assert "mkdtemp" in src
assert "immutable=1" not in src.split("def collect_grok_tokens")[1].split("def rank_token_wins")[0] or True
# Prefer copy path: look for -wal copy in collect_grok_tokens
body = src.split("def collect_grok_tokens")[1].split("def rank_token_wins")[0]
assert "-wal" in body and "mkdtemp" in body
assert "immutable=1" not in body
PY

echo "== C. YAML folded description + list-form paths =="
py_check "folded description parses full text" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.common import parse_front_matter
from pathlib import Path
text = Path("$FIX/claude/home/.claude/skills/big-skill/SKILL.md").read_text()
meta, body = parse_front_matter(text)
desc = meta.get("description") or ""
assert ">-" not in desc, desc
assert "intentionally long skill description" in desc, desc
assert len(desc.encode()) > 100, len(desc)
PY

assert_eq "list-form paths rule is conditional" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("frontend.md"))')" \
  "conditional"
assert_contains "list-form paths noted" \
  "$(py_get "$TMP/claude-context.json" 'next(i["notes"] for i in data["inventory"] if i["path"].endswith("frontend.md"))')" \
  "paths="

# Folded desc should be counted as full size in inventory
assert_ge "folded skill desc bytes" \
  "$(py_get "$TMP/claude-context.json" 'next(i["bytes"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="big-skill" and i["kind"]=="skill_description")')" \
  100

echo "== E. Grok cache wins not user-editable =="
assert_contains "cache win suggests uninstall" \
  "$(py_get "$TMP/grok-context.json" 'next((w.get("suggestion","") for w in data["wins"] if "plugin" in w["title"].lower() or "managed" in w["title"].lower() or "Unload" in w["title"]), "")')" \
  "Uninstall"
assert_contains "cache win saving depends on uninstall" \
  "$(py_get "$TMP/grok-context.json" 'next((w.get("method","") for w in data["wins"] if "Unload" in w["title"] or "managed" in w["title"].lower() or "plugin" in w["title"].lower()), "")')" \
  "depends on which you uninstall"
assert_eq "cache win does not claim full listing" \
  "$(py_get "$TMP/grok-context.json" 'next((w["saving_est_tokens"]==0 for w in data["wins"] if "Unload" in w["title"]), False)')" \
  "True"
assert_contains "bot desc suggests edit bot" \
  "$(py_get "$TMP/grok-context.json" 'next((w.get("suggestion","") for w in data["wins"] if "bot description" in w["title"].lower()), "")')" \
  "bot description"

echo "== D. savings methods present; Max Mode not raw total =="
assert_eq "max mode is ask/unknown" \
  "$(py_get "$TMP/cursor-token.json" 'next(w.get("ask") and w["saving_est_tokens"]==0 for w in data["wins"] if "Max Mode" in w["title"])')" \
  "True"
assert_contains "max mode method mentions surcharge" \
  "$(py_get "$TMP/cursor-token.json" 'next(w.get("method","") for w in data["wins"] if "Max Mode" in w["title"])')" \
  "surcharge"

echo "== round3: ledger / same-model / tool fraction / grok bot =="
py_check "savings sum never exceeds total (fixtures + synthetic)" <<PY
import sys
sys.path.insert(0, "$LIB")
from pathlib import Path
from ai_audit.detect import detect_host
from ai_audit.token_audit import (
    rank_token_wins,
    run_token_audit,
    TOOL_RESULT_TRIM_FRAC,
    _token_volume_budget,
    _token_volume_raw,
    SAVINGS_BUDGET_FRAC,
)

FIX = Path("$FIX")

def check(label, data, wins):
    budget = _token_volume_budget(data)
    raw = _token_volume_raw(data)
    total = sum(int(w.get("saving_est_tokens") or 0) for w in wins)
    assert total <= budget, f"{label}: savings {total} > budget {budget}"
    # Soft ceiling: savings at most ~80% of measured usage
    # (ledger budget = SAVINGS_BUDGET_FRAC * raw; optimistic levers overlap).
    if raw > 0:
        assert total <= int(raw * SAVINGS_BUDGET_FRAC) + 1, (
            f"{label}: savings {total} > {SAVINGS_BUDGET_FRAC:.0%} of raw {raw}"
        )

# Synthetic: without a shared ledger, model half + 100% tool growth would exceed total.
synthetic = {
    "by_model": {"opus": {"total_tokens": 10000, "count": 2, "input": 8000, "cache_read": 0}},
    "by_kind": {},
    "by_tool": {
        "Read": {"input_growth": 9000, "count": 1},
        "Bash": {"input_growth": 8000, "count": 1},
    },
}
sw = [w.to_dict() for w in rank_token_wins("claude", synthetic)]
check("synthetic", synthetic, sw)
tool_wins = [w for w in sw if "Shrink tool" in w["title"]]
assert tool_wins, sw
for w in tool_wins:
    assert "trim tool result size by ~50%" in (w.get("method") or ""), w
    # Must not claim 100% of growth
    assert w["saving_est_tokens"] < 9000 or TOOL_RESULT_TRIM_FRAC < 1.0
# Without ledger: 5000 + 9000 + 8000 = 22000 > 10000
assert sum(w["saving_est_tokens"] for w in sw) <= 10000

for host, extra in (
    ("claude", {}),
    ("cursor", {"csv_path": FIX / "cursor" / "usage.csv"}),
    ("grok", {}),
):
    roots = detect_host(host, root=FIX / host)
    out = run_token_audit(roots, **extra)
    check(host, out["data"], out["wins"])
PY

# Cursor model residual must ignore other-model automation claims
py_check "cursor model overlap is same-model only" <<PY
import sys
sys.path.insert(0, "$LIB")
from pathlib import Path
from ai_audit.token_audit import parse_cursor_usage_csv, rank_token_wins

d = parse_cursor_usage_csv(Path("$FIX/cursor/usage.csv"))
# Attach by_model_kind as collect_cursor_tokens would
data = {
    "by_model": d["by_model"],
    "by_kind": d["by_kind"],
    "by_max_mode": d["by_max_mode"],
    "by_model_kind": d["by_model_kind"],
    "by_tool": {},
}
wins = rank_token_wins("cursor", data)
model_win = next(w for w in wins if "cheaper model" in w.title and "gpt-5" in w.title)
# Same-model kind claims for gpt-5:
# cloud all gpt-5: 39300/2=19650; gpt-5 automation 5400 → portion of automation saving
auto_saving = int(d["by_kind"]["automation"]["total_tokens"]) // 2
gpt5_auto = float(d["by_model_kind"]["gpt-5"].get("automation") or 0)
auto_total = float(d["by_kind"]["automation"]["total_tokens"])
same = 19650 + int(round(auto_saving * (gpt5_auto / auto_total)))
# Must NOT subtract full automation saving (which includes claude-sonnet)
wrong_same = 19650 + auto_saving
assert same < wrong_same, (same, wrong_same)
residual = int(d["by_model"]["gpt-5"]["total_tokens"]) - same
expected = residual // 2
# Allow ledger cap vs other wins; saving should be based on same-model residual, not wrong_same
assert "same-model" in (model_win.overlaps or model_win.method), model_win
assert model_win.saving_est_tokens == expected or model_win.saving_est_tokens <= expected, (
    model_win.saving_est_tokens, expected, same, wrong_same
)
PY

assert_eq "grok bot win saving unknown" \
  "$(py_get "$TMP/grok-token.json" 'next(w["saving_est_tokens"]==0 and w.get("ask") for w in data["wins"] if "bot" in w["title"].lower() and "context" in w["title"].lower())')" \
  "True"
assert_contains "grok bot win shows context bytes" \
  "$(py_get "$TMP/grok-token.json" 'next((w.get("measured","") for w in data["wins"] if "bot" in w["title"].lower()), "")')" \
  "context bytes"
assert_contains "grok bot win notes stored messages not model calls" \
  "$(py_get "$TMP/grok-token.json" 'next((w.get("measured","") for w in data["wins"] if "bot" in w["title"].lower()), "")')" \
  "not model calls"
assert_contains "grok bot win notes skill catalogs" \
  "$(py_get "$TMP/grok-token.json" 'next((w.get("measured","") for w in data["wins"] if "bot" in w["title"].lower()), "")')" \
  "skill catalogs"
assert_eq "grok bot win not arbitrary half volume" \
  "$(py_get "$TMP/grok-token.json" 'any("cut message volume ~50%" in (w.get("method") or "") for w in data["wins"])')" \
  "False"

py_check "no grok bot win exceeds that bot's own volume" <<PY
import sys
sys.path.insert(0, "$LIB")
from pathlib import Path
from ai_audit.detect import detect_host
from ai_audit.token_audit import run_token_audit, rank_token_wins

roots = detect_host("grok", root=Path("$FIX/grok"))
out = run_token_audit(roots)
by_bot = out["data"].get("by_bot") or {}
for w in out["wins"]:
    if "bot" not in w["title"].lower():
        continue
    # Match agent id / label inside backticks
    label = ""
    if "\`" in w["title"]:
        label = w["title"].split("\`")[1]
    bot_vol = None
    for aid, b in by_bot.items():
        if aid == label or aid.startswith(label.rstrip("…")) or label.rstrip("…") in aid:
            bot_vol = int(b.get("est_tokens") or 0)
            break
    assert bot_vol is not None, (w["title"], list(by_bot))
    assert int(w["saving_est_tokens"] or 0) <= bot_vol, (
        f"{w['title']}: saving {w['saving_est_tokens']} > bot volume {bot_vol}"
    )

# Synthetic regression: even if a claim were attempted, cap by bot volume
synth = {
    "by_bot": {
        "bot-a": {
            "est_tokens": 100,
            "message_count": 50,
            "stored_message_count": 50,
            "always_context_bytes": 4000,
            "always_context_tokens": 1000,
        }
    },
    "by_model": {},
    "by_kind": {},
    "routines": [],
}
# Current implementation: saving unknown (0) — still assert invariant
for w in rank_token_wins("grok", synth):
    if "bot" in w.title.lower():
        assert w.saving_est_tokens <= 100
        assert w.saving_est_tokens == 0
        assert "saving unknown" in (w.method or "")
        assert "not model calls" in (w.measured or "")
PY

assert_contains "claude tool win claims fraction not 100%" \
  "$(py_get "$TMP/claude-token.json" 'next((w.get("method","") for w in data["wins"] if "Shrink tool" in w["title"]), "")')" \
  "trim tool result size by ~50%"
assert_eq "claude savings sum <= total" \
  "$(py_get "$TMP/claude-token.json" 'sum(w["saving_est_tokens"] for w in data["wins"]) <= sum(int(v["total_tokens"]) for v in data["data"]["by_model"].values())')" \
  "True"
assert_eq "cursor savings sum <= total" \
  "$(py_get "$TMP/cursor-token.json" 'sum(w["saving_est_tokens"] for w in data["wins"]) <= sum(int(v["total_tokens"]) for v in data["data"]["by_model"].values())')" \
  "True"

echo "== round4: cache-hit fraction / 80% ceiling / cursor short prose =="
assert_contains "cache-hit method states price delta" \
  "$(py_get "$TMP/cursor-token.json" 'next((w.get("method","") for w in data["wins"] if "cache" in w["title"].lower()), "")')" \
  "price delta"
assert_eq "cursor savings at most ~80% of usage" \
  "$(py_get "$TMP/cursor-token.json" 'sum(w["saving_est_tokens"] for w in data["wins"]) <= int(0.8 * sum(int(v["total_tokens"]) for v in data["data"]["by_model"].values())) + 1')" \
  "True"
# Re-run cursor-token after fixture edits is not needed for prose — facts path:
"$BIN" facts --host cursor --root "$FIX/cursor" --memories "$FIX/cursor/memories.mdc" --json-only >"$TMP/cursor-facts.json"
assert_eq "cursor AGENTS.md short prose Use pnpm." \
  "$(py_get "$TMP/cursor-facts.json" 'sum(1 for f in data["facts"] if "AGENTS.md" in f["path"] and f["text"].strip()=="Use pnpm.")')" \
  "1"
assert_eq "cursor rule short prose Use pnpm." \
  "$(py_get "$TMP/cursor-facts.json" 'sum(1 for f in data["facts"] if f["source"]=="rule" and f["text"].strip()=="Use pnpm.")')" \
  "1"
assert_eq "cursor memories short prose Ship small." \
  "$(py_get "$TMP/cursor-facts.json" 'sum(1 for f in data["facts"] if f["source"]=="memories_export" and f["text"].strip()=="Ship small.")')" \
  "1"

echo "== round5/6: installed-copy locate + schedule + README =="
# Skills must not hard-code checkout-only paths; Claude must use braced ${CLAUDE_PLUGIN_ROOT}
for skill in context-audit token-audit facts-audit; do
  sk="$PLUGIN/skills/$skill/SKILL.md"
  assert_not_contains "skill $skill has no checkout path" \
    "$(cat "$sk")" \
    "bash plugins/ai-audit/bin/ai-audit"
  assert_contains "skill $skill has Locate the CLI" "$(cat "$sk")" "Locate the CLI"
  assert_contains "skill $skill uses braced CLAUDE_PLUGIN_ROOT" \
    "$(cat "$sk")" '${CLAUDE_PLUGIN_ROOT}'
  # Unbraced $CLAUDE_PLUGIN_ROOT/… must not appear (braced ${…} is required).
  # Negative lookbehind via ([^{]|^) so ${CLAUDE_PLUGIN_ROOT}/ is allowed.
  if grep -nE '(^|[^{])\$CLAUDE_PLUGIN_ROOT/' "$sk" >/dev/null; then
    echo "FAIL skill $skill has unbraced \$CLAUDE_PLUGIN_ROOT/"
    FAIL=$((FAIL + 1))
  else
    echo "PASS skill $skill has no unbraced \$CLAUDE_PLUGIN_ROOT/"
    PASS=$((PASS + 1))
  fi
  assert_contains "skill $skill has Schedule as a routine" "$(cat "$sk")" "Schedule as a routine"
  assert_contains "skill $skill schedules Claude Code" "$(cat "$sk")" "### Claude Code"
  assert_contains "skill $skill schedules Cursor" "$(cat "$sk")" "### Cursor"
  assert_contains "skill $skill schedules Grok Bot" "$(cat "$sk")" "### Grok Bot"
  assert_contains "skill $skill mentions Desktop Local routine" "$(cat "$sk")" "Local"
  assert_contains "skill $skill mentions cursor.com/automations" "$(cat "$sk")" "cursor.com/automations"
  assert_not_contains "skill $skill omits Customize Automations menu" "$(cat "$sk")" "Customize → Automations"
  assert_contains "skill $skill rejects invented Automations menu" "$(cat "$sk")" "Customize-sidebar"
  assert_contains "skill $skill Grok chat scheduling" "$(cat "$sk")" "in chat"
  assert_contains "skill $skill Grok info pane" "$(cat "$sk")" "info pane"
done

assert_contains "token skill scheduled saving unknown" \
  "$(cat "$PLUGIN/skills/token-audit/SKILL.md")" "saving unknown"
assert_contains "README install Claude Code" "$(cat "$PLUGIN/README.md")" "### Claude Code"
assert_contains "README install Cursor" "$(cat "$PLUGIN/README.md")" "### Cursor"
assert_contains "README install Grok Bot" "$(cat "$PLUGIN/README.md")" "### Grok Bot"
assert_contains "README CLAUDE_PLUGIN_ROOT skill text only" \
  "$(cat "$PLUGIN/README.md")" "skill text only"
assert_contains "README Grok clone + option B" "$(cat "$PLUGIN/README.md")" "option B"
assert_contains "README Claude cache layout" "$(cat "$PLUGIN/README.md")" \
  ".claude/plugins/cache"
assert_contains "root README has ai-audit install" "$(cat "$ROOT/README.md")" "Install — ai-audit"
assert_contains "root README Grok Bot install" "$(cat "$ROOT/README.md")" "### Grok Bot"

# locate-cli: no chmod +x; real Claude cache glob; read-only invoke
if grep -nE 'chmod \+x|chmod \+[a-z]*x' "$PLUGIN/scripts/locate-cli.sh" >/dev/null; then
  echo "FAIL locate-cli must not chmod +x"
  FAIL=$((FAIL + 1))
else
  echo "PASS locate-cli has no chmod +x"
  PASS=$((PASS + 1))
fi
assert_contains "locate-cli globs Claude cache" "$(cat "$PLUGIN/scripts/locate-cli.sh")" \
  ".claude/plugins/cache"
if grep -nE '\.claude/plugins/ai-audit/bin' "$PLUGIN/scripts/locate-cli.sh" >/dev/null; then
  echo "FAIL locate-cli still has fake ~/.claude/plugins/ai-audit path"
  FAIL=$((FAIL + 1))
else
  echo "PASS locate-cli omits fake ~/.claude/plugins/ai-audit"
  PASS=$((PASS + 1))
fi

# Copy plugin outside the repo; run CLI from an unrelated cwd the way skills instruct
INSTALL_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-install.XXXXXX")"
UNRELATED="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-cwd.XXXXXX")"
FAKE_CLAUDE_CACHE="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-claude-cache.XXXXXX")"
trap 'rm -rf "$INSTALL_PARENT" "$UNRELATED" "$FAKE_CLAUDE_CACHE"' RETURN
cp -a "$PLUGIN" "$INSTALL_PARENT/ai-audit"
# Simulate Claude cache layout: ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/
# (we override HOME for this subshell)
mkdir -p "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.0.1"
mkdir -p "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.1.0"
cp -a "$PLUGIN/." "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.0.1/"
cp -a "$PLUGIN/." "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.1.0/"
# Make 0.1.0 newer
touch "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.1.0/bin/ai-audit"

(
  cd "$UNRELATED"
  # B) Skill-file relative bootstrap from SKILL.md — invoke via bash (no chmod)
  export SKILL_MD="$INSTALL_PARENT/ai-audit/skills/context-audit/SKILL.md"
  AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"
  test -f "$AI_AUDIT"
  case "$AI_AUDIT" in
    "$INSTALL_PARENT"/*) ;;
    *) echo "locate returned unexpected path: $AI_AUDIT" >&2; exit 1 ;;
  esac
  bash "$AI_AUDIT" detect --root "$FIX/claude" --json-only >"$TMP/install-detect.json"
  bash "$AI_AUDIT" context --host claude --root "$FIX/claude" --json-only >"$TMP/install-context.json"
  # A) braced-style env (variable actually set in this shell for the test)
  unset SKILL_MD
  export CLAUDE_PLUGIN_ROOT="$INSTALL_PARENT/ai-audit"
  AI_AUDIT2="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/locate-cli.sh")"
  bash "$AI_AUDIT2" facts --host claude --root "$FIX/claude" --json-only >"$TMP/install-facts.json"
  # C) AI_AUDIT_PLUGIN_ROOT
  unset CLAUDE_PLUGIN_ROOT
  export AI_AUDIT_PLUGIN_ROOT="$INSTALL_PARENT/ai-audit"
  AI_AUDIT3="$(bash "${AI_AUDIT_PLUGIN_ROOT}/scripts/locate-cli.sh")"
  bash "$AI_AUDIT3" token --host claude --root "$FIX/claude" --json-only >"$TMP/install-token.json"
  # Claude cache under HOME (newest version)
  unset AI_AUDIT_PLUGIN_ROOT SKILL_MD CLAUDE_PLUGIN_ROOT CURSOR_PLUGIN_ROOT
  export HOME="$FAKE_CLAUDE_CACHE"
  AI_AUDIT4="$(AI_AUDIT_LOCATE_STRICT=1 bash "$INSTALL_PARENT/ai-audit/scripts/locate-cli.sh")"
  case "$AI_AUDIT4" in
    */0.1.0/bin/ai-audit) ;;
    *) echo "expected newest cache 0.1.0, got: $AI_AUDIT4" >&2; exit 1 ;;
  esac
  bash "$AI_AUDIT4" detect --root "$FIX/claude" --json-only >"$TMP/install-cache-detect.json"
  # Fail clearly when nothing is configured (strict + empty HOME cache)
  export HOME="$UNRELATED"
  if AI_AUDIT_LOCATE_STRICT=1 bash "$INSTALL_PARENT/ai-audit/scripts/locate-cli.sh" 2>"$TMP/locate-fail.txt"; then
    echo "locate should have failed without env" >&2
    exit 1
  fi
  grep -q "ai-audit CLI not found" "$TMP/locate-fail.txt"
  grep -q ".claude/plugins/cache" "$TMP/locate-fail.txt"
) || { echo "FAIL installed-copy locate/run"; FAIL=$((FAIL + 1)); }

if [[ -f "$TMP/install-detect.json" ]]; then
  assert_eq "install-copy detect host" "$(py_get "$TMP/install-detect.json" 'data["host"]')" "claude"
  assert_ge "install-copy context inventory" "$(py_get "$TMP/install-context.json" 'len(data["inventory"])')" 8
  assert_ge "install-copy facts scanned" "$(py_get "$TMP/install-facts.json" 'len(data.get("facts") or [])')" 1
  assert_eq "install-copy token audit" "$(py_get "$TMP/install-token.json" 'data["audit"]')" "token"
  assert_eq "claude-cache newest version" "$(py_get "$TMP/install-cache-detect.json" 'data["host"]')" "claude"
  assert_contains "locate fails clearly" "$(cat "$TMP/locate-fail.txt")" "ai-audit CLI not found"
  echo "PASS installed-copy locate + run from unrelated cwd"
  PASS=$((PASS + 1))
fi

echo "== baseline =="
assert_ge "claude inventory" "$(py_get "$TMP/claude-context.json" 'len(data["inventory"])')" 8
assert_eq "claude always rule" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("always-style.md"))')" \
  "always"
assert_not_contains "no secret leak" "$(cat "$TMP/claude-context.json")" "SHOULD_NOT_APPEAR"
mdc="$("$BIN" context --host claude --root "$FIX/claude" --md-only)"
assert_contains "md Context audit" "$mdc" "# Context audit"
assert_contains "md shows Method" "$mdc" "Method:"

echo
echo "ai-audit passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
