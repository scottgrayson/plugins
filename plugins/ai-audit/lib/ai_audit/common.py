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

# Secret-ish assignment names (NAME=value always redacts; label: uses digit/quoted/prefix rules).
# No bare KEY / primary_key / sort_key / cache_key (avoids Key: / *_key: prose FPs).
_SECRET_NAME_EXACT = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "passphrase",
        "pwd",
        "token",
        "secret",
        "api_key",
        "api-key",
        "access_key",
        "private_key",
        "credentials",
        "x-api-key",
        "x_api_key",
        "db_pass",
        "mysql_pwd",
    }
)
_SECRET_NAME_SUFFIX_RE = re.compile(
    r"(?i)(?:PASSWORD|PASSWD|TOKEN|SECRET|_API_KEY|_SECRET_KEY|_ACCESS_KEY|"
    r"_PRIVATE_KEY|_PWD)$"
)
# MAX_/MIN_/LIMIT_/DEFAULT_… with a purely numeric value — keep (e.g. MAX_TOKEN=4096).
_MAX_LIMIT_NAME_RE = re.compile(r"(?i)^(?:MAX|MIN|LIMIT|DEFAULT)_[A-Za-z0-9_]+$")
_PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)^(?:\$\{[^}]+\}$|\$[A-Za-z_][A-Za-z0-9_]*$|<[^>]+>$|\*{3,}$|x{3,}$|changeme)$"
)
_MODEL_NAME_RE = re.compile(r"(?i)^(?:claude-|gpt-|grok-|gemini-|o1|o3)")
# key=value / key: value — value may be single- or double-quoted (incl. spaces)
_KV_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_-]*)\s*([=:])\s*"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)"
)
# JSON "password": "…" / 'password': '…' and the same key family
_JSON_SECRET_RE = re.compile(
    r"(?i)([\"'](?:password|passwd|secret|token|api[_-]?key|"
    r"[A-Za-z_][A-Za-z0-9_]*?(?:PASSWORD|PASSWD|TOKEN|SECRET)|"
    r"[A-Za-z_][A-Za-z0-9_]*(?:_API_KEY|_SECRET_KEY|_ACCESS_KEY)|PRIVATE_KEY)[\"'])"
    r"\s*:\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
# URL user:password@host (user may be empty: redis://:pw@host)
_URL_USERINFO_RE = re.compile(
    r"(?i)\b((?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|ftp)://)"
    r"([^/\s:@]*):([^@/\s]+)@"
)
# PEM / PGP private key blocks
_PEM_PRIVATE_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
    r".*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)
_PGP_PRIVATE_RE = re.compile(
    r"-----BEGIN PGP PRIVATE KEY BLOCK-----"
    r".*?"
    r"-----END PGP PRIVATE KEY BLOCK-----",
    re.DOTALL,
)

_SECRET_TOKEN_RES = (
    # Provider API keys / tokens (entire match → label…REDACTED)
    (re.compile(r"(?i)\bsk-ant-[A-Za-z0-9_\-]{8,}\b"), "sk-ant-…REDACTED"),
    (re.compile(r"(?i)\bsk-proj-[A-Za-z0-9_\-]{16,}\b"), "sk-proj-…REDACTED"),
    (re.compile(r"(?i)\bsk-or-[A-Za-z0-9_\-]{16,}\b"), "sk-or-…REDACTED"),
    # Stripe live / test / restricted keys
    (re.compile(r"(?i)\bsk_live_[A-Za-z0-9]{8,}\b"), "sk_live_…REDACTED"),
    (re.compile(r"(?i)\brk_live_[A-Za-z0-9]{8,}\b"), "rk_live_…REDACTED"),
    (re.compile(r"(?i)\bsk_test_[A-Za-z0-9]{8,}\b"), "sk_test_…REDACTED"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-…REDACTED"),
    (re.compile(r"(?i)\bxai-[A-Za-z0-9_\-]{16,}\b"), "xai-…REDACTED"),
    (re.compile(r"(?i)\b(ghp_|gho_|github_pat_|glpat-)[A-Za-z0-9_\-]{8,}\b"), "gh-…REDACTED"),
    (re.compile(r"(?i)\bnpm_[A-Za-z0-9]{10,}\b"), "npm_…REDACTED"),
    (re.compile(r"(?i)\bwhsec_[A-Za-z0-9_\-]{8,}\b"), "whsec_…REDACTED"),
    (re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9_\-]{10,}\b"), "xox-…REDACTED"),
    (re.compile(r"(?i)\bAIza[A-Za-z0-9_\-]{20,}\b"), "AIza-…REDACTED"),
    (re.compile(r"(?i)\bAKIA[A-Z0-9]{12,}\b"), "AKIA-…REDACTED"),
    # Cursor Admin API keys
    (re.compile(r"(?i)\bcrsr_[A-Za-z0-9_\-]{8,}\b"), "crsr_…REDACTED"),
    # Slack incoming webhook URLs
    (
        re.compile(
            r"(?i)https://hooks\.slack\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+"
        ),
        "https://hooks.slack.com/services/…REDACTED",
    ),
    # Bare JWT (header.payload.sig). Bearer/Basic handled in redact_secrets with credential check.
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+=*(?:\.[A-Za-z0-9_\-]+=*){2,}\b"),
        "eyJ…REDACTED",
    ),
)
_BEARER_BASIC_RE = re.compile(
    r"(?i)\b(Bearer|Basic)\s+([A-Za-z0-9_\-\.=+/]{4,})"
)
# Bare 40-char AWS secret access key shape (base64-ish). Only applied with context.
_AWS_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9/+=])([A-Za-z0-9+/]{40})(?![A-Za-z0-9/+=])"
)
_AWS_CONTEXT_BEFORE_RE = re.compile(
    r"(?i)(?:"
    r"(?:aws[_-]?secret(?:_access)?[_-]?key|secret[_-]?access[_-]?key|"
    r"aws[_-]?secret|secret)\s*[=:\s]\s*$|"
    r"AKIA[A-Z0-9]{12,}\s+\S{0,60}\s*$|"
    r"\b(?:aws\s+)?(?:secret(?:\s+access\s+key)?|key)\s+$|"
    r"\baws\s+$"
    r")"
)

