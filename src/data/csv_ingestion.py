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


def _parse_float(val):
    """Permissive float parser; returns None for blanks or non-numeric."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


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

    # ── Phase 1 enrichment columns (migration 016) ──
    # Insurance payer — canonical column + common aliases from test data / Lightdash
    "insurance_payer": "insurance_payer",
    "insurance payer": "insurance_payer",
    "insurer": "insurance_payer",
    "payer": "insurance_payer",
    "payer_name": "insurance_payer",
    "payer name": "insurance_payer",

    # Client / provider / agent IDs (Alma internal UUIDs, non-PHI)
    "client_id": "client_id",
    "client id": "client_id",
    "alma client id": "client_id",
    "provider_id": "provider_id",
    "provider id": "provider_id",
    "alma provider id": "provider_id",
    "agent_id": "agent_id",
    "agent id": "agent_id",
    "cs agent id": "agent_id",
    "cs agent": "agent_id",

    # Service state (2-char US state code where service was rendered)
    "service_state": "service_state",
    "service state": "service_state",
    "state": "service_state",
    "state code": "service_state",

    # Channel (email, chat, phone, sms, etc)
    "channel": "channel",
    "ticket channel": "channel",
    "contact channel": "channel",

    # Session date (date of clinical session if referenced in ticket)
    "session_date": "session_date",
    "session date": "session_date",
    "clinical session date": "session_date",

    # Dispute amount (USD, extracted later from ticket body in most sources)
    "dispute_amount_usd": "dispute_amount_usd",
    "dispute amount": "dispute_amount_usd",
    "dispute amount usd": "dispute_amount_usd",

    # Tags (semicolon-delimited per spec)
    "tags": "tags",
    "ticket tags": "tags",
    "ticket_tags": "tags",
    "labels": "tags",
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


# ═══════════════════════════════════
#  READ + ENCODE
# ═══════════════════════════════════

def _read_csv_with_encoding(file_path):
    """Read CSV file with encoding fallback. Returns (headers, rows)."""
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
    rows = list(reader)
    return headers, rows


# ═══════════════════════════════════
#  COLUMN MAPPING + VALIDATION
# ═══════════════════════════════════

def _resolve_columns(headers, column_override=None, allow_self_contained=False):
    """Resolve column mapping and validate required fields.
    Returns (col_mapping, mapped_fields, unmapped).

    When allow_self_contained=True (Kodif-style), full_thread replaces comment_body
    as the required conversation field.
    """
    if column_override:
        col_mapping = {}
        unmapped = []
        for i, h in enumerate(headers):
            norm = _normalize_header(h)
            if norm in column_override:
                col_mapping[i] = column_override[norm]
            else:
                unmapped.append(h.strip())
    else:
        col_mapping, unmapped = _map_columns(headers)

    mapped_fields = set(col_mapping.values())

    if allow_self_contained:
        required = {"ticket_id", "full_thread"}
    else:
        required = {"ticket_id", "comment_body"}
    missing = required - mapped_fields
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}\n"
            f"Mapped: {sorted(mapped_fields)}\n"
            f"Unmapped headers: {unmapped}"
        )

    return col_mapping, mapped_fields, unmapped


# ═══════════════════════════════════
#  GROUP BY TICKET
# ═══════════════════════════════════

def _group_rows_by_ticket(rows, col_mapping, _progress, total_rows):
    """Parse and group CSV rows by ticket_id. Returns tickets dict."""
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
        # Phase 1 enrichment (nullable; absent in legacy exports)
        "insurance_payer": None,
        "client_id": None,
        "provider_id": None,
        "agent_id": None,
        "service_state": None,
        "channel": None,
        "session_date": None,
        "dispute_amount_usd": None,
        "tags": None,   # raw string from CSV; normalized at write time
    })

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

        # Enrichment fields (first non-empty row wins, per ticket)
        for str_field in ("insurance_payer", "client_id", "provider_id",
                          "agent_id", "service_state", "channel",
                          "session_date", "tags"):
            if not t[str_field] and record.get(str_field):
                t[str_field] = record[str_field].strip() or None
        if t["dispute_amount_usd"] is None and record.get("dispute_amount_usd"):
            try:
                t["dispute_amount_usd"] = float(record["dispute_amount_usd"])
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

    return tickets


# ═══════════════════════════════════
#  WRITE TO DB
# ═══════════════════════════════════

def _write_tickets_to_db(db, tickets, dataset_id, _progress, table_prefix=None):
    """Write all tickets and conversations to the database.
    Additive: dedupe by ticket_id, only insert new tickets.
    When table_prefix is provided, also writes to per-source tables.
    """
    from src.data.import_tracker import get_existing_ticket_ids

    existing_ids = get_existing_ticket_ids(db.conn, table_prefix=table_prefix)
    new_tickets = {tid: t for tid, t in tickets.items() if tid not in existing_ids}
    skipped = len(tickets) - len(new_tickets)

    import logging
    logging.getLogger("alma.csv_ingestion").info(
        "Import dedupe: %d new, %d skipped (already exist)", len(new_tickets), skipped
    )

    inserted = 0
    for tid, t in new_tickets.items():
        _write_single_ticket(db, tid, t, dataset_id, table_prefix=table_prefix)

        inserted += 1
        if inserted % 1000 == 0:
            pct = 70 + int((inserted / len(new_tickets)) * 25) if new_tickets else 95
            _progress(f"Rebuilt {inserted:,} / {len(new_tickets):,} conversations...", min(pct, 95))

    return inserted


def _write_single_ticket(db, tid, t, dataset_id, table_prefix=None):
    """Write a single ticket, its comments, and conversation to the DB."""
    trc_code = t["trc_code"]

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
        "trc_label": trc_code,
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
    db.upsert_ticket(ticket_data, table_prefix=table_prefix)

    if requester_hash:
        try:
            db.conn.execute(
                "UPDATE tickets SET requester_hash = ? WHERE ticket_id = ?",
                (requester_hash, tid)
            )
        except Exception:
            pass

    # Build thread from comments — sorted chronologically
    comments = t["comments"]
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
        }, table_prefix=table_prefix)

    full_thread = "\n\n---\n\n".join(thread_lines)
    preview = (thread_lines[0][:200] + "...") if thread_lines and len(thread_lines[0]) > 200 else (thread_lines[0] if thread_lines else "")

    conv_data = {
        "ticket_id": tid,
        "subject": t["subject"],
        "trc_code": trc_code,
        "trc_label": trc_code,
        "status": t["status"].lower() if t["status"] else "",
        "csat_score": t["csat_score"],
        "created_at": t["created_at"],
        "solved_at": "",
        "message_count": len(comments),
        "client_messages": customer_count,
        "agent_messages": agent_count,
        "full_thread": full_thread,
        "thread_preview": preview,
        "source": "csv",
    }
    db.upsert_conversation(conv_data, table_prefix=table_prefix)

    if dataset_id is not None:
        try:
            db.conn.execute(
                "UPDATE conversations SET dataset_id = ? WHERE ticket_id = ?",
                (dataset_id, tid)
            )
        except Exception:
            pass

    # Phase 1: pre-populate ticket_index with enrichment so warehouse filters
    # work immediately, before any NLP scan runs. Non-fatal on error — falls
    # back to "enrichment only visible after scan" behavior.
    try:
        from src.services.ticket_index_writer import ensure_ticket_index_row
        enrich_meta = {
            "subject": t["subject"],
            "trc_code": trc_code,
            "trc_label": trc_code,
            "created_at": t["created_at"],
            "csat_score": t["csat_score"],
            "message_count": len(comments),
            "dataset_id": dataset_id,
            "insurance_payer": t.get("insurance_payer"),
            "client_id": t.get("client_id"),
            "provider_id": t.get("provider_id"),
            "agent_id": t.get("agent_id"),
            "service_state": t.get("service_state"),
            "channel": t.get("channel"),
            "session_date": t.get("session_date"),
            "dispute_amount_usd": t.get("dispute_amount_usd"),
            "tags": t.get("tags"),
        }
        ensure_ticket_index_row(tid, enrich_meta, db.conn)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger("alma.csv_ingestion").warning(
            "ensure_ticket_index_row failed for %s: %s", tid, exc
        )


# ═══════════════════════════════════
#  SELF-CONTAINED (KODIF) INGESTION
# ═══════════════════════════════════

def _group_self_contained(rows, col_mapping, _progress, total_rows):
    """Parse rows for self-contained conversations (e.g., Kodif).

    Each row is one conversation with full_thread already built.
    Returns tickets dict matching the same shape as _group_rows_by_ticket.
    """
    tickets = {}
    for idx, row in enumerate(rows):
        record = _parse_row(row, col_mapping)
        tid = record.get("ticket_id", "").strip()
        if not tid:
            continue

        full_thread = record.get("full_thread", "").strip()
        if not full_thread:
            continue

        # Build a compatible ticket dict (same shape as Zendesk path)
        tickets[tid] = {
            "comments": [{"body": full_thread, "created_at": record.get("created_at", ""), "role": "customer"}],
            "subject": record.get("subject", f"Conversation {tid}"),
            "trc_code": record.get("trc_code", ""),
            "status": record.get("status", "closed"),
            "csat_score": record.get("csat_score"),
            "created_at": record.get("created_at", ""),
            "requester_email": "",
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None,
            "first_reply_hours": None,
            # Phase 1 enrichment
            "insurance_payer": (record.get("insurance_payer") or "").strip() or None,
            "client_id": (record.get("client_id") or "").strip() or None,
            "provider_id": (record.get("provider_id") or "").strip() or None,
            "agent_id": (record.get("agent_id") or "").strip() or None,
            "service_state": (record.get("service_state") or "").strip() or None,
            "channel": (record.get("channel") or "").strip() or None,
            "session_date": (record.get("session_date") or "").strip() or None,
            "dispute_amount_usd": _parse_float(record.get("dispute_amount_usd")),
            "tags": (record.get("tags") or "").strip() or None,
            "_full_thread": full_thread,  # Pre-built thread, skip rebuild
        }

        if idx % 5000 == 0 and idx > 0:
            pct = 15 + int((idx / total_rows) * 50)
            _progress(f"Processing row {idx:,} / {total_rows:,}...", min(pct, 65))

    return tickets


def _write_self_contained_to_db(db, tickets, dataset_id, _progress, table_prefix=None):
    """Write self-contained conversations (Kodif-style) to the database.

    Unlike the Zendesk path, no conversation rebuild is needed —
    full_thread is already in the data.
    When table_prefix is provided, also writes to per-source tables.
    """
    from src.data.import_tracker import get_existing_ticket_ids

    existing_ids = get_existing_ticket_ids(db.conn, table_prefix=table_prefix)
    new_tickets = {tid: t for tid, t in tickets.items() if tid not in existing_ids}
    skipped = len(tickets) - len(new_tickets)

    import logging
    logging.getLogger("alma.csv_ingestion").info(
        "Self-contained import: %d new, %d skipped", len(new_tickets), skipped
    )

    inserted = 0
    for tid, t in new_tickets.items():
        trc_code = t["trc_code"]
        full_thread = t.get("_full_thread", "")
        preview = (full_thread[:200] + "...") if len(full_thread) > 200 else full_thread

        # Insert ticket
        ticket_data = {
            "ticket_id": tid,
            "subject": t["subject"],
            "trc_code": trc_code,
            "trc_label": trc_code,
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
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None,
            "first_reply_hours": None,
        }
        db.upsert_ticket(ticket_data, table_prefix=table_prefix)

        # Insert conversation (full_thread already built)
        conv_data = {
            "ticket_id": tid,
            "subject": t["subject"],
            "trc_code": trc_code,
            "trc_label": trc_code,
            "status": t["status"].lower() if t["status"] else "",
            "csat_score": t["csat_score"],
            "created_at": t["created_at"],
            "solved_at": "",
            "message_count": 1,
            "client_messages": 1,
            "agent_messages": 0,
            "full_thread": full_thread,
            "thread_preview": preview,
            "dataset_id": 0,
        }
        db.upsert_conversation(conv_data, table_prefix=table_prefix)

        if dataset_id is not None:
            try:
                db.conn.execute(
                    "UPDATE conversations SET dataset_id = ? WHERE ticket_id = ?",
                    (dataset_id, tid)
                )
            except Exception:
                pass

        # Phase 1 — pre-populate ticket_index with enrichment (self-contained path)
        try:
            from src.services.ticket_index_writer import ensure_ticket_index_row
            ensure_ticket_index_row(tid, {
                "subject": t["subject"],
                "trc_code": trc_code,
                "trc_label": trc_code,
                "created_at": t["created_at"],
                "csat_score": t["csat_score"],
                "message_count": 1,
                "dataset_id": dataset_id,
                "insurance_payer": t.get("insurance_payer"),
                "client_id": t.get("client_id"),
                "provider_id": t.get("provider_id"),
                "agent_id": t.get("agent_id"),
                "service_state": t.get("service_state"),
                "channel": t.get("channel"),
                "session_date": t.get("session_date"),
                "dispute_amount_usd": t.get("dispute_amount_usd"),
                "tags": t.get("tags"),
            }, db.conn)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("alma.csv_ingestion").warning(
                "ensure_ticket_index_row (self-contained) failed for %s: %s", tid, exc
            )

        inserted += 1
        if inserted % 1000 == 0:
            pct = 70 + int((inserted / len(new_tickets)) * 25) if new_tickets else 95
            _progress(f"Loaded {inserted:,} / {len(new_tickets):,} conversations...", min(pct, 95))

    return inserted


# ═══════════════════════════════════
#  POST-INGEST
# ═══════════════════════════════════

def _post_ingest(db, tickets, _progress, table_prefix=None):
    """Run post-ingestion jobs: FTS, entity extraction, n-gram classification."""
    _progress("Finalizing...", 96)
    db.commit()

    _progress("Building search index...", 97)
    db.rebuild_fts_index()
    if table_prefix:
        db.rebuild_source_fts(table_prefix)

    # Entity extraction (P3.0)
    _progress("Extracting entities...", 98)
    try:
        from src.data.entity_extractor import load_entity_dictionaries, extract_entities
        payer_dict, product_dict = load_entity_dictionaries()
        for tid, t in tickets.items():
            text = t["subject"] + " " + " ".join(
                c["body"] for c in t["comments"][:3]
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

    # N-gram provisional classification
    try:
        from src.data.ngram_matcher import NgramMatcher
        matcher = NgramMatcher(db)
        has_patterns = db.conn.execute(
            "SELECT 1 FROM sub_patterns WHERE tier IN ('active','probationary') LIMIT 1"
        ).fetchone()
        if has_patterns:
            new_ticket_ids = list(tickets.keys())
            matcher.classify_batch(new_ticket_ids)
            _progress("Provisional sub-pattern classification complete", 98)
    except Exception:
        pass  # N-gram matching is non-critical


# ═══════════════════════════════════
#  MAIN ENTRY POINT
# ═══════════════════════════════════

def ingest_csv(file_path, db, progress_callback=None, dataset_id=None,
               column_override=None, source_config=None):
    """
    Ingest a Lightdash CSV export into the database.

    Args:
        file_path: Path to the CSV file
        db: DatabaseManager instance
        progress_callback: Optional callable(message, percent) for UI updates
        dataset_id: Optional dataset ID for A/B testing (tags all ingested conversations)
        column_override: Optional dict {normalized_header: target_field} from
                        CSVReformatter.  When provided, used instead of COLUMN_MAP
                        for column mapping.  (Build 8.0)
        source_config: Optional dict with source-specific config:
                       - 'source_type': 'zendesk' | 'kodif' | 'custom'
                       - 'column_mapping': dict mapping source cols to internal fields
                       - 'conversation_structure': 'row_per_comment' | 'self_contained'
                       When provided, applies source-type-specific transforms.
                       (Session 5: Multi-source)

    Returns:
        dict with ingestion stats
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")

    def _progress(msg, pct=None):
        if progress_callback:
            progress_callback(msg, pct)

    # 1. Read CSV
    _progress("Reading CSV file...", 5)
    headers, rows = _read_csv_with_encoding(file_path)
    total_rows = len(rows)

    # 2. Resolve columns — apply source-type column mapping if provided
    _progress("Parsing rows...", 10)
    is_self_contained = (
        source_config and source_config.get("conversation_structure") == "self_contained"
    )
    effective_override = column_override
    if not effective_override and source_config and source_config.get("column_mapping"):
        effective_override = source_config["column_mapping"]
    col_mapping, mapped_fields, unmapped = _resolve_columns(
        headers, effective_override, allow_self_contained=is_self_contained,
    )

    # 3. Group by ticket
    _progress(f"Processing {total_rows} rows...", 15)
    if is_self_contained:
        tickets = _group_self_contained(rows, col_mapping, _progress, total_rows)
    else:
        tickets = _group_rows_by_ticket(rows, col_mapping, _progress, total_rows)

    # 4. Track import run
    from src.data.import_tracker import start_import_run, complete_import_run, fail_import_run
    source_label = source_config.get("source_type", "csv") if source_config else "csv"
    run_id = start_import_run(db.conn, source_label, "incremental", file_path.name)

    # Resolve table_prefix for per-source dual-write.
    # Auto-detects default source when no source_config is provided,
    # ensuring every import populates per-source warehouse tables.
    table_prefix = None
    try:
        from src.data.source_registry import SourceRegistry
        reg = SourceRegistry(db.conn)
        source_id_to_use = (source_config or {}).get("source_id")
        if not source_id_to_use:
            default_src = reg.get_default_source()
            if default_src:
                source_id_to_use = default_src["source_id"]
        if source_id_to_use:
            src = reg.get_source(source_id_to_use)
            if src:
                table_prefix = src["table_prefix"]
    except Exception:
        pass

    try:
        # 5. Write to DB (additive — dedupe inside)
        _progress(f"Loading {len(tickets)} conversations...", 70)
        if is_self_contained:
            inserted = _write_self_contained_to_db(db, tickets, dataset_id, _progress, table_prefix=table_prefix)
        else:
            inserted = _write_tickets_to_db(db, tickets, dataset_id, _progress, table_prefix=table_prefix)
        skipped = len(tickets) - inserted

        # 6. Post-ingest
        _post_ingest(db, tickets, _progress, table_prefix=table_prefix)

        complete_import_run(db.conn, run_id, {
            "tickets_seen": len(tickets),
            "tickets_new": inserted,
            "tickets_skipped": skipped,
        })
    except Exception as e:
        fail_import_run(db.conn, run_id, str(e))
        raise

    # 7. Stats
    stats = {
        "total_csv_rows": total_rows,
        "tickets_created": inserted,
        "tickets_seen": len(tickets),
        "tickets_skipped": skipped,
        "comments_stored": sum(len(t["comments"]) for t in tickets.values()),
        "mapped_fields": sorted(mapped_fields),
        "unmapped_headers": unmapped,
        "has_resolution_times": "assignment_to_resolution_hours" in mapped_fields,
    }

    _progress(f"Done — {inserted:,} new conversations loaded ({skipped:,} skipped)", 100)

    db.log_analysis("csv_import", {
        "file": file_path.name,
        "rows": total_rows,
        "tickets": inserted,
    }, ticket_count=inserted)

    # Clear the Clear-&-Close session-visibility flag so the
    # Conversation Search page shows the newly imported rows.
    if inserted > 0:
        try:
            from src.services.clear_session import set_conversation_search_hidden
            set_conversation_search_hidden(str(db.db_path), False)
        except Exception:
            pass  # non-fatal — the user can toggle Search again

    return stats
