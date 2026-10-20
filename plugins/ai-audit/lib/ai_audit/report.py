"""JSON + short markdown report emitters."""

from __future__ import annotations

import json
from typing import Any

from .common import redact_secrets, redact_tree


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(redact_tree(payload), indent=2, sort_keys=True) + "\n"


def _recon_lines(recon: dict[str, Any] | None) -> list[str]:
    if not recon:
        return []
    if recon.get("no_data") or recon.get("status") == "no_data":
        match = "no data"
    elif recon.get("ok"):
        match = "yes"
    else:
        match = "NO"
    return [
        "## Reconciliation",
        "",
        f"- Source total: **{int(recon.get('source_total_tokens') or 0)}**",
        f"- Sum by_model: **{int(recon.get('by_model_sum') or 0)}**",
        f"- Sum by_kind: **{int(recon.get('by_kind_sum') or 0)}**",
        f"- Matches: **{match}**",
        "",
    ]


def context_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
    items = payload.get("inventory") or []
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
        "Sizes are approximate (chars ÷ 4) for inventory only.",
        "",
        "## Always-loaded",
        "",
    ]
    limit = 25
    ranked = sorted(always, key=lambda x: -int(x.get("est_tokens") or 0))
    for i in ranked[:limit]:
        path = redact_secrets(i.get("path") or "?")
        kind = i.get("kind") or ""
        tok = int(i.get("est_tokens") or 0)
        lines.append(f"- `{kind}` · ~{tok} tok · `{path}`")
    if len(ranked) > limit:
        lines.append(f"- … +{len(ranked) - limit} more (see JSON inventory)")
    if not always:
        lines.append("_None detected._")
    lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Read-only: no files were changed.",
            "- Secret-valued config keys and secret-shaped values were skipped or redacted.",
            "",
        ]
    )
    return "\n".join(lines)


def token_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
    summary = payload.get("summary") or {}
    notes = payload.get("notes") or []
    data = payload.get("data") or {}
    lines = [
        f"# Token audit ({host})",
        "",
    ]
    if summary:
        lines.append("## Summary")
        lines.append("")
        for k, v in summary.items():
            if k == "reconciliation":
                continue
            lines.append(f"- {k}: {redact_secrets(str(v))}")
        lines.append("")
    lines.extend(_recon_lines(summary.get("reconciliation") or data.get("reconciliation")))

    by_model = data.get("by_model") or {}
    if by_model:
        lines.append("## By model")
        lines.append("")
        for model, stats in list(by_model.items())[:15]:
            if isinstance(stats, dict):
                tot = int(stats.get("total_tokens") or 0)
            else:
                tot = int(stats or 0)
            lines.append(f"- `{model}`: {tot}")
        lines.append("")

    by_kind = data.get("by_kind") or {}
    if by_kind:
        lines.append("## By kind")
        lines.append("")
        for kind, stats in by_kind.items():
            tot = int((stats or {}).get("total_tokens") or 0)
            lines.append(f"- `{kind}`: {tot}")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    if notes:
        for n in notes:
            lines.append(f"- {redact_secrets(str(n))}")
    else:
        lines.append("- Prefer official auto sources; otherwise a user-downloaded usage file.")
        lines.append("- Never scrape login tokens or cookies.")
    lines.append("")
    lines.append("- Read-only: no files were changed. Secret-shaped values are redacted.")
    lines.append("")
    return "\n".join(lines)


def facts_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
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
        lines.append(f"> {redact_secrets(str(n))}")
        lines.append("")
    if issues:
        lines.append("## Issues")
        lines.append("")
        for iss in issues[:40]:
            kind = iss.get("kind", "issue")
            path = redact_secrets(iss.get("path", ""))
            detail = redact_secrets(iss.get("detail", ""))
            when = iss.get("date") or ""
            extra = f" ({when})" if when else ""
            if len(detail) > 200:
                detail = detail[:199] + "…"
            lines.append(f"- `{kind}`{extra}: {detail}")
            if path:
                lines.append(f"  - `{path}`")
        lines.append("")
    else:
        lines.append("_No stale, duplicate, conflict, or orphan issues found._")
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- Read-only: propose edits as a diff; do not edit memory without approval.",
            "- Fact quotes are capped (~80 chars); secret-shaped values are redacted.",
            "",
        ]
    )
    return "\n".join(lines)
