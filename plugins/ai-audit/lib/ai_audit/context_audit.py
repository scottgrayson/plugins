"""Context inventory collectors for Claude Code, Cursor, and Grok Bot."""

from __future__ import annotations

import re
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

_IMPORT_RE = re.compile(r"@([^\s\]\)\"\'<>]+)")


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


def _utf8_bytes(s: str) -> int:
    return len(s.encode("utf-8"))


def _skill_extra_files(skill_dir: Path, skill_md: Path) -> list[tuple[Path, int]]:
    """Every file in the skill folder except SKILL.md, labeled on-demand."""
    out: list[tuple[Path, int]] = []
    if not skill_dir.is_dir():
        return out
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.resolve() == skill_md.resolve():
            continue
        out.append((p, file_bytes(p)))
    return out


def _skill_items(host: str, scope: str, skill_md: Path) -> list[InventoryItem]:
    text = safe_read_text(skill_md) or ""
    desc_chars, meta, body = skill_description_chars(text)
    name = str(meta.get("name") or skill_md.parent.name)
    disable = bool(meta.get("disable-model-invocation"))
    items: list[InventoryItem] = []

    if disable:
        if host == "grok":
            # Catalog lists only the skill path (no description).
            path_listing = _utf8_bytes(str(skill_md))
            items.append(
                _item(
                    host,
                    "skill_path",
                    scope,
                    skill_md,
                    path_listing,
                    "always",
                    notes=f"skill={name}; disable-model-invocation — path only in catalog",
                    skill=name,
                    disable_model_invocation=True,
                )
            )
        else:
            # Claude Code / Cursor: manual-only; description not always listed.
            items.append(
                _item(
                    host,
                    "skill_description",
                    scope,
                    skill_md,
                    _utf8_bytes(str(meta.get("description") or "")),
                    "on_demand",
                    notes=f"skill={name}; disable-model-invocation — manual invoke only",
                    skill=name,
                    disable_model_invocation=True,
                )
            )
    else:
        items.append(
            _item(
                host,
                "skill_description",
                scope,
                skill_md,
                _utf8_bytes(str(meta.get("description") or "")),
                "always",
                notes=f"skill={name}; description always listed",
                skill=name,
                disable_model_invocation=False,
            )
        )

    items.append(
        _item(
            host,
            "skill_body",
            scope,
            skill_md,
            _utf8_bytes(body),
            "on_demand",
            notes=f"skill={name}; body loads when invoked",
            skill=name,
        )
    )

    for extra, nbytes in _skill_extra_files(skill_md.parent, skill_md):
        rel = extra.relative_to(skill_md.parent) if skill_md.parent in extra.parents or extra.parent == skill_md.parent else extra.name
        items.append(
            _item(
                host,
                "skill_asset",
                scope,
                extra,
                nbytes,
                "on_demand",
                notes=f"skill={name}; extra file {rel}",
                skill=name,
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
            _utf8_bytes(desc),
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
            _utf8_bytes(body),
            "on_demand",
            notes=f"subagent={name}; prompt when invoked",
            subagent=str(name),
        ),
    ]


def _resolve_imports(
    host: str,
    scope: str,
    path: Path,
    seen: set[Path],
) -> list[InventoryItem]:
    """Follow @path imports recursively; always-load; guard cycles."""
    items: list[InventoryItem] = []
    resolved = path.resolve()
    if resolved in seen:
        return items
    seen.add(resolved)
    if not path.is_file():
        return items
    text = safe_read_text(path) or ""
    items.append(
        _item(
            host,
            "instructions",
            scope,
            path,
            file_bytes(path),
            "always",
            notes="loaded at launch" + (" (import)" if len(seen) > 1 else ""),
            imported=len(seen) > 1,
        )
    )
    base = path.parent
    for m in _IMPORT_RE.finditer(text):
        rel = m.group(1).strip()
        if rel.startswith("http://") or rel.startswith("https://"):
            continue
        candidate = (base / rel).resolve()
        # Also try as-is relative to project-ish parents
        if not candidate.is_file():
            continue
        items.extend(_resolve_imports(host, scope, candidate, seen))
    return items


def _nested_instruction_files(project: Path) -> list[Path]:
    """Nested CLAUDE.md / AGENTS.md under the project (excluding root copies handled separately)."""
    found: list[Path] = []
    for name in ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"):
        for p in project.rglob(name):
            if p.is_file():
                found.append(p)
    return sorted(set(found))


