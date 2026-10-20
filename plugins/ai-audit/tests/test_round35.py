#!/usr/bin/env python3
"""Round-35 tests: the segment after a product folder in path/source fields."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from ai_audit.common import redact_tree  # noqa: E402

CACHE = "~/.cursor/plugins/cache/m/{}/{}/skills/x/SKILL.md"
HASH40 = "0123456789abcdef0123456789abcdef01234567"
HASH64 = HASH40 + "0123456789abcdef01234567"


def _r(field: str, text: str) -> str:
    return redact_tree({field: text})[field]


def test_keep_shapes() -> None:
    for name, seg in (
        ("1password", "1.2.3"),
        ("1password", "v2.0.0"),
        ("lastpass", "2.0.0-beta"),
        ("bitwarden", "2.0.0-rc.1"),
        ("bitwarden", "1.2"),
        ("bitwarden", "release_v0.1.4"),
        ("1password", "skills"),
        ("1password", "latest"),
        ("1password", "main"),
        ("bitwarden", HASH40),
        ("bitwarden", HASH64),
    ):
        for field in ("source", "path"):
            text = CACHE.format(name, seg)
            assert _r(field, text) == text, (field, text)


def test_hide_shapes() -> None:
    for seg in (
        "hunter2",
        "x9y8z7",
        "Hunter2Fake",
        "abc1",
        "deadbeef12",
        HASH40[:12],
        HASH40[:39],
        HASH40.upper(),
        "12345",
    ):
        for field in ("source", "path"):
            text = f"~/.cursor/password-free/m/1password/{seg}/skills/x"
            got = _r(field, text)
            assert seg not in got, (field, text, got)


def test_prose_unchanged() -> None:
    assert "hunter2" not in _r("description", "1password/hunter2")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
