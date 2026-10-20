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
    enabled: str = "yes"  # yes | no | unknown (plugin rows only; reporting, not filtering)

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


_MARKETPLACE_FILES = (
    ".cursor-plugin/marketplace.json",
    ".claude-plugin/marketplace.json",
    "marketplace.json",
)
_PLUGIN_MARKER_NAMES = (
    "skills", "rules", "commands", "agents", "hooks", "hooks.json", "mcp.json", ".mcp.json",
)
_HEX_SHA_RE = re.compile(r"(?i)^[0-9a-f]{7,40}$")
_VERSION_DIR_RE = re.compile(r"(?i)^v?\d+(?:\.\d+){0,3}(?:[-+][\w.]+)?$|^[0-9a-f]{7,40}$")


def _load_marketplace(root: Path) -> dict[str, Any]:
    for rel in _MARKETPLACE_FILES:
        data = load_json_safe(root / rel)
        if isinstance(data, dict):
            return data
    return {}


def _is_plugin_dir(path: Path) -> bool:
    """A plugin (or marketplace) root: has a manifest, marketplace.json, or content dirs."""
    if _load_plugin_manifest(path) or _load_marketplace(path):
        return True
    return any((path / n).exists() for n in _PLUGIN_MARKER_NAMES)


def _plugin_name(root: Path) -> str:
    """Display name: manifest name, else dir name (version/sha dirs use the parent)."""
    name = _load_plugin_manifest(root).get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    if _VERSION_DIR_RE.match(root.name) and root.parent.name:
        return root.parent.name
    return root.name


def _plugin_root_rel(root: Path) -> str:
    """marketplace pluginRoot (e.g. "plugins") from plugin.json or marketplace.json."""
    for manifest in (_load_plugin_manifest(root), _load_marketplace(root)):
        meta = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
        rel = meta.get("pluginRoot") or manifest.get("pluginRoot")
        if isinstance(rel, str) and rel.strip():
            return rel.strip()
    return ""


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
    """Include marketplace sub-plugins under pluginRoot (e.g. <sha>/plugins/<name>/)."""
    out = [plugin_root]
    rel = _plugin_root_rel(plugin_root)
    if rel:
        nested_root = plugin_root / rel
        if nested_root.is_dir():
            for sub in sorted(nested_root.iterdir(), key=lambda p: p.name):
                if sub.name.startswith("."):
                    continue
                if sub.is_dir() or (sub.is_symlink() and sub.resolve().is_dir()):
                    out.append(sub)
    return out


def _version_key(path: Path) -> tuple[Any, ...]:
    nums = tuple(int(n) for n in re.findall(r"\d+", path.name)[:4])
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (nums, mtime)


def _cache_units(
    plugin_dir: Path, *, latest_only: bool = False, preferred: frozenset[str] = frozenset()
) -> list[Path]:
    """
    One cache entry -> plugin root(s). A flat plugin (or a marketplace checkout dir)
    is its own root; otherwise its subdirs are version dirs.
    """
    if _is_plugin_dir(plugin_dir):
        return [plugin_dir]
    vers = [
        v for v in sorted(plugin_dir.iterdir(), key=lambda p: p.name)
        if v.is_dir() and not v.name.startswith(".")
    ]
    if latest_only and len(vers) > 1:
        chosen = [v for v in vers if str(v.resolve()) in preferred]
        vers = chosen or [max(vers, key=_version_key)]
    return vers or [plugin_dir]


