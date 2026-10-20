#!/usr/bin/env python3
"""Offline tests for provider · environment detection and report sorting."""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = Path(__file__).resolve().parents[1]
FIX = PLUGIN / "fixtures" / "cursor"
sys.path.insert(0, str(LIB))

from ai_audit.environment import (  # noqa: E402
    detect_environment,
    is_claude_cloud,
    is_cursor_cloud,
    local_os_label,
)
from ai_audit.cursor_startup import sort_grouped_rows  # noqa: E402
from ai_audit.detect import detect_host  # noqa: E402
from ai_audit.context_audit import run_context_audit  # noqa: E402
from ai_audit.report import context_markdown  # noqa: E402


def test_local_os() -> None:
    assert local_os_label("Darwin") == "Mac"
    assert local_os_label("Linux") == "Linux"
    assert local_os_label("Windows") == "Windows"


def test_cursor_cloud_markers() -> None:
    # Bare CURSOR_AGENT alone is not enough (local CLI also sets it).
    # HOSTNAME overridden so this cloud VM does not add hostname=cursor.
    cloud, hits = is_cursor_cloud(
        {"HOSTNAME": "scotts-macbook", "CURSOR_AGENT": "1"},
        path_exists=lambda p: False,
    )
    assert cloud is False
    # Single marker is not enough (D4: need >=2)
    cloud, hits = is_cursor_cloud(
        {"HOSTNAME": "scotts-macbook", "CURSOR_AGENT_WORKER_ID": "w-1"},
        path_exists=lambda p: False,
    )
    assert cloud is False
    # Local ~/.cursor/agent.sock must not count (HOSTNAME overridden so this
    # cloud VM's hostname=cursor does not add a second marker).
    cloud, hits = is_cursor_cloud(
        {
            "HOSTNAME": "scotts-macbook",
            "CURSOR_AGENT_SOCKET": "/Users/scott/.cursor/agent.sock",
            "CURSOR_AGENT_WORKER_ID": "w-1",
        },
        path_exists=lambda p: False,
    )
    assert cloud is False, hits
    cloud, hits = is_cursor_cloud(
        {
            "HOSTNAME": "scotts-macbook",
            "CURSOR_AGENT": "1",
            "CURSOR_AGENT_SOCKET": "/run/cursor/api.sock",
            "CLOUD_AGENT_ALL_SECRET_NAMES": "X",
        },
        path_exists=lambda p: p == "/exec-daemon",
    )
    assert cloud is True
    assert "CURSOR_AGENT_SOCKET" in hits or "CLOUD_AGENT_*" in hits

    # Local CLI mode wins
    cloud, hits = is_cursor_cloud(
        {
            "HOSTNAME": "scotts-macbook",
            "CURSOR_AGENT": "1",
            "CURSOR_AGENT_CLI_LOCAL_MODE": "true",
            "CURSOR_AGENT_WORKER_ID": "w-1",
        },
        path_exists=lambda p: False,
    )
    assert cloud is False

    # Falsy values (no/off/0/false) are not markers (D4)
    cloud, hits = is_cursor_cloud(
        {
            "HOSTNAME": "scotts-macbook",
            "CURSOR_AGENT_WORKER_ID": "no",
            "CURSOR_WORKER_POOL_NAME": "false",
            "CLOUD_AGENT_ALL_SECRET_NAMES": "0",
            "CURSOR_WORKLOAD_CGROUP": "off",
        },
        path_exists=lambda p: False,
    )
    assert cloud is False


def test_claude_cloud_markers() -> None:
    # Round-19: Claude needs ≥2 markers
    assert is_claude_cloud({"CLAUDE_CODE_ENVIRONMENT_KIND": "byoc"})[0] is False
    assert is_claude_cloud({"CLAUDE_CODE_ENTRYPOINT": "remote"})[0] is False
    assert is_claude_cloud(
        {
            "CLAUDE_CODE_ENVIRONMENT_KIND": "byoc",
            "CLAUDE_CODE_ENTRYPOINT": "remote",
        }
    )[0] is True
    assert is_claude_cloud(
        {
            "CLAUDE_CODE_REMOTE_SESSION_ID": "rs",
            "CLAUDE_CODE_REMOTE": "1",
        }
    )[0] is True
    assert is_claude_cloud({"CLAUDE_CODE_ENTRYPOINT": "cli"})[0] is False
    assert is_claude_cloud({"CLAUDE_CODE_REMOTE": "no"})[0] is False


