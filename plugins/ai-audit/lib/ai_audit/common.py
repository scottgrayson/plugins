"""Shared helpers: sizes, front matter, secret skips, ranking."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

CHARS_PER_TOKEN = 4.0

SECRET_BASENAMES = frozenset(
    {
        "host-secrets.json",
        "box-secrets.json",
        "gateway.json",
        "chrome-cookie-seed.json",
        ".env",
        ".env.local",
        "credentials.json",
        "secrets.json",
    }
)
SECRET_DIR_NAMES = frozenset({"secrets", ".ai"})
SECRET_PATH_FRAGMENTS = (
    "/secrets/",
    "/.ai/secrets/",
    "host-secrets",
    "box-secrets",
)
SECRET_JSON_KEYS = frozenset(
    {
        "env",
        "headers",
        "apiKey",
        "api_key",
        "token",
        "password",
        "secret",
        "authorization",
        "Authorization",
        "accessToken",
        "refreshToken",
    }
)

# Value-pattern redaction for secrets in free-text (facts, notes, periods).
# Whole values are replaced (not a visible prefix of the secret).
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*([=:])\s*([^\s,;]+)"
)
_SECRET_TOKEN_RES = (
    # Provider API keys / tokens (entire match → label…REDACTED)
    (re.compile(r"(?i)\bsk-ant-[A-Za-z0-9_\-]{8,}\b"), "sk-ant-…REDACTED"),
    (re.compile(r"(?i)\bsk-proj-[A-Za-z0-9_\-]{16,}\b"), "sk-proj-…REDACTED"),
    (re.compile(r"(?i)\bsk-or-[A-Za-z0-9_\-]{16,}\b"), "sk-or-…REDACTED"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-…REDACTED"),
    (re.compile(r"(?i)\bxai-[A-Za-z0-9_\-]{16,}\b"), "xai-…REDACTED"),
    (re.compile(r"(?i)\b(ghp_|gho_|github_pat_)[A-Za-z0-9_\-]{16,}\b"), "gh-…REDACTED"),
    (re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9_\-]{10,}\b"), "xox-…REDACTED"),
    (re.compile(r"(?i)\bAIza[A-Za-z0-9_\-]{20,}\b"), "AIza-…REDACTED"),
    (re.compile(r"(?i)\bAKIA[A-Z0-9]{12,}\b"), "AKIA-…REDACTED"),
    # Cursor Admin API keys
    (re.compile(r"(?i)\bcrsr_[A-Za-z0-9_\-]{8,}\b"), "crsr_…REDACTED"),
    # Bearer + JWT (header.payload.sig)
    (
        re.compile(
            r"(?i)\bBearer\s+eyJ[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+){2,}"
        ),
        "Bearer …REDACTED",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+=*(?:\.[A-Za-z0-9_\-]+=*){2,}\b"),
        "eyJ…REDACTED",
    ),
)
# AWS secret access key: 40-char base64-ish with / or + (avoids redacting plain hex).
_AWS_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9/+=])([A-Za-z0-9+/]{40})(?![A-Za-z0-9/+=])"
)


def redact_secrets(text: str) -> str:
    """Replace secret-shaped values in user-sourced strings. Safe on non-strings via str()."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    out = text
    # key=value / key: value — keep the key name, redact the whole value
    out = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}…REDACTED", out)
    for rx, repl in _SECRET_TOKEN_RES:
        out = rx.sub(repl, out)

    def _aws(m: re.Match[str]) -> str:
        s = m.group(1)
        return "…REDACTED" if ("/" in s or "+" in s) else s

    out = _AWS_SECRET_RE.sub(_aws, out)
    return out


def redact_tree(obj: Any) -> Any:
    """Deep-copy-ish walk: redact every string leaf in dict/list structures."""
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, list):
        return [redact_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_tree(v) for k, v in obj.items()}
    return obj



@dataclass
class InventoryItem:
    host: str
    kind: str
    scope: str
    path: str
    bytes: int
    est_tokens: int
    load: str  # always | conditional | on_demand | unknown
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Win:
    title: str
    measured: str
    rationale: str
    suggestion: str = ""
    path: str = ""
    ask: bool = False
    method: str = ""
    overlaps: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FactIssue:
    kind: str  # stale | duplicate | conflict | orphan
    path: str
    detail: str
    date: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["detail"] = redact_secrets(d.get("detail") or "")
        d["suggestion"] = redact_secrets(d.get("suggestion") or "")
        return d


def est_tokens(n_bytes_or_chars: int) -> int:
    return max(0, int(round(n_bytes_or_chars / CHARS_PER_TOKEN)))


