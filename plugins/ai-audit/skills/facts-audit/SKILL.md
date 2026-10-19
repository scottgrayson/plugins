---
name: facts-audit
description: Check saved memory/facts for staleness, duplicates, conflicts, and orphaned files. Read-only; propose diffs only.
---

# facts-audit

Scan saved memory/facts for stale dates, duplicates, conflicts, and orphans. Output ranked cleanup wins. **Never edit memory without explicit approval** — show a diff/suggestion only.

## Locate the CLI

Do **not** use a repo-relative `plugins/ai-audit/bin/ai-audit` path — that only works inside this marketplace checkout. Resolve the installed plugin copy:

1. **Claude Code:** `"${CLAUDE_PLUGIN_ROOT}/bin/ai-audit"`.
2. **Cursor:** `${CURSOR_PLUGIN_ROOT}` when set; else `~/.cursor/plugins/local/ai-audit`, the plugin cache, or `../..` from this skill file.
3. **Grok Bot:** `../../bin/ai-audit` relative to this skill folder.

```bash
# A) Claude Code:
AI_AUDIT="$(bash "$CLAUDE_PLUGIN_ROOT/scripts/locate-cli.sh")"

# B) Skill-file relative — set SKILL_MD to THIS file:
export SKILL_MD="/absolute/path/to/skills/facts-audit/SKILL.md"
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"

# C) Explicit install root:
export AI_AUDIT_PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ai-audit"
AI_AUDIT="$(bash "$AI_AUDIT_PLUGIN_ROOT/scripts/locate-cli.sh")"

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

## Schedule as a routine

Memory drifts slowly — monthly is usually enough. Never edit memory without approval.

### Claude Code

1. Scheduled task or `/loop` monthly.
2. Prompt: `Run the facts-audit skill via CLAUDE_PLUGIN_ROOT. Execute ai-audit facts --host claude --stale-days 30. List stale/duplicate/conflict/orphan wins. Propose diffs only — do not edit memory.`

### Cursor

1. Create a monthly **Automation**.
2. Prompt: `Invoke facts-audit. Resolve the plugin CLI, scan rules/AGENTS.md, and if a memories export path is provided run with --memories. Summarize cleanup wins; do not edit files.`
3. Optionally remind the user to **Export memories** before the run.

### Grok Bot

1. Create a monthly **Routine**.
2. Message: `Run facts-audit. Resolve ../../bin/ai-audit from this skill folder. List stale/duplicate/orphan wins. Do not edit profile.md.`
3. Confirm the routine is enabled on the active bot.
