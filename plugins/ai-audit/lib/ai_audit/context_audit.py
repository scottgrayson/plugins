"""Context inventory collectors for Claude Code, Cursor, and Grok Bot."""

from __future__ import annotations

import os
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
    safe_read_text,
    skill_description_chars,
)
from .detect import Roots

_IMPORT_RE = re.compile(r"@([^\s\]\)\"\'<>]+)")

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "Library",
        "Applications",
    }
)
_MAX_NEST_DEPTH = 8


def _under_ai_audit_fixtures(path: Path) -> bool:
    """True when path is inside an .../ai-audit/fixtures/... tree."""
    parts = [part.lower() for part in Path(path).parts]
    for i, part in enumerate(parts):
        if part == "ai-audit" and i + 1 < len(parts) and parts[i + 1] == "fixtures":
            return True
    return False


def _path_is_excluded(path: Path, project: Path | None = None) -> bool:
    """Skip host plugin cache/marketplace trees and (when not the scan target) ai-audit fixtures."""
    parts = [part.lower() for part in Path(path).parts]
    for i, part in enumerate(parts):
        # ~/.claude/plugins/{cache,marketplaces,...} and ~/.cursor/plugins/...
        if part == "plugins" and i > 0 and parts[i - 1] in (".claude", ".cursor"):
            return True
    # Exclude this plugin's fixtures when auditing some other project (e.g. monorepo
    # checkout). Keep them when --root points at the fixtures themselves. When no
    # project is supplied (e.g. walking sand-data workflows), do not guess.
    if project is not None and _under_ai_audit_fixtures(path):
        if _under_ai_audit_fixtures(project):
            return False
        return True
    return False


def _project_allows_nested_scan(project: Path, home: Path) -> bool:
    """Do not recurse when the 'project' is the user home (Terminal opens on ~)."""
    try:
        return project.resolve() != home.resolve()
    except OSError:
        return False


def _iter_named_files(
    root: Path,
    name: str,
    max_depth: int = _MAX_NEST_DEPTH,
    project: Path | None = None,
) -> list[Path]:
    """Bounded walk for a basename; prunes skip dirs and plugin caches."""
    if not root.is_dir():
        return []
    found: list[Path] = []
    root_res = root.resolve()
    proj = project if project is not None else root_res
    for dirpath, dirnames, filenames in os.walk(root_res):
        cur = Path(dirpath)
        try:
            rel = cur.relative_to(root_res)
            depth = 0 if str(rel) == "." else len(rel.parts)
        except ValueError:
            depth = 0
        if _path_is_excluded(cur, proj):
            dirnames[:] = []
            continue
        kept: list[str] = []
        for d in dirnames:
            child = cur / d
            if _path_is_excluded(child, proj):
                continue
            if d in _SKIP_DIR_NAMES:
                continue
            if d.startswith(".") and d not in (".claude", ".cursor"):
                continue
            if depth + 1 > max_depth:
                continue
            kept.append(d)
        dirnames[:] = kept
        if depth > max_depth:
            continue
        if name in filenames:
            path = cur / name
            if path.is_file() and not _path_is_excluded(path, proj):
                found.append(path)
    return sorted(set(found))


