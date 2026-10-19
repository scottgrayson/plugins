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
    isolated: bool = False  # True when --root fully isolates detection

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "home": str(self.home),
            "project": str(self.project),
            "sand_data": str(self.sand_data) if self.sand_data else None,
            "claude_home": str(self.claude_home) if self.claude_home else None,
            "cursor_home": str(self.cursor_home) if self.cursor_home else None,
            "isolated": self.isolated,
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

    --root points at a fixture/root that contains home/, project/, and/or
    sand-data/. When set, detection is fully isolated: no fallback to
    /home/box/*, real $HOME, or env sand-data outside that tree.
    """
    isolated = root is not None
    sand: Optional[Path] = None

    if isolated:
        root_path = Path(root).resolve()
        # Fixture layouts only — never leave the root.
        if (root_path / "home").is_dir():
            home_path = root_path / "home"
        elif (root_path / ".claude").is_dir() or (root_path / ".cursor").is_dir():
            home_path = root_path
        else:
            # Do not fall back to real $HOME — stay inside --root.
            home_path = root_path

        if (root_path / "project").is_dir():
            project_path = root_path / "project"
        else:
            project_path = root_path

        if (root_path / "sand-data").is_dir():
            sand = root_path / "sand-data"
        elif (root_path / "workflows").is_dir() and (root_path / "agents").is_dir():
            sand = root_path
        # Intentionally ignore AI_AUDIT_SAND_DATA / /home/box/* when isolated.
    else:
        home_path = Path(home or os.environ.get("HOME", "/")).expanduser()
        project_path = Path(project or os.getcwd()).resolve()

        env_sand = os.environ.get("AI_AUDIT_SAND_DATA") or os.environ.get("GROK_BOT_DATA")
        if env_sand:
            sand = Path(env_sand)
        else:
            sand = _exists_dir(
                Path("/home/box/sand-data"),
                Path("/home/box/agent-data"),
                home_path / "sand-data",
            )

    claude_home = home_path / ".claude"
    cursor_home = home_path / ".cursor"

    forced = (host or "auto").lower().strip()
    if forced in ("claude", "claude-code", "anthropic"):
        return Roots("claude", home_path, project_path, sand, claude_home, cursor_home, isolated)
    if forced in ("cursor",):
        return Roots("cursor", home_path, project_path, sand, claude_home, cursor_home, isolated)
    if forced in ("grok", "grok-bot", "bot"):
        return Roots("grok", home_path, project_path, sand, claude_home, cursor_home, isolated)

    # Auto-detect from roots only (already isolated when --root set).
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
        if (claude_home / "CLAUDE.md").is_file() or (project_path / "CLAUDE.md").is_file():
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
    return Roots(best, home_path, project_path, sand, claude_home, cursor_home, isolated)
