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
    rank_wins,
    safe_read_text,
)
from .detect import Roots

# Minimum events before suggesting a cache-hit win (avoid single-subagent noise).
CACHE_HIT_MIN_EVENTS = 5
# Fraction of measured tool-result prompt growth claimed as a trim saving.
TOOL_RESULT_TRIM_FRAC = 0.5
# Cache-hit saving: share of (input − cache_read) that is realistically static
# enough to become cache hits, times the typical cached-vs-uncached input price
# delta (~10¢ cached vs ~$1 uncached → ~90% cost reduction on that share).
CACHE_HIT_CACHEABLE_SHARE = 0.5
CACHE_HIT_PRICE_DELTA = 0.9  # fraction of uncached input price saved when cached
# Soft ceiling: optimistic levers overlap; never imply you can erase nearly all spend.
SAVINGS_BUDGET_FRAC = 0.8


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

    return {
        "source": str(path),
        "event_count": len(events),
        "events": events,
        "by_model": _sorted(by_model),
        "by_kind": _sorted(by_kind),
        "by_max_mode": _sorted(by_max_mode),
        "by_model_kind": {
            m: dict(kinds) for m, kinds in sorted(by_model_kind.items())
        },
    }


def fetch_cursor_admin_usage(
    api_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Opt-in team Admin API (requires --cursor-admin-api plus env key).
    Uses documented Basic auth. Supports date range + pagination.
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

    while True:
        payload: dict[str, Any] = {"page": page, "pageSize": page_size}
        if start_date:
            payload["startDate"] = start_date
        if end_date:
            payload["endDate"] = end_date
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

        # Stop when a short page arrives or pagination metadata says done.
        total_pages = data.get("totalPages") or data.get("pageCount")
        if total_pages is not None and page >= int(total_pages):
            break
        if len(events) < page_size:
            break
        page += 1
        if page > 50:  # hard safety cap
            break

    clean_model = {
        k: {kk: vv for kk, vv in v.items()}
        for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total_tokens"])
    }
    clean_kind = {
        k: {kk: vv for kk, vv in v.items()}
        for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]["total_tokens"])
    }
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
    for base in (projects, alt):
        if base.is_dir():
            jsonl_files.extend(sorted(base.rglob("*.jsonl")))

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

    return {
        "source": "local_jsonl",
        "transcript_files": len(jsonl_files),
        "sessions_with_usage": sessions,
        "usage_rows_deduped": deduped_rows,
        "by_model": {
            k: dict(v)
            for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total_tokens"])
        },
        "by_tool": {
            k: dict(v) for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1]["count"])
        },
        "estimate": False,
        "notes": [
            "Local session JSONL only — cloud/web Console usage is not on disk.",
            "Usage deduped by message.id / requestId (split responses).",
            "Tool cost attributed to the request carrying tool_result (input growth).",
            "Dollar figures omitted; rank by tokens. List-price $ is not your bill.",
        ],
    }


def collect_cursor_tokens(
    roots: Roots,
    csv_path: Optional[Path] = None,
    use_admin_api: bool = False,
    admin_start: Optional[str] = None,
    admin_end: Optional[str] = None,
) -> dict[str, Any]:
    notes = [
        "No local token data in the IDE DB — use a dashboard Usage CSV (cloud agents + automations).",
        "Never scrape session tokens from state.vscdb.",
        "Cloud usage is the priority; local/IDE rows are secondary.",
    ]
    csv_data = None
    if csv_path:
        csv_data = parse_cursor_usage_csv(Path(csv_path))
    else:
        for candidate in (
            roots.project / "usage.csv",
            Path.cwd() / "usage.csv",
            roots.home / "usage.csv",
        ):
            if candidate.is_file():
                csv_data = parse_cursor_usage_csv(candidate)
                break

    admin = None
    if use_admin_api:
        admin = fetch_cursor_admin_usage(start_date=admin_start, end_date=admin_end)
        if admin and admin.get("error"):
            notes.append(f"Admin API failed: {admin['error']}")
            admin = None
        elif admin:
            notes.append("Included opt-in Admin API usage-events (Basic auth).")
    else:
        notes.append("Admin API not used (pass --cursor-admin-api to opt in).")

    if csv_data and not csv_data.get("error"):
        primary = csv_data
        source = "usage_csv"
    elif admin:
        primary = admin
        source = "admin_api"
    else:
        return {
            "source": "none",
            "estimate": False,
            "by_model": {},
            "by_kind": {},
            "by_max_mode": {},
            "by_tool": {},
            "notes": notes
            + [
                "Ask the user to download Usage CSV from the Cursor dashboard, then re-run with --csv PATH.",
            ],
            "missing_csv": True,
        }

    return {
        "source": source,
        "estimate": False,
        "by_model": primary.get("by_model") or {},
        "by_kind": primary.get("by_kind") or {},
        "by_max_mode": primary.get("by_max_mode") or {},
        "by_model_kind": primary.get("by_model_kind") or {},
        "by_tool": {},
        "event_count": primary.get("event_count", 0),
        "csv": csv_data.get("source") if csv_data else None,
        "admin": bool(admin),
        "notes": notes,
    }


