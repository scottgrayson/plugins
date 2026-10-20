---
name: facts-audit
description: Check saved memory/facts for staleness, duplicates, conflicts, and orphaned files. Read-only; propose diffs only.
disable-model-invocation: true
---

# facts-audit

Scan saved memory/facts for stale dates, duplicates, conflicts, and orphans. Each issue includes a reason. **Never edit memory without explicit approval** — show a diff/suggestion only.

## Locate the CLI

Do **not** use a repo-relative `plugins/ai-audit/bin/ai-audit` path. Always invoke with `bash …/bin/ai-audit` (read-only; do not `chmod +x`).

1. **Claude Code:** braced `${CLAUDE_PLUGIN_ROOT}` only (substituted in skill *text*; not in the Bash tool env). An unbraced dollar-var plus slash path fails.
2. **Cursor:** `${CURSOR_PLUGIN_ROOT}` / local / cache / `SKILL_MD` relative.
3. **Grok Bot:** option B (`SKILL_MD` → `../../bin/ai-audit`).

```bash
# A) Claude Code — braced ${CLAUDE_PLUGIN_ROOT} only:
AI_AUDIT="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/locate-cli.sh")"

# B) Skill-file relative — set SKILL_MD to THIS file:
export SKILL_MD="/absolute/path/to/skills/facts-audit/SKILL.md"
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"

# C) Explicit install root:
export AI_AUDIT_PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ai-audit"
AI_AUDIT="$(bash "${AI_AUDIT_PLUGIN_ROOT}/scripts/locate-cli.sh")"

bash "$AI_AUDIT" facts --help
```

## Run

```bash
bash "$AI_AUDIT" facts --host auto --stale-days 30
bash "$AI_AUDIT" facts --host grok --stale-days 14
bash "$AI_AUDIT" facts --host claude
bash "$AI_AUDIT" facts --host cursor --memories path/to/exported-memories.mdc
```

### Host notes

| Host | Source | Dates |
| --- | --- | --- |
| Grok Bot | `user-memory/by-agent/<id>/profile.md` lines `- (YYYY-MM-DD) fact` | Inline date (ignore mtime) |
| Claude Code | Auto-memory topic files (`modified` front matter) + CLAUDE.md/rules | Topic `modified`; index undated |
| Cursor | Server-side memories only | No dates unless export provided; also scan rules/AGENTS.md |

For Cursor: explain that memories are server-side; ask the user to run **Export memories** and pass `--memories` if they have an `.mdc`.

## Rules

- Read-only by default.
- Prefer newer dated facts when resolving conflicts.
- Do not open secret files or print tokens.
- v0.1.0 does not estimate savings or schedule runs.
