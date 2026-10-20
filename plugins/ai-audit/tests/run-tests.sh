#!/usr/bin/env bash
# Fixture-based tests for ai-audit stage 1 (context + detect + redaction).
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

py_check() {
  local name="$1"
  local script out rc=0
  script="$(cat)"
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

echo "== 2. context inventories =="
"$BIN" context --host grok --root "$FIX/grok" --json-only >"$TMP/grok-context.json"
assert_eq "grok active bot always" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="bot_description" and (i.get("meta") or {}).get("agent_id")=="agent-active")')" \
  "always"
assert_eq "managed cache only novel skills" \
  "$(py_get "$TMP/grok-context.json" 'next(((i.get("meta") or {}).get("skill_count") for i in data["inventory"] if i["kind"]=="managed_skill_cache"), 0)')" \
  "1"
assert_eq "orphan load unknown" \
  "$(py_get "$TMP/grok-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="memory" and (i.get("meta") or {}).get("orphan"))')" \
  "unknown"
assert_eq "context wins empty (no savings)" \
  "$(py_get "$TMP/grok-context.json" 'len(data["wins"])')" "0"

"$BIN" context --host claude --root "$FIX/claude" --json-only >"$TMP/claude-context.json"
assert_eq "claude manual skill on_demand" \
  "$(py_get "$TMP/claude-context.json" 'next(i["load"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="manual-only" and i["kind"]=="skill_description")')" \
  "on_demand"
assert_ge "big-skill skill_asset" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if i["kind"]=="skill_asset" and (i.get("meta") or {}).get("skill")=="big-skill")')" \
  1
assert_ge "import always" \
  "$(py_get "$TMP/claude-context.json" 'sum(1 for i in data["inventory"] if i["path"].endswith("shared-conventions.md") and i["load"]=="always")')" 1
assert_eq "MCP sized by schemas" \
  "$(py_get "$TMP/claude-context.json" 'next((i.get("meta") or {}).get("sized_by") for i in data["inventory"] if i["kind"]=="mcp_servers")')" \
  "schemas"

"$BIN" context --host cursor --root "$FIX/cursor" --json-only >"$TMP/cursor-context.json"
assert_eq "cursor slash-only on_demand" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if (i.get("meta") or {}).get("skill")=="slash-only" and i["kind"]=="skill_description")')" \
  "on_demand"
assert_eq "cli-config not context" \
  "$(py_get "$TMP/cursor-context.json" 'next(i["load"] for i in data["inventory"] if i["kind"]=="cli_config")')" "unknown"

echo "== 3. skills / docs / locate =="
assert_contains "context skill present" "$(ls "$PLUGIN/skills")" "context-audit"
assert_not_contains "no token-audit skill" "$(ls "$PLUGIN/skills")" "token-audit"
assert_not_contains "no facts-audit skill" "$(ls "$PLUGIN/skills")" "facts-audit"
sk="$PLUGIN/skills/context-audit/SKILL.md"
assert_not_contains "skill has no checkout path" "$(cat "$sk")" "bash plugins/ai-audit/bin/ai-audit"
assert_contains "skill has Locate the CLI" "$(cat "$sk")" "Locate the CLI"
assert_contains "skill uses braced CLAUDE_PLUGIN_ROOT" "$(cat "$sk")" '${CLAUDE_PLUGIN_ROOT}'
assert_contains "HUMAN-TEST present" "$(cat "$PLUGIN/HUMAN-TEST.md")" "Human test steps"
assert_contains "HUMAN-TEST stage 1" "$(cat "$PLUGIN/HUMAN-TEST.md")" "stage 1"
assert_contains "README stage 1" "$(cat "$PLUGIN/README.md")" "stage 1"
assert_contains "README install Claude Code" "$(cat "$PLUGIN/README.md")" "### Claude Code"
assert_contains "README install Cursor" "$(cat "$PLUGIN/README.md")" "### Cursor"
assert_contains "root README has ai-audit install" "$(cat "$ROOT/README.md")" "Install — ai-audit"
assert_not_contains "CLI has no token subcommand" "$("$BIN" --help 2>&1)" "token"
assert_not_contains "CLI has no facts subcommand" "$("$BIN" --help 2>&1)" "facts"
assert_contains "CLI has context" "$("$BIN" --help 2>&1)" "context"

