#!/usr/bin/env python3
"""Offline tests for Cursor startup report: tokenizer, User Rules, MCP flags."""

from __future__ import annotations

import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
FIX = PLUGIN / "fixtures" / "cursor"
sys.path.insert(0, str(LIB))

from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402
from ai_audit.tokenize import count_tokens, tokenizer_label  # noqa: E402


def main() -> int:
    assert tokenizer_label() == "tiktoken o200k_base"
    assert count_tokens("hello world") >= 2

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

    # MCP needs-auth flag from mcp_auth tool descriptor
    mcp_items = " ".join(r["item"] for r in payload["rows"] if r["group"] == "MCP servers")
    assert "needs-auth" in mcp_items or "needs-auth" in blob

    # Skills show 200k/1M budget metadata
    assert "skill_budget" in payload
    assert payload["skill_budget"]["budget_200k"] == 4000

    # Grouped markdown
    from ai_audit.report import context_markdown

    md = context_markdown(payload)
    assert "Startup context report" in md
    assert "Always-apply rules" in md
    assert "Skill descriptions" in md
    assert "Purpose" in md and "AI suggestion" in md
    assert "LEAKED_VERIFIER" not in md

    print(
        f"PASS cursor_startup tokenizer={tokenizer_label()} "
        f"total_200k={payload['total_tokens_200k']} "
        f"total_1m={payload['total_tokens_1m']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
