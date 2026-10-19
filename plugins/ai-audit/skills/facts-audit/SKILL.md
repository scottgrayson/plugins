---
name: facts-audit
description: Check saved memory/facts for staleness, duplicates, conflicts, and orphaned files. Read-only; propose diffs only.
---

# facts-audit

Scan saved memory/facts for stale dates, duplicates, conflicts, and orphans. Output ranked cleanup wins. **Never edit memory without explicit approval** — show a diff/suggestion only.

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

## Schedule as a routine

Memory drifts slowly — monthly is usually enough. Never edit memory without approval.

### Claude Code

- **`/loop`** is session-scoped (~7 day expiry). **Cloud Routines** cannot see local auto-memory under `~/.claude`.
- **Preferred:** Claude Desktop → **Routines** → **New routine** → **Local**, using braced `${CLAUDE_PLUGIN_ROOT}` and `bash …/bin/ai-audit facts --host claude --stale-days 30`.
- Cloud routines only fit **repo-only** facts (e.g. `AGENTS.md` / rules committed in the repo).

### Cursor

- Entry points: [cursor.com/automations](https://cursor.com/automations), the **Agents** window, or `/automate`. Do **not** invent a Customize-sidebar Automations menu.
- Cloud automations see the **attached repo**, not local `~/.cursor` or a memories export on disk.
- Schedule **repo-level** facts audits (rules / `AGENTS.md` in the repo). For exported memories `.mdc`, run facts-audit **locally** with `--memories`.

### Grok Bot

- Ask the bot **in chat**: e.g. `Run the ai-audit facts audit every Monday at 9am and send me the top wins. Do not edit profile.md.`
- View routines via the bot name in the chat header → info pane → **Routines** list.
