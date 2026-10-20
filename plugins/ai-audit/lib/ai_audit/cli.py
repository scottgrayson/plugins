"""ai-audit CLI: detect | context | token | facts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .context_audit import run_context_audit
from .detect import detect_host
from .facts_audit import run_facts_audit
from .report import context_markdown, facts_markdown, to_json, token_markdown
from .token_audit import run_token_audit


def _emit(payload: dict[str, Any], md: str, args: argparse.Namespace) -> int:
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


def _load_routines(path: Optional[str]) -> Optional[list]:
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("routines"), list):
        return data["routines"]
    raise SystemExit(f"--routines-json must be a list or {{routines: [...]}}: {path}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ai-audit",
        description="Read-only context / token / facts audits (never writes; never prints secrets).",
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

    t = sub.add_parser("token", help="Attribute token usage from official sources or a usage file")
    add_common(t)
    t.add_argument("--csv", default=None, help="Cursor dashboard Usage CSV path")
    t.add_argument(
        "--usage-file",
        default=None,
        help="User-downloaded usage file (CSV or JSON) — fallback for every host",
    )
    t.add_argument(
        "--cursor-admin-api",
        action="store_true",
        help="Request Cursor Admin API (also used automatically when CURSOR_ADMIN_API_KEY is set).",
    )
    t.add_argument("--admin-start", default=None, help="Admin API startDate (ISO)")
    t.add_argument("--admin-end", default=None, help="Admin API endDate (ISO)")
    t.add_argument(
        "--usage-total",
        type=float,
        default=None,
        help="Pasted Usage page total for Grok Bot when no usage file is available",
    )
    t.add_argument(
        "--usage-period",
        default=None,
        help="Label for --usage-total (e.g. 'week of 2026-10-13')",
    )
    t.add_argument(
        "--routines-json",
        default=None,
        help="Optional JSON file of routines [{name, cron}] (informational)",
    )

    f = sub.add_parser("facts", help="Audit memory/facts for stale/duplicate/conflict/orphan")
    add_common(f)
    f.add_argument("--stale-days", type=int, default=30)
    f.add_argument("--memories", default=None, help="Cursor exported memories .mdc")

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

    if args.cmd == "token":
        payload = run_token_audit(
            roots,
            csv_path=Path(args.csv) if args.csv else None,
            use_admin_api=bool(args.cursor_admin_api),
            admin_start=args.admin_start,
            admin_end=args.admin_end,
            usage_total=args.usage_total,
            usage_period=args.usage_period,
            routines=_load_routines(args.routines_json),
            usage_file=Path(args.usage_file) if args.usage_file else None,
        )
        return _emit(payload, token_markdown(payload), args)

    if args.cmd == "facts":
        payload = run_facts_audit(
            roots,
            stale_days=args.stale_days,
            memories_path=Path(args.memories) if args.memories else None,
        )
        return _emit(payload, facts_markdown(payload), args)

    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