# Keys whose string values are filesystem paths — never value-redact.
_PATH_FIELD_KEYS = frozenset(
    {
        "path",
        "home",
        "project",
        "sand_data",
        "claude_home",
        "cursor_home",
        "installPath",
        "projectPath",
    }
)


def _strip_value_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


_KNOWN_SECRET_PREFIX_RE = re.compile(
    r"(?i)^(?:sk[-_]|rk[-_]|ghp_|gho_|github_pat_|glpat-|npm_|whsec_|"
    r"xox[baprs]-|AIza|AKIA|crsr_|xai-|eyJ)"
)


def _is_placeholder_value(value: str) -> bool:
    v = _strip_value_quotes(value).strip()
    if not v:
        return True
    return bool(_PLACEHOLDER_VALUE_RE.match(v))


def _is_secret_assignment_name(name: str) -> bool:
    """True for password/token/secret/api_key/… names (not bare key / primary_key)."""
    n = name.lower().replace("-", "_")
    if n in _SECRET_NAME_EXACT:
        return True
    # Hyphenated header form
    if name.lower() in _SECRET_NAME_EXACT:
        return True
    if _SECRET_NAME_SUFFIX_RE.search(name):
        # Reject bare KEY / *_key that are not api/secret/access/private
        if re.search(r"(?i)_key$", name) and not re.search(
            r"(?i)(?:api_key|secret_key|access_key|private_key)$", name
        ):
            return False
        return True
    return False


def _auth_token_looks_credential(tok: str) -> bool:
    """Bearer/Basic follow-on: base64-ish, has a digit, 16+ mixed, or known prefix."""
    if not tok or tok.lower() in {"authentication", "auth", "token", "header", "scheme"}:
        return False
    if _KNOWN_SECRET_PREFIX_RE.match(tok) or tok.startswith("eyJ"):
        return True
    if re.search(r"\d", tok):
        return True
    if re.search(r"[+/=]", tok):  # base64 alphabet extras
        return True
    if len(tok) >= 16 and re.search(r"[A-Za-z]", tok) and re.search(r"[0-9]", tok):
        return True
    # Long base64-looking (mixed case) without requiring a digit
    if (
        len(tok) >= 16
        and re.fullmatch(r"[A-Za-z0-9+/=]+", tok)
        and re.search(r"[A-Z]", tok)
        and re.search(r"[a-z]", tok)
    ):
        return True
    return False


def looks_like_secret_value(value: str) -> bool:
    """Label-form value: no spaces + (digit or quoted or known prefix); not model ids."""
    raw = (value or "").strip()
    if not raw or _is_placeholder_value(raw):
        return False
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"')
    v = _strip_value_quotes(raw).strip()
    if not v:
        return False
    if _MODEL_NAME_RE.match(v):
        return False
    if quoted:
        return True
    if " " in v or "\t" in v:
        return False
    if _KNOWN_SECRET_PREFIX_RE.match(v):
        return True
    if re.search(r"\d", v):
        return True
    return False