def _mcp_items(
    host: str,
    scope: str,
    cfg_path: Path,
    defer_schemas: bool,
) -> list[InventoryItem]:
    data = load_json_safe(cfg_path)
    if data is None:
        return []
    servers = data.get("mcpServers") or data.get("mcp") or {}
    if not isinstance(servers, dict):
        # Flat map of server → config
        if isinstance(data, dict) and "mcpServers" not in data and any(
            isinstance(v, dict) for v in data.values()
        ):
            servers = data
        else:
            servers = {}
    names = sorted(servers.keys()) if isinstance(servers, dict) else []
    schema_bytes = 0
    has_schemas = False
    for name, cfg in (servers.items() if isinstance(servers, dict) else []):
        if not isinstance(cfg, dict):
            continue
        # Optional on-disk tool schema snapshots (fixtures / exports)
        for key in ("tools", "toolSchemas", "schemas"):
            tools = cfg.get(key)
            if isinstance(tools, list):
                has_schemas = True
                schema_bytes += _utf8_bytes(str(tools))
            elif isinstance(tools, dict):
                has_schemas = True
                schema_bytes += _utf8_bytes(str(tools))
        schema_file = cfg.get("schemaFile") or cfg.get("toolsFile")
        if isinstance(schema_file, str):
            sp = Path(schema_file)
            if not sp.is_absolute():
                sp = cfg_path.parent / sp
            if sp.is_file():
                has_schemas = True
                schema_bytes += file_bytes(sp)

    if has_schemas:
        return [
            _item(
                host,
                "mcp_servers",
                scope,
                cfg_path,
                schema_bytes,
                "always" if not defer_schemas else "conditional",
                notes=f"{len(names)} server(s); sized by tool schemas",
                servers=names,
                server_count=len(names),
                sized_by="schemas",
            )
        ]
    # Config-size only (names / instructions) — not a fake schema estimate
    listing = _utf8_bytes(",".join(names))
    return [
        _item(
            host,
            "mcp_servers",
            scope,
            cfg_path,
            listing,
            "always",
            notes=(
                f"{len(names)} server(s); config-size only (no tool schemas on disk)"
                + ("; schemas deferred by default" if defer_schemas else "")
            ),
            servers=names,
            server_count=len(names),
            sized_by="config",
        )
    ]


# --- Claude Code -----------------------------------------------------------


