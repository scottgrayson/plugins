# Changelog

## 0.1.0

- Initial release: three read-only skills (`context-audit`, `token-audit`, `facts-audit`).
- Shared `bin/ai-audit` core detects host (or takes `--host`) and emits JSON plus a short markdown report.
- Hosts: Claude Code, Cursor, Grok Bot. Fixture-based tests for each.
