---
name: preview
description: Start, report, and wait on a temporary local (or shared) preview of a repo app or PR. Use when asked to preview a PR, branch, or local app.
disable-model-invocation: true
---

# preview

Share the same scripts Actions mode uses. No AI is required to keep the preview up — you only orchestrate them.

## Agents: local only — never trigger Actions

This skill has `disable-model-invocation: true`: it is not auto-selected from its description. Run it only via explicit `/preview` (or when a consumer repo rule tells you to invoke it). Do not start a preview on your own initiative.

**Run previews on your own VM in agent mode.** Start locally with `preview up --agent --detach` so you can hit the site on localhost, debug, and edit in place.

**Do not tick the PR checkbox, edit the PR body to start a preview, or otherwise trigger the GitHub Actions preview workflow.** Actions mode is for humans reviewing a PR in the browser. Agents must use local agent mode so they can inspect the live site on the same machine.

**Default: no tunnel.** `preview up --agent` starts the app + local proxy only and prints a `http://127.0.0.1:<port>` URL. Test against that localhost URL.

**Share only when a human asks for a link.** Use `preview up --agent --share` (or `preview share`) to open a Cloudflare quick tunnel (`*.trycloudflare.com`) and report the public URL. Do not share by default.

## Locate the plugin

Prefer the installed plugin copy. From a checkout of this marketplace repo:

```bash
PREVIEW_BIN="$PWD/plugins/preview/bin/preview"
chmod +x "$PREVIEW_BIN"
```

If the plugin is installed elsewhere, set `PREVIEW_BIN` to that install's `bin/preview`.

## Config

All repo-side preview files live under `.preview/`:

- `.preview/config.json` — plain values only
- `.preview/steps/<name>.sh` — named step scripts
- `.preview/README.md` — optional human notes

Secrets are not supported in v1; previews work for apps that need no secrets.

Defaults when omitted: `type=static`, `idleSeconds=300` (5m), `totalCapSeconds=900` (15m). In Actions/CLI the total cap always wins over idle. Agent mode ignores both timers. Pre-set `PREVIEW_*` env vars win over config.json.

Built-in ignore (merged with `ignorePaths`): path wildcard `*poll*` plus static assets (common extensions and `/assets/`, `/static/`). Repos add their own patterns via `ignorePaths` in `.preview/config.json` (fnmatch wildcards supported).

## Commands

```bash
# Agent mode (required for agents): localhost only; no timers; detach once READY
PREVIEW_PR_NUMBER=<n> PREVIEW_BRANCH=<name> \
  "$PREVIEW_BIN" up --agent --detach --config .preview/config.json --labels "<csv labels>" --workdir <app-dir>

# When a human asks for a public link:
"$PREVIEW_BIN" up --agent --share --config .preview/config.json --workdir <app-dir>
# or:
"$PREVIEW_BIN" share --config .preview/config.json --workdir <app-dir>

# Actions / human CLI (always tunnels; idle + total cap)
"$PREVIEW_BIN" up --config .preview/config.json

# Print URL once READY (localhost or public)
"$PREVIEW_BIN" url

# Status / reason
"$PREVIEW_BIN" status

# Request shutdown
"$PREVIEW_BIN" stop [reason]
```

`up --agent --detach` prints the URL when READY and exits 0, leaving the app (and proxy, and tunnel if `--share`) running. Tear down with `preview stop`. Without `--detach`, `up` stays in the foreground under the watchdog (agent mode: no timers; exits on `stop`, app-exit, or tunnel-exit). READY means the URL answered HTTP (localhost for agent default; public `trycloudflare.com` when shared or in Actions). Prefer QUIC/HTTP3 when available, else HTTP/2, else plain HTTPS/HTTP.

## Agent session rules (required)

1. Start with `preview up --agent --detach` (add `--share` only if the human asked for a public link). Non-zero exit if startup fails.
2. Drive the printed URL for local testing (or reply with the public link if shared). Mention they can refresh after your edits when sharing.
3. When finished, run `preview stop` and report the stop reason from `preview status` / stderr (`stop`, `app-exit`, `tunnel-exit`, …). Idle and total-cap stops do not apply in agent mode.

Live edits on static / file-served apps appear on the same URL after refresh. Prefer editing files in place; do not restart unless the start command itself must change.

## Previewing a PR (local agent mode)

1. Check out the PR head (or work in the already-checked-out PR branch).
2. `cd` to the app directory that has `.preview/config.json` (for this repo: `examples/preview`).
3. Set `PREVIEW_PR_NUMBER` from the PR.
4. Pass PR labels via `--labels` if present (`preview:<step>` only). Unknown steps must fail with the valid list — do not invent commands.
5. Run `up --agent --detach`, drive the localhost URL, then `preview stop`. Add `--share` only when the human wants a public link.
6. Never trigger the Actions checkbox workflow.

## Keep-alive

A reverse proxy logs every request. In Actions/CLI mode, a single idle timer starts when the preview is READY and resets on each counted request (page loads, form posts, user XHR). Never counted: default `*poll*`, `ignorePaths`, and static assets. There is no first-visit / grace-period logic.

**Agent mode (`--agent` or `PREVIEW_AGENT_MODE=1`):** no idle timer and no total cap. The preview lives until `preview stop` (or app/tunnel exit). Human/Actions mode still applies idle + `totalCapSeconds` (cap wins).
