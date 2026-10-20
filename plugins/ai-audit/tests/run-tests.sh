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
  python3 -B - "$file" "$expr" <<'PY'
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
  # -B: never write __pycache__ into the plugin lib during tests
  out="$(python3 -B -c "$script" 2>&1)" || rc=$?
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
INSTALL_PARENT=""
UNRELATED=""
FAKE_CLAUDE_CACHE=""
cleanup() {
  rm -rf "$TMP" ${INSTALL_PARENT:+"$INSTALL_PARENT"} ${UNRELATED:+"$UNRELATED"} ${FAKE_CLAUDE_CACHE:+"$FAKE_CLAUDE_CACHE"}
}
trap cleanup EXIT

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
# v0.1.0: no savings wins; reconciliation must match CSV totals
assert_eq "cursor wins empty (no savings estimates)" \
  "$(py_get "$TMP/cursor-token.json" 'len(data["wins"])')" "0"
assert_eq "cursor reconciliation ok" \
  "$(py_get "$TMP/cursor-token.json" 'data["data"]["reconciliation"]["ok"]')" "True"
assert_eq "cursor recon source total" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["reconciliation"]["source_total_tokens"])')" "54500"
assert_eq "cursor recon by_model sum" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["reconciliation"]["by_model_sum"])')" "54500"
assert_eq "cursor recon by_kind sum" \
  "$(py_get "$TMP/cursor-token.json" 'int(data["data"]["reconciliation"]["by_kind_sum"])')" "54500"

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
assert_eq "context wins empty (no savings)" \
  "$(py_get "$TMP/grok-context.json" 'len(data["wins"])')" "0"

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

echo "== 13. Grok no official auto + usage file fallback =="
"$BIN" token --host grok --root "$FIX/grok" --json-only >"$TMP/grok-token.json"
assert_eq "grok source none without file" \
  "$(py_get "$TMP/grok-token.json" 'data["data"]["source"]')" "none"
assert_eq "grok official_auto false" \
  "$(py_get "$TMP/grok-token.json" 'data["data"]["official_auto"]')" "False"
assert_contains "grok says not possible" "$(py_get "$TMP/grok-token.json" '" ".join(data["notes"])')" "No official auto-download"
assert_contains "grok asks for file" "$(py_get "$TMP/grok-token.json" '" ".join(data["notes"])')" "usage-file"
cat >"$TMP/grok-usage.json" <<'EOF'
{"usage_total": 80, "usage_period": "week", "unit": "percent"}
EOF
"$BIN" token --host grok --root "$FIX/grok" --usage-file "$TMP/grok-usage.json" --json-only >"$TMP/grok-token2.json"
assert_eq "grok usage file source" \
  "$(py_get "$TMP/grok-token2.json" 'data["data"]["source"]')" "usage_file"
assert_eq "grok usage total from file" \
  "$(py_get "$TMP/grok-token2.json" 'data["data"]["usage_total"]')" "80"
assert_eq "grok file recon ok" \
  "$(py_get "$TMP/grok-token2.json" 'data["data"]["reconciliation"]["ok"]')" "True"
assert_eq "grok file recon total" \
  "$(py_get "$TMP/grok-token2.json" 'int(data["data"]["reconciliation"]["source_total_tokens"])')" "80"
assert_eq "grok wins empty" "$(py_get "$TMP/grok-token2.json" 'len(data["wins"])')" "0"

echo "== 14. token notes + usage-total fallback =="
md="$("$BIN" token --host grok --root "$FIX/grok" --usage-total 42 --usage-period "week" --md-only)"
assert_contains "md shows usage total" "$md" "42"
assert_contains "md reconciliation" "$md" "Reconciliation"
assert_contains "md no official" "$md" "No official auto-download"

echo "== 15. Admin API key auto; mocked offline; no CURSOR_API_KEY fallback =="
# CSV still preferred when provided; Admin API must be skipped (no live HTTP)
CURSOR_ADMIN_API_KEY="fake-key" \
  "$BIN" token --host cursor --root "$FIX/cursor" --csv "$FIX/cursor/usage.csv" --json-only >"$TMP/with-key-csv.json"
