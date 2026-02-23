"""
Alma Insights — CSV Ingestion
Maps Lightdash CSV exports to internal database schema.
Handles conversation rebuilding from comment-level rows.
"""

import csv
import io
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from src.data.rebuild_utils import normalize_role, sort_events_chronologically


# ═══ LIGHTDASH COLUMN MAPPING ═══
# Maps known Lightdash column headers to internal field names.
# Lightdash headers are verbose; we normalize to short keys.
COLUMN_MAP = {
    # Ticket ID
    "zendesk ticket zendesk ticket id": "ticket_id",
    "zendesk ticket id": "ticket_id",
    "zendesk ticket ticket id": "ticket_id",
    "ticket id": "ticket_id",

    # Subject
    "zendesk ticket [pii fields] ticket subject [pii]": "subject",
    "ticket subject [pii]": "subject",
    "ticket subject": "subject",
    "subject": "subject",

    # TRC
    "zendesk ticket ticket reason code list": "trc_code",
    "ticket reason code list": "trc_code",
    "trc": "trc_code",

    # Status
    "zendesk ticket status": "status",
    "zendesk ticket ticket status": "status",
    "ticket status": "status",
    "status": "status",

    # CSAT
    "zendesk satisfaction (csat) rating satisfaction (csat) score": "csat_score",
    "satisfaction (csat) score": "csat_score",
    "csat score": "csat_score",
    "csat": "csat_score",

    # Created date
    "zendesk ticket created (est) day": "created_at",
    "zendesk ticket created day": "created_at",
    "ticket created (est) day": "created_at",
    "created day": "created_at",
    "created_at": "created_at",
    "created": "created_at",

    # Comment body
    "zendesk ticket comment [pii field] comment body [pii]": "comment_body",
    "comment body [pii]": "comment_body",
    "comment body": "comment_body",

    # Author role (end-user = customer, agent = employee, null/empty = bot)
    "zendesk user (ticket updater) user role": "author_role",
    "user (ticket updater) user role": "author_role",
    "zendesk ticket comment author role": "author_role",
    "comment author role": "author_role",
    "author role": "author_role",
    "user role": "author_role",
    "role": "author_role",

    # Event timestamp (raw EST string, can be NULL)
    "zendesk ticket update details created (est) raw": "event_timestamp_raw",
    "ticket update details created (est) raw": "event_timestamp_raw",
    "update details created (est) raw": "event_timestamp_raw",

    # Resolution times (for TRC Analytics only, not Conversations)
    "zendesk ticket assignment to resolution time in hours (calendar)": "assignment_to_resolution_hours",
    "assignment to resolution time in hours (calendar)": "assignment_to_resolution_hours",

    "zendesk ticket total resolution time in hours (calendar)": "total_resolution_hours",

    # Requester email (for repeat contact hashing)
    "zendesk user (requester) [pii field] user email [pii]": "requester_email",
    "user (requester) user email [pii]": "requester_email",
    "requester email": "requester_email",
    "requester_email": "requester_email",
    "total resolution time in hours (calendar)": "total_resolution_hours",

    "zendesk ticket first reply time in hours (calendar)": "first_reply_hours",
    "first reply time in hours (calendar)": "first_reply_hours",
}


def _normalize_header(h):
    """Normalize a CSV header for matching."""
    return h.strip().lower().replace("\n", " ").replace("  ", " ")


def _map_columns(headers):
    """Map CSV headers to internal field names. Returns {col_index: field_name}."""
    mapping = {}
    unmapped = []
    for i, h in enumerate(headers):
        norm = _normalize_header(h)
        if norm in COLUMN_MAP:
            mapping[i] = COLUMN_MAP[norm]
        else:
            unmapped.append(h.strip())
    return mapping, unmapped


def _parse_row(row, col_mapping):
    """Convert a CSV row to a dict using the column mapping."""
    record = {}
    for i, val in enumerate(row):
        if i in col_mapping:
            field = col_mapping[i]
            record[field] = val.strip() if val else ""
    return record


