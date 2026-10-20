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
