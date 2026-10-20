# Changelog

## 0.1.0 (stage 1 — startup context)

- Stage 1 ships **context-audit** only: read-only inventory of what loads into agent context.
- Shared `bin/ai-audit` core: `detect` + `context` (JSON + markdown). Hosts: Claude Code, Cursor, Grok Bot.
- **Tokenizer:** vendored **tiktoken o200k_base** ranks (offline; `tiktoken` package required). Cursor report states the tokenizer name.
- **Cursor grouped report:** Always-apply rules, User Rules, Skill descriptions (200k + 1M budget cascade, realpath dedupe), MCP (startup lines + on-demand schema size / tool counts / caps / needs-auth), Model/settings (hooks). Purpose and AI suggestion columns left blank for the agent.
- **User Rules:** exact-key read of `aicontext.personalContext` from a temp copy of `state.vscdb` only.
- Shared assignment redaction (rounds 16–17): whole-value after secret-like name + separator; exceptions only when the entire value matches; wider names/separators; `changeme` is not a placeholder. Inventory filesystem paths are never rewritten.
- Context audit: bounded project scan; skips plugin cache/marketplace trees and this plugin's fixtures; dedupes resolved paths; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand. Enabled plugin skills from `enabledPlugins` + `installed_plugins.json`.
- **Later stages (not in this release):** token usage attribution, facts/memory hygiene.
- Out of scope: scheduled runs and savings estimates.
- HUMAN-TEST: Claude / Cursor install, context command, secret check on context output.
