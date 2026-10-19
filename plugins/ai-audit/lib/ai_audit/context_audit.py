"""Context inventory collectors for Claude Code, Cursor, and Grok Bot."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from .common import (
    InventoryItem,
    Win,
    est_tokens,
    file_bytes,
    load_json_safe,
    mcp_server_names,
    parse_front_matter,
    rank_wins,
    safe_read_text,
    skill_description_chars,
)
from .detect import Roots


def _item(
    host: str,
    kind: str,
    scope: str,
    path: Path | str,
    nbytes: int,
    load: str,
    notes: str = "",
    **meta: Any,
) -> InventoryItem:
    return InventoryItem(
        host=host,
        kind=kind,
        scope=scope,
        path=str(path),
        bytes=nbytes,
        est_tokens=est_tokens(nbytes),
        load=load,
        notes=notes,
        meta=meta or {},
    )


def _skill_items(host: str, scope: str, skill_md: Path) -> list[InventoryItem]:
    text = safe_read_text(skill_md) or ""
    desc_chars, meta, body = skill_description_chars(text)
    name = meta.get("name") or skill_md.parent.name
    items = [
        _item(
            host,
            "skill_description",
            scope,
            skill_md,
            desc_chars,
            "always",
            notes=f"skill={name}; description always listed",
            skill=str(name),
            disable_model_invocation=bool(meta.get("disable-model-invocation")),
        ),
        _item(
            host,
            "skill_body",
            scope,
            skill_md,
            len(body.encode("utf-8")),
            "on_demand",
            notes=f"skill={name}; body loads when invoked",
            skill=str(name),
        ),
    ]
    refs = skill_md.parent / "references"
    if refs.is_dir():
        total = sum(file_bytes(p) for p in refs.rglob("*") if p.is_file())
        if total:
            items.append(
                _item(
                    host,
                    "skill_refs",
                    scope,
                    refs,
                    total,
                    "on_demand",
                    notes=f"skill={name}; reference files",
                    skill=str(name),
                )
            )
    return items


def _walk_skills(host: str, scope: str, root: Path) -> list[InventoryItem]:
    if not root.is_dir():
        return []
    items: list[InventoryItem] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        items.extend(_skill_items(host, scope, skill_md))
    return items


def _agent_items(host: str, scope: str, path: Path) -> list[InventoryItem]:
    text = safe_read_text(path) or ""
    meta, body = parse_front_matter(text)
    desc = str(meta.get("description") or "")
    name = meta.get("name") or path.stem
    return [
        _item(
            host,
            "subagent_description",
            scope,
            path,
            len(desc),
            "always",
            notes=f"subagent={name}",
            subagent=str(name),
            model=str(meta.get("model") or ""),
        ),
        _item(
            host,
            "subagent_body",
            scope,
            path,
            len(body.encode("utf-8")),
            "on_demand",
            notes=f"subagent={name}; prompt when invoked",
            subagent=str(name),
        ),
    ]


# --- Claude Code -----------------------------------------------------------


def collect_claude(roots: Roots) -> list[InventoryItem]:
    host = "claude"
    items: list[InventoryItem] = []
    home = roots.claude_home or (roots.home / ".claude")
    project = roots.project

    # Instructions / CLAUDE.md chain
    candidates = [
        (home / "CLAUDE.md", "user"),
        (project / "CLAUDE.md", "project"),
        (project / ".claude" / "CLAUDE.md", "project"),
        (project / "CLAUDE.local.md", "project_local"),
        (project / "AGENTS.md", "project"),
    ]
    for path, scope in candidates:
        if path.is_file():
            n = file_bytes(path)
            items.append(
                _item(host, "instructions", scope, path, n, "always", notes="loaded at launch")
            )

    # Rules
    for rules_dir, scope in (
        (home / "rules", "user"),
        (project / ".claude" / "rules", "project"),
    ):
        if not rules_dir.is_dir():
            continue
        for path in sorted(rules_dir.rglob("*.md")):
            text = safe_read_text(path) or ""
            meta, _ = parse_front_matter(text)
            paths_globs = meta.get("paths")
            load = "conditional" if paths_globs else "always"
            notes = f"paths={paths_globs}" if paths_globs else "no paths: always loaded"
            items.append(_item(host, "rule", scope, path, file_bytes(path), load, notes=notes))

    # Auto memory
    projects_dir = home / "projects"
    if projects_dir.is_dir():
        for mem in projects_dir.glob("*/memory/MEMORY.md"):
            n = file_bytes(mem)
            # First 200 lines / 25 KB always
            always_n = min(n, 25 * 1024)
            items.append(
                _item(
                    host,
                    "auto_memory_index",
                    "project",
                    mem,
                    always_n,
                    "always",
                    notes="first 200 lines / 25 KB always",
                    full_bytes=n,
                )
            )
            topic_dir = mem.parent
            for topic in sorted(topic_dir.glob("*.md")):
                if topic.name == "MEMORY.md":
                    continue
                items.append(
                    _item(
                        host,
                        "auto_memory_topic",
                        "project",
                        topic,
                        file_bytes(topic),
                        "on_demand",
                        notes="topic file loads on demand",
                    )
                )

    # Skills
    for skills_dir, scope in (
        (home / "skills", "user"),
        (project / ".claude" / "skills", "project"),
    ):
        items.extend(_walk_skills(host, scope, skills_dir))

    # Subagents
    for agents_dir, scope in (
        (home / "agents", "user"),
        (project / ".claude" / "agents", "project"),
    ):
        if agents_dir.is_dir():
            for path in sorted(agents_dir.glob("*.md")):
                items.extend(_agent_items(host, scope, path))

    # Output styles
    for styles_dir, scope in (
        (home / "output-styles", "user"),
        (project / ".claude" / "output-styles", "project"),
    ):
        if styles_dir.is_dir():
            for path in sorted(styles_dir.glob("*.md")):
                items.append(
                    _item(
                        host,
                        "output_style",
                        scope,
                        path,
                        file_bytes(path),
                        "always",
                        notes="active style is in the system prompt",
                    )
                )

    # MCP — count servers only; never print env/headers
    for cfg_path, scope in (
        (roots.home / ".claude.json", "user"),
        (project / ".mcp.json", "project"),
        (project / ".claude" / ".mcp.json", "project"),
    ):
        data = load_json_safe(cfg_path)
        if data is None:
            continue
        names = mcp_server_names(data)
        # Rough: ~200 bytes listing cost per server name (upper bound placeholder)
        listing = sum(len(n) + 40 for n in names)
        items.append(
            _item(
                host,
                "mcp_servers",
                scope,
                cfg_path,
                listing,
                "always",
                notes=f"{len(names)} server(s); schemas deferred by default; names only counted",
                servers=names,
                server_count=len(names),
            )
        )

    # Plugins enabled listing
    settings = load_json_safe(home / "settings.json") or {}
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    if isinstance(enabled, dict) and enabled:
        blob = ",".join(sorted(enabled.keys()))
        items.append(
            _item(
                host,
                "plugins",
                "user",
                home / "settings.json",
                len(blob),
                "always",
                notes=f"{len(enabled)} enabled plugin key(s); component load rules apply",
                plugin_count=len(enabled),
            )
        )

    return items


# --- Cursor ----------------------------------------------------------------


def _cursor_rule_load(meta: dict[str, Any]) -> tuple[str, str]:
    if meta.get("alwaysApply") is True:
        return "always", "alwaysApply: true"
    globs = meta.get("globs")
    if globs:
        return "conditional", f"globs={globs}"
    if meta.get("description"):
        return "conditional", "description-only: agent may pull when relevant"
    return "on_demand", "manual @-mention only"


def collect_cursor(roots: Roots) -> list[InventoryItem]:
    host = "cursor"
    items: list[InventoryItem] = []
    home = roots.cursor_home or (roots.home / ".cursor")
    project = roots.project

    # Project rules (.mdc only)
    for rules_dir, scope in (
        (project / ".cursor" / "rules", "project"),
        (home / "rules", "user"),
    ):
        if not rules_dir.is_dir():
            continue
        for path in sorted(rules_dir.rglob("*.mdc")):
            text = safe_read_text(path) or ""
            meta, _ = parse_front_matter(text)
            load, notes = _cursor_rule_load(meta)
            items.append(_item(host, "rule", scope, path, file_bytes(path), load, notes=notes))

    # AGENTS.md
    agents_md = project / "AGENTS.md"
    if agents_md.is_file():
        items.append(
            _item(host, "instructions", "project", agents_md, file_bytes(agents_md), "always")
        )
    claude_md = project / "CLAUDE.md"
    if claude_md.is_file():
        items.append(
            _item(
                host,
                "instructions",
                "project",
                claude_md,
                file_bytes(claude_md),
                "always",
                notes="CLI also reads CLAUDE.md",
            )
        )

    # Skills across compat dirs (duplicate detection later)
    skill_roots = [
        (home / "skills", "user_cursor"),
        (project / ".cursor" / "skills", "project_cursor"),
        (roots.home / ".agents" / "skills", "user_agents"),
        (project / ".agents" / "skills", "project_agents"),
        (roots.home / ".claude" / "skills", "user_claude_compat"),
        (project / ".claude" / "skills", "project_claude_compat"),
        (roots.home / ".codex" / "skills", "user_codex_compat"),
        (project / ".codex" / "skills", "project_codex_compat"),
    ]
    for skills_dir, scope in skill_roots:
        items.extend(_walk_skills(host, scope, skills_dir))

    # Subagents
    for agents_dir, scope in (
        (home / "agents", "user"),
        (project / ".cursor" / "agents", "project"),
        (roots.home / ".claude" / "agents", "user_claude_compat"),
        (project / ".claude" / "agents", "project_claude_compat"),
    ):
        if agents_dir.is_dir():
            for path in sorted(agents_dir.glob("*.md")):
                items.extend(_agent_items(host, scope, path))

    # MCP
    for cfg_path, scope in (
        (home / "mcp.json", "user"),
        (project / ".cursor" / "mcp.json", "project"),
    ):
        data = load_json_safe(cfg_path)
        if data is None:
            continue
        names = mcp_server_names(data) or (
            sorted(data.keys()) if isinstance(data, dict) and "mcpServers" not in data else []
        )
        # Cursor docs unclear on deferral — treat tool list as always (upper bound).
        listing = sum(len(n) + 80 for n in names)
        items.append(
            _item(
                host,
                "mcp_servers",
                scope,
                cfg_path,
                listing,
                "always",
                notes=f"{len(names)} server(s); schemas assumed always (upper bound)",
                servers=names,
                server_count=len(names),
            )
        )

    # CLI default model / maxMode (cost levers, tiny context)
    for cfg_path, scope in (
        (home / "cli-config.json", "user"),
        (project / ".cursor" / "cli.json", "project"),
    ):
        data = load_json_safe(cfg_path)
        if not isinstance(data, dict):
            continue
        model = str(data.get("model") or "")
        max_mode = data.get("maxMode")
        blob = f"model={model};maxMode={max_mode}"
        items.append(
            _item(
                host,
                "cli_config",
                scope,
                cfg_path,
                len(blob),
                "always",
                notes="default model / Max mode are cost levers",
                model=model,
                maxMode=max_mode,
            )
        )

    items.append(
        _item(
            host,
            "user_rules",
            "user",
            "(not on disk)",
            0,
            "always",
            notes="User rules live in app settings — ask the user to paste them",
            missing=True,
        )
    )
    return items


# --- Grok Bot --------------------------------------------------------------


def collect_grok(roots: Roots) -> list[InventoryItem]:
    host = "grok"
    items: list[InventoryItem] = []
    sand = roots.sand_data
    if sand is None or not sand.is_dir():
        return items

    # Workflows / skills
    workflows = sand / "workflows"
    items.extend(_walk_skills(host, "workflows", workflows))

    # Managed + plugin skill caches (descriptions)
    for cache_path, kind in (
        (sand / "managed-skills" / "cache.json", "managed_skill_cache"),
        (sand / "plugin-skills" / "cache.json", "plugin_skill_cache"),
    ):
        data = load_json_safe(cache_path)
        if not isinstance(data, dict):
            continue
        skills = data.get("skills") or []
        desc_total = 0
        for s in skills:
            if isinstance(s, dict):
                desc_total += len(str(s.get("description") or ""))
        items.append(
            _item(
                host,
                kind,
                "cache",
                cache_path,
                desc_total,
                "always",
                notes=f"{len(skills)} skill description(s) listed",
                skill_count=len(skills),
            )
        )

    # Bot profiles
    agents_dir = sand / "agents"
    active_id = None
    active = load_json_safe(agents_dir / "active-agent.json")
    if isinstance(active, dict):
        active_id = active.get("id") or active.get("agentId") or active.get("active")

    if agents_dir.is_dir():
        for profile in sorted(agents_dir.glob("*/profile.json")):
            data = load_json_safe(profile) or {}
            desc = str(data.get("description") or "") if isinstance(data, dict) else ""
            agent_id = profile.parent.name
            load = "always" if active_id and agent_id == active_id else "on_demand"
            notes = "active bot description" if load == "always" else "inactive bot"
            items.append(
                _item(
                    host,
                    "bot_description",
                    "agent",
                    profile,
                    len(desc),
                    load,
                    notes=notes,
                    agent_id=agent_id,
                    name=str((data or {}).get("name") or ""),
                )
            )

    # Per-bot memory (always for that bot)
    mem_root = sand / "user-memory" / "by-agent"
    if mem_root.is_dir():
        agent_ids = {p.name for p in agents_dir.iterdir()} if agents_dir.is_dir() else set()
        for profile_md in sorted(mem_root.glob("*/profile.md")):
            agent_id = profile_md.parent.name
            orphan = agent_id not in agent_ids
            items.append(
                _item(
                    host,
                    "memory",
                    "agent",
                    profile_md,
                    file_bytes(profile_md),
                    "always",
                    notes="orphaned agent memory" if orphan else "kept in mind every turn",
                    agent_id=agent_id,
                    orphan=orphan,
                )
            )

    # Global settings — MCP custom instructions only (sizes); skip secret files
    settings = load_json_safe(sand / "settings.json")
    if isinstance(settings, dict):
        custom = str(settings.get("mcpCustomInstructions") or "")
        by_server = settings.get("mcpCustomInstructionsByServerId") or {}
        extra = 0
        if isinstance(by_server, dict):
            extra = sum(len(str(v)) for v in by_server.values())
        n = len(custom) + extra
        if n:
            items.append(
                _item(
                    host,
                    "mcp_instructions",
                    "global",
                    sand / "settings.json",
                    n,
                    "always",
                    notes="custom MCP instructions (values not printed)",
                )
            )
        box_servers = settings.get("mcpBoxServers") or []
        if isinstance(box_servers, list) and box_servers:
            listing = sum(len(str(s)) + 40 for s in box_servers)
            items.append(
                _item(
                    host,
                    "mcp_servers",
                    "global",
                    sand / "settings.json",
                    listing,
                    "always",
                    notes=f"{len(box_servers)} box MCP server(s); tool names always",
                    server_count=len(box_servers),
                )
            )

    return items


# --- Ranker ----------------------------------------------------------------


def rank_context_wins(items: list[InventoryItem]) -> list[Win]:
    wins: list[Win] = []

    # Large always-on items
    for it in items:
        if it.load != "always":
            continue
        if it.kind in ("user_rules",) and it.bytes == 0:
            continue
        if it.est_tokens < 50:
            continue
        wins.append(
            Win(
                title=f"Trim always-on {it.kind}",
                saving_est_tokens=it.est_tokens,
                measured=f"{it.bytes} bytes (~{it.est_tokens} tokens), load=always",
                rationale=it.notes or "Always loaded into context",
                suggestion=f"Shorten or split `{it.path}` (suggestion only — do not apply).",
                path=it.path,
            )
        )

    # Duplicate skills across scopes (same skill name, multiple description entries)
    by_skill: dict[str, list[InventoryItem]] = defaultdict(list)
    for it in items:
        if it.kind == "skill_description":
            key = str(it.meta.get("skill") or Path(it.path).parent.name)
            by_skill[key].append(it)
    for name, group in by_skill.items():
        scopes = {g.scope for g in group}
        if len(group) >= 2 and len(scopes) >= 2:
            # Saving = all but one copy of the always-listed descriptions
            ordered = sorted(group, key=lambda g: -g.est_tokens)
            saving = sum(g.est_tokens for g in ordered[1:])
            if saving > 0:
                wins.append(
                    Win(
                        title=f"Deduplicate skill `{name}` across folders",
                        saving_est_tokens=saving,
                        measured=f"{len(group)} copies in scopes {sorted(scopes)}",
                        rationale="Same skill description listed from multiple compat directories",
                        suggestion="Keep one install path; remove or disable the duplicates.",
                        path=ordered[0].path,
                    )
                )

    # Long skill descriptions
    for it in items:
        if it.kind == "skill_description" and it.bytes > 400:
            wins.append(
                Win(
                    title=f"Shorten skill description ({it.meta.get('skill') or Path(it.path).parent.name})",
                    saving_est_tokens=max(0, it.est_tokens - est_tokens(200)),
                    measured=f"{it.bytes} description chars (cap guidance ~200–400)",
                    rationale="Skill descriptions are always listed",
                    suggestion="Move detail into the skill body (on demand).",
                    path=it.path,
                )
            )

    # Orphan memory
    for it in items:
        if it.kind == "memory" and it.meta.get("orphan"):
            wins.append(
                Win(
                    title="Remove orphaned bot memory file",
                    saving_est_tokens=it.est_tokens,
                    measured=f"{it.bytes} bytes always loaded for missing agent",
                    rationale="profile.md exists for an agent id with no agent directory",
                    suggestion="Delete or archive the orphaned profile.md after approval.",
                    path=it.path,
                )
            )

    # Large MCP catalogs
    for it in items:
        if it.kind == "mcp_servers" and int(it.meta.get("server_count") or 0) >= 3:
            wins.append(
                Win(
                    title="Disable unused MCP servers",
                    saving_est_tokens=it.est_tokens,
                    measured=f"{it.meta.get('server_count')} servers configured",
                    rationale="Each enabled server contributes tool names (and possibly schemas) every turn",
                    suggestion="Disable servers you rarely use; confirm with /mcp or the connector catalog.",
                    path=it.path,
                )
            )

    # Prefer unique titles by path; ranker sorts by saving
    # Deduplicate near-identical wins on same path+title
    seen: set[tuple[str, str]] = set()
    unique: list[Win] = []
    for w in sorted(wins, key=lambda x: (-x.saving_est_tokens, x.title)):
        key = (w.title, w.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(w)
    return rank_wins(unique, limit=5)


COLLECTORS = {
    "claude": collect_claude,
    "cursor": collect_cursor,
    "grok": collect_grok,
}


def run_context_audit(roots: Roots) -> dict[str, Any]:
    collector = COLLECTORS[roots.host]
    items = collector(roots)
    wins = rank_context_wins(items)
    return {
        "audit": "context",
        "host": roots.host,
        "roots": roots.to_dict(),
        "inventory": [i.to_dict() for i in items],
        "wins": [w.to_dict() for w in wins],
        "counts": {
            "items": len(items),
            "always": sum(1 for i in items if i.load == "always"),
            "on_demand": sum(1 for i in items if i.load == "on_demand"),
            "conditional": sum(1 for i in items if i.load == "conditional"),
            "always_est_tokens": sum(i.est_tokens for i in items if i.load == "always"),
            "total_est_tokens": sum(i.est_tokens for i in items),
        },
    }