if grep -nE 'chmod \+x|chmod \+[a-z]*x' "$PLUGIN/scripts/locate-cli.sh" >/dev/null; then
  echo "FAIL locate-cli must not chmod +x"
  FAIL=$((FAIL + 1))
else
  echo "PASS locate-cli has no chmod +x"
  PASS=$((PASS + 1))
fi

INSTALL_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-install.XXXXXX")"
UNRELATED="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-cwd.XXXXXX")"
FAKE_CLAUDE_CACHE="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-claude-cache.XXXXXX")"
cp -a "$PLUGIN" "$INSTALL_PARENT/ai-audit"
mkdir -p "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.0.1"
mkdir -p "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.1.0"
cp -a "$PLUGIN/." "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.0.1/"
cp -a "$PLUGIN/." "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.1.0/"
touch "$FAKE_CLAUDE_CACHE/.claude/plugins/cache/scottgrayson-plugins/ai-audit/0.1.0/bin/ai-audit"

(
  cd "$UNRELATED"
  export SKILL_MD="$INSTALL_PARENT/ai-audit/skills/context-audit/SKILL.md"
  AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"
  test -f "$AI_AUDIT"
  bash "$AI_AUDIT" detect --root "$FIX/claude" --json-only >"$TMP/install-detect.json"
  bash "$AI_AUDIT" context --host claude --root "$FIX/claude" --json-only >"$TMP/install-context.json"
  unset SKILL_MD
  export CLAUDE_PLUGIN_ROOT="$INSTALL_PARENT/ai-audit"
  AI_AUDIT2="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/locate-cli.sh")"
  bash "$AI_AUDIT2" context --host claude --root "$FIX/claude" --json-only >"$TMP/install-context2.json"
  unset CLAUDE_PLUGIN_ROOT
  export AI_AUDIT_PLUGIN_ROOT="$INSTALL_PARENT/ai-audit"
  AI_AUDIT3="$(bash "${AI_AUDIT_PLUGIN_ROOT}/scripts/locate-cli.sh")"
  bash "$AI_AUDIT3" detect --root "$FIX/claude" --json-only >"$TMP/install-detect2.json"
  unset AI_AUDIT_PLUGIN_ROOT SKILL_MD CLAUDE_PLUGIN_ROOT CURSOR_PLUGIN_ROOT
  export HOME="$FAKE_CLAUDE_CACHE"
  AI_AUDIT4="$(AI_AUDIT_LOCATE_STRICT=1 bash "$INSTALL_PARENT/ai-audit/scripts/locate-cli.sh")"
  case "$AI_AUDIT4" in
    */0.1.0/bin/ai-audit) ;;
    *) echo "expected newest cache 0.1.0, got: $AI_AUDIT4" >&2; exit 1 ;;
  esac
  bash "$AI_AUDIT4" detect --root "$FIX/claude" --json-only >"$TMP/install-cache-detect.json"
  export HOME="$UNRELATED"
  if AI_AUDIT_LOCATE_STRICT=1 bash "$INSTALL_PARENT/ai-audit/scripts/locate-cli.sh" 2>"$TMP/locate-fail.txt"; then
    echo "locate should have failed without env" >&2
    exit 1
  fi
  grep -q "ai-audit CLI not found" "$TMP/locate-fail.txt"
) || { echo "FAIL installed-copy locate/run"; FAIL=$((FAIL + 1)); }

if [[ -f "$TMP/install-detect.json" ]]; then
  assert_eq "install-copy detect host" "$(py_get "$TMP/install-detect.json" 'data["host"]')" "claude"
  assert_ge "install-copy context inventory" "$(py_get "$TMP/install-context.json" 'len(data["inventory"])')" 8
  assert_eq "claude-cache newest version" "$(py_get "$TMP/install-cache-detect.json" 'data["host"]')" "claude"
  assert_contains "locate fails clearly" "$(cat "$TMP/locate-fail.txt")" "ai-audit CLI not found"
  echo "PASS installed-copy locate + run from unrelated cwd"
  PASS=$((PASS + 1))