def _estimate_runs_per_week(cron: str) -> float:
    """Rough weekly run count from a cron expression (5-field)."""
    parts = (cron or "").split()
    if len(parts) < 5:
        return 0.0
    minute, hour, dom, month, dow = parts[:5]

    def _count_field(field: str, max_n: int) -> float:
        if field == "*":
            return float(max_n)
        if "/" in field:
            # */n
            base, _, step = field.partition("/")
            try:
                step_n = int(step)
            except ValueError:
                return 1.0
            span = max_n if base in ("*", "") else 1
            return max(1.0, span / step_n)
        return float(len(field.split(",")))

    per_hour = _count_field(minute, 60) if minute != "*" else 1.0
    # If minute is */n, runs per hour = 60/n; if specific minutes, count them.
    if minute == "*":
        per_hour = 1.0
    elif minute.startswith("*/"):
        try:
            per_hour = 60 / int(minute[2:])
        except ValueError:
            per_hour = 1.0
    else:
        per_hour = float(len(minute.split(",")))

    if hour == "*":
        per_day = per_hour * 24
    elif hour.startswith("*/"):
        try:
            per_day = per_hour * (24 / int(hour[2:]))
        except ValueError:
            per_day = per_hour
    else:
        per_day = per_hour * len(hour.split(","))

    if dow not in ("*", "?"):
        # Specific weekdays
        days = len(dow.split(","))
        return per_day * days
    if dom not in ("*", "?"):
        return per_day * len(dom.split(","))  # per month ≈ treat as weekly/4 later
    return per_day * 7


