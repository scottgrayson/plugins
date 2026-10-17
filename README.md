# plugins

MIT-licensed plugins by Scott Grayson. This marketplace currently ships **preview**: temporary public previews of a repo app on any branch or PR via a free Cloudflare quick tunnel (`*.trycloudflare.com`).

Two modes share the same scripts:

1. **Actions mode** — humans tick a PR checkbox; GitHub Actions runs the tunnel (no AI).
2. **Agent mode** — install the plugin in Cursor or Claude Code; the agent runs `preview up --agent` on its own VM (never ticks the Actions checkbox).

Plugin package: [`plugins/preview/`](plugins/preview/) · Example: [`examples/preview/`](examples/preview/)

Secrets are not supported in v1; previews work for apps that need no secrets.

All repo-side preview files live under `.preview/` (`config.json`, `steps/`, optional `README.md`).

---

## Actions mode (no AI)

### 1. Repo setup

Commit these next to your app:

**`.preview/config.json`** (all fields optional; defaults shown):

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

| Field | Meaning |
| --- | --- |
| `type` | `static` (built-in file server) or any label; non-static requires `start` |
| `start` | Shell command to boot the app (receives `PORT`) |
| `port` | App listen port |
| `idleSeconds` | Idle timeout after READY (Actions/CLI). Ignored in agent mode |
| `totalCapSeconds` | Hard max lifetime; **always wins** over idle |
| `defaultSteps` | Step names to run when the PR has no `preview:<step>` labels |
| `ignorePaths` | Extra fnmatch patterns never counted as usage (merged with built-in `*poll*` + static assets) |
| `root` | Doc root for `type: static` when `start` is empty |

**Steps** (optional): scripts at `.preview/steps/<name>.sh`. JSON never embeds commands — only step names. PR label `preview:migrate` runs `.preview/steps/migrate.sh`. Unknown labels fail with the valid list. With no labels, `defaultSteps` run.

**Optional `.preview/README.md`**: human check notes for the preview.

### 2. Workflow

Add `.github/workflows/preview.yml`. Pin a release tag when one exists (`@v1`); until then use `@main`. This marketplace dogfoods the same shape in [`.github/workflows/preview.yml`](.github/workflows/preview.yml) (relative `uses`, `working-directory: examples/preview`):

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
    with:
      working-directory: .
      config: .preview/config.json
```

What this wires:

| Event | Job | Effect |
| --- | --- | --- |
| PR `opened` / `reopened` | `ensure-checkbox` | Inserts `- [ ] Check to generate preview` as line 1 if missing |
| PR `edited` with box ticked | `mark-loading` → `preview` → `finalize` | Loading line → public URL → idle checkbox restored |

You can also add the checkbox yourself as the first line of the PR body; the ensure job is a convenience. Ticking it (PR body edit) is the only trigger the shipped workflow listens for.

### 3. Use it

Open a PR → confirm line 1 is `- [ ] Check to generate preview` → tick the box → wait for `[Preview Website](<url>) · created … · expires after 5m idle`. When the preview stops, finalize resets the idle checkbox. Stop reason is in the job log only (no PR comments).

### Custom triggers

No `onDemand` flag and no triggers list in the plugin. For comment commands, labels, schedule, etc., write your own workflow that calls the same reusable workflow or composite action.

### Security

Fork PRs are skipped (`head.repo.full_name == github.repository`); the workflow listens on `pull_request` only (never `pull_request_target`). Quick-tunnel URLs are public to anyone with the link (no login), so use preview-only data. Secrets are not supported in v1.

---

## Agent mode (plugin)

Agents run the tunnel on their own VM and **must not** tick the Actions checkbox or otherwise start the preview workflow.

### Install — Claude Code

Marketplace name in this repo: `scottgrayson-plugins` (see [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json)).

In a Claude Code session:

```text
/plugin marketplace add scottgrayson/plugins
/plugin install preview@scottgrayson-plugins
```

Or from your shell:

```bash
claude plugin marketplace add scottgrayson/plugins
claude plugin install preview@scottgrayson-plugins
```

One-shot (adds the marketplace if needed):

```text
/plugin install preview --marketplace scottgrayson/plugins
```

### Install — Cursor

This repo is a Cursor team marketplace catalog ([`.cursor-plugin/marketplace.json`](.cursor-plugin/marketplace.json)). Per [Cursor plugins docs](https://cursor.com/docs/plugins):

1. **Dashboard → Plugins & MCPs → Team Marketplaces → Add Marketplace → Import from Repo**
2. Paste `https://github.com/scottgrayson/plugins` (requires Teams/Enterprise for team marketplaces)
3. Open **Customize** in the sidebar → find **preview** → **Install** (user or project scope)

For local smoke-testing without a team marketplace, copy the plugin into `~/.cursor/plugins/local/preview` (must contain `.cursor-plugin/plugin.json`) and reload the window. Official Marketplace `/add-plugin` only works for plugins listed on [cursor.com/marketplace](https://cursor.com/marketplace).

### Run in a consumer repo

1. Same `.preview/config.json` / steps as Actions mode.
2. Ask the agent to preview the PR/branch, or run the CLI from the installed plugin (skill locates `bin/preview`):

```bash
preview up --agent --config .preview/config.json --workdir .
```

From a checkout of this marketplace (without installing):

```bash
plugins/preview/bin/preview up --agent --config .preview/config.json --workdir path/to/app
```

`up --agent` disables the idle timer; only `totalCapSeconds` applies. Stay in the turn until it stops, then report the reason. See [`plugins/preview/skills/preview/SKILL.md`](plugins/preview/skills/preview/SKILL.md).

### Optional: repo agent rules

A consumer repo can point its own agent rules at the plugin with a neutral one-liner in `AGENTS.md`, `CLAUDE.md`, or `.cursor/rules` — for example:

```text
To deploy a preview for testing, invoke `/preview` (or run `preview up --agent`).
```

The skill sets `disable-model-invocation: true`, so it never auto-fires from its description — only via explicit `/preview` (or a repo rule that tells the agent to invoke it).

---

## Defaults

| | Actions / CLI | Agent (`--agent`) |
| --- | --- | --- |
| Idle after READY | **5 minutes** (`idleSeconds: 300`) | **Off** |
| Total max | **15 minutes** (`totalCapSeconds: 900`) | Same — safety net |
| Cap vs idle | Cap always wins | Cap only |

Built-in ignore (always on): path wildcard `*poll*` plus static assets. Add more via `ignorePaths`. Single idle timer starts at READY and resets on counted requests only — no grace period.

---

## Config reference

Minimal static site:

```json
{
  "type": "static",
  "root": ".",
  "port": 4173
}
```

App with a start command and ignored health checks:

```json
{
  "type": "server",
  "start": "npm run start",
  "port": 3000,
  "idleSeconds": 300,
  "totalCapSeconds": 900,
  "defaultSteps": ["seed"],
  "ignorePaths": ["/healthz*", "*/heartbeat"]
}
```

```bash
# .preview/steps/seed.sh
#!/usr/bin/env bash
set -euo pipefail
echo "seed ok"
```

Workflow inputs (`working-directory`, `config`) are relative to the consumer repo checkout. Ref: prefer `@v1` once released; `@main` until then.

More detail: [`plugins/preview/README.md`](plugins/preview/README.md). Named Cloudflare tunnels are out of scope for v1.