def _iter_nested_skill_mds(
    root: Path,
    max_depth: int = _MAX_NEST_DEPTH,
    project: Path | None = None,
) -> list[Path]:
    """Find package-level .claude/skills/*/SKILL.md under root (bounded)."""
    if not root.is_dir():
        return []
    found: list[Path] = []
    root_res = root.resolve()
    proj = project if project is not None else root_res
    project_skills = (root_res / ".claude" / "skills").resolve()
    for dirpath, dirnames, _filenames in os.walk(root_res):
        cur = Path(dirpath)
        try:
            rel = cur.relative_to(root_res)
            depth = 0 if str(rel) == "." else len(rel.parts)
        except ValueError:
            depth = 0
        if _path_is_excluded(cur, proj):
            dirnames[:] = []
            continue
        kept: list[str] = []
        for d in dirnames:
            child = cur / d
            if _path_is_excluded(child, proj):
                continue
            if d in _SKIP_DIR_NAMES:
                continue
            if d.startswith(".") and d not in (".claude", ".cursor"):
                continue
            if depth + 1 > max_depth:
                continue
            kept.append(d)
        dirnames[:] = kept
        if cur.name != "skills" or cur.parent.name != ".claude":
            continue
        try:
            if cur.resolve() == project_skills:
                continue  # project/.claude/skills handled separately
        except OSError:
            pass
        if not cur.is_dir():
            continue
        for child in sorted(cur.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if skill_md.is_file() and not _path_is_excluded(skill_md, proj):
                found.append(skill_md)
    return sorted(set(found))


def _dedupe_items(items: list[InventoryItem]) -> list[InventoryItem]:
    """One inventory row per resolved path (first wins)."""
    seen: set[str] = set()
    out: list[InventoryItem] = []
    for it in items:
        try:
            key = str(Path(it.path).resolve())
        except OSError:
            key = f"{it.kind}:{it.path}:{it.notes}"
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out




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


def _skill_items(
    host: str,
    scope: str,
    skill_md: Path,
    *,
    force_on_demand: bool = False,
) -> list[InventoryItem]:
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
                    "on_demand" if force_on_demand else "always",
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
    elif force_on_demand:
        # Nested package .claude/skills below the project root: Claude Code docs say
        # these do not load at startup (on-demand only).
        items.append(
            _item(
                host,
                "skill_description",
                scope,
                skill_md,
                _utf8_bytes(str(meta.get("description") or "")),
                "on_demand",
                notes=f"skill={name}; nested .claude/skills — on demand (not always-loaded)",
                skill=name,
                disable_model_invocation=False,
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


def _walk_skills(
    host: str, scope: str, root: Path, project: Path | None = None
) -> list[InventoryItem]:
    if not root.is_dir():
        return []
    if _path_is_excluded(root, project):
        return []
    items: list[InventoryItem] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        if _path_is_excluded(skill_md, project):
            continue
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


def _nested_instruction_files(
    project: Path, home: Optional[Path] = None
) -> list[Path]:
    """Nested CLAUDE.md / AGENTS.md under the project (excluding root copies handled separately)."""
    if home is not None and not _project_allows_nested_scan(project, home):
        return []
    found: list[Path] = []
    for name in ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md"):
        for p in _iter_named_files(project, name, project=project):
            if p.parent.resolve() == project.resolve():
                continue  # root copies handled separately
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


def _enabled_plugin_ids(claude_home: Path, project: Path) -> list[str]:
    """
    Enabled plugin ids after Claude Code precedence: user < project < local.
    A later settings file's true/false overrides earlier ones for the same key.
    """
    merged: dict[str, bool] = {}
    order: list[str] = []
    for settings_path in (
        claude_home / "settings.json",
        project / ".claude" / "settings.json",
        project / ".claude" / "settings.local.json",
    ):
        data = load_json_safe(settings_path)
        if not isinstance(data, dict):
            continue
        enabled = data.get("enabledPlugins")
        if not isinstance(enabled, dict):
            continue
        for key, val in enabled.items():
            if not isinstance(val, bool):
                continue
            k = str(key)
            if k not in merged:
                order.append(k)
            merged[k] = val
    return [k for k in order if merged.get(k) is True]


def _read_installed_plugins(claude_home: Path) -> dict[str, Any]:
    """Load ~/.claude/plugins/installed_plugins.json (v2 plugins map or legacy flat)."""
    path = claude_home / "plugins" / "installed_plugins.json"
    data = load_json_safe(path)
    if not isinstance(data, dict):
        return {}
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        return plugins
    # Legacy: top-level keys are plugin ids
    return {k: v for k, v in data.items() if k not in ("version",)}


def _install_paths_for_plugin(entries: Any, project: Path) -> list[Path]:
    """Resolve unique installPath dirs for one plugin id (user/managed, then matching project)."""
    if isinstance(entries, dict):
        entry_list = [entries]
    elif isinstance(entries, list):
        entry_list = [e for e in entries if isinstance(e, dict)]
    else:
        return []

    chosen: list[dict[str, Any]] = []
    for e in entry_list:
        scope = str(e.get("scope") or "")
        if scope in ("user", "managed"):
            chosen.append(e)
    if not chosen:
        proj_s = str(project.resolve()) if project else ""
        for e in entry_list:
            if str(e.get("scope") or "") != "project":
                continue
            pp = e.get("projectPath")
            if pp is None or str(Path(str(pp)).expanduser().resolve()) == proj_s:
                chosen.append(e)
    if not chosen:
        chosen = list(entry_list)

    paths: list[Path] = []
    seen: set[str] = set()
    for e in chosen:
        raw = e.get("installPath")
        if not raw:
            continue
        p = Path(str(raw)).expanduser()
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            paths.append(p)
    return paths


def _skill_mds_in_plugin_root(install_path: Path) -> list[Path]:
    """List SKILL.md files for one plugin install — no full cache walk."""
    found: list[Path] = []
    skills_dir = install_path / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if skill_md.is_file():
                found.append(skill_md)
    root_skill = install_path / "SKILL.md"
    if root_skill.is_file():
        found.append(root_skill)
    return found


def _enabled_plugin_skill_items(
    host: str,
    claude_home: Path,
    project: Path,
    enabled_ids: list[str],
) -> list[InventoryItem]:
    """
    Skills of enabled plugins via installed_plugins.json install paths only.
    Skips fixtures and duplicate resolved paths; does not walk the whole plugin cache.
    """
    registry = _read_installed_plugins(claude_home)
    items: list[InventoryItem] = []
    seen_skill: set[str] = set()
    for plugin_id in enabled_ids:
        entries = registry.get(plugin_id)
        if entries is None:
            continue
        for install_path in _install_paths_for_plugin(entries, project):
            if _under_ai_audit_fixtures(install_path):
                continue
            for skill_md in _skill_mds_in_plugin_root(install_path):
                if _under_ai_audit_fixtures(skill_md):
                    continue
                try:
                    key = str(skill_md.resolve())
                except OSError:
                    key = str(skill_md)
                if key in seen_skill:
                    continue
                seen_skill.add(key)
                for it in _skill_items(host, "plugin", skill_md):
                    it.notes = (
                        f"{it.notes}; plugin={plugin_id}"
                        if it.notes
                        else f"plugin={plugin_id}"
                    )
                    it.meta = {**(it.meta or {}), "plugin": plugin_id}
                    items.append(it)
    return items


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

    # Nested package instruction files (bounded; never when project == ~)
    for path in _nested_instruction_files(project, home=roots.home):
        if path.resolve() in seen_imports:
            continue
        # Nested AGENTS.md / CLAUDE.md / CLAUDE.local.md load in that subtree only
        load = "conditional" if path.parent.resolve() != project.resolve() else "always"
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
        items.extend(_walk_skills(host, scope, skills_dir, project=project))
    # Nested package skills (e.g. packages/foo/.claude/skills) — on-demand only
    if _project_allows_nested_scan(project, roots.home):
        for skill_md in _iter_nested_skill_mds(project, project=project):
            items.extend(
                _skill_items(host, "nested_package", skill_md, force_on_demand=True)
            )

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

    enabled_ids = _enabled_plugin_ids(home, project)
    if enabled_ids:
        blob = ",".join(sorted(enabled_ids))
        items.append(
            _item(
                host,
                "plugins",
                "user",
                home / "settings.json",
                _utf8_bytes(blob),
                "always",
                notes=f"{len(enabled_ids)} enabled plugin key(s)",
                plugin_count=len(enabled_ids),
            )
        )
        items.extend(_enabled_plugin_skill_items(host, home, project, enabled_ids))

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

    for path in _nested_instruction_files(project, home=roots.home):
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
        items.extend(_walk_skills(host, scope, skills_dir, project=project))

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
    workflow_items = _walk_skills(
        host, "workflows", sand / "workflows", project=roots.project
    )
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
        per_skill: list[dict[str, Any]] = []
        for s in skills:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or s.get("name") or "")
            name = str(s.get("name") or sid)
            if name in workflow_skill_names or sid in workflow_skill_names:
                continue
            sb = _utf8_bytes(str(s.get("description") or ""))
            desc_bytes += sb
            counted += 1
            plugin = str(s.get("pluginName") or s.get("plugin") or s.get("connector") or "")
            per_skill.append(
                {
                    "name": name,
                    "bytes": sb,
                    "est_tokens": est_tokens(sb),
                    "plugin": plugin,
                }
            )
        per_skill.sort(key=lambda x: -int(x.get("bytes") or 0))
        if counted:
            items.append(
                _item(
                    host,
                    kind,
                    "cache",
                    cache_path,
                    desc_bytes,
                    "always",
                    notes=(
                        f"{counted} skill description(s) not already in workflows (bytes); "
                        "app-managed — not user-editable"
                    ),
                    skill_count=counted,
                    not_user_editable=True,
                    top_skills=per_skill[:5],
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
    """v0.1.0: savings estimates are out of scope — inventory only."""
    return []



COLLECTORS = {
    "claude": collect_claude,
    "cursor": collect_cursor,
    "grok": collect_grok,
}


def run_context_audit(roots: Roots) -> dict[str, Any]:
    collector = COLLECTORS[roots.host]
    items = _dedupe_items(collector(roots))
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
