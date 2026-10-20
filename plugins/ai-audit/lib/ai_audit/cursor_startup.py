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

# Fixed estimate stand-in (~638 o200k); real Cursor preamble measured ~637 on Mac.
# Labelled as estimate (round-19 D7 honesty) — not claimed as measured verbatim.
_MCP_PREAMBLE = (Path(__file__).with_name("mcp_preamble.txt").read_text(encoding="utf-8"))
_MCP_PREAMBLE_NOTE = (
    "fixed estimate from vendored mcp_preamble.txt (o200k stand-in; "
    "Mac-measured real preamble ≈637 tokens)"
)

_USER_RULES_KEY = "aicontext.personalContext"
_PLUGIN_IDS_PREFIX = "cursor.plugins.installedIds."


@dataclass
class ReportRow:
    group: str
    item: str
    tokens: int
    tokens_1m: Optional[int] = None  # skills: 200k vs 1M
    description: str = ""  # from file (first line / frontmatter), ~60 chars, redacted
    source: str = ""  # short path e.g. ~/.claude/skills via symlink
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _short_source(path: Path | str, home: Path) -> str:
    """Short display path relative to home when possible."""
    try:
        p = Path(path)
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        home_r = home.resolve() if home.exists() else home
        try:
            rel = resolved.relative_to(home_r)
            return "~/" + str(rel).replace("\\", "/")
        except ValueError:
            pass
        s = str(p)
        home_s = str(home)
        if s.startswith(home_s):
            return "~" + s[len(home_s) :]
        return s
    except Exception:
        return str(path)


