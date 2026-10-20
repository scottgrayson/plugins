#!/usr/bin/env bash
# Print the path to bin/ai-audit, trying install layouts in order.
# Env (optional):
#   CLAUDE_PLUGIN_ROOT   — Claude Code plugin root (substituted in skill *text* only;
#                          not exported to the Bash tool environment)
#   CURSOR_PLUGIN_ROOT   — Cursor plugin root when set
#   SKILL_MD             — absolute path to a skill's SKILL.md (…/skills/<name>/SKILL.md)
#   AI_AUDIT_PLUGIN_ROOT — explicit plugin root override
#   AI_AUDIT_LOCATE_STRICT=1 — skip script-relative sibling (for tests)
# Exits 1 with a clear message if nothing is found.
# Fully read-only: never chmod. Invoke the printed path with `bash …/bin/ai-audit`.
set -euo pipefail

candidates=()

add() {
  local p="${1:-}"
  [[ -n "$p" ]] || return 0
  candidates+=("$p")
}

add_newest_first() {
  # Print paths newest-first by mtime (GNU and BSD stat).
  local f
  local -a files=()
  while IFS= read -r f; do
    [[ -n "$f" && -f "$f" ]] && files+=("$f")
  done
  if [[ ${#files[@]} -eq 0 ]]; then
    return 0
  fi
  # Sort by mtime descending using ls -t when available on the parent list.
  local sorted
  sorted="$(ls -t "${files[@]}" 2>/dev/null || printf '%s\n' "${files[@]}")"
  while IFS= read -r f; do
    add "$f"
  done <<<"$sorted"
}

add "${AI_AUDIT_PLUGIN_ROOT:+$AI_AUDIT_PLUGIN_ROOT/bin/ai-audit}"
add "${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/bin/ai-audit}"
add "${CURSOR_PLUGIN_ROOT:+$CURSOR_PLUGIN_ROOT/bin/ai-audit}"

if [[ -n "${SKILL_MD:-}" && -f "$SKILL_MD" ]]; then
  # skills/<name>/SKILL.md → plugin root is ../..
  skill_plugin="$(cd "$(dirname "$SKILL_MD")/../.." && pwd)"
  add "$skill_plugin/bin/ai-audit"
fi

# If this locate script itself lives in an install (…/scripts/locate-cli.sh),
# prefer that sibling bin/ — unless strict mode (tests).
if [[ "${AI_AUDIT_LOCATE_STRICT:-}" != "1" ]]; then
  self_plugin="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  add "$self_plugin/bin/ai-audit"
fi

# Cursor local install
add "${HOME:-}/.cursor/plugins/local/ai-audit/bin/ai-audit"

# Claude Code real layout: ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/
# Prefer newest version by mtime.
add_newest_first < <(
  ls -d \
    "${HOME:-}"/.claude/plugins/cache/*/*/ai-audit/*/bin/ai-audit \
    "${HOME:-}"/.claude/plugins/cache/*/ai-audit/*/bin/ai-audit \
    2>/dev/null || true
)

# Cursor versioned / cache installs (newest first)
add_newest_first < <(
  ls -d \
    "${HOME:-}"/.cursor/plugins/*/ai-audit/bin/ai-audit \
    "${HOME:-}"/.cursor/plugins/cache/*/ai-audit/bin/ai-audit \
    "${HOME:-}"/.cursor/plugins/cache/*/*/ai-audit/bin/ai-audit \
    "${HOME:-}"/.cursor/plugins/cache/*/*/ai-audit/*/bin/ai-audit \
    2>/dev/null || true
)

for c in "${candidates[@]}"; do
  if [[ -f "$c" ]]; then
    # Prefer realpath when available so callers get a stable absolute path
    if command -v realpath >/dev/null 2>&1; then
      realpath "$c"
    else
      (cd "$(dirname "$c")" && echo "$(pwd)/$(basename "$c")")
    fi
    exit 0
  fi
done

cat >&2 <<'EOF'
ai-audit CLI not found.

Tried, in order:
  $AI_AUDIT_PLUGIN_ROOT/bin/ai-audit
  $CLAUDE_PLUGIN_ROOT/bin/ai-audit
  $CURSOR_PLUGIN_ROOT/bin/ai-audit
  $(dirname $SKILL_MD)/../../bin/ai-audit
  ~/.cursor/plugins/local/ai-audit/bin/ai-audit
  ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/bin/ai-audit (newest first)
  ~/.cursor/plugins/**/ai-audit/bin/ai-audit (cache)

Fix: install the full ai-audit plugin (bin/ + lib/ + skills/), set
SKILL_MD to this skill's SKILL.md (option B), or set AI_AUDIT_PLUGIN_ROOT /
CLAUDE_PLUGIN_ROOT / CURSOR_PLUGIN_ROOT. Invoke with:
  bash "$AI_AUDIT" <command>
(do not chmod; the locator is read-only).
EOF
exit 1
