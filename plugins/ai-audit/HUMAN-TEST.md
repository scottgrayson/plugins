# Human test steps (≤5 minutes on a Mac)

Run each audit once on a real setup. The CLI uses zero AI tokens.

**Before you start:** `cd` into a real project directory (not `~`). Context audit scopes to the current project.

**Install once:** run the **Claude Code block OR the Cursor block** below — not both. Each block sets its own `PLUGIN` / `AI_AUDIT`; do not let a second block overwrite the first.

---

## 0. Install and confirm the 3 skills (~2 min)

### Option A — Claude Code

While this PR is open / unmerged, add the branch marketplace (after merge, use plain `scottgrayson/plugins`):

```bash
claude plugin marketplace add 'scottgrayson/plugins#cursor/ai-audit-plugin-f41a'
# After merge to main, prefer:  claude plugin marketplace add scottgrayson/plugins
claude plugin install ai-audit@scottgrayson-plugins
claude plugin details ai-audit@scottgrayson-plugins
```

Expect: `Skills (3)` listing `context-audit`, `facts-audit`, `token-audit`.

```bash
PLUGIN="$(ls -d ~/.claude/plugins/cache/*/ai-audit/*/ 2>/dev/null | sort | tail -1)"
PLUGIN="${PLUGIN%/}"
test -f "$PLUGIN/bin/ai-audit"
AI_AUDIT="$PLUGIN/bin/ai-audit"
ls "$PLUGIN/skills/context-audit/SKILL.md" \
   "$PLUGIN/skills/token-audit/SKILL.md" \
   "$PLUGIN/skills/facts-audit/SKILL.md"
echo "PASS Claude install paths ($PLUGIN)"
```

### Option B — Cursor

1. Dashboard → Plugins / Team Marketplaces → add `https://github.com/scottgrayson/plugins` (or the PR branch URL while open)
2. Customize → Plugins → install **ai-audit**
3. Or local copy from a clone:

```bash
mkdir -p ~/.cursor/plugins/local
cp -R "$(pwd)/plugins/ai-audit" ~/.cursor/plugins/local/ai-audit
# Reload Window, then confirm skills under the Plugins UI
```

```bash
PLUGIN="$HOME/.cursor/plugins/local/ai-audit"
test -f "$PLUGIN/bin/ai-audit"
AI_AUDIT="$PLUGIN/bin/ai-audit"
ls "$PLUGIN/skills/context-audit/SKILL.md" \
   "$PLUGIN/skills/token-audit/SKILL.md" \
   "$PLUGIN/skills/facts-audit/SKILL.md"
echo "PASS Cursor install paths ($PLUGIN)"
```

Slash-invoke `/ai-audit:context-audit` (or the other two) once in the host UI if you want a UI check.

---

## 1. Context audit (~30 s)

```bash
cd /path/to/your/project   # required — do not run from ~
bash "$AI_AUDIT" context --host claude --md-only | head -40
bash "$AI_AUDIT" context --host cursor --md-only | head -40
bash "$AI_AUDIT" context --host grok --md-only | head -40
```

Expect: always-loaded list with real project paths (not `~/.claude/plugins/.../fixtures/...`). If more than 25 always-loaded items, a `+N more` line appears.

---

## 2. Token audit (~1.5 min) — one host at a time

**Claude Code:**

```bash
bash "$AI_AUDIT" token --host claude --md-only
```

**Cursor — file fallback (everyone):**

1. Cursor dashboard → **Usage** → download/export CSV (name varies; usually under `~/Downloads/`)
2. Resolve a real file with a zsh-safe glob (zsh is the Mac default):

```bash
# zsh: null_glob so a non-matching pattern does not abort the line
if [ -n "${ZSH_VERSION:-}" ]; then
  setopt null_glob
fi
CSV=""
for candidate in "$HOME"/Downloads/*[Uu]sage*.csv "$HOME"/Downloads/usage*.csv; do
  [ -f "$candidate" ] || continue
  CSV="$candidate"
  break
done
# Prefer newest if several matched via find (also zsh/bash safe):
if [ -z "$CSV" ]; then
  CSV="$(find "$HOME/Downloads" -maxdepth 1 -iname '*usage*.csv' -type f 2>/dev/null | sort | tail -1)"
fi
echo "Using CSV: ${CSV:-<none>}"
test -n "$CSV" && test -f "$CSV"
bash "$AI_AUDIT" token --host cursor --csv "$CSV" --md-only
```

Optional **Enterprise team admin only** (individual / Pro: skip):

```bash
export CURSOR_ADMIN_API_KEY=…   # team Admin API key — not a browser cookie
bash "$AI_AUDIT" token --host cursor --md-only
```

**Grok Bot** (no official auto-download):

```bash
printf '%s\n' '{"usage_total":72,"usage_period":"this week","unit":"percent"}' >/tmp/grok-usage.json
bash "$AI_AUDIT" token --host grok --usage-file /tmp/grok-usage.json --md-only
```

---

## 3. Facts audit (~30 s)

```bash
bash "$AI_AUDIT" facts --host claude --stale-days 30 --md-only | head -40
bash "$AI_AUDIT" facts --host cursor --stale-days 30 --md-only | head -40
bash "$AI_AUDIT" facts --host grok --stale-days 30 --md-only | head -40
```

---

## 4. Secrets stay redacted + bad CSV errors (~30 s)

Plant a realistic fake key; raw value count must be **0**. Checks print visible PASS/FAIL:

```bash
SECRET_ROOT="$(mktemp -d)"
FIX="$PLUGIN/fixtures/grok"
cp -R "$FIX" "$SECRET_ROOT/grok"
printf '%s\n' '- (2025-03-01) db password=Hunter2FakePass!99 crsr_fake0123456789abcdef0123456789' \
  >>"$SECRET_ROOT/grok/sand-data/user-memory/by-agent/agent-active/profile.md"

bash "$AI_AUDIT" facts --host grok --root "$SECRET_ROOT/grok" --json-only \
  | tee /tmp/ai-audit-secret-facts.json >/dev/null
bash "$AI_AUDIT" facts --host grok --root "$SECRET_ROOT/grok" --md-only \
  | tee /tmp/ai-audit-secret-facts.md >/dev/null

if [ "$(grep -c 'Hunter2FakePass' /tmp/ai-audit-secret-facts.json || true)" -eq 0 ]; then
  echo "PASS secrets json redacted"
else
  echo "FAIL secrets json leaked Hunter2FakePass"; exit 1
fi
if [ "$(grep -c 'crsr_fake' /tmp/ai-audit-secret-facts.json || true)" -eq 0 ]; then
  echo "PASS crsr_ json redacted"
else
  echo "FAIL crsr_ leaked"; exit 1
fi
if grep -q REDACTED /tmp/ai-audit-secret-facts.json; then
  echo "PASS REDACTED marker present"
else
  echo "FAIL missing REDACTED marker"; exit 1
fi

rm -rf "$SECRET_ROOT"
```

Bad CSV path must fail visibly (message is `does not exist`; exit code non-zero):

```bash
if bash "$AI_AUDIT" token --host cursor --csv /nonexistent/usage.csv --md-only 2>&1 \
    | tee /tmp/ai-audit-bad-csv.txt | grep -qi 'does not exist'; then
  echo "PASS bad csv message"
else
  echo "FAIL bad csv message missing"; exit 1
fi
if ! bash "$AI_AUDIT" token --host cursor --csv /nonexistent/usage.csv --json-only >/dev/null 2>&1; then
  echo "PASS bad csv exit non-zero"
else
  echo "FAIL bad csv exit was zero"; exit 1
fi
```
