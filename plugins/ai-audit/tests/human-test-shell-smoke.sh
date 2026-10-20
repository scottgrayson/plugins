#!/usr/bin/env bash
# Smoke-test HUMAN-TEST.md shell patterns under zsh (Mac default) when available,
# else bash with nomatch-equivalent settings. Offline / token-free.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../../.." && pwd)"
PLUGIN="$ROOT/plugins/ai-audit"
HT="$PLUGIN/HUMAN-TEST.md"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ai-audit-ht-smoke.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

fail() { echo "FAIL human-test-shell-smoke: $*"; exit 1; }
pass() { echo "PASS human-test-shell-smoke: $*"; }

# Doc contract checks (same under any shell)
grep -q "scottgrayson/plugins#cursor/ai-audit-plugin-f41a" "$HT" \
  || fail "missing branch marketplace form"
grep -q "Claude Code block OR the Cursor block" "$HT" \
  || fail "missing Claude OR Cursor guidance"
grep -q "into a real project directory" "$HT" \
  || fail "missing cd-into-project guidance"
grep -q "does not exist" "$HT" || fail "missing does not exist grep target"
grep -q "null_glob" "$HT" || fail "missing zsh null_glob CSV guidance"
grep -q "PASS secrets json redacted" "$HT" || fail "missing visible PASS"
grep -q "password=Hunter2FakePass" "$HT" || fail "missing password= plant"
grep -q '/path/to/plugins/ai-audit' "$HT" \
  || fail "Cursor install must be path-independent (/path/to/plugins/ai-audit)"
if grep -nE 'exit 1' "$HT" >/dev/null; then
  fail "HUMAN-TEST must not use exit 1 (closes interactive terminals)"
fi
# No pwd-relative Cursor install
if grep -nE 'cp -R "\$\(pwd\)/plugins/ai-audit"' "$HT" >/dev/null; then
  fail "Cursor install still uses \$(pwd)/plugins/ai-audit"
fi

# Extract fenced bash/sh/zsh code blocks; assert no shell '#' comments inside;
# syntax-check each with zsh -f -n when zsh is available.
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
    """True if an unquoted # would start a shell comment (stock zsh without interactivecomments)."""
    if not line.strip():
        return False
    # Remove single- and double-quoted spans so '#' inside marketplace URLs is fine.
    cleaned = re.sub(r"'[^']*'", "''", line)
    cleaned = re.sub(r'"[^"]*"', '""', cleaned)
    # Drop ${#…} and $# so length/positional forms are not flagged.
    cleaned = re.sub(r"\$\{#", "", cleaned)
    cleaned = re.sub(r"\$#", "", cleaned)
    return "#" in cleaned

bad_comments = []
for bi, block in enumerate(blocks):
    for li, line in enumerate(block.splitlines(), 1):
        if line_has_shell_comment(line):
            bad_comments.append(f"block {bi+1} line {li}: {line!r}")

if bad_comments:
    print("inline/full-line '#' comments inside code blocks:", file=sys.stderr)
    for b in bad_comments[:20]:
        print(" ", b, file=sys.stderr)
    sys.exit(1)

# Syntax-check with zsh -f -n when available (CI installs zsh).
import shutil
zsh = shutil.which("zsh")
if zsh:
    for bi, block in enumerate(blocks):
        path = tmp / f"block-{bi+1}.zsh"
        path.write_text(block, encoding="utf-8")
        proc = subprocess.run(
            [zsh, "-f", "-n", "--", str(path)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"zsh -f -n failed for block {bi+1}:", file=sys.stderr)
            print(proc.stderr or proc.stdout, file=sys.stderr)
            sys.exit(1)
    print(f"zsh -f -n ok for {len(blocks)} blocks")
else:
    print("zsh not installed; skipped -n syntax check", file=sys.stderr)
    sys.exit(2)

print(f"no shell '#' comments in {len(blocks)} code blocks")
PY
ht_rc=$?
set -e
if [ "$ht_rc" -eq 2 ]; then
  fail "zsh required for HUMAN-TEST code-block syntax check (install zsh)"
elif [ "$ht_rc" -ne 0 ]; then
  fail "HUMAN-TEST code-block check failed"
fi
pass "HUMAN-TEST code blocks: no '#' comments + zsh -f -n"

# CSV glob: zsh must not abort when one pattern has no match
DOWNLOADS="$TMP/Downloads"
mkdir -p "$DOWNLOADS"
# Only the second-style name exists (simulates real Usage export naming)
printf 'x\n' >"$DOWNLOADS/Cursor_usage_2026-10-01.csv"

run_csv_glob() {
  # shellcheck disable=SC1091
  if [ -n "${ZSH_VERSION:-}" ]; then
    setopt null_glob
  elif [ -n "${BASH_VERSION:-}" ]; then
    # bash equivalent of not aborting on no-match
    shopt -s nullglob
  fi
  CSV=""
  # shellcheck disable=SC2086
  for candidate in "$DOWNLOADS"/*[Uu]sage*.csv "$DOWNLOADS"/usage*.csv "$DOWNLOADS"/no_such_pattern*.csv; do
    [ -f "$candidate" ] || continue
    CSV="$candidate"
    break
  done
  if [ -z "$CSV" ]; then
    CSV="$(find "$DOWNLOADS" -maxdepth 1 -iname '*usage*.csv' -type f 2>/dev/null | sort | tail -1)"
  fi
  [ -n "$CSV" ] && [ -f "$CSV" ] || return 1
  echo "$CSV"
}

if command -v zsh >/dev/null 2>&1 && [ -z "${AI_AUDIT_FORCE_BASH_SMOKE:-}" ]; then
  CSV="$(zsh -c '
    setopt null_glob
    DOWNLOADS="$1"
    CSV=""
    for candidate in "$DOWNLOADS"/*[Uu]sage*.csv "$DOWNLOADS"/usage*.csv "$DOWNLOADS"/no_such_pattern*.csv; do
      [ -f "$candidate" ] || continue
      CSV="$candidate"
      break
    done
    if [ -z "$CSV" ]; then
      CSV="$(find "$DOWNLOADS" -maxdepth 1 -iname "*usage*.csv" -type f 2>/dev/null | sort | tail -1)"
    fi
    print -r -- "$CSV"
  ' zsh "$DOWNLOADS")" || fail "zsh CSV glob aborted"
  [ -f "$CSV" ] || fail "zsh CSV glob empty (got: ${CSV:-})"
  pass "zsh CSV glob → $CSV"
else
  # bash with nullglob (nomatch-equivalent: unmatched globs expand to nothing)
  CSV="$(run_csv_glob)" || fail "bash CSV glob failed"
  pass "bash CSV glob → $CSV"
fi

# Visible PASS/FAIL pattern from step 4 (message text)
fake_out="$TMP/bad-csv.txt"
printf '%s\n' "CSV path does not exist: /nonexistent/usage.csv" >"$fake_out"
if grep -qi 'does not exist' "$fake_out"; then
  pass "does not exist grep matches tool message"
else
  fail "does not exist grep missed"
fi

echo "human-test-shell-smoke ok"
