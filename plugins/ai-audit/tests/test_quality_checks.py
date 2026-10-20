#!/usr/bin/env python3
"""Unit tests for deterministic quality checks (one pass + one fail each)."""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
LAYOUT = PLUGIN / "fixtures" / "cursor-layout"
sys.path.insert(0, str(LIB))

from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402
from ai_audit.report import context_markdown  # noqa: E402
from ai_audit.quality_checks import (  # noqa: E402
    check_broken_frontmatter,
    check_broken_references,
    check_dead_contradictory_rules,
    check_file_over_500_lines,
    check_same_name_drift,
    check_skill_descriptions,
    check_stale_names,
    run_quality_checks,
)


def _roots():
    return detect_host(host="cursor", root=LAYOUT)


def _findings(fn, **kwargs):
    return fn(_roots(), **kwargs)


def _has(findings, *, check: str, needle: str) -> bool:
    return any(
        f.check == check and needle in f.finding for f in findings
    )


def _none_for(findings, *, check: str, path_substr: str) -> bool:
    return not any(
        f.check == check and path_substr in (f.finding + f.source)
        for f in findings
    )


def test_broken_references_pass_and_fail() -> None:
    skills = [
        LAYOUT / "project/.cursor/skills/qc-pass-refs/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-refs/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-pass-desc/SKILL.md",
    ]
    findings = _findings(check_broken_references, skill_files=skills, rule_files=[])
    assert _has(
        findings, check="broken_references", needle="does-not-exist-qc.md"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="broken_references", needle="qc-missing-skill-xyz"
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="broken_references", path_substr="qc-pass-refs"
    ), [f.finding for f in findings]


def test_dead_contradictory_rules_pass_and_fail() -> None:
    rules = [
        LAYOUT / "project/.cursor/rules/qc-pass-rule.mdc",
        LAYOUT / "project/.cursor/rules/qc-fail-dead.mdc",
        LAYOUT / "project/.cursor/rules/qc-fail-contradict.mdc",
        LAYOUT / "project/.cursor/rules/qc-fail-empty-glob.mdc",
    ]
    findings = _findings(check_dead_contradictory_rules, rule_files=rules)
    assert _has(
        findings, check="dead_contradictory_rules", needle="never loads"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="dead_contradictory_rules", needle="contradictory"
    ), [f.finding for f in findings]
    assert _has(
        findings,
        check="dead_contradictory_rules",
        needle="zz-no-such-extension-qc.zzzz",
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="dead_contradictory_rules", path_substr="qc-pass-rule"
    ), [f.finding for f in findings]


def test_skill_description_pass_and_fail() -> None:
    skills = [
        LAYOUT / "project/.cursor/skills/qc-pass-desc/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-desc-short/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-desc-missing/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-desc-long/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-desc-notrig/SKILL.md",
    ]
    findings = _findings(check_skill_descriptions, skill_files=skills)
    assert _has(
        findings, check="skill_description", needle="under 20 chars"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="skill_description", needle="description missing"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="skill_description", needle="over 1024 chars"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="skill_description", needle="no trigger wording"
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="skill_description", path_substr="qc-pass-desc"
    ), [f.finding for f in findings]


def test_file_over_500_lines_pass_and_fail() -> None:
    skills = [
        LAYOUT / "project/.cursor/skills/qc-pass-desc/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-lines/SKILL.md",
    ]
    findings = _findings(
        check_file_over_500_lines, skill_files=skills, rule_files=[]
    )
    assert _has(
        findings, check="file_over_500_lines", needle="over 500"
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="file_over_500_lines", path_substr="qc-pass-desc"
    ), [f.finding for f in findings]


def test_same_name_drift_pass_and_fail() -> None:
    # Fail: qc-drift two realpaths, different content
    drift = [
        LAYOUT / "project/.cursor/skills/qc-drift/SKILL.md",
        LAYOUT / "home/.cursor/skills/qc-drift/SKILL.md",
    ]
    findings = _findings(
        check_same_name_drift, skill_files=drift, rule_files=[]
    )
    assert _has(
        findings, check="same_name_drift", needle="qc-drift"
    ), [f.finding for f in findings]

    # Pass: linked-alpha is a symlink → realpath dedupe → no drift
    alpha_link = LAYOUT / "home/.cursor/skills/linked-alpha/SKILL.md"
    alpha_real = LAYOUT / "home/Code/skills/linked-alpha/SKILL.md"
    assert alpha_link.is_symlink() or alpha_link.resolve() == alpha_real.resolve()
    pass_findings = _findings(
        check_same_name_drift,
        skill_files=[alpha_link, alpha_real],
        rule_files=[],
    )
    assert pass_findings == [], [f.finding for f in pass_findings]


def test_stale_names_pass_and_fail() -> None:
    skills = [
        LAYOUT / "project/.cursor/skills/qc-pass-desc/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-stale/SKILL.md",
    ]
    findings = _findings(
        check_stale_names, skill_files=skills, rule_files=[]
    )
    assert _has(
        findings, check="stale_names", needle="agent-environment"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="stale_names", needle="ai-plugins"
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="stale_names", path_substr="qc-pass-desc"
    ), [f.finding for f in findings]


def test_broken_frontmatter_pass_and_fail() -> None:
    skills = [
        LAYOUT / "project/.cursor/skills/qc-pass-desc/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-frontmatter/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-noname/SKILL.md",
        LAYOUT / "project/.cursor/skills/qc-fail-desc-missing/SKILL.md",
    ]
    rules = [
        LAYOUT / "project/.cursor/rules/qc-pass-rule.mdc",
        LAYOUT / "project/.cursor/rules/qc-fail-unknown-keys.mdc",
    ]
    findings = _findings(
        check_broken_frontmatter, skill_files=skills, rule_files=rules
    )
    assert _has(
        findings, check="broken_frontmatter", needle="unclosed front matter"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="broken_frontmatter", needle="missing name"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="broken_frontmatter", needle="missing description"
    ), [f.finding for f in findings]
    assert _has(
        findings, check="broken_frontmatter", needle="unknown front-matter keys"
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="broken_frontmatter", path_substr="qc-pass-desc"
    ), [f.finding for f in findings]
    assert _none_for(
        findings, check="broken_frontmatter", path_substr="qc-pass-rule"
    ), [f.finding for f in findings]


def test_run_quality_checks_sorted_and_in_report() -> None:
    roots = _roots()
    checks = run_quality_checks(roots)
    assert checks, "expected fixture findings"
    # Severity order: high before medium before low
    rank = {"high": 0, "medium": 1, "low": 2}
    ranks = [rank.get(c["severity"], 9) for c in checks]
    assert ranks == sorted(ranks), ranks[:20]

    payload = run_context_audit(roots, env_override="Cursor · Mac")
    assert "checks" in payload and payload["checks"]
    assert "should-not-load" not in str(payload.get("checks"))
    md = context_markdown(payload)
    assert "### Checks" in md
    assert "| Item | Check | Finding | Source |" in md
    assert "broken_references" in md or "skill_description" in md


def main() -> int:
    test_broken_references_pass_and_fail()
    test_dead_contradictory_rules_pass_and_fail()
    test_skill_description_pass_and_fail()
    test_file_over_500_lines_pass_and_fail()
    test_same_name_drift_pass_and_fail()
    test_stale_names_pass_and_fail()
    test_broken_frontmatter_pass_and_fail()
    test_run_quality_checks_sorted_and_in_report()
    roots = _roots()
    findings = run_quality_checks(roots)
    print(f"PASS quality_checks findings={len(findings)}")
    for f in findings:
        print(f"  [{f['severity']}] {f['check']}: {f['finding']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
