#!/usr/bin/env bash
# Print the path to bin/ai-audit, trying install layouts in order.
# Env (optional):
#   CLAUDE_PLUGIN_ROOT  — Claude Code plugin root (substituted in plugin skills)
#   CURSOR_PLUGIN_ROOT  — Cursor plugin root when set
#   SKILL_MD            — absolute path to a skill's SKILL.md (plugin/skills/<name>/SKILL.md)
#   AI_AUDIT_PLUGIN_ROOT — explicit plugin root override
# Exits 1 with a clear message if nothing is found.
set -euo pipefail

candidates=()

add() {
  local p="${1:-}"
  [[ -n "$p" ]] || return 0
  candidates+=("$p")
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
# prefer that sibling bin/ — but only when the caller did not ask for a
# "nothing configured" failure (AI_AUDIT_LOCATE_STRICT=1).
if [[ "${AI_AUDIT_LOCATE_STRICT:-}" != "1" ]]; then
  self_plugin="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  add "$self_plugin/bin/ai-audit"
fi

# Common local installs
add "${HOME:-}/.cursor/plugins/local/ai-audit/bin/ai-audit"
add "${HOME:-}/.claude/plugins/ai-audit/bin/ai-audit"
add "${HOME:-}/.config/claude/plugins/ai-audit/bin/ai-audit"

# Cursor versioned / cache installs (best-effort; first match wins)
while IFS= read -r hit; do
  add "$hit"
done < <(
  ls -d \
    "${HOME:-}"/.cursor/plugins/*/ai-audit/bin/ai-audit \
    "${HOME:-}"/.cursor/plugins/cache/*/ai-audit/bin/ai-audit \
    "${HOME:-}"/.cursor/plugins/cache/*/*/ai-audit/bin/ai-audit \
    2>/dev/null || true
)

for c in "${candidates[@]}"; do
  if [[ -f "$c" ]]; then
    chmod +x "$c" 2>/dev/null || true
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
  ~/.claude/plugins/ai-audit/bin/ai-audit
  ~/.cursor/plugins/**/ai-audit/bin/ai-audit (cache)

Fix: install the full ai-audit plugin (bin/ + lib/ + skills/), set
CLAUDE_PLUGIN_ROOT or CURSOR_PLUGIN_ROOT, or export SKILL_MD to this
skill's SKILL.md path so the plugin root can be derived.
EOF
exit 1
