# Human test steps (≤5 minutes on a Mac)

Run each audit once on a real setup. The CLI uses zero AI tokens.

## 0. Install and confirm the 3 skills (~2 min)

### Claude Code

```bash
claude plugin marketplace add scottgrayson/plugins
claude plugin install ai-audit@scottgrayson-plugins
claude plugin details ai-audit@scottgrayson-plugins
```

Expect: `Skills (3)` listing `context-audit`, `facts-audit`, `token-audit`.

Find the installed plugin root (newest cache):

```bash
PLUGIN="$(ls -d ~/.claude/plugins/cache/*/ai-audit/*/ 2>/dev/null | sort | tail -1)"
# strip trailing slash if present:
PLUGIN="${PLUGIN%/}"
test -f "$PLUGIN/bin/ai-audit"
AI_AUDIT="$PLUGIN/bin/ai-audit"
ls "$PLUGIN/skills/context-audit/SKILL.md" \
   "$PLUGIN/skills/token-audit/SKILL.md" \
   "$PLUGIN/skills/facts-audit/SKILL.md"
```

### Cursor

1. Dashboard → Plugins / Team Marketplaces → add `https://github.com/scottgrayson/plugins`
2. Customize → Plugins → install **ai-audit**
3. Or local copy:

```bash
mkdir -p ~/.cursor/plugins/local
# from a clone of scottgrayson/plugins:
cp -R "$(pwd)/plugins/ai-audit" ~/.cursor/plugins/local/ai-audit
# Reload Window, then confirm skills under the Plugins UI
```

```bash
PLUGIN="$HOME/.cursor/plugins/local/ai-audit"
# or, if installed from marketplace cache, pick the newest:
# PLUGIN="$(ls -d ~/.cursor/plugins/**/ai-audit 2>/dev/null | head -1)"
AI_AUDIT="$PLUGIN/bin/ai-audit"
ls "$PLUGIN/skills/context-audit/SKILL.md" \
   "$PLUGIN/skills/token-audit/SKILL.md" \
   "$PLUGIN/skills/facts-audit/SKILL.md"
```

Slash-invoke `/ai-audit:context-audit` (or the other two) once in each host if you want a UI check.

---

## 1. Context audit (~30 s)

```bash
bash "$AI_AUDIT" context --host claude --md-only | head -40
bash "$AI_AUDIT" context --host cursor --md-only | head -40
bash "$AI_AUDIT" context --host grok --md-only | head -40
```

Expect: always-loaded list with real paths. If more than 25 always-loaded items, a `+N more` line appears. No file changes.

---

## 2. Token audit (~1.5 min) — one host at a time

**Claude Code** (reads local JSONL under `~/.claude/projects/` or `$CLAUDE_CONFIG_DIR/projects/`):

```bash
bash "$AI_AUDIT" token --host claude --md-only
```

**Cursor — file fallback (everyone):**

1. Open the Cursor dashboard → **Usage**
2. Download / export the usage CSV (filename varies; often lands in `~/Downloads/`)
3. Pass the real file (glob is fine if only one match):

```bash
CSV="$(ls -t ~/Downloads/*usage*.csv ~/Downloads/usage*.csv 2>/dev/null | head -1)"
echo "Using CSV: $CSV"
test -f "$CSV"
bash "$AI_AUDIT" token --host cursor --csv "$CSV" --md-only
```

Optional **Enterprise team admin only** (individual / Pro: skip):

```bash
export CURSOR_ADMIN_API_KEY=…   # team Admin API key — not a browser cookie
bash "$AI_AUDIT" token --host cursor --md-only
```

**Grok Bot** (no official auto-download — provide a usage file):

```bash
printf '%s\n' '{"usage_total":72,"usage_period":"this week","unit":"percent"}' >/tmp/grok-usage.json
bash "$AI_AUDIT" token --host grok --usage-file /tmp/grok-usage.json --md-only
```

Expect: summary + reconciliation. Empty/no CSV → clear error or “no data”, not a silent success with `Matches: yes`.

---

## 3. Facts audit (~30 s)

```bash
bash "$AI_AUDIT" facts --host claude --stale-days 30 --md-only | head -40
bash "$AI_AUDIT" facts --host cursor --stale-days 30 --md-only | head -40
bash "$AI_AUDIT" facts --host grok --stale-days 30 --md-only | head -40
```

Expect: issues with a kind and a reason. No edits to memory files.

---

## 4. Secrets stay redacted (~30 s)

Plant a realistic fake key in a throwaway copy, then confirm the raw value never appears (count must be **0**):

```bash
SECRET_ROOT="$(mktemp -d)"
# Prefer the plugin fixtures if present next to the install:
FIX="$PLUGIN/fixtures/grok"
cp -R "$FIX" "$SECRET_ROOT/grok"
printf '%s\n' '- (2025-02-01) Old token sk-ant-FAKE1234567890abcdef' \
  >>"$SECRET_ROOT/grok/sand-data/user-memory/by-agent/agent-active/profile.md"

bash "$AI_AUDIT" facts --host grok --root "$SECRET_ROOT/grok" --json-only \
  | tee /tmp/ai-audit-secret-facts.json >/dev/null
bash "$AI_AUDIT" facts --host grok --root "$SECRET_ROOT/grok" --md-only \
  | tee /tmp/ai-audit-secret-facts.md >/dev/null

test "$(grep -c 'FAKE1234567890abcdef' /tmp/ai-audit-secret-facts.json || true)" -eq 0
test "$(grep -c 'FAKE1234567890abcdef' /tmp/ai-audit-secret-facts.md || true)" -eq 0
grep -q REDACTED /tmp/ai-audit-secret-facts.json

rm -rf "$SECRET_ROOT"
```

Also confirm a bad CSV path is not silent:

```bash
bash "$AI_AUDIT" token --host cursor --csv /nonexistent/usage.csv --md-only | grep -i 'not found'
```