fi

FRESH="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-fresh.XXXXXX")"
cp -a "$PLUGIN" "$FRESH/ai-audit"
find "$FRESH/ai-audit" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$FRESH/ai-audit" -type f -name '*.pyc' -delete 2>/dev/null || true
bash "$FRESH/ai-audit/bin/ai-audit" detect --root "$FIX/claude" --json-only >/dev/null
bash "$FRESH/ai-audit/bin/ai-audit" context --host claude --root "$FIX/claude" --json-only >/dev/null
if find "$FRESH/ai-audit" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) | grep -q .; then
  echo "FAIL fresh install left __pycache__ or .pyc"
  FAIL=$((FAIL + 1))
else
  echo "PASS fresh install leaves no __pycache__"
  PASS=$((PASS + 1))
fi
rm -rf "$FRESH"
assert_contains "bin sets PYTHONDONTWRITEBYTECODE" "$(cat "$PLUGIN/bin/ai-audit")" "PYTHONDONTWRITEBYTECODE=1"

echo "== 4. HUMAN-TEST shell smoke =="
if bash "$PLUGIN/tests/human-test-shell-smoke.sh"; then
  echo "PASS HUMAN-TEST shell smoke"
  PASS=$((PASS + 1))
else
  echo "FAIL HUMAN-TEST shell smoke"
  FAIL=$((FAIL + 1))
fi

echo "== 5. redaction + context path safety =="
py_check "redact: path-safe AWS + secret shapes" <<PY
import sys
sys.path.insert(0, "$LIB")
from ai_audit.common import redact_secrets, redact_tree, looks_like_secret_value
path = "/tmp/r10fp/abcdefghijklmnopqrstuv/CLAUDE.md"
assert redact_secrets(path) == path
path2 = "/home/box/sand-data/workflows/why/references/epistemics.md"
assert redact_secrets(path2) == path2
tree = {"inventory": [{"path": path2, "notes": "db password=Hunter2FakePass!99"}]}
out = redact_tree(tree)
assert out["inventory"][0]["path"] == path2
assert "Hunter2FakePass" not in out["inventory"][0]["notes"]
aws = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
assert aws not in redact_secrets("aws secret " + aws)
assert "dragon" not in redact_secrets("password: dragon")
assert "REDACTED" not in redact_secrets("token: optional")
# Round-10 original string restored; simple rule intentionally over-redacts it.
assert "REDACTED" in redact_secrets("Default token: Composer model")
assert "Hunter2FakePass99" not in redact_secrets("password: Hunter2FakePass99 for prod")
assert "dragon" not in redact_secrets("password: set to dragon")
assert "dragon" not in redact_secrets("password - dragon")
assert "dragon" not in redact_secrets("passwd dragon")
assert redact_secrets("Tests: pass=196") == "Tests: pass=196"
assert looks_like_secret_value("tok_live_abc123XYZ999")
PY

PATHFP="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-r10fp.XXXXXX")"
mkdir -p "$PATHFP/abcdefghijklmnopqrstuv"
printf 'x\n' >"$PATHFP/abcdefghijklmnopqrstuv/CLAUDE.md"
HOMEFP="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-r10home.XXXXXX")"
mkdir -p "$HOMEFP/.claude"
(
  cd "$PATHFP/abcdefghijklmnopqrstuv"
  HOME="$HOMEFP" "$BIN" context --host claude --md-only >"$TMP/r10-path.md"
  HOME="$HOMEFP" "$BIN" context --host claude --json-only >"$TMP/r10-path.json"
)
assert_not_contains "context md keeps long path segment" "$(cat "$TMP/r10-path.md")" "REDACTED.md"
assert_contains "context md shows CLAUDE.md path" "$(cat "$TMP/r10-path.md")" "abcdefghijklmnopqrstuv/CLAUDE.md"
assert_not_contains "context json keeps long path segment" "$(cat "$TMP/r10-path.json")" "REDACTED"
rm -rf "$PATHFP" "$HOMEFP"