def collect_grok_tokens(
    roots: Roots,
    usage_total: Optional[float] = None,
    usage_period: Optional[str] = None,
    routines: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    sand = roots.sand_data
    notes = [
        "ESTIMATE: Grok Bot Usage & Billing shows weekly totals only — no per-bot/tool/model breakdown on disk.",
        "Model choice is host-picked; 'cheaper model' is not a user lever here.",
        "Ask the user for Usage page numbers (--usage-total / --usage-period) and list routines (--routines-json).",
    ]
    by_bot: dict[str, dict[str, float]] = {}
    if sand and (sand / "search-index.db").is_file():
        db_path = sand / "search-index.db"
        tmp_dir = None
        try:
            # Copy db + wal + shm so we see recent WAL rows without rewriting the live DB.
            # Opening the live file read-only can miss uncheckpointed WAL content.
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
                        "est_tokens": float(est_tokens(int(total_len))),
                    }
            finally:
                con.close()
        except (sqlite3.Error, OSError) as e:
            notes.append(f"search-index.db unreadable: {type(e).__name__}")
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    routine_files = 0
    if sand and (sand / "agents").is_dir():
        for auto_dir in (sand / "agents").glob("*/automations"):
            if auto_dir.is_dir():
                routine_files += sum(1 for _ in auto_dir.iterdir())

    routines = routines or []
    routine_estimates: list[dict[str, Any]] = []
    for r in routines:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or r.get("id") or "routine")
        cron = str(r.get("cron") or r.get("schedule") or "")
        runs = _estimate_runs_per_week(cron) if cron else float(r.get("runs_per_week") or 0)
        routine_estimates.append(
            {"name": name, "cron": cron, "est_runs_per_week": runs}
        )

    # Always-loaded context evidence per bot (description + memory bytes).
    # Not converted into a token saving: context×messages is a different unit
    # from the message-text volume that backs the ledger budget, and Grok
    # exposes no per-call token data. Skill catalogs also load every turn.
    if sand:
        for agent_id, stats in by_bot.items():
            always_bytes = 0
            profile = sand / "agents" / agent_id / "profile.json"
            pdata = load_json_safe(profile) if profile.is_file() else None
            if isinstance(pdata, dict):
                always_bytes += len(str(pdata.get("description") or "").encode("utf-8"))
            mem = sand / "user-memory" / "by-agent" / agent_id / "profile.md"
            if mem.is_file():
                always_bytes += mem.stat().st_size
            msg_n = float(stats.get("message_count") or 0)
            stats["always_context_bytes"] = float(always_bytes)
            stats["always_context_tokens"] = float(est_tokens(always_bytes))
            stats["stored_message_count"] = msg_n

    total_tokens = sum(v["est_tokens"] for v in by_bot.values()) or 1.0
    for v in by_bot.values():
        v["share"] = round(v["est_tokens"] / total_tokens, 4)

    if usage_total is not None:
        notes.append(
            f"User-provided usage total: {usage_total}"
            + (f" ({usage_period})" if usage_period else "")
        )

    return {
        "source": "estimate_message_volume",
        "estimate": True,
        "by_bot": dict(sorted(by_bot.items(), key=lambda kv: -kv[1]["est_tokens"])),
        "by_model": {},
        "by_kind": {},
        "by_tool": {},
        "routine_files_on_disk": routine_files,
        "routines": routine_estimates,
        "usage_total": usage_total,
        "usage_period": usage_period,
        "notes": notes,
    }


def _token_volume_raw(data: dict[str, Any]) -> int:
    """Raw measured token volume (before the soft savings ceiling)."""
    by_model = data.get("by_model") or {}
    if by_model:
        return int(sum(float(v.get("total_tokens") or 0) for v in by_model.values()))
    if data.get("usage_total") is not None:
        return max(0, int(float(data["usage_total"])))
    by_bot = data.get("by_bot") or {}
    if by_bot:
        return int(sum(float(v.get("est_tokens") or 0) for v in by_bot.values()))
    by_kind = data.get("by_kind") or {}
    return int(sum(float(v.get("total_tokens") or 0) for v in by_kind.values()))


def _token_volume_budget(data: dict[str, Any]) -> int:
    """
    Ledger ceiling for claimed savings.

    Individual wins are optimistic and partially overlapping. Cap at
    SAVINGS_BUDGET_FRAC of measured usage so the report never implies you can
    erase nearly all spend by stacking every lever.
    """
    raw = _token_volume_raw(data)
    return int(raw * SAVINGS_BUDGET_FRAC)


class _ClaimedLedger:
    """Shared savings ledger so reported savings never exceed measured volume."""

    def __init__(self, budget: int) -> None:
        self.budget = max(0, int(budget))
        self.claimed = 0
        self.by_model: dict[str, int] = defaultdict(int)

    def remaining(self) -> int:
        return max(0, self.budget - self.claimed)

    def claim(self, amount: int, *, model: Optional[str] = None) -> int:
        amt = max(0, int(amount))
        amt = min(amt, self.remaining())
        self.claimed += amt
        if model and amt:
            self.by_model[model] += amt
        return amt

    def same_model_claimed(self, model: str) -> int:
        return int(self.by_model.get(model, 0))


