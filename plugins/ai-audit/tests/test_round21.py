#!/usr/bin/env python3
"""Round-21 tests: kebab assignment redaction, ~ project dedupe, disabled plugin, prose refs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
LAYOUT = PLUGIN / "fixtures" / "cursor-layout"
BIN = PLUGIN / "bin" / "ai-audit"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_secrets  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.quality_checks import (  # noqa: E402
    check_broken_references,
    check_skill_descriptions,
    run_quality_checks,
)

REDACT = [
    ("pw-prod=dragon", "dragon"),
    ("password-prod=x9Fake", "x9Fake"),
    ("pwd-prod=x9Fake", "x9Fake"),
    ("pass-prod=x9Fake", "x9Fake"),
    ("token-prod=x9Fake", "x9Fake"),
    ("secret-prod: x9Fake", "x9Fake"),
    ("pass-phrase: x9Fake", "x9Fake"),
    ("pin-code=1234", "1234"),
    ("--pw-prod x9Fake", "x9Fake"),
    ("secret, hunter22X", "hunter22X"),
    ("password, hunter22X", "hunter22X"),
]
KEEP = [
    "token-budget",
    "secret-scanning",
    "auth-flow",
    "session-report",
    "secret-leak-watch",
    "~/.cursor/skills/secret-scanning/SKILL.md",
    "grep -p foo",
]


def test_redaction_kebab_assignments() -> None:
    for text, secret in REDACT:
        assert secret not in redact_secrets(text), (text, redact_secrets(text))
    for text in KEEP:
        assert redact_secrets(text) == text, (text, redact_secrets(text))


def test_project_dir_is_home_dedupes_mcp() -> None:
    home = LAYOUT / "home"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB)
    proc = subprocess.run(
        [str(BIN), "context", "--host", "cursor", "--home", str(home), "--json-only"],
        capture_output=True, text=True, env=env, cwd=home, check=False,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    payload = json.loads(proc.stdout)
    mcp = [r for r in payload["rows"] if r["item"].startswith("dupe-srv")]
    # user ~/.cursor/mcp.json (once) + the flat-plugin copy; was x3 with project read too
    assert len(mcp) == 2, [r["item"] for r in mcp]
    assert sorted(r["enabled"] for r in mcp) == ["unknown", "yes"]
    for name in ("clickup", "ghost-server"):
        rows = [r["item"] for r in payload["rows"] if r["item"].startswith(name)]
        assert len(rows) == 1, (name, rows)


def test_vscdb_disabled_plugin_listed_counted_and_checked() -> None:
    roots = detect_host(host="cursor", root=LAYOUT)
    payload = run_context_audit(roots, env_override="Cursor · Mac")
    row = next(
        r for r in payload["rows"]
        if r["group"] == "Skill descriptions" and r["item"] == "vscdb-disabled-demo"
    )
    assert row["enabled"] == "no"
    assert row["tokens"] > 0 and row["tokens_1m"] == row["tokens"]
    assert any(
        c["check"] == "broken_references" and "vscdb-missing-note.md" in c["finding"]
        for c in payload["checks"]
    ), payload["checks"]


def test_apply_during_trigger_and_prose_skill_ref() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        d = tdp / "s"
        d.mkdir()
        sk = d / "SKILL.md"
        sk.write_text(
            "---\nname: s\ndescription: Apply during every release review of the docs\n---\n"
            "Do not switch skill mid-task or rule of-thumb style.\n",
            encoding="utf-8",
        )
        roots = detect_host(host="cursor", root=LAYOUT)
        f = check_skill_descriptions(roots, skill_files=[sk])
        assert not [x for x in f if "trigger" in x.finding.lower()], [x.finding for x in f]
        refs = check_broken_references(roots, skill_files=[sk], rule_files=[])
        assert not refs, [x.finding for x in refs]
    # the unquoted multi-segment fixture reference is still flagged
    allf = run_quality_checks(roots)
    assert any("qc-missing-skill-xyz" in c["finding"] for c in allf)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round21 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