HELP_ONE="$(printf '%s' "$("$BIN" --help 2>&1 || true)" | tr '\n' ' ')"
assert_contains "CLI help redacts common secret formats" "$HELP_ONE" "redacts common secret formats"

py_check "nested .claude/skills marked on_demand" <<PY
import sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, "$LIB")
from ai_audit.context_audit import run_context_audit
from ai_audit.detect import Roots
td = Path(tempfile.mkdtemp())
try:
    home = td / "home"; home.mkdir()
    claude = home / ".claude"; claude.mkdir()
    proj = td / "proj"
    nest = proj / "packages" / "pkg" / ".claude" / "skills" / "deep-skill"
    nest.mkdir(parents=True)
    (nest / "SKILL.md").write_text("---\nname: deep-skill\ndescription: nested desc\n---\nbody\n")
    (proj / "CLAUDE.md").write_text("# p\n")
    roots = Roots("claude", home, proj, None, claude, home / ".cursor", False)
    inv = run_context_audit(roots)["inventory"]
    rows = [i for i in inv if "deep-skill" in i["path"] and i["kind"] == "skill_description"]
    assert rows and all(i["load"] == "on_demand" for i in rows)
finally:
    shutil.rmtree(td)
PY

py_check "enabled plugin skills from installed_plugins.json" <<PY
import json, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, "$LIB")
from ai_audit.context_audit import run_context_audit
from ai_audit.detect import Roots
td = Path(tempfile.mkdtemp())
try:
    home = td / "home"
    claude = home / ".claude"
    proj = td / "proj"; proj.mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("# p\n")
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"ai-audit@scottgrayson-plugins": True, "other@mp": False}
    }))
    inst = claude / "plugins" / "cache" / "scottgrayson-plugins" / "ai-audit" / "0.1.0"
    for name in ("context-audit",):
        d = inst / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: %s\ndescription: d\n---\nb\n" % name)
    other = claude / "plugins" / "cache" / "mp" / "other" / "1.0.0" / "skills" / "other-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: other-skill\ndescription: no\n---\nx\n")
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {
            "ai-audit@scottgrayson-plugins": [
                {"scope": "user", "installPath": str(inst), "version": "0.1.0"},
            ],
            "other@mp": [
                {"scope": "user", "installPath": str(other.parent.parent), "version": "1.0.0"},
            ],
        }
    }))
    roots = Roots("claude", home, proj, None, claude, home / ".cursor", False)
    inv = run_context_audit(roots)["inventory"]
    plugin_skills = [i for i in inv if i.get("kind") == "skill_description" and i.get("scope") == "plugin"]
    names = sorted({(i.get("meta") or {}).get("skill") or Path(i["path"]).parent.name for i in plugin_skills})
    assert names == ["context-audit"], names
    assert not any("other-skill" in i["path"] for i in inv)
finally:
    shutil.rmtree(td)
PY

assert_contains "HUMAN-TEST path-independent Cursor cp" "$(cat "$PLUGIN/HUMAN-TEST.md")" "/path/to/plugins/ai-audit"
assert_not_contains "HUMAN-TEST no exit 1" "$(cat "$PLUGIN/HUMAN-TEST.md")" "exit 1"

