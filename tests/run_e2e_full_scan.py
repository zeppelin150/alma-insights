"""
Phase 5.3 — Full Production E2E NLP Scan
=========================================
Runs ScanOrchestrator against all ~888 tickets in C:/alma-insights/data/local_warehouse.db.
Measures classification coverage, novelty validation (batched), analyst reports,
meta-analyzer sub-taxonomy build, and pattern merge (post-meta).

Captures before/after metrics for comparison with prior subtaxonomy build.

Usage:
    python tests/run_e2e_full_scan.py
"""
import sys, os, json, time, sqlite3, logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "local_warehouse.db")
RESULTS_PATH = str(Path(__file__).resolve().parent.parent / "tests" / "e2e_scan_results.json")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("e2e_scan")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def capture_baseline():
    """Capture pre-scan metrics for comparison."""
    conn = get_db()
    baseline = {}

    baseline["tickets"] = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    baseline["conversations"] = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    baseline["trcs"] = conn.execute("SELECT COUNT(DISTINCT trc_code) FROM tickets").fetchone()[0]

    # Existing classifications
    baseline["classifications"] = conn.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications"
    ).fetchone()[0]
    baseline["novels_before"] = conn.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications WHERE is_novel = 1"
    ).fetchone()[0]

    # Check for novelty_verdict column
    try:
        conn.execute("SELECT novelty_verdict FROM nlp_ticket_classifications LIMIT 1")
        baseline["has_novelty_verdict_col"] = True
    except Exception:
        baseline["has_novelty_verdict_col"] = False

    # Existing sub-patterns
    baseline["sub_patterns"] = conn.execute("SELECT COUNT(*) FROM sub_patterns").fetchone()[0]
    baseline["sub_pattern_ngrams"] = conn.execute("SELECT COUNT(*) FROM sub_pattern_ngrams").fetchone()[0]
    baseline["sub_pattern_snapshots"] = conn.execute("SELECT COUNT(*) FROM sub_pattern_snapshots").fetchone()[0]
    baseline["nlp_findings"] = conn.execute("SELECT COUNT(*) FROM nlp_findings").fetchone()[0]

    # Existing analyst reports
    try:
        baseline["analyst_reports"] = conn.execute("SELECT COUNT(*) FROM analyst_reports").fetchone()[0]
    except Exception:
        baseline["analyst_reports"] = 0

    # Existing scans
    baseline["prior_scans"] = conn.execute("SELECT COUNT(*) FROM nlp_scan_runs").fetchone()[0]

    conn.close()
    return baseline


