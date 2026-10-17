---
name: preview
description: Start, report, and wait on a temporary Cloudflare quick-tunnel preview of a repo app or PR. Use when asked to preview a PR, branch, or local app.
---

# preview

Share the same scripts Actions mode uses. No AI is required to keep the tunnel up — you only orchestrate them and stay in the turn until shutdown.

## Agents: local only — never trigger Actions

**Run previews on your own VM in agent mode.** Start the tunnel locally with `preview up --agent` so you can hit the site, debug, and edit in place.

**Do not tick the PR checkbox, edit the PR body to start a preview, or otherwise trigger the GitHub Actions preview workflow.** Actions mode is for humans reviewing a PR in the browser. Agents must use local agent mode so they can inspect the live site on the same machine.

## Locate the plugin

Prefer the installed plugin copy. From a checkout of this marketplace repo:

```bash
PREVIEW_BIN="$PWD/plugins/preview/bin/preview"
chmod +x "$PREVIEW_BIN"
```

If the plugin is installed elsewhere, set `PREVIEW_BIN` to that install's `bin/preview`.

## Config

Repo config is `.preview.json` (plain values only). Named steps live at `.preview/steps/<name>.sh`. Optional human notes: `PREVIEW.md`. Secrets are not supported in v1; previews work for apps that need no secrets.

Defaults when omitted: `type=static`, `idleSeconds=300` (5m), `totalCapSeconds=900` (15m). Max always wins: the total cap stops the preview even if idle would allow more.

Built-in ignore (merged with `ignorePaths`): path wildcard `*poll*` plus static assets (common extensions and `/assets/`, `/static/`). Repos add their own patterns via `ignorePaths` in `.preview.json` (fnmatch wildcards supported).

## Commands

```bash
# Agent mode (required for agents): no idle timer; only totalCapSeconds as safety net
PREVIEW_PR_NUMBER=<n> PREVIEW_BRANCH=<name> \
  "$PREVIEW_BIN" up --agent --config .preview.json --labels "<csv labels>" --workdir <app-dir>

# Actions / human CLI (idle + total cap)
"$PREVIEW_BIN" up --config .preview.json

# Print public URL once READY
"$PREVIEW_BIN" url

# Status / reason
"$PREVIEW_BIN" status

# Request shutdown
"$PREVIEW_BIN" stop [reason]
```

`up` prints the public URL when READY, then keeps running in the foreground under the keep-alive watchdog. READY means the public `trycloudflare.com` URL answered HTTP (QUIC/HTTP3 when available, else HTTP/2, else plain HTTPS).

## Agent session rules (required)

Cloud agent VMs may stop when a turn ends. You must:

1. Start the preview with `preview up --agent` **in the foreground of this turn** (or background it and immediately `preview wait`).
2. As soon as the URL is known, reply to the human with the link (and mention they can refresh after your edits).
3. **Do not end the turn while the preview is up.** Keep waiting on the watchdog until status is `stopped`.
4. When it stops, report `preview stopped` with the reason from `preview status` / stderr (`total-cap`, `stop`, `app-exit`, `tunnel-exit`, …). Idle stop does not apply in agent mode.

Live edits on static / file-served apps appear on the same URL after refresh. Prefer editing files in place; do not restart the tunnel unless the start command itself must change.

## Previewing a PR (local agent mode)

1. Check out the PR head (or work in the already-checked-out PR branch).
2. `cd` to the app directory that has `.preview.json` (for this repo: `examples/hello-world`).
3. Set `PREVIEW_PR_NUMBER` from the PR.
4. Pass PR labels via `--labels` if present (`preview:<step>` only). Unknown steps must fail with the valid list — do not invent commands.
5. Run `up --agent`, report the URL, stay until stopped, then report the stop reason.
6. Never trigger the Actions checkbox workflow.

## Keep-alive

A reverse proxy logs every request. In Actions/CLI mode, a single idle timer starts when the preview is READY and resets on each counted request (page loads, form posts, user XHR). Never counted: default `*poll*`, `ignorePaths`, and static assets. There is no first-visit / grace-period logic.

**Agent mode (`--agent` or `PREVIEW_AGENT_MODE=1`):** no idle timer. The preview lives for the agent's turn; only `totalCapSeconds` applies as a safety net. The total cap always wins in every mode.
