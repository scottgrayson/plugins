---
name: preview
description: Start, report, and wait on a temporary Cloudflare quick-tunnel preview of a repo app or PR. Use when asked to preview a PR, branch, or local app.
---

# preview

Share the same scripts Actions mode uses. No AI is required to keep the tunnel up — you only orchestrate them and stay in the turn until shutdown.

## Locate the plugin

Prefer the installed plugin copy. From a checkout of this marketplace repo:

```bash
PREVIEW_BIN="$PWD/plugins/preview/bin/preview"
chmod +x "$PREVIEW_BIN"
```

If the plugin is installed elsewhere, set `PREVIEW_BIN` to that install's `bin/preview`.

## Config

Repo config is `.preview.json` (plain values only). Named steps live at `.preview/steps/<name>.sh`. Optional human notes: `PREVIEW.md`. Secrets come from env vars (see `.env.example` names only) — never from the repo.

Defaults when omitted: `type=static`, `idleSeconds=900`, `totalCapSeconds=7200`.

## Commands

```bash
# Start (blocks until idle / total cap / stop / crash)
PREVIEW_PR_NUMBER=<n> PREVIEW_BRANCH=<name> \
  "$PREVIEW_BIN" up --config .preview.json --labels "<csv labels>" --workdir <app-dir>

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

1. Start the preview with `preview up` **in the foreground of this turn** (or background it and immediately `preview wait`).
2. As soon as the URL is known, reply to the human with the link (and mention they can refresh after your edits).
3. **Do not end the turn while the preview is up.** Keep waiting on the watchdog until status is `stopped`.
4. When it stops, report `preview stopped` with the reason from `preview status` / stderr (`idle`, `total-cap`, `stop`, `app-exit`, `tunnel-exit`, …).

Live edits on static / file-served apps appear on the same URL after refresh. Prefer editing files in place; do not restart the tunnel unless the start command itself must change.

## Previewing a PR

1. Check out the PR head (or work in the already-checked-out PR branch).
2. `cd` to the app directory that has `.preview.json` (for this repo: `examples/hello-world`).
3. Export any secrets named in `.env.example`.
4. Set `PREVIEW_PR_NUMBER` from the PR.
5. Pass PR labels via `--labels` if present (`preview:<step>` only). Unknown steps must fail with the valid list — do not invent commands.
6. Run `up`, report the URL, stay until stopped, then report the stop reason.

## Keep-alive

A reverse proxy logs every request. Only counted traffic resets the idle timer (page loads, form posts, user XHR). Never counted: `ignorePaths`, static assets, and best-effort steady-interval polling. The total cap always wins.
