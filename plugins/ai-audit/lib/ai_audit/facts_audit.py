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
    rank_wins,
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
            "text": self.text,
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
                    saving_est_tokens=est_tokens(profile_md.stat().st_size if profile_md.is_file() else 0),
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
                    detail=f"Fact dated {f.date} is older than {stale_days} days: {f.text[:120]}",
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
                    detail=f"Time-bound wording may be outdated: {f.text[:120]}",
                    date=f.date,
                    saving_est_tokens=est_tokens(len(f.text)),
                    suggestion="Rewrite without relative time, or delete if obsolete.",
                )
            )

    _add_duplicates_and_conflicts(facts, issues)
    return facts, issues, notes


def collect_claude_facts(roots: Roots, stale_days: int) -> tuple[list[Fact], list[FactIssue], list[str]]:
    notes: list[str] = [
        "Topic files use YAML `modified` front matter. MEMORY.md index lines and CLAUDE.md have no dates — use git/mtime.",
        "Consider `/doctor prompt-audit` for instruction files (not auto memory).",
    ]
    facts: list[Fact] = []
    issues: list[FactIssue] = []
    home = roots.claude_home or (roots.home / ".claude")
    cutoff = today_utc() - timedelta(days=stale_days)

    projects = home / "projects"
    if projects.is_dir():
        for topic in sorted(projects.glob("*/memory/*.md")):
            if topic.name == "MEMORY.md":
                # Index lines — no dates; treat each non-empty bullet as a fact without date
                text = safe_read_text(topic) or ""
                for line in text.splitlines():
                    s = line.strip()
                    if s.startswith("-") and len(s) > 2:
                        facts.append(
                            Fact(
                                host="claude",
                                path=str(topic),
                                text=s.lstrip("- ").strip(),
                                source="MEMORY.md",
                            )
                        )
                continue
            text = safe_read_text(topic) or ""
            meta, body = parse_front_matter(text)
            modified = str(meta.get("modified") or "")
            ftype = str(meta.get("type") or "topic")
            body_text = body.strip() or topic.stem.replace("_", " ")
            facts.append(
                Fact(
                    host="claude",
                    path=str(topic),
                    text=body_text[:500],
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

    # CLAUDE.md lines as undated facts (for duplicate checks vs memory)
    for path in (
        roots.project / "CLAUDE.md",
        roots.project / ".claude" / "CLAUDE.md",
        home / "CLAUDE.md",
    ):
        text = safe_read_text(path)
        if not text:
            continue
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("-") and len(s) > 8:
                facts.append(
                    Fact(host="claude", path=str(path), text=s.lstrip("- ").strip(), source="CLAUDE.md")
                )

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
        meta, body = parse_front_matter(text)
        # Treat bullets / paragraphs as facts — no reliable dates in export
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
            elif s and not s.startswith("#") and len(s) > 20:
                facts.append(
                    Fact(host="cursor", path=str(memories_path), text=s, source="memories_export")
                )
        notes.append(f"Loaded memories export: {memories_path}")
    else:
        notes.append(
            "No memories export provided. Ask the user to run Export memories and pass --memories PATH."
        )

    # Rules / AGENTS as facts for duplicate detection
    for path in (
        roots.project / "AGENTS.md",
        roots.project / "CLAUDE.md",
    ):
        text = safe_read_text(path)
        if not text:
            continue
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("-") and len(s) > 8:
                facts.append(
                    Fact(host="cursor", path=str(path), text=s.lstrip("- ").strip(), source=path.name)
                )

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
            for line in body.splitlines():
                s = line.strip()
                if s.startswith("-") and len(s) > 8:
                    facts.append(
                        Fact(host="cursor", path=str(path), text=s.lstrip("- ").strip(), source="rule")
                    )

    # No dates → heuristic time-bound only
    for f in facts:
        if TIME_BOUND_WORDS.search(f.text):
            issues.append(
                FactIssue(
                    kind="stale",
                    path=f.path,
                    detail=f"Time-bound wording (no date available): {f.text[:120]}",
                    saving_est_tokens=est_tokens(len(f.text)),
                    suggestion="Check git history for rules; confirm or remove.",
                )
            )

    _add_duplicates_and_conflicts(facts, issues)
    return facts, issues, notes


def _add_duplicates_and_conflicts(facts: list[Fact], issues: list[FactIssue]) -> None:
    # Exact / near-duplicate by normalized hash and similarity
    by_hash: dict[str, list[Fact]] = {}
    for f in facts:
        by_hash.setdefault(fact_hash(f.text), []).append(f)
    for h, group in by_hash.items():
        if len(group) < 2:
            continue
        paths = sorted({g.path for g in group})
        agents = sorted({g.agent_id for g in group if g.agent_id})
        detail = f"Repeated fact across {len(group)} entries"
        if agents:
            detail += f" (agents: {', '.join(agents)})"
        detail += f": {group[0].text[:100]}"
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

    # Pairwise high-similarity conflicts: opposing polarity keywords
    oppose = [
        ("always", "never"),
        ("prefer", "avoid"),
        ("enable", "disable"),
        ("must", "must not"),
        ("use ", "do not use "),
    ]
    n = len(facts)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = facts[i], facts[j]
            if a.path == b.path and a.text == b.text:
                continue
            sim = similarity(a.text, b.text)
            if sim < 0.45:
                continue
            # Same topic-ish: check opposing words
            al, bl = a.text.lower(), b.text.lower()
            conflict = False
            for w1, w2 in oppose:
                if (w1 in al and w2 in bl) or (w2 in al and w1 in bl):
                    conflict = True
                    break
            if conflict or (sim > 0.75 and a.text.lower() != b.text.lower() and a.date and b.date and a.date != b.date):
                newer = a if (a.date or "") >= (b.date or "") else b
                older = b if newer is a else a
                issues.append(
                    FactIssue(
                        kind="conflict",
                        path=f"{a.path} vs {b.path}",
                        detail=f"Possible conflict (sim={sim:.2f}): “{a.text[:80]}” vs “{b.text[:80]}”",
                        date=newer.date,
                        saving_est_tokens=est_tokens(len(older.text)),
                        suggestion=f"Prefer the newer dated fact ({newer.date or 'undated'}); remove the other after approval.",
                    )
                )


def issues_to_wins(issues: list[FactIssue]) -> list[Win]:
    wins: list[Win] = []
    # Aggregate by kind
    by_kind: dict[str, list[FactIssue]] = {}
    for iss in issues:
        by_kind.setdefault(iss.kind, []).append(iss)

    for kind, group in by_kind.items():
        saving = sum(i.saving_est_tokens for i in group)
        wins.append(
            Win(
                title={
                    "stale": "Drop or refresh stale facts",
                    "duplicate": "Deduplicate repeated facts",
                    "conflict": "Resolve conflicting facts",
                    "orphan": "Remove orphaned memory files",
                }.get(kind, f"Fix {kind} issues"),
                saving_est_tokens=max(saving, len(group)),
                measured=f"{len(group)} {kind} issue(s); ~{saving} est. tokens",
                rationale=group[0].detail[:160],
                suggestion=group[0].suggestion or "Show a diff; apply only after approval.",
                path=group[0].path,
            )
        )
    return rank_wins(wins, limit=5)


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
