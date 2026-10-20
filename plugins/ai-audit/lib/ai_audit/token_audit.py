"""Token / cost collectors. Cloud first, local second. Never scrape login tokens."""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .common import (
    Win,
    est_tokens,
    is_secret_path,
    load_json_safe,
    redact_secrets,
    safe_read_text,
)
from .detect import Roots


def _norm_header(h: str) -> str:
    """Case-insensitive, punctuation-tolerant header key."""
    s = (h or "").strip().lower()
    s = s.replace("(w/", "with_").replace("(w/o", "without_")
    s = re.sub(r"[^\w]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _num(row: dict[str, str], *keys: str) -> float:
    for k in keys:
        nk = _norm_header(k) if " " in k or "(" in k else k
        # Also try already-normalized
        for candidate in (k, nk, _norm_header(k)):
            if candidate in row and row[candidate] not in (None, ""):
                try:
                    return float(str(row[candidate]).replace(",", "").replace("$", ""))
                except ValueError:
                    continue
    return 0.0


def _row_get(row: dict[str, str], *keys: str) -> str:
    for k in keys:
        for candidate in (k, _norm_header(k)):
            if candidate in row and row[candidate]:
                return str(row[candidate]).strip()
    return ""


def _classify_kind(kind_raw: str, row: dict[str, str]) -> str:
    """Map Kind / source / ID columns to cloud_agent | automation | local."""
    blob = " ".join(
        [
            kind_raw,
            _row_get(row, "source", "type", "event_type"),
            "cloud" if _row_get(row, "cloud_agent_id", "cloudagentid", "agent_id") else "",
            "auto" if _row_get(row, "automation_id", "automationid") else "",
        ]
    ).lower()
    if any(x in blob for x in ("cloud", "headless", "background")):
        return "cloud_agent"
    if any(x in blob for x in ("automation", "routine", "cron")):
        return "automation"
    if any(x in blob for x in ("ide", "local", "composer", "chat", "agent")):
        # Prefer explicit local/IDE; bare "agent" without cloud → local
        if "cloud" not in blob and "automation" not in blob:
            if any(x in blob for x in ("ide", "local", "composer", "chat")):
                return "local"
    if _row_get(row, "cloud_agent_id", "cloudagentid"):
        return "cloud_agent"
    if _row_get(row, "automation_id", "automationid"):
        return "automation"
    # Kind column values seen in dashboard exports
    k = kind_raw.lower()
    if k in ("cloud agent", "cloud_agent", "cloud-agent", "cloud"):
        return "cloud_agent"
    if k in ("automation", "automations"):
        return "automation"
    if k in ("", "included", "on-demand", "on_demand"):
        # Fall through — still classify via IDs above; else local/IDE default for remaining
        pass
    return "local"



def _to_epoch_ms(value: Optional[str]) -> Optional[int]:
    """Convert ISO date/datetime or numeric string to epoch milliseconds (Admin API)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        # Heuristic: seconds vs ms
        return n if n > 10_000_000_000 else n * 1000
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            dt = datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def parse_cursor_usage_csv(path: Path) -> dict[str, Any]:
    """
    Parse a Cursor dashboard Usage CSV export.

    Supports real dashboard headers such as:
      Date, Kind, Model, Max Mode, Input (w/ Cache Write), Input (w/o Cache Write),
      Cache Read, Output, Total Tokens, Cost, Cloud Agent ID, Automation ID, …
    """
    text = safe_read_text(path)
    if text is None:
        return {"error": f"cannot read CSV: {path}", "events": [], "by_model": {}}

    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.DictReader(text.splitlines())
    events: list[dict[str, Any]] = []
    empty = lambda: {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
        "total_tokens": 0.0,
        "cost": 0.0,
        "count": 0.0,
    }
    by_model: dict[str, dict[str, float]] = defaultdict(empty)
    by_kind: dict[str, dict[str, float]] = defaultdict(empty)
    by_max_mode: dict[str, dict[str, float]] = defaultdict(empty)
    by_model_kind: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for raw in reader:
        row = {_norm_header(k or ""): (v or "").strip() for k, v in raw.items()}
        model = _row_get(row, "model", "model_name", "llm") or "unknown"
        kind_raw = _row_get(row, "kind", "source", "type")
        kind = _classify_kind(kind_raw, row)
        max_mode_raw = _row_get(row, "max_mode", "maxmode")
        max_mode = (
            "on"
            if max_mode_raw.lower() in ("true", "1", "yes", "on")
            else ("off" if max_mode_raw else "unknown")
        )

        # Real dashboard: Input (w/ Cache Write) + Input (w/o Cache Write)
        inp_with = _num(
            row,
            "input_with_cache_write",
            "input_w_cache_write",
            "input_(w/_cache_write)",
        )
        inp_without = _num(
            row,
            "input_without_cache_write",
            "input_w_o_cache_write",
            "input_(w/o_cache_write)",
            "input_wo_cache_write",
        )
        # Normalized forms after _norm_header on "Input (w/ Cache Write)"
        if not inp_with:
            inp_with = _num(row, "input_with_cache_write")
        if not inp_without:
            inp_without = float(row.get("input_without_cache_write") or 0) or _num(
                row, "input_without_cache_write"
            )
        # Fallback aliases
        inp = inp_with + inp_without
        if inp == 0:
            inp = _num(row, "input", "input_tokens", "prompt_tokens")

        out = _num(row, "output", "output_tokens", "completion_tokens")
        cache_read = _num(row, "cache_read", "cache_read_tokens", "cached_input")
        cache_write = inp_with if inp_with else _num(
            row, "cache_write", "cache_write_tokens", "input_cache_write"
        )
        total = _num(row, "total_tokens", "total") or (
            inp + out + cache_read
        )
        cost = _num(row, "cost", "total_cost", "charged", "price")
        date = _row_get(row, "date", "timestamp", "created_at")

        ev = {
            "date": date,
            "model": model,
            "kind": kind,
            "kind_raw": kind_raw,
            "max_mode": max_mode,
            "input": inp,
            "input_with_cache_write": inp_with,
            "input_without_cache_write": inp_without,
            "output": out,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "total_tokens": total,
            "cost": cost,
            "cloud_agent_id": _row_get(row, "cloud_agent_id", "cloudagentid"),
            "automation_id": _row_get(row, "automation_id", "automationid"),
        }
        events.append(ev)

        for bucket, key in (
            (by_model, model),
            (by_kind, kind),
            (by_max_mode, max_mode),
        ):
            m = bucket[key]
            m["input"] += inp
            m["output"] += out
            m["cache_read"] += cache_read
            m["cache_write"] += cache_write
            m["total_tokens"] += total
            m["cost"] += cost
            m["count"] += 1
        by_model_kind[model][kind] += total

    def _sorted(d: dict) -> dict:
        return {
            k: dict(v)
            for k, v in sorted(d.items(), key=lambda kv: -kv[1]["total_tokens"])
        }

    by_model_s = _sorted(by_model)
    by_kind_s = _sorted(by_kind)
    events_total = float(sum(float(e["total_tokens"]) for e in events))
    return {
        "source": str(path),
        "event_count": len(events),
        "events": events,
        "by_model": by_model_s,
        "by_kind": by_kind_s,
        "by_max_mode": _sorted(by_max_mode),
        "by_model_kind": {
            m: dict(kinds) for m, kinds in sorted(by_model_kind.items())
        },
        "reconciliation": _reconcile_token_buckets(events_total, by_model_s, by_kind_s),
    }


def fetch_cursor_admin_usage(
    api_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Enterprise-team Admin API when CURSOR_ADMIN_API_KEY is set (or --cursor-admin-api).
    Individual / Pro accounts: use a dashboard Usage CSV instead.
    Dates are sent as epoch milliseconds. Uses documented Basic auth + pagination.
    Never scrapes IDE DBs.
    """
    # Explicit admin key only — never fall back to other API key env vars.
    key = api_key or os.environ.get("CURSOR_ADMIN_API_KEY")
    if not key:
        return None

    # Documented Basic auth: key as username, empty password.
    basic = base64.b64encode(f"{key}:".encode()).decode()
    url = "https://api.cursor.com/teams/filtered-usage-events"
    empty = lambda: {
        "input": 0.0,
        "output": 0.0,
        "cache_read": 0.0,
        "cache_write": 0.0,
        "total_tokens": 0.0,
        "cost": 0.0,
        "count": 0.0,
        "automations": 0.0,
        "cloud_agents": 0.0,
    }
    by_model: dict[str, dict[str, float]] = defaultdict(empty)
    by_kind: dict[str, dict[str, float]] = defaultdict(empty)
    by_model_kind: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    all_events: list = []
    page = 1
    page_size = 100

    truncated = False
    max_pages = 500  # safety; if hit, report truncated rather than silent stop
    while True:
        payload: dict[str, Any] = {"page": page, "pageSize": page_size}
        start_ms = _to_epoch_ms(start_date)
        end_ms = _to_epoch_ms(end_date)
        if start_ms is not None:
            payload["startDate"] = start_ms
        if end_ms is not None:
            payload["endDate"] = end_ms
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {basic}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            return {"error": f"admin API request failed: {type(e).__name__}", "events": []}

        events = data.get("usageEvents") or data.get("events") or data.get("data") or []
        if not isinstance(events, list):
            events = []
        if not events:
            break
        all_events.extend(events)

        for ev in events:
            if not isinstance(ev, dict):
                continue
            model = str(ev.get("model") or "unknown")
            usage = ev.get("tokenUsage") if isinstance(ev.get("tokenUsage"), dict) else {}
            inp = float(usage.get("inputTokens") or usage.get("input") or 0)
            out = float(usage.get("outputTokens") or usage.get("output") or 0)
            cr = float(usage.get("cacheReadTokens") or usage.get("cacheRead") or 0)
            cw = float(usage.get("cacheWriteTokens") or usage.get("cacheWrite") or 0)
            cost = float(ev.get("chargedCents") or usage.get("totalCents") or 0) / 100.0
            kind = "local"
            if ev.get("automationId"):
                kind = "automation"
            elif ev.get("cloudAgentId") or ev.get("isHeadless"):
                kind = "cloud_agent"
            row_total = inp + out + cr + cw
            for bucket, key_name in ((by_model, model), (by_kind, kind)):
                m = bucket[key_name]
                m["input"] += inp
                m["output"] += out
                m["cache_read"] += cr
                m["cache_write"] += cw
                m["total_tokens"] += row_total
                m["cost"] += cost
                m["count"] += 1
                if kind == "automation":
                    m["automations"] = m.get("automations", 0) + 1
                if kind == "cloud_agent":
                    m["cloud_agents"] = m.get("cloud_agents", 0) + 1
            by_model_kind[model][kind] += row_total

        # Docs return pagination.numPages / hasNextPage (also accept legacy keys).
        pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
        total_pages = (
            pagination.get("numPages")
            or data.get("totalPages")
            or data.get("pageCount")
        )
        has_next = pagination.get("hasNextPage")
        if has_next is False:
            break
        if total_pages is not None and page >= int(total_pages):
            break
        # Prefer explicit hasNextPage=True over a short final-ish page.
        if has_next is not True and len(events) < page_size:
            break
        page += 1
        if page > max_pages:
            truncated = True
            break

    clean_model = {
        k: {kk: vv for kk, vv in v.items()}
        for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total_tokens"])
    }
    clean_kind = {
        k: {kk: vv for kk, vv in v.items()}
        for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]["total_tokens"])
    }
    events_total = float(sum(float(v["total_tokens"]) for v in clean_model.values()))
    return {
        "source": "admin_api",
        "event_count": len(all_events),
        "by_model": clean_model,
        "by_kind": clean_kind,
        "by_model_kind": {
            m: dict(kinds) for m, kinds in sorted(by_model_kind.items())
        },
        "by_max_mode": {},
        "events": [],
        "pages_fetched": page,
        "truncated": truncated,
        "reconciliation": _reconcile_token_buckets(events_total, clean_model, clean_kind),
        "notes": (
            [
                f"Admin API results truncated after {max_pages} pages "
                f"({len(all_events)} events). Narrow --admin-start/--admin-end or re-run."
            ]
            if truncated
            else []
        ),
    }


