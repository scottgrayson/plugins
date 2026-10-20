#!/usr/bin/env python3
"""Offline tests for Cursor startup report: tokenizer, User Rules, MCP flags."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# os used by readonly-dir test

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
FIX = PLUGIN / "fixtures" / "cursor"
sys.path.insert(0, str(LIB))

from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402
from ai_audit.tokenize import count_tokens, tokenizer_label  # noqa: E402
from ai_audit.cursor_startup import (  # noqa: E402
    _MCP_PREAMBLE,
    _find_state_vscdb,
    _open_vscdb_readonly,
    _read_user_rules,
)


def test_tokenizer_and_specials() -> None:
    assert tokenizer_label() == "tiktoken o200k_base"
    assert count_tokens("hello world") >= 2
    # D6: literal special tokens must not crash
    assert count_tokens("<|endoftext|> hello") >= 1


def test_mcp_preamble_estimate_band() -> None:
    # Fixed estimate stand-in tokenizes near the Mac-measured ~637
    tok = count_tokens(_MCP_PREAMBLE)
    assert 600 <= tok <= 700, f"MCP preamble tokens={tok} (expected ~637 band)"


def test_no_tmpdir_files_during_run() -> None:
    """D2: opening state.vscdb must not create temp copies under TMPDIR."""
    with tempfile.TemporaryDirectory(prefix="ai-audit-tmpdir-test.") as tmp:
        tmp_path = Path(tmp)
        before = set(tmp_path.iterdir())
        env = os.environ.copy()
        env["TMPDIR"] = str(tmp_path)
        env["TMP"] = str(tmp_path)
        env["TEMP"] = str(tmp_path)
        # Run in-process with TMPDIR overridden
        old = {k: os.environ.get(k) for k in ("TMPDIR", "TMP", "TEMP")}
        try:
            os.environ["TMPDIR"] = str(tmp_path)
            os.environ["TMP"] = str(tmp_path)
            os.environ["TEMP"] = str(tmp_path)
            roots = detect_host(host="cursor", root=FIX)
            payload = run_context_audit(roots)
            assert payload.get("rows")
            # Also exercise the readonly open directly
            db = _find_state_vscdb(roots)
            assert db is not None
            conn = _open_vscdb_readonly(db)
            conn.close()
            _read_user_rules(roots)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        after = set(tmp_path.iterdir())
        created = after - before
        # No ai-audit-state.* (or any) files from our code
        leaked = [
            p
            for p in created
            if p.name.startswith("ai-audit-state") or p.suffix in {".vscdb", ".db"}
        ]
        assert not leaked, f"temp files created under TMPDIR: {leaked}"
        assert not created, f"unexpected TMPDIR entries: {created}"


def test_vscdb_wal_readonly_sees_recent_write() -> None:
    """mode=ro&immutable=0 should see WAL updates without copying."""
    with tempfile.TemporaryDirectory(prefix="ai-audit-wal.") as tmp:
        db = Path(tmp) / "state.vscdb"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            ("aicontext.personalContext", "rules-v1"),
        )
        conn.commit()
        # Leave WAL present; update via a second connection
        conn2 = sqlite3.connect(str(db))
        conn2.execute(
            "UPDATE ItemTable SET value = ? WHERE key = ?",
            ("rules-v2-wal", "aicontext.personalContext"),
        )
        conn2.commit()
        conn2.close()
        # Read-only URI must see the WAL write
        ro = _open_vscdb_readonly(db)
        try:
            row = ro.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("aicontext.personalContext",),
            ).fetchone()
        finally:
            ro.close()
        conn.close()
        assert row and row[0] == "rules-v2-wal", row


def test_vscdb_immutable_no_wal_creates_nothing() -> None:
    """When no -wal/-shm exist, immutable=1 must not create them."""
    with tempfile.TemporaryDirectory(prefix="ai-audit-nowal.") as tmp:
        db = Path(tmp) / "state.vscdb"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            ("aicontext.personalContext", "plain"),
        )
        conn.commit()
        conn.close()
        wal, shm = Path(str(db) + "-wal"), Path(str(db) + "-shm")
        # Remove any wal/shm left by close
        wal.unlink(missing_ok=True)
        shm.unlink(missing_ok=True)
        before = set(Path(tmp).iterdir())
        ro = _open_vscdb_readonly(db)
        row = ro.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("aicontext.personalContext",),
        ).fetchone()
        ro.close()
        after = set(Path(tmp).iterdir())
        assert row and row[0] == "plain"
        assert not wal.exists() and not shm.exists(), (wal.exists(), shm.exists())
        assert after == before or after - before == set(), after - before


def test_vscdb_readonly_dir_unreadable() -> None:
    """Readonly directory / locked DB → User Rules unreadable row, no traceback."""
    with tempfile.TemporaryDirectory(prefix="ai-audit-ro.") as tmp:
        home = Path(tmp) / "home"
        gs = home / ".cursor" / "User" / "globalStorage"
        gs.mkdir(parents=True)
        db = gs / "state.vscdb"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            ("aicontext.personalContext", "x"),
        )
        conn.commit()
        conn.close()
        # Make directory read-only so sqlite cannot create wal/shm sidecars
        os.chmod(gs, 0o555)
        try:
            from ai_audit.detect import Roots

            roots = Roots(
                host="cursor",
                home=home,
                project=home,
                cursor_home=home / ".cursor",
            )
            row = _read_user_rules(roots)
            # Either reads successfully with immutable=1, or reports unreadable
            assert row.group == "User Rules"
            if "unreadable" in row.item:
                assert (row.meta or {}).get("unreadable") is True
        finally:
            os.chmod(gs, 0o755)


def test_layout_fixture_shapes() -> None:
    """Synthetic layout fixture: symlinks, nested plugins, disabled, empty-window."""
    layout = PLUGIN / "fixtures" / "cursor-layout"
    if not layout.is_dir():
        return
    roots = detect_host(host="cursor", root=layout)
    payload = run_context_audit(roots, env_override="Cursor · Mac")
    blob = str(payload)
    skill = next(
        r for r in payload["rows"]
        if r["group"] == "Skill descriptions" and r["item"] == "vscdb-disabled-demo"
    )
    assert skill["enabled"] == "no"
    assert skill["tokens"] > 0
    items = " ".join(r["item"] for r in payload["rows"])
    assert "linked-alpha" in items
    assert "global.mdc" in items
    assert "nested-alpha" in items
    assert any("repeats per prompt" in r["item"] for r in payload["rows"])
    assert payload["skill_budget"]["unique_realpaths"] >= 1
    assert "unique_names_lower_bound" in payload["skill_budget"]
    # From project root → basename/project slug, not stale Users-fixture
    mcp_notes = [
        (r.get("meta") or {}).get("mcp_selection", "")
        for r in payload["rows"]
        if r["group"] == "MCP servers"
    ]
    assert any("Users-fixture" not in n or "empty-window" in n or "project" in n for n in mcp_notes)


def main() -> int:
    test_tokenizer_and_specials()
    test_mcp_preamble_estimate_band()
    test_no_tmpdir_files_during_run()
    test_vscdb_wal_readonly_sees_recent_write()
    test_vscdb_immutable_no_wal_creates_nothing()
    test_vscdb_readonly_dir_unreadable()
    test_layout_fixture_shapes()

    roots = detect_host(host="cursor", root=FIX)
    payload = run_context_audit(roots)
    blob = str(payload)

    # Toxic MCP OAuth verifier must never appear (exact-key User Rules only).
    assert "LEAKED_VERIFIER_SHOULD_NEVER_APPEAR_XYZ" not in blob
    assert "mcp_code_verifier" not in blob

    assert payload.get("tokenizer") == "tiktoken o200k_base"
    assert "rows" in payload and "groups" in payload
    assert payload.get("total_tokens_200k", 0) > 0

    # User Rules from fixture DB
    user_rows = [r for r in payload["rows"] if r["group"] == "User Rules"]
    assert user_rows, "missing User Rules group"
    assert any(r["tokens"] > 0 for r in user_rows), user_rows
    assert any(
        (r.get("meta") or {}).get("open", "").startswith("sqlite mode=ro")
        for r in user_rows
    ), user_rows

    # MCP needs-auth flag from mcp_auth tool descriptor
    mcp_items = " ".join(
        r["item"] for r in payload["rows"] if r["group"] == "MCP servers"
    )
    assert "needs-auth" in mcp_items or "needs-auth" in blob
    # Selection note present
    mcp_meta = [
        r.get("meta") or {}
        for r in payload["rows"]
        if r["group"] == "MCP servers" and (r.get("meta") or {}).get("server")
    ]
    assert mcp_meta and any(m.get("mcp_selection") for m in mcp_meta), mcp_meta

    # Script hooks labelled unmeasured (D7)
    hook_items = [
        r for r in payload["rows"] if "unmeasured: script hook" in str(r.get("item"))
    ]
    assert hook_items, "expected unmeasured script hook row"
    assert all((r.get("meta") or {}).get("unmeasured") for r in hook_items)

    # Skills show 200k/1M budget metadata
    assert "skill_budget" in payload
    assert payload["skill_budget"]["budget_200k"] == 4000

    # Grouped markdown
    from ai_audit.report import context_markdown

    md = context_markdown(payload)
    assert "Startup context report" in md
    assert payload.get("environment", {}).get("heading")
    assert "Always-apply rules" in md or "Always-apply rules" in str(
        payload.get("groups")
    )
    assert "Skill descriptions" in md or "Skill descriptions" in str(
        payload.get("groups")
    )
    assert "| Item | Tokens | Description | Source |" in md
    assert "AI suggestion" not in md
    assert "| Purpose |" not in md
    assert "LEAKED_VERIFIER" not in md
    assert "temp copy" not in md.lower()
    # Preamble is a fixed estimate, not measured
    pre = [r for r in payload["rows"] if "preamble" in r["item"].lower()]
    assert pre and (pre[0].get("meta") or {}).get("fixed_estimate") is True
    # Sorted groups
    groups = payload["groups"]
    subs = payload["subtotals"]
    scores = [subs[g]["tokens_200k"] for g in groups]
    assert scores == sorted(scores, reverse=True)

    print(
        f"PASS cursor_startup tokenizer={tokenizer_label()} "
        f"total_200k={payload['total_tokens_200k']} "
        f"total_1m={payload['total_tokens_1m']} "
        f"preamble={count_tokens(_MCP_PREAMBLE)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