echo "== 6. install FAIL gates + Cursor copy =="
py_check "HUMAN-TEST install checks FAIL on empty HOME" <<PY
import pathlib, re, subprocess, tempfile
ht = pathlib.Path("$PLUGIN/HUMAN-TEST.md").read_text(encoding="utf-8")
blocks = re.findall(r"\`\`\`(?:bash|sh|zsh)\n(.*?)\`\`\`", ht, flags=re.DOTALL)
checks = [b for b in blocks if "install paths" in b and "PASS" in b and "FAIL" in b]
assert len(checks) >= 2
empty = tempfile.mkdtemp(prefix="ai-audit-empty-home.")
try:
    for bi, block in enumerate(checks):
        script = "HOME=%r\nexport HOME\nunset AI_AUDIT PLUGIN\n%s\n" % (empty, block)
        proc = subprocess.run(["bash", "--norc", "--noprofile", "-c", script], capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        assert "FAIL" in out and "install paths" in out, (bi, out)
        assert not re.search(r"^PASS .*install paths", out, re.M), (bi, out)
finally:
    import shutil; shutil.rmtree(empty)
PY

py_check "enabledPlugins precedence local > project > user" <<PY
import json, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, "$LIB")
from ai_audit.context_audit import run_context_audit, _enabled_plugin_ids
from ai_audit.detect import Roots
td = Path(tempfile.mkdtemp())
try:
    home = td / "home"; claude = home / ".claude"; claude.mkdir(parents=True)
    proj = td / "proj"; (proj / ".claude").mkdir(parents=True); (proj / "CLAUDE.md").write_text("# p\n")
    plug = "ai-audit@scottgrayson-plugins"
    inst = claude / "plugins" / "cache" / "scottgrayson-plugins" / "ai-audit" / "0.1.0"
    d = inst / "skills" / "context-audit"; d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: context-audit\ndescription: d\n---\nb\n")
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {plug: [{"scope": "user", "installPath": str(inst), "version": "0.1.0"}]},
    }))
    (claude / "settings.json").write_text(json.dumps({"enabledPlugins": {plug: True}}))
    (proj / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {plug: False}}))
    assert _enabled_plugin_ids(claude, proj) == []
    roots = Roots("claude", home, proj, None, claude, home / ".cursor", False)
    inv = run_context_audit(roots)["inventory"]
    assert not any(i.get("scope") == "plugin" for i in inv)
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({"enabledPlugins": {plug: True}}))
    assert _enabled_plugin_ids(claude, proj) == [plug]
finally:
    shutil.rmtree(td)
PY

assert_contains "HUMAN-TEST mentions Cursor cache" "$(cat "$PLUGIN/HUMAN-TEST.md")" ".cursor/plugins/cache"
assert_contains "HUMAN-TEST idempotent rm" "$(cat "$PLUGIN/HUMAN-TEST.md")" "rm -rf ~/.cursor/plugins/local/ai-audit"
py_check "Cursor local copy does not nest; placeholder skips rm" <<PY
import pathlib, re, subprocess, tempfile, shutil
ht = pathlib.Path("$PLUGIN/HUMAN-TEST.md").read_text(encoding="utf-8")
blocks = re.findall(r"\`\`\`(?:bash|sh|zsh)\n(.*?)\`\`\`", ht, flags=re.DOTALL)
cp_blocks = [b for b in blocks if "rm -rf" in b and "plugins/local/ai-audit" in b and "cp -R" in b and "SRC=" in b]
assert cp_blocks
src = pathlib.Path("$PLUGIN")
home = pathlib.Path(tempfile.mkdtemp(prefix="ai-audit-cursor-cp."))
try:
    dest = home / ".cursor" / "plugins" / "local" / "ai-audit"
    dest.mkdir(parents=True); (dest / "bin").mkdir(); (dest / "bin" / "ai-audit").write_text("#!/bin/sh\n")
    script = "HOME=%r\nexport HOME\n%s\n" % (str(home), cp_blocks[0])
    proc = subprocess.run(["bash", "--norc", "-c", script], capture_output=True, text=True)
    assert "FAIL" in (proc.stdout or "")
    assert (dest / "bin" / "ai-audit").is_file()
    script2 = cp_blocks[0].replace("/path/to/plugins/ai-audit", str(src))
    script2 = "HOME=%r\nexport HOME\n%s\n" % (str(home), script2)
    subprocess.run(["bash", "--norc", "-c", script2], check=True, capture_output=True, text=True)
    assert not (dest / "ai-audit").exists()
    subprocess.run(["bash", "--norc", "-c", script2], check=True, capture_output=True, text=True)
    assert not (dest / "ai-audit").exists()
finally:
    shutil.rmtree(home)
PY

