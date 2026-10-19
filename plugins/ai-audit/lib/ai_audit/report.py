"""JSON + short markdown report emitters."""

from __future__ import annotations

import json
from typing import Any


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def wins_markdown(wins: list[dict[str, Any]], heading: str = "Biggest wins") -> list[str]:
    lines = [f"## {heading}", ""]
    if not wins:
        lines.append("_No ranked wins — inventory was empty or below thresholds._")
        lines.append("")
        return lines
    for i, w in enumerate(wins, 1):
        title = w.get("title") or "win"
        saving = w.get("saving_est_tokens", 0)
        measured = w.get("measured") or ""
        rationale = w.get("rationale") or ""
        suggestion = w.get("suggestion") or ""
        path = w.get("path") or ""
        lines.append(f"{i}. **{title}** — ~{saving} est. tokens")
        if measured:
            lines.append(f"   - Measured: {measured}")
        if path:
            lines.append(f"   - Path: `{path}`")
        if rationale:
            lines.append(f"   - Why: {rationale}")
        if suggestion:
            lines.append(f"   - Suggestion (not applied): {suggestion}")
        lines.append("")
    return lines


def context_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
    items = payload.get("inventory") or []
    wins = payload.get("wins") or []
    always = [i for i in items if i.get("load") == "always"]
    always_tokens = sum(int(i.get("est_tokens") or 0) for i in always)
    total_tokens = sum(int(i.get("est_tokens") or 0) for i in items)

    lines = [
        f"# Context audit ({host})",
        "",
        f"Inventory: **{len(items)}** items · "
        f"~{total_tokens} est. tokens total · "
        f"~{always_tokens} always-loaded (~{len(always)} items).",
        "",
        "Token figures are approximate (chars ÷ 4) and for ranking only.",
        "",
    ]
    lines.extend(wins_markdown(wins))
    lines.extend(
        [
            "## Notes",
            "",
            "- Read-only: no files were changed.",
            "- Proposed fixes are suggestions only — show as a diff; never apply without approval.",
            "- Secret-valued config keys were skipped or redacted.",
            "",
        ]
    )
    return "\n".join(lines)


def token_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
    wins = payload.get("wins") or []
    summary = payload.get("summary") or {}
    estimate = bool(payload.get("estimate"))
    lines = [
        f"# Token audit ({host})",
        "",
    ]
    if estimate:
        lines.append(
            "**Estimate:** this host does not expose per-bot/tool token logs locally. "
            "Figures below are labeled estimates."
        )
        lines.append("")
    if summary:
        lines.append("## Summary")
        lines.append("")
        for k, v in summary.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    lines.extend(wins_markdown(wins))
    lines.extend(
        [
            "## Notes",
            "",
            "- Priority: cloud/dashboard usage first, local transcripts second.",
            "- Never scrape login tokens from app databases; use a downloaded CSV or optional Admin API key via env.",
            "",
        ]
    )
    return "\n".join(lines)


def facts_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
    wins = payload.get("wins") or []
    issues = payload.get("issues") or []
    facts = payload.get("facts") or []
    notes = payload.get("notes") or []
    lines = [
        f"# Facts audit ({host})",
        "",
        f"Facts scanned: **{len(facts)}** · Issues: **{len(issues)}**.",
        "",
    ]
    for n in notes:
        lines.append(f"> {n}")
        lines.append("")
    lines.extend(wins_markdown(wins, heading="Biggest cleanup wins"))
    if issues:
        lines.append("## Issues")
        lines.append("")
        for iss in issues[:20]:
            kind = iss.get("kind", "issue")
            path = iss.get("path", "")
            detail = iss.get("detail", "")
            when = iss.get("date") or ""
            extra = f" ({when})" if when else ""
            lines.append(f"- `{kind}`{extra}: {detail}")
            if path:
                lines.append(f"  - `{path}`")
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Read-only: propose edits as a diff; do not edit memory without approval.",
            "",
        ]
    )
    return "\n".join(lines)
