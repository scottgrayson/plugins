# Changelog

## 0.1.0 (stage 1 — startup context)

- Stage 1 ships **context-audit** only: read-only inventory of what loads into agent context.
- Shared `bin/ai-audit` core: `detect` + `context` (JSON + markdown). Hosts: Claude Code, Cursor, Grok Bot.
- Shared value-pattern redaction for secret-shaped strings in context output (assignment scanner + known-prefix tokens). Inventory filesystem paths are never rewritten.
- Context audit: bounded project scan; skips plugin cache/marketplace trees and this plugin's fixtures; dedupes resolved paths; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand. Enabled plugin skills from `enabledPlugins` + `installed_plugins.json`.
- **Later stages (not in this release):** token usage attribution, facts/memory hygiene.
- Out of scope: scheduled runs and savings estimates.
- HUMAN-TEST: Claude / Cursor install, context command, secret check on context output.
