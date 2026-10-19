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

Claude sets `CLAUDE_PLUGIN_ROOT` for installed plugin skills. Skills call `"${CLAUDE_PLUGIN_ROOT}/bin/ai-audit"` (or `scripts/locate-cli.sh`).

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

1. Install the full plugin package (must include `bin/`, `lib/`, `scripts/`, and `skills/` — not a single skill folder alone).
2. Register or copy skills into the bot's workflows / skill catalog so each skill folder remains under the plugin tree (`…/skills/<name>/SKILL.md` next to `…/bin/ai-audit`).
3. From a skill, resolve the CLI as `../../bin/ai-audit` or run `scripts/locate-cli.sh` with `SKILL_MD` set to that skill file.

---

## Locate the CLI (installed copy)

Skills must not hard-code `plugins/ai-audit/bin/…` (checkout-only). Use `scripts/locate-cli.sh`, which tries in order:

1. `$AI_AUDIT_PLUGIN_ROOT/bin/ai-audit`
2. `$CLAUDE_PLUGIN_ROOT/bin/ai-audit`
3. `$CURSOR_PLUGIN_ROOT/bin/ai-audit`
4. `$(dirname $SKILL_MD)/../../bin/ai-audit`
5. `~/.cursor/plugins/local/ai-audit/bin/ai-audit`
6. `~/.claude/plugins/ai-audit/bin/ai-audit`
7. Cursor plugin cache globs under `~/.cursor/plugins/`

```bash
export SKILL_MD=/path/to/ai-audit/skills/context-audit/SKILL.md
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"
bash "$AI_AUDIT" detect
```

---

## CLI

From an **installed** plugin root (or after locate):

```bash
chmod +x bin/ai-audit scripts/locate-cli.sh
./bin/ai-audit detect
./bin/ai-audit context --host auto
./bin/ai-audit token --host cursor --csv path/to/usage.csv
./bin/ai-audit facts --host grok --stale-days 30
```

From a marketplace **checkout** (dev only):

```bash
AI_AUDIT_BIN="$PWD/plugins/ai-audit/bin/ai-audit"
"$AI_AUDIT_BIN" detect
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

- **Claude Code:** file-based context inventory; local session JSONL for tokens; auto-memory topic `modified` dates for facts.
- **Cursor:** file-based context inventory; usage CSV (and optional Admin API via env) for tokens; memories only if an exported `.mdc` is provided.
- **Grok Bot:** workflows / agent profiles / `profile.md` memory on disk; token usage estimated from message volume (labeled as estimate); routines via `--routines-json`.

---

## Scheduling as a routine

Each skill's `SKILL.md` has a **Schedule as a routine** section with concrete steps for:

| Host | Mechanism |
| --- | --- |
| Claude Code | Scheduled task / `/loop` prompt that locates the CLI via `CLAUDE_PLUGIN_ROOT` |
| Cursor | Cloud Agent / Automation with cron + skill invoke |
| Grok Bot | Bot Routine with cron message to run the skill |

The core stays read-only either way — routines summarize wins and never apply edits without approval.
