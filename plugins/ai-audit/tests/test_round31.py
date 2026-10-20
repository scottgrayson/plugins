#!/usr/bin/env python3
"""Round-31 tests: the product-name exemption covers only the product token."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

from ai_audit.common import redact_tree  # noqa: E402
from test_redaction_corpus import load_labeled  # noqa: E402


def _desc(text: str) -> str:
    return redact_tree({"description": text})["description"]


def test_rows_prefixed_by_product_still_redact() -> None:
    rows = [
        (raw, value)
        for expect, raw, value, _label, field in load_labeled()
        if expect == "redact" and field == "description"
    ]
    assert len(rows) >= 50, len(rows)
    for raw, value in rows:
        for text in ("1Password " + raw, "1password (" + raw + ")"):
            out = _desc(text)
            assert value not in out, (text, out)


def test_rest_after_product_is_scanned() -> None:
    for text, val in (
        ("1password (password is dragonfly)", "dragonfly"),
        ("1password (secret dragonfly)", "dragonfly"),
        ("Use 1Password, password is dragonfly", "dragonfly"),
        ("1password hunter2", "hunter2"),
        ("password manager: x9", "x9"),
        ("password manager hunter2", "hunter2"),
    ):
        assert val not in _desc(text), text


def test_readable_stays() -> None:
    for text in (
        "user-1password (15 tools)",
        "Use 1Password to fetch the API key",
        "1password",
        "1Password.",
        "1password-dragonfly",
        "password manager",
        "Use a password manager for this",
        "1Password (password manager)",
    ):
        out = _desc(text)
        assert out == text, (text, out)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round31 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
