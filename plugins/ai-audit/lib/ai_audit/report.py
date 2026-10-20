"""JSON + grouped markdown report emitters (stage 1: context)."""

from __future__ import annotations

import json
from typing import Any

from .common import redact_tree


def to_json(payload: dict[str, Any]) -> str:
    return json.dumps(redact_tree(payload), indent=2, sort_keys=True) + "\n"


def _env_heading(payload: dict[str, Any]) -> str:
    info = payload.get("environment") or {}
    if isinstance(info, dict) and info.get("heading"):
        return str(info["heading"])
    host = payload.get("host", "unknown")
    labels = {"cursor": "Cursor", "claude": "Claude Code", "grok": "Grok Bot"}
    return f"{labels.get(host, host)} · Unknown"


def _legacy_context_markdown(payload: dict[str, Any]) -> str:
    heading = _env_heading(payload)
    items = payload.get("inventory") or []
    always = [i for i in items if i.get("load") == "always"]
    always_tokens = sum(int(i.get("est_tokens") or 0) for i in always)
    total_tokens = sum(int(i.get("est_tokens") or 0) for i in items)
    tok = payload.get("tokenizer") or "chars÷4 estimate"

    lines = [
        f"# Context audit — {heading}",
        "",
        f"Provider · environment: **{heading}**",
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
    ranked = sorted(
        always,
        key=lambda x: (-int(x.get("est_tokens") or 0), str(x.get("path") or "")),
    )
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


def _checks_markdown(payload: dict[str, Any]) -> list[str]:
    """Deterministic quality Checks table (facts only; already severity-sorted)."""
    checks = payload.get("checks") or []
    lines = [
        "### Checks",
        "",
        "Deterministic, token-free facts (no AI). Report each finding verbatim; "
        "do not add judgement.",
        "",
        "| Item | Check | Finding | Source |",
        "|---|---|---|---|",
    ]
    if not checks:
        lines.append("| _(none)_ |  |  |  |")
    else:
        for c in checks:
            item = str(c.get("item") or "").replace("|", "\\|")
            check = str(c.get("check") or "").replace("|", "\\|")
            finding = str(c.get("finding") or "").replace("|", "\\|")
            source = str(c.get("source") or "").replace("|", "\\|")
            lines.append(f"| {item} | {check} | {finding} | {source} |")
    lines.append("")
    return lines


def _per_prompt_line(payload: dict[str, Any]) -> list[str]:
    n = int(payload.get("per_prompt_tokens") or 0)
    if "Per prompt" not in (payload.get("groups") or []):
        return []
    return [
        f"Per-prompt hooks add **{n:,}** tokens on every prompt. They are listed "
        "under **Per prompt** and are not part of the startup total.",
        "",
    ]


def _one_section_markdown(payload: dict[str, Any]) -> list[str]:
    heading = _env_heading(payload)
    tokenizer = payload.get("tokenizer") or "tiktoken o200k_base"
    total_200k = int(payload.get("total_tokens_200k") or 0)
    total_1m = int(payload.get("total_tokens_1m") or 0)
    window = payload.get("context_window") or "200k"
    groups = payload.get("groups") or []
    rows = payload.get("rows") or []
    subtotals = payload.get("subtotals") or {}

    lines = [
        f"## {heading}",
        "",
        f"**Total at startup: {total_200k:,} tokens** (200k-context model). "
        f"**{total_1m:,}** on a 1M model. "
        f"Sort window: **{window}**.",
        "",
        *_per_prompt_line(payload),
        f"Tokenizer: **{tokenizer}** (offline, vendored).",
        "",
        "Legend: **Item**, **Tokens**, **Description**, and **Source** are "
        "scripted facts only (description is quoted/truncated from the file; "
        "source is a short path). The agent must not add generated text to the "
        "table. Skill rows show `200k (1M)` when descriptions differ by budget. "
        "Groups and rows are sorted by tokens descending.",
        "",
    ]

    for group in groups:
        grow = [r for r in rows if r.get("group") == group]
        sub = subtotals.get(group) or {}
        sub_200 = int(sub.get("tokens_200k") or 0)
        sub_1m = int(sub.get("tokens_1m") or 0)
        if group == "Skill descriptions":
            lines.append(f"### {group} · subtotal {sub_200:,} ({sub_1m:,})")
        elif group == "Per prompt":
            lines.append(f"### {group} · {sub_200:,} per prompt (not in startup total)")
        else:
            lines.append(f"### {group} · subtotal {sub_200:,}")
        lines.append("")
        lines.append("| Item | Tokens | Description | Source | Enabled |")
        lines.append("|---|---:|---|---|---|")
        for r in grow:
            item = str(r.get("item") or "").replace("|", "\\|")
            t200 = int(r.get("tokens") or 0)
            t1m = r.get("tokens_1m")
            if t1m is not None and int(t1m) != t200:
                tok_cell = f"{t200} ({int(t1m)})"
            else:
                tok_cell = str(t200)
            desc = str(r.get("description") or "").replace("|", "\\|")
            source = str(r.get("source") or "").replace("|", "\\|")
            enabled = str(r.get("enabled") or "yes")
            lines.append(f"| {item} | {tok_cell} | {desc} | {source} | {enabled} |")
        if not grow:
            lines.append("| _(none)_ | 0 |  |  |  |")
        lines.append("")

    lines.extend(_checks_markdown(payload))
    return lines


def grouped_context_markdown(payload: dict[str, Any]) -> str:
    """Grouped table report; one section per provider·environment."""
    sections = payload.get("sections")
    if isinstance(sections, list) and sections:
        lines = [
            "# Startup context report",
            "",
        ]
        for sec in sections:
            lines.extend(_one_section_markdown(sec))
            lines.append("")
    else:
        lines = [
            "# Startup context report",
            "",
        ]
        lines.extend(_one_section_markdown(payload))

    keys: list[str] = []
    for sec in (payload.get("sections") or [payload]):
        for k in sec.get("state_vscdb_keys") or []:
            if k not in keys:
                keys.append(k)
    key_list = ", ".join(f"`{k}`" for k in keys) if keys else "none (no state.vscdb found)"
    lines.extend(
        [
            "## Notes",
            "",
            "- Read-only: no files were changed; MCP servers were not launched.",
            "- User Rules: only `aicontext.personalContext` is read from "
            "`state.vscdb` via SQLite `mode=ro` (exact-key query; no copy).",
            f"- state.vscdb keys read (read-only): {key_list}.",
            "- Enabled column: `yes`/`no` for Cursor plugins comes from the "
            "`cursor.plugins.installedIds.*` keys, whose format is unverified, so "
            "no plugin is excluded or discounted because of it; `unknown` means no "
            "key matched. Claude Code plugins use `enabledPlugins` in settings.json. "
            "Everything else is `yes` (present on disk).",
            "- Secret-shaped values in notes/meta were redacted.",
            "- Facts only: do not invent Purpose/suggestion columns.",
            "- Checks: deterministic findings only — report verbatim, no judgement.",
            "",
        ]
    )
    return "\n".join(lines)


def context_markdown(payload: dict[str, Any]) -> str:
    if payload.get("rows") and payload.get("groups"):
        return grouped_context_markdown(payload)
    if payload.get("sections"):
        return grouped_context_markdown(payload)
    return _legacy_context_markdown(payload)