def _discover_cache_plugins(
    cache: Path, *, latest_only: bool = False, preferred: frozenset[str] = frozenset()
) -> list[Path]:
    """
    Plugin roots under a plugin cache. Handles:
      <cache>/<market>/<plugin>/                         (flat)
      <cache>/<market>/<plugin>/<version>/               (versioned)
      <cache>/<market>-ai-skills/<sha>/plugins/<name>/   (marketplace pluginRoot)
    """
    if not cache.is_dir():
        return []
    roots: list[Path] = []
    for market in sorted(cache.iterdir(), key=lambda x: x.name):
        if not market.is_dir() or market.name.startswith("."):
            continue
        children = [
            c for c in sorted(market.iterdir(), key=lambda x: x.name)
            if c.is_dir() and not c.name.startswith(".")
        ]
        # Several marketplace checkouts (shas): only the newest is live.
        shas = [c for c in children if _HEX_SHA_RE.match(c.name) and _load_marketplace(c)]
        if len(shas) > 1:
            newest = max(shas, key=lambda p: p.stat().st_mtime)
            children = [c for c in children if c not in shas or c == newest]
        for child in children:
            for unit in _cache_units(child, latest_only=latest_only, preferred=preferred):
                roots.extend(_expand_nested_plugins(unit))
    return _dedupe_paths(roots)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for r in paths:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _read_enabled_plugin_state(roots: Roots) -> tuple[dict[str, bool], list[str]]:
    """
    Read cursor.plugins.installedIds.* from state.vscdb (exact key prefix only).
    Returns (enabled map, keys read). The value format is UNVERIFIED: the map only
    feeds the report's Enabled column and never excludes a plugin.
    """
    db_path = _find_state_vscdb(roots)
    if db_path is None:
        return {}, []
    enabled: dict[str, bool] = {}
    keys: list[str] = []
    try:
        conn = _open_vscdb_readonly(db_path)
        try:
            cur = conn.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE ?",
                (_PLUGIN_IDS_PREFIX + "%",),
            )
            for key, value in cur.fetchall():
                keys.append(str(key))
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
        return {}, []
    return enabled, keys


def _read_enabled_plugin_map(roots: Roots) -> dict[str, bool]:
    return _read_enabled_plugin_state(roots)[0]


def state_vscdb_keys_read(roots: Roots) -> list[str]:
    """Every state.vscdb key this audit reads (exact key or prefix match results)."""
    if _find_state_vscdb(roots) is None:
        return []
    return [_USER_RULES_KEY, *sorted(_read_enabled_plugin_state(roots)[1])]


def _plugin_state(root: Path, cursor_home: Path, emap: dict[str, bool]) -> str:
    """yes / no / unknown from state.vscdb. Reporting only; never used to exclude."""
    if not emap:
        return "unknown"
    name = _plugin_name(root)
    market = ""
    try:
        market = root.relative_to(cursor_home / "plugins" / "cache").parts[0]
    except ValueError:
        pass
    keys = [f"{market}/{name}"] if market else []
    keys += [name, root.name]
    if root.parent.name:
        keys += [root.parent.name, f"{root.parent.parent.name}/{root.parent.name}"]
    for key in keys:
        if key in emap:
            return "yes" if emap[key] else "no"
    return "unknown"


def _active_plugin_roots(
    cursor_home: Path, *, enabled_map: Optional[dict[str, bool]] = None
) -> list[Path]:
    """
    Plugin roots: local (follow symlinks) + cache (flat, versioned, and
    marketplace pluginRoot sub-plugins). Nothing is excluded by state.vscdb
    (its format is unverified); ``enabled_map`` is accepted for compatibility.
    """
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
            roots.extend(_expand_nested_plugins(target))
    roots.extend(_discover_cache_plugins(cursor_home / "plugins" / "cache"))
    return _dedupe_paths(roots)


def _plugin_skill_roots_state(
    cursor_home: Path, *, emap: Optional[dict[str, bool]] = None
) -> list[tuple[Path, str, str]]:
    """(skills_dir, source_label, enabled) for Cursor plugins."""
    found: list[tuple[Path, str, str]] = []
    for plugin_root in _active_plugin_roots(cursor_home):
        skills = _plugin_rel_dir(plugin_root, "skills", "skills")
        if skills is not None:
            found.append(
                (
                    skills,
                    f"plugin:{_plugin_name(plugin_root)}",
                    _plugin_state(plugin_root, cursor_home, emap or {}),
                )
            )
    return found


