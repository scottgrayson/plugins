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

# ---------------------------------------------------------------------------
# Redaction: assignment scanner (name classify + value classify) + token pass
# ---------------------------------------------------------------------------

# Exact secret-ish names (normalized: lower, - → _). No bare "key" / primary_key.
_SECRET_NAME_EXACT = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "passphrase",
        "pwd",
        "pw",
        "token",
        "secret",
        "api_key",
        "apikey",
        "access_key",
        "access_token",
        "private_key",
        "credentials",
        "credential",
        "x_api_key",
        "db_pass",
        "mysql_pwd",
        "auth",
        "authorization",
        "session",
        "cookie",
    }
)
_SECRET_NAME_SUFFIX_RE = re.compile(
    r"(?i)(?:PASSWORD|PASSWD|TOKEN|SECRET|_API_KEY|_SECRET_KEY|_ACCESS_KEY|"
    r"_ACCESS_TOKEN|_PRIVATE_KEY|_PWD|_PASS)$"
)
# Non-secret even if suffix matches TOKEN/SECRET (config knobs / UI counters).
_NONSECRET_NAME_RE = re.compile(
    r"(?i)^(?:MAX|MIN|LIMIT|DEFAULT)_(?:TOKEN|SECRET|PASSWORD|PASS|PWD)$"
)

