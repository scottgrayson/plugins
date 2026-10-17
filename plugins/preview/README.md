# preview

Temporary public previews of a repo app on any branch or PR, through a free Cloudflare quick tunnel (`*.trycloudflare.com`).

Two modes share the same scripts:

1. **GitHub Actions** — tick a one-line checkbox at the top of the PR description.
2. **Agent** — run `preview up --agent` on the agent's own VM; stay in-session until the total cap or stop. Agents must not trigger the Actions workflow.

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
  "idleSeconds": 300,
  "totalCapSeconds": 900,
  "defaultSteps": [],
  "ignorePaths": [],
  "root": "."
}
```

Defaults when omitted: idle **5 minutes** (`300`), total max **15 minutes** (`900`). The total cap always wins.

Built-in ignore (always on): path wildcard `*poll*` plus static assets. Add repo-specific patterns with `ignorePaths` (fnmatch wildcards, e.g. `/healthz*`, `*/heartbeat`).

Named steps are scripts at `.preview/steps/<name>.sh` — never commands embedded in JSON. Optional `PREVIEW.md` for human check notes. Secrets never live in the repo; list names in `.env.example`.

PR labels `preview:<step>` select extra steps. Unknown steps fail with the valid list. With no labels, `defaultSteps` run. Non-PR branches work; PR-only checkbox/comment steps are skipped.

## Actions (consumer)

The shipped workflow listens only for the PR description checkbox (`pull_request` edited / opened / reopened). There is no `onDemand` flag and no triggers list in the plugin.

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

### Custom triggers

Repos that want other triggers (comment commands, labels, schedule, etc.) write their own workflow that calls the reusable workflow or composite action. Example sketch:

```yaml
name: Preview on comment
on:
  issue_comment:
    types: [created]
jobs:
  preview:
    if: github.event.issue.pull_request && contains(github.event.comment.body, '/preview')
    uses: scottgrayson/plugins/.github/workflows/preview-reusable.yml@main
    secrets: inherit
    with:
      working-directory: .
```

(You would still need a small job to mark the PR body control line / set `start=yes` the same way the shipped checkbox path does.)

PR description line 1 is exactly one of:

- `- [ ] Check to generate preview` (idle)
- `Preview deploy started at 11:52 AM ET` or `… (estimate 29s)` (loading; no checkbox)
- `[Preview Website](<url>) · created 11:52 AM ET · expires after 5m idle` (running; no checkbox)

Ticking the idle box starts a tiny `mark-loading` job (inline PATCH only). The estimate comes from `<!-- preview:last-deploy=29s -->` in the description when present; with no history there is no estimate text. At READY the running line is written once and the hidden last-deploy comment is updated. A separate `finalize` job resets to idle so the open PR page updates live. Stop reason goes to the job log only (no PR comments). GitHub strips `target=_blank` from PR bodies, so the Preview Website link is plain markdown (Cmd/Ctrl-click for a new tab).

## Agent

See [`skills/preview/SKILL.md`](skills/preview/SKILL.md). Agents run `preview up --agent` locally on their VM (no idle timer; total cap only) and must never tick the Actions checkbox. Stay in the turn until the preview stops, then report the reason.

## Keep-alive

A small reverse proxy sits between the tunnel and the app and logs each request. In Actions/CLI mode a single idle timer starts at READY and resets on counted traffic only. Never counted: default `*poll*`, `ignorePaths`, and static assets. No first-visit / grace-period logic. Agent mode disables idle entirely. The total cap always wins.

## v2

Named Cloudflare tunnels are out of scope for v1.
