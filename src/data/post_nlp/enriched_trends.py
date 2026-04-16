"""Compute enriched trends from ticket_index after NLP scan.

Aggregates tickets by dimension (friction_type, sub_pattern, sentiment)
and Monday-anchored ISO week period. Writes to enriched_trends table.

Period format: Monday-anchored dates (YYYY-MM-DD) — NOT strftime('%Y-%W')
which has a year-boundary rollover bug. Monday dates sort correctly as
plain strings and velocity computation across year boundaries is accurate.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Dimensions to aggregate
_DIMENSIONS = ["friction_type", "sub_pattern", "sentiment_polarity"]

# SQLite expression for Monday of the ISO week containing ticket_created_date
# date(..., 'weekday 0', '-6 days') gives the Monday of that week
_MONDAY_EXPR = "date(ticket_created_date, 'weekday 1', '-7 days')"


def compute_enriched_trends(conn, scan_id: str) -> int:
    """Compute and write enriched trend rows for a scan.

    Args:
        conn: SQLite connection.
        scan_id: The scan to aggregate.

    Returns:
        Count of rows written.
    """
    total_tickets = _get_scan_ticket_count(conn, scan_id)
    if total_tickets == 0:
        logger.warning("No tickets for scan %s, skipping enriched trends", scan_id)
        return 0

    written = 0
    for dimension in _DIMENSIONS:
        rows = _aggregate_dimension(conn, scan_id, dimension)
        periods = _compute_velocity(rows)
        written += _write_trends(conn, scan_id, dimension, periods, total_tickets)

    conn.commit()
    logger.info("Wrote %d enriched trend rows for scan %s", written, scan_id)
    return written


def _get_scan_ticket_count(conn, scan_id: str) -> int:
    """Get total ticket count for the scan."""
    row = conn.execute(
        "SELECT COUNT(*) FROM ticket_index WHERE last_seen_scan_id = ?",
        (scan_id,),
    ).fetchone()
    return row[0] if row else 0


def _aggregate_dimension(conn, scan_id: str, dimension: str) -> list[dict]:
    """Aggregate ticket counts by dimension value and Monday-anchored period."""
    sql = f"""
        SELECT {dimension} AS dim_value,
               {_MONDAY_EXPR} AS period,
               COUNT(*) AS cnt,
               GROUP_CONCAT(DISTINCT trc_code) AS trcs
        FROM ticket_index
        WHERE last_seen_scan_id = ?
          AND {dimension} IS NOT NULL
          AND ticket_created_date IS NOT NULL
        GROUP BY {dimension}, period
        ORDER BY {dimension}, period
    """
    rows = conn.execute(sql, (scan_id,)).fetchall()
    return [
        {
            "dim_value": r[0],
            "period": r[1],
            "count": r[2],
            "trcs": r[3],
        }
        for r in rows
    ]


def _compute_velocity(rows: list[dict]) -> list[dict]:
    """Add velocity (period-over-period change) to aggregated rows.

    Groups by dim_value, sorts by period (Monday date), and computes
    velocity as (current - previous) / previous. First period gets None.
    """
    by_dim: dict[str, list[dict]] = {}
    for r in rows:
        by_dim.setdefault(r["dim_value"], []).append(r)

    result = []
    for dim_value, periods in by_dim.items():
        sorted_periods = sorted(periods, key=lambda x: x["period"])
        prev_count = None
        for p in sorted_periods:
            if prev_count is not None and prev_count > 0:
                p["velocity"] = round((p["count"] - prev_count) / prev_count, 3)
            else:
                p["velocity"] = None
            prev_count = p["count"]
            result.append(p)

    return result


def _write_trends(
    conn, scan_id: str, dimension: str,
    periods: list[dict], total_tickets: int,
) -> int:
    """Write trend rows using INSERT OR REPLACE for idempotency."""
    written = 0
    for p in periods:
        pct = round(p["count"] / total_tickets, 4) if total_tickets > 0 else 0
        trc_breakdown = _build_trc_breakdown(p.get("trcs", ""))
        conn.execute(
            "INSERT OR REPLACE INTO enriched_trends "
            "(scan_id, dimension, dimension_value, period, "
            "ticket_count, pct_of_total, velocity, trc_breakdown) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id, dimension, p["dim_value"], p["period"],
                p["count"], pct, p["velocity"], trc_breakdown,
            ),
        )
        written += 1
    return written


def _build_trc_breakdown(trcs_csv: str) -> str:
    """Convert comma-separated TRC list to JSON object."""
    if not trcs_csv:
        return "{}"
    trcs = [t.strip() for t in trcs_csv.split(",") if t.strip()]
    # Simple count (each unique TRC mentioned once per GROUP_CONCAT DISTINCT)
    breakdown = {trc: 1 for trc in trcs}
    return json.dumps(breakdown)
