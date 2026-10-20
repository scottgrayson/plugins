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
- Grok Bot cloud: requires 2+ markers (env + /home/box); sand-data alone is not enough.
- Local OS labels: platform.system() → Mac / Linux / Windows
  (Darwin → Mac). Cloud requires >=2 independent markers (round-18 D4).
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from .common import redact_secrets

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
_FALSY = frozenset({"", "0", "false", "False", "no", "No", "NO", "off", "Off", "OFF", "none", "None"})

PROVIDER_LABELS = {
    "cursor": "Cursor",
    "claude": "Claude Code",
    "grok": "Grok Bot",
}

_ENV_WHITELIST = frozenset({"Cloud", "Mac", "Linux", "Windows"})


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
    if val is None:
        return False
    s = val.strip()
    if s in _FALSY:
        return False
    return bool(s)


def _sanitize_env_override(text: str) -> str:
    """Whitelist env labels; redact anything secret-shaped in --env text."""
    cleaned = redact_secrets(text.strip())
    # Drop control chars
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    return cleaned[:120]


def is_cursor_cloud(
    env: Mapping[str, str] | None = None,
    *,
    path_exists: Optional[Any] = None,
) -> tuple[bool, list[str]]:
    """
    Return (is_cloud, matched_markers).

    Round-18 D4: require two independent markers. Bare CURSOR_AGENT alone ≠ cloud.
    """
    e = env if env is not None else os.environ
    exists = path_exists or (lambda p: Path(p).exists())

    # Explicit local CLI mode → not cloud.
    if _truthy(e.get("CURSOR_AGENT_CLI_LOCAL_MODE")):
        return False, ["CURSOR_AGENT_CLI_LOCAL_MODE"]

    hits: list[str] = []

    if _truthy(e.get("CURSOR_AGENT_WORKER_ID")):
        hits.append("CURSOR_AGENT_WORKER_ID")
    if _truthy(e.get("CURSOR_WORKER_POOL_NAME")):
        hits.append("CURSOR_WORKER_POOL_NAME")

    # Socket must look like a cloud agent socket, not ~/.cursor/agent.sock
    sock = e.get("CURSOR_AGENT_SOCKET") or ""
    if _truthy(sock) and (
        sock.startswith("/run/cursor/") or sock.startswith("/var/run/cursor/")
    ):
        hits.append("CURSOR_AGENT_SOCKET")

    if _truthy(e.get("CLOUD_AGENT_ALL_SECRET_NAMES")) or _truthy(
        e.get("CLOUD_AGENT_INJECTED_SECRET_NAMES")
    ):
        hits.append("CLOUD_AGENT_*")
    if _truthy(e.get("CURSOR_WORKLOAD_CGROUP")):
        hits.append("CURSOR_WORKLOAD_CGROUP")
    if (e.get("HOSTNAME") or platform.node() or "").lower() == "cursor":
        hits.append("hostname=cursor")
    if exists("/exec-daemon"):
        hits.append("/exec-daemon")
    if exists("/opt/cursor/artifacts") or exists("/opt/cursor"):
        # Weaker alone; counts toward the two-marker rule with others.
        if "opt-cursor" not in hits:
            hits.append("/opt/cursor")

    # Need >= 2 independent markers.
    return (len(hits) >= 2, hits)


def is_claude_cloud(env: Mapping[str, str] | None = None) -> tuple[bool, list[str]]:
    """Claude Cloud requires ≥2 independent markers (round-19 #4)."""
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
    return (len(hits) >= 2, hits)


def is_grok_cloud(
    env: Mapping[str, str] | None = None,
    *,
    sand_data: Optional[Path] = None,
    path_exists: Optional[Any] = None,
) -> tuple[bool, list[str]]:
    """
    Grok Bot cloud: require 2+ markers. sand-data alone (fixtures / local desktop)
    is not enough. Box-only markers: SAND_BOX_ID / BOX_ID + /home/box (round-19 #4).
    """
    e = env if env is not None else os.environ
    exists = path_exists or (lambda p: Path(p).exists())
    hits: list[str] = []
    for key in ("GROK_BOT", "GROK_AGENT", "XAI_GROK_BOT"):
        if _truthy(e.get(key)):
            hits.append(key)
    if _truthy(e.get("SAND_DATA")):
        hits.append("SAND_DATA")
    # Box-only cloud markers (Grok desktop sand-data is not enough).
    for key in ("SAND_BOX_ID", "BOX_ID", "CURSOR_CLOUD_AGENT_ID"):
        if _truthy(e.get(key)):
            hits.append(key)
    if exists("/home/box"):
        hits.append("/home/box")
    if exists("/home/box/sand-data") and _truthy(e.get("SAND_BOX_ID") or e.get("BOX_ID")):
        # Reinforces box pairing; already counted via keys + /home/box.
        pass
    # sand_data path alone does NOT count toward cloud (fixture/local).
    return (len(hits) >= 2, hits)


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
    or a full "Provider · Env" string (sanitised / redacted).
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
        ov = _sanitize_env_override(override)
        for sep in (" · ", "·", " / ", "/"):
            if sep in ov:
                parts = [p.strip() for p in ov.split(sep) if p.strip()]
                if len(parts) >= 2:
                    prov = parts[0]
                    # Only allow known provider labels or current provider
                    if prov not in PROVIDER_LABELS.values():
                        prov = provider_label
                    env_part = parts[1]
                    if env_part not in _ENV_WHITELIST:
                        # Redact unknown env half to a safe token
                        env_part = "Cloud" if "cloud" in env_part.lower() else local_os_label()
                        if "REDACTED" in parts[1]:
                            env_part = local_os_label()
                    return {
                        "provider": prov,
                        "provider_key": provider_key,
                        "environment": env_part,
                        "heading": f"{prov} · {env_part}",
                        "markers": ["--env override"],
                        "cloud": env_part == "Cloud",
                    }
                break
        # Environment-only override (whitelist)
        low = ov.lower()
        if low == "cloud":
            env_label = "Cloud"
        elif low in {"mac", "darwin"}:
            env_label = "Mac"
        elif low == "linux":
            env_label = "Linux"
        elif low in {"windows", "win"}:
            env_label = "Windows"
        else:
            env_label = local_os_label()
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