def is_secret_path(path: Path | str) -> bool:
    p = Path(path)
    name = p.name
    if name in SECRET_BASENAMES:
        return True
    if any(part.lower() == "secrets" for part in p.parts):
        return True
    s = str(p)
    return any(frag in s for frag in SECRET_PATH_FRAGMENTS)


def safe_read_text(path: Path, max_bytes: int = 4 * 1024 * 1024) -> Optional[str]:
    if is_secret_path(path):
        return None
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > max_bytes:
            with path.open("rb") as f:
                data = f.read(max_bytes)
            return data.decode("utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """
    YAML-ish front matter: scalars, folded/literal blocks (>-, >, |, |-),
    inline lists [a, b], and list-form keys with one '- item' per line.
    Stdlib only (no PyYAML).
    """
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, Any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        # Continuation / list items belong to prior key — handled below.
        if line[:1] in (" ", "\t") and not line.lstrip().startswith("-"):
            i += 1
            continue
        if ":" not in line.split("#")[0]:
            i += 1
            continue
        # Only treat as a new key if it starts at column 0 (or not a list dash).
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent > 0 and stripped.startswith("-"):
            i += 1
            continue
        if indent > 0:
            i += 1
            continue

        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            i += 1
            continue

        # Folded / literal block scalars
        if raw in (">", ">-", ">|", "|", "|-", "|+"):
            style = raw[0]  # > or |
            strip_final = raw.endswith("-")
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    block_lines.append("")
                    i += 1
                    continue
                if not nxt.startswith((" ", "\t")):
                    break
                # Strip one level of common indent (2 spaces typical)
                if nxt.startswith("  "):
                    block_lines.append(nxt[2:])
                elif nxt.startswith("\t"):
                    block_lines.append(nxt[1:])
                else:
                    block_lines.append(nxt.lstrip())
                i += 1
            if style == ">":
                # Folded: single newlines → space; blank lines keep paragraph breaks.
                value = re.sub(r"[ \t]*\n[ \t]*\n[ \t]*", "\n\n", "\n".join(block_lines))
                value = re.sub(r"(?<!\n)\n(?!\n)", " ", value)
                value = re.sub(r"[ \t]+", " ", value).strip()
            else:
                value = "\n".join(block_lines)
                if strip_final:
                    value = value.rstrip("\n")
            meta[key] = value
            continue

        # List-form: key:  followed by indented "- item" lines
        if raw == "" or raw == "[]":
            peek = i + 1
            list_items: list[str] = []
            while peek < len(lines):
                nxt = lines[peek]
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    peek += 1
                    continue
                ns = nxt.lstrip()
                nindent = len(nxt) - len(ns)
                if nindent == 0:
                    break
                if ns.startswith("-"):
                    item = ns[1:].strip().strip("\"'")
                    list_items.append(item)
                    peek += 1
                    continue
                break
            if list_items or raw == "[]":
                meta[key] = list_items
                i = peek
                continue

        if raw.lower() in ("true", "yes"):
            meta[key] = True
        elif raw.lower() in ("false", "no"):
            meta[key] = False
        elif raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            if not inner:
                meta[key] = []
            else:
                meta[key] = [x.strip().strip("\"'") for x in inner.split(",")]
        elif (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            meta[key] = raw[1:-1]
        else:
            meta[key] = raw
        i += 1

    body = text[m.end() :]
    return meta, body


def redact_json_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in SECRET_JSON_KEYS:
                out[k] = "<redacted>"
            else:
                out[k] = redact_json_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_json_obj(x) for x in obj]
    return obj


def load_json_safe(path: Path) -> Optional[Any]:
    if is_secret_path(path):
        return None
    text = safe_read_text(path)
    if text is None:
        return None
    try:
        return redact_json_obj(json.loads(text))
    except json.JSONDecodeError:
        return None


def normalize_fact_text(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    return t


def fact_hash(text: str) -> str:
    return hashlib.sha256(normalize_fact_text(text).encode()).hexdigest()[:16]


def similarity(a: str, b: str) -> float:
    wa = set(normalize_fact_text(a).split())
    wb = set(normalize_fact_text(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def parse_iso_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def walk_files(root: Path, patterns: tuple[str, ...] = ("*",)) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for pat in patterns:
        for p in root.rglob(pat):
            if p.is_file() and not is_secret_path(p):
                out.append(p)
    return sorted(set(out))


def mcp_server_names(config: Any) -> list[str]:
    if not isinstance(config, dict):
        return []
    servers = config.get("mcpServers") or config.get("mcp") or {}
    if isinstance(servers, dict):
        return sorted(servers.keys())
    return []


def skill_description_chars(text: str) -> tuple[int, dict[str, Any], str]:
    meta, body = parse_front_matter(text)
    desc = str(meta.get("description") or "")
    return len(desc.encode("utf-8")), meta, body
