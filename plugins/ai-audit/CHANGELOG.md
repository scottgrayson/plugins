# Changelog

## 0.1.0

- Initial release: three read-only skills (`context-audit`, `token-audit`, `facts-audit`).
- Shared `bin/ai-audit` core detects host (or takes `--host`) and emits JSON plus a short markdown report.
- Hosts: Claude Code, Cursor, Grok Bot. Fixture-based tests for each.
- Token sources: Claude local JSONL / `$CLAUDE_CONFIG_DIR`; Cursor Enterprise Admin API (epoch ms + pagination) or Usage CSV; Grok Bot asks for a usage file.
- Shared value-pattern redaction for secret-shaped strings in all JSON/md output (password=/secret=/token=, AWS secret keys, `crsr_…`, Bearer JWTs — whole values).
- Context audit: bounded project scan; skips `~/.claude/plugins` cache/marketplace trees and this plugin's fixtures; dedupes; does not treat `~` as a project tree.
- Unparseable `--admin-start`/`--admin-end` exit non-zero; missing `--csv` exits non-zero.
- Reconciliation: source totals vs by_model / by_kind sums (`no data` when empty).
- Out of scope: scheduled runs and savings estimates (removed).
- Hardening: `--root` isolation; no live Admin API in tests (mocked); no pycache; HUMAN-TEST zsh-safe + branch marketplace install.