assert_eq "csv still primary with key" \
  "$(py_get "$TMP/with-key-csv.json" 'data["data"]["source"]')" "usage_csv"
assert_contains "csv skips Admin API" \
  "$(py_get "$TMP/with-key-csv.json" '" ".join(data["notes"])')" "CSV provided"
assert_eq "admin flag false when csv used" \
  "$(py_get "$TMP/with-key-csv.json" 'data["data"]["admin"]')" "False"

py_check "admin key var only + mocked pagination/epoch (offline)" <<PY
import sys, os, json
from unittest import mock
sys.path.insert(0, "$LIB")
from ai_audit.token_audit import fetch_cursor_admin_usage, _to_epoch_ms

assert fetch_cursor_admin_usage(api_key=None) is None
os.environ["CURSOR_API_KEY"] = "should-not-be-used"
os.environ.pop("CURSOR_ADMIN_API_KEY", None)
assert fetch_cursor_admin_usage(api_key=None) is None, "must not use CURSOR_API_KEY"
src = open("$LIB/ai_audit/token_audit.py", encoding="utf-8").read()
assert "CURSOR_ADMIN_API_KEY" in src
assert "Basic" in src and "pageSize" in src
fn = src[src.index("def fetch_cursor_admin_usage"):src.index("def _usage_dedupe_key")]
assert "CURSOR_API_KEY" not in fn

ms = _to_epoch_ms("2026-10-01")
assert isinstance(ms, int) and ms > 10_000_000_000, ms

captured = []
def fake_urlopen(req, timeout=20):
    body = json.loads(req.data.decode())
    captured.append(body)
    page = body["page"]
    class Resp:
        def read(self):
            if page == 1:
                return json.dumps({
                    "usageEvents": [{"model": "m", "tokenUsage": {
                        "inputTokens": 1, "outputTokens": 2,
                        "cacheReadTokens": 0, "cacheWriteTokens": 0,
                    }}],
                    "pagination": {"numPages": 2, "hasNextPage": True},
                }).encode()
            return json.dumps({
                "usageEvents": [{"model": "m", "tokenUsage": {
                    "inputTokens": 3, "outputTokens": 4,
                    "cacheReadTokens": 0, "cacheWriteTokens": 0,
                }}],
                "pagination": {"numPages": 2, "hasNextPage": False},
            }).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return Resp()

with mock.patch("urllib.request.urlopen", fake_urlopen):
    os.environ["CURSOR_ADMIN_API_KEY"] = "test-key"
    out = fetch_cursor_admin_usage(start_date="2026-10-01", end_date="2026-10-20")
    del os.environ["CURSOR_ADMIN_API_KEY"]
assert isinstance(captured[0]["startDate"], int), captured[0]
assert isinstance(captured[0]["endDate"], int), captured[0]
assert out["event_count"] == 2, out
assert out["pages_fetched"] == 2, out
assert out.get("truncated") is False
PY

echo "== 16. fact quote / sqlite wal / empty wins =="
py_check "rank_token_wins always empty in v0.1.0" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.token_audit import rank_token_wins
data = {
  "by_model": {"solo": {"input": 5000, "cache_read": 10, "output": 1, "total_tokens": 5011, "count": 1}},
  "by_kind": {}, "by_tool": {}, "by_bot": {}, "routines": [],
}
assert rank_token_wins("cursor", data) == []
PY

"$BIN" facts --host grok --root "$FIX/grok" --stale-days 30 --json-only >"$TMP/grok-facts.json"
assert_eq "fact text capped ~80" \
  "$(py_get "$TMP/grok-facts.json" 'max(len(f["text"]) for f in data["facts"]) <= 80')" "True"
assert_eq "facts wins empty" "$(py_get "$TMP/grok-facts.json" 'len(data["wins"])')" "0"
assert_ge "grok stale issues with reason" \
  "$(py_get "$TMP/grok-facts.json" 'sum(1 for i in data["issues"] if i["kind"]=="stale" and i.get("detail"))')" 1
