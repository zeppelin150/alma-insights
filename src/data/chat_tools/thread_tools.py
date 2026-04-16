"""Thread-reading chat tool handlers.

PHI Level 2 — these return full conversation text (PII-redacted).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_MAX_THREAD_LEN = 8_000      # 8K per single thread
_MAX_BATCH_THREAD_LEN = 4_000  # 4K per thread in batch mode
_MAX_BATCH_TOTAL = 20_000    # 20K total for batch
_MAX_BATCH_SIZE = 5           # Max tickets per batch call

# Simple PII patterns for redaction
_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN-REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL-REDACTED]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE-REDACTED]"),
]


def handle_read_thread(conn, args: dict, session_filters: dict) -> dict:
    """Read the full conversation thread for one ticket.

    Applies PII redaction and 8K truncation.
    """
    ticket_id = args.get("ticket_id", "")
    if not ticket_id:
        return {"error": "ticket_id is required"}

    # Scope validation: if session has filters, check ticket is in scope
    scope_err = _validate_scope(conn, ticket_id, session_filters)
    if scope_err:
        return scope_err

    from src.data.source_registry import SourceRegistry
    from src.data.warehouse_query import WarehouseQuery
    _reg = SourceRegistry(conn)
    _wq = WarehouseQuery(conn, _reg)
    _source_id = session_filters.get("source_id") if session_filters else None
    _rows = _wq.query_conversations_raw(
        "SELECT ticket_id, subject, trc_code, status, created_at, "
        "message_count, full_thread FROM {table} "
        "WHERE ticket_id = ?",
        (ticket_id,),
        source_id=_source_id,
    )
    row = _rows[0] if _rows else None

    if row is None:
        return {"error": f"No conversation found for ticket {ticket_id}"}

    thread_text = row[6] or ""
    redacted = _redact_pii(thread_text)
    truncated = _truncate(redacted, _MAX_THREAD_LEN)

    return {
        "ticket_id": row[0],
        "subject": row[1],
        "trc_code": row[2],
        "status": row[3],
        "created_at": row[4],
        "message_count": row[5],
        "thread": truncated,
        "truncated": len(redacted) > _MAX_THREAD_LEN,
    }


def handle_read_threads_batch(conn, args: dict, session_filters: dict) -> dict:
    """Read threads for up to 5 tickets at once.

    Each thread capped at 4K, total output capped at 20K.
    """
    ticket_ids = args.get("ticket_ids", [])
    if not ticket_ids:
        return {"error": "ticket_ids is required (list of strings)"}
    if len(ticket_ids) > _MAX_BATCH_SIZE:
        return {"error": f"Max {_MAX_BATCH_SIZE} tickets per batch, got {len(ticket_ids)}"}

    threads = []
    total_len = 0

    for tid in ticket_ids:
        scope_err = _validate_scope(conn, tid, session_filters)
        if scope_err:
            threads.append({"ticket_id": tid, **scope_err})
            continue

        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        _reg2 = SourceRegistry(conn)
        _wq2 = WarehouseQuery(conn, _reg2)
        _source_id2 = session_filters.get("source_id") if session_filters else None
        _rows2 = _wq2.query_conversations_raw(
            "SELECT ticket_id, subject, trc_code, message_count, full_thread "
            "FROM {table} WHERE ticket_id = ?",
            (tid,),
            source_id=_source_id2,
        )
        row = _rows2[0] if _rows2 else None

        if row is None:
            threads.append({"ticket_id": tid, "error": "Not found"})
            continue

        thread_text = row[4] or ""
        redacted = _redact_pii(thread_text)

        # Per-thread cap
        available = min(_MAX_BATCH_THREAD_LEN, _MAX_BATCH_TOTAL - total_len)
        if available <= 0:
            threads.append({"ticket_id": tid, "error": "Batch size limit reached"})
            continue

        truncated = _truncate(redacted, available)
        total_len += len(truncated)

        threads.append({
            "ticket_id": row[0],
            "subject": row[1],
            "trc_code": row[2],
            "message_count": row[3],
            "thread": truncated,
            "truncated": len(redacted) > available,
        })

    return {"threads": threads, "count": len(threads)}


def _validate_scope(conn, ticket_id: str, session_filters: dict) -> dict | None:
    """Check ticket exists in session scope. Returns error dict or None."""
    if not session_filters:
        return None  # No scope restriction

    # Check ticket exists in ticket_index with matching filters
    from src.data.filter_engine import build_filter_query
    sql, params = build_filter_query(
        filters=session_filters,
        select_columns=["ti.ticket_id"],
        base_table="ticket_index",
    )
    sql += " AND ti.ticket_id = ?"
    params.append(ticket_id)

    row = conn.execute(sql, params).fetchone()
    if row is None:
        return {"error": f"Ticket {ticket_id} not in current session scope"}
    return None


def _redact_pii(text: str) -> str:
    """Apply PII redaction patterns to text."""
    result = text
    for pattern, replacement in _PII_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len chars, breaking at last newline."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_nl = cut.rfind("\n")
    if last_nl > max_len * 0.8:
        return cut[:last_nl] + "\n[... truncated]"
    return cut + "\n[... truncated]"
