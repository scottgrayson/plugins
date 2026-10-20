"""
Detect provider · environment headings for startup reports.

Heading examples: "Cursor · Mac", "Cursor · Cloud", "Claude Code · Cloud",
"Grok Bot · Cloud".

Sources (documented inline at each marker):
- Cursor local vs cloud:
  - Docs: https://cursor.com/docs/agent/tools/terminal — CURSOR_AGENT is set in
    Cursor agent terminal sessions (local IDE and cloud).
  - Docs: https://cursor.com/docs/cloud-agent/setup — Cloud Agents run in
    Cursor-managed VMs with injected secrets.
  - Observable on Cursor Cloud Agent VMs (this repo's CI/dev box, 2026-10-20):
    CURSOR_AGENT=1, CURSOR_AGENT_SOCKET=/run/cursor/api.sock, hostname "cursor",
    /exec-daemon present, CLOUD_AGENT_* env vars, CURSOR_WORKLOAD_CGROUP.
  - Local Cursor CLI often sets CURSOR_AGENT=1 with CURSOR_AGENT_CLI_LOCAL_MODE
    (see software-mansion/argent cloud-agent-detect.ts). Worker pool cloud runs
    may set CURSOR_AGENT_WORKER_ID / CURSOR_WORKER_POOL_NAME.
- Claude Code cloud:
  - Docs: https://code.claude.com/docs/en/env-vars and
    https://code.claude.com/docs/en/cloud-environments —
    CLAUDE_CODE_REMOTE / CLAUDE_CODE_REMOTE_SESSION_ID.
  - Claude Code binary cloud check (mirrored by argent): CLAUDE_CODE_ENVIRONMENT_KIND
    in {byoc, anthropic_cloud}; CLAUDE_CODE_ENTRYPOINT in remote_* / claude-in-teams.
- Grok Bot cloud: sand-data layout / GROK-style box markers when present; otherwise
  treat as Cloud when sand_data roots are used without a local desktop OS signal.
- Local OS labels: platform.system() → Mac / Linux / Windows
  (Darwin → Mac). Cloud markers win over local OS for the environment half.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any, Mapping, Optional

# Claude Code remote entrypoints (argent cloud-agent-detect.ts; Claude binary).
_CLAUDE_REMOTE_ENTRYPOINTS = frozenset(
    {
        "remote",
        "remote_baku",
        "remote_cowork",
        "remote_desktop",
        "remote_mobile",
        "claude-in-teams",
    }
)
_CLAUDE_CLOUD_KINDS = frozenset({"byoc", "anthropic_cloud"})

PROVIDER_LABELS = {
    "cursor": "Cursor",
    "claude": "Claude Code",
    "grok": "Grok Bot",
}


def local_os_label(system: Optional[str] = None) -> str:
    """Map platform.system() to Mac / Linux / Windows."""
    s = (system or platform.system() or "").lower()
    if s == "darwin":
        return "Mac"
    if s == "windows":
        return "Windows"
    if s == "linux":
        return "Linux"
    return "Linux" if s else "Linux"


def _truthy(val: Optional[str]) -> bool:
    return bool(val) and val.strip() not in {"0", "false", "False", ""}


def is_cursor_cloud(
    env: Mapping[str, str] | None = None,
    *,
    path_exists: Optional[Any] = None,
) -> tuple[bool, list[str]]:
    """
    Return (is_cloud, matched_markers).

    Prefer high-confidence cloud-only markers over bare CURSOR_AGENT (also local).
    """
    e = env if env is not None else os.environ
    exists = path_exists or (lambda p: Path(p).exists())
    hits: list[str] = []

    # Worker / pool (argent; cursor-agent cloud workers).
    if _truthy(e.get("CURSOR_AGENT_WORKER_ID")):
        hits.append("CURSOR_AGENT_WORKER_ID")
    if _truthy(e.get("CURSOR_WORKER_POOL_NAME")):
        hits.append("CURSOR_WORKER_POOL_NAME")

    # Managed Cloud Agent VM observables (verified on Cursor cloud pods).
    if _truthy(e.get("CURSOR_AGENT_SOCKET")) and "cursor" in (
        e.get("CURSOR_AGENT_SOCKET") or ""
    ).lower():
        hits.append("CURSOR_AGENT_SOCKET")
    if _truthy(e.get("CLOUD_AGENT_ALL_SECRET_NAMES")) or _truthy(
        e.get("CLOUD_AGENT_INJECTED_SECRET_NAMES")
    ):
        hits.append("CLOUD_AGENT_*")
    if _truthy(e.get("CURSOR_WORKLOAD_CGROUP")):
        hits.append("CURSOR_WORKLOAD_CGROUP")
    if (e.get("HOSTNAME") or platform.node() or "").lower() == "cursor":
        # Only count hostname with another cloud signal or agent socket/exec-daemon.
        if hits or exists("/exec-daemon") or exists("/opt/cursor"):
            hits.append("hostname=cursor")
    if exists("/exec-daemon") and (
        _truthy(e.get("CURSOR_AGENT")) or hits
    ):
        if "exec-daemon" not in " ".join(hits):
            hits.append("/exec-daemon")

    # Explicit local CLI mode → not cloud even if CURSOR_AGENT is set.
    if _truthy(e.get("CURSOR_AGENT_CLI_LOCAL_MODE")):
        return False, ["CURSOR_AGENT_CLI_LOCAL_MODE"]

    return (len(hits) > 0, hits)


def is_claude_cloud(env: Mapping[str, str] | None = None) -> tuple[bool, list[str]]:
    e = env if env is not None else os.environ
    hits: list[str] = []
    kind = (e.get("CLAUDE_CODE_ENVIRONMENT_KIND") or "").strip()
    if kind in _CLAUDE_CLOUD_KINDS:
        hits.append(f"CLAUDE_CODE_ENVIRONMENT_KIND={kind}")
    entry = (e.get("CLAUDE_CODE_ENTRYPOINT") or "").strip()
    if entry in _CLAUDE_REMOTE_ENTRYPOINTS:
        hits.append(f"CLAUDE_CODE_ENTRYPOINT={entry}")
    if _truthy(e.get("CLAUDE_CODE_REMOTE_SESSION_ID")):
        hits.append("CLAUDE_CODE_REMOTE_SESSION_ID")
    if _truthy(e.get("CLAUDE_CODE_REMOTE")):
        hits.append("CLAUDE_CODE_REMOTE")
    return (len(hits) > 0, hits)


def is_grok_cloud(
    env: Mapping[str, str] | None = None,
    *,
    sand_data: Optional[Path] = None,
    path_exists: Optional[Any] = None,
) -> tuple[bool, list[str]]:
    """
    Grok Bot typically runs on a remote box with sand-data/.
    Markers: GROK_BOT / XAI agent env, or sand-data layout without desktop Cursor.
    """
    e = env if env is not None else os.environ
    exists = path_exists or (lambda p: Path(p).exists())
    hits: list[str] = []
    for key in ("GROK_BOT", "GROK_AGENT", "XAI_GROK_BOT", "SAND_DATA"):
        if _truthy(e.get(key)):
            hits.append(key)
    if sand_data and Path(sand_data).is_dir():
        hits.append("sand_data/")
    # Grok cloud boxes often use /home/box (observable on Grok agent VMs).
    if exists("/home/box") and (hits or sand_data):
        hits.append("/home/box")
    return (len(hits) > 0, hits)


def detect_environment(
    provider: str,
    *,
    env: Mapping[str, str] | None = None,
    override: Optional[str] = None,
    sand_data: Optional[Path] = None,
    path_exists: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Return provider, environment, heading, and detection evidence.

    override: force environment half only ("Cloud", "Mac", "Linux", "Windows")
    or a full "Provider · Env" string.
    """
    provider_key = {
        "cursor": "cursor",
        "claude": "claude",
        "grok": "grok",
        "claude_code": "claude",
        "grok_bot": "grok",
    }.get((provider or "").lower().replace(" ", "_"), (provider or "cursor").lower())
    provider_label = PROVIDER_LABELS.get(provider_key, provider_key.title())

    if override:
        ov = override.strip()
        # Accept "Provider · Env" or "Provider / Env" as a full heading override.
        for sep in (" · ", "·", " / ", "/"):
            if sep in ov:
                parts = [p.strip() for p in ov.split(sep) if p.strip()]
                if len(parts) >= 2:
                    return {
                        "provider": parts[0],
                        "provider_key": provider_key,
                        "environment": parts[1],
                        "heading": f"{parts[0]} · {parts[1]}",
                        "markers": ["--env override"],
                        "cloud": parts[1].lower() == "cloud",
                    }
                break
        # Environment-only override
        env_label = ov[0].upper() + ov[1:] if ov else ov
        if ov.lower() == "cloud":
            env_label = "Cloud"
        elif ov.lower() in {"mac", "darwin"}:
            env_label = "Mac"
        elif ov.lower() == "linux":
            env_label = "Linux"
        elif ov.lower() in {"windows", "win"}:
            env_label = "Windows"
        return {
            "provider": provider_label,
            "provider_key": provider_key,
            "environment": env_label,
            "heading": f"{provider_label} · {env_label}",
            "markers": ["--env override"],
            "cloud": env_label == "Cloud",
        }

    e = env if env is not None else os.environ
    markers: list[str] = []
    cloud = False

    if provider_key == "cursor":
        cloud, markers = is_cursor_cloud(e, path_exists=path_exists)
    elif provider_key == "claude":
        cloud, markers = is_claude_cloud(e)
    elif provider_key == "grok":
        cloud, markers = is_grok_cloud(
            e, sand_data=sand_data, path_exists=path_exists
        )

    if cloud:
        env_label = "Cloud"
    else:
        env_label = local_os_label()
        markers = markers or [f"platform.system={platform.system()}"]

    return {
        "provider": provider_label,
        "provider_key": provider_key,
        "environment": env_label,
        "heading": f"{provider_label} · {env_label}",
        "markers": markers,
        "cloud": cloud,
    }


def section_heading(info: dict[str, Any]) -> str:
    return str(info.get("heading") or "Unknown · Unknown")