def _desc_snippet(text: str, *, limit: int = 60) -> str:
    """First useful line / description, truncated, redacted."""
    raw = (text or "").strip()
    if not raw:
        return ""
    # Prefer non-heading prose line
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("---") or s.startswith("#"):
            continue
        if s.startswith("name:") or s.startswith("description:"):
            # yaml value after key
            parts = s.split(":", 1)
            if len(parts) == 2 and parts[1].strip():
                s = parts[1].strip().strip("\"'")
            else:
                continue
        out = s if len(s) <= limit else s[: limit - 1] + "…"
        return redact_secrets(out)
    out = raw.splitlines()[0].strip()
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return redact_secrets(out)


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
    """
    List SKILL.md under root, following directory symlinks (round-19 #3).
    Deduping by realpath happens in _collect_skill_entries.
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    seen_dirs: set[str] = set()

    def walk(dir_path: Path, depth: int = 0) -> None:
        if depth > 6:
            return
        try:
            key = str(dir_path.resolve())
        except OSError:
            key = str(dir_path)
        if key in seen_dirs:
            return
        seen_dirs.add(key)
        try:
            children = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            # skills/<name>/SKILL.md (name may be a symlink to a dir)
            skill_md = child / "SKILL.md"
            try:
                if skill_md.is_file() or (child.is_symlink() and skill_md.exists()):
                    out.append(skill_md)
                    continue
            except OSError:
                pass
            if child.is_dir() and not child.is_symlink():
                # Nested skills folders (rare); also plugin skill trees
                if child.name == "skills" or depth == 0:
                    walk(child, depth + 1)
            elif child.is_symlink():
                try:
                    if child.resolve().is_dir():
                        walk(child, depth + 1)
                except OSError:
                    pass

    # If root itself is a skills dir, scan children; else look for skills/
    if (root / "SKILL.md").is_file():
        out.append(root / "SKILL.md")
    walk(root, 0)
    # Also classic rglob for non-symlink nested layouts (plugins/.../skills/*/SKILL.md)
    try:
        for skill_md in sorted(root.rglob("SKILL.md")):
            try:
                rel = skill_md.relative_to(root)
            except ValueError:
                rel = skill_md
            if any(p.startswith(".") for p in rel.parts[:-1]):
                continue
            if skill_md not in out:
                out.append(skill_md)
    except OSError:
        pass
    return out


def _load_plugin_manifest(plugin_root: Path) -> dict[str, Any]:
    for cand in (
        plugin_root / ".cursor-plugin" / "plugin.json",
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / "plugin.json",
    ):
        data = load_json_safe(cand)
        if isinstance(data, dict):
            return data
    return {}


def _plugin_rel_dir(plugin_root: Path, key: str, default: str) -> Optional[Path]:
    """Resolve a plugin.json path field (e.g. rules: \"cursor/rules\")."""
    manifest = _load_plugin_manifest(plugin_root)
    rel = manifest.get(key) if key in manifest else default
    if rel is None:
        return None
    if not isinstance(rel, str) or not rel.strip():
        rel = default
    path = plugin_root / rel
    return path if path.is_dir() else None


def _expand_nested_plugins(plugin_root: Path) -> list[Path]:
    """Include marketplace nested plugins via metadata.pluginRoot."""
    out = [plugin_root]
    manifest = _load_plugin_manifest(plugin_root)
    meta = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    plugin_root_rel = meta.get("pluginRoot") or manifest.get("pluginRoot")
    if isinstance(plugin_root_rel, str) and plugin_root_rel.strip():
        nested_root = plugin_root / plugin_root_rel
        if nested_root.is_dir():
            for sub in sorted(nested_root.iterdir(), key=lambda p: p.name):
                if sub.is_dir() or (sub.is_symlink() and sub.resolve().is_dir()):
                    out.append(sub)
    return out


def _read_enabled_plugin_map(roots: Roots) -> dict[str, bool]:
    """
    Read cursor.plugins.installedIds.* from state.vscdb (exact key prefix only).
    Value JSON may include {\"enabled\": bool}. Missing keys → treat as enabled
    when present on disk (legacy); explicit enabled:false excludes.
    """
    db_path = _find_state_vscdb(roots)
    if db_path is None:
        return {}
    enabled: dict[str, bool] = {}
    try:
        conn = _open_vscdb_readonly(db_path)
        try:
            cur = conn.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE ?",
                (_PLUGIN_IDS_PREFIX + "%",),
            )
            for key, value in cur.fetchall():
                suffix = str(key)[len(_PLUGIN_IDS_PREFIX) :]
                # suffix examples: marketplace/plugin or publisher.plugin
                on = True
                if value is not None:
                    raw = (
                        value.decode("utf-8", errors="replace")
                        if isinstance(value, bytes)
                        else str(value)
                    )
                    raw_s = raw.strip()
                    if raw_s.lower() in {"0", "false", "disabled", "off", "no"}:
                        on = False
                    elif raw_s.lower() in {"1", "true", "enabled", "on", "yes"}:
                        on = True
                    else:
                        try:
                            data = json.loads(raw_s)
                            if isinstance(data, dict) and "enabled" in data:
                                on = bool(data.get("enabled"))
                            elif data is False:
                                on = False
                        except (json.JSONDecodeError, TypeError):
                            on = True
                enabled[suffix] = on
                # Also index by bare plugin name
                bare = suffix.split("/")[-1].split(".")[-1]
                enabled.setdefault(bare, on)
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return enabled


def _plugin_is_enabled(plugin_root: Path, enabled_map: dict[str, bool]) -> bool:
    if not enabled_map:
        return True  # no DB info → include (fixture without vscdb keys)
    name = plugin_root.name
    # version dir → parent is plugin id
    parent = plugin_root.parent.name if plugin_root.parent else name
    for key in (name, parent, f"{plugin_root.parent.parent.name}/{parent}"):
        if key in enabled_map:
            return enabled_map[key]
    return True