assert_ge "grok conflict or orphan with reason" \
  "$(py_get "$TMP/grok-facts.json" 'sum(1 for i in data["issues"] if i["kind"] in ("conflict","orphan") and i.get("detail"))')" 1

py_check "sqlite opens copied db+wal+shm" <<PY
import sys
sys.path.insert(0, "$LIB")
src = open("$LIB/ai_audit/token_audit.py", encoding="utf-8").read()
assert "search-index.db-wal" in src or '"-wal"' in src
assert "mkdtemp" in src
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

assert_ge "folded skill desc bytes" \
  "$(py_get "$TMP/claude-context.json" 'next(i["bytes"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="big-skill" and i["kind"]=="skill_description")')" \
  100

echo "== E. Grok inventory lists managed cache (no savings wins) =="
assert_ge "managed skill cache in inventory" \
  "$(py_get "$TMP/grok-context.json" 'sum(1 for i in data["inventory"] if i["kind"]=="managed_skill_cache")')" 1
assert_ge "bot description in inventory" \
  "$(py_get "$TMP/grok-context.json" 'sum(1 for i in data["inventory"] if i["kind"]=="bot_description")')" 1

echo "== D. no savings estimates in token report =="
assert_eq "cursor wins empty" "$(py_get "$TMP/cursor-token.json" 'len(data["wins"])')" "0"
assert_eq "claude wins empty" "$(py_get "$TMP/claude-token.json" 'len(data["wins"])')" "0"
assert_contains "cursor md has reconciliation" \
  "$("$BIN" token --host cursor --root "$FIX/cursor" --csv "$FIX/cursor/usage.csv" --md-only)" \
  "Reconciliation"

echo "== round3: reconciliation across hosts =="
py_check "reconciliation ok for claude + cursor fixtures" <<PY
import sys
sys.path.insert(0, "$LIB")
from pathlib import Path
from ai_audit.detect import detect_host
from ai_audit.token_audit import run_token_audit, parse_cursor_usage_csv, parse_usage_file

FIX = Path("$FIX")

d = parse_cursor_usage_csv(FIX / "cursor" / "usage.csv")
r = d["reconciliation"]
assert r["ok"], r
assert r["source_total_tokens"] == r["by_model_sum"] == r["by_kind_sum"] == 54500

out = run_token_audit(detect_host("claude", root=FIX / "claude"))
r = out["data"]["reconciliation"]
assert r["ok"], r
assert r["source_total_tokens"] == r["by_model_sum"]
assert out["wins"] == []

uf = Path("$TMP") / "recon-grok.json"
uf.write_text('{"usage_total": 55, "usage_period": "week", "unit": "percent"}', encoding="utf-8")
out = run_token_audit(detect_host("grok", root=FIX / "grok"), usage_file=uf)
assert out["data"]["source"] == "usage_file"
assert out["data"]["reconciliation"]["ok"]
assert int(out["data"]["reconciliation"]["source_total_tokens"]) == 55

uf2 = Path("$TMP") / "recon-claude.json"
uf2.write_text(
    '{"by_model": {"claude-sonnet-4": {"total_tokens": 100}, "claude-opus-4": {"total_tokens": 200}}}',
    encoding="utf-8",
)
parsed = parse_usage_file(uf2)
assert parsed["reconciliation"]["ok"]
assert int(parsed["reconciliation"]["source_total_tokens"]) == 300
PY

echo "== round4: cursor short prose facts =="
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

echo "== round5/6: installed-copy locate + README (no schedule) =="
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
  assert_not_contains "skill $skill has no Schedule section" "$(cat "$sk")" "Schedule as a routine"
  assert_not_contains "skill $skill omits savings ranking" "$(cat "$sk")" "est. tokens saved"
done

assert_contains "token skill documents Grok not possible" \
  "$(cat "$PLUGIN/skills/token-audit/SKILL.md")" "Not possible"