# Values after '=' may include URL colons; values after ':' must not swallow nested 'name:'
_VALUE_EQ = (
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\$\{[^}]+\}|<[^>\n]+>|[^\s,;&]+)"
)
_VALUE_COLON = (
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\$\{[^}]+\}|<[^>\n]+>|"
    r"https?://\S+|[^\s,;&=:]+)"
)
# name= / name: starters (value parsed separately so nested assignments are found)
_ASSIGN_EQ_START_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
)
_ASSIGN_COLON_START_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])([\"']?)([A-Za-z_][A-Za-z0-9_-]*)\1\s*:\s*"
)
# --name value  /  --name=value
_ASSIGN_FLAG_RE = re.compile(
    rf"(?i)(--([A-Za-z][A-Za-z0-9_-]*))(?:(=|\s+)({_VALUE_EQ}))"
)
# URL query ?name=value &name=value
_ASSIGN_QUERY_RE = re.compile(
    rf"(?i)([?&])([A-Za-z_][A-Za-z0-9_-]*)=({_VALUE_EQ})"
)
_VALUE_PARSE_EQ_RE = re.compile(_VALUE_EQ)
_VALUE_PARSE_COLON_RE = re.compile(_VALUE_COLON)

_URL_USERINFO_RE = re.compile(
    r"(?i)\b((?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|ftp)://)"
    r"([^/\s:@]*):([^@/\s]+)@"
)
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
_BEARER_BASIC_RE = re.compile(
    r"(?i)\b(Bearer|Basic)\s+([A-Za-z0-9_\-\.=+/]{4,})"
)

_SECRET_TOKEN_RES = (
    (re.compile(r"(?i)\bsk-ant-[A-Za-z0-9_\-]{8,}\b"), "sk-ant-…REDACTED"),
    (re.compile(r"(?i)\bsk-proj-[A-Za-z0-9_\-]{16,}\b"), "sk-proj-…REDACTED"),
    (re.compile(r"(?i)\bsk-or-[A-Za-z0-9_\-]{16,}\b"), "sk-or-…REDACTED"),
    (re.compile(r"(?i)\bsk_live_[A-Za-z0-9]{8,}\b"), "sk_live_…REDACTED"),
    (re.compile(r"(?i)\brk_live_[A-Za-z0-9]{8,}\b"), "rk_live_…REDACTED"),
    (re.compile(r"(?i)\bsk_test_[A-Za-z0-9]{8,}\b"), "sk_test_…REDACTED"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-…REDACTED"),
    (re.compile(r"(?i)\bxai-[A-Za-z0-9_\-]{16,}\b"), "xai-…REDACTED"),
    (re.compile(r"(?i)\b(ghp_|gho_|ghs_|github_pat_|glpat-)[A-Za-z0-9_\-]{8,}\b"), "gh-…REDACTED"),
    (re.compile(r"(?i)\bhf_[A-Za-z0-9]{10,}\b"), "hf_…REDACTED"),
    (re.compile(r"(?i)\bnpm_[A-Za-z0-9]{10,}\b"), "npm_…REDACTED"),
    (re.compile(r"(?i)\bwhsec_[A-Za-z0-9_\-]{8,}\b"), "whsec_…REDACTED"),
    (re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9_\-]{10,}\b"), "xox-…REDACTED"),
    (re.compile(r"(?i)\bAIza[A-Za-z0-9_\-]{20,}\b"), "AIza-…REDACTED"),
    (re.compile(r"(?i)\bAKIA[A-Z0-9]{12,}\b"), "AKIA-…REDACTED"),
    (re.compile(r"(?i)\bcrsr_[A-Za-z0-9_\-]{8,}\b"), "crsr_…REDACTED"),
    (
        re.compile(
            r"(?i)https://hooks\.slack\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]+"
        ),
        "https://hooks.slack.com/services/…REDACTED",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+=*(?:\.[A-Za-z0-9_\-]+=*){2,}\b"),
        "eyJ…REDACTED",
    ),
)
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
_PATH_WORD_RE = re.compile(
    r"(?i)(?:home|users|tmp|var|opt|workspace|sand-data|agent-data|"
    r"references|workflows|plugins|claude|cursor|packages|node_modules)"
)
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

_PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)^(?:\$\{[^}]+\}$|\$[A-Za-z_][A-Za-z0-9_]*$|<[^>]+>$|\*{3,}$|x{3,}$|changeme)$"
)
_MODEL_NAME_RE = re.compile(r"(?i)^(?:claude-|gpt-|grok-|gemini-|o1|o3)")
_BOOLEAN_VALUE_RE = re.compile(
    r"(?i)^(?:true|false|yes|no|on|off|enabled|disabled)$"
)
_NULL_VALUE_RE = re.compile(r"(?i)^(?:null|none|nil|undefined)$")
_NUMBER_VALUE_RE = re.compile(r"^\d+$")
_VERSION_VALUE_RE = re.compile(r"^v?\d+(?:\.\d+)*$")
# Requirement-style atoms ("8+", "12+") — instructional, not secrets.
_REQUIREMENT_VALUE_RE = re.compile(r"^\d+\+$")
_PATH_VALUE_RE = re.compile(r"^(?:~|/|file://|[A-Za-z]:\\)")
_UUID_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_GIT_SHA_RE = re.compile(r"(?i)^[0-9a-f]{7,40}$")
_KNOWN_SECRET_PREFIX_RE = re.compile(
    r"(?i)^(?:sk[-_]|rk[-_]|ghp_|gho_|ghs_|github_pat_|glpat-|npm_|whsec_|hf_|"
    r"xox[baprs]-|AIza|AKIA|crsr_|xai-|eyJ)"
)


def _strip_value_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _norm_name(name: str) -> str:
    return name.lower().replace("-", "_")


def is_secret_name(name: str) -> bool:
    """Classify assignment/header/flag name as secret-ish."""
    if not name:
        return False
    if _NONSECRET_NAME_RE.match(name):
        return False
    n = _norm_name(name)
    if n in _SECRET_NAME_EXACT:
        return True
    if _SECRET_NAME_SUFFIX_RE.search(name):
        # Reject bare *_key (primary_key, sort_key, cache_key, public_key, …)
        if re.search(r"(?i)_key$", name) and not re.search(
            r"(?i)(?:api_key|secret_key|access_key|private_key)$", name
        ):
            return False
        return True
    return False


