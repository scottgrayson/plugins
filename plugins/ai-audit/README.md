# ai-audit (stage 1 — startup context)

Read-only inventory of what loads into an agent's context at startup: instructions, rules, skills, subagents, MCP/connectors, plugins, bot descriptions, and memory paths.

| Skill | What it answers |
| --- | --- |
| `context-audit` | What loads into context; on Cursor, grouped startup table with real o200k tokens |

**Tokenizer:** tiktoken `o200k_base` (ranks vendored under `vendor/` — see `vendor/README.md` for source URL, sha256, MIT notice). Install once: `python3 -m pip install --user 'tiktoken==0.14.0'` (package still required; pure-Python BPE was not shipped because o200k’s pre-tokenizer needs fancy-regex). No network at runtime after install.

**Later stages (not in this PR):** token usage attribution, facts/memory hygiene.

**Hard rules:** the core never changes files, never applies fixes (suggestions only), never reads or prints secret values, and never lifts login tokens from app databases.

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
# Reload Window / restart Cursor, then invoke /ai-audit:context-audit
```

### Grok Bot

1. Ask the bot to **clone this repo** (or a branch) onto its computer.
2. Follow the skill’s **Locate the CLI option B**: set `SKILL_MD` to the absolute path of `skills/context-audit/SKILL.md` under the clone, then run `scripts/locate-cli.sh` and `bash "$AI_AUDIT" …`.

---

## Locate the CLI (installed copy)

```bash
export SKILL_MD=/path/to/ai-audit/skills/context-audit/SKILL.md
AI_AUDIT="$(bash "$(dirname "$SKILL_MD")/../../scripts/locate-cli.sh")"
bash "$AI_AUDIT" detect
bash "$AI_AUDIT" context --host auto
```

---

## CLI

```bash
bash bin/ai-audit detect
bash bin/ai-audit context --host auto
```

| Flag | Meaning |
| --- | --- |
| `--host auto\|claude\|cursor\|grok` | Force host (default: auto-detect) |
| `--root PATH` | Override home / project / sand-data roots (tests and fixtures) |
| `--project PATH` | Project directory |
| `--json-only` / `--md-only` | Emit only one format (default: both) |
| `--out-dir PATH` | Write `report.json` and `report.md` here |

Secret-shaped values in notes and other string fields are redacted; inventory filesystem paths are never rewritten.
