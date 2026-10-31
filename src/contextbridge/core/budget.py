"""Budget monitor (spec §10, BLUEPRINT §10). Provider-independent core: readings in, assessments out.

Verified Claude Code status-line fields (docs, 2026-10-31): `context_window.used_percentage`
(input tokens only; may be null early in a session), `context_window.context_window_size`,
`rate_limits.five_hour|seven_day.{used_percentage, resets_at (Unix seconds)}`. `rate_limits` only
exists for Pro/Max/Ultra (or a spend-limited gateway), only after the first API response, and each
window can be absent. Absent is never treated as zero, and one budget is never inferred from another.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .db.connection import now_iso, write_tx

LEVELS = (70, 80, 85, 90, 95)
THROTTLE_S = 15
FINAL_REVIEW, TOOL_CALL, RESPONSE, NEXT_OP = 1500, 500, 500, 3000
LEVEL_ACTION = {
    70: "warn",
    80: "sync unsaved decisions and state now (remember / update_state)",
    85: "call checkpoint now",
    90: "recommend prepare_handoff to the user at the next safe point",
    95: "emergency: call checkpoint immediately, then prepare_handoff (mode quick)",
}


@dataclass
class Reading:
    source: str
    session_key: str
    ts: str
    context_used_pct: float | None = None
    context_used_tokens: int | None = None
    context_window_tokens: int | None = None
    five_hour_pct: float | None = None
    five_hour_reset_at: int | None = None
    seven_day_pct: float | None = None
    seven_day_reset_at: int | None = None
    project_id: int | None = None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _obj(d: dict[str, Any], key: str) -> dict[str, Any]:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def parse_statusline(obj: dict[str, Any]) -> Reading:
    cw, rl = _obj(obj, "context_window"), _obj(obj, "rate_limits")
    five, seven = _obj(rl, "five_hour"), _obj(rl, "seven_day")
    pct, size = _num(cw.get("used_percentage")), _int(cw.get("context_window_size"))
    return Reading(
        source="claude_code_statusline", session_key=str(obj.get("session_id") or "unknown")[:120], ts=now_iso(),
        context_used_pct=pct, context_window_tokens=size,
        context_used_tokens=int(size * pct / 100) if pct is not None and size else None,
        five_hour_pct=_num(five.get("used_percentage")), five_hour_reset_at=_int(five.get("resets_at")),
        seven_day_pct=_num(seven.get("used_percentage")), seven_day_reset_at=_int(seven.get("resets_at")))


_COLS = ("context_used_pct", "context_used_tokens", "context_window_tokens", "five_hour_pct", "five_hour_reset_at",
         "seven_day_pct", "seven_day_reset_at")


def record(conn: sqlite3.Connection, r: Reading) -> bool:
    """Store a reading, throttled: at most one per THROTTLE_S per session unless a percentage moved ≥ 1 point."""
    last = conn.execute("SELECT * FROM telemetry WHERE session_key = ? ORDER BY id DESC LIMIT 1",
                        (r.session_key,)).fetchone()
    if last is not None:
        age = (datetime.fromisoformat(r.ts) - datetime.fromisoformat(last["ts"])).total_seconds()
        moved = any((getattr(r, c) is None) != (last[c] is None) or
                    (getattr(r, c) is not None and last[c] is not None and abs(getattr(r, c) - last[c]) >= 1)
                    for c in ("context_used_pct", "five_hour_pct", "seven_day_pct"))
        if age < THROTTLE_S and not moved and last["project_id"] == r.project_id:
            return False
    with write_tx(conn):
        conn.execute(f"INSERT INTO telemetry(session_key, project_id, ts, source, {', '.join(_COLS)})"
                     f" VALUES (?,?,?,?,{','.join('?' * len(_COLS))})",
                     (r.session_key, r.project_id, r.ts, r.source, *(getattr(r, c) for c in _COLS)))
        conn.execute("DELETE FROM telemetry WHERE session_key = ? AND id NOT IN (SELECT id FROM telemetry WHERE"
                     " session_key = ? ORDER BY id DESC LIMIT 200)", (r.session_key, r.session_key))
    return True


def latest(conn: sqlite3.Connection, *, session_key: str | None = None,
           project_id: int | None = None) -> dict[str, Any] | None:
    if session_key:
        row = conn.execute("SELECT * FROM telemetry WHERE session_key = ? ORDER BY id DESC LIMIT 1",
                           (session_key,)).fetchone()
    elif project_id is not None:
        row = conn.execute("SELECT * FROM telemetry WHERE project_id = ? ORDER BY id DESC LIMIT 1",
                           (project_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def age_seconds(ts: str) -> float:
    return max(0.0, (datetime.now().astimezone() - datetime.fromisoformat(ts)).total_seconds())


def _window_expired(reset_at: int | None) -> bool:
    return reset_at is not None and reset_at <= datetime.now(timezone.utc).timestamp()


def handoff_cost(window: int | None) -> dict[str, int]:
    """Tokens the MODEL needs to finish a handoff. Small by design (P3: the server assembles it)."""
    reserve = int((window or 200_000) * 0.10)
    required = FINAL_REVIEW + 2 * TOOL_CALL + RESPONSE + reserve
    return {"required": required, "low": int(required * 0.8), "high": int(required * 1.3), "reserve": reserve}


def assess(reading: dict[str, Any] | None, *, stale_after_s: int = 300,
           levels: tuple[int, ...] = LEVELS) -> dict[str, Any]:
    """Highest crossed level across context / 5h / 7d, and the feasibility of a handoff."""
    if reading is None:
        return {"telemetry": "unavailable", "level": 0, "budget": None, "feasible": None,
                "recommendation": "none", "stale": None}
    stale = age_seconds(reading["ts"]) > stale_after_s
    budgets = {"context": reading.get("context_used_pct"),
               "five_hour": None if _window_expired(reading.get("five_hour_reset_at")) else reading.get("five_hour_pct"),
               "seven_day": None if _window_expired(reading.get("seven_day_reset_at")) else reading.get("seven_day_pct")}
    level, which = 0, None
    for name, pct in budgets.items():
        if pct is None:
            continue
        crossed = max((lv for lv in levels if pct >= lv), default=0)
        if crossed > level:
            level, which = crossed, name
    cost = handoff_cost(reading.get("context_window_tokens"))
    feasible = None
    if reading.get("context_used_pct") is not None and reading.get("context_window_tokens"):
        available = reading["context_window_tokens"] * (1 - reading["context_used_pct"] / 100)
        feasible = available >= cost["required"]
        if available < cost["required"] + NEXT_OP and level < 85:  # spec §10.4: act now regardless of percentage
            level, which = 85, "context (feasibility)"
    rec = LEVEL_ACTION.get(level, "none") if level >= 80 else ("none" if level < 70 else "warn")
    return {"telemetry": "available", "stale": stale, "level": level, "budget": which, "budgets": budgets,
            "feasible": feasible, "est_handoff_cost": cost, "recommendation": rec,
            "recommended_mode": "quick" if (feasible is False or level >= 95) else "standard",
            "reading_age_s": int(age_seconds(reading["ts"]))}


def as_dict(r: Reading) -> dict[str, Any]:
    return asdict(r)


# ---------------------------------------------------------------- status line rendering

def _pct(v: float | None) -> str:
    return "–" if v is None else f"{v:.0f}%"


def status_line(reading: Reading, assessment: dict[str, Any], *, slug: str | None, ckpt_age_min: int | None) -> str:
    parts = [f"ctx {_pct(reading.context_used_pct)}", f"5h {_pct(reading.five_hour_pct)}",
             f"7d {_pct(reading.seven_day_pct)}"]
    if slug is None:
        parts.append("no linked project")
    else:
        parts.append("ckpt " + ("never" if ckpt_age_min is None else f"{ckpt_age_min}m ago"))
        f = assessment.get("feasible")
        parts.append("handoff " + ("✓" if f else "⚠ low room" if f is False else "?"))
        parts.append(slug)
    line = " · ".join(parts)
    lv = assessment.get("level", 0)
    if lv >= 85:
        line = f"⚠ {lv}% {line}"
    return line


def budget_status(conn: sqlite3.Connection, project_id: int | None, session_key: str | None, *,
                  stale_after_s: int = 300) -> dict[str, Any]:
    """`get_budget_status`: latest reading + assessment + context health. Honest 'unavailable'."""
    r = latest(conn, session_key=session_key) if session_key else latest(conn, project_id=project_id) \
        if project_id is not None else None
    a = assess(r, stale_after_s=stale_after_s)

    def window(pct_key: str, reset_key: str) -> Any:
        if r is None or r.get(pct_key) is None or _window_expired(r.get(reset_key)):
            return "unavailable"
        reset = r.get(reset_key)
        return {"used_pct": r[pct_key], "resets_at": datetime.fromtimestamp(reset, timezone.utc).astimezone()
                .isoformat(timespec="minutes") if reset else None}

    out: dict[str, Any] = {
        "telemetry": a["telemetry"], "stale": a["stale"],
        "context": "unavailable" if r is None or r.get("context_used_pct") is None else {
            "used_pct": r["context_used_pct"], "window_tokens": r.get("context_window_tokens"),
            "used_tokens": r.get("context_used_tokens")},
        "five_hour": window("five_hour_pct", "five_hour_reset_at"),
        "seven_day": window("seven_day_pct", "seven_day_reset_at"),
        "level": a["level"], "feasible": a["feasible"],
        "est_handoff_cost_range": a.get("est_handoff_cost") or handoff_cost(None),
        "recommendation": a["recommendation"], "recommended_mode": a.get("recommended_mode", "standard"),
    }
    if r is not None:
        out["reading_at"], out["reading_age_s"], out["source"] = r["ts"], int(age_seconds(r["ts"])), r["source"]
    if a["telemetry"] == "unavailable":
        out["note"] = ("No usage telemetry from this client (Claude Desktop and claude.ai don't expose any). "
                       "Rely on checkpoint_status: checkpoint every 20-30 min and when the user mentions limits.")
    elif a["stale"]:
        out["note"] = f"Latest reading is {out['reading_age_s']} s old (stale); numbers may be out of date."
    if project_id is not None:
        from .status import checkpoint_status
        cs = checkpoint_status(conn, project_id)
        out["context_health"] = {
            "minutes_since_checkpoint": cs["minutes_since_checkpoint"],
            "writes_since_checkpoint": cs["writes_since_checkpoint"],
            "open_conflicts": conn.execute("SELECT count(*) FROM conflicts WHERE project_id = ? AND status = 'open'",
                                           (project_id,)).fetchone()[0],
            "uncaptured_sessions": conn.execute("SELECT count(*) FROM sessions WHERE project_id = ? AND capture = 'none'",
                                                (project_id,)).fetchone()[0]}
    return out