def classify_value(value: str) -> str:
    """
    Classify a raw value atom.
    Returns one of: empty, placeholder, boolean, null, number, version, path,
    uuid, gitsha, model, prose, secretlike.
    """
    raw = (value or "").strip()
    if not raw:
        return "empty"
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"')
    v = _strip_value_quotes(raw).strip()
    if not v:
        return "empty"
    if _PLACEHOLDER_VALUE_RE.match(raw) or _PLACEHOLDER_VALUE_RE.match(v):
        return "placeholder"
    if _BOOLEAN_VALUE_RE.match(v):
        return "boolean"
    if _NULL_VALUE_RE.match(v):
        return "null"
    if _NUMBER_VALUE_RE.match(v):
        return "number"
    if _VERSION_VALUE_RE.match(v):
        return "version"
    if _REQUIREMENT_VALUE_RE.match(v):
        return "prose"
    if _PATH_VALUE_RE.match(v) or (v.startswith("/") and "/" in v[1:]):
        return "path"
    if _UUID_RE.match(v):
        return "uuid"
    if _GIT_SHA_RE.match(v) and not re.search(r"[/+=]", v):
        return "gitsha"
    if _MODEL_NAME_RE.match(v):
        return "model"
    if _KNOWN_SECRET_PREFIX_RE.match(v):
        return "secretlike"
    # Unquoted multi-word → instructional prose ("8+ chars required", "none needed")
    if (" " in v or "\t" in v) and not quoted:
        return "prose"
    if quoted:
        # Quoted non-placeholder content is treated as a secret payload
        return "secretlike"
    # Letter-only tokens: Titlecase / short lowercase English → prose;
    # long lowercase or camelCase → secretlike (ghfake, FakeTok).
    if re.fullmatch(r"[A-Za-z][A-Za-z\-]*", v):
        if v[0].isupper() and v[1:].islower():
            return "prose"  # Composer, Hunter (titlecase English)
        if v.isupper() and len(v) <= 6:
            return "prose"
        if v.islower() and len(v) < 6:
            return "prose"
        return "secretlike"
    return "secretlike"


# uuid/gitsha are NOT safe here: hex-looking secrets (abc123def) must still redact.
_SAFE_VALUE_KINDS = frozenset(
    {
        "empty",
        "placeholder",
        "boolean",
        "null",
        "number",
        "version",
        "path",
        "model",
        "prose",
    }
)


def should_redact_assignment(name: str, value: str) -> bool:
    """Single decision: secret-ish name + non-safe value → redact."""
    if not is_secret_name(name):
        return False
    return classify_value(value) not in _SAFE_VALUE_KINDS


def looks_like_secret_value(value: str) -> bool:
    """Public helper: value would be redacted if paired with a secret-ish name."""
    return classify_value(value) == "secretlike"


def _auth_token_looks_credential(tok: str) -> bool:
    if not tok:
        return False
    low = tok.lower()
    if low in {
        "authentication",
        "auth",
        "token",
        "header",
        "scheme",
        "of",
        "setup",
        "for",
        "the",
        "api",
        "is",
        "disabled",
        "enabled",
        "bad",
        "news",
    }:
        return False
    kind = classify_value(tok)
    if kind in _SAFE_VALUE_KINDS:
        return False
    # Long opaque tokens (incl. lowercase 16+ with no digit)
    if len(tok) >= 16 and re.fullmatch(r"[A-Za-z0-9_\-\.=+/]+", tok):
        return True
    if re.search(r"\d", tok) or re.search(r"[+/=]", tok):
        return True
    if _KNOWN_SECRET_PREFIX_RE.match(tok) or tok.startswith("eyJ"):
        return True
    return False


def _aws_candidate_is_pathlike(secret: str, full: str, start: int) -> bool:
    if secret.startswith("/"):
        return True
    after = full[start + len(secret) : start + len(secret) + 12]
    if after.startswith((".md", ".py", ".json", ".txt", ".yaml", ".yml", ".toml")):
        return True
    before = full[max(0, start - 32) : start]
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
        if "/" not in secret and "+" not in secret:
            return secret
        before = text[max(0, m.start() - 100) : m.start()]
        if _AWS_CONTEXT_BEFORE_RE.search(before):
            return "…REDACTED"
        return secret

    return _AWS_SECRET_RE.sub(repl, text)


@dataclass(frozen=True)
class _AssignHit:
    start: int
    end: int
    name: str
    value_start: int
    value_end: int
    value: str
    prefix: str  # text between name and value to keep (e.g. "=", ": ", "--password ")