def _plugin_skill_roots(
    cursor_home: Path, *, enabled_map: Optional[dict[str, bool]] = None
) -> list[tuple[Path, str]]:
    """Return (skills_dir, source_label) for Cursor plugins."""
    return [(d, label) for d, label, _s in _plugin_skill_roots_state(cursor_home, emap=enabled_map)]


def _claude_plugin_roots(home: Path) -> list[tuple[Path, str]]:
    """
    ~/.claude/plugins/cache/<market>/<plugin>/<version>/ roots (and pluginRoot
    sub-plugins) as (root, enabled). Newest version per plugin unless
    installed_plugins.json names an installPath. enabledPlugins:false (verified
    Claude Code settings format) excludes; true -> yes; absent -> unknown.
    """
    cache = home / ".claude" / "plugins" / "cache"
    if not cache.is_dir():
        return []
    settings = load_json_safe(home / ".claude" / "settings.json") or {}
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    emap: dict[str, bool] = {}
    if isinstance(enabled, dict):
        emap = {str(k): bool(v) for k, v in enabled.items()}
    elif isinstance(enabled, list):
        emap = {str(x): True for x in enabled}
    preferred: set[str] = set()
    inst = load_json_safe(home / ".claude" / "plugins" / "installed_plugins.json")
    plugins = inst.get("plugins") if isinstance(inst, dict) else None
    if isinstance(plugins, dict):
        for entries in plugins.values():
            for ent in entries if isinstance(entries, list) else []:
                ip = ent.get("installPath") if isinstance(ent, dict) else None
                if isinstance(ip, str):
                    try:
                        preferred.add(str(Path(ip).resolve()))
                    except OSError:
                        pass
    out: list[tuple[Path, str]] = []
    for root in _discover_cache_plugins(
        cache, latest_only=True, preferred=frozenset(preferred)
    ):
        try:
            parts = root.relative_to(cache).parts
        except ValueError:
            parts = ()
        market = parts[0] if parts else ""
        plugin = parts[1] if len(parts) > 1 else root.name
        name = _plugin_name(root)
        ids = {
            f"{plugin}@{market}", f"{market}/{plugin}", plugin,
            f"{name}@{market}", f"{market}/{name}", name,
        }
        states = [emap[i] for i in ids if i in emap]
        if states and not any(states):
            continue
        out.append((root, "yes" if states else "unknown"))
    return out


def _claude_plugin_skill_roots(home: Path) -> list[tuple[Path, str]]:
    """~/.claude/plugins/cache skills (and nested), honouring enabledPlugins."""
    return [
        (root / "skills", f"claude-plugin:{_plugin_name(root)}")
        for root, _state in _claude_plugin_roots(home)
        if (root / "skills").is_dir()
    ]


def _skill_roots(roots: Roots) -> list[tuple[Path, str, str]]:
    """
    Every skills dir as (dir, source label, enabled). Shared by the context table
    and the quality Checks so both see the same discovered set.
    """
    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    project = roots.project
    enabled_map = _read_enabled_plugin_map(roots)
    roots_list: list[tuple[Path, str, str]] = [
        (d, label, "yes")
        for d, label in (
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
        )
    ]
    roots_list.extend(_plugin_skill_roots_state(cursor_home, emap=enabled_map))
    claude_state = {
        (root / "skills"): st for root, st in _claude_plugin_roots(home)
    }
    roots_list.extend(
        (d, label, claude_state.get(d, "unknown"))
        for d, label in _claude_plugin_skill_roots(home)
    )
    return roots_list



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
    roots_list = _skill_roots(roots)

    by_realpath: dict[str, dict[str, Any]] = {}
    name_paths: dict[str, list[str]] = {}
    link_note: dict[str, str] = {}

    for root, source, enabled in roots_list:
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
                "enabled": enabled,
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

    def add_rule(path: Path, source: str, enabled: str = "yes") -> None:
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
                enabled=enabled,
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

    for plugin_root in _active_plugin_roots(cursor_home):
        rules_dir = _plugin_rel_dir(plugin_root, "rules", "rules")
        if rules_dir is None:
            continue
        state = _plugin_state(plugin_root, cursor_home, enabled_map)
        for path in sorted(rules_dir.rglob("*.mdc")):
            add_rule(path, f"plugin:{_plugin_name(plugin_root)}", state)

    # Claude plugin cache rules
    for root, state in _claude_plugin_roots(home):
        rules_dir = root / "rules"
        if rules_dir.is_dir():
            for path in sorted(rules_dir.glob("*.mdc")):
                add_rule(path, f"claude-plugin:{_plugin_name(root)}", state)

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