assert_contains "token skill documents Claude JSONL" \
  "$(cat "$PLUGIN/skills/token-audit/SKILL.md")" "session JSONL"
assert_contains "token skill documents Cursor Admin API" \
  "$(cat "$PLUGIN/skills/token-audit/SKILL.md")" "Admin API"
assert_contains "HUMAN-TEST present" "$(cat "$PLUGIN/HUMAN-TEST.md")" "Human test steps"
assert_contains "README mentions out of scope" "$(cat "$PLUGIN/README.md")" "Out of scope"
assert_not_contains "README omits Scheduling as a routine" "$(cat "$PLUGIN/README.md")" "## Scheduling as a routine"
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
# Temps are cleaned by the EXIT trap (RETURN never fires at top level).
INSTALL_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-install.XXXXXX")"
UNRELATED="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-cwd.XXXXXX")"
FAKE_CLAUDE_CACHE="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-claude-cache.XXXXXX")"
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

# Fresh copy: a run must not create __pycache__ under the install
FRESH="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-fresh.XXXXXX")"
cp -a "$PLUGIN" "$FRESH/ai-audit"
# Ensure no pre-existing pycache from the workspace copy
find "$FRESH/ai-audit" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$FRESH/ai-audit" -type f -name '*.pyc' -delete 2>/dev/null || true
bash "$FRESH/ai-audit/bin/ai-audit" detect --root "$FIX/claude" --json-only >/dev/null
bash "$FRESH/ai-audit/bin/ai-audit" context --host claude --root "$FIX/claude" --json-only >/dev/null
if find "$FRESH/ai-audit" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) | grep -q .; then
  echo "FAIL fresh install left __pycache__ or .pyc"
  find "$FRESH/ai-audit" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \)
  FAIL=$((FAIL + 1))
else
  echo "PASS fresh install leaves no __pycache__"
  PASS=$((PASS + 1))
fi
rm -rf "$FRESH"

assert_contains "bin sets PYTHONDONTWRITEBYTECODE" "$(cat "$PLUGIN/bin/ai-audit")" "PYTHONDONTWRITEBYTECODE=1"
assert_contains "bin uses python3 -B" "$(cat "$PLUGIN/bin/ai-audit")" "python3 -B"

echo "== round8 regressions =="
# 1. secrets leak through facts + usage-file period
SECRET_FIX="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-secret.XXXXXX")"
cp -a "$FIX/grok" "$SECRET_FIX/grok"
printf '%s\n' '- (2025-02-01) Old token sk-ant-FAKE1234567890abcdef' \
  >>"$SECRET_FIX/grok/sand-data/user-memory/by-agent/agent-active/profile.md"
"$BIN" facts --host grok --root "$SECRET_FIX/grok" --json-only >"$TMP/secret-facts.json"
"$BIN" facts --host grok --root "$SECRET_FIX/grok" --md-only >"$TMP/secret-facts.md"
assert_eq "facts json redacts planted sk-ant key" \
  "$(grep -c 'FAKE1234567890abcdef' "$TMP/secret-facts.json" || true)" "0"
assert_eq "facts md redacts planted sk-ant key" \
  "$(grep -c 'FAKE1234567890abcdef' "$TMP/secret-facts.md" || true)" "0"
assert_contains "facts shows REDACTED marker" "$(cat "$TMP/secret-facts.json")" "REDACTED"
assert_eq "facts issues have no saving_est_tokens" \
  "$(py_get "$TMP/secret-facts.json" 'all("saving_est_tokens" not in i for i in data["issues"])')" "True"
printf '%s\n' '{"usage_total":9,"usage_period":"week sk-ant-FAKE1234567890abcdef","unit":"percent"}' \
  >"$TMP/secret-usage.json"
"$BIN" token --host grok --root "$FIX/grok" --usage-file "$TMP/secret-usage.json" --json-only \
  >"$TMP/secret-usage-out.json"
assert_eq "usage-file period redacts key" \
  "$(grep -c 'FAKE1234567890abcdef' "$TMP/secret-usage-out.json" || true)" "0"
rm -rf "$SECRET_FIX"

