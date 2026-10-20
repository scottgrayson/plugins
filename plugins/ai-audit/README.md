# ai-audit

Read-only audits of what loads into an agent's context, where token usage comes from, and whether saved facts are stale, duplicated, conflicting, or orphaned.

Three on-demand skills share one deterministic core (`bin/ai-audit`):

| Skill | What it answers |
| --- | --- |
| `context-audit` | What loads into context, how big it is, always vs on-demand |
| `token-audit` | Where token usage comes from (official logs/API or a usage file) |
| `facts-audit` | Stale, duplicate, conflicting, or orphaned memory/facts (with reasons) |

**Hard rules:** the core never changes files, never applies fixes (suggestions and diffs only), never reads or prints secret values, and never lifts login tokens from app databases.

**Out of scope for v0.1.0:** scheduled runs and savings estimates.

Version: **0.1.0**. Full marketplace install guide: [root README](../../README.md). Human smoke steps: [HUMAN-TEST.md](HUMAN-TEST.md).

---

## Install

### Claude Code

```text
/plugin marketplace add scottgrayson/plugins
/plugin install ai-audit@scottgrayson-plugins
```

`${CLAUDE_PLUGIN_ROOT}` is **substituted in skill text only** — it is **not** exported to the Bash tool environment. Skills must use the braced form `${CLAUDE_PLUGIN_ROOT}` (never unbraced `$CLAUDE_PLUGIN_ROOT/…`). Installed copies live under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.

### Cursor

1. **Teams / Enterprise marketplace:** add this marketplace from the Cursor dashboard, then install **ai-audit** from Customize → Plugins.
2. **Local smoke-test:** copy the plugin folder into place and reload Cursor:

```bash
mkdir -p ~/.cursor/plugins/local
cp -R /path/to/plugins/ai-audit ~/.cursor/plugins/local/ai-audit
# Reload Window / restart Cursor, then invoke a skill
```

Cursor documents `${CURSOR_PLUGIN_ROOT}` for `mcp.json`. Skills also try `~/.cursor/plugins/local/ai-audit`, the plugin cache, and paths relative to each `SKILL.md`.

### Grok Bot

Path that works:

1. Ask the bot to **clone this repo** (or a branch) onto its computer.
2. Follow the skill’s **Locate the CLI option B**: set `SKILL_MD` to the absolute path of the skill’s `SKILL.md` under the clone (`…/plugins/ai-audit/skills/<name>/SKILL.md`), then run `scripts/locate-cli.sh` and `bash "$AI_AUDIT" …`.
3. **Optional:** copy only the three skill folders into the bot’s skills catalog **if** `bin/` (and `lib/` / `scripts/`) stay resolvable via `../../bin/ai-audit` from each skill folder — otherwise prefer the full clone + option B.

---

## Locate the CLI (installed copy)

Skills must not hard-code `plugins/ai-audit/bin/…` (checkout-only). Use `scripts/locate-cli.sh`, which tries in order:

1. `$AI_AUDIT_PLUGIN_ROOT/bin/ai-audit`
2. `$CLAUDE_PLUGIN_ROOT/bin/ai-audit` (when that env is actually set)
3. `$CURSOR_PLUGIN_ROOT/bin/ai-audit`
4. `$(dirname $SKILL_MD)/../../bin/ai-audit`
5. `~/.cursor/plugins/local/ai-audit/bin/ai-audit`
6. `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/bin/ai-audit` (newest first)
7. Cursor plugin cache globs under `~/.cursor/plugins/`

The locator is **read-only** (no `chmod`). Always invoke:

```bash
export SKILL_MD=/path/to/ai-audit/skills/context-audit/SKILL.md
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"
bash "$AI_AUDIT" detect
```

---

## CLI

From an **installed** plugin root (or after locate):

```bash
bash bin/ai-audit detect
bash bin/ai-audit context --host auto
bash bin/ai-audit token --host cursor --csv path/to/usage.csv
bash bin/ai-audit token --host grok --usage-file path/to/usage.json
bash bin/ai-audit facts --host grok --stale-days 30
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--host auto\|claude\|cursor\|grok` | Force host (default: auto-detect) |
| `--root PATH` | Override home / project / sand-data roots (tests and fixtures) |
| `--project PATH` | Project directory for Claude/Cursor collectors |
| `--csv PATH` | Cursor usage CSV from the dashboard |
| `--usage-file PATH` | User-downloaded usage file (CSV or JSON) for any host |
| `--memories PATH` | Cursor exported memories `.mdc` |
| `--stale-days N` | Facts older than N days are stale (default 30) |
| `--json-only` / `--md-only` | Emit only one format (default: both) |
| `--out-dir PATH` | Write `report.json` and `report.md` here |

---

## Token usage sources (official)

| Host | Official auto-download | Link / note |
| --- | --- | --- |
| Claude Code | Yes — local session JSONL | [Manage sessions](https://code.claude.com/docs/en/sessions) (`~/.claude/projects/<cwd-slug>/*.jsonl`) |
| Cursor | Yes — Admin API when `CURSOR_ADMIN_API_KEY` is set | [Admin API](https://cursor.com/docs/account/teams/admin-api) (`POST /teams/filtered-usage-events`). File fallback: dashboard Usage CSV |
| Grok Bot | **Not possible** | No documented Bot usage API/export. xAI Management API covers API-key spend, not Bot. Ask for a Usage & Billing JSON file (`--usage-file`) |

Never scraping. Never copied cookies/keys. Totals in the report include a **reconciliation** block (source total vs sum of buckets).

---

## What each host can measure

- **Claude Code:** file-based context inventory; local session JSONL for tokens; auto-memory topic `modified` dates for facts.
- **Cursor:** file-based context inventory; Usage CSV or Admin API for tokens; memories only if an exported `.mdc` is provided.
- **Grok Bot:** workflows / agent profiles / `profile.md` memory on disk; token usage only from a user-provided usage file or `--usage-total`.
