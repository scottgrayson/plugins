# ai-audit

Read-only audits of what loads into an agent's context, where token cost comes from, and whether saved facts are stale, duplicated, conflicting, or orphaned.

Three on-demand skills share one deterministic core (`bin/ai-audit`):

| Skill | What it answers |
| --- | --- |
| `context-audit` | What loads into context, how big it is, always vs on-demand, and the 3–5 biggest wins |
| `token-audit` | Where token cost comes from (by bot/agent, routine/automation, model, tool) |
| `facts-audit` | Stale, duplicate, conflicting, or orphaned memory/facts |

**Hard rules:** the core never changes files, never applies fixes (suggestions and diffs only), never reads or prints secret values, and never lifts login tokens from app databases.

Version: **0.1.0**. Full marketplace install guide: [root README](../../README.md).

---

## Install

### Claude Code

```text
/plugin marketplace add scottgrayson/plugins
/plugin install ai-audit@scottgrayson-plugins
```

### Cursor

Add this marketplace from the dashboard (Teams/Enterprise), then install **ai-audit** from Customize. For local smoke-testing, copy into `~/.cursor/plugins/local/ai-audit` and reload.

---

## CLI

From a checkout of this marketplace:

```bash
AI_AUDIT_BIN="$PWD/plugins/ai-audit/bin/ai-audit"
chmod +x "$AI_AUDIT_BIN"

"$AI_AUDIT_BIN" detect
"$AI_AUDIT_BIN" context --host auto
"$AI_AUDIT_BIN" token --host cursor --csv path/to/usage.csv
"$AI_AUDIT_BIN" facts --host grok --stale-days 30
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--host auto\|claude\|cursor\|grok` | Force host (default: auto-detect) |
| `--root PATH` | Override home / project / sand-data roots (tests and fixtures) |
| `--project PATH` | Project directory for Claude/Cursor collectors |
| `--csv PATH` | Cursor usage CSV from the dashboard |
| `--memories PATH` | Cursor exported memories `.mdc` |
| `--stale-days N` | Facts older than N days are stale (default 30) |
| `--json-only` / `--md-only` | Emit only one format (default: both) |
| `--out-dir PATH` | Write `report.json` and `report.md` here |

---

## What each host can measure

See the research notes that shipped with this work for path details. Short version:

- **Claude Code:** file-based context inventory; local session JSONL for tokens; auto-memory topic `modified` dates for facts.
- **Cursor:** file-based context inventory; usage CSV (and optional Admin API via env) for tokens; memories only if an exported `.mdc` is provided.
- **Grok Bot:** workflows / agent profiles / `profile.md` memory on disk; token usage estimated from message volume × routine schedule (labeled as estimate).

---

## Scheduling as a routine

Each skill's `SKILL.md` has a short "Schedule" section covering a Grok Bot routine, a Cursor automation, and a Claude Code scheduled task. The core stays read-only either way.