def _parse_value_at(
    text: str, pos: int, *, colon: bool = False
) -> tuple[str, int, int] | None:
    """Parse a value atom at pos → (value, value_start, value_end) or None."""
    rx = _VALUE_PARSE_COLON_RE if colon else _VALUE_PARSE_EQ_RE
    m = rx.match(text, pos)
    if not m:
        return None
    return m.group(0), m.start(), m.end()


def _collect_assignments(text: str) -> list[_AssignHit]:
    """Find every name=value / name: value / --name value / ?name=value span."""
    hits: list[_AssignHit] = []

    # Every name= occurrence (including nested password= inside staging=password=x)
    for m in _ASSIGN_EQ_START_RE.finditer(text):
        name = m.group(1)
        parsed = _parse_value_at(text, m.end(), colon=False)
        if not parsed:
            continue
        val, vs, ve = parsed
        prefix = text[m.start() : vs]
        hits.append(_AssignHit(m.start(), ve, name, vs, ve, val, prefix))

    # Every name: occurrence (including nested password: inside A: password: x).
    # Skip when the atom is really the name of a nested name=value (Cookie: session=…).
    for m in _ASSIGN_COLON_START_RE.finditer(text):
        name = m.group(2)
        parsed = _parse_value_at(text, m.end(), colon=True)
        if not parsed:
            continue
        val, vs, ve = parsed
        if ve < len(text) and text[ve] == "=":
            continue
        prefix = text[m.start() : vs]
        hits.append(_AssignHit(m.start(), ve, name, vs, ve, val, prefix))

    for m in _ASSIGN_FLAG_RE.finditer(text):
        flag, name, sep, val = m.group(1), m.group(2), m.group(3), m.group(4)
        if val is None:
            continue
        prefix = f"{flag}{sep}"
        hits.append(
            _AssignHit(m.start(), m.end(), name, m.start(4), m.end(), val, prefix)
        )

    for m in _ASSIGN_QUERY_RE.finditer(text):
        amp, name, val = m.group(1), m.group(2), m.group(3)
        prefix = f"{amp}{name}="
        hits.append(
            _AssignHit(m.start(), m.end(), name, m.start(3), m.end(), val, prefix)
        )

    return hits


def _apply_assignment_redactions(text: str) -> str:
    secret_hits = [
        h for h in _collect_assignments(text) if should_redact_assignment(h.name, h.value)
    ]
    if not secret_hits:
        return text
    # Prefer innermost: later-starting / shorter spans first, then drop outer wrappers
    # and any remaining overlaps so nested name=value wins over a label prefix.
    secret_hits.sort(key=lambda h: (-h.start, h.end - h.start))
    chosen: list[_AssignHit] = []
    for h in secret_hits:
        if any(h.start <= c.start and c.end <= h.end for c in chosen):
            continue  # outer wrapper around an already-chosen inner secret
        if any(c.start <= h.start and h.end <= c.end for c in chosen):
            continue  # fully inside an already-chosen span
        if any(not (h.end <= c.start or h.start >= c.end) for c in chosen):
            continue  # partial overlap with a chosen hit
        chosen.append(h)
    chosen.sort(key=lambda h: h.start, reverse=True)
    out = text
    for h in chosen:
        out = out[: h.start] + f"{h.prefix}…REDACTED" + out[h.end :]
    return out


def redact_secrets(text: str) -> str:
    """
    Replace secret-shaped values in user-sourced strings.

    Pass 1: PEM/PGP blocks, URL userinfo.
    Pass 2: assignment scanner (name=value, name: value, --name, ?query=).
    Pass 3: Bearer/Basic + known-prefix tokens + contextual AWS secrets.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    out = text

    out = _PEM_PRIVATE_RE.sub(
        "-----BEGIN PRIVATE KEY-----…REDACTED-----END PRIVATE KEY-----", out
    )
    out = _PGP_PRIVATE_RE.sub(
        "-----BEGIN PGP PRIVATE KEY BLOCK-----…REDACTED-----END PGP PRIVATE KEY BLOCK-----",
        out,
    )
    out = _URL_USERINFO_RE.sub(r"\1\2:…REDACTED@", out)

    out = _apply_assignment_redactions(out)

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
                out[k] = v
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
