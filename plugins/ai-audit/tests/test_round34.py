#!/usr/bin/env python3
"""Round-34 tests: product dir names in path fields, comma connective remainder."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from ai_audit.common import redact_tree  # noqa: E402

CACHE = "~/.cursor/plugins/cache/m/{}/{}/skills/x/SKILL.md"


def _r(field: str, text: str) -> str:
    return redact_tree({field: text})[field]


def test_product_dir_in_path_fields() -> None:
    for field in ("source", "path"):
        for name, ver in (("1password", "1.2.3"), ("bitwarden", "2.0.0")):
            text = CACHE.format(name, ver)
            assert _r(field, text) == text, (field, text)


def test_path_field_rest_still_scanned() -> None:
    for field in ("source", "path"):
        for text, val in (
            ("m/1password=hunter2", "hunter2"),
            ("m/1password/password=x9", "x9"),
            ("m/1password:x9", "x9"),
        ):
            assert val not in _r(field, text), (field, text)


def test_prose_fields_not_loosened() -> None:
    assert "hunter2" not in _r("description", "1password/hunter2")
    assert "hunter2" not in _r("description", "/1password/hunter2")
    assert _r("description", "1password/hunter2") != "1password/hunter2"


def test_comma_connective_remainder() -> None:
    for text in ("Bitwarden, etc, x9y8z7", "1Password, and x9y8z7", "1Password, or, etc, x9y8z7"):
        assert "x9y8z7" not in _r("description", text), text
    for text in ("Bitwarden, and more", "Bitwarden, etc.", "Bitwarden, and"):
        assert _r("description", text) == text, text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