def _usage_dedupe_key(obj: dict, msg: dict) -> Optional[str]:
    for key in (
        msg.get("id"),
        obj.get("requestId"),
        obj.get("request_id"),
        msg.get("requestId"),
    ):
        if key:
            return str(key)
    return None


def _extract_tools(content: Any) -> list[dict[str, str]]:
    tools: list[dict[str, str]] = []
    if not isinstance(content, list):
        return tools
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tools.append(
                {
                    "name": str(block.get("name") or "tool"),
                    "id": str(block.get("id") or ""),
                }
            )
    return tools


def _extract_tool_results(content: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            out.append(str(block.get("tool_use_id") or block.get("name") or "tool"))
    return out


def _prompt_tokens(r: dict[str, Any]) -> int:
    """Input + cache_creation + cache_read (prompt-side tokens for growth)."""
    return (
        int(r.get("input") or 0)
        + int(r.get("cache_write") or 0)
        + int(r.get("cache_read") or 0)
    )


def _parse_claude_jsonl(path: Path) -> list[dict[str, Any]]:
    """
    Parse transcript lines. Deduplicate *usage* by message.id / requestId
    (split responses repeat usage), but still collect tool_use from every line.
    """
    rows: list[dict[str, Any]] = []
    seen_usage: set[str] = set()
    if is_secret_path(path):
        return rows
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
                if not isinstance(msg, dict):
                    continue

                content = msg.get("content") or obj.get("content") or []
                tools_used = _extract_tools(content)
                tool_results = _extract_tool_results(content)
                usage = msg.get("usage") or obj.get("usage")
                has_usage = isinstance(usage, dict)

                if not has_usage:
                    if tools_used or tool_results:
                        rows.append(
                            {
                                "model": str(msg.get("model") or obj.get("model") or ""),
                                "input": 0,
                                "output": 0,
                                "cache_read": 0,
                                "cache_write": 0,
                                "tools_used": tools_used,
                                "tool_results": tool_results,
                                "has_usage": False,
                                "path": str(path),
                                "dedupe_key": None,
                            }
                        )
                    continue

                dedupe = _usage_dedupe_key(obj, msg)
                usage_dup = bool(dedupe and dedupe in seen_usage)
                if dedupe and not usage_dup:
                    seen_usage.add(dedupe)

                if usage_dup:
                    # Keep tool_use from this split line; skip re-counting usage.
                    if tools_used or tool_results:
                        rows.append(
                            {
                                "model": str(msg.get("model") or obj.get("model") or "unknown"),
                                "input": 0,
                                "output": 0,
                                "cache_read": 0,
                                "cache_write": 0,
                                "tools_used": tools_used,
                                "tool_results": tool_results,
                                "has_usage": False,
                                "path": str(path),
                                "dedupe_key": dedupe,
                                "usage_deduped": True,
                            }
                        )
                    continue

                rows.append(
                    {
                        "model": str(msg.get("model") or obj.get("model") or "unknown"),
                        "input": int(usage.get("input_tokens") or 0),
                        "output": int(usage.get("output_tokens") or 0),
                        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
                        "tools_used": tools_used,
                        "tool_results": tool_results,
                        "has_usage": True,
                        "path": str(path),
                        "dedupe_key": dedupe,
                    }
                )
    except OSError:
        return rows
    return rows


def _attribute_tools(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """
    Attribute tool cost to the request that carries the tool_result
    (next request's prompt-token growth including cache), not the tool_use request.
    """
    by_tool: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "attributed_input": 0, "input_growth": 0}
    )
    pending_tools: list[str] = []
    prev_prompt = 0

    for r in rows:
        if pending_tools and r.get("has_usage"):
            cur = _prompt_tokens(r)
            growth = max(0, cur - prev_prompt)
            share = growth / len(pending_tools) if pending_tools else 0
            for name in pending_tools:
                by_tool[name]["count"] += 1
                by_tool[name]["attributed_input"] += cur
                by_tool[name]["input_growth"] += int(share)
            pending_tools = []

        if r.get("has_usage"):
            prev_prompt = _prompt_tokens(r)

        for t in r.get("tools_used") or []:
            pending_tools.append(t["name"] if isinstance(t, dict) else str(t))

    return dict(by_tool)


