#!/usr/bin/env python3
"""Round-32 tests: a password-manager product name in a key acts like `password`."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tests"))

from ai_audit.common import redact_secrets, redact_tree  # noqa: E402
from test_redaction_corpus import load_labeled  # noqa: E402

REPLACEMENTS = ("1password", "1Password", "lastpass", "bitwarden")

# (input, replacement) -> one-line reason the variant cannot redact.
EXCEPTIONS: dict[tuple[str, str], str] = {}


def variants() -> list[tuple[str, str, str, str, str]]:
    """(source input, replacement, variant, value, field) for every redact row."""
    out = []
    for expect, raw, value, _label, field in load_labeled():
        if expect != "redact" or not value:
            continue
        m = re.search("password", raw, re.I)
        if not m:
            continue
        for rep in REPLACEMENTS:
            out.append((raw, rep, raw[: m.start()] + rep + raw[m.end() :], value, field))
    return out


def test_named_examples() -> None:
    for text, val in (
        ("1password-dragonfly: hunter2Fake", "hunter2Fake"),
        ("1password-dragonfly:x9", "x9"),
        ("my_lastpass_key=abc1", "abc1"),
        ("BITWARDEN_TOKEN=x9", "x9"),
        ("my lastpass key: abc1", "abc1"),
        ("ＬastPass_key=abc1", "abc1"),
    ):
        assert val not in redact_secrets(text), text
        assert val not in redact_tree({"description": text})["description"], text


def test_keep_rows() -> None:
    for text in (
        "user-1password (15 tools)",
        "1Password (password manager)",
        "Use 1Password to fetch the API key",
        "1password-dragonfly",
        "1password",
        "1Password.",
    ):
        assert redact_tree({"description": text})["description"] == text, text


def test_property_variants() -> None:
    vs = variants()
    used = set()
    for raw, rep, text, value, field in vs:
        if (raw, rep) in EXCEPTIONS:
            used.add((raw, rep))
            continue
        for got in (redact_tree({field: text})[field],):
            assert value not in got, f"LEAK in={text!r} out={got!r} value={value!r}"
    assert used == set(EXCEPTIONS), set(EXCEPTIONS) - used
    print(f"property variants generated: {len(vs)}; exceptions: {len(EXCEPTIONS)}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