def capture_post_scan(scan_id):
    """Capture post-scan metrics for comparison."""
    conn = get_db()
    results = {"scan_id": scan_id}

    # ── Scan run status ──
    run = conn.execute(
        "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    results["status"] = run["status"]
    results["total_tickets"] = run["total_tickets"]
    results["total_batches"] = run["total_batches"]
    results["completed_batches"] = run["completed_batches"]
    results["estimated_cost_usd"] = run["estimated_cost_usd"]
    results["actual_cost_usd"] = run["actual_cost_usd"]
    results["total_input_tokens"] = run["total_input_tokens"]
    results["total_output_tokens"] = run["total_output_tokens"]

    # ── Classification counts ──
    cls = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) as novels,
               SUM(CASE WHEN is_novel = 0 THEN 1 ELSE 0 END) as not_novel
        FROM nlp_ticket_classifications WHERE scan_id = ?
    """, (scan_id,)).fetchone()
    results["classified_total"] = cls["total"]
    results["novels_total"] = cls["novels"]
    results["not_novel_total"] = cls["not_novel"]

    # ── Novelty verdicts (Phase 5.3 new) ──
    try:
        verdicts = conn.execute("""
            SELECT novelty_verdict, COUNT(*) as cnt
            FROM nlp_ticket_classifications
            WHERE scan_id = ?
            GROUP BY novelty_verdict
        """, (scan_id,)).fetchall()
        results["novelty_verdicts"] = {
            (v["novelty_verdict"] or "NULL"): v["cnt"] for v in verdicts
        }
    except Exception as e:
        results["novelty_verdicts"] = {"error": str(e)}

    # ── Classification quality sampling ──
    samples = conn.execute("""
        SELECT ticket_id, trc, sub_cluster, friction_type,
               sentiment_intensity, anomaly_flag, is_novel,
               sub_cluster_confidence, novelty_verdict, novelty_match
        FROM nlp_ticket_classifications
        WHERE scan_id = ?
        ORDER BY RANDOM()
        LIMIT 15
    """, (scan_id,)).fetchall()
    results["sample_classifications"] = [dict(s) for s in samples]

    # ── Field validation ──
    field_checks = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN sub_cluster IS NOT NULL AND LENGTH(sub_cluster) > 0 THEN 1 ELSE 0 END) as has_sub_cluster,
            SUM(CASE WHEN summary IS NOT NULL AND LENGTH(summary) > 10 THEN 1 ELSE 0 END) as has_summary,
            SUM(CASE WHEN sub_cluster_confidence >= 0 AND sub_cluster_confidence <= 1 THEN 1 ELSE 0 END) as valid_confidence,
            SUM(CASE WHEN sentiment_intensity >= 1 AND sentiment_intensity <= 5 THEN 1 ELSE 0 END) as valid_sentiment,
            SUM(CASE WHEN key_phrases IS NOT NULL AND LENGTH(key_phrases) > 2 THEN 1 ELSE 0 END) as has_key_phrases,
            SUM(CASE WHEN root_cause_hint IS NOT NULL AND LENGTH(root_cause_hint) > 2 THEN 1 ELSE 0 END) as has_root_cause
        FROM nlp_ticket_classifications WHERE scan_id = ?
    """, (scan_id,)).fetchone()
    results["field_validation"] = dict(field_checks)

    # ── Per-TRC classification distribution ──
    trc_dist = conn.execute("""
        SELECT trc, COUNT(*) as cnt,
               SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) as novels
        FROM nlp_ticket_classifications
        WHERE scan_id = ?
        GROUP BY trc ORDER BY cnt DESC
    """, (scan_id,)).fetchall()
    results["trc_classification_dist"] = [
        {"trc": t["trc"][:60], "count": t["cnt"], "novels": t["novels"]}
        for t in trc_dist
    ]

    # ── Batch details ──
    batches = conn.execute(
        "SELECT * FROM nlp_batches WHERE scan_id = ? ORDER BY batch_number",
        (scan_id,)
    ).fetchall()
    results["batch_count"] = len(batches)
    batch_details = []
    for b in batches:
        trc = b["trc"]
        if trc and trc.startswith("["):
            try:
                trcs = json.loads(trc)
                trc = f"[{len(trcs)} TRCs]"
            except Exception:
                pass
        batch_details.append({
            "batch_number": b["batch_number"],
            "status": b["status"],
            "ticket_count": b["ticket_count"],
            "latency_ms": b["latency_ms"],
            "retry_count": b["retry_count"],
            "trc": (trc or "")[:60],
        })
    results["batch_details"] = batch_details

    # ── Sub-patterns (subtaxonomy) ──
    pats = conn.execute("SELECT * FROM sub_patterns ORDER BY lifetime_tickets DESC").fetchall()
    results["sub_patterns_total"] = len(pats)
    results["sub_patterns"] = []
    for p in pats:
        d = dict(p)
        results["sub_patterns"].append({
            "sub_pattern": (d.get("sub_pattern") or "")[:80],
            "trc_code": (d.get("trc_code") or d.get("trc") or "")[:60],
            "tier": d.get("tier", "?"),
            "lifetime_tickets": d.get("lifetime_tickets", 0),
            "label": (d.get("label") or "")[:60],
            "friction_type": d.get("friction_type", ""),
        })

    # ── N-grams and snapshots ──
    results["sub_pattern_ngrams"] = conn.execute("SELECT COUNT(*) FROM sub_pattern_ngrams").fetchone()[0]
    results["sub_pattern_snapshots"] = conn.execute("SELECT COUNT(*) FROM sub_pattern_snapshots").fetchone()[0]

    # ── NLP findings ──
    findings = conn.execute(
        "SELECT * FROM nlp_findings WHERE scan_id = ?", (scan_id,)
    ).fetchall()
    results["nlp_findings_total"] = len(findings)
    results["nlp_findings"] = [
        {
            "finding_type": f["finding_type"],
            "title": (f["title"] or "")[:80],
            "ticket_count": f["ticket_count"],
            "impact_score": f["impact_score"],
        }
        for f in findings
    ]

    # ── Analyst reports (Phase 5.3 key metric) ──
    try:
        areps = conn.execute(
            "SELECT * FROM analyst_reports WHERE scan_id = ? ORDER BY created_at",
            (scan_id,)
        ).fetchall()
        results["analyst_reports_total"] = len(areps)
        results["analyst_reports"] = []
        for r in areps:
            d = dict(r)
            content = d.get("content", "")
            content_len = len(content) if content else 0
            # Try to get summary from JSON content
            try:
                parsed = json.loads(content) if content else {}
                summary_key = next(
                    (k for k in ["summary", "overall_assessment", "audit_summary", "validation_summary"]
                     if k in parsed), None
                )
                summary = str(parsed.get(summary_key, ""))[:200] if summary_key else ""
            except Exception:
                summary = content[:200] if content else ""

            results["analyst_reports"].append({
                "report_type": d.get("report_type", ""),
                "content_length": content_len,
                "summary_preview": summary,
                "created_at": d.get("created_at", ""),
            })
    except Exception as e:
        results["analyst_reports_total"] = 0
        results["analyst_reports"] = []
        results["analyst_reports_error"] = str(e)

    # ── Scan events timeline ──
    events = conn.execute("""
        SELECT event_type, status, COUNT(*) as cnt
        FROM scan_events WHERE scan_id = ?
        GROUP BY event_type, status ORDER BY cnt DESC
    """, (scan_id,)).fetchall()
    results["scan_events"] = [
        {"event_type": e["event_type"], "status": e["status"], "count": e["cnt"]}
        for e in events
    ]

    # Errors
    errors = conn.execute("""
        SELECT message, timestamp FROM scan_events
        WHERE scan_id = ? AND status = 'error'
        ORDER BY timestamp
    """, (scan_id,)).fetchall()
    results["errors"] = [{"message": e["message"], "time": e["timestamp"]} for e in errors]

    conn.close()
    return results


