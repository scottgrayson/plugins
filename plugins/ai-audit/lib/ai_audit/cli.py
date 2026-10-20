"""ai-audit CLI: detect | context (stage 1 — startup context only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .common import redact_tree
from .context_audit import run_context_audit
from .detect import detect_host
from .environment import detect_environment
from .report import context_markdown, to_json


def _emit(payload: dict[str, Any], args: argparse.Namespace) -> int:
    """Redact once at the data layer, then render JSON and markdown from that."""
    payload = redact_tree(payload)
    if payload.get("audit") == "detect":
        env = (payload.get("environment") or {}).get("heading") or "?"
        host = payload.get("host") or "?"
        md = f"# Detect\n\nHost: **{host}**\n\nEnvironment: **{env}**\n"
        payload.pop("detect_markdown", None)
    else:
        md = context_markdown(payload)
    # to_json also redacts; payload is already clean — safe either way.
    json_text = to_json(payload)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json_text, encoding="utf-8")
        (out / "report.md").write_text(
            md if md.endswith("\n") else md + "\n", encoding="utf-8"
        )
    if args.json_only:
        sys.stdout.write(json_text)
    elif args.md_only:
        sys.stdout.write(md if md.endswith("\n") else md + "\n")
    else:
        sys.stdout.write(md if md.endswith("\n") else md + "\n")
        sys.stdout.write("\n---\n\n")
        sys.stdout.write(json_text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-audit",
        description=(
            "Read-only startup context audit "
            "(never writes; redacts common secret formats)."
        ),
    )
    p.add_argument("--version", action="version", version=f"ai-audit {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--host",
            default="auto",
            choices=["auto", "claude", "cursor", "grok"],
            help="Force host or auto-detect (default: auto)",
        )
        sp.add_argument("--home", default=None, help="Override $HOME")
        sp.add_argument("--project", default=None, help="Project directory")
        sp.add_argument(
            "--root",
            default=None,
            help="Fixture/root dir containing home/, project/, and/or sand-data/ "
            "(fully isolates detection — no /home/box fallback)",
        )
        sp.add_argument(
            "--env",
            default=None,
            dest="env_override",
            help=(
                "Override environment half (Cloud|Mac|Linux|Windows) or a full "
                "heading like 'Cursor · Cloud'"
            ),
        )
        sp.add_argument(
            "--context-window",
            default="200k",
            choices=["200k", "1m"],
            help="Active context window for skill-row sorting (default: 200k)",
        )
        sp.add_argument("--json-only", action="store_true")
        sp.add_argument("--md-only", action="store_true")
        sp.add_argument("--out-dir", default=None, help="Write report.json and report.md here")

    d = sub.add_parser("detect", help="Detect host and roots")
    add_common(d)

    c = sub.add_parser("context", help="Inventory what agents load into context")
    add_common(c)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    roots = detect_host(
        host=args.host,
        home=Path(args.home) if args.home else None,
        project=Path(args.project) if args.project else None,
        root=Path(args.root) if args.root else None,
    )

    env_info = detect_environment(
        roots.host,
        override=getattr(args, "env_override", None),
        sand_data=roots.sand_data,
    )

    if args.cmd == "detect":
        payload = {
            "audit": "detect",
            "version": __version__,
            "environment": env_info,
            "provider": env_info.get("provider"),
            "host": roots.host,
            **roots.to_dict(),
        }
        return _emit(payload, args)

    if args.cmd == "context":
        try:
            payload = run_context_audit(
                roots,
                env_override=getattr(args, "env_override", None),
                context_window=getattr(args, "context_window", "200k"),
                env_info=env_info,
            )
        except RuntimeError as exc:
            # D3: one-line install hint — never dump a traceback for missing tiktoken.
            msg = str(exc).strip() or "ai-audit failed"
            if "tiktoken" in msg.lower() or "tokenizer" in msg.lower():
                sys.stderr.write(
                    "ai-audit: install the offline tokenizer with: "
                    "python3 -m pip install --user 'tiktoken==0.14.0'\n"
                )
                return 1
            sys.stderr.write(f"ai-audit: {msg}\n")
            return 1
        return _emit(payload, args)

    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
