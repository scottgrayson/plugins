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
claude plugin install ai-audit@scottgrayson-plugins
claude plugin details ai-audit@scottgrayson-plugins
```

After merge to main, prefer: `claude plugin marketplace add scottgrayson/plugins`.

Expect: `Skills (3)` listing `context-audit`, `facts-audit`, `token-audit`.

```bash
PLUGIN="$(find "$HOME/.claude/plugins/cache" -path '*/ai-audit/*/bin/ai-audit' -type f 2>/dev/null | sort | tail -1)"
PLUGIN="${PLUGIN%/bin/ai-audit}"
if test -n "$PLUGIN" && test -f "$PLUGIN/bin/ai-audit" \
  && ls "$PLUGIN/skills/context-audit/SKILL.md" \
       "$PLUGIN/skills/token-audit/SKILL.md" \
       "$PLUGIN/skills/facts-audit/SKILL.md" >/dev/null 2>&1; then
  AI_AUDIT="$PLUGIN/bin/ai-audit"
  echo "PASS Claude install paths ($PLUGIN)"
else
  echo "FAIL Claude install paths (${PLUGIN:-missing})"
fi
```

### Option B — Cursor

1. Dashboard → Plugins / Team Marketplaces → add `https://github.com/scottgrayson/plugins` (or the PR branch URL while open)
2. Customize → Plugins → install **ai-audit** (marketplace copies land under `~/.cursor/plugins/cache/…/ai-audit/`)
3. Or local copy from a clone (path-independent — replace `SRC` with your checkout). Only removes the prior local copy when the source directory exists (so a leftover `/path/to` placeholder does not wipe an install):

```bash
SRC=/path/to/plugins/ai-audit
if [ -d "$SRC" ]; then
  mkdir -p ~/.cursor/plugins/local
  rm -rf ~/.cursor/plugins/local/ai-audit
  cp -R "$SRC" ~/.cursor/plugins/local/ai-audit
  echo "PASS Cursor local copy"
else
  echo "FAIL Cursor source missing or still placeholder ($SRC)"
fi
```

Reload Window, then confirm skills under the Plugins UI.

Prefer the local copy when present; otherwise fall back to the newest marketplace/cache install under `~/.cursor/plugins/cache/` (versioned `…/ai-audit/<ver>/bin/ai-audit` or flat `…/ai-audit/bin/ai-audit`):

```bash
PLUGIN="$HOME/.cursor/plugins/local/ai-audit"
if [ ! -f "$PLUGIN/bin/ai-audit" ]; then
  PLUGIN="$(find "$HOME/.cursor/plugins/cache" \
    \( -path '*/ai-audit/*/bin/ai-audit' -o -path '*/ai-audit/bin/ai-audit' \) \
    -type f 2>/dev/null | sort | tail -1)"
  PLUGIN="${PLUGIN%/bin/ai-audit}"
fi
if test -n "$PLUGIN" && test -f "$PLUGIN/bin/ai-audit" \
  && ls "$PLUGIN/skills/context-audit/SKILL.md" \
       "$PLUGIN/skills/token-audit/SKILL.md" \
       "$PLUGIN/skills/facts-audit/SKILL.md" >/dev/null 2>&1; then
  AI_AUDIT="$PLUGIN/bin/ai-audit"
  echo "PASS Cursor install paths ($PLUGIN)"
else
  echo "FAIL Cursor install paths (${PLUGIN:-missing})"
fi
```

Slash-invoke `/ai-audit:context-audit` (or the other two) once in the host UI if you want a UI check.

---

## 1. Context audit (~30 s)

Required — do not run from `~`; use your real project path:

```bash
cd /path/to/your/project
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  bash "$AI_AUDIT" context --host claude --md-only | head -40
  bash "$AI_AUDIT" context --host cursor --md-only | head -40
  bash "$AI_AUDIT" context --host grok --md-only | head -40
fi
```

Expect: always-loaded list with real project paths (not `~/.claude/plugins/.../fixtures/...`). If more than 25 always-loaded items, a `+N more` line appears.

---

## 2. Token audit (~1.5 min) — one host at a time

**Claude Code:**

```bash
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  bash "$AI_AUDIT" token --host claude --md-only
fi
```

**Cursor — file fallback (everyone):**

1. Cursor dashboard → **Usage** → download/export CSV (name varies; usually under `~/Downloads/`)
2. Resolve a real file with a zsh-safe glob (zsh is the Mac default).

zsh: `null_glob` so a non-matching pattern does not abort the line:

```bash
if [ -n "${ZSH_VERSION:-}" ]; then
  setopt null_glob
fi
CSV=""
for candidate in "$HOME"/Downloads/*[Uu]sage*.csv "$HOME"/Downloads/usage*.csv; do
  [ -f "$candidate" ] || continue
  CSV="$candidate"
  break
done
```

Prefer newest if several matched via find (also zsh/bash safe):

```bash
if [ -z "$CSV" ]; then
  CSV="$(find "$HOME/Downloads" -maxdepth 1 -iname '*usage*.csv' -type f 2>/dev/null | sort | tail -1)"
fi
echo "Using CSV: ${CSV:-<none>}"
test -n "$CSV" && test -f "$CSV"
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  bash "$AI_AUDIT" token --host cursor --csv "$CSV" --md-only
fi
```

Optional **Enterprise team admin only** (individual / Pro: skip). Export a team Admin API key — not a browser cookie:

```bash
export CURSOR_ADMIN_API_KEY=…
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  bash "$AI_AUDIT" token --host cursor --md-only
fi
```

**Grok Bot** (no official auto-download):

```bash
printf '%s\n' '{"usage_total":72,"usage_period":"this week","unit":"percent"}' >/tmp/grok-usage.json
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  bash "$AI_AUDIT" token --host grok --usage-file /tmp/grok-usage.json --md-only
fi
```

---

## 3. Facts audit (~30 s)

```bash
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  bash "$AI_AUDIT" facts --host claude --stale-days 30 --md-only | head -40
  bash "$AI_AUDIT" facts --host cursor --stale-days 30 --md-only | head -40
  bash "$AI_AUDIT" facts --host grok --stale-days 30 --md-only | head -40
fi
```

---

## 4. Secrets stay redacted + bad CSV errors (~30 s)

Plant a realistic fake key; raw value count must be **0**. Checks print visible PASS/FAIL (use `echo FAIL` — do not `exit`, which closes an interactive terminal):

```bash
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
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
    echo "FAIL secrets json leaked Hunter2FakePass"
  fi
  if [ "$(grep -c 'crsr_fake' /tmp/ai-audit-secret-facts.json || true)" -eq 0 ]; then
    echo "PASS crsr_ json redacted"
  else
    echo "FAIL crsr_ leaked"
  fi
  if grep -q REDACTED /tmp/ai-audit-secret-facts.json; then
    echo "PASS REDACTED marker present"
  else
    echo "FAIL missing REDACTED marker"
  fi

  rm -rf "$SECRET_ROOT"
fi
```

Bad CSV path must fail visibly (message is `does not exist`; exit code non-zero):

```bash
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  if bash "$AI_AUDIT" token --host cursor --csv /nonexistent/usage.csv --md-only 2>&1 \
      | tee /tmp/ai-audit-bad-csv.txt | grep -qi 'does not exist'; then
    echo "PASS bad csv message"
  else
    echo "FAIL bad csv message missing"
  fi
  if ! bash "$AI_AUDIT" token --host cursor --csv /nonexistent/usage.csv --json-only >/dev/null 2>&1; then
    echo "PASS bad csv exit non-zero"
  else
    echo "FAIL bad csv exit was zero"
  fi
fi
```