def _active_plugin_roots(
    cursor_home: Path, *, enabled_map: Optional[dict[str, bool]] = None
) -> list[Path]:
    """
    Plugin roots: local (follow symlinks) + cache versions + nested pluginRoot.
    Exclude plugins explicitly disabled in state.vscdb.
    """
    emap = enabled_map or {}
    roots: list[Path] = []
    local = cursor_home / "plugins" / "local"
    if local.is_dir():
        for p in sorted(local.iterdir(), key=lambda x: x.name):
            if not (p.is_dir() or p.is_symlink()):
                continue
            try:
                target = p.resolve() if p.is_symlink() else p
            except OSError:
                target = p
            if not target.is_dir():
                continue
            if not _plugin_is_enabled(p, emap) and not _plugin_is_enabled(target, emap):
                continue
            roots.extend(_expand_nested_plugins(target))
    cache = cursor_home / "plugins" / "cache"
    if cache.is_dir():
        for market in sorted(cache.iterdir(), key=lambda x: x.name):
            if not market.is_dir():
                continue
            for plugin in sorted(market.iterdir(), key=lambda x: x.name):
                if not plugin.is_dir():
                    continue
                versions = [v for v in plugin.iterdir() if v.is_dir()]
                candidates = versions or [plugin]
                # All version dirs (layout facts: usually one); do not collapse
                # distinct plugins that share skill names.
                for ver in candidates:
                    if not _plugin_is_enabled(ver, emap) and not _plugin_is_enabled(
                        plugin, emap
                    ):
                        continue
                    roots.extend(_expand_nested_plugins(ver))
    # Dedupe by realpath
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _plugin_skill_roots(
    cursor_home: Path, *, enabled_map: Optional[dict[str, bool]] = None
) -> list[tuple[Path, str]]:
    """Return (skills_dir, source_label) for enabled Cursor plugins."""
    found: list[tuple[Path, str]] = []
    for plugin_root in _active_plugin_roots(cursor_home, enabled_map=enabled_map):
        skills = _plugin_rel_dir(plugin_root, "skills", "skills")
        if skills is not None:
            found.append((skills, f"plugin:{plugin_root.name}"))
    return found


def _claude_plugin_skill_roots(home: Path) -> list[tuple[Path, str]]:
    """~/.claude/plugins/cache skills (and nested), honouring enabledPlugins."""
    found: list[tuple[Path, str]] = []
    settings = load_json_safe(home / ".claude" / "settings.json") or {}
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    enabled_set: Optional[set[str]] = None
    if isinstance(enabled, dict):
        enabled_set = {k for k, v in enabled.items() if v}
    elif isinstance(enabled, list):
        enabled_set = set(str(x) for x in enabled)
    cache = home / ".claude" / "plugins" / "cache"
    if not cache.is_dir():
        return found
    for market in sorted(cache.iterdir(), key=lambda x: x.name):
        if not market.is_dir():
            continue
        for plugin in sorted(market.iterdir(), key=lambda x: x.name):
            if not plugin.is_dir():
                continue
            plugin_id = f"{market.name}/{plugin.name}"
            if enabled_set is not None and plugin_id not in enabled_set and plugin.name not in enabled_set:
                # Also allow installed_plugins.json presence
                pass  # still scan unless explicitly disabled false
            if isinstance(enabled, dict) and enabled.get(plugin_id) is False:
                continue
            versions = [v for v in plugin.iterdir() if v.is_dir()]
            for ver in versions or [plugin]:
                for root in _expand_nested_plugins(ver):
                    skills = root / "skills"
                    if skills.is_dir():
                        found.append((skills, f"claude-plugin:{root.name}"))
    return found


