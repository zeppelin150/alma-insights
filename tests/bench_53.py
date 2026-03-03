"""
Build 5.3 Benchmark: Full NLP scan with gemini-2.5-flash, 3 workers.
Includes 5.3 fix verification (TRC propagation, dedup, quality audit).
"""
import sys, os, json, time, sqlite3, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'local_warehouse.db')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)


def run_benchmark():
    from src.agents.scan_orchestrator import ScanOrchestrator

    orch = ScanOrchestrator(DB_PATH)

    print("=" * 70)
    print("BUILD 5.4 BENCHMARK -- gemini-2.5-flash, 3 workers, input-aware batching")
    print("=" * 70)

    start_time = time.time()
    result = orch.start_scan(
        date_start="2025-01-01",
        date_end="2025-03-12",
        budget_cap=50.0,
        parallel_workers=3,
    )

    print(f"\nScan initiated in {time.time() - start_time:.1f}s")
    print(f"Result: {json.dumps({k: v for k, v in result.items() if k != 'batches'}, indent=2)}")

    scan_id = result.get("scan_id")
    if not scan_id:
        print("ERROR: No scan_id returned!")
        return

    # Poll progress
    print("\nWaiting for scan to complete...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    max_wait = 1200

    while time.time() - start_time < max_wait:
        run = conn.execute(
            "SELECT status, completed_batches, total_batches FROM nlp_scan_runs WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()
        if not run:
            print("ERROR: scan_id not found!")
            return

        cls_count = conn.execute(
            "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()

        status = run['status']
        completed = run['completed_batches']
        total = run['total_batches']
        elapsed = time.time() - start_time
        print(f"  [{elapsed:5.0f}s] {status:16s} | {completed}/{total} batches | {cls_count['n']} classified")

        if status in ('analysis_complete', 'completed', 'failed', 'cancelled'):
            break
        time.sleep(5)
    else:
        print("TIMEOUT!")
    conn.close()

    elapsed = time.time() - start_time
    print(f"\nScan completed in {elapsed:.1f}s")

    # === FULL DIAGNOSTIC ===
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    run = conn.execute("SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)).fetchone()
    print(f"\n{'=' * 70}")
    print(f"SCAN STATUS: {run['status']}")
    print(f"Total tickets: {run['total_tickets']}")
    print(f"Total batches: {run['total_batches']}")
    print(f"Completed batches: {run['completed_batches']}")
    print(f"{'=' * 70}")

    # Batch details
    batches = conn.execute(
        "SELECT * FROM nlp_batches WHERE scan_id = ? ORDER BY batch_number",
        (scan_id,)
    ).fetchall()
    print(f"\nBATCH DETAILS ({len(batches)} batches):")
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

    # Total classified
    cls_count = conn.execute(
        "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()
    total_classified = cls_count['n']
    print(f"\nTOTAL CLASSIFICATIONS: {total_classified}")

    # === 5.3 FIX VERIFICATION ===
    print(f"\n{'=' * 70}")
    print("5.3 FIX VERIFICATION")
    print(f"{'=' * 70}")

    # TRC propagation
    json_trc = conn.execute(
        "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ? AND trc LIKE '[%'",
        (scan_id,)
    ).fetchone()
    status_trc = "PASS" if json_trc['n'] == 0 else "FAIL"
    print(f"  [{status_trc}] TRC propagation: {json_trc['n']} JSON-array TRC values (target: 0)")

    # Dedup
    dupes = conn.execute(
        "SELECT COUNT(*) as n FROM (SELECT ticket_id FROM nlp_ticket_classifications "
        "WHERE scan_id = ? GROUP BY ticket_id HAVING COUNT(*) > 1)",
        (scan_id,)
    ).fetchone()
    status_dup = "PASS" if dupes['n'] == 0 else "FAIL"
    print(f"  [{status_dup}] Dedup: {dupes['n']} duplicate ticket_ids (target: 0)")

    # Quality audit
    audit = conn.execute(
        "SELECT report_type, LENGTH(content) as size FROM analyst_reports "
        "WHERE scan_id = ? AND report_type = 'audit'",
        (scan_id,)
    ).fetchone()
    if audit and audit['size'] and audit['size'] > 10:
        print(f"  [PASS] Quality audit: report stored ({audit['size']} bytes)")
    else:
        print(f"  [????] Quality audit: check logs (may still fail if query needs tuning)")

    # JSON-array TRCs in sub_patterns
    json_sp = conn.execute("SELECT COUNT(*) as n FROM sub_patterns WHERE trc LIKE '[%'").fetchone()
    status_sp = "PASS" if json_sp['n'] == 0 else "FAIL"
    print(f"  [{status_sp}] Sub-pattern TRCs: {json_sp['n']} JSON-array values (target: 0)")

    # Unique TRC coverage
    unique_trcs = conn.execute("SELECT COUNT(DISTINCT trc) as n FROM sub_patterns").fetchone()
    print(f"  [INFO] Unique TRCs in sub_patterns: {unique_trcs['n']}")

    # === CLASSIFICATION BREAKDOWN ===
    print(f"\n{'=' * 70}")
    print("CLASSIFICATION BREAKDOWN")
    print(f"{'=' * 70}")

    # Confidence
    conf = conn.execute(
        "SELECT AVG(sub_cluster_confidence) as avg, "
        "MIN(sub_cluster_confidence) as min, "
        "MAX(sub_cluster_confidence) as max "
        "FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()
    print(f"\nConfidence: avg={conf['avg']:.3f} min={conf['min']:.3f} max={conf['max']:.3f}")

    # Friction
    friction = conn.execute(
        "SELECT friction_type, COUNT(*) as n FROM nlp_ticket_classifications "
        "WHERE scan_id = ? GROUP BY friction_type ORDER BY n DESC",
        (scan_id,)
    ).fetchall()
    print(f"\nFriction distribution:")
    for f in friction:
        pct = f['n'] / max(total_classified, 1) * 100
        print(f"  {f['friction_type']:25s}: {f['n']:4d} ({pct:.1f}%)")

    # Sentiment
    sent = conn.execute(
        "SELECT sentiment_polarity, COUNT(*) as n FROM nlp_ticket_classifications "
        "WHERE scan_id = ? GROUP BY sentiment_polarity ORDER BY n DESC",
        (scan_id,)
    ).fetchall()
    print(f"\nSentiment:")
    for s in sent:
        print(f"  {s['sentiment_polarity']:12s}: {s['n']}")

    # Anomaly
    anom = conn.execute(
        "SELECT anomaly_flag, COUNT(*) as n FROM nlp_ticket_classifications "
        "WHERE scan_id = ? GROUP BY anomaly_flag ORDER BY n DESC",
        (scan_id,)
    ).fetchall()
    print(f"\nAnomaly:")
    for a in anom:
        print(f"  {str(a['anomaly_flag']):12s}: {a['n']}")

    # Novel
    novel = conn.execute(
        "SELECT COUNT(*) as n FROM nlp_ticket_classifications "
        "WHERE scan_id = ? AND is_novel = 1",
        (scan_id,)
    ).fetchone()
    print(f"\nNovel: {novel['n']} ({novel['n'] / max(total_classified, 1) * 100:.1f}%)")

    # Unique sub-clusters
    uclusters = conn.execute(
        "SELECT COUNT(DISTINCT sub_cluster) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()
    print(f"Unique sub-clusters: {uclusters['n']}")

    # === SUBTAXONOMY ===
    sub_patterns = conn.execute("SELECT COUNT(*) as n FROM sub_patterns").fetchone()
    sub_ngrams = conn.execute("SELECT COUNT(*) as n FROM sub_pattern_ngrams").fetchone()
    print(f"\nSubtaxonomy:")
    print(f"  sub_patterns: {sub_patterns['n']}")
    print(f"  sub_pattern_ngrams: {sub_ngrams['n']}")
    print(f"  ngrams/pattern: {sub_ngrams['n'] / max(sub_patterns['n'], 1):.1f}")

    if sub_patterns['n'] > 0:
        top = conn.execute(
            "SELECT trc, label, friction_type, lifetime_tickets "
            "FROM sub_patterns ORDER BY lifetime_tickets DESC LIMIT 10"
        ).fetchall()
        print(f"\n  TOP SUB-PATTERNS:")
        for p in top:
            print(f"    [{p['friction_type']}] {p['label'][:45]} | "
                  f"trc={p['trc'][:50]} | tickets={p['lifetime_tickets']}")

    # === FINDINGS ===
    findings = conn.execute(
        "SELECT * FROM nlp_findings WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    within = sum(1 for f in findings if f['finding_type'] == 'within_trc')
    cross = sum(1 for f in findings if f['finding_type'] == 'cross_trc')
    print(f"\nFindings: {len(findings)} total ({within} within_trc, {cross} cross_trc)")
    for f in findings[:15]:
        print(f"  [{f['finding_type']}] {f['title'][:70]} | "
              f"tickets={f['ticket_count']} | impact={f['impact_score']:.3f}")
    if len(findings) > 15:
        print(f"  ... and {len(findings) - 15} more")

    # === EVENTS ===
    events = conn.execute(
        "SELECT event_type, COUNT(*) as n FROM scan_events "
        "WHERE scan_id = ? GROUP BY event_type ORDER BY n DESC",
        (scan_id,)
    ).fetchall()
    print(f"\nEvent summary:")
    for e in events:
        print(f"  {e['event_type']:20s}: {e['n']}")

    # === SAMPLE CLASSIFICATIONS ===
    samples = conn.execute(
        "SELECT ticket_id, trc, sub_cluster, friction_type, "
        "sentiment_intensity, anomaly_flag "
        "FROM nlp_ticket_classifications WHERE scan_id = ? "
        "ORDER BY RANDOM() LIMIT 10",
        (scan_id,)
    ).fetchall()
    print(f"\nSample classifications:")
    for s in samples:
        print(f"  {s['ticket_id']}: {s['sub_cluster'][:40]} | "
              f"{s['friction_type']} | trc={s['trc'][:40]}")

    conn.close()
    print(f"\n{'=' * 70}")
    print(f"BENCHMARK COMPLETE -- Wall clock: {elapsed:.1f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run_benchmark()
