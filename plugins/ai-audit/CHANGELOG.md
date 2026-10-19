# Changelog

## 0.1.0

- Initial release: three read-only skills (`context-audit`, `token-audit`, `facts-audit`).
- Shared `bin/ai-audit` core detects host (or takes `--host`) and emits JSON plus a short markdown report.
- Hosts: Claude Code, Cursor, Grok Bot. Fixture-based tests for each.
- Hardening: `--root` fully isolates detection; real Cursor dashboard CSV headers; Claude JSONL dedupe + tool_result attribution; Grok `activeAgentId` + skill-cache dedupe; `disable-model-invocation` handling; `@import` / nested coverage; honest wins (no invented savings); opt-in Admin API with Basic auth; facts conflict word-boundaries.
