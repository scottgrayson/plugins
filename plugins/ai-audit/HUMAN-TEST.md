# Human test steps (≤5 minutes)

Run each audit once on a real setup. No AI tokens required for the CLI itself.

## Prerequisites

- This repo (or an installed copy of `plugins/ai-audit`)
- `python3` on PATH

```bash
PLUGIN=/path/to/plugins/ai-audit   # or your install root
AI_AUDIT="$PLUGIN/bin/ai-audit"
```

Confirm the three skills exist:

```bash
ls "$PLUGIN/skills/context-audit/SKILL.md" \
   "$PLUGIN/skills/token-audit/SKILL.md" \
   "$PLUGIN/skills/facts-audit/SKILL.md"
```

---

## 1. Context audit (~1 min)

```bash
bash "$AI_AUDIT" context --host auto --md-only
# Or force a host / fixture:
bash "$AI_AUDIT" context --host claude --root "$PLUGIN/fixtures/claude" --md-only
```

**Expect:** inventory of always-loaded items with paths that exist on disk under the scanned roots. No file changes.

---

## 2. Token audit (~2 min)

**Claude Code (auto JSONL):**

```bash
bash "$AI_AUDIT" token --host claude --md-only
```

**Cursor (file fallback — download Usage CSV from the dashboard first):**

```bash
bash "$AI_AUDIT" token --host cursor --csv ~/Downloads/usage.csv --md-only
# Optional auto: export CURSOR_ADMIN_API_KEY=… then omit --csv
```

**Grok Bot (no official auto — provide a usage file):**

```bash
printf '%s\n' '{"usage_total":72,"usage_period":"this week","unit":"percent"}' >/tmp/grok-usage.json
bash "$AI_AUDIT" token --host grok --usage-file /tmp/grok-usage.json --md-only
```

**Expect:** summary + reconciliation (`Matches: yes`). Grok without a file should say official auto is not possible and ask for a file.

---

## 3. Facts audit (~1 min)

```bash
bash "$AI_AUDIT" facts --host grok --root "$PLUGIN/fixtures/grok" --stale-days 30 --md-only
# Or on a live Grok sand-data / Claude memory / Cursor --memories export:
bash "$AI_AUDIT" facts --host auto --stale-days 30 --md-only
```

**Expect:** issues listed with a kind (`stale` / `duplicate` / `conflict` / `orphan`) and a reason. No edits to memory files.

---

## 4. Read-only + secrets (~30 s)

```bash
bash "$AI_AUDIT" context --host claude --root "$PLUGIN/fixtures/claude" --json-only | grep -i secret || true
# Confirm no SHOULD_NOT_APPEAR / credential values leak from fixtures
```

**Expect:** secrets redacted/skipped; working tree unchanged (`git status` clean if you only ran audits).
