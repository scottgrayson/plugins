#!/usr/bin/env python3
"""Round-27 tests: product names acting as keys, glued tails, version fingerprint, absolute refs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "fixtures" / "cursor-layout"
sys.path.insert(0, str(ROOT / "lib"))

from ai_audit.common import redact_tree  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.quality_checks import run_quality_checks  # noqa: E402

PRODUCTS = ("1password", "1Password", "lastpass", "keepass", "bitwarden")


def _both(text: str) -> list[str]:
    row = redact_tree({"description": text, "item": text})
    return [row["description"], row["item"]]


def test_product_as_key_hides_value() -> None:
    for p in PRODUCTS:
        for sep in (": ", "=", " => ", " := ", " is "):
            for out in _both(f"{p}{sep}hunter2Fake"):
                assert "hunter2Fake" not in out, (p, sep, out)


def test_product_glued_digit_tail_hides() -> None:
    for text, tail in (
        ("1password-hunter2", "hunter2"),
        ("1password_dragon7", "dragon7"),
        ("lastpass-hunter2", "hunter2"),
        ("bitwarden-hunter2", "hunter2"),
    ):
        for out in _both(text):
            assert tail not in out, (text, out)


def test_product_names_and_word_tails_stay() -> None:
    for text in ("1password", "lastpass", "1password-dragonfly", "user-1password (15 tools)"):
        assert _both(text) == [text, text], text


def test_no_real_version_fingerprint() -> None:
    out = subprocess.run(
        ["rg", "-uu", "-l", "-F", ".".join(["0", "15", "25"]), str(ROOT)], capture_output=True, text=True
    )
    assert out.stdout.strip() == "", out.stdout


def _refs(name: str) -> list[str]:
    roots = detect_host(host="cursor", root=LAYOUT)
    return [
        c["meta"]["ref"]
        for c in run_quality_checks(roots)
        if c["check"] == "broken_references" and f"/{name}/" in c["source"]
    ]


def test_prose_absolute_path_is_checked() -> None:
    assert _refs("qc-abs-prose") == ["/opt/qc-abs-missing/settings.toml"]


def test_prose_sandbox_path_is_skipped() -> None:
    assert "/workspace/qc-abs-sandbox/notes.md" not in _refs("qc-abs-prose")


def test_code_absolute_path_is_kept() -> None:
    assert _refs("qc-abs-code") == []


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round27 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