def collect_claude(roots: Roots) -> list[InventoryItem]:
    host = "claude"
    items: list[InventoryItem] = []
    home = roots.claude_home or (roots.home / ".claude")
    project = roots.project
    seen_imports: set[Path] = set()

    roots_files = [
        (home / "CLAUDE.md", "user"),
        (project / "CLAUDE.md", "project"),
        (project / ".claude" / "CLAUDE.md", "project"),
        (project / "CLAUDE.local.md", "project_local"),
        (project / "AGENTS.md", "project"),
    ]
    for path, scope in roots_files:
        if path.is_file():
            items.extend(_resolve_imports(host, scope, path, seen_imports))

    # Nested package instruction files
    for path in _nested_instruction_files(project):
        if path.resolve() in seen_imports:
            continue
        # Nested AGENTS.md / CLAUDE.md load when working in that subtree
        load = "conditional" if path.parent != project else "always"
        if path.name == "CLAUDE.local.md":
            load = "always"
        items.extend(
            _resolve_imports(host, "nested", path, seen_imports)
            if load == "always"
            else [
                _item(
                    host,
                    "instructions",
                    "nested",
                    path,
                    file_bytes(path),
                    "conditional",
                    notes="nested; loads in subtree",
                )
            ]
        )

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

    projects_dir = home / "projects"
    if projects_dir.is_dir():
        for mem in projects_dir.glob("*/memory/MEMORY.md"):
            n = file_bytes(mem)
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
            for topic in sorted(mem.parent.glob("*.md")):
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

    for skills_dir, scope in (
        (home / "skills", "user"),
        (project / ".claude" / "skills", "project"),
    ):
        items.extend(_walk_skills(host, scope, skills_dir))
    # Nested package skills (e.g. packages/foo/.claude/skills)
    for skill_md in project.rglob(".claude/skills/*/SKILL.md"):
        # Avoid double-counting project/.claude/skills
        try:
            skill_md.relative_to(project / ".claude" / "skills")
            continue
        except ValueError:
            pass
        items.extend(_skill_items(host, "nested_package", skill_md))

    for agents_dir, scope in (
        (home / "agents", "user"),
        (project / ".claude" / "agents", "project"),
    ):
        if agents_dir.is_dir():
            for path in sorted(agents_dir.glob("*.md")):
                items.extend(_agent_items(host, scope, path))

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

    for cfg_path, scope in (
        (roots.home / ".claude.json", "user"),
        (project / ".mcp.json", "project"),
        (project / ".claude" / ".mcp.json", "project"),
    ):
        items.extend(_mcp_items(host, scope, cfg_path, defer_schemas=True))

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
                _utf8_bytes(blob),
                "always",
                notes=f"{len(enabled)} enabled plugin key(s)",
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
    seen: set[Path] = set()

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

    for path in _nested_instruction_files(project):
        if path.resolve() in seen:
            continue
        load = "always" if path.parent == project else "conditional"
        if load == "always":
            items.extend(_resolve_imports(host, "project", path, seen))
        else:
            items.append(
                _item(
                    host,
                    "instructions",
                    "nested",
                    path,
                    file_bytes(path),
                    "conditional",
                    notes="nested; loads in subtree",
                )
            )
            seen.add(path.resolve())

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

    for agents_dir, scope in (
        (home / "agents", "user"),
        (project / ".cursor" / "agents", "project"),
        (roots.home / ".claude" / "agents", "user_claude_compat"),
        (project / ".claude" / "agents", "project_claude_compat"),
    ):
        if agents_dir.is_dir():
            for path in sorted(agents_dir.glob("*.md")):
                items.extend(_agent_items(host, scope, path))

    for cfg_path, scope in (
        (home / "mcp.json", "user"),
        (project / ".cursor" / "mcp.json", "project"),
    ):
        items.extend(_mcp_items(host, scope, cfg_path, defer_schemas=False))

    # CLI config is NOT context — report as config only.
    for cfg_path, scope in (
        (home / "cli-config.json", "user"),
        (project / ".cursor" / "cli.json", "project"),
    ):
        data = load_json_safe(cfg_path)
        if not isinstance(data, dict):
            continue
        model = str(data.get("model") or "")
        max_mode = data.get("maxMode")
        items.append(
            _item(
                host,
                "cli_config",
                scope,
                cfg_path,
                0,
                "unknown",
                notes="config only (not loaded into context); cost lever",
                model=model,
                maxMode=max_mode,
                context=False,
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


def _active_agent_id(sand: Path) -> Optional[str]:
    """Real settings use activeAgentId; also accept legacy keys."""
    for path in (
        sand / "settings.json",
        sand / "agents" / "active-agent.json",
    ):
        data = load_json_safe(path)
        if not isinstance(data, dict):
            continue
        for key in ("activeAgentId", "active_agent_id", "id", "agentId", "active"):
            if data.get(key):
                return str(data[key])
    return None


def collect_grok(roots: Roots) -> list[InventoryItem]:
    host = "grok"
    items: list[InventoryItem] = []
    sand = roots.sand_data
    if sand is None or not sand.is_dir():
        return items

    # Workflows are the source of truth for skill files.
    workflow_items = _walk_skills(host, "workflows", sand / "workflows")
    items.extend(workflow_items)
    workflow_skill_names = {
        str(it.meta.get("skill"))
        for it in workflow_items
        if it.kind in ("skill_description", "skill_path")
    }

    # Managed / plugin caches: only count skills NOT already covered by workflows
    # (dedupe by skill id/name). Measure bytes, not raw char length of Python str.
    for cache_path, kind in (
        (sand / "managed-skills" / "cache.json", "managed_skill_cache"),
        (sand / "plugin-skills" / "cache.json", "plugin_skill_cache"),
    ):
        data = load_json_safe(cache_path)
        if not isinstance(data, dict):
            continue
        skills = data.get("skills") or []
        desc_bytes = 0
        counted = 0
        for s in skills:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or s.get("name") or "")
            name = str(s.get("name") or sid)
            if name in workflow_skill_names or sid in workflow_skill_names:
                continue
            desc_bytes += _utf8_bytes(str(s.get("description") or ""))
            counted += 1
        if counted:
            items.append(
                _item(
                    host,
                    kind,
                    "cache",
                    cache_path,
                    desc_bytes,
                    "always",
                    notes=f"{counted} skill description(s) not already in workflows (bytes)",
                    skill_count=counted,
                )
            )

    agents_dir = sand / "agents"
    active_id = _active_agent_id(sand)

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
                    _utf8_bytes(desc),
                    load,
                    notes=notes,
                    agent_id=agent_id,
                    name=str((data or {}).get("name") or ""),
                )
            )

    # Memory: only the active bot's profile.md is always loaded.
    mem_root = sand / "user-memory" / "by-agent"
    if mem_root.is_dir():
        agent_ids = {p.name for p in agents_dir.iterdir() if p.is_dir()} if agents_dir.is_dir() else set()
        for profile_md in sorted(mem_root.glob("*/profile.md")):
            agent_id = profile_md.parent.name
            orphan = agent_id not in agent_ids
            if orphan:
                items.append(
                    _item(
                        host,
                        "memory",
                        "agent",
                        profile_md,
                        file_bytes(profile_md),
                        "unknown",
                        notes="orphaned memory file — not loaded (cleanup/clutter)",
                        agent_id=agent_id,
                        orphan=True,
                        loads=False,
                    )
                )
            elif active_id and agent_id == active_id:
                items.append(
                    _item(
                        host,
                        "memory",
                        "agent",
                        profile_md,
                        file_bytes(profile_md),
                        "always",
                        notes="active bot memory; kept in mind every turn",
                        agent_id=agent_id,
                        orphan=False,
                    )
                )
            else:
                items.append(
                    _item(
                        host,
                        "memory",
                        "agent",
                        profile_md,
                        file_bytes(profile_md),
                        "on_demand",
                        notes="inactive bot memory; not in active context",
                        agent_id=agent_id,
                        orphan=False,
                    )
                )

    settings = load_json_safe(sand / "settings.json")
    if isinstance(settings, dict):
        custom = str(settings.get("mcpCustomInstructions") or "")
        by_server = settings.get("mcpCustomInstructionsByServerId") or {}
        extra = 0
        if isinstance(by_server, dict):
            extra = sum(_utf8_bytes(str(v)) for v in by_server.values())
        n = _utf8_bytes(custom) + extra
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
            listing = _utf8_bytes(",".join(str(s) for s in box_servers))
            items.append(
                _item(
                    host,
                    "mcp_servers",
                    "global",
                    sand / "settings.json",
                    listing,
                    "always",
                    notes=f"{len(box_servers)} box MCP server(s); config-size only",
                    server_count=len(box_servers),
                    sized_by="config",
                )
            )

    return items


