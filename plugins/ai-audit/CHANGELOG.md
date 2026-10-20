# Changelog

## 0.1.0 (stage 1 — startup context)

- Stage 1 ships **context-audit** only: read-only inventory of what loads into agent context.
- Shared `bin/ai-audit` core: `detect` + `context` (JSON + markdown). Hosts: Claude Code, Cursor, Grok Bot.
- Shared assignment redaction (round 16): after a secret-like name and separator (`=`, `:`, ` is `, `-`/`→`/`->`, or whitespace for `password`/`passwd`), redact the whole value. Exceptions: empty, placeholders, null/none, booleans, a single status-word allowlist entry, knob numbers, and `pass=N` test counts. No prose heuristics. Inventory filesystem paths are never rewritten.
- Context audit: bounded project scan; skips plugin cache/marketplace trees and this plugin's fixtures; dedupes resolved paths; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand. Enabled plugin skills from `enabledPlugins` + `installed_plugins.json`.
- **Later stages (not in this release):** token usage attribution, facts/memory hygiene.
- Out of scope: scheduled runs and savings estimates.
- HUMAN-TEST: Claude / Cursor install, context command, secret check on context output.