def _mcp_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _mcp_configured(roots: Roots) -> list[dict[str, Any]]:
    """Servers named in mcp.json files (user, project) and active plugins' mcp.json."""
    cursor_home = roots.cursor_home or (roots.home / ".cursor")
    emap = _read_enabled_plugin_map(roots)
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: Any, origin: str, plugin: str = "", state: str = "yes") -> None:
        key = (origin + ":" + plugin, str(name))
        if key not in seen:
            seen.add(key)
            entries.append(
                {"name": str(name), "origin": origin, "plugin": plugin, "enabled": state}
            )

    seen_cfg: set[str] = set()
    for cfg, origin in (
        (cursor_home / "mcp.json", "user"),
        (roots.project / ".cursor" / "mcp.json", "project"),
    ):
        # Project folder == ~ (no-folder window): one file, user scope only.
        try:
            real = str(cfg.resolve())
        except OSError:
            real = str(cfg)
        if real in seen_cfg:
            continue
        seen_cfg.add(real)
        data = load_json_safe(cfg)
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers") or data.get("servers") or {}
        if isinstance(servers, dict):
            for name in servers:
                add(name, origin)
    # Active plugin mcp.json only (skip stale cached versions — D7)
    for plugin_root in _active_plugin_roots(cursor_home):
        pname = _plugin_name(plugin_root)
        state = _plugin_state(plugin_root, cursor_home, emap)
        paths = [plugin_root / "mcp.json"]
        if not paths[0].is_file() and not _plugin_root_rel(plugin_root):
            # (a pluginRoot container's sub-plugins are separate roots)
            paths = [p for p in plugin_root.glob("**/mcp.json") if p.is_file()]
        for path in paths:
            data = load_json_safe(path) if path.is_file() else None
            if not isinstance(data, dict):
                continue
            servers = data.get("mcpServers") or data.get("servers") or {}
            if isinstance(servers, dict):
                for name in servers:
                    add(name, "plugin", pname, state)
    return entries


def _mcp_folder_candidates(entry: dict[str, Any], folders: list[str]) -> list[str]:
    """Descriptor folder names that can belong to a configured server, best first."""
    name = entry["name"]
    sv = list(dict.fromkeys([name, _mcp_slug(name)]))
    cands: list[str] = []
    if entry["origin"] == "plugin":
        pl = list(dict.fromkeys([entry["plugin"], _mcp_slug(entry["plugin"])]))
        cands += [f"plugin-{p}-{v}" for p in pl for v in sv]
        cands += [f"plugin-{v}" for v in sv]
        cands += [name] if name.startswith("plugin-") else []
    else:
        cands += [name]
        cands += [f"user-{v}" for v in sv]
        cands += [f"project-{v}" for v in sv]
        slug = _mcp_slug(name)
        cands += [f for f in folders if f.startswith("project-") and f.endswith(f"-{slug}")]
    return [c for c in dict.fromkeys(cands) if c in folders]


