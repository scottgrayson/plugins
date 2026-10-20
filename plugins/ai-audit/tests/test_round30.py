#!/usr/bin/env python3
"""Round-30 tests: parenthesis after a password-manager product name."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from ai_audit.common import redact_tree  # noqa: E402

PRODUCTS = ("1password", "1Password", "lastpass", "bitwarden", "dashlane", "keepass", "keepassxc")


def _both(text: str) -> list[str]:
    row = redact_tree({"description": text, "item": text})
    return [row["description"], row["item"]]


def test_value_in_brackets_hides() -> None:
    for p in PRODUCTS:
        for body, val in (
            ("(hunter2)", "hunter2"),
            ("(x9y8)", "x9y8"),
            ("(Hunter2Fake)", "Hunter2Fake"),
            ("(pw: x9)", "x9"),
            ("(pw=x9)", "x9"),
            ("(hUnTerFake)", "hUnTerFake"),
            ("(15 tools, x9)", "x9"),
            ("(two  words)", "words"),
            ("[hunter2]", "hunter2"),
            ("{hunter2}", "hunter2"),
            ("[password manager]", "manager"),
        ):
            for out in _both(f"{p} {body}"):
                assert val not in out, (p, body, out)


def test_safe_parentheses_stay() -> None:
    for text in (
        "user-1password (15 tools)",
        "user-1password (3 tools)",
        "user-1password (1 tool)",
        "1Password (password manager)",
        "bitwarden (Password Manager)",
        "1password (password manager) for secrets",
        "1password (15 tools) for secrets",
    ):
        assert _both(text) == [text, text], text


def test_close_must_be_followed_by_safe_context() -> None:
    for text in ("1password (15 tools)x9", "1password (password manager):x9"):
        for out in _both(text):
            assert "x9" not in out, (text, out)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round30 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
