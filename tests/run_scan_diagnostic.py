"""
Diagnostic NLP scan — reproduces production conditions.

16 workers, full date range, gemini-3-flash-preview active.
Logs every scan_event to stdout+file so we can observe failure modes in real time.

Usage:
    python tests/run_scan_diagnostic.py

Output:
    tests/scan_diagnostic.log      — full logger output
    tests/scan_diagnostic.json     — post-scan metrics snapshot
"""
import sys
import os
import json
import time
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "diag_scan.db")
LOG_PATH = str(Path(__file__).resolve().parent / "scan_diagnostic.log")
RESULTS_PATH = str(Path(__file__).resolve().parent / "scan_diagnostic.json")

# Dual-output logging (file + stdout, flushed)
class FlushHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_PATH, mode='w', encoding='utf-8'),
        FlushHandler(sys.stdout),
    ],
)
logger = logging.getLogger("diag")


def get_db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def event_tailer(scan_id, stop_event):
    """Poll scan_events table every 2s and print new events with
    symbols so we see them in real time."""
    seen = 0
    conn = get_db()
    while not stop_event.is_set():
        try:
            rows = conn.execute("""
                SELECT rowid, timestamp, event_type, status, message
                FROM scan_events WHERE scan_id = ? ORDER BY rowid
            """, (scan_id,)).fetchall()
            for r in rows[seen:]:
                # ASCII only — Windows cp1252 console chokes on emoji
                sym = {"info": "[i]", "warn": "[!]", "error": "[X]"}.get(r["status"], "[?]")
                logger.info(f"[EVT] {sym} {r['event_type']:20s} {r['status']:6s} | {r['message']}")
            seen = len(rows)
        except Exception as e:
            logger.debug(f"event tail failed: {e}")
        stop_event.wait(2.0)
    conn.close()


def progress_tailer(scan_id, stop_event, start_time):
    """Poll batch + classification state every 10s."""
    last_completed = -1
    last_cls = -1
    conn = get_db()
    while not stop_event.is_set():
        try:
            run = conn.execute(
                "SELECT status, completed_batches, total_batches FROM nlp_scan_runs WHERE scan_id = ?",
                (scan_id,)
            ).fetchone()
            cls = conn.execute(
                "SELECT COUNT(*) FROM nlp_ticket_classifications WHERE scan_id = ?",
                (scan_id,)
            ).fetchone()[0]

            if run and (run["completed_batches"] != last_completed or cls != last_cls):
                elapsed = time.time() - start_time
                logger.info(
                    f"[PROG] t+{elapsed:6.0f}s | {run['status']:20s} | "
                    f"batches={run['completed_batches']}/{run['total_batches']} | "
                    f"classified={cls}"
                )
                last_completed = run["completed_batches"]
                last_cls = cls
        except Exception as e:
            logger.debug(f"progress tail failed: {e}")
        stop_event.wait(10.0)
    conn.close()


def capture_final(scan_id):
    """Snapshot post-scan DB state for analysis."""
    conn = get_db()
    out = {"scan_id": scan_id}
    run = conn.execute("SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)).fetchone()
    if run:
        out["status"] = run["status"]
        out["total_batches"] = run["total_batches"]
        out["completed_batches"] = run["completed_batches"]
        out["actual_cost_usd"] = run["actual_cost_usd"]

    out["classifications"] = conn.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications WHERE scan_id = ?", (scan_id,)
    ).fetchone()[0]

    # Dropped tickets — in scan's batches but no classification
    out["dropped_tickets"] = conn.execute("""
        SELECT COUNT(DISTINCT bt.ticket_id)
        FROM nlp_batch_tickets bt
        LEFT JOIN nlp_ticket_classifications tc
            ON tc.ticket_id = bt.ticket_id AND tc.scan_id = bt.scan_id
        WHERE bt.scan_id = ? AND tc.ticket_id IS NULL
    """, (scan_id,)).fetchone()[0]

    # Batch-level stats
    batches = conn.execute("""
        SELECT status, COUNT(*) as n, AVG(latency_ms) as avg_lat,
               MAX(latency_ms) as max_lat, SUM(retry_count) as retries
        FROM nlp_batches WHERE scan_id = ? GROUP BY status
    """, (scan_id,)).fetchall()
    out["batch_stats"] = [dict(b) for b in batches]

    # Event counts
    events = conn.execute("""
        SELECT event_type, status, COUNT(*) as n FROM scan_events
        WHERE scan_id = ? GROUP BY event_type, status ORDER BY n DESC
    """, (scan_id,)).fetchall()
    out["events"] = [dict(e) for e in events]

    # Error events
    errors = conn.execute("""
        SELECT event_type, message FROM scan_events
        WHERE scan_id = ? AND status = 'error' ORDER BY rowid LIMIT 20
    """, (scan_id,)).fetchall()
    out["errors"] = [dict(e) for e in errors]

    conn.close()
    return out


