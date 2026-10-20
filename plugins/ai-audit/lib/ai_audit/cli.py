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
from .report import context_markdown, to_json


def _emit(payload: dict[str, Any], md: str, args: argparse.Namespace) -> int:
    # Redact every user-sourced string leaf before any output.
    payload = redact_tree(payload)
    json_text = to_json(payload)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json_text, encoding="utf-8")
        (out / "report.md").write_text(md, encoding="utf-8")
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

    if args.cmd == "detect":
        payload = {"audit": "detect", "version": __version__, **roots.to_dict()}
        return _emit(payload, f"# Detect\n\nHost: **{roots.host}**\n", args)

    if args.cmd == "context":
        payload = run_context_audit(roots)
        return _emit(payload, context_markdown(payload), args)

    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
