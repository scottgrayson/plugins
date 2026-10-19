---
name: facts-audit
description: Check saved memory/facts for staleness, duplicates, conflicts, and orphaned files. Read-only; propose diffs only.
---

# facts-audit

Scan saved memory/facts for stale dates, duplicates, conflicts, and orphans. Output ranked cleanup wins. **Never edit memory without explicit approval** — show a diff/suggestion only.

## Locate the core

```bash
bash plugins/ai-audit/bin/ai-audit facts --help
# or: PYTHONPATH=plugins/ai-audit/lib python3 -m ai_audit facts --help
```

## Run

```bash
bash plugins/ai-audit/bin/ai-audit facts --host auto --stale-days 30
bash plugins/ai-audit/bin/ai-audit facts --host grok --stale-days 14
bash plugins/ai-audit/bin/ai-audit facts --host claude
bash plugins/ai-audit/bin/ai-audit facts --host cursor --memories path/to/exported-memories.mdc
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

- **Grok Bot:** monthly routine: “Run facts-audit; list stale/duplicate/orphan wins; do not edit profile.md.”
- **Cursor:** monthly automation that audits rules + any provided memories export.
- **Claude Code:** scheduled task: `ai-audit facts --host claude --stale-days 30` and summarize issues.
