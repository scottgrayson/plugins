"""Offline tiktoken o200k_base tokenizer (vendored ranks; no network)."""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Optional

TOKENIZER_NAME = "tiktoken o200k_base"
_VENDOR_FILE = "o200k_base.tiktoken"
_EXPECTED_HASH = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"

# Same pattern string as tiktoken_ext.openai_public.o200k_base.
_O200K_PAT = "|".join(
    [
        r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
        r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
        r"""\p{N}{1,3}""",
        r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
        r"""\s*[\r\n]+""",
        r"""\s+(?!\S)""",
        r"""\s+""",
    ]
)


def _vendor_path() -> Path:
    # lib/ai_audit/tokenize.py → plugins/ai-audit/vendor/
    return Path(__file__).resolve().parents[2] / "vendor" / _VENDOR_FILE


def _load_ranks(path: Path) -> dict[bytes, int]:
    ranks: dict[bytes, int] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            token_b64, rank_s = line.split(" ")
            ranks[base64.b64decode(token_b64)] = int(rank_s)
    return ranks


@lru_cache(maxsize=1)
def get_encoding():
    """Return a tiktoken Encoding built from the vendored o200k_base file."""
    try:
        from tiktoken.core import Encoding
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ai-audit requires the tiktoken package for offline o200k token counts. "
            "Install with: pip install tiktoken"
        ) from exc

    path = _vendor_path()
    if not path.is_file():
        raise RuntimeError(f"vendored tokenizer missing: {path}")
    # Integrity check (sha256 of file bytes).
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != _EXPECTED_HASH:
        raise RuntimeError(
            f"vendored o200k_base.tiktoken hash mismatch: {digest} != {_EXPECTED_HASH}"
        )
    ranks = _load_ranks(path)
    return Encoding(
        name="o200k_base",
        pat_str=_O200K_PAT,
        mergeable_ranks=ranks,
        special_tokens={"<|endoftext|>": 199999, "<|endofprompt|>": 200018},
    )


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(get_encoding().encode(text))


def tokenizer_label() -> str:
    return TOKENIZER_NAME
