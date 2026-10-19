---
name: context-audit
description: Inventory everything that loads into an agent's context and rank the 3–5 biggest size wins. Read-only; never applies fixes.
---

# context-audit

Read-only inventory of instructions, rules, skills, subagents, MCP/connectors, plugins, routines/automations, bot descriptions, and memory. Rank the 3–5 biggest wins by estimated tokens saved (chars ÷ 4).

## Locate the CLI

Do **not** use a repo-relative `plugins/ai-audit/bin/ai-audit` path — that only works inside this marketplace checkout. Resolve the installed plugin copy:

1. **Claude Code:** prefer `"${CLAUDE_PLUGIN_ROOT}/bin/ai-audit"` (Claude substitutes `CLAUDE_PLUGIN_ROOT` in plugin skills).
2. **Cursor:** `${CURSOR_PLUGIN_ROOT}` is documented for `mcp.json`; if unset, try `~/.cursor/plugins/local/ai-audit`, then the plugin cache under `~/.cursor/plugins/`, or derive the plugin root from this skill file (`skills/context-audit/SKILL.md` → `../..`).
3. **Grok Bot:** skills ship as folders under the plugin; resolve relative to this skill file: `../../bin/ai-audit`.

Run the locator (fails clearly if nothing matches). Pick one bootstrap:

```bash
# A) Claude Code — CLAUDE_PLUGIN_ROOT is substituted for plugin skills:
AI_AUDIT="$(bash "$CLAUDE_PLUGIN_ROOT/scripts/locate-cli.sh")"

# B) Skill-file relative (Cursor / Grok / any host) — set SKILL_MD to THIS file:
export SKILL_MD="/absolute/path/to/skills/context-audit/SKILL.md"
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"

# C) Explicit install root (e.g. local Cursor plugin):
export AI_AUDIT_PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ai-audit"
AI_AUDIT="$(bash "$AI_AUDIT_PLUGIN_ROOT/scripts/locate-cli.sh")"

bash "$AI_AUDIT" context --help
```

Once located, you can also call `"$AI_AUDIT"` directly (the `bin/ai-audit` wrapper sets `PYTHONPATH`).

## Run

```bash
bash "$AI_AUDIT" context --host auto
bash "$AI_AUDIT" context --host claude --root path/to/fixture
bash "$AI_AUDIT" context --host cursor --project .
bash "$AI_AUDIT" context --host grok
```

Show the markdown report to the user. Proposed fixes are **suggestions only** — present as a diff; never edit files.

## When to ask the user

| Gap | Ask for |
| --- | --- |
| Cursor user / team rules | Paste from Customize → Rules (not on disk) |
| Grok routines / connectors | Bot can list routines and fetch connector schemas; or open the info pane |
| Claude live breakdown | Paste `/context` output to calibrate |

Never read secret files (`host-secrets.json`, `gateway.json`, MCP `env`/`headers` values, `~/.ai/secrets/`). Never lift login tokens from app databases.

## Schedule as a routine

Keep the audit on a cadence so context bloat is caught early. The core stays read-only.

### Claude Code

1. Open a scheduled task / cron-style reminder (or `/loop` on an interval the user approves).
2. Prompt: `Run the context-audit skill. Locate the CLI via CLAUDE_PLUGIN_ROOT, run ai-audit context --host claude, and reply with the top 5 wins only. Do not edit files.`
3. Prefer a weekly schedule (e.g. Monday morning).

### Cursor

1. Create a **Cloud Agent / Automation** (Customize → Automations, or the Automations UI).
2. Schedule: weekly cron (e.g. `0 9 * * 1`).
3. Prompt: `Invoke the context-audit skill in a clean chat. Resolve the plugin CLI (local install or cache), run context --host cursor, summarize the top 5 wins. Suggestions only — do not apply edits.`
4. Attach any required repo; do not rely on a marketplace checkout path.

### Grok Bot

1. In the bot settings, create a **Routine** with a weekly cron.
2. Message: `Run context-audit. Resolve ../../bin/ai-audit from this skill folder (or use locate-cli.sh). Reply with the top 5 wins only. Do not edit files.`
3. Confirm the routine appears under the bot's automations list.
