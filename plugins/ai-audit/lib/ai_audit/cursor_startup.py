"""
Cursor startup-context measurement (skills budget, MCP, User Rules, hooks).

Read-only. Never launches MCP servers. Never reads anysphere.cursor-mcp.* DB keys.
Tokenizer: vendored tiktoken o200k_base (offline).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from .common import (
    load_json_safe,
    parse_front_matter,
    redact_secrets,
    safe_read_text,
)
from .detect import Roots
from .tokenize import count_tokens, tokenizer_label

# Cursor skills section budget = 2% of agent token limit.
_AGENT_LIMIT_200K = 200_000
_AGENT_LIMIT_1M = 1_000_000
_MAX_MCP_TOOLS = 100
_WARN_MCP_TOOLS = 80

_CODEX_BUILTIN_BLOCKLIST = frozenset(
    {
        "imagegen",
        "openai-docs",
        "plugin-creator",
        "skill-creator",
        "skill-installer",
    }
)

# Measured ~637 o200k on Scott's Mac (gaps-2026-10-20); vendored stand-in text.
_MCP_PREAMBLE = (Path(__file__).with_name("mcp_preamble.txt").read_text(encoding="utf-8"))

_USER_RULES_KEY = "aicontext.personalContext"


@dataclass
class ReportRow:
    group: str
    item: str
    tokens: int
    tokens_1m: Optional[int] = None  # skills: 200k vs 1M
    purpose: str = ""  # blank — agent fills
    suggestion: str = ""  # blank — agent fills
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _skill_xml(full_path: str, description: str | None) -> str:
    if description is None:
        return f'<agent_skill fullPath="{full_path}"></agent_skill>'
    return f'<agent_skill fullPath="{full_path}">{description}</agent_skill>'


def _rule_xml(name: str, body: str) -> str:
    return f'<always_applied_workspace_rule name="{name}">{body}</always_applied_workspace_rule>'


def _mcp_server_line(name: str, folder: str) -> str:
    return f'<mcp_file_system_server name="{name}" folderPath="{folder}"/>'


def _has_hidden_segment(path: Path) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in path.parts
               if part not in path.anchor)


def _iter_skill_mds(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        # Skip hidden path segments (e.g. ~/.codex/skills/.system/…)
        try:
            rel = skill_md.relative_to(root)
        except ValueError:
            rel = skill_md
        if any(p.startswith(".") for p in rel.parts[:-1]):
            continue
        out.append(skill_md)
    return out


def _active_plugin_roots(cursor_home: Path) -> list[Path]:
    """
    Plugin roots to scan: all of plugins/local, plus only the newest version
    per plugin id under plugins/cache (skip stale cached versions — D7).
    """
    roots: list[Path] = []
    local = cursor_home / "plugins" / "local"
    if local.is_dir():
        roots.extend(sorted(p for p in local.iterdir() if p.is_dir()))
    cache = cursor_home / "plugins" / "cache"
    if not cache.is_dir():
        return roots
    # cache layout: cache/<marketplace>/<plugin>/<version>/
    by_plugin: dict[str, list[Path]] = {}
    for market in cache.iterdir():
        if not market.is_dir():
            continue
        for plugin in market.iterdir():
            if not plugin.is_dir():
                continue
            versions = [v for v in plugin.iterdir() if v.is_dir()]
            if not versions:
                # flat plugin dir
                by_plugin.setdefault(plugin.name, []).append(plugin)
                continue
            # Prefer newest by mtime, then name
            newest = max(versions, key=lambda p: (p.stat().st_mtime, p.name))
            by_plugin.setdefault(f"{market.name}/{plugin.name}", []).append(newest)
    for paths in by_plugin.values():
        roots.extend(paths)
    return roots


def _plugin_skill_roots(cursor_home: Path) -> list[tuple[Path, str]]:
    """Return (skills_dir, source_label) for active Cursor plugins only."""
    found: list[tuple[Path, str]] = []
    for plugin_root in _active_plugin_roots(cursor_home):
        skills = plugin_root / "skills"
        if skills.is_dir():
            found.append((skills, f"plugin:{plugin_root.name}"))
    return found


def _collect_skill_entries(
    roots: Roots,
) -> list[dict[str, Any]]:
    """Enumerate skills with realpath dedupe (symlinks collapse; same name does not)."""
    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    project = roots.project
    roots_list: list[tuple[Path, str]] = [
        (cursor_home / "skills", "user_cursor"),
        (cursor_home / "skills-cursor", "builtin"),
        (project / ".cursor" / "skills", "project_cursor"),
        (home / ".claude" / "skills", "user_claude"),
        (project / ".claude" / "skills", "project_claude"),
        (home / ".codex" / "skills", "user_codex"),
        (project / ".codex" / "skills", "project_codex"),
        (home / ".agents" / "skills", "user_agents"),
        (project / ".agents" / "skills", "project_agents"),
        (home / ".grok" / "skills", "user_grok"),
        (project / ".grok" / "skills", "project_grok"),
    ]
    roots_list.extend(_plugin_skill_roots(cursor_home))

    by_realpath: dict[str, dict[str, Any]] = {}
    name_paths: dict[str, list[str]] = {}

    for root, source in roots_list:
        for skill_md in _iter_skill_mds(root):
            text = safe_read_text(skill_md) or ""
            meta, _ = parse_front_matter(text)
            name = str(meta.get("name") or skill_md.parent.name)
            if name in _CODEX_BUILTIN_BLOCKLIST:
                continue
            if meta.get("disable-model-invocation"):
                continue
            try:
                real = str(skill_md.resolve())
            except OSError:
                real = str(skill_md)
            desc = str(meta.get("description") or "").strip()
            entry = {
                "name": name,
                "path": str(skill_md),
                "realpath": real,
                "source": source,
                "description": desc,
            }
            if real in by_realpath:
                # Symlink / same file — keep first, note alias.
                by_realpath[real].setdefault("aliases", []).append(str(skill_md))
                continue
            by_realpath[real] = entry
            name_paths.setdefault(name, []).append(str(skill_md))

    entries = list(by_realpath.values())
    for e in entries:
        others = [p for p in name_paths.get(e["name"], []) if p != e["path"]]
        # Same name, different realpath → duplicate copies (not collapsed).
        e["same_name_copies"] = others
        e["dup"] = bool(others) or bool(e.get("aliases"))
    return entries


def _budget_render(
    entries: list[dict[str, Any]], agent_limit: int
) -> tuple[list[dict[str, Any]], int, str]:
    """
    Simulate Cursor's 2% skills budget cascade.
    Returns (rendered rows with tokens, section_total, mode).
    mode: full | shortened | path_only | omitted
    """
    budget = int(agent_limit * 0.02)
    # Strategy 1: full descriptions
    full_xmls = [
        (_skill_xml(e["path"], e["description"]), e) for e in entries
    ]
    full_total = sum(count_tokens(x) for x, _ in full_xmls)
    wrapper = count_tokens("<agent_skills>\n</agent_skills>\n")
    if full_total + wrapper <= budget:
        rows = []
        for xml, e in full_xmls:
            rows.append({**e, "xml": xml, "tokens": count_tokens(xml), "mode": "full"})
        return rows, full_total + wrapper, "full"

    # Strategy 2: shortened descriptions (24–480 chars)
    short_xmls = []
    for e in entries:
        d = e["description"]
        if len(d) > 480:
            d = d[:480]
        elif 0 < len(d) < 24:
            d = d  # keep as-is
        short_xmls.append((_skill_xml(e["path"], d), e))
    short_total = sum(count_tokens(x) for x, _ in short_xmls)
    if short_total + wrapper <= budget:
        rows = []
        for xml, e in short_xmls:
            rows.append({**e, "xml": xml, "tokens": count_tokens(xml), "mode": "shortened"})
        return rows, short_total + wrapper, "shortened"

    # Strategy 3: path only
    path_xmls = [(_skill_xml(e["path"], None), e) for e in entries]
    # Drop skills from the end until under budget (omit notice).
    kept: list[tuple[str, dict[str, Any]]] = []
    running = wrapper + count_tokens('Additional skills omitted\n')
    omitted = 0
    for xml, e in path_xmls:
        t = count_tokens(xml)
        if running + t > budget:
            omitted += 1
            continue
        running += t
        kept.append((xml, e))
    rows = []
    for xml, e in kept:
        rows.append({**e, "xml": xml, "tokens": count_tokens(xml), "mode": "path_only"})
    mode = "path_only" if omitted == 0 else "path_only_omitted"
    return rows, running, mode


def _always_apply_rules(roots: Roots) -> list[ReportRow]:
    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    project = roots.project
    rows: list[ReportRow] = []
    seen: set[str] = set()

    def add_rule(path: Path, source: str) -> None:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        text = safe_read_text(path) or ""
        meta, body = parse_front_matter(text)
        if meta.get("alwaysApply") is not True:
            return
        seen.add(key)
        xml = _rule_xml(str(path), body.strip())
        rows.append(
            ReportRow(
                group="Always-apply rules",
                item=path.name,
                tokens=count_tokens(xml),
                meta={"path": str(path), "source": source, "alwaysApply": True},
            )
        )

    for rules_dir, source in (
        (project / ".cursor" / "rules", "project"),
        (cursor_home / "rules", "user"),
    ):
        if rules_dir.is_dir():
            for path in sorted(rules_dir.rglob("*.mdc")):
                add_rule(path, source)

    # Plugin rules dirs (active versions only — D7)
    for plugin_root in _active_plugin_roots(cursor_home):
        rules_dir = plugin_root / "rules"
        if not rules_dir.is_dir():
            continue
        for path in sorted(rules_dir.glob("*.mdc")):
            add_rule(path, f"plugin:{plugin_root.name}")

    return rows


def _find_state_vscdb(roots: Roots) -> Optional[Path]:
    """Locate state.vscdb without reading any MCP/auth keys."""
    env = os.environ.get("AI_AUDIT_CURSOR_STATE_VSCDB")
    if env and Path(env).is_file():
        return Path(env)
    home = roots.home
    candidates = [
        home
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "globalStorage"
        / "state.vscdb",
        home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb",
        # Fixture shorthand under cursor home
        (roots.cursor_home or home / ".cursor")
        / "User"
        / "globalStorage"
        / "state.vscdb",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _open_vscdb_readonly(db_path: Path) -> sqlite3.Connection:
    """
    Open state.vscdb read-only without copying (round-18 D2).

    Uses SQLite URI ``mode=ro&immutable=0``. ``immutable=0`` (explicit) lets
    SQLite read a sibling ``-wal`` when present so recent Cursor writes are
    visible, without taking a writer lock or creating a temp copy. If a future
    SQLite build refused WAL under ``mode=ro``, keep ``immutable=0`` (never
    ``immutable=1``, which ignores WAL). We never SELECT keys other than
    aicontext.personalContext — no copies, no temp files.
    """
    # Absolute path as URI (quote special chars; keep /).
    uri = (
        "file:"
        + quote(str(db_path.resolve()), safe="/:")
        + "?mode=ro&immutable=0"
    )
    return sqlite3.connect(uri, uri=True)


def _read_user_rules(roots: Roots) -> ReportRow:
    """
    Read ONLY aicontext.personalContext from state.vscdb (read-only URI).
    No temp copies. Exact-key query. Never SELECT other keys.
    """
    db_path = _find_state_vscdb(roots)
    if db_path is None:
        return ReportRow(
            group="User Rules",
            item="In-app User Rules",
            tokens=0,
            meta={"missing": True, "key": _USER_RULES_KEY},
        )

    conn = _open_vscdb_readonly(db_path)
    try:
        cur = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            (_USER_RULES_KEY,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    value = ""
    if row and row[0] is not None:
        raw = row[0]
        if isinstance(raw, bytes):
            value = raw.decode("utf-8", errors="replace")
        else:
            value = str(raw)
    tokens = count_tokens(value) if value else 0
    return ReportRow(
        group="User Rules",
        item="In-app User Rules",
        tokens=tokens,
        meta={
            "key": _USER_RULES_KEY,
            "bytes": len(value.encode("utf-8")),
            "empty": not bool(value.strip()),
            "db": str(db_path),
            "open": "sqlite mode=ro (no copy)",
        },
    )


def _cursor_project_slug(project: Path) -> str:
    """Cursor encodes absolute paths as slugs (/, \\, : → -)."""
    try:
        raw = str(project.resolve())
    except OSError:
        raw = str(project)
    return re.sub(r"[/\\:]+", "-", raw).strip("-")


def _mcp_project_dir(roots: Roots) -> tuple[Optional[Path], str]:
    """
    Pick MCP tool descriptors deterministically (D7).
    Prefer ~/.cursor/projects/<exact-slug>/mcps for cwd; else note user-level.
    Returns (mcps_dir_or_None, selection_note).
    """
    cursor_home = roots.cursor_home or (roots.home / ".cursor")
    projects = cursor_home / "projects"
    slug = _cursor_project_slug(roots.project)
    if projects.is_dir():
        exact = projects / slug / "mcps"
        if exact.is_dir():
            return exact, f"project mcps via slug {slug}"
        # Fallback: unique directory whose name equals the project basename
        # (fixtures use short names like fixture-proj).
        basename = roots.project.name
        matches = [
            p / "mcps"
            for p in projects.iterdir()
            if p.is_dir() and (p / "mcps").is_dir() and p.name == basename
        ]
        if len(matches) == 1:
            return matches[0], f"project mcps via basename {basename}"
        # Contained slug match only when unique
        contained = [
            p / "mcps"
            for p in projects.iterdir()
            if p.is_dir()
            and (p / "mcps").is_dir()
            and basename
            and basename.lower() in p.name.lower()
        ]
        if len(contained) == 1:
            return contained[0], f"project mcps via unique name match {contained[0].parent.name}"
    return None, "no project mcps dir; server lines from mcp.json only"


def _mcp_server_names(roots: Roots) -> list[str]:
    cursor_home = roots.cursor_home or (roots.home / ".cursor")
    names: list[str] = []
    seen: set[str] = set()
    for cfg in (
        cursor_home / "mcp.json",
        roots.project / ".cursor" / "mcp.json",
    ):
        data = load_json_safe(cfg)
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers") or data.get("servers") or {}
        if isinstance(servers, dict):
            for name in servers:
                if name not in seen:
                    seen.add(name)
                    names.append(str(name))
    # Active plugin mcp.json only (skip stale cached versions — D7)
    for plugin_root in _active_plugin_roots(cursor_home):
        paths = [plugin_root / "mcp.json"]
        if not paths[0].is_file():
            paths = [p for p in plugin_root.glob("**/mcp.json") if p.is_file()]
        for path in paths:
            if not path.is_file():
                continue
            data = load_json_safe(path)
            if not isinstance(data, dict):
                continue
            servers = data.get("mcpServers") or data.get("servers") or {}
            if isinstance(servers, dict):
                for name in servers:
                    label = (
                        f"plugin-{name}"
                        if not str(name).startswith("plugin-")
                        else str(name)
                    )
                    if label not in seen:
                        seen.add(label)
                        names.append(label)
    return names


def _mcp_rows(roots: Roots) -> list[ReportRow]:
    rows: list[ReportRow] = [
        ReportRow(
            group="MCP servers",
            item="MCP file-system preamble",
            tokens=count_tokens(_MCP_PREAMBLE),
            meta={"fixed": True, "measured_o200k": True},
        )
    ]
    mcps_root, selection_note = _mcp_project_dir(roots)
    names = _mcp_server_names(roots)
    # Also discover descriptor folders even if not in mcp.json
    folder_names: list[str] = []
    if mcps_root and mcps_root.is_dir():
        folder_names = sorted(p.name for p in mcps_root.iterdir() if p.is_dir())
    all_names = list(dict.fromkeys([*names, *folder_names]))

    total_tools = 0
    name_counts: dict[str, int] = {}
    for name in all_names:
        name_counts[name] = name_counts.get(name, 0) + 1
        tools_dir = (mcps_root / name / "tools") if mcps_root else None
        tool_files = (
            sorted(tools_dir.glob("*.json")) if tools_dir and tools_dir.is_dir() else []
        )
        tool_count = len(tool_files)
        total_tools += tool_count
        schema_text = ""
        for tf in tool_files:
            schema_text += safe_read_text(tf) or ""
        schema_tokens = count_tokens(schema_text) if schema_text else 0
        folder_raw = str(tools_dir.parent) if tools_dir else f"(no descriptors)/{name}"
        # D8: redact credentials that may appear in folderPath (URL userinfo, etc.)
        folder = redact_secrets(folder_raw)
        line = _mcp_server_line(name, folder)
        line_tokens = count_tokens(line)
        flags: list[str] = []
        # Heuristics from descriptors (read-only; never launch)
        if tool_count == 0:
            flags.append("failed-to-start-or-empty")
        if tool_count == 1 and tool_files:
            raw = (safe_read_text(tool_files[0]) or "").lower()
            if "mcp_auth" in raw or '"name": "mcp_auth"' in raw.replace(" ", ""):
                flags.append("needs-auth")
        if name_counts[name] > 1 or any(
            n != name and n.rstrip("0123456789") == name.rstrip("0123456789")
            for n in all_names
            if n != name
        ):
            # duplicate-ish names (plugin-clickup vs clickup flagged in item text)
            pass
        if tool_count > _MAX_MCP_TOOLS:
            flags.append(f"over-{_MAX_MCP_TOOLS}-tool-cap")
        item = f"{name} ({tool_count} tools)"
        if flags:
            item += f" [{', '.join(flags)}]"
        rows.append(
            ReportRow(
                group="MCP servers",
                item=item,
                tokens=line_tokens,
                meta={
                    "server": name,
                    "tool_count": tool_count,
                    "schema_tokens_on_demand": schema_tokens,
                    "flags": flags,
                    "folder": folder,
                    "mcp_selection": selection_note,
                },
            )
        )

    # Overlap / duplicate server names (clickup vs plugin-clickup)
    bases: dict[str, list[str]] = {}
    for name in all_names:
        base = re.sub(r"^plugin-", "", name)
        bases.setdefault(base, []).append(name)
    for base, group in bases.items():
        if len(group) > 1:
            rows.append(
                ReportRow(
                    group="MCP servers",
                    item=f"duplicate servers: {', '.join(group)}",
                    tokens=0,
                    meta={"duplicate": group},
                )
            )

    if total_tools > _MAX_MCP_TOOLS:
        rows.append(
            ReportRow(
                group="MCP servers",
                item=f"total tools {total_tools} over maxMcpTools={_MAX_MCP_TOOLS}",
                tokens=0,
                meta={"total_tools": total_tools, "cap": _MAX_MCP_TOOLS},
            )
        )
    elif total_tools > _WARN_MCP_TOOLS:
        rows.append(
            ReportRow(
                group="MCP servers",
                item=f"total tools {total_tools} above warnMcpTools={_WARN_MCP_TOOLS}",
                tokens=0,
                meta={"total_tools": total_tools, "warn": _WARN_MCP_TOOLS},
            )
        )
    return rows


def _hooks_rows(roots: Roots) -> list[ReportRow]:
    """
    Parse active-plugin hooks.json.

    Static `additionalContext` is counted. Script/command hooks inject runtime
    context that we cannot measure offline (measured ~156 on Scott's Mac) —
    report tokens=0 with an explicit unmeasured reason (D7).
    """
    rows: list[ReportRow] = []
    cursor_home = roots.cursor_home or (roots.home / ".cursor")
    for plugin_root in _active_plugin_roots(cursor_home):
        hooks_paths = [plugin_root / "hooks.json"]
        if not hooks_paths[0].is_file():
            hooks_paths = [p for p in plugin_root.glob("**/hooks.json") if p.is_file()]
        for hooks_path in hooks_paths:
            if not hooks_path.is_file():
                continue
            data = load_json_safe(hooks_path)
            if not isinstance(data, dict):
                continue
            hooks = data.get("hooks") or data
            if not isinstance(hooks, dict):
                continue
            for event, entries in hooks.items():
                if not isinstance(entries, list):
                    continue
                for ent in entries:
                    if not isinstance(ent, dict):
                        continue
                    ctx = ent.get("additionalContext") or ent.get("additional_context")
                    if isinstance(ctx, str) and ctx.strip():
                        rows.append(
                            ReportRow(
                                group="Model/settings",
                                item=f"Hook: {hooks_path.parent.name} {event}",
                                tokens=count_tokens(ctx),
                                meta={"path": str(hooks_path), "event": event},
                            )
                        )
                    else:
                        cmd = str(ent.get("command") or ent.get("script") or "")
                        rows.append(
                            ReportRow(
                                group="Model/settings",
                                item=(
                                    f"Hook: {hooks_path.parent.name} {event} "
                                    f"[unmeasured: script hook]"
                                ),
                                tokens=0,
                                meta={
                                    "path": str(hooks_path),
                                    "event": event,
                                    "command": cmd[:200],
                                    "unmeasured": True,
                                    "unmeasured_reason": (
                                        "script/command hooks inject runtime context; "
                                        "not visible offline (Mac measured ~156 tokens)"
                                    ),
                                },
                            )
                        )
    return rows


def _row_sort_key(row: dict[str, Any] | ReportRow, *, window: str) -> tuple[int, str]:
    """Tokens desc (active window for skills), then name asc."""
    if isinstance(row, ReportRow):
        item = row.item
        t200 = row.tokens
        t1m = row.tokens_1m if row.tokens_1m is not None else row.tokens
        group = row.group
    else:
        item = str(row.get("item") or "")
        t200 = int(row.get("tokens") or 0)
        t1m = row.get("tokens_1m")
        t1m = int(t1m) if t1m is not None else t200
        group = str(row.get("group") or "")
    if group == "Skill descriptions" and window == "1m":
        tok = t1m
    else:
        tok = t200
    return (-tok, item.lower())


def sort_grouped_rows(
    rows: list[dict[str, Any]],
    subtotals: dict[str, dict[str, int]],
    *,
    window: str = "200k",
) -> tuple[list[str], list[dict[str, Any]]]:
    """Groups by subtotal desc; rows within group by tokens desc, name asc."""

    def group_subtotal(g: str) -> int:
        s = subtotals.get(g) or {}
        if window == "1m":
            return int(s.get("tokens_1m") or 0)
        return int(s.get("tokens_200k") or 0)

    groups_present = list(dict.fromkeys(r.get("group") for r in rows if r.get("group")))
    groups_order = sorted(groups_present, key=lambda g: (-group_subtotal(str(g)), str(g)))
    sorted_rows: list[dict[str, Any]] = []
    for g in groups_order:
        grow = [r for r in rows if r.get("group") == g]
        grow.sort(key=lambda r: _row_sort_key(r, window=window))
        sorted_rows.extend(grow)
    return [str(g) for g in groups_order], sorted_rows


def build_cursor_startup_report(
    roots: Roots,
    *,
    context_window: str = "200k",
) -> dict[str, Any]:
    """Build grouped startup report for Cursor host."""
    window = "1m" if str(context_window).lower() in {"1m", "1000000", "1M"} else "200k"
    skill_entries = _collect_skill_entries(roots)
    rows_200k, total_200k, mode_200k = _budget_render(skill_entries, _AGENT_LIMIT_200K)
    rows_1m, total_1m, mode_1m = _budget_render(skill_entries, _AGENT_LIMIT_1M)

    # Map path → 1M tokens for dual display
    tok_1m_by_path = {r["path"]: r["tokens"] for r in rows_1m}

    skill_rows: list[ReportRow] = []
    # Group same-name dups for display
    by_name: dict[str, list[dict[str, Any]]] = {}
    for r in rows_200k:
        by_name.setdefault(r["name"], []).append(r)

    for name, group in by_name.items():
        paths = [g["path"] for g in group]
        sources = sorted({g["source"] for g in group})
        t200 = sum(g["tokens"] for g in group)
        t1m = sum(tok_1m_by_path.get(g["path"], g["tokens"]) for g in group)
        dup = len(group) > 1 or any(g.get("same_name_copies") for g in group)
        label = name
        if dup:
            label = f"{name} ×{len(group)} (dup: {', '.join(sources)})"
        skill_rows.append(
            ReportRow(
                group="Skill descriptions",
                item=label,
                tokens=t200,
                tokens_1m=t1m,
                meta={
                    "paths": paths,
                    "sources": sources,
                    "mode_200k": mode_200k,
                    "mode_1m": mode_1m,
                    "dup": dup,
                },
            )
        )
    listed_200 = sum(r.tokens for r in skill_rows)
    listed_1m = sum((r.tokens_1m or r.tokens) for r in skill_rows)
    skill_rows.append(
        ReportRow(
            group="Skill descriptions",
            item=f"Section wrapper + budget ({mode_200k})",
            tokens=max(0, total_200k - listed_200),
            tokens_1m=max(0, total_1m - listed_1m),
            meta={"mode_200k": mode_200k, "mode_1m": mode_1m, "listed": len(rows_200k)},
        )
    )

    rule_rows = _always_apply_rules(roots)
    user_row = _read_user_rules(roots)
    mcp_rows = _mcp_rows(roots)
    hook_rows = _hooks_rows(roots)
    settings_rows = list(hook_rows)
    settings_rows.extend(
        [
            ReportRow(
                group="Model/settings",
                item="Context window 200k (default)",
                tokens=0,
                meta={"skill_budget": int(_AGENT_LIMIT_200K * 0.02)},
            ),
            ReportRow(
                group="Model/settings",
                item="Context window 1M",
                tokens=0,
                meta={"skill_budget": int(_AGENT_LIMIT_1M * 0.02)},
            ),
            ReportRow(
                group="Model/settings",
                item="MCP file-system mode on",
                tokens=0,
                meta={},
            ),
        ]
    )

    all_rows = [*rule_rows, user_row, *skill_rows, *mcp_rows, *settings_rows]
    row_dicts = [r.to_dict() for r in all_rows]

    subtotals: dict[str, dict[str, int]] = {}
    for g in {r.group for r in all_rows}:
        grow = [r for r in all_rows if r.group == g]
        subtotals[g] = {
            "tokens_200k": sum(r.tokens for r in grow),
            "tokens_1m": sum(
                r.tokens_1m if r.tokens_1m is not None else r.tokens for r in grow
            ),
        }

    groups_order, sorted_rows = sort_grouped_rows(
        row_dicts, subtotals, window=window
    )

    total_200k_all = sum(s["tokens_200k"] for s in subtotals.values())
    total_1m_all = sum(s["tokens_1m"] for s in subtotals.values())

    return {
        "host": "cursor",
        "audit": "context",
        "tokenizer": tokenizer_label(),
        "context_window": window,
        "total_tokens_200k": total_200k_all,
        "total_tokens_1m": total_1m_all,
        "subtotals": subtotals,
        "groups": groups_order,
        "rows": sorted_rows,
        "skill_budget": {
            "limit_200k": _AGENT_LIMIT_200K,
            "budget_200k": int(_AGENT_LIMIT_200K * 0.02),
            "mode_200k": mode_200k,
            "limit_1m": _AGENT_LIMIT_1M,
            "budget_1m": int(_AGENT_LIMIT_1M * 0.02),
            "mode_1m": mode_1m,
            "listed_200k": len(rows_200k),
            "listed_1m": len(rows_1m),
            "unique_realpaths": len(skill_entries),
        },
    }
