---
name: token-audit
description: Find where token cost comes from by bot/agent, routine/automation, model, and tool. Rank the biggest wins. Read-only.
---

# token-audit

Attribute token cost and rank 3–5 wins (run less often, cheaper model, trim context). **Priority: cloud usage first, local second.**

## Locate the core

```bash
AI_AUDIT_BIN="$PWD/plugins/ai-audit/bin/ai-audit"
chmod +x "$AI_AUDIT_BIN"
```

## Run by host

### Cursor (cloud CSV first)

Ask the user to download the Usage CSV from the Cursor dashboard (covers cloud agents and automations; there is no local token data). Then:

```bash
"$AI_AUDIT_BIN" token --host cursor --csv ~/Downloads/usage.csv --no-admin-api
```

Optional team Admin API: if `CURSOR_ADMIN_API_KEY` is set, the core may call `filtered-usage-events`. **Never require it.** Never scrape session tokens from `state.vscdb`.

### Grok Bot (estimate)

Usage page shows totals only. The core estimates per-bot cost from message volume × routine schedule and **labels it as an estimate**:

```bash
"$AI_AUDIT_BIN" token --host grok
```

Ask for weekly Usage % / on-demand numbers and for the bot to list routine schedules.

### Claude Code (local JSONL + optional cloud)

```bash
"$AI_AUDIT_BIN" token --host claude
```

Parses `~/.claude/projects/**/*.jsonl` usage fields. Ask the user to paste `/usage` or Console/web figures when they want cloud attribution.

## Rules

- Read-only; suggestions only.
- Never print API keys or secret config values.
- Rank by measured tokens (or clearly labeled estimates).

## Schedule as a routine

- **Grok Bot:** weekly routine: “Run token-audit; lead with routine-frequency wins; label estimates.”
- **Cursor:** weekly automation after the user drops a fresh Usage CSV into a known path, or remind them to export first.
- **Claude Code:** scheduled task running `ai-audit token --host claude` and summarizing top models/tools.