def test_grok_cloud_sand_data_alone() -> None:
    from ai_audit.environment import is_grok_cloud

    # sand-data path alone is not enough (fixture / local desktop)
    cloud, hits = is_grok_cloud(
        {},
        sand_data=Path("/tmp/fixtures/grok/sand-data"),
        path_exists=lambda p: False,
    )
    assert cloud is False
    # /home/box alone is not enough (box without SAND_BOX_ID)
    cloud, hits = is_grok_cloud(
        {},
        path_exists=lambda p: p == "/home/box",
    )
    assert cloud is False
    # GROK_BOT=no is falsy
    cloud, hits = is_grok_cloud(
        {"GROK_BOT": "no"},
        path_exists=lambda p: p == "/home/box",
    )
    assert cloud is False
    cloud, hits = is_grok_cloud(
        {"GROK_BOT": "1"},
        path_exists=lambda p: p == "/home/box",
    )
    assert cloud is True
    # Box-only pairing: SAND_BOX_ID + /home/box
    cloud, hits = is_grok_cloud(
        {"SAND_BOX_ID": "box-1"},
        path_exists=lambda p: p == "/home/box",
    )
    assert cloud is True


def test_override_and_heading() -> None:
    info = detect_environment("cursor", override="Mac")
    assert info["heading"] == "Cursor · Mac"
    info = detect_environment("claude", override="Claude Code · Cloud")
    assert info["heading"] == "Claude Code · Cloud"
    info = detect_environment("grok", override="Cloud")
    assert info["heading"] == "Grok Bot · Cloud"
    # D4: --env text with secrets is sanitised / redacted
    info = detect_environment("cursor", override="x · password=Hunter2Fake")
    assert "Hunter2Fake" not in info["heading"]
    assert "REDACTED" in info["heading"] or info["environment"] in {
        "Mac",
        "Linux",
        "Windows",
        "Cloud",
    }


def test_sort_groups_and_rows() -> None:
    rows = [
        {"group": "A", "item": "small", "tokens": 10},
        {"group": "A", "item": "big", "tokens": 100},
        {"group": "A", "item": "mid", "tokens": 50},
        {"group": "B", "item": "x", "tokens": 5},
        {
            "group": "Skill descriptions",
            "item": "skill-hi-1m",
            "tokens": 10,
            "tokens_1m": 500,
        },
        {
            "group": "Skill descriptions",
            "item": "skill-hi-200k",
            "tokens": 200,
            "tokens_1m": 20,
        },
    ]
    sub = {
        "A": {"tokens_200k": 160, "tokens_1m": 160},
        "B": {"tokens_200k": 5, "tokens_1m": 5},
        "Skill descriptions": {"tokens_200k": 210, "tokens_1m": 520},
    }
    groups, sorted_rows = sort_grouped_rows(rows, sub, window="200k")
    # Skills subtotal 210 > A 160 > B 5
    assert groups[0] == "Skill descriptions"
    assert groups[1] == "A"
    skill = [r for r in sorted_rows if r["group"] == "Skill descriptions"]
    assert skill[0]["item"] == "skill-hi-200k"  # sorted by 200k tokens
    a_rows = [r for r in sorted_rows if r["group"] == "A"]
    assert [r["item"] for r in a_rows] == ["big", "mid", "small"]

    groups1, sorted1 = sort_grouped_rows(rows, sub, window="1m")
    assert groups1[0] == "Skill descriptions"
    skill1 = [r for r in sorted1 if r["group"] == "Skill descriptions"]
    assert skill1[0]["item"] == "skill-hi-1m"  # sorted by 1M tokens


def test_fixture_report_heading_and_sort() -> None:
    roots = detect_host(host="cursor", root=FIX)
    payload = run_context_audit(
        roots, env_override="Cursor · Mac", context_window="200k"
    )
    assert payload["environment"]["heading"] == "Cursor · Mac"
    md = context_markdown(payload)
    assert "Cursor · Mac" in md
    # Groups ordered by subtotal desc
    groups = payload["groups"]
    subs = payload["subtotals"]
    scores = [subs[g]["tokens_200k"] for g in groups]
    assert scores == sorted(scores, reverse=True), (groups, scores)
    # Within a group, rows descending by tokens
    for g in groups:
        grow = [r for r in payload["rows"] if r["group"] == g]
        toks = [int(r["tokens"]) for r in grow]
        assert toks == sorted(toks, reverse=True), (g, toks)


def main() -> int:
    test_local_os()
    test_cursor_cloud_markers()
    test_claude_cloud_markers()
    test_grok_cloud_sand_data_alone()
    test_override_and_heading()
    test_sort_groups_and_rows()
    test_fixture_report_heading_and_sort()
    print("PASS environment detection + sort")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"FAIL {e}", file=sys.stderr)
        raise SystemExit(1)
