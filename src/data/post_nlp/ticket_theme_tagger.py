"""Tag tickets to NLP findings via ticket_theme_tags junction table.

For each finding in a scan, identifies matching tickets by:
  1. sub_pattern IN (top_sub_patterns) → confidence 1.0
  2. friction_type = dominant_friction_type → confidence 0.8

Handles NULL top_sub_patterns gracefully (logs warning, skips).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def tag_tickets_to_findings(conn, scan_id: str) -> int:
    """Link tickets to findings for a scan.

    Args:
        conn: SQLite connection (or db_manager with .conn attribute).
        scan_id: The scan whose findings to process.

    Returns:
        Count of tags written.
    """
    # Support both raw connection and db_manager object
    db_conn = getattr(conn, "conn", conn)

    findings = _load_findings(db_conn, scan_id)
    if not findings:
        logger.info("No findings for scan %s, skipping tagging", scan_id)
        return 0

    now = datetime.now(timezone.utc).isoformat()
    written = 0

    for finding in findings:
        finding_id = finding["finding_id"]
        theme_id = finding_id  # Use finding_id as the theme_id

        sub_patterns = _parse_json_list(finding.get("top_sub_patterns"))
        friction_type = finding.get("dominant_friction_type")

        if not sub_patterns and not friction_type:
            logger.debug("Finding %s has no sub_patterns or friction_type, skipping", finding_id)
            continue

        # Tag by sub_pattern match (confidence 1.0)
        written += _tag_by_sub_patterns(
            db_conn, scan_id, finding_id, theme_id,
            sub_patterns, now,
        )

        # Tag by friction_type match (confidence 0.8, skip already-tagged)
        written += _tag_by_friction_type(
            db_conn, scan_id, finding_id, theme_id,
            friction_type, sub_patterns, now,
        )

    db_conn.execute("DELETE FROM ticket_theme_tags WHERE 0")  # no-op to keep conn active
    try:
        db_conn.commit()
    except Exception:
        # If conn is a db_manager, it may handle commits differently
        pass

    logger.info("Wrote %d ticket-theme tags for scan %s", written, scan_id)
    return written


def _load_findings(conn, scan_id: str) -> list[dict]:
    """Load findings for a scan."""
    rows = conn.execute(
        "SELECT finding_id, finding_type, title, "
        "top_sub_patterns, dominant_friction_type, top_trcs "
        "FROM nlp_findings WHERE scan_id = ?",
        (scan_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _tag_by_sub_patterns(
    conn, scan_id: str, finding_id: str, theme_id: str,
    sub_patterns: list[str], now: str,
) -> int:
    """Tag tickets whose sub_pattern matches one of the finding's top_sub_patterns."""
    if not sub_patterns:
        return 0

    placeholders = ", ".join("?" for _ in sub_patterns)
    tickets = conn.execute(
        f"SELECT ticket_id FROM ticket_index "
        f"WHERE last_seen_scan_id = ? "
        f"AND sub_pattern IN ({placeholders})",
        [scan_id] + sub_patterns,
    ).fetchall()

    written = 0
    for row in tickets:
        ticket_id = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO ticket_theme_tags "
            "(ticket_id, theme_id, finding_id, confidence, scan_id, tagged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, theme_id, finding_id, 1.0, scan_id, now),
        )
        written += 1
    return written


def _tag_by_friction_type(
    conn, scan_id: str, finding_id: str, theme_id: str,
    friction_type: str | None, already_tagged_patterns: list[str],
    now: str,
) -> int:
    """Tag tickets by friction_type match (lower confidence).

    Skips tickets already tagged by sub_pattern match.
    """
    if not friction_type:
        return 0

    # Exclude tickets already tagged by sub_pattern
    exclude_clause = ""
    params = [scan_id, friction_type]
    if already_tagged_patterns:
        placeholders = ", ".join("?" for _ in already_tagged_patterns)
        exclude_clause = f"AND sub_pattern NOT IN ({placeholders})"
        params.extend(already_tagged_patterns)

    tickets = conn.execute(
        f"SELECT ticket_id FROM ticket_index "
        f"WHERE last_seen_scan_id = ? "
        f"AND friction_type = ? "
        f"{exclude_clause}",
        params,
    ).fetchall()

    written = 0
    for row in tickets:
        ticket_id = row[0]
        conn.execute(
            "INSERT OR REPLACE INTO ticket_theme_tags "
            "(ticket_id, theme_id, finding_id, confidence, scan_id, tagged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, theme_id, finding_id, 0.8, scan_id, now),
        )
        written += 1
    return written


def _parse_json_list(raw: str | None) -> list[str]:
    """Parse a JSON array string, returning empty list on failure."""
    if not raw:
        return []
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(v) for v in result if v]
        return []
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse JSON list: %s", raw[:100] if raw else "None")
        return []


def _row_to_dict(row) -> dict:
    """Convert sqlite3.Row or tuple to dict."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    cols = ["finding_id", "finding_type", "title",
            "top_sub_patterns", "dominant_friction_type", "top_trcs"]
    return {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
