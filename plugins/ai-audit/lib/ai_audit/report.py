"""JSON + short markdown report emitters (stage 1: context)."""

from __future__ import annotations

import json
from typing import Any

from .common import redact_tree


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(redact_tree(payload), indent=2, sort_keys=True) + "\n"


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
        # Inventory paths are never secret-redacted (AWS-shape false positives).
        path = i.get("path") or "?"
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
