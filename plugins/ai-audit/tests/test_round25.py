#!/usr/bin/env python3
"""Round-25 tests: broken_references false positives, product names, Editable column."""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
LAYOUT = Path(__file__).resolve().parents[1] / "fixtures" / "cursor-layout"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_tree  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.quality_checks import is_editable_source, run_quality_checks  # noqa: E402
from ai_audit.report import _checks_markdown  # noqa: E402


def _refs(name: str) -> list[str]:
    roots = detect_host(host="cursor", root=LAYOUT)
    return [
        c["meta"]["ref"]
        for c in run_quality_checks(roots)
        if c["check"] == "broken_references" and f"/{name}/" in c["source"]
    ]


def test_example_paths_are_kept() -> None:
    assert _refs("qc-keep-examples") == []


def test_real_missing_refs_are_flagged() -> None:
    assert sorted(_refs("qc-flag-real")) == sorted(
        [
            "~/.cursor/rules/qc-flag-real-rule.mdc",
            "scripts/qc-flag-missing.sh",
            "references/qc-flag-missing.md",
            "./qc-flag-missing-notes.md",
            "docs-missing/qc-flag-link.md",
            "./references/qc-flag-code.md",
            "~/.qc-flag-home/config.json",
        ]
    )


def test_password_manager_names_keep_tool_counts() -> None:
    for name in ("user-1password", "user-bitwarden", "user-lastpass", "user-keepass"):
        row = {"item": f"{name} (12 tools)", "description": name}
        assert redact_tree(row) == row, name


def test_name_and_path_tails() -> None:
    for field, text, secret in [
        ("description", "/x/password/hunter2", "hunter2"),
        ("description", "passwordhunter2", "hunter2"),
        ("description", "api_key_abc123", "abc123"),
        ("source", "~/.secrets/hunter2", "hunter2"),
        ("source", "x/secret_ab12", "ab12"),
    ]:
        assert secret not in redact_tree({field: text})[field], text
    for text in ("secret-leak-watch", "password-reset-flow", "api_key_rotation", "~/.secrets/"):
        assert redact_tree({"description": text})["description"] == text, text


def test_editable_column() -> None:
    assert is_editable_source("~/.cursor/plugins/cache/m/p/1.0/skills/a/SKILL.md") == "no"
    assert is_editable_source("~/.claude/plugins/cache/m/p/1.0/skills/a/SKILL.md") == "no"
    assert is_editable_source("~/.cursor/skills/a/SKILL.md") == "yes"
    roots = detect_host(host="cursor", root=LAYOUT)
    checks = run_quality_checks(roots)
    assert all(c["editable"] in ("yes", "no") for c in checks)
    assert any(c["editable"] == "no" for c in checks)
    md = "\n".join(_checks_markdown({"checks": checks}))
    assert "| Item | Check | Finding | Source | Editable |" in md


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round25 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
