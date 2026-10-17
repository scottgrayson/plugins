# plugins

MIT-licensed plugins by Scott Grayson.

## preview

Temporary public previews of a repo app on any branch or PR, exposed through a free Cloudflare quick tunnel (`trycloudflare.com`).

- Plugin: [`plugins/preview/`](plugins/preview/)
- Example app: [`examples/hello-world/`](examples/hello-world/)
- Cursor marketplace: [`.cursor-plugin/marketplace.json`](.cursor-plugin/marketplace.json)
- Claude Code marketplace: [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json)

### Quick start (Actions)

Consumer repos call the reusable workflow:

```yaml
jobs:
  preview:
    uses: scottgrayson/plugins/.github/workflows/preview-reusable.yml@main
    secrets: inherit
    with:
      working-directory: .
```

This repository uses it on itself to preview `examples/hello-world`.

### Quick start (agent / CLI)

Agents run previews locally on their VM — never tick the Actions checkbox:

```bash
cd examples/hello-world
../../plugins/preview/bin/preview up --agent
```

Defaults: idle 5 minutes (Actions/CLI only), total max 15 minutes. Agent mode disables idle.

Named Cloudflare tunnels are planned for v2.
