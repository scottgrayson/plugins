# Changelog

## 0.1.0 (stage 1 — startup context)

- Stage 1 ships **context-audit** only: read-only inventory of what loads into agent context.
- Shared `bin/ai-audit` core: `detect` + `context` (JSON + markdown). Hosts: Claude Code, Cursor, Grok Bot.
- **Tokenizer:** vendored **tiktoken o200k_base** ranks (offline; `tiktoken` package required). Cursor report states the tokenizer name.
- **Provider · environment** headings (e.g. `Cursor · Cloud`): auto-detect local OS vs cloud agent markers; `--env` override; JSON carries `provider` + `environment`.
- **Sort:** groups by subtotal descending; rows by tokens descending (skill rows use `--context-window` 200k|1m).
- **Cursor grouped report:** Always-apply rules, User Rules, Skill descriptions (200k + 1M budget cascade, realpath dedupe + name-dedupe lower bound), MCP (startup lines + on-demand schema size / tool counts / caps / needs-auth), Model/settings (hooks). Columns: **Item | Tokens | Description | Source** (facts only).
- **User Rules:** exact-key read of `aicontext.personalContext` from `state.vscdb` via SQLite `mode=ro` with `immutable=1` when no WAL/SHM (no sidecar create); WAL-aware when present; errors → unreadable row.
- **Round 18:** redact once before any renderer; Cloud ≥2 markers; vendor README + pinned `tiktoken==0.14.0`.
- **Round 19:** `-p` only in mysql-like / secret-shaped contexts; follow skill/dir symlinks; nested `pluginRoot` + custom `plugin.json` paths; enabled/disabled from `cursor.plugins.installedIds.*`; MCP `empty-window` vs project slug; Claude session hooks measure `rule.txt`; synthetic `fixtures/cursor-layout`.
- **Quality checks:** deterministic, token-free Checks section (`Item | Check | Finding | Source`) below the Cursor table — broken references, dead/contradictory rules, skill description length/triggers, files over 500 lines, same-name drift, stale names, broken front matter. No AI; findings are facts with `file:line`; agent reports them verbatim.
- Shared assignment redaction (rounds 16–19): whole-value after secret-like name + separator; `-p` keep cases for hyphenated names/ports; i18n password names; quoted trailing words; zero-width strip. Inventory filesystem paths are never rewritten.
- Context audit: bounded project scan; skips plugin cache/marketplace trees and this plugin's fixtures; dedupes resolved paths; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand. Enabled plugin skills from `enabledPlugins` + `installed_plugins.json`.
- **Round 20:**
  - `broken_references` skips URLs, bare domains, e-mail, template tokens (`<name>`, `${X}`, `{var}`, `$VAR`) and example paths (`path/to/x`, `.../x`); it flags only relative, absolute and `~` paths and `@file` includes with a real file extension.
  - MCP rows match `mcp.json` and plugin server names to synced descriptor folders (`user-<name>`, `plugin-<plugin>-<server>`) before flagging `failed-to-start-or-empty`. `duplicate servers` needs two distinct folders for one server.
  - Plugin discovery covers flat `cache/<market>/<plugin>/`, `cache/<market>-ai-skills/<sha>/plugins/<name>/` (marketplace `pluginRoot`) and Claude `cache/<market>/<plugin>/<semver>/`. The context table and Checks share one discovered set.
  - `UserPromptSubmit` hooks move to a **Per prompt** group outside the startup total. Hook rows use the plugin name, and each `hooks.json` is read once.
  - Redaction keeps kebab names that start with a secret word (`token-budget`, `secret-scanning`) and prose such as `auth,`. Added `Kennwort`, `senha`, `パスワード`.
  - Grok Bot cloud needs two of `SAND_BOX_TENANT_ID`, `SAND_BOX_STORE_ID`, `SAND_BOX_BOOT_ID`, `/home/box/agent-data`. A bare `/home/box` no longer counts.
  - Trigger check also accepts `whenever`, `apply to/before/after`, `use this skill to/when`, `use to`.
  - Stale names default to empty. Set `AI_AUDIT_STALE_NAMES` (comma-separated) or `stale_names = [...]` in `~/.config/ai-audit/config.toml`.
  - Plugins are never excluded because of `state.vscdb` (format unverified). The report has an **Enabled** column (`yes`/`no`/`unknown`) and its notes list every `state.vscdb` key read.
  - The skills wrapper row now holds only the fixed `<agent_skills>` text. Skills the 200k budget omits are listed as their own rows, so the 1M column is itemised.
- **Later stages (not in this release):** token usage attribution, facts/memory hygiene.
- Out of scope: scheduled runs and savings estimates.
- HUMAN-TEST: Claude / Cursor install, context command, secret check on context output.
- **Round 21:** kebab-name exception removed from assignments (`pw-prod=x` redacts); project dir == home reads `~/.cursor/mcp.json` once; vscdb-disabled plugin skill asserted listed/counted/checked; `apply during` trigger; prose `skill mid-task` not a reference; `secret, X` redacts; `grep -p foo` and `password reset flow docs` kept.
- **Round 22:** removed the "password <prose>" allowlist (leaks over reuse); `redis-cli -a` and `htpasswd -b` redacted; `tests/data/redaction_corpus.tsv` regression corpus.