def main():
    logger.info("=" * 70)
    logger.info(f"DIAGNOSTIC NLP SCAN — 16 workers, gemini-3-flash-preview")
    logger.info(f"DB: {DB_PATH}")
    logger.info(f"Started: {datetime.now().isoformat()}")
    logger.info("=" * 70)

    # Model sanity
    from src.llm.model_registry import ModelRegistry
    active = ModelRegistry.instance().active()
    logger.info(f"Active model: {active.id} (model_string={active.model_string})")

    # Migration
    from src.data.db_manager import DatabaseManager
    dm = DatabaseManager(Path(DB_PATH))
    dm.initialize()
    dm.close()
    logger.info("DB schema initialized")

    # Start scan
    from src.agents.scan_orchestrator import ScanOrchestrator
    orch = ScanOrchestrator(DB_PATH)
    logger.info(f"Orchestrator model: {orch._model}")

    start_time = time.time()
    result = orch.start_scan(
        date_start="2025-01-01",
        date_end="2025-04-15",
        budget_cap=50.0,
        parallel_workers=16,
    )
    scan_id = result.get("scan_id")
    if not scan_id:
        logger.error(f"No scan_id returned: {result}")
        return
    logger.info(f"Scan launched: {scan_id}")

    # Start tailers
    stop = threading.Event()
    t1 = threading.Thread(target=event_tailer, args=(scan_id, stop), daemon=True)
    t2 = threading.Thread(target=progress_tailer, args=(scan_id, stop, start_time), daemon=True)
    t1.start()
    t2.start()

    # Poll until terminal
    max_wait = 3600  # 60 min hard cap (gemini-3 is slow)
    poll = 5
    conn = get_db()
    try:
        while time.time() - start_time < max_wait:
            run = conn.execute(
                "SELECT status FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            if not run:
                logger.error("scan_id vanished from DB!")
                break
            if run["status"] in ("completed", "completed_with_errors", "failed", "cancelled",
                                 "analysis_complete", "scan_complete"):
                logger.info(f"Reached terminal status: {run['status']}")
                break
            time.sleep(poll)
        else:
            logger.warning(f"HARD TIMEOUT — scan did not terminate in {max_wait}s")
    finally:
        conn.close()
        stop.set()
        time.sleep(3)  # let tailers drain

    total = time.time() - start_time
    logger.info(f"Scan total wall time: {total:.1f}s ({total/60:.1f} min)")

    # Final snapshot
    final = capture_final(scan_id)
    final["total_wall_s"] = round(total, 1)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, default=str)

    logger.info("=" * 70)
    logger.info("FINAL SNAPSHOT")
    logger.info("=" * 70)
    logger.info(f"  Status: {final.get('status')}")
    logger.info(f"  Batches: {final.get('completed_batches')}/{final.get('total_batches')}")
    logger.info(f"  Classifications: {final.get('classifications')}")
    logger.info(f"  Dropped tickets: {final.get('dropped_tickets')}")
    logger.info(f"  Cost: ${final.get('actual_cost_usd') or 0:.4f}")
    logger.info(f"  Batch stats: {final.get('batch_stats')}")
    logger.info(f"  Error events: {len(final.get('errors', []))}")
    for e in final.get("errors", [])[:10]:
        logger.info(f"    • {e['event_type']}: {e['message'][:120]}")

    logger.info(f"Full JSON: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
