"""
Debug: Run a single mixed-TRC batch to see what the model outputs.
"""
import sys, os, json, sqlite3, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "data" / "local_warehouse.db")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)

def test_mixed_batch():
    """Test classification of a small mixed-TRC batch."""
    from src.agents.gemini_bridge_wrapper import GeminiBridge
    from src.agents.worker_agent import WorkerAgent

    # Pick 3 small TRCs that appear in the mixed batches
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Find TRCs with exactly 3-4 tickets each
    small_trcs = conn.execute("""
        SELECT trc_code, COUNT(DISTINCT ticket_id) as n
        FROM conversations
        WHERE trc_code IS NOT NULL AND trc_code != ''
        GROUP BY trc_code
        HAVING n BETWEEN 3 AND 5
        ORDER BY RANDOM()
        LIMIT 3
    """).fetchall()

    print(f"Selected TRCs:")
    trc_list = []
    for row in small_trcs:
        print(f"  {row['trc_code']}: {row['n']} tickets")
        trc_list.append(row['trc_code'])

    trc_json = json.dumps(trc_list)

    # Get tickets for these TRCs
    tickets = []
    for trc in trc_list:
        rows = conn.execute("""
            SELECT DISTINCT ticket_id, trc_code, full_thread
            FROM conversations
            WHERE trc_code = ?
            LIMIT 3
        """, (trc,)).fetchall()
        for r in rows:
            tickets.append({
                "ticket_id": r["ticket_id"],
                "trc": r["trc_code"],
                "full_thread": (r["full_thread"] or "")[:2000],
            })

    print(f"\nTotal tickets for test: {len(tickets)}")
    conn.close()

    # Boot bridge and worker
    bridge = GeminiBridge(model="gemini-2.0-flash")
    bridge.ensure_running()
    worker = WorkerAgent("test_worker", bridge, DB_PATH)

    # Build payload
    payload = {
        "scan_id": "test-scan",
        "batch_id": "test-batch-mixed",
        "trc": trc_json,
        "tickets": tickets,
        "stats_context": "",
        "sub_taxonomy": "",
        "chunk_n": 1,
        "chunk_total": 1,
        "date_start": "2025-01-01",
        "date_end": "2025-03-12",
    }

    # Build prompt (just to inspect)
    prompt = worker._build_prompt(payload)
    print(f"\n{'='*70}")
    print(f"PROMPT LENGTH: {len(prompt)} chars")
    print(f"PROMPT FIRST 500 chars:")
    print(prompt[:500])
    print(f"\nPROMPT LAST 500 chars:")
    print(prompt[-500:])
    print(f"{'='*70}")

    # Classify
    print(f"\nClassifying {len(tickets)} tickets...")
    result = worker.classify_batch(payload)

    print(f"\n{'='*70}")
    print(f"RESULT:")
    for k, v in result.items():
        print(f"  {k}: {v}")

    # Check what was stored
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stored = conn.execute("""
        SELECT ticket_id, sub_cluster, friction_type
        FROM nlp_ticket_classifications
        WHERE scan_id = 'test-scan'
    """).fetchall()
    print(f"\nSTORED CLASSIFICATIONS ({len(stored)}):")
    for s in stored:
        print(f"  {s['ticket_id']}: {s['sub_cluster']} | {s['friction_type']}")

    # Clean up test data
    conn.execute("DELETE FROM nlp_ticket_classifications WHERE scan_id = 'test-scan'")
    conn.commit()
    conn.close()

    bridge.shutdown()


if __name__ == "__main__":
    test_mixed_batch()
