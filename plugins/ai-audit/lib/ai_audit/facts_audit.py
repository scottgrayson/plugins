"""Facts / memory staleness, duplicates, conflicts, orphans. Read-only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from .common import (
    FactIssue,
    Win,
    est_tokens,
    fact_hash,
    parse_front_matter,
    parse_iso_date,
    safe_read_text,
    similarity,
    today_utc,
)
from .detect import Roots

GROK_FACT_RE = re.compile(r"^\s*-\s*\((\d{4}-\d{2}-\d{2})\)\s*(.+?)\s*$")
TIME_BOUND_WORDS = re.compile(
    r"\b(currently|this week|this month|today|yesterday|right now|asap|temporary|interim)\b",
    re.I,
)
_IMPORT_RE = re.compile(r"@([^\s\]\)\"\'<>]+)")
FACT_QUOTE_MAX = 80


def _q(text: str, n: int = FACT_QUOTE_MAX) -> str:
    t = text.strip()
    return t if len(t) <= n else t[: n - 1] + "…"


@dataclass
class Fact:
    host: str
    path: str
    text: str
    date: str = ""
    source: str = ""
    agent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "path": self.path,
            "text": _q(self.text),
            "date": self.date,
            "source": self.source,
            "agent_id": self.agent_id,
            "est_tokens": est_tokens(len(self.text)),
        }


def _parse_grok_profile(path: Path, agent_id: str) -> list[Fact]:
    text = safe_read_text(path) or ""
    facts: list[Fact] = []
    for line in text.splitlines():
        m = GROK_FACT_RE.match(line)
        if not m:
            continue
        facts.append(
            Fact(
                host="grok",
                path=str(path),
                text=m.group(2).strip(),
                date=m.group(1),
                source="profile.md",
                agent_id=agent_id,
            )
        )
    return facts


def _bullet_facts(host: str, path: Path, text: str, source: str) -> list[Fact]:
    """Extract bullets and non-bullet prose lines/sentences as facts."""
    facts: list[Fact] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("@"):
            continue
        if s.startswith("---"):
            continue
        if s.startswith("-") and len(s) > 8:
            facts.append(
                Fact(host=host, path=str(path), text=s.lstrip("- ").strip(), source=source)
            )
            continue
        # Non-bullet prose: same 8-char minimum as bullets (catch short policy lines).
        if len(s) > 8 and not s.startswith(("```", "<!--")):
            # Prefer whole line when short; otherwise split sentences.
            parts = re.split(r"(?<=[.!?])\s+", s) if len(s) > 120 else [s]
            for part in parts:
                p = part.strip()
                if len(p) > 8:
                    facts.append(Fact(host=host, path=str(path), text=p, source=source))
    return facts


def _collect_imports(path: Path, seen: set[Path]) -> list[Path]:
    out: list[Path] = []
    resolved = path.resolve()
    if resolved in seen or not path.is_file():
        return out
    seen.add(resolved)
    out.append(path)
    text = safe_read_text(path) or ""
    base = path.parent
    for m in _IMPORT_RE.finditer(text):
        rel = m.group(1).strip()
        if rel.startswith("http"):
            continue
        cand = (base / rel).resolve()
        if cand.is_file():
            out.extend(_collect_imports(cand, seen))
    return out


def collect_grok_facts(roots: Roots, stale_days: int) -> tuple[list[Fact], list[FactIssue], list[str]]:
    notes: list[str] = [
        "Only on-disk profile.md facts are audited. Server-side work summaries are not visible.",
        "Use the inline (YYYY-MM-DD) date, not file mtime (restore times are misleading).",
    ]
    facts: list[Fact] = []
    issues: list[FactIssue] = []
    sand = roots.sand_data
    if not sand or not sand.is_dir():
        notes.append("No sand-data root found.")
        return facts, issues, notes

    agents_dir = sand / "agents"
    agent_ids = {p.name for p in agents_dir.iterdir() if p.is_dir()} if agents_dir.is_dir() else set()
    mem_root = sand / "user-memory" / "by-agent"
    if not mem_root.is_dir():
        notes.append("No user-memory/by-agent directory.")
        return facts, issues, notes

    cutoff = today_utc() - timedelta(days=stale_days)
    for profile_md in sorted(mem_root.glob("*/profile.md")):
        agent_id = profile_md.parent.name
        if agent_id not in agent_ids:
            issues.append(
                FactIssue(
                    kind="orphan",
                    path=str(profile_md),
                    detail=f"Memory for agent `{agent_id}` but no agents/{agent_id} directory",
                    saving_est_tokens=0,  # cleanup/clutter only — not loaded, no token saving
                    suggestion="Archive or delete orphaned profile.md after approval.",
                )
            )
        facts.extend(_parse_grok_profile(profile_md, agent_id))

    for f in facts:
        d = parse_iso_date(f.date)
        if d and d < cutoff:
            issues.append(
                FactIssue(
                    kind="stale",
                    path=f.path,
                    detail=f"Fact dated {f.date} is older than {stale_days} days: {_q(f.text)}",
                    date=f.date,
                    saving_est_tokens=est_tokens(len(f.text)),
                    suggestion="Confirm still true; delete or re-date if updated.",
                )
            )
        elif TIME_BOUND_WORDS.search(f.text):
            issues.append(
                FactIssue(
                    kind="stale",
                    path=f.path,
                    detail=f"Time-bound wording may be outdated: {_q(f.text)}",
                    date=f.date,
                    saving_est_tokens=est_tokens(len(f.text)),
                    suggestion="Rewrite without relative time, or delete if obsolete.",
                )
            )

    _add_duplicates_and_conflicts(facts, issues)
    return facts, issues, notes


def collect_claude_facts(roots: Roots, stale_days: int) -> tuple[list[Fact], list[FactIssue], list[str]]:
    notes: list[str] = [
        "Topic files use YAML `modified` front matter. CLAUDE.md/rules have no dates — use git/mtime.",
        "MEMORY.md index lines are not counted separately when topic files exist.",
        "Consider `/doctor prompt-audit` for instruction files.",
    ]
    facts: list[Fact] = []
    issues: list[FactIssue] = []
    home = roots.claude_home or (roots.home / ".claude")
    cutoff = today_utc() - timedelta(days=stale_days)

    projects = home / "projects"
    if projects.is_dir():
        for mem_dir in sorted(projects.glob("*/memory")):
            topic_files = [p for p in mem_dir.glob("*.md") if p.name != "MEMORY.md"]
            # Prefer topic files; skip MEMORY.md index lines to avoid double-counting.
            if topic_files:
                for topic in sorted(topic_files):
                    text = safe_read_text(topic) or ""
                    meta, body = parse_front_matter(text)
                    modified = str(meta.get("modified") or "")
                    ftype = str(meta.get("type") or "topic")
                    body_text = body.strip() or topic.stem.replace("_", " ")
                    facts.append(
                        Fact(
                            host="claude",
                            path=str(topic),
                            text=body_text,
                            date=modified[:10] if modified else "",
                            source=f"topic:{ftype}",
                        )
                    )
                    d = parse_iso_date(modified)
                    if d and d < cutoff:
                        issues.append(
                            FactIssue(
                                kind="stale",
                                path=str(topic),
                                detail=f"Topic modified {modified} older than {stale_days} days",
                                date=modified[:10],
                                saving_est_tokens=est_tokens(len(body_text)),
                                suggestion="Update or delete the topic file after approval.",
                            )
                        )
            else:
                mem = mem_dir / "MEMORY.md"
                if mem.is_file():
                    facts.extend(
                        _bullet_facts("claude", mem, safe_read_text(mem) or "", "MEMORY.md")
                    )

    # CLAUDE.md, CLAUDE.local.md, rules, and @imports
    seed_paths = [
        roots.project / "CLAUDE.md",
        roots.project / "CLAUDE.local.md",
        roots.project / ".claude" / "CLAUDE.md",
        home / "CLAUDE.md",
    ]
    for rules_dir in (home / "rules", roots.project / ".claude" / "rules"):
        if rules_dir.is_dir():
            seed_paths.extend(sorted(rules_dir.rglob("*.md")))

    seen: set[Path] = set()
    for seed in seed_paths:
        for path in _collect_imports(seed, seen):
            text = safe_read_text(path) or ""
            _, body = parse_front_matter(text)
            facts.extend(_bullet_facts("claude", path, body or text, path.name))

    _add_duplicates_and_conflicts(facts, issues)
    return facts, issues, notes


def collect_cursor_facts(
    roots: Roots,
    memories_path: Optional[Path],
    stale_days: int,
) -> tuple[list[Fact], list[FactIssue], list[str]]:
    notes: list[str] = [
        "Cursor memories are server-side only (no local files). Export via the palette command if available.",
        "Without an exported .mdc, only on-disk rules / AGENTS.md are scanned — and they have no per-fact dates.",
    ]
    facts: list[Fact] = []
    issues: list[FactIssue] = []

    if memories_path and Path(memories_path).is_file():
        text = safe_read_text(Path(memories_path)) or ""
        _, body = parse_front_matter(text)
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("-") and len(s) > 2:
                facts.append(
                    Fact(
                        host="cursor",
                        path=str(memories_path),
                        text=s.lstrip("- ").strip(),
                        source="memories_export",
                    )
                )
            elif s and not s.startswith("#") and len(s) > 8:
                facts.append(
                    Fact(host="cursor", path=str(memories_path), text=s, source="memories_export")
                )
        notes.append(f"Loaded memories export: {memories_path}")
    else:
        notes.append(
            "No memories export provided. Ask the user to run Export memories and pass --memories PATH."
        )

    for path in (
        roots.project / "AGENTS.md",
        roots.project / "CLAUDE.md",
        roots.project / "CLAUDE.local.md",
    ):
        text = safe_read_text(path)
        if not text:
            continue
        facts.extend(_bullet_facts("cursor", path, text, path.name))

    rules_dirs = [
        roots.project / ".cursor" / "rules",
        (roots.cursor_home or roots.home / ".cursor") / "rules",
    ]
    for rules_dir in rules_dirs:
        if not rules_dir.is_dir():
            continue
        for path in sorted(rules_dir.rglob("*.mdc")):
            text = safe_read_text(path) or ""
            _, body = parse_front_matter(text)
            facts.extend(_bullet_facts("cursor", path, body, "rule"))

    for f in facts:
        if TIME_BOUND_WORDS.search(f.text):
            issues.append(
                FactIssue(
                    kind="stale",
                    path=f.path,
                    detail=f"Time-bound wording (no date available): {_q(f.text)}",
                    saving_est_tokens=est_tokens(len(f.text)),
                    suggestion="Check git history for rules; confirm or remove.",
                )
            )

    _add_duplicates_and_conflicts(facts, issues)
    return facts, issues, notes


def _has_word(text: str, pattern: str) -> bool:
    """Word-boundary / phrase match; handles negation pairs carefully."""
    return re.search(pattern, text, flags=re.I) is not None


def _polarity_conflict(a: str, b: str) -> bool:
    """
    Detect opposing polarity with word boundaries.
    'must not X' vs 'must not X' is NOT a conflict.
    'use pnpm' vs 'use npm' / 'do not use pnpm' is a conflict.
    """
    al, bl = a.lower(), b.lower()

    pairs = [
        (r"\balways\b", r"\bnever\b"),
        (r"\bprefer\b", r"\bavoid\b"),
        (r"\benable\b", r"\bdisable\b"),
        # Affirmative "must" without following "not" vs "must not" / "must never"
        (r"\bmust\b(?!\s+not\b)(?!\s+never\b)", r"\bmust\s+not\b"),
        (r"\buse\b(?!\s+not\b)", r"\bdo\s+not\s+use\b"),
        (r"\buse\b(?!\s+not\b)", r"\bdon'?t\s+use\b"),
    ]
    for pos, neg in pairs:
        if (_has_word(al, pos) and _has_word(bl, neg)) or (
            _has_word(bl, pos) and _has_word(al, neg)
        ):
            return True

    # Package-manager style: both say "use X" with different X
    use_re = re.compile(r"\buse\s+(\w+)\b", re.I)
    ma = use_re.search(al)
    mb = use_re.search(bl)
    if ma and mb and ma.group(1) != mb.group(1):
        # Same topic window (high overlap elsewhere) handled by caller similarity
        return True

    return False


def _add_duplicates_and_conflicts(facts: list[Fact], issues: list[FactIssue]) -> None:
    by_hash: dict[str, list[Fact]] = {}
    for f in facts:
        by_hash.setdefault(fact_hash(f.text), []).append(f)
    for _h, group in by_hash.items():
        if len(group) < 2:
            continue
        paths = sorted({g.path for g in group})
        agents = sorted({g.agent_id for g in group if g.agent_id})
        detail = f"Repeated fact across {len(group)} entries"
        if agents:
            detail += f" (agents: {', '.join(agents)})"
        detail += f": {_q(group[0].text)}"
        issues.append(
            FactIssue(
                kind="duplicate",
                path="; ".join(paths[:3]),
                detail=detail,
                date=max((g.date for g in group if g.date), default=""),
                saving_est_tokens=est_tokens(sum(len(g.text) for g in group[1:])),
                suggestion="Keep the newest dated copy; delete extras after approval.",
            )
        )

    n = len(facts)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = facts[i], facts[j]
            if a.path == b.path and a.text == b.text:
                continue
            sim = similarity(a.text, b.text)
            conflict = _polarity_conflict(a.text, b.text)
            # Polarity conflicts (pnpm vs npm, always vs never) can share few words;
            # require only modest topical overlap. Near-duplicates need higher sim.
            if conflict and sim < 0.2:
                continue
            if not conflict and sim < 0.45:
                continue
            dated_near_dup = (
                sim > 0.75
                and a.text.lower() != b.text.lower()
                and a.date
                and b.date
                and a.date != b.date
                and conflict
            )
            if conflict or dated_near_dup:
                newer = a if (a.date or "") >= (b.date or "") else b
                older = b if newer is a else a
                issues.append(
                    FactIssue(
                        kind="conflict",
                        path=f"{a.path} vs {b.path}",
                        detail=f"Possible conflict (sim={sim:.2f}): “{_q(a.text)}” vs “{_q(b.text)}”",
                        date=newer.date,
                        saving_est_tokens=est_tokens(len(older.text)),
                        suggestion=(
                            f"Prefer the newer dated fact ({newer.date or 'undated'}); "
                            "remove the other after approval."
                        ),
                    )
                )


def issues_to_wins(issues: list[FactIssue]) -> list[Win]:
    """v0.1.0: savings-ranked wins out of scope — issues carry the reasons."""
    return []



def run_facts_audit(
    roots: Roots,
    stale_days: int = 30,
    memories_path: Optional[Path] = None,
) -> dict[str, Any]:
    if roots.host == "claude":
        facts, issues, notes = collect_claude_facts(roots, stale_days)
    elif roots.host == "cursor":
        facts, issues, notes = collect_cursor_facts(roots, memories_path, stale_days)
    else:
        facts, issues, notes = collect_grok_facts(roots, stale_days)

    wins = issues_to_wins(issues)
    return {
        "audit": "facts",
        "host": roots.host,
        "roots": roots.to_dict(),
        "stale_days": stale_days,
        "facts": [f.to_dict() for f in facts],
        "issues": [i.to_dict() for i in issues],
        "wins": [w.to_dict() for w in wins],
        "notes": notes,
        "counts": {
            "facts": len(facts),
            "issues": len(issues),
            "stale": sum(1 for i in issues if i.kind == "stale"),
            "duplicate": sum(1 for i in issues if i.kind == "duplicate"),
            "conflict": sum(1 for i in issues if i.kind == "conflict"),
            "orphan": sum(1 for i in issues if i.kind == "orphan"),
        },
    }
