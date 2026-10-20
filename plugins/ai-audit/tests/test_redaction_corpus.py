#!/usr/bin/env python3
"""Redaction regression corpus: every line of data/redaction_corpus.tsv through the report path."""

from __future__ import annotations

import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
CORPUS = Path(__file__).resolve().parent / "data" / "redaction_corpus.tsv"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_tree  # noqa: E402  (what report.py calls)

_ESC_RE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|(.))")
_SIMPLE = {"t": "\t", "n": "\n", "\\": "\\"}


def _unescape(s: str) -> str:
    return _ESC_RE.sub(
        lambda m: chr(int(m.group(1), 16)) if m.group(1) else _SIMPLE.get(m.group(2), m.group(0)),
        s,
    )


def load_corpus() -> list[tuple[str, str, str]]:
    return [(r[0], r[1], r[2], r[4]) for r in load_labeled()]


def load_labeled() -> list[tuple[str, str, str, str, str]]:
    rows = []
    for n, line in enumerate(CORPUS.read_text(encoding="utf-8").split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        label = parts[3] if len(parts) >= 4 else ""
        field = parts[4] if len(parts) == 5 else "description"
        assert label in ("", "by-design"), (n, line)
        assert len(parts) <= 5 and field in ("description", "source", "path"), (n, line)
        parts = parts[:3]
        assert len(parts) == 3 and parts[0] in ("redact", "keep", "overredact"), (n, line)
        expect, raw, value = parts[0], _unescape(parts[1]), _unescape(parts[2])
        assert expect != "redact" or value, f"line {n}: redact row needs a value"
        assert expect != "redact" or value in raw, f"line {n}: value not in input"
        rows.append((expect, raw, value, label, field))
    return rows


def check_line(expect: str, raw: str, value: str, field: str = "description") -> None:
    # Through a free-text field by default, as report rows do (multi-line inputs
    # included). Optional 5th column names a path-like field (source, path).
    got = redact_tree({field: raw})[field]
    if expect == "redact":
        assert value not in got, f"LEAK in={raw!r} out={got!r} value={value!r}"
    elif expect == "keep":
        assert got == raw, f"KEEP changed in={raw!r} out={got!r}"


def test_redaction_corpus() -> None:
    rows = load_corpus()
    assert len({(r[1], r[3]) for r in rows}) == len(rows), "duplicate corpus inputs"
    failures = []
    for expect, raw, value, field in rows:
        try:
            check_line(expect, raw, value, field)
        except AssertionError as e:
            failures.append(str(e))
    assert not failures, "\n".join(failures)


def main() -> int:
    test_redaction_corpus()
    rows = load_corpus()
    c = {k: sum(1 for r in rows if r[0] == k) for k in ("redact", "keep", "overredact")}
    by_design = sum(1 for r in load_labeled() if r[3] == "by-design")
    print(
        f"PASS redaction_corpus redact={c['redact']} keep={c['keep']} "
        f"overredact={c['overredact']} by-design={by_design}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
