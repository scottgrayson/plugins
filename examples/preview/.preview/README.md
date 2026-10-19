# preview example notes

- Confirm the greeting shows the PR number (or branch name).
- Click **Ping** — that hits `/ping` and counts as real usage (resets the idle timer).
- `/poll` runs every 29s from the page. The plugin's built-in `*poll*` ignore matches it, so poll alone must not keep the preview alive.
- Defaults (Actions/CLI): idle **5 minutes**, total max **15 minutes**. The total cap always wins.
- Agent mode (`preview up --agent --detach`): localhost only (no tunnel); no timers. Tear down with `preview stop`. Use `--share` / `preview share` when a human needs a public link.