# 2/3 Admin API epoch + pagination covered in section 15 mock
# 3 missing --csv path errors clearly + non-zero exit
set +e
"$BIN" token --host cursor --root "$FIX/cursor" --csv /nonexistent/usage.csv --json-only \
  >"$TMP/missing-csv.json" 2>"$TMP/missing-csv.err"
missing_csv_rc=$?
set -e
assert_contains "missing csv error" \
  "$(py_get "$TMP/missing-csv.json" 'str(data.get("data",{}).get("error") or "") + " ".join(data.get("notes") or [])')" \
  "does not exist"
assert_eq "missing csv exit non-zero" "$([[ $missing_csv_rc -ne 0 ]] && echo yes || echo no)" "yes"

# 4 duplicate usage-file note
printf '%s\n' '{"usage_total":3,"unit":"percent"}' >"$TMP/uf.json"
"$BIN" token --host cursor --root "$FIX/cursor" --usage-file "$TMP/uf.json" --json-only \
  >"$TMP/uf-out.json"
assert_eq "usage-file Loaded note once" \
  "$(py_get "$TMP/uf-out.json" 'sum(1 for n in data["notes"] if "Loaded user usage file" in n)')" "1"

# 5 +N more in context md
py_check "context md +N more when >25 always" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.report import context_markdown
items = [{"load": "always", "path": f"/p/{i}", "kind": "k", "est_tokens": i} for i in range(30)]
md = context_markdown({"host": "x", "inventory": items})
assert "+5 more" in md, md
PY

# 6 Matches: no data for empty recon
"$BIN" token --host cursor --root "$FIX/cursor" --json-only >"$TMP/nodata-token.json"
assert_eq "empty cursor recon no_data" \
  "$(py_get "$TMP/nodata-token.json" 'data["data"]["reconciliation"].get("status") or data["data"]["reconciliation"].get("no_data")')" \
  "no_data"
md_nd="$("$BIN" token --host cursor --root "$FIX/cursor" --md-only)"
assert_contains "md says no data" "$md_nd" "no data"

# 7 CLAUDE_CONFIG_DIR
py_check "CLAUDE_CONFIG_DIR sets claude_home" <<PY
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, "$LIB")
from ai_audit.detect import detect_host
td = Path(tempfile.mkdtemp())
(td / "projects").mkdir()
os.environ["CLAUDE_CONFIG_DIR"] = str(td)
try:
    r = detect_host("claude")
    assert r.claude_home == td, r.claude_home
finally:
    os.environ.pop("CLAUDE_CONFIG_DIR", None)
    import shutil
    shutil.rmtree(td)
PY

# 8 no routines leftovers
assert_not_contains "cli has no --routines-json" "$(cat "$PLUGIN/lib/ai_audit/cli.py")" "--routines-json"
assert_eq "no routines.json fixture" \
  "$(test -f "$FIX/grok/routines.json" && echo yes || echo no)" "no"
assert_not_contains "token_audit has no SAVINGS_BUDGET" "$(cat "$PLUGIN/lib/ai_audit/token_audit.py")" "SAVINGS_BUDGET"
assert_not_contains "skill omits Grok routines row" \
  "$(cat "$PLUGIN/skills/context-audit/SKILL.md")" "Grok routines / connectors"
"$BIN" token --host grok --root "$FIX/grok" --usage-total 1 --json-only >"$TMP/no-routines.json"
assert_eq "token output has no routines field" \
  "$(py_get "$TMP/no-routines.json" '"routines" in data["data"]')" "False"

# 9 py_check uses -B; trap cleans install temps (assert cleanup function exists)
assert_contains "py_check uses python3 -B" "$(grep -A2 'py_check()' "$PLUGIN/tests/run-tests.sh" | tr '\n' ' ')" "python3 -B"
assert_contains "EXIT trap cleanup" "$(cat "$PLUGIN/tests/run-tests.sh")" "trap cleanup EXIT"
assert_not_contains "no RETURN trap for install temps" "$(cat "$PLUGIN/tests/run-tests.sh")" "trap 'rm -rf \"\$INSTALL_PARENT\""