def _mcp_server_key(folder: str) -> str:
    return re.sub(r"^(?:user|plugin|project)-", "", folder)


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
    folders: list[str] = []
    if mcps_root and mcps_root.is_dir():
        folders = sorted(p.name for p in mcps_root.iterdir() if p.is_dir())

    # Match configured servers (mcp.json / plugin mcp.json) to synced descriptor
    # folders (user-<name>, plugin-<plugin>-<server>) before calling any of them
    # missing; a folder is claimed by at most one configured server.
    claimed: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for entry in _mcp_configured(roots):
        free = [c for c in _mcp_folder_candidates(entry, folders) if c not in claimed]
        if free:
            claimed[free[0]] = entry
        else:
            unmatched.append(entry)
    # (display name, descriptor folder or None, server key, enabled)
    servers: list[tuple[str, Optional[str], str, str]] = []
    for entry in unmatched:
        servers.append((entry["name"], None, entry["name"], entry["enabled"]))
    for folder in folders:
        ent = claimed.get(folder)
        key = ent["name"] if ent else _mcp_server_key(folder)
        servers.append((folder, folder, key, ent["enabled"] if ent else "yes"))

    total_tools = 0
    for name, folder_name, _key, enabled in servers:
        # Pathlib treats "postgres://…" as absolute — never join unsafe names.
        safe_seg = bool(folder_name) and not any(
            c in folder_name for c in (":", "/", "\\", "@")
        )
        tools_dir = (
            (mcps_root / folder_name / "tools") if (mcps_root and safe_seg) else None
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
                enabled=enabled,
            )
        )

    # Duplicate servers: only when two distinct descriptor folders map to one server.
    by_key: dict[str, list[str]] = {}
    for _name, folder_name, key, _en in servers:
        if folder_name:
            by_key.setdefault(key, []).append(folder_name)
    for key, group in by_key.items():
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


_PER_PROMPT_GROUP = "Per prompt"
_PER_PROMPT_EVENTS = frozenset({"userpromptsubmit", "beforesubmitprompt"})


