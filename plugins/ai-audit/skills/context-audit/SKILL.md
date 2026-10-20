---
name: context-audit
description: Inventory everything that loads into an agent's context. Read-only; never applies fixes.
disable-model-invocation: true
---

# context-audit

Read-only inventory of instructions, rules, skills, subagents, MCP/connectors, plugins, bot descriptions, and memory. On **Cursor**, the report is a grouped startup table with real **tiktoken o200k_base** token counts (offline, vendored ranks).

## Locate the CLI

Do **not** use a repo-relative `plugins/ai-audit/bin/ai-audit` path — that only works inside this marketplace checkout. Resolve the installed plugin copy. Always invoke with `bash …/bin/ai-audit` (read-only; do not `chmod +x`).

1. **Claude Code:** Claude substitutes **only** the braced form `${CLAUDE_PLUGIN_ROOT}` in skill *text*. It is **not** exported to the Bash tool environment, so an unbraced dollar-var plus slash path becomes `/scripts/…` and fails. Always write `${CLAUDE_PLUGIN_ROOT}`.
2. **Cursor:** `${CURSOR_PLUGIN_ROOT}` is documented for `mcp.json`; if unset, try `~/.cursor/plugins/local/ai-audit`, the plugin cache, or derive the plugin root from this skill file (`skills/context-audit/SKILL.md` → `../..`).
3. **Grok Bot:** resolve relative to this skill file: `../../bin/ai-audit` (option B below).

```bash
# A) Claude Code — braced ${CLAUDE_PLUGIN_ROOT} is substituted in skill text only:
AI_AUDIT="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/locate-cli.sh")"

# B) Skill-file relative (Cursor / Grok / any host) — set SKILL_MD to THIS file:
export SKILL_MD="/absolute/path/to/skills/context-audit/SKILL.md"
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"

# C) Explicit install root (e.g. local Cursor plugin):
export AI_AUDIT_PLUGIN_ROOT="${HOME}/.cursor/plugins/local/ai-audit"
AI_AUDIT="$(bash "${AI_AUDIT_PLUGIN_ROOT}/scripts/locate-cli.sh")"

bash "$AI_AUDIT" context --help
```

Requires Python package `tiktoken` (encoding ranks are vendored; no network at runtime).

## Run

```bash
bash "$AI_AUDIT" context --host auto
bash "$AI_AUDIT" context --host claude --root path/to/fixture
bash "$AI_AUDIT" context --host cursor --project .
bash "$AI_AUDIT" context --host grok
```

Show the markdown report to the user.

### Report is facts only (Cursor grouped table)

The script fills **Item**, **Tokens**, **Description** (quoted/truncated from the file, redacted), **Source** (short path), and **Enabled** (`yes`, `no` or `unknown`; reporting only, nothing is excluded by it). Below the inventory table, a **Checks** section lists deterministic, token-free findings (`Item | Check | Finding | Source`), sorted by severity. Every finding is a fact with `file:line`.

**Do not** add generated text to the inventory table or the Checks table. Do not invent Purpose, AI suggestion, or token numbers. Report check findings **verbatim** — add no judgement. Do not edit files. Pattern hints (vague words, shouting) are not in this release.

Broken-reference check scope: an unquoted two-segment skill reference such as `skill my-skill` is not checked, to avoid prose false positives. Quoted names and names with three or more segments are checked. Absolute non-home paths (for example `/opt/...`, `/srv/...`) are checked in prose and skipped inside inline or fenced code. Sandbox, container and temp roots (`/workspace/`, `/app/`, `/tmp/`) and placeholder directories starting with `example` are treated as examples and skipped in prose too.

## When to ask the user

| Gap | Ask for |
| --- | --- |
| Grok connectors (not on disk) | Ask the bot in chat, or open the info pane (click the bot name in the chat header) |
| Claude live breakdown | Paste `/context` output to calibrate |

Never read secret files (`host-secrets.json`, `gateway.json`, MCP `env`/`headers` values, `~/.ai/secrets/`). Never lift login tokens from app databases. User Rules: the CLI reads only `aicontext.personalContext` and the `cursor.plugins.installedIds.*` keys (Enabled column) from `state.vscdb` via SQLite `mode=ro` (no temp copy). The report notes list every key read. Never other keys.