py_check "HUMAN-TEST install PASS on versioned cache layout" <<PY
import pathlib, re, subprocess, tempfile, shutil
ht = pathlib.Path("$PLUGIN/HUMAN-TEST.md").read_text(encoding="utf-8")
blocks = re.findall(r"\`\`\`(?:bash|sh|zsh)\n(.*?)\`\`\`", ht, flags=re.DOTALL)
claude_check = next(b for b in blocks if "PASS Claude install paths" in b)
cursor_check = next(b for b in blocks if "PASS Cursor install paths" in b)
src = pathlib.Path("$PLUGIN")
home = pathlib.Path(tempfile.mkdtemp(prefix="ai-audit-r12-layout."))
try:
    ver = home / ".claude" / "plugins" / "cache" / "scottgrayson-plugins" / "ai-audit" / "0.1.0"
    shutil.copytree(src, ver)
    script = "HOME=%r\nexport HOME\nunset AI_AUDIT PLUGIN\n%s\n" % (str(home), claude_check)
    proc = subprocess.run(["bash", "--norc", "-c", script], capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "PASS Claude install paths" in out and "/0.1.0" in out, out
    cver = home / ".cursor" / "plugins" / "cache" / "mp" / "ai-audit" / "1.2.3"
    shutil.copytree(src, cver)
    script_c = "HOME=%r\nexport HOME\nunset AI_AUDIT PLUGIN\n%s\n" % (str(home), cursor_check)
    proc_c = subprocess.run(["bash", "--norc", "-c", script_c], capture_output=True, text=True)
    out_c = (proc_c.stdout or "") + (proc_c.stderr or "")
    assert "PASS Cursor install paths" in out_c, out_c
finally:
    shutil.rmtree(home)
PY

echo "== redact_cases table =="
REDACT_OUT="$(python3 -B "$PLUGIN/tests/test_redact_cases.py" 2>&1)" && REDACT_RC=0 || REDACT_RC=$?
if [[ $REDACT_RC -eq 0 ]]; then
  echo "PASS $REDACT_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL redact_cases table (rc=$REDACT_RC)"
  echo "  $REDACT_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== cursor startup (o200k / User Rules / MCP) =="
CUR_OUT="$(python3 -B "$PLUGIN/tests/test_cursor_startup.py" 2>&1)" && CUR_RC=0 || CUR_RC=$?
if [[ $CUR_RC -eq 0 ]]; then
  echo "PASS $CUR_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL cursor_startup (rc=$CUR_RC)"
  echo "  $CUR_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== quality checks (deterministic, token-free) =="
QC_OUT="$(python3 -B "$PLUGIN/tests/test_quality_checks.py" 2>&1)" && QC_RC=0 || QC_RC=$?
if [[ $QC_RC -eq 0 ]]; then
  echo "PASS $QC_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL quality_checks (rc=$QC_RC)"
  echo "  $QC_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== environment heading + sort =="
ENV_OUT="$(python3 -B "$PLUGIN/tests/test_environment.py" 2>&1)" && ENV_RC=0 || ENV_RC=$?
if [[ $ENV_RC -eq 0 ]]; then
  echo "PASS $ENV_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL environment (rc=$ENV_RC)"
  echo "  $ENV_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-20 (references, MCP, plugin shapes, hooks, redaction) =="
R20_OUT="$(python3 -B "$PLUGIN/tests/test_round20.py" 2>&1)" && R20_RC=0 || R20_RC=$?
if [[ $R20_RC -eq 0 ]]; then
  echo "PASS $R20_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round20 (rc=$R20_RC)"
  echo "  $R20_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-21 (kebab redaction, ~ project dedupe, disabled plugin, prose refs) =="
R21_OUT="$(python3 -B "$PLUGIN/tests/test_round21.py" 2>&1)" && R21_RC=0 || R21_RC=$?
if [[ $R21_RC -eq 0 ]]; then
  echo "PASS $R21_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round21 (rc=$R21_RC)"
  echo "  $R21_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-25 (reference false positives, product names, tails, Editable) =="
R25_OUT="$(python3 -B "$PLUGIN/tests/test_round25.py" 2>&1)" && R25_RC=0 || R25_RC=$?
if [[ $R25_RC -eq 0 ]]; then
  echo "PASS $R25_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round25 (rc=$R25_RC)"
  echo "  $R25_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-27 (product keys, glued tails, absolute refs) =="
R27_OUT="$(python3 -B "$PLUGIN/tests/test_round27.py" 2>&1)" && R27_RC=0 || R27_RC=$?
if [[ $R27_RC -eq 0 ]]; then
  echo "PASS $R27_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round27 (rc=$R27_RC)"
  echo "  $R27_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-29 (product allowlist, example* segment limit) =="
R29_OUT="$(python3 -B "$PLUGIN/tests/test_round29.py" 2>&1)" && R29_RC=0 || R29_RC=$?
if [[ $R29_RC -eq 0 ]]; then
  echo "PASS $R29_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round29 (rc=$R29_RC)"
  echo "  $R29_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-30 (product parenthesis allowlist) =="
R30_OUT="$(python3 -B "$PLUGIN/tests/test_round30.py" 2>&1)" && R30_RC=0 || R30_RC=$?
if [[ $R30_RC -eq 0 ]]; then
  echo "PASS $R30_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round30 (rc=$R30_RC)"
  echo "  $R30_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-31 (product token only, password manager phrase) =="
R31_OUT="$(python3 -B "$PLUGIN/tests/test_round31.py" 2>&1)" && R31_RC=0 || R31_RC=$?
if [[ $R31_RC -eq 0 ]]; then
  echo "PASS $R31_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round31 (rc=$R31_RC)"
  echo "  $R31_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-32 (password-manager product name in a key) =="
R32_OUT="$(python3 -B "$PLUGIN/tests/test_round32.py" 2>&1)" && R32_RC=0 || R32_RC=$?
if [[ $R32_RC -eq 0 ]]; then
  echo "PASS $R32_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round32 (rc=$R32_RC)"
  echo "  $R32_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-34 (product dir in path fields, comma connective remainder) =="
R34_OUT="$(python3 -B "$PLUGIN/tests/test_round34.py" 2>&1)" && R34_RC=0 || R34_RC=$?
if [[ $R34_RC -eq 0 ]]; then
  echo "PASS $R34_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round34 (rc=$R34_RC)"
  echo "  $R34_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== round-35 (segment after product folder in path fields) =="
R35_OUT="$(python3 -B "$PLUGIN/tests/test_round35.py" 2>&1)" && R35_RC=0 || R35_RC=$?
if [[ $R35_RC -eq 0 ]]; then
  echo "PASS $R35_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL round35 (rc=$R35_RC)"
  echo "  $R35_OUT"
  FAIL=$((FAIL + 1))
fi

echo "== redaction corpus =="
CORPUS_OUT="$(python3 -B "$PLUGIN/tests/test_redaction_corpus.py" 2>&1)" && CORPUS_RC=0 || CORPUS_RC=$?
if [[ $CORPUS_RC -eq 0 ]]; then
  echo "PASS $CORPUS_OUT"
  PASS=$((PASS + 1))
else
  echo "FAIL redaction corpus (rc=$CORPUS_RC)"
  echo "  $CORPUS_OUT"
  FAIL=$((FAIL + 1))
fi

py_check "HUMAN-TEST secret check FAIL when AI_AUDIT unset" <<PY
import pathlib, re, subprocess
ht = pathlib.Path("$PLUGIN/HUMAN-TEST.md").read_text(encoding="utf-8")
blocks = re.findall(r"\`\`\`(?:bash|sh|zsh)\n(.*?)\`\`\`", ht, flags=re.DOTALL)
secret_block = next(b for b in blocks if "PASS secrets json redacted" in b)
assert "AI_AUDIT unset or missing" in secret_block
script = "unset AI_AUDIT PLUGIN\nHOME=/tmp\nexport HOME\n%s\n" % secret_block
proc = subprocess.run(["bash", "--norc", "-c", script], capture_output=True, text=True)
out = proc.stdout or ""
assert "FAIL AI_AUDIT unset or missing" in out, out
assert "PASS secrets" not in out, out
PY

echo "== public-repo clean =="
assert_contains "one skill present" "$(ls "$PLUGIN/skills")" "context-audit"
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
