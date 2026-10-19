---
name: token-audit
description: Find where token cost comes from by bot/agent, routine/automation, model, and tool. Rank the biggest wins. Read-only.
---

# token-audit

Attribute token cost and rank 3–5 wins. **Priority: cloud usage first, local second.** Never invent savings numbers — measured wins only; otherwise ask/check items.

## Locate the core

From a marketplace checkout:

```bash
python3 -m ai_audit token --help
# or:
bash plugins/ai-audit/bin/ai-audit token --help
```

Set `PYTHONPATH=plugins/ai-audit/lib` when invoking `python3 -m ai_audit`.

## Run by host

### Cursor (cloud CSV first)

Ask the user to download the Usage CSV from the Cursor dashboard, then:

```bash
bash plugins/ai-audit/bin/ai-audit token --host cursor --csv ~/Downloads/usage.csv
```

Optional Admin API (opt-in only): `--cursor-admin-api` **and** `CURSOR_ADMIN_API_KEY`. Uses Basic auth, supports `--admin-start` / `--admin-end`, paginates. Never scrape `state.vscdb`.

### Grok Bot (estimate)

Ask the user for Usage & Billing numbers, and list the bot's own routines. Then:

```bash
bash plugins/ai-audit/bin/ai-audit token --host grok \
  --usage-total 72 --usage-period "week of 2026-10-13" \
  --routines-json /tmp/routines.json
```

`routines.json` shape: `[{"name":"hourly","cron":"0 * * * *"}]`. Without these, the report asks you to collect them (no invented token number).

### Claude Code

```bash
bash plugins/ai-audit/bin/ai-audit token --host claude
```

Parses local JSONL (deduped by message.id/requestId). Tool cost is attributed to the request that carries the tool_result.

## Schedule as a routine

- **Grok Bot:** weekly: “Run token-audit; ask for Usage totals and list routines; label estimates.”
- **Cursor:** weekly after a fresh Usage CSV export.
- **Claude Code:** scheduled task running `ai_audit token --host claude`.
