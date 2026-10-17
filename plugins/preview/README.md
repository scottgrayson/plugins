# preview

Temporary public previews of a repo app on any branch or PR via a free Cloudflare quick tunnel (`*.trycloudflare.com`).

Two modes share the same scripts:

1. **Actions mode** — humans tick a PR checkbox; GitHub Actions runs the tunnel (no AI).
2. **Agent mode** — install this plugin in Cursor or Claude Code; the agent runs `preview up --agent` on its own VM (never ticks the Actions checkbox).

Secrets are not supported in v1; previews work for apps that need no secrets.

All repo-side preview files live under `.preview/` (`config.json`, `steps/`, optional `README.md`).

Full marketplace install guide: [root README](../../README.md). This page matches that guide and adds plugin-local notes.

---

## Actions mode (no AI)

### 1. Repo setup

Commit next to your app:

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

**Steps** (optional): `.preview/steps/<name>.sh`. JSON never embeds commands. PR label `preview:migrate` runs `.preview/steps/migrate.sh`. Unknown labels fail with the valid list. With no labels, `defaultSteps` run.

**Optional `.preview/README.md`**: human check notes.

### 2. Workflow

Add `.github/workflows/preview.yml`. Pin `@v1` when released; until then `@main`. This marketplace dogfoods the same shape (relative `uses`, `working-directory: examples/preview`):

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

| Event | Job | Effect |
| --- | --- | --- |
| PR `opened` / `reopened` | `ensure-checkbox` | Inserts `- [ ] Check to generate preview` as line 1 if missing |
| PR `edited` with box ticked | `mark-loading` → `preview` → `finalize` | Loading line → public URL → idle checkbox restored |

You can add the checkbox yourself as PR body line 1; the ensure job is a convenience. The shipped workflow’s only start trigger is ticking that box.

### 3. Use it

Open a PR → confirm line 1 is `- [ ] Check to generate preview` → tick the box → wait for `[Preview Website](<url>) · created … · expires after 5m idle`. Finalize restores the idle checkbox. Stop reason is in the job log only.

PR description line 1 is exactly one of:

- `- [ ] Check to generate preview` (idle)
- `Preview deploy started at 11:52 AM ET` or `… (estimate 29s)` (loading; estimate only when `<!-- preview:last-deploy=… -->` exists)
- `[Preview Website](<url>) · created 11:52 AM ET · expires after 5m idle` (running)

GitHub strips `target=_blank` from PR bodies; use Cmd/Ctrl-click for a new tab.

### Custom triggers

No `onDemand` flag and no triggers list. For comment commands, labels, schedule, etc., write your own workflow that calls the reusable workflow or composite action.

---

## Agent mode (plugin)

Agents run the tunnel on their own VM and **must not** tick the Actions checkbox.

### Install — Claude Code

Marketplace name: `scottgrayson-plugins`.

```text
/plugin marketplace add scottgrayson/plugins
/plugin install preview@scottgrayson-plugins
```

```bash
claude plugin marketplace add scottgrayson/plugins
claude plugin install preview@scottgrayson-plugins
```

```text
/plugin install preview --marketplace scottgrayson/plugins
```

### Install — Cursor

Per [Cursor plugins docs](https://cursor.com/docs/plugins):

1. **Dashboard → Plugins & MCPs → Team Marketplaces → Add Marketplace → Import from Repo**
2. Paste `https://github.com/scottgrayson/plugins` (Teams/Enterprise)
3. **Customize** → find **preview** → **Install**

Local smoke-test: copy this plugin to `~/.cursor/plugins/local/preview` and reload. `/add-plugin` only works for plugins on [cursor.com/marketplace](https://cursor.com/marketplace).

### Run in a consumer repo

```bash
preview up --agent --config .preview/config.json --workdir .
```

From a checkout of this marketplace:

```bash
bin/preview up --agent --config /path/to/app/.preview/config.json --workdir /path/to/app
```

`--agent` disables idle; only `totalCapSeconds` applies. See [`skills/preview/SKILL.md`](skills/preview/SKILL.md).

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
| Idle after READY | **5 minutes** (`300`) | **Off** |
| Total max | **15 minutes** (`900`) | Same — safety net |
| Cap vs idle | Cap always wins | Cap only |

Built-in ignore: `*poll*` + static assets. Single idle timer from READY; resets on counted requests only.

---

## Config reference

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

CLI only (no plugin install): run `bin/preview` from this package. Workflow ref: prefer `@v1` once released; `@main` until then.

## v2

Named Cloudflare tunnels are out of scope for v1.
