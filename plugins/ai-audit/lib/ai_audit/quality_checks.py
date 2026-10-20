"""
Deterministic, token-free quality checks for startup context files.

No AI. Every finding is a fact with file:line. Pure functions — easy to unit-test.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .common import parse_front_matter, safe_read_text
from .detect import Roots

# Severity sort key (lower = first).
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# The public plugin ships no stale names. Set AI_AUDIT_STALE_NAMES (comma-separated)
# or `stale_names = ["a", "b"]` in ~/.config/ai-audit/config.toml.
DEFAULT_STALE_NAMES: tuple[str, ...] = ()
STALE_NAMES_ENV = "AI_AUDIT_STALE_NAMES"

# Cursor .mdc front-matter keys we recognise.
_MDC_KNOWN_KEYS = frozenset(
    {
        "description",
        "alwaysApply",
        "globs",
        "paths",
        "name",
        "disable-model-invocation",
    }
)

_TRIGGER_RE = re.compile(
    r"(?i)\b(?:when|whenever|use when|use for|apply when|apply to|apply before|"
    r"apply after|apply during|use this skill to|use this skill when|use to)\b"
)

_FILE_EXTS = frozenset(
    "md mdc txt rst json jsonc yaml yml toml ini cfg conf env py js mjs cjs ts tsx jsx "
    "sh bash zsh rb go rs java kt swift c h cpp hpp cs php sql html htm css scss xml "
    "csv tsv log pdf png jpg jpeg gif svg lock gradle tf ipynb".split()
)
_DOMAIN_TLDS = "com|org|net|io|dev|ai|app|co|edu|gov|xyz|cloud|tech|info|biz"

# Spans that are never file references: URLs, e-mail, bare domains, and any
# whitespace-delimited token holding a template placeholder (<name>, ${X}, {var},
# {{x}}, $VAR, %VAR%).
_MASK_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]{1,20}://[^\s<>\"'`]*|\bmailto:\S+")
_MASK_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MASK_DOMAIN_RE = re.compile(
    r"(?i)(?<![\w./~@-])(?:[a-z0-9][a-z0-9-]*\.)+(?:%s)(?![\w-])"
    r"(?:[/:][^\s`'\"<>)\]]*)?" % _DOMAIN_TLDS
)
_PLACEHOLDER = (
    r"(?:<[^<>\n]{0,80}>|\$\{[^}\n]*\}|\{\{[^}\n]*\}\}|\{[^{}\n]*\}|"
    r"\$[A-Za-z_]\w*|%[A-Za-z_]+%)"
)
_PLAIN = r"[^\s`'\"()<>{}$%]"
_MASK_PLACEHOLDER_RE = re.compile(rf"(?:{_PLAIN}*{_PLACEHOLDER})+{_PLAIN}*")
# Example paths: path/to/x, your-file.md, example.md, .../x, foo/bar.
_PLACEHOLDER_PATH_RE = re.compile(
    r"(?i)(?:^|/)(?:path/to|your[-_]|my[-_]|example|sample|foo|bar|baz|xxx+|"
    r"file(?:name)?\.\w+|name\.\w+|\.\.\.)"
)

# @file / @path style includes. The @ must start a token (not e-mail / @scope/pkg).
_AT_FILE_RE = re.compile(
    r"(?i)(?<![\w@./:+-])@(?:(?:file|path|include)\s+)?[`'\"]?"
    r"((?:\./|\.\./|~/|/)?[\w.\-/]+\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/@-])"
)
_PATH_REF_RE = re.compile(
    r"(?i)(?<![\w/.@-])((?:\./|\.\./|~/|/)[^\s`'\"<>()\[\]{}|,;*]+"
    r"\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/-])"
)
# Skill-relative paths with no ./ prefix, e.g. references/x.md or scripts/y.sh.
_SKILL_SUBDIRS = ("references", "reference", "scripts", "assets", "templates", "resources")
_SKILL_REL_RE = re.compile(
    r"(?i)(?<![\w/.@~-])((?:%s)/[^\s`'\"<>()\[\]{}|,;*]+"
    r"\.[A-Za-z][A-Za-z0-9]{0,7})(?![\w/-])" % "|".join(_SKILL_SUBDIRS)
)
# Markdown link targets: [text](target) or [text](target "title").
_MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)\n]*)?\)")
_FENCE_RE = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,}).*$")

# skill/rule name references: quoted/backticked, or kebab-case (avoids
# prose like "skill descriptions").
_NAME_REF_RE = re.compile(
    r"(?i)\b(?:skill|rule)\s+"
    r"(?:[`'\"]([A-Za-z][A-Za-z0-9_-]{1,64})[`'\"]|"
    r"([A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+(?:-[A-Za-z0-9_-]+)+))"
)


@dataclass
class CheckFinding:
    item: str
    check: str
    finding: str
    source: str
    severity: str = "medium"
    line: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["editable"] = is_editable_source(self.source)
        return d


def _short(path: Path, home: Path, project: Path | None = None) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        home_r = home.resolve() if home.exists() else home
        rel = resolved.relative_to(home_r)
        return "~/" + str(rel).replace("\\", "/")
    except Exception:
        pass
    if project is not None:
        try:
            proj_r = project.resolve() if project.exists() else project
            rel = resolved.relative_to(proj_r)
            return str(rel).replace("\\", "/")
        except Exception:
            pass
    return str(path).replace("\\", "/")


def _mask_non_refs(text: str) -> str:
    """Blank (same length, newlines kept) spans that can never be file references."""

    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    for rx in (_MASK_URL_RE, _MASK_EMAIL_RE, _MASK_DOMAIN_RE, _MASK_PLACEHOLDER_RE):
        text = rx.sub(blank, text)
    return text


def _looks_like_file_ref(ref: str) -> bool:
    if "://" in ref or "*" in ref or _PLACEHOLDER_PATH_RE.search(ref):
        return False
    ext = ref.rsplit(".", 1)[-1].lower()
    return ext in _FILE_EXTS


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """Offsets of fenced code blocks and inline code spans."""
    out: list[tuple[int, int]] = []
    open_at: Optional[int] = None
    fence = ""
    for m in _FENCE_RE.finditer(text):
        mark = m.group(1)
        if open_at is None:
            open_at, fence = m.start(), mark
        elif mark[0] == fence[0] and len(mark) >= len(fence):
            out.append((open_at, m.end()))
            open_at = None
    if open_at is not None:
        out.append((open_at, len(text)))
    for m in re.finditer(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", text, re.DOTALL):
        out.append((m.start(), m.end()))
    return out


def _in_ranges(ranges: Sequence[tuple[int, int]], offset: int) -> bool:
    return any(a <= offset < b for a, b in ranges)


_UNEDITABLE_FRAGMENTS = (
    "/plugins/cache/",
    "/plugins/marketplaces/",
    "/plugins/installed/",
    "/skills-cursor/",
    "/.cursor/extensions/",
    "/.vscode/extensions/",
)


def is_editable_source(source: str) -> str:
    """'no' for files under plugin caches and other tool-managed locations."""
    s = "/" + str(source).replace("\\", "/").lstrip("/")
    return "no" if any(f in s for f in _UNEDITABLE_FRAGMENTS) else "yes"


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _iter_skill_files(roots: Roots) -> list[Path]:
    """Same skill set the startup context table lists (user, project, all plugin shapes)."""
    # Lazy import avoids circular import with cursor_startup → quality_checks.
    from .cursor_startup import _iter_skill_mds, _skill_roots

    out: list[Path] = []
    seen: set[str] = set()
    for skills_dir, _label, _state in _skill_roots(roots):
        for skill_md in _iter_skill_mds(skills_dir):
            try:
                key = str(skill_md.resolve())
            except OSError:
                key = str(skill_md)
            if key in seen:
                continue
            seen.add(key)
            out.append(skill_md)
    return out


def _iter_rule_files(roots: Roots) -> list[Path]:
    """User/project rules plus Cursor and Claude plugin .mdc rules."""
    from .cursor_startup import _active_plugin_roots, _claude_plugin_roots, _plugin_rel_dir

    home = roots.home
    cursor_home = roots.cursor_home or (home / ".cursor")
    project = roots.project
    dirs: list[Path] = [
        project / ".cursor" / "rules",
        cursor_home / "rules",
        project / ".claude" / "rules",
        home / ".claude" / "rules",
    ]
    for plugin_root in _active_plugin_roots(cursor_home):
        rules = _plugin_rel_dir(plugin_root, "rules", "rules")
        if rules is not None:
            dirs.append(rules)
        # Also nested cursor/rules (config-plugin layout)
        nested = plugin_root / "cursor" / "rules"
        if nested.is_dir():
            dirs.append(nested)
    for plugin_root, _state in _claude_plugin_roots(home):
        if (plugin_root / "rules").is_dir():
            dirs.append(plugin_root / "rules")
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir() and not d.is_symlink():
            continue
        try:
            for path in d.rglob("*.mdc"):
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                out.append(path)
        except OSError:
            continue
    return out


def _glob_matches_project(project: Path, pattern: str) -> bool:
    """True if pattern matches at least one path under project."""
    if not project.is_dir() or not pattern or pattern.startswith("/"):
        return False
    try:
        if list(project.glob(pattern)):
            return True
        # Bare *.ext — Path.glob is non-recursive; rglob covers the tree.
        if "/" not in pattern and pattern.startswith("*."):
            return any(True for _ in project.rglob(pattern))
        if not pattern.startswith("**/"):
            return bool(list(project.glob("**/" + pattern)))
    except (ValueError, OSError):
        return False
    return False


def _known_skill_names(skill_files: Sequence[Path]) -> set[str]:
    names: set[str] = set()
    for p in skill_files:
        text = safe_read_text(p) or ""
        meta, _ = parse_front_matter(text)
        names.add(str(meta.get("name") or p.parent.name))
    return names


def _known_rule_names(rule_files: Sequence[Path]) -> set[str]:
    return {p.stem for p in rule_files} | {p.name for p in rule_files}


def check_broken_references(
    roots: Roots,
    *,
    skill_files: Optional[Sequence[Path]] = None,
    rule_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """Paths, @file includes, and skill/rule names that do not exist."""
    home = roots.home
    project = roots.project
    skills = list(skill_files) if skill_files is not None else _iter_skill_files(roots)
    rules = list(rule_files) if rule_files is not None else _iter_rule_files(roots)
    known_skills = _known_skill_names(skills)
    known_rules = _known_rule_names(rules)
    findings: list[CheckFinding] = []
    seen_path_refs: set[tuple[str, int, str]] = set()
    seen_name_refs: set[tuple[str, int, str, str]] = set()

    for path in [*skills, *rules]:
        text = safe_read_text(path) or ""
        # Scan body only — front-matter globs like **/*.py are not path refs.
        _meta, body = parse_front_matter(text)
        if body is not text:
            scan = body
            body_offset = len(text) - len(body)
        else:
            scan = text
            body_offset = 0
        base = path.parent
        src = _short(path, home, project)
        scan = _mask_non_refs(scan)
        code = _code_ranges(scan)
        cands: list[tuple[str, int, str]] = []  # (ref, offset, kind)
        for rx, kind in (
            (_AT_FILE_RE, "at"),
            (_PATH_REF_RE, "path"),
            (_SKILL_REL_RE, "skillrel"),
        ):
            for m in rx.finditer(scan):
                cands.append((m.group(1), m.start(1), kind))
        for m in _MD_LINK_RE.finditer(scan):
            target = m.group(1).split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(("/", "#")) or _in_ranges(code, m.start()):
                continue
            cands.append((target, m.start(1), "link"))
        for ref, start, kind in cands:
            if not _looks_like_file_ref(ref) or ref.startswith("/"):
                continue  # absolute non-home paths: sandbox, container, temp
            if ref.startswith("~/"):
                cand = home / ref[2:]
            else:
                if ref.startswith("./") and _in_ranges(code, start):
                    # Project-relative example in code; only skill-owned names count.
                    parts = ref.split("/")
                    if not (
                        len(parts) > 2
                        and (parts[1] in _SKILL_SUBDIRS or (base / parts[1]).exists())
                    ):
                        continue
                if ref.startswith("../") and _in_ranges(code, start):
                    continue
                cand = (base / ref) if base.exists() else project / ref
                try:
                    cand = cand.resolve()
                except OSError:
                    pass
            if not cand.exists():
                # also try relative to project
                alt = project / ref.lstrip("./")
                if alt.exists():
                    continue
                line = _line_at(text, body_offset + start)
                key = (src, line, ref)
                if key in seen_path_refs:
                    continue
                seen_path_refs.add(key)
                findings.append(
                    CheckFinding(
                        item=path.name,
                        check="broken_references",
                        finding=f"{src}:{line}: missing path {ref!r}",
                        source=src,
                        severity="high",
                        line=line,
                        meta={"ref": ref, "kind": kind},
                    )
                )
        for m in _NAME_REF_RE.finditer(scan):
            kind = "skill" if m.group(0).lower().lstrip().startswith("skill") else "rule"
            name = m.group(1) or m.group(2)
            if not name:
                continue
            line = _line_at(text, body_offset + m.start())
            nkey = (src, line, kind, name)
            if nkey in seen_name_refs:
                continue
            if kind == "skill" and name not in known_skills:
                seen_name_refs.add(nkey)
                findings.append(
                    CheckFinding(
                        item=path.name,
                        check="broken_references",
                        finding=(
                            f"{src}:{line}: "
                            f"referenced skill {name!r} not found"
                        ),
                        source=src,
                        severity="high",
                        line=line,
                        meta={"ref_name": name, "kind": "skill"},
                    )
                )
            elif (
                kind == "rule"
                and name not in known_rules
                and f"{name}.mdc" not in known_rules
            ):
                seen_name_refs.add(nkey)
                findings.append(
                    CheckFinding(
                        item=path.name,
                        check="broken_references",
                        finding=(
                            f"{src}:{line}: "
                            f"referenced rule {name!r} not found"
                        ),
                        source=src,
                        severity="high",
                        line=line,
                        meta={"ref_name": name, "kind": "rule"},
                    )
                )
    return findings


def check_dead_contradictory_rules(
    roots: Roots,
    *,
    rule_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """
    .mdc with alwaysApply false and no globs and no description (never loads);
    globs that match nothing in the scanned project;
    alwaysApply true plus globs (contradictory).
    """
    home = roots.home
    project = roots.project
    rules = list(rule_files) if rule_files is not None else _iter_rule_files(roots)
    findings: list[CheckFinding] = []

    for path in rules:
        text = safe_read_text(path) or ""
        meta, _ = parse_front_matter(text)
        always = meta.get("alwaysApply")
        globs = meta.get("globs") or meta.get("paths") or []
        if isinstance(globs, str):
            globs = [globs]
        if not isinstance(globs, list):
            globs = []
        desc = str(meta.get("description") or "").strip()
        src = _short(path, home, project)

        if always is False and not globs and not desc:
            findings.append(
                CheckFinding(
                    item=path.name,
                    check="dead_contradictory_rules",
                    finding=(
                        f"{src}:1: alwaysApply false with no globs and no "
                        f"description (never loads)"
                    ),
                    source=src,
                    severity="medium",
                    line=1,
                )
            )

        if always is True and globs:
            findings.append(
                CheckFinding(
                    item=path.name,
                    check="dead_contradictory_rules",
                    finding=(
                        f"{src}:1: alwaysApply true plus globs={globs!r} "
                        f"(contradictory)"
                    ),
                    source=src,
                    severity="medium",
                    line=1,
                    meta={"globs": globs},
                )
            )

        if globs and project.is_dir():
            for g in globs:
                pattern = str(g)
                if not _glob_matches_project(project, pattern):
                    findings.append(
                        CheckFinding(
                            item=path.name,
                            check="dead_contradictory_rules",
                            finding=(
                                f"{src}:1: globs entry {pattern!r} matches "
                                f"nothing under project"
                            ),
                            source=src,
                            severity="medium",
                            line=1,
                            meta={"glob": pattern},
                        )
                    )
    return findings


def check_skill_descriptions(
    roots: Roots,
    *,
    skill_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """Missing, under 20 chars, over 1024 chars, or no trigger wording."""
    home = roots.home
    project = roots.project
    skills = list(skill_files) if skill_files is not None else _iter_skill_files(roots)
    findings: list[CheckFinding] = []
    for path in skills:
        text = safe_read_text(path) or ""
        meta, _ = parse_front_matter(text)
        src = _short(path, home, project)
        # Locate description line for file:line
        desc_line = 1
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("description:"):
                desc_line = i
                break
        if "description" not in meta or meta.get("description") is None:
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="skill_description",
                    finding=f"{src}:{desc_line}: skill description missing",
                    source=src,
                    severity="medium",
                    line=desc_line,
                )
            )
            continue
        desc = str(meta.get("description") or "")
        if len(desc) < 20:
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="skill_description",
                    finding=(
                        f"{src}:{desc_line}: skill description under 20 chars "
                        f"({len(desc)})"
                    ),
                    source=src,
                    severity="medium",
                    line=desc_line,
                )
            )
        if len(desc) > 1024:
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="skill_description",
                    finding=(
                        f"{src}:{desc_line}: skill description over 1024 chars "
                        f"({len(desc)})"
                    ),
                    source=src,
                    severity="low",
                    line=desc_line,
                )
            )
        if desc and not _TRIGGER_RE.search(desc):
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="skill_description",
                    finding=(
                        f"{src}:{desc_line}: skill description has no trigger "
                        f"wording (when/whenever/use when/use for/apply to/use this skill to)"
                    ),
                    source=src,
                    severity="low",
                    line=desc_line,
                )
            )
    return findings


def check_file_over_500_lines(
    roots: Roots,
    *,
    skill_files: Optional[Sequence[Path]] = None,
    rule_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """Skills and rules over 500 lines."""
    home = roots.home
    project = roots.project
    skills = list(skill_files) if skill_files is not None else _iter_skill_files(roots)
    rules = list(rule_files) if rule_files is not None else _iter_rule_files(roots)
    findings: list[CheckFinding] = []
    for path in [*skills, *rules]:
        text = safe_read_text(path) or ""
        n = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        if n > 500:
            src = _short(path, home, project)
            findings.append(
                CheckFinding(
                    item=path.name,
                    check="file_over_500_lines",
                    finding=f"{src}:1: file has {n} lines (over 500)",
                    source=src,
                    severity="low",
                    line=1,
                    meta={"lines": n},
                )
            )
    return findings


def check_same_name_drift(
    roots: Roots,
    *,
    skill_files: Optional[Sequence[Path]] = None,
    rule_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """
    Same skill/rule name in multiple folders with different content
    (hash after realpath dedupe).
    """
    home = roots.home
    project = roots.project
    skills = list(skill_files) if skill_files is not None else _iter_skill_files(roots)
    rules = list(rule_files) if rule_files is not None else _iter_rule_files(roots)
    findings: list[CheckFinding] = []

    def group_by_name(files: Sequence[Path], *, skill: bool) -> dict[str, list[tuple[str, str, Path]]]:
        # name -> list of (realpath, hash, path)
        groups: dict[str, list[tuple[str, str, Path]]] = {}
        by_real: dict[str, Path] = {}
        for p in files:
            try:
                real = str(p.resolve())
            except OSError:
                real = str(p)
            if real in by_real:
                continue  # realpath dedupe
            by_real[real] = p
            text = safe_read_text(p) or ""
            meta, _ = parse_front_matter(text)
            name = str(meta.get("name") or (p.parent.name if skill else p.stem))
            groups.setdefault(name, []).append((real, _content_hash(text), p))
        return groups

    for kind, files, is_skill in (
        ("skill", skills, True),
        ("rule", rules, False),
    ):
        for name, entries in group_by_name(files, skill=is_skill).items():
            hashes = {h for _, h, _ in entries}
            if len(entries) > 1 and len(hashes) > 1:
                paths = [_short(p, home, project) for _, _, p in entries]
                findings.append(
                    CheckFinding(
                        item=name,
                        check="same_name_drift",
                        finding=(
                            f"{paths[0]}:1: {kind} name {name!r} has different "
                            f"content at {', '.join(paths)}"
                        ),
                        source=paths[0],
                        severity="medium",
                        line=1,
                        meta={"paths": paths, "kind": kind},
                    )
                )
    return findings


def _config_stale_names(config_path: Path) -> list[str]:
    """`stale_names = [...]` (top level or [checks]) from a TOML file; [] if absent."""
    text = safe_read_text(config_path)
    if not text:
        return []
    try:
        import tomllib

        data = tomllib.loads(text)
    except ImportError:  # Python < 3.11: one-line array fallback
        m = re.search(r"(?m)^\s*stale_names\s*=\s*\[(.*?)\]", text, re.DOTALL)
        return re.findall(r"[\"']([^\"']+)[\"']", m.group(1)) if m else []
    except ValueError:
        return []
    val = data.get("stale_names")
    if val is None and isinstance(data.get("checks"), dict):
        val = data["checks"].get("stale_names")
    return [str(v) for v in val] if isinstance(val, list) else []


def load_stale_names(roots: Optional[Roots] = None) -> tuple[str, ...]:
    """Stale names from AI_AUDIT_STALE_NAMES (comma-separated) and config.toml."""
    names: list[str] = []
    env = os.environ.get(STALE_NAMES_ENV, "")
    names.extend(n.strip() for n in env.split(",") if n.strip())
    home = roots.home if roots is not None else Path.home()
    names.extend(_config_stale_names(home / ".config" / "ai-audit" / "config.toml"))
    return tuple(dict.fromkeys(names))


def check_stale_names(
    roots: Roots,
    *,
    stale_names: Optional[Sequence[str]] = None,
    skill_files: Optional[Sequence[Path]] = None,
    rule_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """Whole-word matches of configurable stale names in skills/rules text."""
    home = roots.home
    project = roots.project
    skills = list(skill_files) if skill_files is not None else _iter_skill_files(roots)
    rules = list(rule_files) if rule_files is not None else _iter_rule_files(roots)
    findings: list[CheckFinding] = []
    if stale_names is None:
        stale_names = load_stale_names(roots)
    if not stale_names:
        return findings
    # Whole-word, case-insensitive
    parts = [re.escape(s) for s in stale_names if s]
    if not parts:
        return findings
    rx = re.compile(r"(?i)\b(?:%s)\b" % "|".join(parts))
    for path in [*skills, *rules]:
        text = safe_read_text(path) or ""
        for m in rx.finditer(text):
            line = _line_at(text, m.start())
            src = _short(path, home, project)
            findings.append(
                CheckFinding(
                    item=path.name,
                    check="stale_names",
                    finding=(
                        f"{src}:{line}: stale name {m.group(0)!r} "
                        f"(whole-word match)"
                    ),
                    source=src,
                    severity="low",
                    line=line,
                    meta={"name": m.group(0)},
                )
            )
    return findings


def check_broken_frontmatter(
    roots: Roots,
    *,
    skill_files: Optional[Sequence[Path]] = None,
    rule_files: Optional[Sequence[Path]] = None,
) -> list[CheckFinding]:
    """
    YAML parse failure, missing name/description for skills,
    unknown keys for .mdc.
    """
    home = roots.home
    project = roots.project
    skills = list(skill_files) if skill_files is not None else _iter_skill_files(roots)
    rules = list(rule_files) if rule_files is not None else _iter_rule_files(roots)
    findings: list[CheckFinding] = []

    def front_matter_broken(text: str) -> Optional[str]:
        if not text.startswith("---"):
            return "missing opening --- front matter"
        # Unclosed
        rest = text[3:]
        if "\n---" not in rest and not rest.lstrip().startswith("---"):
            # allow --- on next lines
            if re.search(r"\n---\s*(\n|$)", text) is None:
                return "unclosed front matter (no closing ---)"
        # Tab after key nonsense / obvious junk
        m = _FRONT_OPEN.search(text)
        if m:
            block = m.group(1)
            if "\t:" in block or block.strip() == "":
                return "empty or invalid front matter block"
        return None

    for path in skills:
        text = safe_read_text(path) or ""
        src = _short(path, home, project)
        broken = front_matter_broken(text)
        if broken:
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="broken_frontmatter",
                    finding=f"{src}:1: {broken}",
                    source=src,
                    severity="high",
                    line=1,
                )
            )
            continue
        meta, _ = parse_front_matter(text)
        if "name" not in meta or not str(meta.get("name") or "").strip():
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="broken_frontmatter",
                    finding=f"{src}:1: skill missing name in front matter",
                    source=src,
                    severity="medium",
                    line=1,
                )
            )
        if "description" not in meta:
            findings.append(
                CheckFinding(
                    item=path.parent.name,
                    check="broken_frontmatter",
                    finding=f"{src}:1: skill missing description in front matter",
                    source=src,
                    severity="medium",
                    line=1,
                )
            )

    for path in rules:
        text = safe_read_text(path) or ""
        src = _short(path, home, project)
        broken = front_matter_broken(text)
        if broken:
            findings.append(
                CheckFinding(
                    item=path.name,
                    check="broken_frontmatter",
                    finding=f"{src}:1: {broken}",
                    source=src,
                    severity="high",
                    line=1,
                )
            )
            continue
        meta, _ = parse_front_matter(text)
        unknown = sorted(k for k in meta if k not in _MDC_KNOWN_KEYS)
        if unknown:
            findings.append(
                CheckFinding(
                    item=path.name,
                    check="broken_frontmatter",
                    finding=(
                        f"{src}:1: .mdc unknown front-matter keys: "
                        f"{', '.join(unknown)}"
                    ),
                    source=src,
                    severity="low",
                    line=1,
                    meta={"unknown_keys": unknown},
                )
            )
    return findings


_FRONT_OPEN = re.compile(r"\A---\s*\n(.*?)(?:\n---\s*(?:\n|$))", re.DOTALL)


def run_quality_checks(
    roots: Roots,
    *,
    stale_names: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Run all checks; return findings sorted by severity then check then finding."""
    skills = _iter_skill_files(roots)
    rules = _iter_rule_files(roots)
    all_findings: list[CheckFinding] = []
    all_findings.extend(
        check_broken_references(roots, skill_files=skills, rule_files=rules)
    )
    all_findings.extend(check_dead_contradictory_rules(roots, rule_files=rules))
    all_findings.extend(check_skill_descriptions(roots, skill_files=skills))
    all_findings.extend(
        check_file_over_500_lines(roots, skill_files=skills, rule_files=rules)
    )
    all_findings.extend(
        check_same_name_drift(roots, skill_files=skills, rule_files=rules)
    )
    all_findings.extend(
        check_stale_names(
            roots, stale_names=stale_names, skill_files=skills, rule_files=rules
        )
    )
    all_findings.extend(
        check_broken_frontmatter(roots, skill_files=skills, rule_files=rules)
    )
    all_findings.sort(
        key=lambda f: (
            _SEVERITY_RANK.get(f.severity, 9),
            f.check,
            f.finding,
        )
    )
    return [f.to_dict() for f in all_findings]
