"""
Alma Insights — Context Injector (Build 11.0)

Builds the [SYSTEM CONTEXT] block prepended to every chat message
sent to Gemini CLI.  Provides data scope, filter state, and
available query tools.
"""

import sqlite3
import logging
from datetime import datetime

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.context_injector")


def build_context(page_state: dict, db_path: str) -> str:
    """Assemble context string from page state + DB queries."""
    parts = ["[SYSTEM CONTEXT]"]

    page = page_state.get("page", "unknown")
    parts.append(f"The user is on the {page} page.")

    trc_filter = page_state.get("trc_filter", "All TRCs")
    date_start = page_state.get("date_start", "")
    date_end = page_state.get("date_end", "")
    if trc_filter or date_start:
        parts.append(f"Active filters: TRC={trc_filter}, Date range={date_start} to {date_end}.")

    # Query data scope
    scope = _get_data_scope(db_path, trc_filter, date_start, date_end)
    parts.append(
        f"Data scope: {scope['tickets']} tickets in ticket_index, "
        f"{scope['patterns']} classified patterns, "
        f"{scope['anomalies']} active anomalies."
    )

    eph = _get_ephemeral_status(db_path)
    parts.append(f"Ephemeral data loaded: {'yes' if eph else 'no'}.")

    recency = _get_scan_recency(db_path)
    if recency:
        parts.append(f"Last NLP scan: {recency}.")

    # Available tools
    parts.append("")
    parts.append("You have access to these query tools via run_shell_command:")
    parts.append("- python -m src.tools.alma_query tickets [--trc X] [--friction-type X] [--from X] [--to X] [--keyword X] [--limit N]")
    parts.append("- python -m src.tools.alma_query ticket-detail --id X")
    parts.append("- python -m src.tools.alma_query trends [--metric X] [--by X] [--months N]")
    parts.append("- python -m src.tools.alma_query anomalies [--severity X]")
    parts.append("- python -m src.tools.alma_query compare --period-a X --period-b X")
    parts.append("- python -m src.tools.alma_query insights [--status X]")
    parts.append("")
    parts.append("When you make quantitative claims, be specific and cite ticket IDs where possible.")
    parts.append("[END CONTEXT]")

    return "\n".join(parts)


def _get_data_scope(db_path: str, trc_filter: str, date_start: str, date_end: str) -> dict:
    """Get ticket count, pattern count, anomaly count from ticket_index."""
    try:
        conn = get_connection(db_path)
        conditions = []
        params = []

        if trc_filter and trc_filter != "All TRCs":
            conditions.append("trc_code = ?")
            params.append(trc_filter)
        if date_start:
            conditions.append("ticket_created_date >= ?")
            params.append(date_start)
        if date_end:
            conditions.append("ticket_created_date <= ?")
            params.append(date_end)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        tickets = conn.execute(f"SELECT COUNT(*) FROM ticket_index{where}", params).fetchone()[0]
        patterns = conn.execute(f"SELECT COUNT(DISTINCT sub_pattern) FROM ticket_index{where}", params).fetchone()[0]
        anomalies = conn.execute(
            f"SELECT COUNT(*) FROM ticket_index{where}{' AND ' if conditions else ' WHERE '}anomaly_flag IS NOT NULL",
            params,
        ).fetchone()[0]
        conn.close()
        return {"tickets": tickets, "patterns": patterns, "anomalies": anomalies}
    except Exception:
        return {"tickets": 0, "patterns": 0, "anomalies": 0}


def _get_ephemeral_status(db_path: str) -> bool:
    """Check if warehouse has any conversation data."""
    try:
        conn = get_connection(db_path)
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(conn, SourceRegistry(conn))
        count = wq.get_ticket_count()
        conn.close()
        return count > 0
    except Exception:
        return False


def _get_scan_recency(db_path: str) -> str | None:
    """Get the most recent scan date."""
    try:
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT MAX(started_at) FROM nlp_scan_runs WHERE status = 'complete'"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None
