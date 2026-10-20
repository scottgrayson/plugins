# Changelog

## 0.1.0 (stage 1 — startup context)

- Stage 1 ships **context-audit** only: read-only inventory of what loads into agent context.
- Shared `bin/ai-audit` core: `detect` + `context` (JSON + markdown). Hosts: Claude Code, Cursor, Grok Bot.
- **Tokenizer:** vendored **tiktoken o200k_base** ranks (offline; `tiktoken` package required). Cursor report states the tokenizer name.
- **Provider · environment** headings (e.g. `Cursor · Cloud`): auto-detect local OS vs cloud agent markers; `--env` override; JSON carries `provider` + `environment`.
- **Sort:** groups by subtotal descending; rows by tokens descending (skill rows use `--context-window` 200k|1m).
- **Cursor grouped report:** Always-apply rules, User Rules, Skill descriptions (200k + 1M budget cascade, realpath dedupe), MCP (startup lines + on-demand schema size / tool counts / caps / needs-auth), Model/settings (hooks). Purpose and AI suggestion columns left blank for the agent.
- **User Rules:** exact-key read of `aicontext.personalContext` from `state.vscdb` via SQLite `mode=ro&immutable=0` (no temp copy; WAL-aware).
- **Round 18:** redact once before any renderer (JSON + markdown); MCP preamble measured (~637 tok); active plugin versions only; script hooks labelled unmeasured; Cloud needs ≥2 markers; `--env` sanitised; vendor README + pinned `tiktoken==0.14.0`.
- Shared assignment redaction (rounds 16–18): whole-value after secret-like name + separator; exceptions only when the entire value matches; wider names/separators (`access key`, `DBPASS`, `pa$$word`, `-pX`, …); `changeme` is not a placeholder. Inventory filesystem paths are never rewritten.
- Context audit: bounded project scan; skips plugin cache/marketplace trees and this plugin's fixtures; dedupes resolved paths; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand. Enabled plugin skills from `enabledPlugins` + `installed_plugins.json`.
- **Later stages (not in this release):** token usage attribution, facts/memory hygiene.
- Out of scope: scheduled runs and savings estimates.
- HUMAN-TEST: Claude / Cursor install, context command, secret check on context output.