def _hooks_from_file(
    hooks_path: Path, home: Path, *, label: str, enabled: str = "yes"
) -> list[ReportRow]:
    """
    Rows for one hooks.json. UserPromptSubmit-style events land in the
    "Per prompt" group (they fire every prompt, not at startup).
    """
    rows: list[ReportRow] = []
    data = load_json_safe(hooks_path)
    if not isinstance(data, dict):
        return rows
    hooks = data.get("hooks") or data
    if not isinstance(hooks, dict):
        return rows

    def make(event: str, suffix: str, **kw: Any) -> ReportRow:
        per_prompt = str(event).lower() in _PER_PROMPT_EVENTS
        note = " (repeats per prompt)" if per_prompt else ""
        return ReportRow(
            group=_PER_PROMPT_GROUP if per_prompt else "Model/settings",
            item=f"Hook: {label} {event}{note}{suffix}",
            enabled=enabled,
            **kw,
        )

    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for ent in entries:
            if not isinstance(ent, dict):
                continue
            ctx = ent.get("additionalContext") or ent.get("additional_context")
            if isinstance(ctx, str) and ctx.strip():
                rows.append(
                    make(
                        event,
                        "",
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
            measured: Optional[Path] = None
            for cand in sibling_candidates:
                if cand.is_file():
                    measured = cand
                    break
            if measured is not None:
                text = safe_read_text(measured) or ""
                rows.append(
                    make(
                        event,
                        "",
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
                    make(
                        event,
                        " [unmeasured: script hook]",
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
    scripts that emit sibling rule.txt are measured from that file.
    UserPromptSubmit rows go to the "Per prompt" group (not startup). Rows are
    labelled by plugin name and each hooks.json is read once (realpath dedupe).
    """
    rows: list[ReportRow] = []
    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    enabled_map = _read_enabled_plugin_map(roots)
    seen_files: set[str] = set()

    def add_file(hooks_path: Path, label: str, state: str) -> None:
        try:
            key = str(hooks_path.resolve())
        except OSError:
            key = str(hooks_path)
        if key in seen_files:
            return
        seen_files.add(key)
        rows.extend(_hooks_from_file(hooks_path, home, label=label, enabled=state))

    for plugin_root in _active_plugin_roots(cursor_home):
        state = _plugin_state(plugin_root, cursor_home, enabled_map)
        for hooks_path in (plugin_root / "hooks.json", plugin_root / "hooks" / "hooks.json"):
            if hooks_path.is_file():
                add_file(hooks_path, _plugin_name(plugin_root), state)

    # Claude plugin cache hooks (SessionStart / UserPromptSubmit + rule.txt)
    for root, state in _claude_plugin_roots(home):
        for hooks_path in (root / "hooks.json", root / "hooks" / "hooks.json"):
            if hooks_path.is_file():
                add_file(hooks_path, _plugin_name(root), state)

    # User-level ~/.cursor/hooks.json (may be symlink)
    user_hooks = cursor_home / "hooks.json"
    if user_hooks.exists():
        add_file(user_hooks, "user-hooks", "yes")

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
    # "Per prompt" is not startup context: always listed after the startup groups.
    groups_order = sorted(
        groups_present,
        key=lambda g: (str(g) == _PER_PROMPT_GROUP, -group_subtotal(str(g)), str(g)),
    )
    sorted_rows: list[dict[str, Any]] = []
    for g in groups_order:
        grow = [r for r in rows if r.get("group") == g]
        grow.sort(key=lambda r: _row_sort_key(r, window=window))
        sorted_rows.extend(grow)
    return [str(g) for g in groups_order], sorted_rows


def _merge_enabled(states: Any) -> str:
    vals = set(states)
    return next(iter(vals)) if len(vals) == 1 else "unknown"


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
    # Skills the 200k budget omitted still load on a 1M model: list them with
    # 0 tokens at 200k so the 1M column is fully itemised (not hidden in the wrapper).
    listed_paths = {r["path"] for r in rows_200k}
    omitted_200k = [r for r in rows_1m if r["path"] not in listed_paths]
    for r in omitted_200k:
        by_name.setdefault(r["name"], []).append({**r, "tokens": 0, "omitted_200k": True})

    for name, group in by_name.items():
        paths = [g["path"] for g in group]
        sources = sorted({g["source"] for g in group})
        t200 = sum(g["tokens"] for g in group)
        t1m = sum(tok_1m_by_path.get(g["path"], g["tokens"]) for g in group)
        dup = len(group) > 1 or any(g.get("same_name_copies") for g in group)
        label = name
        if dup:
            label = f"{name} ×{len(group)} (dup: {', '.join(sources)})"
        if all(g.get("omitted_200k") for g in group):
            label += " [omitted at 200k]"
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
                enabled=_merge_enabled(g.get("enabled", "yes") for g in group),
            )
        )
    listed_200 = sum(r.tokens for r in skill_rows)
    wrapper_note = (
        "Fixed text around the skill list: the <agent_skills> open/close tags"
        + (
            " plus the 'Additional skills omitted' notice Cursor adds when the 200k budget drops skills"
            if mode_200k == "path_only_omitted"
            else ""
        )
        + f". Remainder of the {mode_200k} budget total after the rows above."
    )
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
            description=wrapper_note,
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

    # UserPromptSubmit-style hooks fire per prompt: reported, never in the startup total.
    startup = {g: s for g, s in subtotals.items() if g != _PER_PROMPT_GROUP}
    total_200k_all = sum(s["tokens_200k"] for s in startup.values())
    total_1m_all = sum(s["tokens_1m"] for s in startup.values())
    per_prompt = subtotals.get(_PER_PROMPT_GROUP) or {}

    from .quality_checks import run_quality_checks

    checks = run_quality_checks(roots)

    return {
        "host": "cursor",
        "audit": "context",
        "tokenizer": tokenizer_label(),
        "context_window": window,
        "total_tokens_200k": total_200k_all,
        "total_tokens_1m": total_1m_all,
        "per_prompt_tokens": int(per_prompt.get("tokens_200k") or 0),
        "state_vscdb_keys": state_vscdb_keys_read(roots),
        "subtotals": subtotals,
        "groups": groups_order,
        "rows": sorted_rows,
        "checks": checks,
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
