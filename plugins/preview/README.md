# preview

Temporary public previews of a repo app on any branch or PR, through a free Cloudflare quick tunnel (`*.trycloudflare.com`).

Two modes share the same scripts:

1. **GitHub Actions** — tick a one-line checkbox at the top of the PR description.
2. **Agent** — ask an agent with this plugin installed to preview a PR; it runs the same CLI and stays in-session until the watchdog stops.

## Install

- **Cursor:** add this repo as a marketplace (`.cursor-plugin/marketplace.json`) and install `preview`.
- **Claude Code:** add this repo as a marketplace (`.claude-plugin/marketplace.json`) and install `preview`.
- **CLI only:** run `plugins/preview/bin/preview` from a checkout.

## Repo setup

Commit a `.preview.json` next to your app (plain values only):

```json
{
  "type": "static",
  "start": "",
  "port": 4173,
  "idleSeconds": 900,
  "totalCapSeconds": 7200,
  "defaultSteps": [],
  "ignorePaths": [],
  "root": "."
}
```

Named steps are scripts at `.preview/steps/<name>.sh` — never commands embedded in JSON. Optional `PREVIEW.md` for human check notes. Secrets never live in the repo; list names in `.env.example`.

PR labels `preview:<step>` select extra steps. Unknown steps fail with the valid list. With no labels, `defaultSteps` run. Non-PR branches work; PR-only checkbox/comment steps are skipped.

## Actions (consumer)

```yaml
name: Preview
on:
  pull_request:
    types: [opened, edited, reopened]
permissions:
  contents: read
  pull-requests: write
jobs:
  preview:
    uses: scottgrayson/plugins/.github/workflows/preview-reusable.yml@main
    secrets: inherit
    with:
      working-directory: .
      config: .preview.json
```

Or call the composite action at `scottgrayson/plugins/plugins/preview@main` (see this repo's `.github/workflows/preview.yml`).

PR description line 1 is exactly one of:

- `- [ ] Check to generate preview` (idle)
- `Preview deploy started at 11:52 AM ET` or `… (estimate 29s)` (loading; no checkbox)
- `[Preview Website](<url>) · created 11:52 AM ET · expires after 30s idle` (running; no checkbox)

Ticking the idle box starts a tiny `mark-loading` job (inline PATCH only). The estimate comes from `<!-- preview:last-deploy=29s -->` in the description when present; with no history there is no estimate text. At READY the running line is written once and the hidden last-deploy comment is updated. A separate `finalize` job resets to idle so the open PR page updates live. Stop reason goes to the job log only (no PR comments). GitHub strips `target=_blank` from PR bodies, so the Preview Website link is plain markdown (Cmd/Ctrl-click for a new tab).

## Agent

See [`skills/preview/SKILL.md`](skills/preview/SKILL.md). Stay in the turn until the preview stops, then report the reason.

## Keep-alive

A small reverse proxy sits between the tunnel and the app and logs each request. Only real usage resets the idle timer. Never counted: `ignorePaths`, static assets, and best-effort steady-interval polling. The total cap always wins.

## v2

Named Cloudflare tunnels are out of scope for v1.