def ingest_csv(file_path, db, progress_callback=None, dataset_id=None):
    """
    Ingest a Lightdash CSV export into the database.

    Args:
        file_path: Path to the CSV file
        db: DatabaseManager instance
        progress_callback: Optional callable(message, percent) for UI updates
        dataset_id: Optional dataset ID for A/B testing (tags all ingested conversations)

    Returns:
        dict with ingestion stats
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")

    def _progress(msg, pct=None):
        if progress_callback:
            progress_callback(msg, pct)

    _progress("Reading CSV file...", 5)

    # Read and detect encoding
    raw = file_path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Could not decode CSV file. Try re-exporting from Lightdash as UTF-8.")

    reader = csv.reader(io.StringIO(text))
    headers = next(reader)

    # Map columns
    col_mapping, unmapped = _map_columns(headers)
    mapped_fields = set(col_mapping.values())

    # Validate required fields
    required = {"ticket_id", "comment_body"}
    missing = required - mapped_fields
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}\n"
            f"Mapped: {sorted(mapped_fields)}\n"
            f"Unmapped headers: {unmapped}"
        )

    _progress("Parsing rows...", 10)

    # Parse all rows
    rows = list(reader)
    total_rows = len(rows)

    # Group comment rows by ticket_id
    tickets = defaultdict(lambda: {
        "comments": [],
        "subject": "",
        "trc_code": "",
        "status": "",
        "csat_score": None,
        "created_at": "",
        "requester_email": "",
        "assignment_to_resolution_hours": None,
        "total_resolution_hours": None,
        "first_reply_hours": None,
    })

    _progress(f"Processing {total_rows} comment rows...", 15)

    for idx, row in enumerate(rows):
        record = _parse_row(row, col_mapping)
        tid = record.get("ticket_id", "").strip()
        if not tid:
            continue

        t = tickets[tid]

        # Ticket-level fields (take first non-empty value seen)
        if not t["subject"] and record.get("subject"):
            t["subject"] = record["subject"]
        if not t["trc_code"] and record.get("trc_code"):
            t["trc_code"] = record["trc_code"]
        if not t["status"] and record.get("status"):
            t["status"] = record["status"]
        if t["csat_score"] is None and record.get("csat_score"):
            try:
                t["csat_score"] = float(record["csat_score"])
            except (ValueError, TypeError):
                pass
        if not t["created_at"] and record.get("created_at"):
            t["created_at"] = record["created_at"]
        if not t["requester_email"] and record.get("requester_email"):
            t["requester_email"] = record["requester_email"]

        # Resolution time fields
        for time_field in ("assignment_to_resolution_hours", "total_resolution_hours", "first_reply_hours"):
            if t[time_field] is None and record.get(time_field):
                try:
                    t[time_field] = float(record[time_field])
                except (ValueError, TypeError):
                    pass

        # Comment
        body = record.get("comment_body", "").strip()
        if body:
            role = normalize_role(record.get("author_role", ""))

            t["comments"].append({
                "body": body,
                "created_at": record.get("created_at", ""),
                "event_ts_raw": record.get("event_timestamp_raw", ""),
                "role": role,
            })

        # Progress every 5000 rows
        if idx % 5000 == 0 and idx > 0:
            pct = 15 + int((idx / total_rows) * 50)
            _progress(f"Processing row {idx:,} / {total_rows:,}...", min(pct, 65))

    _progress(f"Rebuilding {len(tickets)} conversations...", 70)

    # Clear existing data
    db.conn.execute("DELETE FROM conversations")
    db.conn.execute("DELETE FROM comments")
    db.conn.execute("DELETE FROM tickets")
    db.conn.commit()

    # Insert tickets and rebuild conversations
    inserted = 0
    for tid, t in tickets.items():
        # Determine TRC label (for now, code = label; can be enriched later)
        trc_code = t["trc_code"]
        trc_label = trc_code  # Same until we have a TRC lookup table

        # Hash requester email for repeat contact detection
        requester_hash = ""
        requester_email = t.get("requester_email", "")
        if requester_email:
            from src.data.entity_extractor import hash_email
            requester_hash = hash_email(requester_email)

        # Insert ticket record
        ticket_data = {
            "ticket_id": tid,
            "subject": t["subject"],
            "trc_code": trc_code,
            "trc_label": trc_label,
            "status": t["status"].lower() if t["status"] else "",
            "priority": "",
            "channel": "",
            "csat_score": t["csat_score"],
            "created_at": t["created_at"],
            "updated_at": "",
            "solved_at": "",
            "requester_name": "",
            "requester_email": "",
            "assignee_name": "",
            "group_name": "",
            "tags": [],
            "custom_fields": {},
            "assignment_to_resolution_hours": t["assignment_to_resolution_hours"],
            "total_resolution_hours": t["total_resolution_hours"],
            "first_reply_hours": t["first_reply_hours"],
        }
        db.upsert_ticket(ticket_data)
        # Set requester_hash separately (column added in P3.0 migration)
        if requester_hash:
            try:
                db.conn.execute(
                    "UPDATE tickets SET requester_hash = ? WHERE ticket_id = ?",
                    (requester_hash, tid)
                )
            except Exception:
                pass

        # Build thread from comments — sorted chronologically with NULL handling
        comments = t["comments"]

        # Wrap for shared sort utility
        wrapped = []
        for ci, c in enumerate(comments):
            wrapped.append({
                "ts_raw": c.get("event_ts_raw") or c.get("created_at") or None,
                "order": ci,
                "comment": c,
            })
        sort_events_chronologically(wrapped)

        thread_lines = []
        customer_count = 0
        agent_count = 0
        bot_count = 0

        for ci, w in enumerate(wrapped):
            c = w["comment"]
            role = c.get("role", "bot")
            if role == "customer":
                display_role = "CUSTOMER"
                customer_count += 1
            elif role == "agent":
                display_role = "AGENT"
                agent_count += 1
            else:
                display_role = "BOT"
                bot_count += 1

            ts_display = w["ts_parsed"].strftime("%Y-%m-%d %H:%M") if w["ts_parsed"] else ""
            synthetic_marker = " ⏱" if w.get("is_synthetic") else ""
            thread_lines.append(f"[{ts_display}{synthetic_marker}] {display_role}:\n{c['body']}")

            db.upsert_comment({
                "comment_id": f"{tid}-{ci}",
                "ticket_id": tid,
                "author_name": display_role.title(),
                "author_role": role,
                "body": c["body"],
                "is_public": True,
                "created_at": ts_display,
            })

        full_thread = "\n\n---\n\n".join(thread_lines)
        preview = (thread_lines[0][:200] + "...") if thread_lines and len(thread_lines[0]) > 200 else (thread_lines[0] if thread_lines else "")

        conv_data = {
            "ticket_id": tid,
            "subject": t["subject"],
            "trc_code": trc_code,
            "trc_label": trc_label,
            "status": t["status"].lower() if t["status"] else "",
            "csat_score": t["csat_score"],
            "created_at": t["created_at"],
            "solved_at": "",
            "message_count": len(comments),
            "client_messages": customer_count,
            "agent_messages": agent_count,
            "full_thread": full_thread,
            "thread_preview": preview,
        }
        db.upsert_conversation(conv_data)
        # Tag with dataset_id if provided (P3.0 A/B testing)
        if dataset_id is not None:
            try:
                db.conn.execute(
                    "UPDATE conversations SET dataset_id = ? WHERE ticket_id = ?",
                    (dataset_id, tid)
                )
            except Exception:
                pass

        inserted += 1
        if inserted % 1000 == 0:
            pct = 70 + int((inserted / len(tickets)) * 25)
            _progress(f"Rebuilt {inserted:,} / {len(tickets):,} conversations...", min(pct, 95))

    _progress("Finalizing...", 96)
    db.commit()

    _progress("Building search index...", 97)
    db.rebuild_fts_index()

    # ── Entity extraction (P3.0) ──
    _progress("Extracting entities...", 98)
    try:
        from src.data.entity_extractor import load_entity_dictionaries, extract_entities
        payer_dict, product_dict = load_entity_dictionaries()
        for tid, t in tickets.items():
            text = t["subject"] + " " + " ".join(
                c["body"] for c in t["comments"][:3]  # First 3 comments only for speed
            )
            entities = extract_entities(text, payer_dict, product_dict)
            if entities.get("payers") or entities.get("product_areas"):
                entity_records = []
                for p in entities.get("payers", []):
                    entity_records.append({
                        "entity_type": "payer",
                        "entity_value": p,
                        "confidence": 0.8,
                    })
                for pa in entities.get("product_areas", []):
                    entity_records.append({
                        "entity_type": "product_area",
                        "entity_value": pa,
                        "confidence": 0.8,
                    })
                db.save_ticket_entities(tid, entity_records)
    except Exception:
        pass  # Entity extraction is non-critical

    # ── Post-ingestion: N-gram provisional classification ──
    try:
        from src.data.ngram_matcher import NgramMatcher
        matcher = NgramMatcher(db)
        # Only runs if sub_patterns table has active entries
        has_patterns = db.conn.execute(
            "SELECT 1 FROM sub_patterns WHERE tier IN ('active','probationary') LIMIT 1"
        ).fetchone()
        if has_patterns:
            new_ticket_ids = list(tickets.keys())
            matcher.classify_batch(new_ticket_ids)
            _progress("Provisional sub-pattern classification complete", 98)
    except Exception:
        pass  # N-gram matching is non-critical

    stats = {
        "total_csv_rows": total_rows,
        "tickets_created": len(tickets),
        "comments_stored": sum(len(t["comments"]) for t in tickets.values()),
        "mapped_fields": sorted(mapped_fields),
        "unmapped_headers": unmapped,
        "has_resolution_times": "assignment_to_resolution_hours" in mapped_fields,
    }

    _progress(f"Done — {stats['tickets_created']:,} conversations loaded", 100)

    db.log_analysis("csv_import", {
        "file": file_path.name,
        "rows": total_rows,
        "tickets": stats["tickets_created"],
    }, ticket_count=stats["tickets_created"])

    return stats