def collect_claude_tokens(roots: Roots) -> dict[str, Any]:
    home = roots.claude_home or (roots.home / ".claude")
    projects = home / "projects"
    alt = roots.home / ".config" / "claude" / "projects"
    jsonl_files: list[Path] = []
    bases = [projects, alt]
    env_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_cfg:
        bases.insert(0, Path(env_cfg).expanduser() / "projects")
    seen: set[Path] = set()
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            jsonl_files.append(path)

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total_tokens": 0,
            "count": 0,
        }
    )
    by_tool: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "attributed_input": 0, "input_growth": 0}
    )
    sessions = 0
    deduped_rows = 0
    for path in jsonl_files:
        rows = _parse_claude_jsonl(path)
        usage_rows = [r for r in rows if r.get("has_usage")]
        if usage_rows:
            sessions += 1
        deduped_rows += len(usage_rows)
        for r in usage_rows:
            m = by_model[r["model"]]
            m["input"] += r["input"]
            m["output"] += r["output"]
            m["cache_read"] += r["cache_read"]
            m["cache_write"] += r["cache_write"]
            m["total_tokens"] += r["input"] + r["output"] + r["cache_read"] + r["cache_write"]
            m["count"] += 1
        for name, stats in _attribute_tools(rows).items():
            by_tool[name]["count"] += stats["count"]
            by_tool[name]["attributed_input"] += stats["attributed_input"]
            by_tool[name]["input_growth"] += stats.get("input_growth", 0)

    by_model_out = {
        k: dict(v)
        for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total_tokens"])
    }
    model_total = float(sum(float(v["total_tokens"]) for v in by_model_out.values()))
    return {
        "source": "local_jsonl",
        "official_auto": True,
        "official_source": (
            "https://code.claude.com/docs/en/sessions — transcripts at "
            "~/.claude/projects/<cwd-slug>/*.jsonl"
        ),
        "transcript_files": len(jsonl_files),
        "sessions_with_usage": sessions,
        "usage_rows_deduped": deduped_rows,
        "by_model": by_model_out,
        "by_tool": {
            k: dict(v) for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1]["count"])
        },
        "estimate": False,
        "reconciliation": _reconcile_token_buckets(model_total, by_model_out),
        "notes": [
            "Official auto source: local session JSONL under ~/.claude/projects/ "
            "(Claude Code docs: Manage sessions).",
            "Usage deduped by message.id / requestId (split responses).",
            "Tool cost attributed to the request carrying tool_result (input growth).",
            "File fallback: pass --usage-file PATH to a JSON/CSV export if JSONL is unavailable.",
        ],
    }


