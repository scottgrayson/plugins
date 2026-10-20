"""JSON + grouped markdown report emitters (stage 1: context)."""

from __future__ import annotations

import json
from typing import Any

from .common import redact_tree


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(redact_tree(payload), indent=2, sort_keys=True) + "\n"


def _legacy_context_markdown(payload: dict[str, Any]) -> str:
    host = payload.get("host", "unknown")
    items = payload.get("inventory") or []
    always = [i for i in items if i.get("load") == "always"]
    always_tokens = sum(int(i.get("est_tokens") or 0) for i in always)
    total_tokens = sum(int(i.get("est_tokens") or 0) for i in items)
    tok = payload.get("tokenizer") or "chars÷4 estimate"

    lines = [
        f"# Context audit ({host})",
        "",
        f"Inventory: **{len(items)}** items · "
        f"~{total_tokens} est. tokens total · "
        f"~{always_tokens} always-loaded (~{len(always)} items).",
        "",
        f"Tokenizer: **{tok}** (legacy host path uses chars÷4 for inventory bytes).",
        "",
        "## Always-loaded",
        "",
    ]
    limit = 25
    ranked = sorted(always, key=lambda x: -int(x.get("est_tokens") or 0))
    for i in ranked[:limit]:
        path = i.get("path") or "?"
        kind = i.get("kind") or ""
        t = int(i.get("est_tokens") or 0)
        lines.append(f"- `{kind}` · ~{t} tok · `{path}`")
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


def grouped_context_markdown(payload: dict[str, Any]) -> str:
    """Grouped table report (Cursor startup measurements)."""
    host = payload.get("host", "cursor")
    tokenizer = payload.get("tokenizer") or "tiktoken o200k_base"
    total_200k = int(payload.get("total_tokens_200k") or 0)
    total_1m = int(payload.get("total_tokens_1m") or 0)
    groups = payload.get("groups") or []
    rows = payload.get("rows") or []
    subtotals = payload.get("subtotals") or {}

    lines = [
        f"# Startup context report ({host})",
        "",
        f"**Total at startup: {total_200k:,} tokens** (200k-context model). "
        f"**{total_1m:,}** on a 1M model.",
        "",
        f"Tokenizer: **{tokenizer}** (offline, vendored).",
        "",
        "Legend: **Item** and **Tokens** are scripted. "
        "**Purpose** and **AI suggestion** are blank for the agent to fill "
        "(see context-audit skill). Skill rows show `200k (1M)` when descriptions "
        "differ by budget.",
        "",
    ]

    for group in groups:
        grow = [r for r in rows if r.get("group") == group]
        sub = subtotals.get(group) or {}
        sub_200 = int(sub.get("tokens_200k") or 0)
        sub_1m = int(sub.get("tokens_1m") or 0)
        if group == "Skill descriptions":
            lines.append(f"## {group} · subtotal {sub_200:,} ({sub_1m:,})")
        else:
            lines.append(f"## {group} · subtotal {sub_200:,}")
        lines.append("")
        lines.append("| Item | Tokens | Purpose | AI suggestion |")
        lines.append("|---|---:|---|---|")
        for r in grow:
            item = str(r.get("item") or "").replace("|", "\\|")
            t200 = int(r.get("tokens") or 0)
            t1m = r.get("tokens_1m")
            if t1m is not None and int(t1m) != t200:
                tok_cell = f"{t200} ({int(t1m)})"
            else:
                tok_cell = str(t200)
            purpose = str(r.get("purpose") or "")
            suggestion = str(r.get("suggestion") or "")
            lines.append(f"| {item} | {tok_cell} | {purpose} | {suggestion} |")
        if not grow:
            lines.append("| _(none)_ | 0 |  |  |")
        lines.append("")

    lines.extend(
        [
            "## Notes",
            "",
            "- Read-only: no files were changed; MCP servers were not launched.",
            "- User Rules: only `aicontext.personalContext` is read from a temp "
            "copy of `state.vscdb` (exact-key query).",
            "- Secret-shaped values in notes/meta were redacted.",
            "",
        ]
    )
    return "\n".join(lines)


def context_markdown(payload: dict[str, Any]) -> str:
    if payload.get("rows") and payload.get("groups"):
        return grouped_context_markdown(payload)
    return _legacy_context_markdown(payload)
