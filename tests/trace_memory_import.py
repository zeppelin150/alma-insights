"""
Alma Insights — Memory Trace: CSV Import Pipeline

Traces memory consumption at every stage of the CSV import path,
isolating exactly which call causes the bloat.

Usage:  python tests/trace_memory_import.py <csv_file>
"""

import sys
import os
import gc
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Memory measurement (no psutil dependency) ──

def get_rss_mb():
    """Get current process working set in MB (Windows)."""
    gc.collect()
    import subprocess
    pid = os.getpid()
    # tasklist /FI gives reliable memory on Windows
    out = subprocess.check_output(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        text=True, stderr=subprocess.DEVNULL,
    )
    # Output: "python.exe","12345","Console","1","123,456 K"
    for line in out.strip().splitlines():
        if str(pid) in line:
            # Last field is memory like "123,456 K" or "123 456 Ko"
            parts = line.strip('"').split('","')
            if len(parts) >= 5:
                mem_str = parts[4].strip('"').replace(",", "").replace(" ", "")
                # Strip trailing unit (K, Ko, etc.)
                digits = "".join(c for c in mem_str if c.isdigit())
                if digits:
                    return round(int(digits) / 1024, 1)  # K -> MB
    return 0.0


def checkpoint(label, baseline_mb=None):
    """Print a memory checkpoint."""
    rss = get_rss_mb()
    delta = f"  (+{rss - baseline_mb:.1f} MB)" if baseline_mb is not None else ""
    print(f"  [{rss:>8.1f} MB]{delta}  {label}")
    return rss


# ── Main trace ──

