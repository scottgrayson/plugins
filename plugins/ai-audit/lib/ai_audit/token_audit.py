"""Token / cost collectors. Cloud first, local second. Never scrape login tokens."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from .common import Win, est_tokens, is_secret_path, rank_wins, safe_read_text
from .detect import Roots


def _num(row: dict[str, str], *keys: str) -> float:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(str(row[k]).replace(",", "").replace("$", ""))
            except ValueError:
                continue
    return 0.0


def parse_cursor_usage_csv(path: Path) -> dict[str, Any]:
    """
    Parse a Cursor dashboard Usage CSV export.

    Column names vary; we accept common aliases. Never required Admin API.
    """
    text = safe_read_text(path)
    if text is None:
        return {"error": f"cannot read CSV: {path}", "events": [], "by_model": {}}

    # Strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.DictReader(text.splitlines())
    events: list[dict[str, Any]] = []
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "input": 0.0,
            "output": 0.0,
            "cache_read": 0.0,
            "cache_write": 0.0,
            "total_tokens": 0.0,
            "cost": 0.0,
            "count": 0.0,
        }
    )
    for raw in reader:
        # Normalize keys
        row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in raw.items()}
        model = (
            row.get("model")
            or row.get("model_name")
            or row.get("llm")
            or "unknown"
        )
        inp = _num(row, "input", "input_tokens", "input_with_cache_write", "prompt_tokens")
        out = _num(row, "output", "output_tokens", "completion_tokens")
        cache_read = _num(row, "cache_read", "cache_read_tokens", "cached_input")
        cache_write = _num(row, "cache_write", "cache_write_tokens", "input_cache_write")
        total = _num(row, "total", "total_tokens") or (inp + out + cache_read + cache_write)
        cost = _num(row, "cost", "total_cost", "charged", "price")
        date = row.get("date") or row.get("timestamp") or row.get("created_at") or ""
        ev = {
            "date": date,
            "model": model,
            "input": inp,
            "output": out,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "total_tokens": total,
            "cost": cost,
        }
        events.append(ev)
        m = by_model[model]
        m["input"] += inp
        m["output"] += out
        m["cache_read"] += cache_read
        m["cache_write"] += cache_write
        m["total_tokens"] += total
        m["cost"] += cost
        m["count"] += 1

    return {
        "source": str(path),
        "event_count": len(events),
        "events": events,
        "by_model": {k: dict(v) for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total_tokens"])},
    }


def fetch_cursor_admin_usage(api_key: Optional[str] = None) -> Optional[dict[str, Any]]:
    """
    Optional team Admin API. Key from env CURSOR_ADMIN_API_KEY or argument.
    Never required. Never scrapes IDE DBs.
    """
    key = api_key or os.environ.get("CURSOR_ADMIN_API_KEY") or os.environ.get("CURSOR_API_KEY")
    if not key:
        return None
    # Do not log the key.
    url = "https://api.cursor.com/teams/filtered-usage-events"
    req = urllib.request.Request(
        url,
        data=json.dumps({"page": 1, "pageSize": 100}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return {"error": f"admin API request failed: {type(e).__name__}", "events": []}

    # Normalize into by_model without echoing secrets
    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "total_tokens": 0.0, "cost": 0.0, "count": 0.0, "automations": 0.0, "cloud_agents": 0.0}
    )
    events = data.get("usageEvents") or data.get("events") or data.get("data") or []
    if not isinstance(events, list):
        events = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        model = str(ev.get("model") or "unknown")
        usage = ev.get("tokenUsage") or {}
        if not isinstance(usage, dict):
            usage = {}
        m = by_model[model]
        m["input"] += float(usage.get("inputTokens") or usage.get("input") or 0)
        m["output"] += float(usage.get("outputTokens") or usage.get("output") or 0)
        m["cache_read"] += float(usage.get("cacheReadTokens") or usage.get("cacheRead") or 0)
        m["cache_write"] += float(usage.get("cacheWriteTokens") or usage.get("cacheWrite") or 0)
        m["total_tokens"] += m["input"] + m["output"]  # incremental already in loop — fix below
        m["cost"] += float(ev.get("chargedCents") or usage.get("totalCents") or 0) / 100.0
        m["count"] += 1
        if ev.get("automationId"):
            m["automations"] += 1
        if ev.get("cloudAgentId") or ev.get("isHeadless"):
            m["cloud_agents"] += 1

    # Recompute total_tokens cleanly
    clean = {}
    for model, m in by_model.items():
        clean[model] = {
            "input": m["input"],
            "output": m["output"],
            "cache_read": m["cache_read"],
            "cache_write": m["cache_write"],
            "total_tokens": m["input"] + m["output"] + m["cache_read"] + m["cache_write"],
            "cost": m["cost"],
            "count": m["count"],
            "automations": m["automations"],
            "cloud_agents": m["cloud_agents"],
        }
    return {
        "source": "admin_api",
        "event_count": len(events),
        "by_model": dict(sorted(clean.items(), key=lambda kv: -kv[1]["total_tokens"])),
        "events": [],  # omit raw events (may contain emails)
    }


def _parse_claude_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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
                msg = obj.get("message") or obj
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage") or obj.get("usage")
                if not isinstance(usage, dict):
                    continue
                model = str(msg.get("model") or obj.get("model") or "unknown")
                tools = []
                content = msg.get("content") or []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tools.append(str(block.get("name") or "tool"))
                rows.append(
                    {
                        "model": model,
                        "input": int(usage.get("input_tokens") or 0),
                        "output": int(usage.get("output_tokens") or 0),
                        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
                        "tools": tools,
                        "path": str(path),
                    }
                )
    except OSError:
        return rows
    return rows


def collect_claude_tokens(roots: Roots) -> dict[str, Any]:
    home = roots.claude_home or (roots.home / ".claude")
    projects = home / "projects"
    # Also ~/.config/claude/projects
    alt = roots.home / ".config" / "claude" / "projects"
    jsonl_files: list[Path] = []
    for base in (projects, alt):
        if base.is_dir():
            jsonl_files.extend(sorted(base.rglob("*.jsonl")))

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total_tokens": 0, "count": 0}
    )
    by_tool: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "attributed_input": 0}
    )
    sessions = 0
    for path in jsonl_files:
        # Skip tool-results blobs that aren't session transcripts if huge — still parse usage if present
        rows = _parse_claude_jsonl(path)
        if rows:
            sessions += 1
        for r in rows:
            m = by_model[r["model"]]
            m["input"] += r["input"]
            m["output"] += r["output"]
            m["cache_read"] += r["cache_read"]
            m["cache_write"] += r["cache_write"]
            m["total_tokens"] += r["input"] + r["output"] + r["cache_read"] + r["cache_write"]
            m["count"] += 1
            for t in r["tools"]:
                by_tool[t]["count"] += 1
                by_tool[t]["attributed_input"] += r["input"]

    return {
        "source": "local_jsonl",
        "transcript_files": len(jsonl_files),
        "sessions_with_usage": sessions,
        "by_model": {k: dict(v) for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["total_tokens"])},
        "by_tool": {k: dict(v) for k, v in sorted(by_tool.items(), key=lambda kv: -kv[1]["count"])},
        "estimate": False,
        "notes": [
            "Local session JSONL only — cloud/web Console usage is not on disk.",
            "Dollar figures omitted; rank by tokens. List-price $ is not your bill.",
            "Transcripts expire after cleanupPeriodDays (default 30).",
        ],
    }


def collect_cursor_tokens(
    roots: Roots,
    csv_path: Optional[Path] = None,
    use_admin_api: bool = True,
) -> dict[str, Any]:
    notes = [
        "No local token data in the IDE DB — use a dashboard Usage CSV (cloud agents + automations).",
        "Never scrape session tokens from state.vscdb.",
    ]
    csv_data = None
    if csv_path:
        csv_data = parse_cursor_usage_csv(Path(csv_path))
    else:
        # Look for usage.csv under --root / fixtures / cwd
        for candidate in (
            roots.project / "usage.csv",
            Path.cwd() / "usage.csv",
            (Path(roots.home) / "usage.csv"),
        ):
            if candidate.is_file():
                csv_data = parse_cursor_usage_csv(candidate)
                break

    admin = None
    if use_admin_api:
        admin = fetch_cursor_admin_usage()
        if admin and admin.get("error"):
            notes.append(f"Admin API skipped/failed: {admin['error']}")
            admin = None
        elif admin:
            notes.append("Included optional Admin API usage-events (key from env).")

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
        "by_tool": {},
        "event_count": primary.get("event_count", 0),
        "csv": csv_data.get("source") if csv_data else None,
        "admin": bool(admin),
        "notes": notes,
    }


def collect_grok_tokens(roots: Roots) -> dict[str, Any]:
    """
    Usage page only shows totals. Estimate per bot from message volume in
    search-index.db (counts + lengths only — never message body text in output)
    and routine placeholders.
    """
    sand = roots.sand_data
    notes = [
        "ESTIMATE: Grok Bot Usage & Billing shows weekly totals only — no per-bot/tool/model breakdown on disk.",
        "Model choice is host-picked; 'cheaper model' is not a user lever here.",
        "Lead with 'run routines less often' — docs name schedule as the top lever.",
    ]
    by_bot: dict[str, dict[str, float]] = {}
    if sand and (sand / "search-index.db").is_file():
        db_path = sand / "search-index.db"
        try:
            # Read-only URI
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cur = con.execute(
                    "SELECT agent_id, COUNT(*), COALESCE(SUM(LENGTH(body)), 0) "
                    "FROM messages GROUP BY agent_id"
                )
                for agent_id, count, total_len in cur.fetchall():
                    # Do not store message text — only aggregates
                    by_bot[str(agent_id)] = {
                        "message_count": float(count),
                        "chars": float(total_len),
                        "est_tokens": float(est_tokens(int(total_len))),
                    }
            finally:
                con.close()
        except sqlite3.Error as e:
            notes.append(f"search-index.db unreadable: {type(e).__name__}")

    # Routine inventory: agents/*/automations may be empty; still report structure
    routine_count = 0
    if sand and (sand / "agents").is_dir():
        for auto_dir in (sand / "agents").glob("*/automations"):
            if auto_dir.is_dir():
                routine_count += sum(1 for _ in auto_dir.iterdir())

    # Estimate relative cost share by message volume
    total_tokens = sum(v["est_tokens"] for v in by_bot.values()) or 1.0
    for v in by_bot.values():
        v["share"] = round(v["est_tokens"] / total_tokens, 4)

    return {
        "source": "estimate_message_volume",
        "estimate": True,
        "by_bot": dict(sorted(by_bot.items(), key=lambda kv: -kv[1]["est_tokens"])),
        "by_model": {},
        "by_tool": {},
        "routine_files_on_disk": routine_count,
        "notes": notes
        + [
            "Ask the user (or bot) for weekly Usage % and routine schedules; multiply routine count × frequency for ranking.",
            "Ask the bot to list routines from its info pane — they are server-side.",
        ],
    }


def rank_token_wins(host: str, data: dict[str, Any]) -> list[Win]:
    wins: list[Win] = []

    by_model = data.get("by_model") or {}
    if by_model:
        # Sort models by total tokens
        ranked = sorted(by_model.items(), key=lambda kv: -float(kv[1].get("total_tokens") or 0))
        if ranked:
            top_model, top = ranked[0]
            total = float(top.get("total_tokens") or 0)
            if total > 0:
                wins.append(
                    Win(
                        title=f"Reduce use of expensive/high-volume model `{top_model}`",
                        saving_est_tokens=int(total * 0.3),
                        measured=f"{int(total)} tokens across {int(top.get('count') or 0)} events",
                        rationale="Highest token volume by model in the usage window",
                        suggestion="Prefer a cheaper model for routine/automation work when quality allows.",
                    )
                )
            # Cache miss opportunity
            for model, m in ranked[:3]:
                cr = float(m.get("cache_read") or 0)
                inp = float(m.get("input") or 0)
                if inp > 0 and cr / max(inp + cr, 1) < 0.2 and inp > 1000:
                    wins.append(
                        Win(
                            title=f"Improve prompt-cache hit rate ({model})",
                            saving_est_tokens=int(inp * 0.2),
                            measured=f"cache_read={int(cr)} vs input={int(inp)}",
                            rationale="Low cache-read share means repeated static context is re-billed",
                            suggestion="Stabilize always-on context; trim volatile prefixes.",
                        )
                    )

        # Automation / cloud agent intensity (admin API fields)
        auto_events = sum(float(m.get("automations") or 0) for m in by_model.values())
        cloud_events = sum(float(m.get("cloud_agents") or 0) for m in by_model.values())
        if auto_events >= 3:
            wins.append(
                Win(
                    title="Run automations less often / narrow triggers",
                    saving_est_tokens=int(sum(float(m.get("total_tokens") or 0) for m in by_model.values()) * 0.25),
                    measured=f"{int(auto_events)} automation-linked events",
                    rationale="Each automation run resends full context",
                    suggestion="Widen schedule intervals; drop chatty triggers.",
                )
            )
        if cloud_events >= 3:
            wins.append(
                Win(
                    title="Tighten cloud-agent usage",
                    saving_est_tokens=int(sum(float(m.get("total_tokens") or 0) for m in by_model.values()) * 0.2),
                    measured=f"{int(cloud_events)} cloud-agent/headless events",
                    rationale="Cloud agents are a primary cloud cost driver",
                    suggestion="Batch work; avoid repeating the same large context.",
                )
            )

    by_tool = data.get("by_tool") or {}
    if by_tool:
        top_tools = sorted(by_tool.items(), key=lambda kv: -float(kv[1].get("count") or 0))[:3]
        for name, t in top_tools:
            if float(t.get("count") or 0) < 3:
                continue
            wins.append(
                Win(
                    title=f"Reduce heavy tool `{name}`",
                    saving_est_tokens=int(float(t.get("attributed_input") or 0) * 0.15),
                    measured=f"{int(t.get('count') or 0)} uses; attributed input ~{int(t.get('attributed_input') or 0)}",
                    rationale="Tool results feed the next request's input tokens",
                    suggestion="Cache results; prefer smaller tool outputs.",
                )
            )

    by_bot = data.get("by_bot") or {}
    if by_bot:
        ranked_bots = sorted(by_bot.items(), key=lambda kv: -float(kv[1].get("est_tokens") or 0))
        for agent_id, b in ranked_bots[:3]:
            wins.append(
                Win(
                    title=f"Lower traffic for bot `{agent_id[:12]}…`" if len(agent_id) > 12 else f"Lower traffic for bot `{agent_id}`",
                    saving_est_tokens=int(float(b.get("est_tokens") or 0) * 0.3),
                    measured=f"{int(b.get('message_count') or 0)} messages; ~{int(b.get('est_tokens') or 0)} est. tokens (from text length)",
                    rationale="Estimated from search-index message volume (not billed tokens)",
                    suggestion="Narrow routine schedules and chat triggers for this bot.",
                )
            )
        # Always surface the routine lever for Grok
        wins.append(
            Win(
                title="Run routines less often / narrow triggers",
                saving_est_tokens=max(
                    100,
                    int(sum(float(b.get("est_tokens") or 0) for b in by_bot.values()) * 0.4),
                ),
                measured=f"routine files on disk={data.get('routine_files_on_disk', 0)} (server-side routines may be higher)",
                rationale="Docs: hourly schedules or busy chat triggers can burn a week of usage in a day",
                suggestion="Ask the bot to list routines; reduce frequency; skip test runs.",
            )
        )

    if data.get("missing_csv"):
        wins.append(
            Win(
                title="Provide Cursor Usage CSV to unlock cloud ranking",
                saving_est_tokens=0,
                measured="no CSV / Admin API data",
                rationale="Cloud usage is the priority source for Cursor",
                suggestion="Download Usage CSV from the dashboard and re-run with --csv.",
            )
        )

    return rank_wins(wins, limit=5)


def run_token_audit(
    roots: Roots,
    csv_path: Optional[Path] = None,
    use_admin_api: bool = True,
) -> dict[str, Any]:
    if roots.host == "claude":
        data = collect_claude_tokens(roots)
    elif roots.host == "cursor":
        data = collect_cursor_tokens(roots, csv_path=csv_path, use_admin_api=use_admin_api)
    else:
        data = collect_grok_tokens(roots)

    wins = rank_token_wins(roots.host, data)
    summary: dict[str, Any] = {
        "source": data.get("source"),
        "estimate": bool(data.get("estimate")),
    }
    if data.get("event_count") is not None:
        summary["events"] = data["event_count"]
    if data.get("sessions_with_usage") is not None:
        summary["sessions_with_usage"] = data["sessions_with_usage"]
    if data.get("by_model"):
        summary["models"] = len(data["by_model"])
        top = next(iter(data["by_model"].items()))
        summary["top_model"] = f"{top[0]} ({int(top[1].get('total_tokens') or 0)} tokens)"
    if data.get("by_bot"):
        summary["bots"] = len(data["by_bot"])

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
