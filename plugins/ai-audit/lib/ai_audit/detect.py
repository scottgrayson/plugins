"""Host detection from env / well-known paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Roots:
    host: str
    home: Path
    project: Path
    sand_data: Optional[Path] = None
    claude_home: Optional[Path] = None
    cursor_home: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "home": str(self.home),
            "project": str(self.project),
            "sand_data": str(self.sand_data) if self.sand_data else None,
            "claude_home": str(self.claude_home) if self.claude_home else None,
            "cursor_home": str(self.cursor_home) if self.cursor_home else None,
        }


def _exists_dir(*candidates: Path) -> Optional[Path]:
    for c in candidates:
        if c and c.is_dir():
            return c
    return None


def detect_host(
    host: str = "auto",
    home: Optional[Path] = None,
    project: Optional[Path] = None,
    root: Optional[Path] = None,
) -> Roots:
    """
    Resolve host and filesystem roots.

    --root can point at a fixture that contains home/, project/, and/or sand-data/.
    """
    home_path = Path(home or os.environ.get("HOME", "/")).expanduser()
    project_path = Path(project or os.getcwd()).resolve()

    sand = None
    claude_home = None
    cursor_home = None

    if root is not None:
        root = Path(root).resolve()
        # Fixture layouts:
        #   root/home/.claude  root/project/...
        #   root/sand-data/...
        #   root/.claude (treat root as home)
        if (root / "home").is_dir():
            home_path = root / "home"
        elif (root / ".claude").is_dir() or (root / ".cursor").is_dir():
            home_path = root
        if (root / "project").is_dir():
            project_path = root / "project"
        if (root / "sand-data").is_dir():
            sand = root / "sand-data"
        elif (root / "workflows").is_dir() and (root / "agents").is_dir():
            sand = root

    # Env overrides for sand-data / box paths.
    env_sand = os.environ.get("AI_AUDIT_SAND_DATA") or os.environ.get("GROK_BOT_DATA")
    if env_sand:
        sand = Path(env_sand)

    # Well-known Grok Bot paths (feature-detect; may change).
    if sand is None:
        sand = _exists_dir(
            Path("/home/box/sand-data"),
            Path("/home/box/agent-data"),
            home_path / "sand-data",
        )

    claude_home = home_path / ".claude"
    cursor_home = home_path / ".cursor"

    forced = (host or "auto").lower().strip()
    if forced in ("claude", "claude-code", "anthropic"):
        return Roots("claude", home_path, project_path, sand, claude_home, cursor_home)
    if forced in ("cursor",):
        return Roots("cursor", home_path, project_path, sand, claude_home, cursor_home)
    if forced in ("grok", "grok-bot", "bot"):
        return Roots("grok", home_path, project_path, sand, claude_home, cursor_home)

    # Auto-detect priority: explicit sand-data with workflows → grok;
    # else .claude denser than .cursor → claude; else cursor if present; else claude.
    score = {"claude": 0, "cursor": 0, "grok": 0}
    if sand and (sand / "workflows").is_dir():
        score["grok"] += 5
    if sand and (sand / "agents").is_dir():
        score["grok"] += 3
    if sand and (sand / "user-memory").is_dir():
        score["grok"] += 2
    if claude_home.is_dir():
        score["claude"] += 2
        if (claude_home / "projects").is_dir():
            score["claude"] += 2
        if list(claude_home.glob("CLAUDE.md")) or (project_path / "CLAUDE.md").is_file():
            score["claude"] += 1
    if cursor_home.is_dir():
        score["cursor"] += 2
        if (project_path / ".cursor").is_dir():
            score["cursor"] += 2
        if (project_path / "AGENTS.md").is_file():
            score["cursor"] += 1
    if (project_path / ".claude").is_dir():
        score["claude"] += 1

    best = max(score, key=lambda k: score[k])
    if score[best] == 0:
        best = "claude"
    return Roots(best, home_path, project_path, sand, claude_home, cursor_home)
