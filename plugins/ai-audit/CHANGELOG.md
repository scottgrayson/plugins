# Changelog

## 0.1.0

- Initial release: three read-only skills (`context-audit`, `token-audit`, `facts-audit`).
- Shared `bin/ai-audit` core detects host (or takes `--host`) and emits JSON plus a short markdown report.
- Hosts: Claude Code, Cursor, Grok Bot. Fixture-based tests for each.
- Token sources: Claude local JSONL / `$CLAUDE_CONFIG_DIR`; Cursor Enterprise Admin API (epoch ms + pagination) or Usage CSV; Grok Bot asks for a usage file.
- Shared value-pattern redaction for secret-shaped strings in all JSON/md output (password=/secret=/token=, AWS secret keys, `crsr_…`, Bearer JWTs — whole values).
- Redaction also covers PEM private keys, URL `user:pass@host`, Stripe `sk_live_`/`rk_live_`, prefixed env vars (`DB_PASSWORD=`, `*_TOKEN`, `*_SECRET`, `*_API_KEY`, `*_KEY`), and JSON `"password": "…"`. Bare 40-char AWS secrets require key/AKIA context and never rewrite filesystem paths; inventory `path` fields are not value-redacted. `token:`/`secret:` prose only redacts secret-looking values.
- Context audit: bounded project scan; skips `~/.claude/plugins` cache/marketplace trees and this plugin's fixtures; dedupes; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand (not always-loaded). Enabled plugin skills listed from `enabledPlugins` + `installed_plugins.json` install paths only (no full cache walk; no fixtures/duplicates).
- Unparseable `--admin-start`/`--admin-end` exit non-zero; missing `--csv` exits non-zero.
- Reconciliation: source totals vs by_model / by_kind sums (`no data` when empty).
- Out of scope: scheduled runs and savings estimates (removed).
- Hardening: `--root` isolation; no live Admin API in tests (mocked); no pycache; HUMAN-TEST zsh-safe (no inline `#` in code blocks; `zsh -f -n` in CI) + branch marketplace install; Cursor install path-independent; checks use `echo FAIL` instead of `exit 1`.
- CLI help: “redacts common secret formats” (not “never prints secrets”).
