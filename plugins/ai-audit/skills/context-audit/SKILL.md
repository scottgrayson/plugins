---
name: context-audit
description: Inventory everything that loads into an agent's context and rank the 3–5 biggest size wins. Read-only; never applies fixes.
---

# context-audit

Read-only inventory of instructions, rules, skills, subagents, MCP/connectors, plugins, routines/automations, bot descriptions, and memory. Rank the 3–5 biggest wins by estimated tokens saved (chars ÷ 4).

## Locate the core

```bash
# From a marketplace checkout:
bash plugins/ai-audit/bin/ai-audit context --help
# or: PYTHONPATH=plugins/ai-audit/lib python3 -m ai_audit context --help
```

## Run

```bash
bash plugins/ai-audit/bin/ai-audit context --host auto
bash plugins/ai-audit/bin/ai-audit context --host claude --root path/to/fixture
bash plugins/ai-audit/bin/ai-audit context --host cursor --project .
bash plugins/ai-audit/bin/ai-audit context --host grok
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

- **Grok Bot:** create a weekly routine: “Run context-audit and reply with the top 5 wins only.”
- **Cursor:** add an automation that invokes this skill on a weekly schedule in a clean chat.
- **Claude Code:** add a scheduled task / `/loop` prompt: `ai-audit context --host claude` and summarize wins.
