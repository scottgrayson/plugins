# hello-world preview notes

- Confirm the greeting shows the PR number (or branch name).
- Click **Ping** — that hits `/ping` and counts as real usage.
- `/poll` runs every 29s from the page and is listed in `ignorePaths`, so it alone must not keep the preview alive.
- Idle is 30 seconds for automated keep-alive tests.
