#!/usr/bin/env bash
# Smoke-test HUMAN-TEST.md shell patterns under zsh when available.
# Offline / token-free. Stage 1: context-only doc contract.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../../.." && pwd)"
PLUGIN="$ROOT/plugins/ai-audit"
HT="$PLUGIN/HUMAN-TEST.md"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-ht-smoke.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

fail() { echo "FAIL human-test-shell-smoke: $*"; exit 1; }
pass() { echo "PASS human-test-shell-smoke: $*"; }

grep -q "scottgrayson/plugins#cursor/ai-audit-plugin-f41a" "$HT" \
  || fail "missing branch marketplace form"
grep -q "Claude Code block OR the Cursor block" "$HT" \
  || fail "missing Claude OR Cursor guidance"
grep -q "into a real project directory" "$HT" \
  || fail "missing cd-into-project guidance"
grep -q "PASS secrets json redacted" "$HT" || fail "missing visible PASS"
grep -q "password=Hunter2FakePass" "$HT" || fail "missing password= plant"
grep -q "context --host" "$HT" || fail "missing context command"
grep -q '/path/to/plugins/ai-audit' "$HT" \
  || fail "Cursor install must be path-independent"
grep -q 'rm -rf ~/.cursor/plugins/local/ai-audit' "$HT" \
  || fail "Cursor local copy must rm -rf before cp"
grep -q '\[ -d "\$SRC" \]' "$HT" \
  || fail "Cursor copy must check source exists before rm -rf"
grep -q 'ai-audit/*/bin/ai-audit' "$HT" \
  || fail "find must target */ai-audit/*/bin/ai-audit"
grep -q '.cursor/plugins/cache' "$HT" \
  || fail "must mention Cursor marketplace/cache path"
grep -q 'FAIL Claude install paths' "$HT" \
  || fail "Claude install check must echo FAIL when missing"
grep -q 'FAIL Cursor install paths' "$HT" \
  || fail "Cursor install check must echo FAIL when missing"
grep -q 'FAIL AI_AUDIT unset or missing' "$HT" \
  || fail "must guard checks when AI_AUDIT unset"
grep -q 'context-audit' "$HT" || fail "must mention context-audit skill"
if grep -nE 'token-audit|facts-audit|token --host|facts --host' "$HT" >/dev/null; then
  fail "HUMAN-TEST must not reference token/facts stage commands"
fi
if grep -nE 'exit 1' "$HT" >/dev/null; then
  fail "HUMAN-TEST must not use exit 1"
fi

set +e
python3 -B - "$HT" "$TMP" <<'PY'
import pathlib, re, subprocess, sys

ht = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(sys.argv[2])
text = ht.read_text(encoding="utf-8")
blocks = re.findall(r"```(?:bash|sh|zsh)\n(.*?)```", text, flags=re.DOTALL)
if not blocks:
    print("no bash/sh/zsh code blocks", file=sys.stderr)
    sys.exit(1)

def line_has_shell_comment(line: str) -> bool:
    if not line.strip():
        return False
    cleaned = re.sub(r"'[^']*'", "''", line)
    cleaned = re.sub(r'"[^"]*"', '""', cleaned)
    cleaned = re.sub(r"\$\{#", "", cleaned)
    cleaned = re.sub(r"\$#", "", cleaned)
    return "#" in cleaned

bad_comments = []
for bi, block in enumerate(blocks):
    for li, line in enumerate(block.splitlines(), 1):
        if line_has_shell_comment(line):
            bad_comments.append((bi, li, line))
if bad_comments:
    for bi, li, line in bad_comments[:8]:
        print(f"block {bi} line {li}: {line}", file=sys.stderr)
    sys.exit(1)

zsh = subprocess.run(["zsh", "-f", "-c", "echo ok"], capture_output=True, text=True)
use_zsh = zsh.returncode == 0 and (zsh.stdout or "").strip() == "ok"
for bi, block in enumerate(blocks):
    path = tmp / f"block-{bi}.sh"
    path.write_text(block, encoding="utf-8")
    if use_zsh:
        proc = subprocess.run(["zsh", "-f", "-n", str(path)], capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"zsh -f -n failed block {bi}: {proc.stderr}", file=sys.stderr)
            sys.exit(1)
print(f"zsh -f -n ok for {len(blocks)} blocks" if use_zsh else f"bash-only: {len(blocks)} blocks")
print(f"no shell '#' comments in {len(blocks)} code blocks")
PY
rc=$?
set -e
[[ $rc -eq 0 ]] || fail "code-block syntax/comments"
pass "HUMAN-TEST code blocks: no '#' comments + syntax check"

echo "human-test-shell-smoke ok"