# 10 HUMAN-TEST + Enterprise docs
assert_contains "HUMAN-TEST has claude plugin install" "$(cat "$PLUGIN/HUMAN-TEST.md")" "claude plugin"
assert_contains "HUMAN-TEST branch marketplace form" "$(cat "$PLUGIN/HUMAN-TEST.md")" \
  "scottgrayson/plugins#cursor/ai-audit-plugin-f41a"
assert_contains "HUMAN-TEST plants password= secret" "$(cat "$PLUGIN/HUMAN-TEST.md")" "password=Hunter2FakePass"
assert_contains "HUMAN-TEST greps does not exist" "$(cat "$PLUGIN/HUMAN-TEST.md")" "does not exist"
assert_contains "HUMAN-TEST says cd into a project" "$(cat "$PLUGIN/HUMAN-TEST.md")" "into a real project directory"
assert_contains "HUMAN-TEST Claude OR Cursor" "$(cat "$PLUGIN/HUMAN-TEST.md")" "Claude Code block OR the Cursor block"
assert_contains "HUMAN-TEST prints PASS" "$(cat "$PLUGIN/HUMAN-TEST.md")" "PASS secrets json redacted"
assert_contains "HUMAN-TEST zsh null_glob" "$(cat "$PLUGIN/HUMAN-TEST.md")" "null_glob"
assert_contains "HUMAN-TEST per-host --host claude" "$(cat "$PLUGIN/HUMAN-TEST.md")" "--host claude"
assert_contains "README Enterprise team admin" "$(cat "$PLUGIN/README.md")" "Enterprise team admin"
assert_contains "token skill Enterprise" "$(cat "$PLUGIN/skills/token-audit/SKILL.md")" "Enterprise"

echo "== round9 regressions =="
# 1. Redaction: password=/secret=/token=, AWS 40-char, crsr_, Bearer JWT (whole value)
py_check "redact password= secret= token= AWS crsr_ Bearer JWT" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.common import redact_secrets
cases = [
    ("db password=Hunter2FakePass!99 for prod", "Hunter2FakePass!99"),
    ("password: Hunter2FakePass!99", "Hunter2FakePass!99"),
    ("secret=SuperSecretValue99", "SuperSecretValue99"),
    ("token=tok_live_abc123XYZ", "tok_live_abc123XYZ"),
    ("key wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY end", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("crsr_fake0123456789abcdef0123456789abcdef0123456789", "crsr_fake0123456789"),
    (
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "eyJhbGciOiJIUzI1NiJ9",
    ),
]
for raw, leak in cases:
    out = redact_secrets(raw)
    assert leak not in out, (raw, out)
    assert "REDACTED" in out, out
    # No long visible secret prefix (whole value redacted)
    assert "Hunter2" not in out
    assert "wJalrXUtnFEMI" not in out
    assert "fake0123456789abcdef" not in out
    assert "eyJhbGciOiJIUzI1NiJ9.eyJ" not in out
PY

SECRET9="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-secret9.XXXXXX")"
cp -a "$FIX/grok" "$SECRET9/grok"
printf '%s\n' \
  '- (2025-03-01) db password=Hunter2FakePass!99 secret=SuperSecretValue99 token=tok_live_abc123XYZ' \
  '- (2025-03-01) aws wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY' \
  '- (2025-03-01) admin crsr_fake0123456789abcdef0123456789abcdef0123456789' \
  '- (2025-03-01) auth Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U' \
  >>"$SECRET9/grok/sand-data/user-memory/by-agent/agent-active/profile.md"
"$BIN" facts --host grok --root "$SECRET9/grok" --json-only >"$TMP/r9-facts.json"
"$BIN" facts --host grok --root "$SECRET9/grok" --md-only >"$TMP/r9-facts.md"
for leak in Hunter2FakePass SuperSecretValue99 tok_live_abc123XYZ wJalrXUtnFEMI crsr_fake0123456789abcdef eyJhbGciOiJIUzI1NiJ9; do
  assert_eq "facts json redacts $leak" "$(grep -c "$leak" "$TMP/r9-facts.json" || true)" "0"
  assert_eq "facts md redacts $leak" "$(grep -c "$leak" "$TMP/r9-facts.md" || true)" "0"
