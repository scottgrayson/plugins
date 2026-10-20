# Changelog

## 0.1.0

- Initial release: three read-only skills (`context-audit`, `token-audit`, `facts-audit`).
- Shared `bin/ai-audit` core detects host (or takes `--host`) and emits JSON plus a short markdown report.
- Hosts: Claude Code, Cursor, Grok Bot. Fixture-based tests for each.
- Token sources: Claude local JSONL (auto); Cursor Admin API when `CURSOR_ADMIN_API_KEY` is set, else Usage CSV; Grok Bot asks for a usage file (no official Bot API).
- Reconciliation: source totals vs by_model / by_kind sums.
- Out of scope: scheduled runs and savings estimates.
- Hardening: `--root` isolation; real Cursor dashboard CSV headers; Claude JSONL dedupe + tool_result attribution; no pycache; secrets skipped/redacted.
