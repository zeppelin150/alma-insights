"""
Alma Insights — Conversation Rebuild
Post-ingestion job: reads raw_ingestion_rows, groups by ticket_id,
sorts chronologically (with NULL timestamp handling), and writes
rebuilt conversations for the UI.
"""

import json
from collections import defaultdict
from typing import Optional, Callable

from src.data.run_logger import RunLogger
from src.data.rebuild_utils import parse_timestamp, normalize_role, sort_events_chronologically


def rebuild_conversations(
    db,
    logger: Optional[RunLogger] = None,
    progress_callback: Optional[Callable] = None,
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
    if log:
        log.rebuild("start", detail="Starting conversation rebuild from raw rows")

    def _progress(msg, pct=None):
        if progress_callback:
            progress_callback(msg, pct)

    _progress("Reading raw rows...", 0)

    # Check if raw_ingestion_rows table exists
    table_exists = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_ingestion_rows'"
    ).fetchone()

    if not table_exists:
        if log:
            log.warn("rebuild", "No raw_ingestion_rows table found — skipping rebuild")
        return {"conversations_rebuilt": 0, "null_timestamps": 0}

    # Read all raw rows, ordered by rowid for stable receipt order
    rows = db.conn.execute(
        "SELECT rowid, ticket_id, raw_json FROM raw_ingestion_rows ORDER BY rowid"
    ).fetchall()

    if not rows:
        if log:
            log.warn("rebuild", "No raw rows to rebuild")
        return {"conversations_rebuilt": 0, "null_timestamps": 0}

    _progress(f"Grouping {len(rows):,} rows by ticket...", 10)

    # ── Group by ticket_id ──
    tickets = defaultdict(list)
    for row in rows:
        ticket_id = row["ticket_id"]
        raw = json.loads(row["raw_json"])
        raw["_rowid"] = row["rowid"]  # receipt order
        tickets[ticket_id].append(raw)

    total_tickets = len(tickets)
    total_null_ts = 0
    total_synthetic = 0

    _progress(f"Rebuilding {total_tickets:,} conversations...", 15)

    # ── Clear existing conversation data ──
    db.conn.execute("DELETE FROM conversations")
    db.conn.execute("DELETE FROM comments")
    db.conn.execute("DELETE FROM tickets")
    db.conn.commit()

    rebuilt = 0
    for ticket_id, events in tickets.items():
        if not ticket_id:
            continue

        # ── Parse timestamps + sort chronologically ──
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
        total_null_ts += null_count
        total_synthetic += null_count

        # ── Extract ticket-level fields (from first event) ──
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

        # Resolution times (take first non-null)
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

        # ── Upsert ticket ──
        db.upsert_ticket({
            "ticket_id": ticket_id,
            "subject": subject,
            "trc_code": trc_code,
            "trc_label": trc_code,  # Code = label until lookup table
            "status": status,
            "priority": "",
            "channel": "",
            "csat_score": csat_score,
            "created_at": created_at,
            "updated_at": "",
            "solved_at": "",
            "requester_name": "",
            "requester_email": "",
            "assignee_name": "",
            "group_name": "",
            "tags": [],
            "custom_fields": {},
        })

        # ── Build thread from sorted events ──
        thread_lines = []
        customer_count = 0
        agent_count = 0
        bot_count = 0

        for ci, evt in enumerate(wrapped_events):
            raw = evt["raw"]
            body = _field(raw, "comments_body", "comment_body", "").strip()
            if not body:
                continue

            # Role normalization
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
            })

        if not thread_lines:
            continue

        full_thread = "\n\n---\n\n".join(thread_lines)
        preview = thread_lines[0][:200] + ("..." if len(thread_lines[0]) > 200 else "")
        msg_count = len(thread_lines)

        db.upsert_conversation({
            "ticket_id": ticket_id,
            "subject": subject,
            "trc_code": trc_code,
            "trc_label": trc_code,
            "status": status,
            "csat_score": csat_score,
            "created_at": created_at,
            "solved_at": "",
            "message_count": msg_count,
            "client_messages": customer_count,
            "agent_messages": agent_count,
            "full_thread": full_thread,
            "thread_preview": preview,
        })

        rebuilt += 1

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
#  HELPERS
# ═══════════════════════════════════

def _field(row: dict, *keys, default=""):
    """Extract a field from a raw row, trying multiple key names."""
    for key in keys:
        val = row.get(key)
        if val is not None and val != "":
            return val
    return default