done
printf '%s\n' '{"usage_total":9,"usage_period":"wk password=Hunter2FakePass!99 crsr_fake0123456789abcdef0123456789 Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig","unit":"percent"}' \
  >"$TMP/r9-usage.json"
"$BIN" token --host grok --root "$FIX/grok" --usage-file "$TMP/r9-usage.json" --json-only \
  >"$TMP/r9-usage-out.json"
"$BIN" token --host grok --root "$FIX/grok" --usage-file "$TMP/r9-usage.json" --md-only \
  >"$TMP/r9-usage-out.md"
assert_eq "usage json redacts password value" "$(grep -c 'Hunter2FakePass' "$TMP/r9-usage-out.json" || true)" "0"
assert_eq "usage md redacts password value" "$(grep -c 'Hunter2FakePass' "$TMP/r9-usage-out.md" || true)" "0"
assert_eq "usage json redacts crsr_" "$(grep -c 'crsr_fake0123456789abcdef' "$TMP/r9-usage-out.json" || true)" "0"
assert_eq "usage json redacts Bearer jwt" "$(grep -c 'eyJhbGciOiJIUzI1NiJ9' "$TMP/r9-usage-out.json" || true)" "0"
rm -rf "$SECRET9"

# 2. Context: no plugin cache/marketplace fixtures; home-as-project skips nested; --root keeps widget-skill; dedupe
assert_eq "fixture nested widget-skill present" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if "widget-skill" in i["path"] and i["kind"]=="skill_description")')" \
  "1"
py_check "context skips ~/.claude/plugins cache+marketplace and home nested" <<PY
import sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, "$LIB")
from ai_audit.context_audit import run_context_audit, _dedupe_items, _item
from ai_audit.detect import Roots
from ai_audit.common import InventoryItem

td = Path(tempfile.mkdtemp())
try:
    home = td / "home"
    home.mkdir()
    claude = home / ".claude"
    for tree in (
        claude / "plugins" / "cache" / "mp" / "ai-audit" / "0.1.0" / "fixtures" / "claude" / "project" / ".claude" / "skills" / "big-skill",
        claude / "plugins" / "marketplaces" / "mp" / "plugins" / "ai-audit" / "fixtures" / "claude" / "project" / ".claude" / "skills" / "big-skill",
    ):
        tree.mkdir(parents=True)
        (tree / "SKILL.md").write_text("---\nname: big-skill\ndescription: fixture leak\n---\nbody\n")
    (home / "CLAUDE.md").write_text("# home\n")
    # home as project (~)
    roots = Roots("claude", home, home, None, claude, home / ".cursor", False)
    inv = run_context_audit(roots)["inventory"]
    bad = [i["path"] for i in inv if "big-skill" in i["path"] or "/plugins/" in i["path"].replace(str(td), "")]
    # Only flag real plugin-tree paths
    bad = [i["path"] for i in inv if ".claude/plugins" in i["path"] or "big-skill" in i["path"]]
    assert bad == [], bad
    # monorepo: exclude ai-audit/fixtures under a different project root
    mono = td / "repo"
    wdir = mono / "plugins" / "ai-audit" / "fixtures" / "claude" / "project" / "packages" / "widget" / ".claude" / "skills" / "widget-skill"
    wdir.mkdir(parents=True)
    (wdir / "SKILL.md").write_text("---\nname: widget-skill\ndescription: d\n---\nx\n")
    (mono / "CLAUDE.md").write_text("# repo\n")
    roots2 = Roots("claude", home, mono, None, claude, home / ".cursor", False)
    inv2 = run_context_audit(roots2)["inventory"]
    assert not any("widget-skill" in i["path"] for i in inv2), [i["path"] for i in inv2 if "widget" in i["path"]]
    # dedupe
    a = _item("claude", "k", "s", "/tmp/x", 1, "always")
    b = _item("claude", "k", "s", "/tmp/x", 2, "always")
    out = _dedupe_items([a, b])
    assert len(out) == 1, out
