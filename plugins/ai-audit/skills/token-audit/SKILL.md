---
name: token-audit
description: Find where token cost comes from by bot/agent, routine/automation, model, and tool. Rank the biggest wins. Read-only.
---

# token-audit

Attribute token cost and rank 3–5 wins. **Priority: cloud usage first, local second.** Never invent savings numbers — measured wins only; otherwise ask/check items.

## Locate the CLI

Do **not** use a repo-relative `plugins/ai-audit/bin/ai-audit` path — that only works inside this marketplace checkout. Resolve the installed plugin copy:

1. **Claude Code:** `"${CLAUDE_PLUGIN_ROOT}/bin/ai-audit"`.
2. **Cursor:** `${CURSOR_PLUGIN_ROOT}` when set; else `~/.cursor/plugins/local/ai-audit`, the plugin cache, or `../..` from this skill file.
3. **Grok Bot:** `../../bin/ai-audit` relative to this skill folder.

```bash
# A) Claude Code:
AI_AUDIT="$(bash "$CLAUDE_PLUGIN_ROOT/scripts/locate-cli.sh")"

# B) Skill-file relative — set SKILL_MD to THIS file:
export SKILL_MD="/absolute/path/to/skills/token-audit/SKILL.md"
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"

# C) Explicit install root:
export AI_AUDIT_PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ai-audit"
AI_AUDIT="$(bash "$AI_AUDIT_PLUGIN_ROOT/scripts/locate-cli.sh")"

bash "$AI_AUDIT" token --help
```

## Run by host

### Cursor (cloud CSV first)

Ask the user to download the Usage CSV from the Cursor dashboard, then:

```bash
bash "$AI_AUDIT" token --host cursor --csv ~/Downloads/usage.csv
```

Optional Admin API (opt-in only): `--cursor-admin-api` **and** `CURSOR_ADMIN_API_KEY`. Uses Basic auth, supports `--admin-start` / `--admin-end`, paginates. Never scrape `state.vscdb`.

### Grok Bot (estimate)

Ask the user for Usage & Billing numbers, and list the bot's own routines. Then:

```bash
bash "$AI_AUDIT" token --host grok \
  --usage-total 72 --usage-period "week of 2026-10-13" \
  --routines-json /tmp/routines.json
```

`routines.json` shape: `[{"name":"hourly","cron":"0 * * * *"}]`. Without these, the report asks you to collect them (no invented token number).

### Claude Code

```bash
bash "$AI_AUDIT" token --host claude
```

Parses local JSONL (deduped by message.id/requestId). Tool cost is attributed to the request that carries the tool_result.

## Schedule as a routine

Keep spend visible on a cadence. Label estimates; never invent numbers.

### Claude Code

1. Scheduled task or `/loop` (interval the user approves).
2. Prompt: `Run the token-audit skill via CLAUDE_PLUGIN_ROOT. Execute ai-audit token --host claude and summarize measured wins only.`
3. Weekly is enough for most teams.

### Cursor

1. Create a weekly **Automation** / Cloud Agent.
2. Before each run, ask the user for a fresh Usage CSV (or store a known download path).
3. Prompt: `Invoke token-audit. Resolve the plugin CLI, run token --host cursor --csv <path>, report the top wins with methods. Do not invent savings.`
4. Optional: only when the user opts in, pass `--cursor-admin-api` with `CURSOR_ADMIN_API_KEY`.

### Grok Bot

1. Create a weekly **Routine**.
2. Message: `Run token-audit. Ask me for Usage & Billing totals and list your routines; write routines.json; run ai-audit token --host grok with --usage-total / --routines-json. Label estimates.`
3. Confirm the routine schedule under the bot's automations.