def print_separator(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def run_full_scan():
    """Run the full E2E scan and capture all metrics."""
    print_separator("PHASE 5.3 - FULL PRODUCTION E2E NLP SCAN")
    print(f"  DB: {DB_PATH}")
    print(f"  Time: {datetime.now().isoformat()}")
    print(f"  Pipeline: Classification -> Analyst(synth,audit,novelty+verdicts)")
    print(f"            -> Meta-Analyzer -> Pattern Merge -> Finalize")

    # ── 1. Capture baseline ──
    print_separator("1. PRE-SCAN BASELINE")
    baseline = capture_baseline()
    for k, v in baseline.items():
        print(f"  {k}: {v}")

    # ── 2. Ensure DB migration runs (adds novelty_verdict columns) ──
    print_separator("2. DB MIGRATION CHECK")
    try:
        from src.data.db_manager import DatabaseManager
        dm = DatabaseManager(Path(DB_PATH))
        dm.initialize()
        dm.close()
        print("  [OK] DatabaseManager.initialize() ran - schema up to date")

        # Verify column exists now
        conn = get_db()
        conn.execute("SELECT novelty_verdict, novelty_match FROM nlp_ticket_classifications LIMIT 1")
        print("  [OK] novelty_verdict + novelty_match columns present")
        conn.close()
    except Exception as e:
        print(f"  [WARN] Migration issue: {e}")

    # ── 3. Start scan ──
    print_separator("3. STARTING FULL SCAN")
    from src.agents.scan_orchestrator import ScanOrchestrator

    orch = ScanOrchestrator(DB_PATH)
    start_time = time.time()

    result = orch.start_scan(
        date_start="2025-01-01",
        date_end="2025-03-12",
        budget_cap=50.0,
        parallel_workers=3,
    )

    init_elapsed = time.time() - start_time
    print(f"  Scan initiated in {init_elapsed:.1f}s")
    safe_result = {k: v for k, v in result.items() if k != 'batches'}
    print(f"  Result: {json.dumps(safe_result, indent=4, default=str)}")

    scan_id = result.get("scan_id")
    if not scan_id:
        print("  ERROR: No scan_id returned!")
        return

    # ── 4. Poll until completion ──
    print_separator("4. POLLING SCAN PROGRESS")
    max_wait = 1800  # 30 minutes for full 888 tickets
    poll_interval = 10
    last_status = ""
    last_classified = 0

    conn = get_db()
    while time.time() - start_time < max_wait:
        run = conn.execute(
            "SELECT status, completed_batches, total_batches FROM nlp_scan_runs WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()
        if not run:
            print("  ERROR: scan_id not found in DB!")
            conn.close()
            return

        cls_count = conn.execute(
            "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()["n"]

        status = run["status"]
        completed = run["completed_batches"]
        total = run["total_batches"]
        elapsed = time.time() - start_time

        # Show progress (always show first, on status change, every 30s, or count change)
        if status != last_status or cls_count != last_classified or int(elapsed) % 30 < poll_interval:
            pct = (completed / total * 100) if total > 0 else 0
            rate = cls_count / elapsed if elapsed > 0 else 0
            print(f"  [{elapsed:6.0f}s] {status:20s} | {completed:3d}/{total} batches ({pct:5.1f}%) | "
                  f"{cls_count:4d} classified | {rate:.1f} cls/s")
            last_status = status
            last_classified = cls_count

        if status in ('analysis_complete', 'completed', 'failed', 'cancelled'):
            break
        time.sleep(poll_interval)
    else:
        print(f"  TIMEOUT: scan did not complete within {max_wait}s!")

    conn.close()
    total_elapsed = time.time() - start_time

    # ── 5. Capture post-scan results ──
    print_separator("5. POST-SCAN RESULTS")
    post = capture_post_scan(scan_id)
    post["total_elapsed_s"] = round(total_elapsed, 1)

    print(f"  Status: {post['status']}")
    print(f"  Total elapsed: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"  Tickets: {post['total_tickets']}")
    print(f"  Batches: {post['completed_batches']}/{post['total_batches']}")
    print(f"  Classified: {post['classified_total']}")
    print(f"  Novels: {post['novels_total']}")
    print(f"  Not novel: {post['not_novel_total']}")
    print(f"  Estimated cost: ${post['estimated_cost_usd']:.4f}" if post['estimated_cost_usd'] else "  Estimated cost: N/A")
    print(f"  Actual cost: ${post['actual_cost_usd']:.4f}" if post['actual_cost_usd'] else "  Actual cost: N/A")

    # ── Novelty verdicts (Phase 5.3) ──
    print_separator("5a. NOVELTY VERDICTS (Phase 5.3)")
    nv = post.get("novelty_verdicts", {})
    for verdict, count in sorted(nv.items()):
        print(f"  {verdict}: {count}")

    # ── Field validation ──
    print_separator("5b. FIELD VALIDATION")
    fv = post.get("field_validation", {})
    total = fv.get("total", 0)
    for field, count in sorted(fv.items()):
        if field == "total":
            continue
        pct = (count / total * 100) if total > 0 else 0
        ok = "[OK]" if pct >= 95 else "[WARN]" if pct >= 80 else "[FAIL]"
        print(f"  {ok} {field}: {count}/{total} ({pct:.1f}%)")

    # ── Analyst reports (Phase 5.3) ──
    print_separator("5c. ANALYST REPORTS (Phase 5.3)")
    print(f"  Total: {post['analyst_reports_total']}")
    for r in post.get("analyst_reports", []):
        print(f"  [{r['report_type']}] {r['content_length']} chars | {r['summary_preview'][:100]}")

    # ── Sub-patterns (subtaxonomy) ──
    print_separator("5d. SUBTAXONOMY BUILD")
    print(f"  Sub-patterns: {post['sub_patterns_total']} (was {baseline['sub_patterns']})")
    print(f"  N-grams: {post['sub_pattern_ngrams']} (was {baseline['sub_pattern_ngrams']})")
    print(f"  Snapshots: {post['sub_pattern_snapshots']} (was {baseline['sub_pattern_snapshots']})")
    print(f"  NLP findings: {post['nlp_findings_total']}")
    print(f"\n  Sub-pattern details:")
    for p in post.get("sub_patterns", []):
        print(f"    [{p['tier']}] {p['label'][:50] or p['sub_pattern'][:50]} | "
              f"trc={p['trc_code'][:40]} | tickets={p['lifetime_tickets']} | "
              f"friction={p['friction_type']}")

    # ── NLP findings ──
    if post.get("nlp_findings"):
        print(f"\n  Findings:")
        for f in post["nlp_findings"]:
            print(f"    [{f['finding_type']}] {f['title']} | "
                  f"tickets={f['ticket_count']} | impact={f['impact_score']}")

    # ── Batch summary ──
    print_separator("5e. BATCH SUMMARY")
    failed = [b for b in post.get("batch_details", []) if b["status"] == "failed"]
    completed_b = [b for b in post.get("batch_details", []) if b["status"] == "completed"]
    total_latency = sum(b["latency_ms"] for b in completed_b)
    avg_latency = total_latency / len(completed_b) if completed_b else 0
    max_latency = max((b["latency_ms"] for b in completed_b), default=0)
    total_retries = sum(b["retry_count"] for b in post.get("batch_details", []))
    print(f"  Completed: {len(completed_b)}")
    print(f"  Failed: {len(failed)}")
    print(f"  Avg latency: {avg_latency:.0f}ms")
    print(f"  Max latency: {max_latency}ms")
    print(f"  Total retries: {total_retries}")
    if failed:
        print(f"  Failed batches:")
        for b in failed:
            print(f"    Batch {b['batch_number']}: {b['trc']}")

    # ── TRC coverage ──
    print_separator("5f. TRC COVERAGE")
    trc_dist = post.get("trc_classification_dist", [])
    print(f"  TRCs classified: {len(trc_dist)}")
    for t in trc_dist[:15]:
        print(f"    {t['trc'][:55]}: {t['count']} classified, {t['novels']} novels")
    if len(trc_dist) > 15:
        print(f"    ... and {len(trc_dist) - 15} more TRCs")

    # ── Scan events ──
    print_separator("5g. SCAN EVENTS")
    for e in post.get("scan_events", []):
        print(f"  {e['event_type']:20s} | {e['status']:10s} | {e['count']}")

    # ── Errors ──
    if post.get("errors"):
        print_separator("5h. ERRORS")
        for e in post["errors"]:
            print(f"  [{e['time']}] {e['message']}")

    # ── 6. Comparison summary ──
    print_separator("6. COMPARISON: OLD vs NEW SUBTAXONOMY")
    print(f"  {'Metric':<35s} {'Before':>10s} {'After':>10s} {'Delta':>10s}")
    print(f"  {'-'*65}")

    comparisons = [
        ("Classifications", baseline["classifications"], post["classified_total"]),
        ("Novel tickets", baseline["novels_before"], post["novels_total"]),
        ("Sub-patterns", baseline["sub_patterns"], post["sub_patterns_total"]),
        ("N-grams", baseline["sub_pattern_ngrams"], post["sub_pattern_ngrams"]),
        ("Snapshots", baseline["sub_pattern_snapshots"], post["sub_pattern_snapshots"]),
        ("NLP findings", baseline["nlp_findings"], post["nlp_findings_total"]),
        ("Analyst reports", baseline["analyst_reports"], post["analyst_reports_total"]),
    ]
    for label, before, after in comparisons:
        delta = after - before
        sign = "+" if delta > 0 else ""
        print(f"  {label:<35s} {before:>10d} {after:>10d} {sign}{delta:>9d}")

    # Phase 5.3 specific metrics
    dup_count = nv.get("DUPLICATE", 0)
    merge_count = nv.get("MERGE", 0)
    valid_count = nv.get("VALID", nv.get("NULL", 0))
    print(f"\n  Phase 5.3 Novelty Verdicts:")
    print(f"    DUPLICATE (removed from novel): {dup_count}")
    print(f"    MERGE (advisory tag): {merge_count}")
    print(f"    VALID/untagged: {valid_count}")
    if post["novels_total"] > 0:
        dup_pct = dup_count / (post["novels_total"] + dup_count) * 100
        print(f"    False-positive rate: {dup_pct:.1f}% ({dup_count} of {post['novels_total'] + dup_count} original novels)")

    # ── 7. Save full results to JSON ──
    full_results = {
        "test_run": {
            "timestamp": datetime.now().isoformat(),
            "db_path": DB_PATH,
            "phase": "5.3",
            "pipeline": "Classification -> Analyst(synth,audit,novelty+verdicts) -> Meta-Analyzer -> Pattern Merge -> Finalize",
        },
        "baseline": baseline,
        "post_scan": post,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(full_results, f, indent=2, default=str)
    print(f"\n  Full results saved to: {RESULTS_PATH}")

    print_separator("SCAN COMPLETE")
    print(f"  Total time: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"  Final status: {post['status']}")
    print(f"  Coverage: {post['classified_total']}/{baseline['tickets']} tickets "
          f"({post['classified_total']/baseline['tickets']*100:.1f}%)" if baseline['tickets'] > 0 else "")

    return full_results


if __name__ == "__main__":
    run_full_scan()
