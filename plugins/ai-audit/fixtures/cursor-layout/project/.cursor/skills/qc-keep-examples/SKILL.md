---
name: qc-keep-examples
description: Use when verifying that example paths in code and sandbox paths are not reported.
---
Fenced project-relative examples:

```ts
import "./src/env.d.ts";
const sql = readFile("./schema.sql");
```

Inline `./node_modules/.bin/tool.js` and `./config/app.json` are examples too.

Sandbox paths: /workspace/app.py and /app/server.py and `/workspace/project/main.py`.
Temp output lands in /tmp/example-run/package/index.d.ts and `/tmp/out.json`.
Absolute `/etc/example/settings.toml` is not ours to check.
Skill-owned file that exists: see references/present.md and [guide](./references/present.md).
Web link [docs](https://example.org/a/guide.md) and anchor [top](#intro).
