"""
Alma Insights — Conversation Rebuild
Post-ingestion job: reads raw_ingestion_rows, groups by ticket_id,
sorts chronologically (with NULL timestamp handling), and writes
rebuilt conversations for the UI.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Optional, Callable

from src.data.run_logger import RunLogger
from src.data.rebuild_utils import parse_timestamp, normalize_role, sort_events_chronologically


def rebuild_conversations(
    db,
    logger: Optional[RunLogger] = None,
    progress_callback: Optional[Callable] = None,
    skip_rebuild: bool = False,
    table_prefix: str | None = None,
) -> dict:
    """
    Rebuild conversations from raw_ingestion_rows table.

    Timestamp rules:
    - Parse "ticket_update_details_created_est_raw" if present
    - NULL/unparsable timestamps sort to START of conversation
    - Multiple NULLs ordered by receipt order (rowid)
    - Synthetic timestamps: min_ts - (n_nulls - idx) seconds

    Returns dict with rebuild stats.
    """
    log = logger

    def _progress(msg, pct=None):
        if progress_callback:
            progress_callback(msg, pct)

    if skip_rebuild:
        if log:
            log.rebuild("skip", detail="Skip rebuild — self-contained conversations")
        return {"conversations_rebuilt": 0, "null_timestamps": 0, "skipped": True}

    if log:
        log.rebuild("start", detail="Starting conversation rebuild from raw rows")

    rows = _fetch_raw_rows(db, log, _progress)
    if rows is None:
        return {"conversations_rebuilt": 0, "null_timestamps": 0}

    _progress(f"Grouping {len(rows):,} rows by ticket...", 10)
    tickets = _group_by_ticket(rows)

    total_tickets = len(tickets)
    _progress(f"Rebuilding {total_tickets:,} conversations...", 15)

    # Dedupe: only rebuild tickets not already in the database
    from src.data.import_tracker import get_existing_ticket_ids

    existing_ids = get_existing_ticket_ids(db.conn, table_prefix=table_prefix)
    original_count = len(tickets)
    tickets = {tid: events for tid, events in tickets.items() if tid not in existing_ids}
    skipped = original_count - len(tickets)

    import logging
    logging.getLogger("alma.conversation_rebuild").info(
        "Rebuild dedupe: %d new tickets, %d skipped", len(tickets), skipped
    )

    rebuilt = 0
    total_null_ts = 0
    total_synthetic = 0

    for ticket_id, events in tickets.items():
        if not ticket_id:
            continue

        result = _rebuild_single_ticket(db, ticket_id, events, table_prefix=table_prefix)
        if result is None:
            continue

        rebuilt += 1
        total_null_ts += result["null_count"]
        total_synthetic += result["null_count"]

        if rebuilt % 500 == 0:
            db.commit()
            pct = int(rebuilt / total_tickets * 100)
            _progress(f"Rebuilt {rebuilt:,}/{total_tickets:,} conversations...", min(pct, 99))

    db.commit()

    if log:
        log.rebuild("complete",
                     detail=f"Rebuilt {rebuilt:,} conversations, "
                            f"{total_null_ts} NULL timestamps ({total_synthetic} synthetic)")
        log.conversations_rebuilt = rebuilt
        if total_null_ts > 0:
            log.warn("null_timestamps",
                     f"{total_null_ts} events had NULL/missing timestamps "
                     f"(placed at conversation start)")

    _progress(f"Done — {rebuilt:,} conversations rebuilt", 100)

    return {
        "conversations_rebuilt": rebuilt,
        "null_timestamps": total_null_ts,
        "synthetic_timestamps": total_synthetic,
        "total_events": len(rows),
    }


# ═══════════════════════════════════
#  SUB-FUNCTIONS
# ═══════════════════════════════════

def _fetch_raw_rows(db, log, _progress):
    """Read raw_ingestion_rows table. Returns list of rows or None."""
    _progress("Reading raw rows...", 0)

    table_exists = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_ingestion_rows'"
    ).fetchone()

    if not table_exists:
        if log:
            log.warn("rebuild", "No raw_ingestion_rows table found — skipping rebuild")
        return None

    rows = db.conn.execute(
        "SELECT rowid, ticket_id, raw_json FROM raw_ingestion_rows ORDER BY rowid"
    ).fetchall()

    if not rows:
        if log:
            log.warn("rebuild", "No raw rows to rebuild")
        return None

    return rows


def _group_by_ticket(rows):
    """Group raw rows by ticket_id, preserving receipt order."""
    tickets = defaultdict(list)
    for row in rows:
        ticket_id = row["ticket_id"]
        raw = json.loads(row["raw_json"])
        raw["_rowid"] = row["rowid"]
        tickets[ticket_id].append(raw)
    return tickets


def _extract_ticket_fields(wrapped_events):
    """Extract ticket-level metadata from sorted events."""
    first = wrapped_events[0]["raw"]
    subject = _field(first, "tickets_subject", "subject", "")
    trc_code = _field(first, "tickets_trc_code", "trc_code", "")
    status = _field(first, "tickets_status", "status", "").lower()
    csat_raw = _field(first, "tickets_csat_score", "csat_score", None)
    created_at = _field(first, "tickets_created_at_day", "created_at", "")

    try:
        csat_score = float(csat_raw) if csat_raw is not None and csat_raw != "" else None
    except (ValueError, TypeError):
        csat_score = None

    return {
        "subject": subject,
        "trc_code": trc_code,
        "status": status,
        "csat_score": csat_score,
        "created_at": created_at,
    }


def _extract_resolution_times(wrapped_events):
    """Extract resolution time fields from first non-null event."""
    assign_res = None
    total_res = None
    first_reply = None

    for evt in wrapped_events:
        r = evt["raw"]
        if assign_res is None:
            v = _field(r, "tickets_assignment_to_resolution_hours", "assignment_to_resolution_hours", None)
            if v is not None and v != "":
                try:
                    assign_res = float(v)
                except (ValueError, TypeError):
                    pass
        if total_res is None:
            v = _field(r, "tickets_total_resolution_hours", "total_resolution_hours", None)
            if v is not None and v != "":
                try:
                    total_res = float(v)
                except (ValueError, TypeError):
                    pass
        if first_reply is None:
            v = _field(r, "tickets_first_reply_hours", "first_reply_hours", None)
            if v is not None and v != "":
                try:
                    first_reply = float(v)
                except (ValueError, TypeError):
                    pass

    return assign_res, total_res, first_reply


def _build_thread(db, ticket_id, wrapped_events, table_prefix=None):
    """Build conversation thread from sorted events. Returns (thread_text, preview, counts) or None."""
    thread_lines = []
    customer_count = 0
    agent_count = 0
    bot_count = 0

    for ci, evt in enumerate(wrapped_events):
        raw = evt["raw"]
        body = _field(raw, "comments_body", "comment_body", "").strip()
        if not body:
            continue

        raw_role = _field(raw, "comments_author_role", "author_role", "")
        role = normalize_role(raw_role)

        if role == "customer":
            display_role = "CUSTOMER"
            customer_count += 1
        elif role == "agent":
            display_role = "AGENT"
            agent_count += 1
        else:
            display_role = "BOT"
            bot_count += 1

        author = _field(raw, "comments_author_name", "author_name", display_role.title())
        ts_display = evt["ts_parsed"].strftime("%Y-%m-%d %H:%M") if evt["ts_parsed"] else ""
        synthetic_marker = " ⏱" if evt.get("is_synthetic") else ""

        thread_lines.append(
            f"[{ts_display}{synthetic_marker}] {display_role} ({author}):\n{body}"
        )

        db.upsert_comment({
            "comment_id": f"{ticket_id}-{ci}",
            "ticket_id": ticket_id,
            "author_name": author,
            "author_role": role,
            "body": body,
            "is_public": True,
            "created_at": ts_display,
        }, table_prefix=table_prefix)

    if not thread_lines:
        return None

    full_thread = "\n\n---\n\n".join(thread_lines)
    preview = thread_lines[0][:200] + ("..." if len(thread_lines[0]) > 200 else "")
    msg_count = len(thread_lines)

    return full_thread, preview, msg_count, customer_count, agent_count


def _rebuild_single_ticket(db, ticket_id, events, table_prefix=None):
    """Rebuild a single ticket's conversation. Returns stats dict or None."""
    # Parse timestamps + sort chronologically
    wrapped_events = []
    for evt in events:
        ts_raw = evt.get("ticket_update_details_created_est_raw", None)
        wrapped_events.append({
            "raw": evt,
            "ts_raw": ts_raw,
            "order": evt.get("_rowid", 0),
        })

    sort_events_chronologically(wrapped_events)
    null_count = sum(1 for e in wrapped_events if e["is_synthetic"])

    # Extract ticket-level fields
    fields = _extract_ticket_fields(wrapped_events)
    assign_res, total_res, first_reply = _extract_resolution_times(wrapped_events)

    # Upsert ticket
    db.upsert_ticket({
        "ticket_id": ticket_id,
        "subject": fields["subject"],
        "trc_code": fields["trc_code"],
        "trc_label": fields["trc_code"],
        "status": fields["status"],
        "priority": "",
        "channel": "",
        "csat_score": fields["csat_score"],
        "created_at": fields["created_at"],
        "updated_at": "",
        "solved_at": "",
        "requester_name": "",
        "requester_email": "",
        "assignee_name": "",
        "group_name": "",
        "tags": [],
        "custom_fields": {},
        "assignment_to_resolution_hours": assign_res,
        "total_resolution_hours": total_res,
        "first_reply_hours": first_reply,
    }, table_prefix=table_prefix)

    # Build thread
    result = _build_thread(db, ticket_id, wrapped_events, table_prefix=table_prefix)
    if result is None:
        return None

    full_thread, preview, msg_count, customer_count, agent_count = result

    db.upsert_conversation({
        "ticket_id": ticket_id,
        "subject": fields["subject"],
        "trc_code": fields["trc_code"],
        "trc_label": fields["trc_code"],
        "status": fields["status"],
        "csat_score": fields["csat_score"],
        "created_at": fields["created_at"],
        "solved_at": "",
        "message_count": msg_count,
        "client_messages": customer_count,
        "agent_messages": agent_count,
        "full_thread": full_thread,
        "thread_preview": preview,
        "dataset_id": 0,
    }, table_prefix=table_prefix)

    return {"null_count": null_count}


# ═══════════════════════════════════
#  HELPERS
# ═══════════════════════════════════

def _field(row: dict, *keys, default=""):
    """Extract a field from a raw row, trying multiple key names."""
    for key in keys:
        val = row.get(key)
        if val is not None and val != "":
            return val
    return default