def rank_token_wins(host: str, data: dict[str, Any]) -> list[Win]:
    """
    Emit savings that estimate what a change would save (with method stated),
    not raw totals. Every positive saving goes through one claimed-tokens ledger
    so the sum of savings can never exceed measured total volume.
    """
    wins: list[Win] = []
    ledger = _ClaimedLedger(_token_volume_budget(data))

    by_model = data.get("by_model") or {}
    by_kind = data.get("by_kind") or {}
    by_model_kind: dict[str, dict[str, float]] = data.get("by_model_kind") or {}

    # Cloud / automation first: estimate halving frequency.
    for kind in ("cloud_agent", "automation"):
        m = by_kind.get(kind)
        if not m:
            continue
        total = int(m.get("total_tokens") or 0)
        count = int(m.get("count") or 0)
        if total <= 0:
            continue
        label = "cloud agents" if kind == "cloud_agent" else "automations"
        desired = max(0, total // 2)
        saving = ledger.claim(desired)
        # Attribute the *actual* claim to models that contributed (same-model overlap only).
        if by_model_kind and total > 0 and saving > 0:
            for model, kinds in by_model_kind.items():
                share = float(kinds.get(kind) or 0)
                if share <= 0:
                    continue
                ledger.by_model[model] += int(round(saving * (share / total)))
        overlaps = ""
        if saving < desired:
            overlaps = f"capped by ledger (wanted {desired})"
        wins.append(
            Win(
                title=f"Run {label} less often",
                saving_est_tokens=saving,
                measured=f"total volume {total} tokens across {count} events (kind={kind})",
                rationale="Cloud usage is the priority lever",
                suggestion="Widen schedules; drop chatty triggers; skip test runs.",
                method="estimate: cut run frequency ~50% → ~half of measured volume",
                overlaps=overlaps,
            )
        )

    by_max = data.get("by_max_mode") or {}
    if by_max.get("on") and int(by_max["on"].get("total_tokens") or 0) > 0:
        total = int(by_max["on"]["total_tokens"])
        wins.append(
            Win(
                title="Turn Max Mode off for routine work",
                saving_est_tokens=0,
                measured=(
                    f"total volume {total} tokens with Max Mode on "
                    f"({int(by_max['on'].get('count') or 0)} events)"
                ),
                rationale="Max Mode surcharge is a cost lever; exact premium not in the CSV",
                suggestion="Disable Max Mode for routine tasks.",
                method="saving unknown — Max surcharge only; total volume shown",
                ask=True,
                overlaps="overlaps model/kind token volumes",
            )
        )

    local = by_kind.get("local")
    if local and int(local.get("total_tokens") or 0) > 0:
        total = int(local["total_tokens"])
        wins.append(
            Win(
                title="Review local/IDE spend",
                saving_est_tokens=0,
                measured=f"total volume {total} tokens across {int(local.get('count') or 0)} events",
                rationale="Local/IDE is secondary to cloud",
                suggestion="Trim context; prefer cheaper models for IDE chats.",
                method="saving unknown — total volume shown; no schedule lever",
                ask=True,
            )
        )

    if by_model:
        ranked = sorted(by_model.items(), key=lambda kv: -float(kv[1].get("total_tokens") or 0))
        top_model, top = ranked[0]
        total = int(top.get("total_tokens") or 0)
        count = int(top.get("count") or 0)
        if total > 0:
            # Only subtract overlap claimed against *this* model (not other models' kind wins).
            same_claimed = ledger.same_model_claimed(top_model)
            residual = max(0, total - same_claimed)
            if residual > 0:
                desired = residual // 2
                saving = ledger.claim(desired, model=top_model)
                overlaps = (
                    f"overlaps same-model kind wins by ~{same_claimed} tokens"
                    if same_claimed
                    else "Overlap ~0"
                )
                if saving < desired:
                    overlaps = (
                        (overlaps + "; " if overlaps else "")
                        + f"capped by ledger (wanted {desired})"
                    )
                wins.append(
                    Win(
                        title=f"Move some `{top_model}` work to a cheaper model",
                        saving_est_tokens=saving,
                        measured=f"total volume {total} tokens across {count} events",
                        rationale="Highest token volume by model",
                        suggestion="Prefer a cheaper model for routine/automation work when quality allows.",
                        method=(
                            "estimate: move ~half of residual volume (after same-model kind wins) "
                            "to a cheaper model; price delta unknown so tokens≈proxy"
                        ),
                        overlaps=overlaps,
                    )
                )
            else:
                wins.append(
                    Win(
                        title=f"Highest-volume model `{top_model}`",
                        saving_est_tokens=0,
                        measured=f"total volume {total} tokens across {count} events",
                        rationale="Volume already covered by same-model kind/frequency wins",
                        suggestion="Prefer a cheaper model when quality allows.",
                        method="saving unknown — overlaps prior same-model kind wins",
                        ask=True,
                        overlaps=f"fully overlapped by same-model claims (~{same_claimed})",
                    )
                )
        for model, m in ranked[:3]:
            cr = float(m.get("cache_read") or 0)
            inp = float(m.get("input") or 0)
            count = int(m.get("count") or 0)
            if (
                count >= CACHE_HIT_MIN_EVENTS
                and inp > 1000
                and cr / max(inp + cr, 1) < 0.2
            ):
                gap = max(0, int(inp - cr))
                # Not 100% of the gap: only the cacheable share, priced at the
                # cached-vs-uncached input delta (tokens as a cost proxy).
                desired = int(
                    gap * CACHE_HIT_CACHEABLE_SHARE * CACHE_HIT_PRICE_DELTA
                )
                saving = ledger.claim(desired, model=model)
                overlaps = ""
                if saving < desired:
                    overlaps = f"capped by ledger (wanted {desired}); overlaps prior claims"
                pct_share = int(CACHE_HIT_CACHEABLE_SHARE * 100)
                pct_delta = int(CACHE_HIT_PRICE_DELTA * 100)
                wins.append(
                    Win(
                        title=f"Improve prompt-cache hit rate ({model})",
                        saving_est_tokens=saving,
                        measured=(
                            f"cache_read={int(cr)} vs input={int(inp)} over {count} events "
                            f"(gap {gap}; claiming ~{pct_share}% cacheable × ~{pct_delta}% price delta)"
                        ),
                        rationale="Low cache-read share means repeated static context is re-billed",
                        suggestion="Stabilize always-on context; trim volatile prefixes.",
                        method=(
                            f"estimate: ~{pct_share}% of (input−cache_read) realistically cacheable "
                            f"× ~{pct_delta}% cached-vs-uncached input price delta"
                        ),
                        overlaps=overlaps,
                    )
                )

    by_tool = data.get("by_tool") or {}
    if by_tool:
        top_tools = sorted(
            by_tool.items(),
            key=lambda kv: -float(kv[1].get("input_growth") or kv[1].get("attributed_input") or 0),
        )[:3]
        pct = int(TOOL_RESULT_TRIM_FRAC * 100)
        for name, t in top_tools:
            growth = int(t.get("input_growth") or 0)
            count = int(t.get("count") or 0)
            if count < 1 or growth <= 0:
                continue
            desired = int(growth * TOOL_RESULT_TRIM_FRAC)
            saving = ledger.claim(desired)
            overlaps = ""
            if saving < desired:
                overlaps = f"capped by ledger (wanted {desired}); overlaps prior claims"
            wins.append(
                Win(
                    title=f"Shrink tool `{name}` results",
                    saving_est_tokens=saving,
                    measured=(
                        f"{count} uses; ~{growth} prompt-token growth on tool_result requests "
                        f"(total volume); claiming ~{pct}% trim"
                    ),
                    rationale="Attributed to the request carrying the tool_result (incl. cache tokens)",
                    suggestion="Prefer smaller tool outputs; cache results.",
                    method=(
                        f"estimate: trim tool result size by ~{pct}% of measured "
                        "prompt growth (input+cache_read+cache_write)"
                    ),
                    overlaps=overlaps,
                )
            )

    # Routines before per-bot wins: schedule is Grok's top lever, and when
    # --usage-total is set it defines the ledger budget.
    routines = data.get("routines") or []
    if routines:
        total_runs = sum(float(r.get("est_runs_per_week") or 0) for r in routines)
        if total_runs > 0:
            usage_total = data.get("usage_total")
            if usage_total:
                desired = int(float(usage_total) * 0.5)
                saving = ledger.claim(desired)
                overlaps = ""
                if saving < desired:
                    overlaps = f"capped by ledger (wanted {desired})"
                wins.append(
                    Win(
                        title="Widen routine schedules",
                        saving_est_tokens=saving,
                        measured=(
                            f"{len(routines)} routines; ~{total_runs:.1f} est. runs/week; "
                            f"usage total {usage_total}"
                            + (f" ({data.get('usage_period')})" if data.get("usage_period") else "")
                        ),
                        rationale="Docs name schedule as the top lever",
                        suggestion="Widen intervals; drop chatty triggers; skip test runs.",
                        method="estimate: cut routine-driven usage ~50% of provided usage total",
                        overlaps=overlaps,
                    )
                )
            else:
                wins.append(
                    Win(
                        title="Widen routine schedules",
                        saving_est_tokens=0,
                        measured=f"{len(routines)} routines; ~{total_runs:.1f} est. runs/week",
                        rationale="Schedules known but no usage total to convert into tokens",
                        suggestion="Provide --usage-total to estimate token share.",
                        method="saving unknown — runs/week measured; need usage total",
                        ask=True,
                    )
                )
    elif host == "grok":
        wins.append(
            Win(
                title="List routines and Usage totals",
                saving_est_tokens=0,
                measured="no routine schedules provided",
                rationale="Grok docs name schedule as the top lever, but no measured routine data yet",
                suggestion=(
                    "Ask the bot to list routines; ask the user for Usage page numbers; "
                    "re-run with --routines-json and --usage-total."
                ),
                method="saving unknown",
                ask=True,
            )
        )

    by_bot = data.get("by_bot") or {}
    if by_bot:
        ranked_bots = sorted(by_bot.items(), key=lambda kv: -float(kv[1].get("est_tokens") or 0))
        for agent_id, b in ranked_bots[:2]:
            label = agent_id if len(agent_id) <= 16 else agent_id[:12] + "…"
            msg_n = int(b.get("stored_message_count") or b.get("message_count") or 0)
            always_bytes = int(b.get("always_context_bytes") or 0)
            always_tok = int(b.get("always_context_tokens") or 0)
            bot_volume = int(b.get("est_tokens") or 0)
            # Prefer saving-unknown: context×messages is not the same unit as the
            # message-text ledger budget, and Grok has no per-call token data.
            # Never claim more than this bot's own estimated volume.
            measured = (
                f"{always_bytes} always-loaded context bytes "
                f"(~{always_tok} tok from desc+memory; skill catalogs also load every turn); "
                f"{msg_n} stored messages (not model calls); "
                f"this bot's message-text volume ~{bot_volume} est. tokens"
            )
            wins.append(
                Win(
                    title=f"Trim always-loaded context for bot `{label}`",
                    saving_est_tokens=0,
                    measured=measured,
                    rationale=(
                        "Grok exposes no per-call tokens; context×messages would mix units "
                        "with the message-text volume budget"
                    ),
                    suggestion=(
                        "Shorten this bot's description and memory; uninstall unused "
                        "plugins/skills (catalogs load every turn)."
                    ),
                    method=(
                        "saving unknown — measured context bytes + stored message count "
                        "(not model calls); no per-call token data"
                    ),
                    ask=True,
                )
            )

    if data.get("missing_csv"):
        wins.append(
            Win(
                title="Provide Cursor Usage CSV",
                saving_est_tokens=0,
                measured="no CSV / Admin API data",
                rationale="Cloud usage is the priority source for Cursor",
                suggestion="Download Usage CSV from the dashboard and re-run with --csv.",
                method="saving unknown",
                ask=True,
            )
        )

    return rank_wins(wins, limit=5)


def run_token_audit(
    roots: Roots,
    csv_path: Optional[Path] = None,
    use_admin_api: bool = False,
    admin_start: Optional[str] = None,
    admin_end: Optional[str] = None,
    usage_total: Optional[float] = None,
    usage_period: Optional[str] = None,
    routines: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if roots.host == "claude":
        data = collect_claude_tokens(roots)
    elif roots.host == "cursor":
        data = collect_cursor_tokens(
            roots,
            csv_path=csv_path,
            use_admin_api=use_admin_api,
            admin_start=admin_start,
            admin_end=admin_end,
        )
    else:
        data = collect_grok_tokens(
            roots,
            usage_total=usage_total,
            usage_period=usage_period,
            routines=routines,
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