def collect_cursor_tokens(
    roots: Roots,
    csv_path: Optional[Path] = None,
    use_admin_api: bool = False,
    admin_start: Optional[str] = None,
    admin_end: Optional[str] = None,
    usage_file: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Official auto: Cursor Admin API when CURSOR_ADMIN_API_KEY is set
    (Enterprise team admin keys only — https://cursor.com/docs/account/teams/admin-api).
    Individual / Pro accounts: download Usage CSV (--csv / --usage-file).
    Never scrape state.vscdb or cookies.
    """
    notes = [
        "Official auto (Enterprise team admin keys only): Admin API "
        "POST /teams/filtered-usage-events when CURSOR_ADMIN_API_KEY is set. "
        "Docs: https://cursor.com/docs/account/teams/admin-api. "
        "Individual / Pro accounts must use a dashboard Usage CSV instead.",
        "File fallback: Cursor dashboard → Usage → export/download CSV, then --csv PATH.",
        "Never scrape session tokens from state.vscdb or browser cookies.",
    ]
    csv_data = None
    explicit_csv = csv_path is not None

    if usage_file and Path(usage_file).suffix.lower() == ".csv":
        csv_path = Path(usage_file)
        explicit_csv = True
    if usage_file and Path(usage_file).suffix.lower() != ".csv":
        file_data = parse_usage_file(Path(usage_file))
        if not file_data.get("error"):
            return {
                **file_data,
                "official_auto": False,
                "estimate": False,
                "notes": notes + list(file_data.get("notes") or []),
            }
        notes.append(f"usage-file failed: {file_data.get('error')}")

    if csv_path:
        csv_path = Path(csv_path)
        if not csv_path.is_file():
            return {
                "source": "none",
                "official_auto": False,
                "estimate": False,
                "by_model": {},
                "by_kind": {},
                "by_max_mode": {},
                "by_tool": {},
                "error": f"CSV not found: {csv_path}",
                "reconciliation": _reconcile_token_buckets(0, {}, {}),
                "notes": notes + [f"CSV path does not exist: {csv_path}"],
                "missing_csv": True,
            }
        csv_data = parse_cursor_usage_csv(csv_path)
        if csv_data.get("error"):
            return {
                "source": "none",
                "official_auto": False,
                "estimate": False,
                "by_model": {},
                "by_kind": {},
                "by_max_mode": {},
                "by_tool": {},
                "error": csv_data["error"],
                "reconciliation": _reconcile_token_buckets(0, {}, {}),
                "notes": notes + [str(csv_data["error"])],
                "missing_csv": True,
            }
    else:
        for candidate in (
            roots.project / "usage.csv",
            Path.cwd() / "usage.csv",
            roots.home / "usage.csv",
        ):
            if candidate.is_file():
                csv_data = parse_cursor_usage_csv(candidate)
                break

    have_csv = bool(csv_data and not csv_data.get("error"))

    # Skip Admin API when a usable CSV is already loaded (avoid discarded live calls).
    key_present = bool(os.environ.get("CURSOR_ADMIN_API_KEY"))
    want_admin = (use_admin_api or key_present) and not have_csv
    admin = None
    if want_admin:
        admin = fetch_cursor_admin_usage(start_date=admin_start, end_date=admin_end)
        if admin and admin.get("error"):
            notes.append(f"Admin API failed: {admin['error']}")
            admin = None
        elif admin:
            notes.append(
                "Fetched usage via Enterprise Admin API (CURSOR_ADMIN_API_KEY)."
            )
            for n in admin.get("notes") or []:
                notes.append(n)
        elif use_admin_api and not key_present:
            notes.append(
                "Admin API requested (--cursor-admin-api) but CURSOR_ADMIN_API_KEY is unset."
            )
    elif have_csv and (use_admin_api or key_present):
        notes.append(
            "CSV provided — Admin API skipped (CSV is the usage source)."
        )
    else:
        notes.append(
            "Admin API not used (Enterprise: set CURSOR_ADMIN_API_KEY; "
            "otherwise pass --csv for a dashboard export)."
        )

    if have_csv:
        primary = csv_data
        source = "usage_csv"
        official_auto = False
    elif admin:
        primary = admin
        source = "admin_api"
        official_auto = True
    else:
        return {
            "source": "none",
            "official_auto": False,
            "estimate": False,
            "by_model": {},
            "by_kind": {},
            "by_max_mode": {},
            "by_tool": {},
            "reconciliation": _reconcile_token_buckets(0, {}, {}),
            "notes": notes
            + [
                "No usage data yet. Download the Usage CSV from the Cursor dashboard "
                "(Usage page → export) and re-run with --csv PATH. "
                "Enterprise team admins may set CURSOR_ADMIN_API_KEY instead.",
            ],
            "missing_csv": True,
        }

    recon = primary.get("reconciliation")
    if not recon:
        by_model = primary.get("by_model") or {}
        by_kind = primary.get("by_kind") or {}
        total = float(sum(float(v.get("total_tokens") or 0) for v in by_model.values()))
        recon = _reconcile_token_buckets(total, by_model, by_kind)

    return {
        "source": source,
        "official_auto": official_auto,
        "official_source": (
            "https://cursor.com/docs/account/teams/admin-api (Enterprise)"
            if source == "admin_api"
            else "Cursor dashboard Usage CSV export"
        ),
        "estimate": False,
        "by_model": primary.get("by_model") or {},
        "by_kind": primary.get("by_kind") or {},
        "by_max_mode": primary.get("by_max_mode") or {},
        "by_model_kind": primary.get("by_model_kind") or {},
        "by_tool": {},
        "event_count": primary.get("event_count", 0),
        "csv": csv_data.get("source") if csv_data else None,
        "admin": bool(admin),
        "truncated": bool(primary.get("truncated")),
        "reconciliation": recon,
        "notes": notes,
    }



def collect_grok_tokens(
    roots: Roots,
    usage_total: Optional[float] = None,
    usage_period: Optional[str] = None,
    usage_file: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Grok Bot has no official machine-readable usage export/API for the Bot itself.

    xAI Management API (/v1/billing/teams/.../usage) covers API-key spend, not
    Grok Bot. Cookie-authenticated grok.com billing endpoints are not official
    for this tool (never scrape cookies/keys).

    File fallback: --usage-file JSON or --usage-total / --usage-period from the
    Bot's Usage & Billing UI.
    """
    notes = [
        "No official auto-download for Grok Bot usage: there is no documented "
        "Bot usage API or export. xAI Management API is for API keys, not Bot. "
        "Do not scrape grok.com cookies.",
        "File fallback: copy totals from the Bot Usage & Billing UI into a JSON "
        'file ({"usage_total": N, "usage_period": "...", "unit": "percent"}) '
        "and pass --usage-file PATH, or use --usage-total / --usage-period.",
    ]
    if usage_file:
        file_data = parse_usage_file(Path(usage_file))
        if file_data.get("error"):
            notes.append(f"usage-file failed: {file_data['error']}")
        else:
            return {
                "source": "usage_file",
                "official_auto": False,
                "official_source": None,
                "estimate": False,
                "by_bot": {},
                "by_model": file_data.get("by_model") or {},
                "by_kind": {},
                "by_tool": {},
                "usage_total": file_data.get("usage_total"),
                "usage_period": file_data.get("usage_period"),
                "unit": file_data.get("unit"),
                "reconciliation": file_data.get("reconciliation")
                or _reconcile_token_buckets(float(file_data.get("usage_total") or 0), {}),
                "notes": notes + list(file_data.get("notes") or []),
            }

    sand = roots.sand_data
    by_bot: dict[str, dict[str, float]] = {}
    if sand and (sand / "search-index.db").is_file():
        db_path = sand / "search-index.db"
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="ai-audit-sqlite-")
            dst = Path(tmp_dir) / "search-index.db"
            shutil.copy2(db_path, dst)
            for suffix in ("-wal", "-shm"):
                side = Path(str(db_path) + suffix)
                if side.is_file():
                    shutil.copy2(side, Path(str(dst) + suffix))
            con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
            try:
                cur = con.execute(
                    "SELECT agent_id, COUNT(*), COALESCE(SUM(LENGTH(body)), 0) "
                    "FROM messages GROUP BY agent_id"
                )
                for agent_id, count, total_len in cur.fetchall():
                    by_bot[str(agent_id)] = {
                        "message_count": float(count),
                        "chars": float(total_len),
                    }
            finally:
                con.close()
        except (sqlite3.Error, OSError) as e:
            notes.append(f"search-index.db unreadable: {type(e).__name__}")
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    safe_period = redact_secrets(str(usage_period)) if usage_period else usage_period
    if usage_total is not None:
        recon = _reconcile_token_buckets(float(usage_total), {})
        # For a scalar total with no buckets, treat as reconciled self-check.
        recon = {
            "source_total_tokens": float(usage_total),
            "by_model_sum": float(usage_total),
            "by_kind_sum": float(usage_total),
            "ok": True,
            "no_data": False,
            "status": "ok",
        }
        notes.append(
            redact_secrets(
                f"User-provided usage total: {usage_total}"
                + (f" ({safe_period})" if safe_period else "")
            )
        )
        return {
            "source": "usage_file",
            "official_auto": False,
            "official_source": None,
            "estimate": False,
            "by_bot": dict(sorted(by_bot.items(), key=lambda kv: -kv[1].get("message_count", 0))),
            "by_model": {},
            "by_kind": {},
            "by_tool": {},
            "usage_total": usage_total,
            "usage_period": safe_period,
            "reconciliation": recon,
            "notes": notes,
            "local_activity": by_bot,
        }

    return {
        "source": "none",
        "official_auto": False,
        "official_source": None,
        "estimate": False,
        "by_bot": dict(sorted(by_bot.items(), key=lambda kv: -kv[1].get("message_count", 0))),
        "by_model": {},
        "by_kind": {},
        "by_tool": {},
        "usage_total": None,
        "usage_period": None,
        "reconciliation": _reconcile_token_buckets(0, {}),
        "notes": notes
        + [
            "Provide a usage file (--usage-file) or --usage-total from the Bot "
            "Usage & Billing page. Local message counts (if any) are activity only, "
            "not official token usage.",
        ],
        "missing_usage_file": True,
        "local_activity": by_bot,
    }



def _reconcile_token_buckets(
    events_total: float,
    by_model: dict,
    by_kind: Optional[dict] = None,
) -> dict[str, Any]:
    """Show that bucket sums match the source event total (criterion 3b)."""
    model_sum = float(sum(float(v.get("total_tokens") or 0) for v in by_model.values()))
    kind_sum = (
        float(sum(float(v.get("total_tokens") or 0) for v in (by_kind or {}).values()))
        if by_kind is not None
        else model_sum
    )
    src = float(events_total)
    matches = abs(src - model_sum) < 0.01 and (
        by_kind is None or abs(src - kind_sum) < 0.01
    )
    no_data = src == 0 and model_sum == 0 and (
        by_kind is None or kind_sum == 0
    )
    return {
        "source_total_tokens": src,
        "by_model_sum": model_sum,
        "by_kind_sum": kind_sum,
        "ok": matches,
        "no_data": no_data,
        "status": "no_data" if no_data else ("ok" if matches else "mismatch"),
    }


def parse_usage_file(path: Path) -> dict[str, Any]:
    """
    User-downloaded usage file fallback (any host).

    Supported shapes:
      - Cursor dashboard CSV (delegates to parse_cursor_usage_csv)
      - JSON: {"usage_total": N, "usage_period": "...", "unit": "percent"|"tokens"|...}
      - JSON: {"total": N, ...} or {"by_model": {"model": {"total_tokens": N}}}
    """
    path = Path(path)
    if not path.is_file():
        return {"error": f"usage file not found: {path}", "source": "usage_file"}
    if path.suffix.lower() == ".csv":
        data = parse_cursor_usage_csv(path)
        data["source"] = "usage_file_csv"
        return data
    text = safe_read_text(path)
    if text is None:
        return {"error": f"cannot read usage file: {path}", "source": "usage_file"}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return {"error": f"usage file JSON invalid: {e}", "source": "usage_file"}
    if not isinstance(raw, dict):
        return {"error": "usage file JSON must be an object", "source": "usage_file"}
    usage_total = raw.get("usage_total", raw.get("total"))
    period = raw.get("usage_period") or raw.get("period")
    unit = raw.get("unit") or "units"
    by_model = raw.get("by_model") or {}
    if by_model and not usage_total:
        usage_total = sum(float((v or {}).get("total_tokens") or v or 0) for v in by_model.values())
    recon_total = float(usage_total or 0)
    if by_model:
        recon = _reconcile_token_buckets(
            recon_total
            if recon_total
            else sum(float((v or {}).get("total_tokens") or 0) for v in by_model.values()),
            {k: (v if isinstance(v, dict) else {"total_tokens": float(v)}) for k, v in by_model.items()},
        )
    else:
        recon = {
            "source_total_tokens": recon_total,
            "by_model_sum": recon_total,
            "by_kind_sum": recon_total,
            "ok": True,
        }
    safe_period = redact_secrets(str(period)) if period else period
    return {
        "source": "usage_file",
        "path": str(path),
        "usage_total": usage_total,
        "usage_period": safe_period,
        "unit": unit,
        "by_model": by_model,
        "by_kind": {},
        "by_tool": {},
        "estimate": False,
        "reconciliation": recon,
        "notes": [
            redact_secrets(
                f"Loaded user usage file ({unit})"
                + (f" for {safe_period}" if safe_period else "")
                + "."
            )
        ],
    }


def rank_token_wins(host: str, data: dict[str, Any]) -> list[Win]:
    """v0.1.0: savings estimates are out of scope — attribution only."""
    return []



def run_token_audit(
    roots: Roots,
    csv_path: Optional[Path] = None,
    use_admin_api: bool = False,
    admin_start: Optional[str] = None,
    admin_end: Optional[str] = None,
    usage_total: Optional[float] = None,
    usage_period: Optional[str] = None,
    usage_file: Optional[Path] = None,
) -> dict[str, Any]:
    if roots.host == "claude":
        if usage_file:
            file_data = parse_usage_file(Path(usage_file))
            if file_data.get("error"):
                data = collect_claude_tokens(roots)
                data.setdefault("notes", []).append(
                    f"usage-file fallback failed: {file_data.get('error')}"
                )
            else:
                data = file_data
                data["official_auto"] = False
                data.setdefault("notes", []).append(
                    "Using --usage-file instead of local JSONL."
                )
        else:
            data = collect_claude_tokens(roots)
    elif roots.host == "cursor":
        data = collect_cursor_tokens(
            roots,
            csv_path=csv_path,
            use_admin_api=use_admin_api,
            admin_start=admin_start,
            admin_end=admin_end,
            usage_file=usage_file,
        )
    else:
        data = collect_grok_tokens(
            roots,
            usage_total=usage_total,
            usage_period=usage_period,
            usage_file=usage_file,
        )

    wins = rank_token_wins(roots.host, data)
    summary: dict[str, Any] = {
        "source": data.get("source"),
        "estimate": bool(data.get("estimate")),
    }
    if data.get("event_count") is not None:
        summary["events"] = data["event_count"]
    if data.get("sessions_with_usage") is not None:
        summary["sessions_with_usage"] = data["sessions_with_usage"]
    if data.get("usage_rows_deduped") is not None:
        summary["usage_rows_deduped"] = data["usage_rows_deduped"]
    if data.get("by_model"):
        summary["models"] = len(data["by_model"])
        top = max(data["by_model"].items(), key=lambda kv: kv[1].get("total_tokens") or 0)
        summary["top_model"] = f"{top[0]} ({int(top[1].get('total_tokens') or 0)} tokens)"
    if data.get("by_kind"):
        summary["by_kind"] = {
            k: int(v.get("total_tokens") or 0) for k, v in data["by_kind"].items()
        }
    if data.get("by_bot"):
        summary["bots"] = len(data["by_bot"])
    if data.get("usage_total") is not None:
        summary["usage_total"] = data["usage_total"]
        if data.get("usage_period"):
            summary["usage_period"] = data["usage_period"]
    if data.get("reconciliation") is not None:
        summary["reconciliation"] = data["reconciliation"]
    if data.get("official_auto") is not None:
        summary["official_auto"] = data["official_auto"]

    return {
        "audit": "token",
        "host": roots.host,
        "roots": roots.to_dict(),
        "summary": summary,
        "estimate": bool(data.get("estimate")),
        "data": data,
        "wins": [w.to_dict() for w in wins],
        "notes": data.get("notes") or [],
    }