finally:
    shutil.rmtree(td)
PY

# 3. Unparseable --admin-start/--admin-end must error (exit 2), not drop silently
set +e
"$BIN" token --host cursor --root "$FIX/cursor" --admin-start '10/01/2026' --json-only \
  >"$TMP/bad-admin-start.json" 2>"$TMP/bad-admin-start.err"
bad_start_rc=$?
"$BIN" token --host cursor --root "$FIX/cursor" --admin-end 'yesterday' --md-only \
  >"$TMP/bad-admin-end.md" 2>"$TMP/bad-admin-end.err"
bad_end_rc=$?
set -e
assert_eq "unparseable admin-start exit 2" "$bad_start_rc" "2"
assert_contains "unparseable admin-start message" \
  "$(cat "$TMP/bad-admin-start.json" "$TMP/bad-admin-start.err" 2>/dev/null || true)" "unparseable"
assert_eq "unparseable admin-end exit 2" "$bad_end_rc" "2"
assert_contains "unparseable admin-end message" "$(cat "$TMP/bad-admin-end.md")" "unparseable"

# 4. HUMAN-TEST shell blocks under zsh when installable, else bash nomatch-equivalent
SHELL_SMOKE="$PLUGIN/tests/human-test-shell-smoke.sh"
assert_eq "human-test shell smoke script exists" "$(test -f "$SHELL_SMOKE" && echo yes || echo no)" "yes"
if ! command -v zsh >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get install -y zsh >/dev/null 2>&1 || apt-get install -y zsh >/dev/null 2>&1 || true
  fi
fi
if bash "$SHELL_SMOKE"; then
  echo "PASS HUMAN-TEST shell smoke"
  PASS=$((PASS + 1))
else
  echo "FAIL HUMAN-TEST shell smoke"
  FAIL=$((FAIL + 1))
fi

echo "== public-repo clean =="
assert_contains "three skills present" "$(ls "$PLUGIN/skills")" "context-audit"
assert_contains "token-audit skill dir" "$(ls "$PLUGIN/skills")" "token-audit"
assert_contains "facts-audit skill dir" "$(ls "$PLUGIN/skills")" "facts-audit"
if rg -n 'scottgrayson/(setup|agent-environment|ai-plugins)' "$PLUGIN" "$ROOT/README.md" 2>/dev/null; then
  echo "FAIL forbidden repo references"
  FAIL=$((FAIL + 1))
else
  echo "PASS no forbidden repo references"
  PASS=$((PASS + 1))
fi
py_check "context inventory paths exist on disk" <<PY
import json
from pathlib import Path
data = json.load(open("$TMP/claude-context.json", encoding="utf-8"))
missing = []
skip_kinds = {"mcp_servers", "cli_config", "managed_skill_cache", "plugin_skill_cache", "user_rules"}
for i in data["inventory"]:
    path = i.get("path") or ""
    if not path or i.get("kind") in skip_kinds:
        continue
    p = Path(path)
    if not p.exists() and int(i.get("bytes") or 0) > 0:
        missing.append(path)
assert not missing, missing[:8]
PY

echo "== baseline =="
assert_ge "claude inventory" "$(py_get "$TMP/claude-context.json" 'len(data["inventory"])')" 8
assert_eq "claude always rule" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if i["path"].endswith("always-style.md"))')" \
  "always"
assert_not_contains "no secret leak" "$(cat "$TMP/claude-context.json")" "SHOULD_NOT_APPEAR"
mdc="$("$BIN" context --host claude --root "$FIX/claude" --md-only)"
assert_contains "md Context audit" "$mdc" "# Context audit"
assert_contains "md shows Always-loaded" "$mdc" "Always-loaded"

echo
echo "ai-audit passed=$PASS failed=$FAIL"
[[ "$FAIL" -eq 0 ]]