def _collect_skill_entries(
    roots: Roots,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Enumerate skills with realpath dedupe (symlinks collapse).

    Returns (realpath-deduped entries, stats) where stats include
    realpath_count (main) and name_count (lower-bound bracket; Cursor's
    real name-dedupe behaviour is unconfirmed — round-19 layout facts).
    """
    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    project = roots.project
    enabled_map = _read_enabled_plugin_map(roots)
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
    roots_list.extend(_plugin_skill_roots(cursor_home, enabled_map=enabled_map))
    roots_list.extend(_claude_plugin_skill_roots(home))

    by_realpath: dict[str, dict[str, Any]] = {}
    name_paths: dict[str, list[str]] = {}
    link_note: dict[str, str] = {}

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
            src_label = source
            try:
                if skill_md.parent.is_symlink():
                    src_label = f"{source} via symlink"
                    link_note[str(skill_md)] = str(skill_md.parent.resolve())
            except OSError:
                pass
            entry = {
                "name": name,
                "path": str(skill_md),
                "realpath": real,
                "source": src_label,
                "description": desc,
                "display_source": _short_source(skill_md, home),
            }
            if real in by_realpath:
                by_realpath[real].setdefault("aliases", []).append(str(skill_md))
                continue
            by_realpath[real] = entry
            name_paths.setdefault(name, []).append(str(skill_md))

    entries = list(by_realpath.values())
    for e in entries:
        others = [p for p in name_paths.get(e["name"], []) if p != e["path"]]
        e["same_name_copies"] = others
        e["dup"] = bool(others) or bool(e.get("aliases"))
    stats = {
        "realpath_count": len(entries),
        "name_count": len(name_paths),
        "name_dedupe_lower_bound": len(name_paths),
    }
    return entries, stats


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
    enabled_map = _read_enabled_plugin_map(roots)

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
        desc = _desc_snippet(str(meta.get("description") or "") or body)
        rows.append(
            ReportRow(
                group="Always-apply rules",
                item=path.name,
                tokens=count_tokens(xml),
                description=desc,
                source=_short_source(path, home),
                meta={"path": str(path), "source": source, "alwaysApply": True},
            )
        )

    # User vs project: when cwd is ~, cursor_home/rules is user not project.
    proj_res = project.resolve() if project.exists() else project
    home_res = home.resolve() if home.exists() else home
    project_is_home = proj_res == home_res

    for rules_dir, source in (
        (project / ".cursor" / "rules", "project"),
        (cursor_home / "rules", "user" if project_is_home else "user"),
    ):
        if rules_dir.is_dir():
            for path in sorted(rules_dir.rglob("*.mdc")):
                # follow rule-dir symlinks via rglob on real dirs; resolve files
                add_rule(path, source)

    for plugin_root in _active_plugin_roots(cursor_home, enabled_map=enabled_map):
        rules_dir = _plugin_rel_dir(plugin_root, "rules", "rules")
        if rules_dir is None:
            continue
        for path in sorted(rules_dir.rglob("*.mdc")):
            add_rule(path, f"plugin:{plugin_root.name}")

    # Claude plugin cache rules
    claude_cache = home / ".claude" / "plugins" / "cache"
    if claude_cache.is_dir():
        for market in claude_cache.iterdir():
            if not market.is_dir():
                continue
            for plugin in market.iterdir():
                if not plugin.is_dir():
                    continue
                for ver in [v for v in plugin.iterdir() if v.is_dir()] or [plugin]:
                    for root in _expand_nested_plugins(ver):
                        rules_dir = root / "rules"
                        if rules_dir.is_dir():
                            for path in sorted(rules_dir.glob("*.mdc")):
                                add_rule(path, f"claude-plugin:{root.name}")

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
    Open state.vscdb read-only without copying (round-18/19).

    - If sibling ``-wal`` or ``-shm`` exists: ``mode=ro&immutable=0`` so recent
      WAL writes are visible without taking a writer lock.
    - If neither exists: ``mode=ro&immutable=1`` so SQLite does **not** create
      empty ``-wal``/``-shm`` next to the DB (round-19 #2).
    Never SELECT keys other than allowlisted prefixes; no temp copies.
    """
    wal = Path(str(db_path) + "-wal")
    shm = Path(str(db_path) + "-shm")
    has_wal = wal.is_file() or shm.is_file()
    immutable = "0" if has_wal else "1"
    uri = (
        "file:"
        + quote(str(db_path.resolve()), safe="/:")
        + f"?mode=ro&immutable={immutable}"
    )
    return sqlite3.connect(uri, uri=True)


def _read_user_rules(roots: Roots) -> ReportRow:
    """
    Read ONLY aicontext.personalContext from state.vscdb (read-only URI).
    No temp copies. Exact-key query. Catch all sqlite errors → unreadable.
    """
    home = roots.home
    db_path = _find_state_vscdb(roots)
    if db_path is None:
        return ReportRow(
            group="User Rules",
            item="In-app User Rules",
            tokens=0,
            description="",
            source="",
            meta={"missing": True, "key": _USER_RULES_KEY},
        )

    try:
        conn = _open_vscdb_readonly(db_path)
        try:
            cur = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                (_USER_RULES_KEY,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        reason = str(exc).strip() or exc.__class__.__name__
        return ReportRow(
            group="User Rules",
            item=f"In-app User Rules [unreadable ({reason})]",
            tokens=0,
            description="",
            source=_short_source(db_path, home),
            meta={
                "key": _USER_RULES_KEY,
                "unreadable": True,
                "reason": reason,
                "db": str(db_path),
            },
        )

    value = ""
    if row and row[0] is not None:
        raw = row[0]
        if isinstance(raw, bytes):
            value = raw.decode("utf-8", errors="replace")
        else:
            value = str(raw)
    tokens = count_tokens(value) if value else 0
    wal = Path(str(db_path) + "-wal")
    shm = Path(str(db_path) + "-shm")
    open_mode = (
        "sqlite mode=ro&immutable=0 (wal present)"
        if (wal.is_file() or shm.is_file())
        else "sqlite mode=ro&immutable=1 (no wal/shm)"
    )
    return ReportRow(
        group="User Rules",
        item="In-app User Rules",
        tokens=tokens,
        description=_desc_snippet(value),
        source=_short_source(db_path, home),
        meta={
            "key": _USER_RULES_KEY,
            "bytes": len(value.encode("utf-8")),
            "empty": not bool(value.strip()),
            "db": str(db_path),
            "open": open_mode,
        },
    )


def _cursor_project_slug(project: Path) -> str:
    """Cursor encodes absolute paths as slugs (/, \\, : → -)."""
    try:
        raw = str(project.resolve())
    except OSError:
        raw = str(project)
    return re.sub(r"[/\\:]+", "-", raw).strip("-")


def _mcp_sync_date(mcps_dir: Path) -> str:
    try:
        ts = mcps_dir.stat().st_mtime
        import datetime as _dt

        return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except OSError:
        return "unknown"


def _mcp_project_dir(roots: Roots) -> tuple[Optional[Path], str]:
    """
    Pick MCP descriptor slug deterministically (round-19 #3).

    - Project workspace → exact Cursor slug for cwd when it has mcps/.
    - Desktop / home / no folder → ``empty-window``.
    - Else newest synced mcps/ by mtime (never a stale ~ slug alone).
    Returns (mcps_dir_or_None, selection_note including sync date).
    """
    cursor_home = roots.cursor_home or (roots.home / ".cursor")
    projects = cursor_home / "projects"
    home = roots.home
    project = roots.project
    try:
        proj_res = project.resolve()
        home_res = home.resolve() if home.exists() else home
    except OSError:
        proj_res, home_res = project, home

    def note_for(slug: str, mcps: Path) -> str:
        return f"slug={slug}; sync={_mcp_sync_date(mcps)}"

    if not projects.is_dir():
        return None, "no ~/.cursor/projects; server lines from mcp.json only"

    # Desktop / auditing from ~ → empty-window
    if proj_res == home_res or project.name in {"", ".", "~"}:
        empty = projects / "empty-window" / "mcps"
        if empty.is_dir():
            return empty, note_for("empty-window", empty)

    slug = _cursor_project_slug(project)
    exact = projects / slug / "mcps"
    if exact.is_dir():
        return exact, note_for(slug, exact)

    basename = project.name
    matches = [
        p / "mcps"
        for p in projects.iterdir()
        if p.is_dir() and (p / "mcps").is_dir() and p.name == basename
    ]
    if len(matches) == 1:
        return matches[0], note_for(matches[0].parent.name, matches[0])

    # Prefer empty-window over stale home-only slugs
    empty = projects / "empty-window" / "mcps"
    if empty.is_dir():
        return empty, note_for("empty-window", empty) + " (fallback)"

    # Newest synced mcps set
    candidates = [
        p / "mcps"
        for p in projects.iterdir()
        if p.is_dir() and (p / "mcps").is_dir()
    ]
    if candidates:
        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return newest, note_for(newest.parent.name, newest) + " (newest sync)"

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
    home = roots.home
    rows: list[ReportRow] = [
        ReportRow(
            group="MCP servers",
            item="MCP file-system preamble",
            tokens=count_tokens(_MCP_PREAMBLE),
            description=_desc_snippet(_MCP_PREAMBLE_NOTE),
            source="vendored mcp_preamble.txt",
            meta={
                "fixed_estimate": True,
                "estimate_note": _MCP_PREAMBLE_NOTE,
                "measured_o200k": False,
            },
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
        # Pathlib treats "postgres://…" as absolute — never join unsafe names.
        safe_seg = bool(name) and not any(c in name for c in (":", "/", "\\", "@"))
        tools_dir = (
            (mcps_root / name / "tools") if (mcps_root and safe_seg) else None
        )
        tool_files = (
            sorted(tools_dir.glob("*.json")) if tools_dir and tools_dir.is_dir() else []
        )
        tool_count = len(tool_files)
        total_tools += tool_count
        schema_text = ""
        for tf in tool_files:
            schema_text += safe_read_text(tf) or ""
        schema_tokens = count_tokens(schema_text) if schema_text else 0
        if tools_dir and tools_dir.is_dir():
            folder_raw = str(tools_dir.parent)
        else:
            folder_raw = f"(no descriptors)/{name}"
        # D8: redact credentials that may appear in folderPath (URL userinfo, etc.)
        folder = redact_secrets(folder_raw)
        line = _mcp_server_line(redact_secrets(name), folder)
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
                description=_desc_snippet(f"{tool_count} tools; {selection_note}"),
                source=(
                    _short_source(folder, home)
                    if (tools_dir and tools_dir.is_dir())
                    else selection_note
                ),
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


def _hooks_from_file(
    hooks_path: Path, home: Path, *, label: str
) -> list[ReportRow]:
    rows: list[ReportRow] = []
    data = load_json_safe(hooks_path)
    if not isinstance(data, dict):
        return rows
    hooks = data.get("hooks") or data
    if not isinstance(hooks, dict):
        return rows
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
                        item=f"Hook: {label} {event}",
                        tokens=count_tokens(ctx),
                        description=_desc_snippet(ctx),
                        source=_short_source(hooks_path, home),
                        meta={"path": str(hooks_path), "event": event},
                    )
                )
                continue
            cmd = str(ent.get("command") or ent.get("script") or "")
            # Claude-format: script that prints sibling rule.txt / inject file
            sibling_candidates = [
                hooks_path.parent / "rule.txt",
                hooks_path.parent / "inject-rule.txt",
                hooks_path.parent / "hooks" / "rule.txt",
            ]
            # When hooks.json lives in hooks/, rule.txt is sibling
            measured: Optional[Path] = None
            for cand in sibling_candidates:
                if cand.is_file():
                    measured = cand
                    break
            if measured is None and ("inject-rule" in cmd or "rule.txt" in cmd):
                # ${CLAUDE_PLUGIN_ROOT}/hooks/inject-rule.sh → hooks/rule.txt
                plugin_hooks = hooks_path.parent
                if (plugin_hooks / "rule.txt").is_file():
                    measured = plugin_hooks / "rule.txt"
                elif hooks_path.parent.name == "hooks" and (
                    hooks_path.parent / "rule.txt"
                ).is_file():
                    measured = hooks_path.parent / "rule.txt"
            if measured is not None:
                text = safe_read_text(measured) or ""
                note = ""
                if event in {"UserPromptSubmit", "SessionStart", "sessionStart"}:
                    note = " (repeats per prompt)" if event == "UserPromptSubmit" else ""
                rows.append(
                    ReportRow(
                        group="Model/settings",
                        item=f"Hook: {label} {event}{note}",
                        tokens=count_tokens(text),
                        description=_desc_snippet(text),
                        source=_short_source(measured, home),
                        meta={
                            "path": str(hooks_path),
                            "event": event,
                            "measured_file": str(measured),
                            "command": cmd[:200],
                        },
                    )
                )
            else:
                rows.append(
                    ReportRow(
                        group="Model/settings",
                        item=f"Hook: {label} {event} [unmeasured: script hook]",
                        tokens=0,
                        description="",
                        source=_short_source(hooks_path, home),
                        meta={
                            "path": str(hooks_path),
                            "event": event,
                            "command": cmd[:200],
                            "unmeasured": True,
                            "unmeasured_reason": (
                                "script/command hook with no sibling rule.txt to measure"
                            ),
                        },
                    )
                )
    return rows


def _hooks_rows(roots: Roots) -> list[ReportRow]:
    """
    Parse plugin + Claude-format hooks.

    Static additionalContext counted. Claude SessionStart/UserPromptSubmit
    scripts that emit sibling rule.txt are measured from that file (note:
    UserPromptSubmit repeats per prompt). Other scripts → unmeasured.
    """
    rows: list[ReportRow] = []
    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    enabled_map = _read_enabled_plugin_map(roots)

    for plugin_root in _active_plugin_roots(cursor_home, enabled_map=enabled_map):
        hooks_paths = list(plugin_root.glob("hooks.json"))
        hooks_paths += list(plugin_root.glob("hooks/hooks.json"))
        for hooks_path in hooks_paths:
            rows.extend(
                _hooks_from_file(hooks_path, home, label=plugin_root.name)
            )

    # Claude plugin cache hooks (SessionStart / UserPromptSubmit + rule.txt)
    claude_cache = home / ".claude" / "plugins" / "cache"
    if claude_cache.is_dir():
        for hooks_path in claude_cache.rglob("hooks.json"):
            rows.extend(
                _hooks_from_file(
                    hooks_path, home, label=hooks_path.parent.parent.name
                )
            )

    # User-level ~/.cursor/hooks.json (may be symlink)
    user_hooks = cursor_home / "hooks.json"
    if user_hooks.exists():
        rows.extend(_hooks_from_file(user_hooks, home, label="user-hooks"))

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
    skill_entries, skill_stats = _collect_skill_entries(roots)
    rows_200k, total_200k, mode_200k = _budget_render(skill_entries, _AGENT_LIMIT_200K)
    rows_1m, total_1m, mode_1m = _budget_render(skill_entries, _AGENT_LIMIT_1M)

    # Map path → 1M tokens for dual display
    tok_1m_by_path = {r["path"]: r["tokens"] for r in rows_1m}
    home = roots.home

    skill_rows: list[ReportRow] = []
    # Main number = realpath-deduped entries (already collapsed in skill_entries).
    # Group by name only for ×N display when multiple realpaths share a name.
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
        desc = _desc_snippet(group[0].get("description") or "")
        src = group[0].get("display_source") or _short_source(paths[0], home)
        skill_rows.append(
            ReportRow(
                group="Skill descriptions",
                item=label,
                tokens=t200,
                tokens_1m=t1m,
                description=desc,
                source=src,
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
            item=(
                f"Section wrapper + budget ({mode_200k}); "
                f"realpath={skill_stats['realpath_count']} "
                f"(name-dedupe lower bound {skill_stats['name_dedupe_lower_bound']})"
            ),
            tokens=max(0, total_200k - listed_200),
            tokens_1m=max(0, total_1m - listed_1m),
            description="skills budget cascade",
            source="",
            meta={
                "mode_200k": mode_200k,
                "mode_1m": mode_1m,
                "listed": len(rows_200k),
                **skill_stats,
            },
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
            "unique_realpaths": skill_stats["realpath_count"],
            "unique_names_lower_bound": skill_stats["name_dedupe_lower_bound"],
        },
    }
