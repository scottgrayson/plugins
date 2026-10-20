# Changelog

## 0.1.0

- Initial release: three read-only skills (`context-audit`, `token-audit`, `facts-audit`).
- Shared `bin/ai-audit` core detects host (or takes `--host`) and emits JSON plus a short markdown report.
- Hosts: Claude Code, Cursor, Grok Bot. Fixture-based tests for each.
- Token sources: Claude local JSONL / `$CLAUDE_CONFIG_DIR`; Cursor Enterprise Admin API (epoch ms + pagination) or Usage CSV; Grok Bot asks for a usage file.
- Shared value-pattern redaction for secret-shaped strings in all JSON/md output (password=/secret=/token=, AWS secret keys, `crsr_…`, Bearer JWTs — whole values).
- Redaction covers PEM/PGP private keys, URL `user:pass@host` (incl. empty user), Stripe `sk_live_`/`sk_test_`/`rk_live_`, `glpat-`/`npm_`/`whsec_`, Slack webhooks, Bearer/Basic, `x-api-key:`, `pass=`/`passphrase:`/`DB_PASS=`/`MYSQL_PWD=`, JSON `"password"`/`'password'`, and quoted values with spaces. Env names: `*PASSWORD`/`*TOKEN`/`*SECRET`/`*_API_KEY`/`*_SECRET_KEY`/`*_ACCESS_KEY`/`PRIVATE_KEY` only (no bare `KEY`/`pwd`). Label values redact only when secret-shaped (no spaces, 16+ mixed alnum, or known prefix; kebab model ids kept). Bare AWS secrets need context; inventory paths never rewritten.
- Context audit: bounded project scan; skips `~/.claude/plugins` cache/marketplace trees and this plugin's fixtures; dedupes; does not treat `~` as a project tree.
- Nested package `.claude/skills` marked on-demand. Enabled plugin skills from `enabledPlugins` + `installed_plugins.json` (precedence: user < project < local).
- Unparseable `--admin-start`/`--admin-end` exit non-zero; missing `--csv` exits non-zero.
- Reconciliation: source totals vs by_model / by_kind sums (`no data` when empty).
- Out of scope: scheduled runs and savings estimates (removed).
- Hardening: `--root` isolation; no live Admin API in tests; HUMAN-TEST zsh-safe; install checks gated PASS/FAIL; Cursor local copy idempotent + cache fallback; CI installs zsh.
- CLI help: “redacts common secret formats”.