# --- Ranker ----------------------------------------------------------------


def rank_context_wins(items: list[InventoryItem]) -> list[Win]:
    wins: list[Win] = []

    for it in items:
        if it.load != "always":
            continue
        if it.kind in ("user_rules", "cli_config") and it.bytes == 0:
            continue
        if it.meta.get("orphan"):
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

    by_skill: dict[str, list[InventoryItem]] = defaultdict(list)
    for it in items:
        if it.kind == "skill_description" and it.load == "always":
            key = str(it.meta.get("skill") or Path(it.path).parent.name)
            by_skill[key].append(it)
    for name, group in by_skill.items():
        scopes = {g.scope for g in group}
        if len(group) >= 2 and len(scopes) >= 2:
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

    for it in items:
        if it.kind == "skill_description" and it.load == "always" and it.bytes > 400:
            wins.append(
                Win(
                    title=f"Shorten skill description ({it.meta.get('skill') or Path(it.path).parent.name})",
                    saving_est_tokens=max(0, it.est_tokens - est_tokens(200)),
                    measured=f"{it.bytes} description bytes",
                    rationale="Skill descriptions are always listed",
                    suggestion="Move detail into the skill body (on demand).",
                    path=it.path,
                )
            )

    # Orphan memory: cleanup/clutter, NOT a token saving.
    for it in items:
        if it.kind == "memory" and it.meta.get("orphan"):
            wins.append(
                Win(
                    title="Clean up orphaned bot memory file",
                    saving_est_tokens=0,
                    measured=f"{it.bytes} bytes on disk; not loaded into any bot context",
                    rationale="Orphaned profile.md is clutter — it does not always-load",
                    suggestion="Archive or delete after approval.",
                    path=it.path,
                    ask=True,
                )
            )

    for it in items:
        if it.kind == "mcp_servers" and int(it.meta.get("server_count") or 0) >= 3:
            wins.append(
                Win(
                    title="Disable unused MCP servers",
                    saving_est_tokens=it.est_tokens if it.meta.get("sized_by") == "schemas" else 0,
                    measured=f"{it.meta.get('server_count')} servers ({it.meta.get('sized_by', 'config')})",
                    rationale=it.notes,
                    suggestion="Disable servers you rarely use.",
                    path=it.path,
                    ask=it.meta.get("sized_by") != "schemas",
                )
            )

    seen: set[tuple[str, str]] = set()
    unique: list[Win] = []
    for w in sorted(wins, key=lambda x: (-(0 if x.ask else x.saving_est_tokens), x.title)):
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
