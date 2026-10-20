#!/usr/bin/env python3
"""Round-20 fixture tests: references, MCP matching, plugin shapes, hooks, redaction."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
LAYOUT = PLUGIN / "fixtures" / "cursor-layout"
sys.path.insert(0, str(LIB))

from ai_audit.common import redact_secrets  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402
from ai_audit.detect import Roots, detect_host  # noqa: E402
from ai_audit.environment import is_grok_cloud  # noqa: E402
from ai_audit.quality_checks import (  # noqa: E402
    _iter_skill_files,
    check_broken_references,
    check_skill_descriptions,
    check_stale_names,
    load_stale_names,
    run_quality_checks,
)
from ai_audit.report import context_markdown  # noqa: E402


def _roots() -> Roots:
    return detect_host(host="cursor", root=LAYOUT)


def _payload() -> dict:
    return run_context_audit(_roots(), env_override="Cursor · Mac")


def _rows(payload: dict, group: str | None = None) -> list[dict]:
    return [r for r in payload["rows"] if group is None or r["group"] == group]


# 1. broken_references: URLs, domains, placeholders -> 0 findings ------------------
def test_urls_and_placeholders_give_zero_findings() -> None:
    skills = [
        LAYOUT / "project/.cursor/skills/qc-pass-urls/SKILL.md",
        next(
            p
            for p in _iter_skill_files(_roots())
            if p.parent.name == "cf-docs"
        ),
    ]
    findings = check_broken_references(_roots(), skill_files=skills, rule_files=[])
    assert findings == [], [f.finding for f in findings]
    all_refs = [
        f for f in run_quality_checks(_roots()) if f["check"] == "broken_references"
    ]
    assert not any("qc-pass-urls" in f["source"] or "cf-docs" in f["source"] for f in all_refs)


def test_real_missing_paths_still_flagged() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "s"
        d.mkdir()
        (d / "real.md").write_text("x\n")
        skill = d / "SKILL.md"
        skill.write_text(
            "---\nname: s\ndescription: Use when testing\n---\n"
            "See ./real.md, ./gone.md, ~/nope-r20.md and @file ./also-gone.md "
            "plus https://x.example.com/a/b.md and <name>/c.md.\n"
        )
        roots = Roots("cursor", Path(td), Path(td), None, None, Path(td) / ".cursor", False)
        refs = sorted(
            f.meta["ref"]
            for f in check_broken_references(roots, skill_files=[skill], rule_files=[])
        )
        assert refs == ["./also-gone.md", "./gone.md", "~/nope-r20.md"], refs


# 2. MCP matching -----------------------------------------------------------------
def test_mcp_matches_descriptor_folders() -> None:
    rows = _rows(_payload(), "MCP servers")
    by_server = {(r.get("meta") or {}).get("server"): r for r in rows if (r.get("meta") or {}).get("server")}
    for folder in (
        "user-clickup",
        "user-dupe-srv",
        "plugin-flat-plugin-flat-server",
        "plugin-market-beta-ticketdesk",
    ):
        assert folder in by_server, sorted(by_server)
        assert "failed-to-start-or-empty" not in by_server[folder]["item"], by_server[folder]
    # no phantom rows for configured names that matched a folder
    for phantom in ("clickup", "plugin-ticketdesk", "flat-server", "ticketdesk"):
        assert phantom not in by_server, phantom
    # a configured server with no descriptors stays flagged
    assert "failed-to-start-or-empty" in by_server["ghost-server"]["item"]


def test_mcp_duplicates_only_for_distinct_folders() -> None:
    items = [r["item"] for r in _rows(_payload(), "MCP servers") if r["item"].startswith("duplicate servers")]
    assert len(items) == 1, items
    assert "user-dupe-srv" in items[0] and "plugin-flat-plugin-dupe-srv" in items[0]
    assert not any("clickup" in i or "ticketdesk" in i for i in items)


# 3. discovered plugin shapes: same set in table and Checks -----------------------
def test_plugin_shapes_discovered_everywhere() -> None:
    payload = _payload()
    skill_items = " ".join(r["item"] for r in _rows(payload, "Skill descriptions"))
    for name in ("flat-skill", "market-alpha", "market-beta", "cf-docs", "nested-alpha", "decision-pack"):
        assert name in skill_items, (name, skill_items)
    rules = " ".join(r["item"] for r in _rows(payload, "Always-apply rules"))
    assert "flat-always.mdc" in rules and "beta-always.mdc" in rules
    # Checks see the same skill files as the table
    table_paths = {p for r in _rows(payload, "Skill descriptions") for p in (r.get("meta") or {}).get("paths", [])}
    check_files = {str(p) for p in _iter_skill_files(_roots())}
    assert table_paths <= check_files, sorted(table_paths - check_files)
    for name in ("flat-skill", "market-alpha", "cf-docs"):
        assert any(f"/{name}/SKILL.md" in p for p in check_files), name


# 4. hooks: Per prompt group -------------------------------------------------------
def test_per_prompt_hooks_excluded_from_startup() -> None:
    payload = _payload()
    pp = _rows(payload, "Per prompt")
    assert pp, "expected Per prompt group"
    assert payload["groups"][-1] == "Per prompt"
    assert all("UserPromptSubmit" in r["item"] for r in pp)
    assert not any("UserPromptSubmit" in r["item"] for r in _rows(payload, "Model/settings"))
    startup = sum(v["tokens_200k"] for g, v in payload["subtotals"].items() if g != "Per prompt")
    assert payload["total_tokens_200k"] == startup
    assert payload["per_prompt_tokens"] == payload["subtotals"]["Per prompt"]["tokens_200k"] > 0
    labels = " ".join(r["item"] for r in _rows(payload, "Model/settings") + pp)
    assert "Hook: prompt-guard UserPromptSubmit" in labels
    assert "Hook: market-beta UserPromptSubmit" in labels
    assert "Hook: decision-pack SessionStart" in labels
    assert "0.3.1" not in labels and "0.1.0" not in labels
    # each hooks.json counted once
    assert len([r for r in pp if "market-beta" in r["item"]]) == 1
    md = context_markdown(payload)
    assert "not in startup total" in md


# 5. redaction ---------------------------------------------------------------------
def test_redaction_keeps_kebab_names_and_prose() -> None:
    keep = [
        "token-budget",
        "secret-scanning",
        "auth-flow",
        "session-report",
        "secret-leak-watch",
        "~/.cursor/skills/token-budget/SKILL.md",
        "/home/u/.claude/skills/secret-scanning/SKILL.md",
        "| session-report | 12 | desc |",
        "Check auth, then retry.",
        "The token, as shown, is short.",
        "Tests pass.",
        "you pass",
    ]
    for text in keep:
        assert redact_secrets(text) == text, (text, redact_secrets(text))


def test_redaction_i18n_password_words() -> None:
    for text, secret in (
        ("Kennwort: Geheim99xyz", "Geheim99xyz"),
        ("Kennwort=Geheim99xyz", "Geheim99xyz"),
        ("senha: Segredo99xyz", "Segredo99xyz"),
        ("senha=Segredo99xyz", "Segredo99xyz"),
        ("パスワード: Himitsu99xyz", "Himitsu99xyz"),
        ("パスワード=Himitsu99xyz", "Himitsu99xyz"),
        ("あなたのパスワード: Himitsu99xyz", "Himitsu99xyz"),
    ):
        assert secret not in redact_secrets(text), (text, redact_secrets(text))


# 6. Grok Bot detection ------------------------------------------------------------
def test_grok_bot_detection_markers() -> None:
    def cloud(env: dict, paths: set[str]) -> bool:
        return is_grok_cloud(env, path_exists=lambda p: p in paths)[0]

    assert cloud({"SAND_BOX_TENANT_ID": "t", "SAND_BOX_STORE_ID": "s"}, set())
    assert cloud({"SAND_BOX_BOOT_ID": "b"}, {"/home/box/agent-data"})
    assert cloud({"SAND_BOX_TENANT_ID": "t"}, {"/home/box/agent-data"})
    assert not cloud({"SAND_BOX_TENANT_ID": "t"}, set())
    assert not cloud({}, {"/home/box/agent-data"})
    assert not cloud({}, {"/home/box", "/home/box/sand-data"})
    assert not cloud({"BOX_ID": "x"}, {"/home/box"})


# 7. low items ---------------------------------------------------------------------
def test_trigger_words_accepted() -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        roots = Roots("cursor", tdp, tdp, None, None, tdp / ".cursor", False)
        skills = []
        phrases = [
            "Apply to every TypeScript file in the repo",
            "Apply before committing any change to main",
            "Apply after running the formatter locally",
            "Runs whenever a new migration file appears",
            "Use this skill to draft release notes",
            "Use this skill when drafting release notes",
            "Use to inspect build artifacts quickly",
        ]
        for i, phrase in enumerate(phrases):
            d = tdp / f"s{i}"
            d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: s{i}\ndescription: {phrase}\n---\nb\n")
            skills.append(d / "SKILL.md")
        f = check_skill_descriptions(roots, skill_files=skills)
        assert not [x for x in f if "trigger" in x.finding], [x.finding for x in f]


def test_stale_names_default_empty_env_and_config() -> None:
    old = os.environ.pop("AI_AUDIT_STALE_NAMES", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            roots = Roots("cursor", tdp, tdp, None, None, tdp / ".cursor", False)
            skill = tdp / "SKILL.md"
            skill.write_text("---\nname: s\ndescription: Use when x\n---\nlegacy-name here\n")
            assert load_stale_names(roots) == ()
            assert check_stale_names(roots, skill_files=[skill], rule_files=[]) == []
            os.environ["AI_AUDIT_STALE_NAMES"] = "legacy-name, other-old"
            assert load_stale_names(roots) == ("legacy-name", "other-old")
            assert len(check_stale_names(roots, skill_files=[skill], rule_files=[])) == 1
            del os.environ["AI_AUDIT_STALE_NAMES"]
            cfg = tdp / ".config" / "ai-audit"
            cfg.mkdir(parents=True)
            (cfg / "config.toml").write_text('stale_names = ["legacy-name"]\n')
            assert load_stale_names(roots) == ("legacy-name",)
            assert len(check_stale_names(roots, skill_files=[skill], rule_files=[])) == 1
    finally:
        os.environ.pop("AI_AUDIT_STALE_NAMES", None)
        if old is not None:
            os.environ["AI_AUDIT_STALE_NAMES"] = old


def test_skills_wrapper_row_explained() -> None:
    rows = [r for r in _rows(_payload(), "Skill descriptions") if r["item"].startswith("Section wrapper")]
    assert len(rows) == 1
    desc = rows[0]["description"]
    assert "<agent_skills>" in desc and "budget" in desc
    assert rows[0]["tokens_1m"] is not None and rows[0]["tokens_1m"] < 50


def test_enabled_column_and_vscdb_keys() -> None:
    payload = _payload()
    skills = {r["item"]: r for r in _rows(payload, "Skill descriptions")}
    # vscdb says disabled: still listed, marked no, never excluded
    assert skills["vscdb-disabled-demo"]["enabled"] == "no"
    assert skills["standalone-tool"]["enabled"] == "yes"
    assert skills["flat-skill"]["enabled"] == "unknown"
    assert skills["claude-real"]["enabled"] == "yes"
    md = context_markdown(payload)
    assert "| Item | Tokens | Description | Source | Enabled |" in md
    keys = payload["state_vscdb_keys"]
    assert "aicontext.personalContext" in keys
    assert "cursor.plugins.installedIds.demo-market/disabled-pack" in keys
    assert not any("anysphere" in k or "LEAKED" in k for k in keys)
    for k in keys:
        assert f"`{k}`" in md, k
    assert "unverified" in md


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"PASS round20 tests={len(tests)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        import traceback

        traceback.print_exc()
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
