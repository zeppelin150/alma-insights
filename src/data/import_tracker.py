"""
Alma Insights — Import Tracker
Logs import runs and provides dedupe gate for incremental imports.
"""

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("alma.import_tracker")


def start_import_run(conn, source: str, mode: str, file_name: str = None) -> str:
    """Create an import_runs record. Returns run_id."""
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO import_runs (run_id, started_at, source, mode, file_name, status)
           VALUES (?, ?, ?, ?, ?, 'running')""",
        (run_id, started_at, source, mode, file_name),
    )
    conn.commit()
    logger.info("Import run started: %s (%s/%s)", run_id[:8], source, mode)
    return run_id


def complete_import_run(conn, run_id: str, stats: dict):
    """Mark an import run as completed with stats."""
    completed_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE import_runs
           SET completed_at = ?, tickets_seen = ?, tickets_new = ?,
               tickets_skipped = ?, status = 'completed'
           WHERE run_id = ?""",
        (
            completed_at,
            stats.get("tickets_seen", 0),
            stats.get("tickets_new", 0),
            stats.get("tickets_skipped", 0),
            run_id,
        ),
    )
    conn.commit()
    logger.info(
        "Import run completed: %s — %d new, %d skipped",
        run_id[:8],
        stats.get("tickets_new", 0),
        stats.get("tickets_skipped", 0),
    )


def fail_import_run(conn, run_id: str, error_msg: str):
    """Mark an import run as failed."""
    completed_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE import_runs
           SET completed_at = ?, status = 'failed', error_message = ?
           WHERE run_id = ?""",
        (completed_at, error_msg, run_id),
    )
    conn.commit()
    logger.warning("Import run failed: %s — %s", run_id[:8], error_msg)


def get_existing_ticket_ids(conn, table_prefix: str | None = None) -> set:
    """Return the set of all ticket_ids currently in the tickets table.

    When table_prefix is provided, queries the per-source table first,
    falling back to shared table if per-source table doesn't exist.
    """
    if table_prefix:
        try:
            rows = conn.execute(
                f"SELECT ticket_id FROM [{table_prefix}_tickets]"
            ).fetchall()
            return {r[0] for r in rows}
        except Exception:
            pass  # per-source table may not exist yet — fall through
    try:
        rows = conn.execute("SELECT ticket_id FROM tickets").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def filter_new_tickets(existing_ids: set, incoming_rows: list, id_column: str = "ticket_id"):
    """Return only rows whose ticket_id is not already in the database.

    Args:
        existing_ids: Set of ticket IDs already in the DB
        incoming_rows: List of dicts with ticket data
        id_column: Key name for the ticket ID field

    Returns:
        (new_rows, skipped_count)
    """
    new_rows = [r for r in incoming_rows if r.get(id_column) not in existing_ids]
    skipped = len(incoming_rows) - len(new_rows)
    return new_rows, skipped