def _kv_should_redact(name: str, sep: str, value: str) -> bool:
    if not _is_secret_assignment_name(name):
        return False
    # MAX_TOKEN=4096 / LIMIT_*=N — keep purely numeric config knobs
    if _MAX_LIMIT_NAME_RE.match(name) and _strip_value_quotes(value).strip().isdigit():
        return False
    if sep == "=":
        # NAME=value: always redact unless empty / placeholder
        return not _is_placeholder_value(value)
    # label: value — digit / quoted / known prefix; plain English stays
    return looks_like_secret_value(value)


_PATH_WORD_RE = re.compile(
    r"(?i)(?:home|users|tmp|var|opt|workspace|sand-data|agent-data|"
    r"references|workflows|plugins|claude|cursor|packages|node_modules)"
)


def _aws_candidate_is_pathlike(secret: str, full: str, start: int) -> bool:
    """True when a 40-char match is (or sits inside) a filesystem path."""
    # Absolute path fragment: match begins with /
    if secret.startswith("/"):
        return True
    after = full[start + len(secret) : start + len(secret) + 12]
    if after.startswith((".md", ".py", ".json", ".txt", ".yaml", ".yml", ".toml")):
        return True
    before = full[max(0, start - 32) : start]
    # Readable path words inside the match (AWS keys are opaque base64)
    if "/" in secret and _PATH_WORD_RE.search(secret):
        return True
    window = before + secret[:24]
    if re.search(r"(?i)/(?:home|Users|tmp|var|etc|opt|workspace|mnt)(?:/|$)", window):
        return True
    return False


def _redact_aws_bare(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        secret = m.group(1)
        if _aws_candidate_is_pathlike(secret, text, m.start()):
            return secret
        # AWS secrets almost always include / or +; skip plain hex/alnum runs
        if "/" not in secret and "+" not in secret:
            return secret
        before = text[max(0, m.start() - 100) : m.start()]
        if _AWS_CONTEXT_BEFORE_RE.search(before):
            return "…REDACTED"
        return secret

    return _AWS_SECRET_RE.sub(repl, text)


def redact_secrets(text: str) -> str:
    """Replace secret-shaped values in user-sourced strings. Safe on non-strings via str()."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    out = text

    # PEM / PGP private keys first (multi-line)
    out = _PEM_PRIVATE_RE.sub("-----BEGIN PRIVATE KEY-----…REDACTED-----END PRIVATE KEY-----", out)
    out = _PGP_PRIVATE_RE.sub(
        "-----BEGIN PGP PRIVATE KEY BLOCK-----…REDACTED-----END PGP PRIVATE KEY BLOCK-----",
        out,
    )

    # URL credentials: keep scheme+user (user may be empty), redact password
    out = _URL_USERINFO_RE.sub(r"\1\2:…REDACTED@", out)

    # JSON "key": "value" / 'key': 'value'
    def _json_sub(m: re.Match[str]) -> str:
        key = m.group(1)
        q = '"' if key.startswith('"') else "'"
        return f"{key}: {q}…REDACTED{q}"

    out = _JSON_SECRET_RE.sub(_json_sub, out)

    # key=value (always for secret names) / key: value (digit/quoted/prefix)
    def _kv_sub(m: re.Match[str]) -> str:
        name, sep, value = m.group(1), m.group(2), m.group(3)
        if _kv_should_redact(name, sep, value):
            return f"{name}{sep}…REDACTED"
        return m.group(0)

    out = _KV_ASSIGN_RE.sub(_kv_sub, out)

    # Bearer/Basic only when the follow-on token looks like a credential
    def _auth_sub(m: re.Match[str]) -> str:
        kind, tok = m.group(1), m.group(2)
        if _auth_token_looks_credential(tok):
            return f"{kind} …REDACTED"
        return m.group(0)

    out = _BEARER_BASIC_RE.sub(_auth_sub, out)

    for rx, repl in _SECRET_TOKEN_RES:
        out = rx.sub(repl, out)

    out = _redact_aws_bare(out)
    return out


def redact_tree(obj: Any) -> Any:
    """Deep-copy-ish walk: redact every string leaf except filesystem path fields."""
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, list):
        return [redact_tree(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _PATH_FIELD_KEYS and isinstance(v, str):
                out[k] = v  # never rewrite inventory / root paths
            else:
                out[k] = redact_tree(v)
        return out
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
