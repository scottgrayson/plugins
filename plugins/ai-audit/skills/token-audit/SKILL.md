---
name: token-audit
description: Find where token cost comes from by bot/agent, routine/automation, model, and tool. Rank the biggest wins. Read-only.
---

# token-audit

Attribute token cost and rank 3–5 wins. **Priority: cloud usage first, local second.** Never invent savings numbers — measured wins only; otherwise ask/check items.

## Locate the CLI

Do **not** use a repo-relative `plugins/ai-audit/bin/ai-audit` path. Always invoke with `bash …/bin/ai-audit` (read-only; do not `chmod +x`).

1. **Claude Code:** use braced `${CLAUDE_PLUGIN_ROOT}` only — Claude substitutes it in skill *text*; it is **not** in the Bash tool environment. An unbraced dollar-var plus slash path becomes `/scripts/…` and fails.
2. **Cursor:** `${CURSOR_PLUGIN_ROOT}` when set; else local/cache/`SKILL_MD` relative.
3. **Grok Bot:** option B (`SKILL_MD` → `../../bin/ai-audit`).

```bash
# A) Claude Code — braced ${CLAUDE_PLUGIN_ROOT} only:
AI_AUDIT="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/locate-cli.sh")"

# B) Skill-file relative — set SKILL_MD to THIS file:
export SKILL_MD="/absolute/path/to/skills/token-audit/SKILL.md"
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"

# C) Explicit install root:
export AI_AUDIT_PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ai-audit"
AI_AUDIT="$(bash "${AI_AUDIT_PLUGIN_ROOT}/scripts/locate-cli.sh")"

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

Label estimates; never invent numbers. Prefer local runs for anything that needs disk or a CSV.

### Claude Code

- **`/loop` is session-scoped** (~7 day expiry). **Cloud Routines** (`/schedule`) cannot see local session JSONL or the plugin cache.
- **Preferred:** Claude Desktop → **Routines** → **New routine** → **Local**.
- Example local routine prompt: `Run /ai-audit:token-audit and reply with the top wins. Do not edit files.`
  (`${CLAUDE_PLUGIN_ROOT}` is **not** substituted in routine prompts — use the slash-skill form.)
- After creating the routine, click **Run now** once and choose **always allow** at the Bash permission prompt so scheduled runs do not stall in Manual permission mode.
- Cloud routines are only suitable if you intentionally limit the audit to repo-checked-in artifacts (unusual for token audit).

### Cursor

- Entry points: [cursor.com/automations](https://cursor.com/automations), the **Agents** window, or `/automate`. Do **not** invent a Customize-sidebar Automations menu.
- Automations are **cloud agents**: no local `~/.cursor`, no local plugin copy, no downloaded Usage CSV.
- **Do not schedule token-audit as a cloud automation** unless you opt into the Admin API (`--cursor-admin-api` + `CURSOR_ADMIN_API_KEY`). Otherwise run token-audit in a **local** agent session with a CSV path the machine can read.

### Grok Bot

- Ask the bot **in chat**: e.g. `Every Monday at 9am, run the ai-audit token audit and send me the top wins. Do not ask me for Usage numbers; report with saving unknown and a short note that Usage totals were not provided.`
- View routines via the bot name in the chat header → info pane → **Routines** list.
- A **scheduled** token audit must **not** prompt for Usage numbers; it reports with **saving unknown** and a note. Interactive runs may still ask for Usage / routines when a human is present.
