---
name: token-audit
description: Attribute token usage from official local logs or documented APIs, with a usage-file fallback. Read-only.
disable-model-invocation: true
---

# token-audit

Attribute token usage by host. Prefer **official auto sources**; otherwise ask for a **user-downloaded usage file**. Never scrape cookies/login tokens. Never invent savings numbers (out of scope for v0.1.0).

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

## Official sources (auto vs file)

| Host | Official auto | File fallback |
| --- | --- | --- |
| Claude Code | Local session JSONL under `~/.claude/projects/` ([Manage sessions](https://code.claude.com/docs/en/sessions)) | `--usage-file` JSON/CSV if JSONL is unavailable |
| Cursor | **Enterprise team admin keys only** — Admin API when `CURSOR_ADMIN_API_KEY` is set ([Admin API](https://cursor.com/docs/account/teams/admin-api); dates as epoch ms). Individual / Pro: CSV only | Dashboard Usage CSV via `--csv` / `--usage-file` |
| Grok Bot | **Not possible** — no documented Bot usage API/export (xAI Management API is for API keys, not Bot) | JSON from Usage & Billing UI via `--usage-file` or `--usage-total` |

## Run by host

### Claude Code

```bash
bash "$AI_AUDIT" token --host claude
```

Parses local JSONL (deduped by message.id/requestId). Tool cost is attributed to the request that carries the tool_result.

### Cursor

Auto (**Enterprise team admin key only** — individual / Pro accounts cannot use this):

```bash
export CURSOR_ADMIN_API_KEY=…   # team Admin API key from Cursor — never from cookies
bash "$AI_AUDIT" token --host cursor
```

File fallback (everyone; required on individual / Pro):

```bash
# Dashboard → Usage → download/export CSV, then pass the real filename:
bash "$AI_AUDIT" token --host cursor --csv ~/Downloads/usage*.csv
```

Never scrape `state.vscdb`.

### Grok Bot

There is **no official auto-download**. Say so plainly, then ask for a usage file:

```bash
# usage.json example: {"usage_total": 72, "usage_period": "week of 2026-10-13", "unit": "percent"}
bash "$AI_AUDIT" token --host grok --usage-file /tmp/usage.json
# or:
bash "$AI_AUDIT" token --host grok --usage-total 72 --usage-period "week of 2026-10-13"
```