def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/trace_memory_import.py <csv_file>")
        sys.exit(1)

    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print("=" * 70)
    print("  MEMORY TRACE: CSV IMPORT PIPELINE")
    print(f"  File: {csv_path}")
    print(f"  Size: {os.path.getsize(csv_path) / 1024:.1f} KB")
    print("=" * 70)

    base = checkpoint("Baseline (before any imports)")

    # ── Phase 1: Import modules ──
    print("\n── Phase 1: Module Imports ──")

    from src.data.db_manager import DatabaseManager
    checkpoint("After importing DatabaseManager", base)

    from src.data.csv_ingestion import ingest_csv, _map_columns, _parse_row, COLUMN_MAP
    checkpoint("After importing csv_ingestion", base)

    import csv
    import io
    from collections import defaultdict
    checkpoint("After importing stdlib (csv, io, collections)", base)

    # ── Phase 2: Setup DB ──
    print("\n── Phase 2: Database Setup ──")

    db_path = Path("data/trace_test.db")
    if db_path.exists():
        db_path.unlink()
    db = DatabaseManager(db_path)
    db.initialize()
    checkpoint("After DB initialize (empty)", base)

    # ── Phase 3: CSV Read ──
    print("\n── Phase 3: CSV Reading ──")

    raw = Path(csv_path).read_bytes()
    checkpoint(f"After read_bytes ({len(raw):,} bytes)", base)

    text = raw.decode("utf-8-sig")
    checkpoint(f"After decode ({len(text):,} chars)", base)

    # Free raw bytes
    del raw
    gc.collect()
    checkpoint("After del raw + gc.collect()", base)

    reader = csv.reader(io.StringIO(text))
    headers = next(reader)
    checkpoint(f"After csv.reader + headers ({len(headers)} columns)", base)

    rows = list(reader)
    total_rows = len(rows)
    checkpoint(f"After list(reader) ({total_rows:,} rows)", base)

    # Free text
    del text
    gc.collect()
    checkpoint("After del text + gc.collect()", base)

    # ── Phase 4: Column Mapping ──
    print("\n── Phase 4: Column Mapping & Row Parsing ──")

    from src.data.csv_ingestion import _normalize_header
    col_mapping, unmapped = _map_columns(headers)
    checkpoint(f"After column mapping ({len(col_mapping)} mapped)", base)

    # ── Phase 5: Ticket Grouping ──
    print("\n── Phase 5: Ticket Grouping ──")

    from src.data.rebuild_utils import normalize_role, sort_events_chronologically

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

    for idx, row in enumerate(rows):
        record = _parse_row(row, col_mapping)
        tid = record.get("ticket_id", "").strip()
        if not tid:
            continue
        t = tickets[tid]
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

        body = record.get("comment_body", "").strip()
        if body:
            role = normalize_role(record.get("author_role", ""))
            t["comments"].append({
                "body": body,
                "created_at": record.get("created_at", ""),
                "event_ts_raw": record.get("event_timestamp_raw", ""),
                "role": role,
            })

    checkpoint(f"After ticket grouping ({len(tickets):,} tickets)", base)

    # Free rows
    del rows
    gc.collect()
    checkpoint("After del rows + gc.collect()", base)

    # ── Phase 6: DB Inserts ──
    print("\n── Phase 6: DB Inserts (tickets + comments + conversations) ──")

    db.conn.execute("DELETE FROM conversations")
    db.conn.execute("DELETE FROM comments")
    db.conn.execute("DELETE FROM tickets")
    db.conn.commit()

    inserted = 0
    for tid, t in tickets.items():
        trc_code = t["trc_code"]

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
            "assignment_to_resolution_hours": t.get("assignment_to_resolution_hours"),
            "total_resolution_hours": t.get("total_resolution_hours"),
            "first_reply_hours": t.get("first_reply_hours"),
        }
        db.upsert_ticket(ticket_data)

        # Rebuild thread
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
        customer_count = agent_count = bot_count = 0

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
            thread_lines.append(f"[{ts_display}] {display_role}:\n{c['body']}")

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
        }
        db.upsert_conversation(conv_data)
        inserted += 1

    db.commit()
    checkpoint(f"After DB inserts ({inserted:,} conversations committed)", base)

    # ── Phase 7: FTS Rebuild ──
    print("\n── Phase 7: FTS5 Index Rebuild ──")

    db.rebuild_fts_index()
    checkpoint("After rebuild_fts_index()", base)

    # ── Phase 8: Entity Extraction ──
    print("\n── Phase 8: Entity Extraction ──")

    try:
        from src.data.entity_extractor import load_entity_dictionaries, extract_entities
        payer_dict, product_dict = load_entity_dictionaries()
        checkpoint("After load_entity_dictionaries()", base)

        for tid, t in tickets.items():
            text = t["subject"] + " " + " ".join(
                c["body"] for c in t["comments"][:3]
            )
            entities = extract_entities(text, payer_dict, product_dict)
            if entities.get("payers") or entities.get("product_areas"):
                entity_records = []
                for p in entities.get("payers", []):
                    entity_records.append({"entity_type": "payer", "entity_value": p, "confidence": 0.8})
                for pa in entities.get("product_areas", []):
                    entity_records.append({"entity_type": "product_area", "entity_value": pa, "confidence": 0.8})
                db.save_ticket_entities(tid, entity_records)
        checkpoint("After entity extraction loop", base)
    except Exception as e:
        checkpoint(f"Entity extraction skipped: {e}", base)

    # ── Phase 9: N-gram matching ──
    print("\n── Phase 9: N-gram Provisional Classification ──")

    try:
        from src.data.ngram_matcher import NgramMatcher
        matcher = NgramMatcher(db)
        has_patterns = db.conn.execute(
            "SELECT 1 FROM sub_patterns WHERE tier IN ('active','probationary') LIMIT 1"
        ).fetchone()
        if has_patterns:
            matcher.classify_batch(list(tickets.keys()))
            checkpoint("After ngram classify_batch()", base)
        else:
            checkpoint("N-gram skipped (no active patterns)", base)
    except Exception as e:
        checkpoint(f"N-gram skipped: {e}", base)

    # ── Phase 10: Free tickets dict ──
    print("\n── Phase 10: Cleanup ──")

    ticket_count = len(tickets)
    comment_count = sum(len(t["comments"]) for t in tickets.values())
    del tickets
    gc.collect()
    checkpoint("After del tickets + gc.collect()", base)

    # ── Phase 11: Simulate post-import UI calls ──
    print("\n── Phase 11: Post-Import DB Operations (simulating _on_data_loaded) ──")

    db.populate_daily_counts()
    checkpoint("After populate_daily_counts()", base)

    db.populate_hourly_counts()
    checkpoint("After populate_hourly_counts()", base)

    # Simulate search_conversations (what run_search does)
    results = db.search_conversations(
        date_from="2020-01-01",
        date_to="2030-12-31",
    )
    checkpoint(f"After search_conversations() ({len(results)} results)", base)

    del results
    gc.collect()
    checkpoint("After del results + gc.collect()", base)

    # ── Phase 12: Full GC + final ──
    print("\n── Phase 12: Final State ──")

    gc.collect()
    gc.collect()
    gc.collect()
    final = checkpoint("Final (after 3x gc.collect)", base)

    # ── Summary ──
    print("\n" + "=" * 70)
    print(f"  CSV: {os.path.getsize(csv_path) / 1024:.1f} KB on disk")
    print(f"  Tickets: {ticket_count:,}  |  Comments: {comment_count:,}")
    print(f"  Memory: {base:.1f} MB → {final:.1f} MB  (+{final - base:.1f} MB)")
    print(f"  Amplification: {(final - base) / (os.path.getsize(csv_path) / (1024*1024)):.0f}x CSV size")
    print("=" * 70)

    # Cleanup test DB
    db.close()
    if db_path.exists():
        db_path.unlink()


if __name__ == "__main__":
    main()
