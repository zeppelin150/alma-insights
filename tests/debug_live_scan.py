"""
Diagnostic: Run a real scan against the production DB with the new batch sizing.
Tests the full ScanOrchestrator pipeline with ~880 tickets.
"""
import sys, os, json, time, sqlite3, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pathlib import Path

DB_PATH = str(Path(__file__).parent.parent / "data" / "local_warehouse.db")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)


def run_scan():
    """Run a real scan and monitor results."""
    from src.agents.scan_orchestrator import ScanOrchestrator

    orch = ScanOrchestrator(DB_PATH)

    print("="*70)
    print("LIVE SCAN DIAGNOSTIC")
    print("="*70)

    # Start scan
    print("\nStarting scan: 2025-01-01 to 2025-03-12")
    start_time = time.time()

    result = orch.start_scan(
        date_start="2025-01-01",
        date_end="2025-03-12",
        budget_cap=50.0,
        parallel_workers=2,
    )

    print(f"\nScan initiated in {time.time() - start_time:.1f}s")
    print(f"Result: {json.dumps({k: v for k, v in result.items() if k != 'batches'}, indent=2)}")
    if 'batches' in result:
        print(f"Batches created: {result.get('total_batches', len(result.get('batches', [])))}")

    scan_id = result.get("scan_id")
    if not scan_id:
        print("ERROR: No scan_id returned!")
        return

    # Wait for scan to complete
    print("\nWaiting for scan to complete...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    max_wait = 1200  # 20 minutes
    poll_interval = 5

    while time.time() - start_time < max_wait:
        run = conn.execute(
            "SELECT status, completed_batches, total_batches FROM nlp_scan_runs WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()
        if not run:
            print("ERROR: scan_id not found in DB!")
            return

        # Check classification count
        cls_count = conn.execute(
            "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()

        status = run['status']
        completed = run['completed_batches']
        total = run['total_batches']

        elapsed = time.time() - start_time
        print(f"  [{elapsed:5.0f}s] {status} | {completed}/{total} batches | "
              f"{cls_count['n']} classified")

        if status in ('analysis_complete', 'completed', 'failed', 'cancelled'):
            break
        time.sleep(poll_interval)
    else:
        print("TIMEOUT: scan did not complete within 10 minutes!")
    conn.close()

    elapsed = time.time() - start_time
    print(f"\nScan completed in {elapsed:.1f}s")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Scan run status
    run = conn.execute(
        "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    print(f"\n{'='*70}")
    print(f"SCAN STATUS: {run['status']}")
    print(f"Total tickets: {run['total_tickets']}")
    print(f"Total batches: {run['total_batches']}")
    print(f"Completed batches: {run['completed_batches']}")
    print(f"{'='*70}")

    # Batch details
    batches = conn.execute(
        "SELECT * FROM nlp_batches WHERE scan_id = ? ORDER BY batch_number",
        (scan_id,)
    ).fetchall()
    print(f"\nBATCH DETAILS ({len(batches)} batches):")
    total_classified = 0
    for b in batches:
        trc = b['trc']
        if trc.startswith('['):
            try:
                trcs = json.loads(trc)
                trc = f"[{len(trcs)} TRCs]"
            except:
                pass
        trc = trc[:50]
        print(f"  Batch {b['batch_number']:2d} | {b['status']:9s} | "
              f"tickets={b['ticket_count']:3d} | "
              f"latency={b['latency_ms']:5d}ms | "
              f"retries={b['retry_count']} | {trc}")

    # Classification counts
    cls_count = conn.execute(
        "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()
    print(f"\nTOTAL CLASSIFICATIONS: {cls_count['n']}")

    # Sample classifications
    samples = conn.execute("""
        SELECT ticket_id, trc, sub_cluster, friction_type,
               sentiment_intensity, anomaly_flag
        FROM nlp_ticket_classifications
        WHERE scan_id = ?
        ORDER BY RANDOM()
        LIMIT 10
    """, (scan_id,)).fetchall()
    if samples:
        print(f"\nSAMPLE CLASSIFICATIONS:")
        for s in samples:
            print(f"  {s['ticket_id']}: {s['sub_cluster'][:40]} | "
                  f"{s['friction_type']} | sent={s['sentiment_intensity']} | "
                  f"anomaly={s['anomaly_flag']}")

    # Findings
    findings = conn.execute(
        "SELECT * FROM nlp_findings WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    print(f"\nFINDINGS: {len(findings)}")
    for f in findings:
        print(f"  [{f['finding_type']}] {f['title'][:60]} | "
              f"tickets={f['ticket_count']} | impact={f['impact_score']}")

    # Scan events summary
    events = conn.execute("""
        SELECT event_type, COUNT(*) as n
        FROM scan_events WHERE scan_id = ?
        GROUP BY event_type ORDER BY n DESC
    """, (scan_id,)).fetchall()
    print(f"\nEVENT SUMMARY:")
    for e in events:
        print(f"  {e['event_type']:20s}: {e['n']}")

    # Errors
    errors = conn.execute("""
        SELECT message FROM scan_events
        WHERE scan_id = ? AND status = 'error'
        ORDER BY timestamp
    """, (scan_id,)).fetchall()
    if errors:
        print(f"\nERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e['message']}")

    # Subtaxonomy verification
    sub_patterns = conn.execute(
        "SELECT COUNT(*) as n FROM sub_patterns"
    ).fetchone()
    sub_ngrams = conn.execute(
        "SELECT COUNT(*) as n FROM sub_pattern_ngrams"
    ).fetchone()
    print(f"\nSUBTAXONOMY:")
    print(f"  sub_patterns: {sub_patterns['n']}")
    print(f"  sub_pattern_ngrams: {sub_ngrams['n']}")

    if sub_patterns['n'] > 0:
        sample_patterns = conn.execute("""
            SELECT trc, label, friction_type, lifetime_tickets
            FROM sub_patterns ORDER BY lifetime_tickets DESC LIMIT 10
        """).fetchall()
        print(f"\n  TOP SUB-PATTERNS:")
        for p in sample_patterns:
            print(f"    [{p['friction_type']}] {p['label'][:45]} | "
                  f"trc={p['trc'][:40]} | tickets={p['lifetime_tickets']}")

    conn.close()


if __name__ == "__main__":
    run_scan()
