#!/usr/bin/env python3
"""Round-29 tests: product-name allowlist, example* path segment limit."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "fixtures" / "cursor-layout"
sys.path.insert(0, str(ROOT / "lib"))

from ai_audit.common import redact_tree  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.quality_checks import run_quality_checks  # noqa: E402

PRODUCTS = ("1password", "1Password", "lastpass", "bitwarden", "dashlane", "keepass", "keepassxc")


def _both(text: str) -> list[str]:
    row = redact_tree({"description": text, "item": text})
    return [row["description"], row["item"]]


def test_any_separator_then_value_hides() -> None:
    seps = (
        " - ", " – ", " — ", "∶", "꞉", "：", " -> ", " => ", "==", "=", ":", " : ",
        "\t=\t", '": "', " = ", "-x9y8z7", "_dragon7", " is ", " are ",
    )
    for p in PRODUCTS:
        for sep in seps:
            for out in _both(f"{p}{sep}hunter2Fake"):
                assert "hunter2Fake" not in out, (p, sep, out)


def test_allowlisted_contexts_stay() -> None:
    for text in (
        "1password",
        "user-1password",
        "user-1password (15 tools)",
        "Use 1Password to fetch the API key",
        "1password-dragonfly",
        "1Password.",
        "1Password. Then continue",
        "Bitwarden, and more",
    ):
        assert _both(text) == [text, text], text


def test_value_after_closing_punct_hides() -> None:
    for text in ("1password, x9", "1password. x9", "1password.x9"):
        for out in _both(text):
            assert "x9" not in out, (text, out)


def _refs(name: str) -> list[str]:
    roots = detect_host(host="cursor", root=LAYOUT)
    return sorted(
        c["meta"]["ref"]
        for c in run_quality_checks(roots)
        if c["check"] == "broken_references" and f"/{name}/" in c["source"]
    )


def test_example_dir_limited_to_first_two_segments() -> None:
    assert _refs("qc-abs-example") == [
        "/Users/x/Code/examples-app/missing.md",
        "/opt/foo/example-co/missing.md",
    ]


def test_example_paths_still_skipped() -> None:
    refs = _refs("qc-keep-examples")
    assert refs == []


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round29 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
