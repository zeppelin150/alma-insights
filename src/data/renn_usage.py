"""Renn usage metering — recording and reading the assistant's LLM activity.

The Claude CLI reports real per-turn usage (input/output tokens and, on API
logins, ``total_cost_usd``) in its terminal ``result`` event; the CLI bridge's
StreamParser already captures them. This module supplies the two halves that
turn those numbers into the enablement Usage tab:

- :class:`RennUsageRecorder` — a bridge-compatible usage sink (the same
  ``log_call`` / ``estimate_tokens`` surface ``UsageTracker`` offers) that
  writes one ``gemini_usage`` row per turn with ``source='renn_chat'``.
  It opens its own WAL connection per write so it is safe from the chat
  worker thread and needs no DatabaseManager.
- Query helpers the Usage panel renders from (:func:`usage_summary`).

``gemini_usage`` is the existing usage ledger (date/hour/source/model/tokens/
cost); Renn rows are distinguished purely by ``source``, so no migration is
needed and the product side's scan accounting is untouched.

Costs: on a subscription (claude.ai) CLI login the CLI reports no cost, so
``cost_usd`` records 0.0 — the Usage tab says so rather than implying free.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.renn_usage")

# Sources counted as "Renn activity" by the Usage tab. Chat is the only
# metered source today; add here (not in the panel) when jobs get metered.
RENN_SOURCES: tuple[str, ...] = ("renn_chat",)

RENN_CHAT_SOURCE = "renn_chat"


class RennUsageRecorder:
    """Bridge-compatible usage sink for Renn's CLI turns.

    Satisfies the surface ``ClaudeCliBridge._log_usage_if_configured`` uses
    (``log_call`` + ``estimate_tokens``) without dragging in DatabaseManager:
    each write opens a fresh WAL connection via the connection factory, so a
    recorder built on the UI thread logs safely from the chat worker thread.
    """

    def __init__(self, db_path, source: str = RENN_CHAT_SOURCE):
        self.db_path = str(db_path)
        self.source = source

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """~1 token per 4 chars — same fallback estimate UsageTracker uses.
        Only reached when the CLI reported no counts for a turn."""
        if not text:
            return 0
        return max(1, int(len(text) * 0.25))

    def log_call(self, source: str | None = None, tokens_in: int = 0,
                 tokens_out: int = 0, model: str = "",
                 scan_id: str | None = None,
                 cost_usd: float | None = None) -> None:
        """Write one usage row. Never raises — metering must not break chat."""
        now = datetime.now()
        try:
            conn = get_connection(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO gemini_usage "
                    "(date, hour, source, scan_id, tokens_in, tokens_out, "
                    " cost_usd, api_calls, model, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (now.strftime("%Y-%m-%d"), now.hour,
                     source or self.source, scan_id,
                     int(tokens_in or 0), int(tokens_out or 0),
                     float(cost_usd or 0.0), model or "",
                     now.isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — best-effort metering
            logger.debug("renn usage log failed: %s", exc)


# ── read side (the Usage panel renders exactly this) ─────────────────

def _src_clause() -> str:
    marks = ", ".join("?" for _ in RENN_SOURCES)
    return f"source IN ({marks})"


def _totals(conn, since: str | None) -> dict:
    sql = (f"SELECT COUNT(*), COALESCE(SUM(tokens_in),0), "
           f"COALESCE(SUM(tokens_out),0), COALESCE(SUM(cost_usd),0.0) "
           f"FROM gemini_usage WHERE {_src_clause()}")
    args: list = list(RENN_SOURCES)
    if since:
        sql += " AND date >= ?"
        args.append(since)
    turns, tin, tout, cost = conn.execute(sql, args).fetchone()
    return {"turns": int(turns), "tokens_in": int(tin),
            "tokens_out": int(tout), "cost_usd": float(cost)}


def daily_series(conn, days: int = 14, *, today: datetime | None = None) -> list[tuple[str, int]]:
    """[(YYYY-MM-DD, turn_count)] for the trailing ``days`` days, zero-filled
    and oldest-first — ready for a bar chart."""
    now = today or datetime.now()
    start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = dict(conn.execute(
        f"SELECT date, COUNT(*) FROM gemini_usage WHERE {_src_clause()} "
        "AND date >= ? GROUP BY date",
        (*RENN_SOURCES, start)).fetchall())
    out = []
    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        out.append((day, int(rows.get(day, 0))))
    return out


def recent_turns(conn, limit: int = 8) -> list[dict]:
    """Newest-first recent activity rows for the panel's list."""
    rows = conn.execute(
        f"SELECT date, hour, source, model, tokens_in, tokens_out, cost_usd "
        f"FROM gemini_usage WHERE {_src_clause()} "
        "ORDER BY created_at DESC LIMIT ?",
        (*RENN_SOURCES, int(limit))).fetchall()
    out = []
    for date, hour, source, model, tin, tout, cost in rows:
        out.append({
            "date": str(date or ""),
            "hour": hour,
            "source": str(source or ""),
            "model": str(model or ""),
            "tokens_in": int(tin or 0),
            "tokens_out": int(tout or 0),
            "cost_usd": float(cost or 0.0),
        })
    return out


def usage_summary(conn, *, today: datetime | None = None) -> dict:
    """Everything the Usage panel shows, in one call (one connection).

    Guarded: any failure returns an all-zero summary rather than raising —
    a broken usage read must never break Settings.
    """
    now = today or datetime.now()
    try:
        return {
            "today": _totals(conn, now.strftime("%Y-%m-%d")),
            "week": _totals(conn, (now - timedelta(days=6)).strftime("%Y-%m-%d")),
            "month": _totals(conn, (now - timedelta(days=29)).strftime("%Y-%m-%d")),
            "all_time": _totals(conn, None),
            "series": daily_series(conn, 14, today=now),
            "recent": recent_turns(conn, 8),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("renn usage summary failed: %s", exc)
        zero = {"turns": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        return {"today": dict(zero), "week": dict(zero), "month": dict(zero),
                "all_time": dict(zero), "series": [], "recent": []}
