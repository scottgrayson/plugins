# Changelog

## 1.1.0

- **Agent mode has no timers**: `--agent` disables both idle and `totalCapSeconds`. Human/Actions mode unchanged (5m idle, 15m cap).
- **Env wins over config**: a pre-set `PREVIEW_*` env var is kept when loading `.preview/config.json` (config.env no longer clobbers it).
- **No false READY on a busy port**: fail fast if the configured app port is already in use; if the app process exits before READY, fail immediately naming the port. Readiness stays tied to our own app (fail-fast rather than auto-picking a free port — config port stays predictable).
- **`--detach` for agent mode**: `preview up --agent --detach` exits 0 once READY (URL on stdout), leaving app/proxy/tunnel running; `preview stop` tears down. Skill recommends detach → drive URL → stop.
