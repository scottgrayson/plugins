# Human test steps — stage 1 context (≤3 minutes on a phone or Mac)

Run the **context** audit once on a real setup. The CLI uses zero AI tokens.

**Before you start:** `cd` into a real project directory (not `~`). Context audit scopes to the current project.

**Install once:** run the **Claude Code block OR the Cursor block** below — not both. Each block sets its own `PLUGIN` / `AI_AUDIT`; do not let a second block overwrite the first.

---

## 0. Install and confirm the context skill

**Tokenizer (once per machine):** Cursor context counts need the `tiktoken` package (ranks are vendored; install is offline after pip fetch):

```bash
python3 -m pip install --user 'tiktoken==0.14.0'
python3 -c "import tiktoken; print('PASS tiktoken', tiktoken.__version__)"
```

If Cursor context exits with a one-line `ai-audit: install the offline tokenizer…` message, re-run the pip line above (no traceback should appear).

### Option A — Claude Code

While this PR is open / unmerged, add the branch marketplace (after merge, use plain `scottgrayson/plugins`):

```bash
claude plugin marketplace add 'scottgrayson/plugins#cursor/ai-audit-plugin-f41a'
claude plugin install ai-audit@scottgrayson-plugins
claude plugin details ai-audit@scottgrayson-plugins
```

Expect: `Skills (1)` listing `context-audit`.

```bash
PLUGIN="$(find "$HOME/.claude/plugins/cache" -path '*/ai-audit/*/bin/ai-audit' -type f 2>/dev/null | sort | tail -1)"
PLUGIN="${PLUGIN%/bin/ai-audit}"
if test -n "$PLUGIN" && test -f "$PLUGIN/bin/ai-audit" \
  && ls "$PLUGIN/skills/context-audit/SKILL.md" >/dev/null 2>&1; then
  AI_AUDIT="$PLUGIN/bin/ai-audit"
  echo "PASS Claude install paths ($PLUGIN)"
else
  echo "FAIL Claude install paths (${PLUGIN:-missing})"
fi
```

### Option B — Cursor

1. Dashboard → Plugins / Team Marketplaces → add `https://github.com/scottgrayson/plugins` (or the PR branch URL while open)
2. Customize → Plugins → install **ai-audit**
3. Or local copy from a clone (path-independent — replace `SRC` with your checkout). Only removes the prior local copy when the source directory exists:

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

Reload Window. Prefer the local copy when present; otherwise fall back to the newest marketplace/cache install:

```bash
PLUGIN="$HOME/.cursor/plugins/local/ai-audit"
if [ ! -f "$PLUGIN/bin/ai-audit" ]; then
  PLUGIN="$(find "$HOME/.cursor/plugins/cache" \
    \( -path '*/ai-audit/*/bin/ai-audit' -o -path '*/ai-audit/bin/ai-audit' \) \
    -type f 2>/dev/null | sort | tail -1)"
  PLUGIN="${PLUGIN%/bin/ai-audit}"
fi
if test -n "$PLUGIN" && test -f "$PLUGIN/bin/ai-audit" \
  && ls "$PLUGIN/skills/context-audit/SKILL.md" >/dev/null 2>&1; then
  AI_AUDIT="$PLUGIN/bin/ai-audit"
  echo "PASS Cursor install paths ($PLUGIN)"
else
  echo "FAIL Cursor install paths (${PLUGIN:-missing})"
fi
```

Slash-invoke `/ai-audit:context-audit` once in the host UI if you want a UI check.

---

## 1. Context audit (grouped table)

Required — do not run from `~`; use your real project path. Cursor hosts should see the grouped startup table (Always-apply rules, User Rules, Skill descriptions, MCP, Model/settings):

```bash
cd /path/to/your/project
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  OUT="$(bash "$AI_AUDIT" context --host auto --md-only | head -80)"
  printf '%s\n' "$OUT"
  if printf '%s\n' "$OUT" | grep -qE 'Context audit|Startup context report'; then
    echo "PASS context audit ran"
  else
    echo "FAIL context audit output missing"
  fi
  if printf '%s\n' "$OUT" | grep -qE 'Cursor · |Claude Code · |Grok Bot · '; then
    echo "PASS provider · environment heading"
  else
    echo "FAIL missing provider · environment heading"
  fi
  if printf '%s\n' "$OUT" | grep -qE 'tiktoken o200k|Tokenizer:'; then
    echo "PASS tokenizer labeled"
  else
    echo "FAIL tokenizer label missing"
  fi
  if printf '%s\n' "$OUT" | grep -qE 'Always-apply rules|Always-loaded|Skill descriptions'; then
    echo "PASS grouped or inventory section present"
  else
    echo "FAIL missing report sections"
  fi
  if printf '%s\n' "$OUT" | grep -q 'Startup context report'; then
    if printf '%s\n' "$OUT" | grep -qiE 'sorted by tokens|Sort window'; then
      echo "PASS sort order noted"
    else
      echo "FAIL sort legend missing"
    fi
  else
    echo "PASS sort check skipped (non-Cursor inventory report)"
  fi
fi
```

On Cursor, expect a heading like `Cursor · Mac` or `Cursor · Cloud`, columns `Item | Tokens | Purpose | AI suggestion` (Purpose/suggestion blank for the agent), groups ordered by subtotal descending, and rows within each group by tokens descending.

---

## 2. Secrets stay redacted in context output

Plant a fake secret in a Grok bot `name` field (inventory meta is redacted). Checks print visible PASS/FAIL (use `echo FAIL` — do not `exit`, which closes an interactive terminal):

```bash
if [ -z "${AI_AUDIT:-}" ] || [ ! -f "$AI_AUDIT" ]; then
  echo "FAIL AI_AUDIT unset or missing"
else
  SECRET_ROOT="$(mktemp -d)"
  FIX="$PLUGIN/fixtures/grok"
  cp -R "$FIX" "$SECRET_ROOT/grok"
  printf '%s\n' '{"name":"db password=Hunter2FakePass!99","description":"test bot"}' \
    >"$SECRET_ROOT/grok/sand-data/agents/agent-active/profile.json"
  bash "$AI_AUDIT" context --host grok --root "$SECRET_ROOT/grok" --json-only \
    | tee /tmp/ai-audit-secret-context.json >/dev/null
  if [ "$(grep -c 'Hunter2FakePass' /tmp/ai-audit-secret-context.json || true)" -eq 0 ]; then
    echo "PASS secrets json redacted"
  else
    echo "FAIL secrets json leaked Hunter2FakePass"
  fi
  if grep -q REDACTED /tmp/ai-audit-secret-context.json; then
    echo "PASS REDACTED marker present"
  else
    echo "FAIL missing REDACTED marker"
  fi
  rm -rf "$SECRET_ROOT"
fi
```
