"""
Build 5.0+ — Full NLP Scanner Pipeline End-to-End Test

Runs the COMPLETE distributed NLP classification pipeline against the
real 888-ticket / 127-TRC dataset with 3 parallel workers and live
Gemini bridges.

Architecture exercised:
  - ScanOrchestrator (top-level coordinator)
  - 3 WorkerAgent instances (persistent, each with own bridge)
  - 1 Supervisor (deterministic health monitor)
  - 1 RateGovernor (shared across all workers)
  - 1 BatchPacker (dynamic batch sizing with TRC learning)
  - 1 AnalystAgent (post-scan cross-TRC synthesis, own bridge)
  - NLPMetaAnalyzer (Layer 2 sub-pattern detection)
  - ToolRegistry per-ticket classification storage
  - Canary probes, retry sweep, watchdog, stall escalation

Pipeline phases:
  1. Preflight: boot 3 bridges, canary probes, spawn workers + supervisor
  2. Classification: batched per-TRC ticket classification via queue
  3. Retry sweep: one final pass for any failed batches
  4. Analyst: cross-TRC synthesis, quality audit, novelty validation, pattern merge
  5. Meta-analyzer: sub-pattern detection, finding generation

Expected runtime: ~8-18 min with 3 workers (depends on API latency).

Usage:
  python tests/test_nlp_full_e2e.py
"""

import io
import sys
import time
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

# ── Force UTF-8 stdout on Windows ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Logging: route internal logs to stdout ──
logging.basicConfig(
    level=logging.INFO,
    format="  [%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

# ── Constants ──
DB_PATH = Path("data/local_warehouse.db")
NUM_WORKERS = 8       # default; overridable via --workers
BUDGET_CAP = 50.0


def _parse_args():
    """Parse CLI args (--workers N)."""
    import argparse
    parser = argparse.ArgumentParser(description="Full NLP Pipeline E2E")
    parser.add_argument("--workers", type=int, default=NUM_WORKERS,
                        help=f"Parallel workers (default: {NUM_WORKERS})")
    return parser.parse_args()


def _fmt_elapsed(start_time):
    """Return MM:SS string from start_time."""
    elapsed = time.time() - start_time
    return f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"


def main():
    args = _parse_args()
    global NUM_WORKERS
    NUM_WORKERS = args.workers
    from src.agents.scan_orchestrator import ScanOrchestrator

    print(flush=True)
    print("=" * 70, flush=True)
    print("  FULL NLP SCANNER PIPELINE -- END-TO-END TEST", flush=True)
    print(f"  888 tickets | 127 TRCs | {NUM_WORKERS} parallel workers", flush=True)
    print("=" * 70, flush=True)
    start_time = time.time()

    # ── Step 1: Verify prerequisites ──
    print(flush=True)
    print("[1/6] Verifying prerequisites...", flush=True)

    if not DB_PATH.exists():
        print(f"  [ABORT] Database not found: {DB_PATH}", flush=True)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    ticket_count = conn.execute(
        "SELECT COUNT(DISTINCT ticket_id) FROM conversations"
    ).fetchone()[0]
    trc_count = conn.execute(
        "SELECT COUNT(DISTINCT trc_code) FROM conversations "
        "WHERE trc_code IS NOT NULL AND trc_code != ''"
    ).fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM conversations"
    ).fetchone()
    date_start = date_range[0][:10]
    date_end = date_range[1][:10]

    print(f"  DB: {ticket_count} tickets, {trc_count} TRCs", flush=True)
    print(f"  Date range: {date_start} to {date_end}", flush=True)

    # Check ACP bridge (Gemini CLI must be available)
    from src.agents.acp_bridge import ACPBridge
    cli_path = ACPBridge._find_gemini_cli()
    if not cli_path:
        print("  [ABORT] Gemini CLI not found (check gemini.cli_path in settings)", flush=True)
        conn.close()
        return 1
    print(f"  ACP bridge: gemini CLI at {cli_path}", flush=True)

    # Show TRC distribution
    print(flush=True)
    print("  Top 5 TRCs:", flush=True)
    top_trcs = conn.execute("""
        SELECT trc_code, COUNT(DISTINCT ticket_id) as n
        FROM conversations
        WHERE trc_code IS NOT NULL AND trc_code != ''
        GROUP BY trc_code ORDER BY n DESC LIMIT 5
    """).fetchall()
    for r in top_trcs:
        print(f"    {r['trc_code'][:55]:<55} tickets={r['n']:>3}", flush=True)
    conn.close()

    # ── Step 2: Clear previous scan data ──
    print(flush=True)
    print("[2/6] Clearing previous scan data...", flush=True)
    clear_conn = sqlite3.connect(str(DB_PATH))
    try:
        for table in ['nlp_ticket_classifications', 'nlp_batches',
                      'nlp_scan_runs', 'scan_events', 'nlp_findings',
                      'sub_patterns', 'sub_pattern_ngrams',
                      'sub_pattern_snapshots', 'analyst_reports',
                      'scan_progress', 'gemini_usage',
                      'ticket_index']:
            try:
                clear_conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass  # table may not exist
        clear_conn.commit()
        print("  Cleared scan tables", flush=True)
    finally:
        clear_conn.close()

    # ── Step 3: Boot orchestrator and start scan ──
    print(flush=True)
    print(f"[3/6] Booting ScanOrchestrator ({NUM_WORKERS} workers)...", flush=True)

    orchestrator = ScanOrchestrator(
        db_path=str(DB_PATH),
        num_workers=NUM_WORKERS,
    )

    t_start = time.time()
    scan_result = orchestrator.start_scan(
        date_start=date_start,
        date_end=date_end,
        parallel_workers=NUM_WORKERS,
        budget_cap=BUDGET_CAP,
        mode='full',
    )

    if 'error' in scan_result:
        print(f"  [ABORT] start_scan failed: {scan_result['error']}", flush=True)
        orchestrator.shutdown()
        return 1

    scan_id = scan_result['scan_id']
    total_batches = scan_result.get('total_batches', '?')
    total_tickets = scan_result.get('total_tickets', '?')

    print(f"  Scan ID:        {scan_id[:16]}...", flush=True)
    print(f"  Total batches:  {total_batches}", flush=True)
    print(f"  Total tickets:  {total_tickets}", flush=True)
    print(f"  Budget cap:     ${BUDGET_CAP:.2f}", flush=True)
    print(f"  Workers:        {NUM_WORKERS}", flush=True)

    # ── Step 4: Poll for progress ──
    print(flush=True)
    print("[4/6] Running classification pipeline...", flush=True)
    print(flush=True)

    last_status_msg = ""
    last_classified = 0
    poll_interval = 10  # seconds
    stall_count = 0
    max_stall = 60  # give up after 10 min of no progress

    while True:
        time.sleep(poll_interval)
        status = orchestrator.get_status(scan_id)

        if 'error' in status and status['error'] not in (
            'no active scan', 'scan not found'
        ):
            print(f"  [{_fmt_elapsed(start_time)}] [ERROR] {status['error']}",
                  flush=True)
            break

        scan_status = status.get('status', 'unknown')
        completed_batches = status.get('completed_batches', 0) or 0
        classified_tickets = status.get('classified_tickets', 0) or 0
        current_trc = status.get('current_batch_trc', '')

        # Build status line
        pct = (completed_batches / total_batches * 100) if total_batches else 0
        status_msg = (
            f"[{_fmt_elapsed(start_time)}] "
            f"{pct:5.1f}% | "
            f"batches: {completed_batches}/{total_batches} | "
            f"classified: {classified_tickets} | "
            f"status: {scan_status}"
        )
        if current_trc:
            status_msg += f" | trc: {current_trc[:40]}"

        # Only print if something changed
        if status_msg != last_status_msg:
            print(f"  {status_msg}", flush=True)
            last_status_msg = status_msg

        # Check for stall
        if classified_tickets == last_classified and classified_tickets > 0:
            stall_count += 1
        else:
            stall_count = 0
        last_classified = classified_tickets

        if stall_count >= max_stall:
            print(f"  [{_fmt_elapsed(start_time)}] [WARN] Stalled for "
                  f"{stall_count * poll_interval}s — aborting", flush=True)
            orchestrator.cancel_scan(scan_id)
            break

        # Check for completion
        if scan_status in ('completed', 'completed_with_errors', 'failed',
                           'cancelled'):
            break

    scan_elapsed = time.time() - t_start

    # ── Step 5: Analyze results ──
    print(flush=True)
    print("=" * 70, flush=True)
    ts = _fmt_elapsed(start_time)
    print(f"[5/6] [{ts}] Scan complete! Analyzing results...", flush=True)
    print("=" * 70, flush=True)

    final_status = orchestrator.get_status(scan_id)
    total_elapsed = time.time() - start_time

    # Query final stats from DB
    results_conn = sqlite3.connect(str(DB_PATH))
    results_conn.row_factory = sqlite3.Row

    classified_total = results_conn.execute(
        "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()['n']

    batches_completed = results_conn.execute(
        "SELECT COUNT(*) as n FROM nlp_batches "
        "WHERE scan_id = ? AND status = 'completed'",
        (scan_id,)
    ).fetchone()['n']

    batches_failed = results_conn.execute(
        "SELECT COUNT(*) as n FROM nlp_batches "
        "WHERE scan_id = ? AND status = 'failed'",
        (scan_id,)
    ).fetchone()['n']

    batches_total = results_conn.execute(
        "SELECT COUNT(*) as n FROM nlp_batches WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()['n']

    # Token/cost stats
    token_row = results_conn.execute("""
        SELECT COALESCE(SUM(input_tokens), 0) as total_in,
               COALESCE(SUM(output_tokens), 0) as total_out,
               COALESCE(SUM(cost_usd), 0.0) as total_cost
        FROM nlp_batches WHERE scan_id = ?
    """, (scan_id,)).fetchone()

    # Findings and sub-patterns
    findings_count = 0
    pattern_count = 0
    try:
        findings_count = results_conn.execute(
            "SELECT COUNT(*) FROM nlp_findings WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()[0]
        pattern_count = results_conn.execute(
            "SELECT COUNT(*) FROM sub_patterns"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Analyst reports
    analyst_count = 0
    try:
        analyst_count = results_conn.execute(
            "SELECT COUNT(*) FROM analyst_reports WHERE scan_id = ?",
            (scan_id,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # TRC coverage
    trcs_classified = results_conn.execute("""
        SELECT COUNT(DISTINCT trc) as n
        FROM nlp_ticket_classifications WHERE scan_id = ?
    """, (scan_id,)).fetchone()['n']

    # Per-TRC breakdown (top 5)
    trc_breakdown = results_conn.execute("""
        SELECT trc, COUNT(*) as n
        FROM nlp_ticket_classifications WHERE scan_id = ?
        GROUP BY trc ORDER BY n DESC LIMIT 10
    """, (scan_id,)).fetchall()

    # Field validation sample
    validation_errors = []
    sample_rows = results_conn.execute("""
        SELECT ticket_id, sentiment_intensity, sentiment_polarity,
               friction_type, anomaly_flag, sub_cluster, sub_cluster_confidence
        FROM nlp_ticket_classifications WHERE scan_id = ?
        LIMIT 50
    """, (scan_id,)).fetchall()

    valid_polarity = {'positive', 'negative', 'neutral', 'mixed'}
    valid_friction = {
        'billing_error', 'incorrect_charge', 'payment_failure',
        'subscription_issue', 'refund_problem', 'service_quality',
        'communication_gap', 'policy_confusion', 'technical_issue',
        'account_access', 'data_error', 'process_friction',
        'wait_time', 'staff_conduct', 'positive_feedback',
        'feature_request', 'credential_issue', 'scheduling_issue',
        'compliance_concern', 'onboarding_friction', 'other',
    }

    for row in sample_rows:
        tid = row['ticket_id']
        si = row['sentiment_intensity']
        if si is not None and (si < 1 or si > 5):
            validation_errors.append(f"{tid}: sentiment_intensity={si}")
        sp = row['sentiment_polarity']
        if sp and sp.lower() not in valid_polarity:
            validation_errors.append(f"{tid}: polarity='{sp}'")
        ft = row['friction_type']
        if ft and ft.lower() not in valid_friction:
            validation_errors.append(f"{tid}: friction='{ft}'")
        sc = row['sub_cluster_confidence']
        if sc is not None and (sc < 0.0 or sc > 1.0):
            validation_errors.append(f"{tid}: confidence={sc}")

    # Scan events timeline
    events = results_conn.execute("""
        SELECT timestamp, event_type, status, message
        FROM scan_events WHERE scan_id = ?
        ORDER BY timestamp
    """, (scan_id,)).fetchall()

    results_conn.close()

    # ── Print results ──
    mins = int(total_elapsed // 60)
    secs = int(total_elapsed % 60)

    print(flush=True)
    print("  TIMING", flush=True)
    print("  ------", flush=True)
    print(f"  Total wall time:    {mins}m {secs}s ({total_elapsed:.1f}s)", flush=True)
    print(f"  Scan time:          {scan_elapsed:.1f}s", flush=True)
    throughput = classified_total / (scan_elapsed / 60) if scan_elapsed > 0 else 0
    print(f"  Throughput:         {throughput:.1f} tickets/min", flush=True)
    print(f"  Per-ticket avg:     {scan_elapsed / max(classified_total, 1):.2f}s",
          flush=True)

    print(flush=True)
    print("  CLASSIFICATION RESULTS", flush=True)
    print("  ----------------------", flush=True)
    print(f"  Tickets classified: {classified_total}", flush=True)
    print(f"  TRCs covered:       {trcs_classified}/{trc_count}", flush=True)
    print(f"  Batches completed:  {batches_completed}/{batches_total}", flush=True)
    print(f"  Batches failed:     {batches_failed}", flush=True)
    print(f"  Final status:       {final_status.get('status', '?')}", flush=True)

    print(flush=True)
    print("  COST & TOKENS", flush=True)
    print("  -------------", flush=True)
    print(f"  Input tokens:       {token_row['total_in']:,}", flush=True)
    print(f"  Output tokens:      {token_row['total_out']:,}", flush=True)
    print(f"  Total cost:         ${token_row['total_cost']:.4f}", flush=True)

    print(flush=True)
    print("  POST-SCAN ANALYSIS", flush=True)
    print("  ------------------", flush=True)
    print(f"  Analyst reports:    {analyst_count}", flush=True)
    print(f"  Meta findings:      {findings_count}", flush=True)
    print(f"  Sub-patterns:       {pattern_count}", flush=True)

    print(flush=True)
    print("  TOP 10 TRCs BY CLASSIFICATION COUNT", flush=True)
    print("  ------------------------------------", flush=True)
    for row in trc_breakdown:
        print(f"    {row['trc'][:55]:<55} n={row['n']:>3}", flush=True)

    print(flush=True)
    print("  FIELD VALIDATION", flush=True)
    print("  ----------------", flush=True)
    if validation_errors:
        print(f"  {len(validation_errors)} validation errors (sample of 50):",
              flush=True)
        for err in validation_errors[:10]:
            print(f"    {err}", flush=True)
    else:
        print(f"  All {len(sample_rows)} sampled classifications passed "
              f"field validation", flush=True)

    # ── Event timeline (key milestones only) ──
    print(flush=True)
    print("  EVENT TIMELINE (key milestones)", flush=True)
    print("  -------------------------------", flush=True)
    milestone_types = {'preflight', 'info', 'analyst', 'error'}
    for ev in events:
        if ev['event_type'] in milestone_types and ev['status'] in (
            'complete', 'error', 'info'
        ):
            ts = ev['timestamp'][11:19] if ev['timestamp'] else '??:??:??'
            print(f"    [{ts}] {ev['message'][:70]}", flush=True)

    # ── Step 6: Cleanup ──
    print(flush=True)
    print("=" * 70, flush=True)
    print("[6/6] Cleanup...", flush=True)
    print("=" * 70, flush=True)

    orchestrator.shutdown()
    print("  Orchestrator shutdown", flush=True)

    # ── Final verdict ──
    print(flush=True)
    print("=" * 70, flush=True)
    print("  FINAL SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"  Wall time:        {mins}m {secs}s", flush=True)
    print(f"  Classified:       {classified_total}/{ticket_count}", flush=True)
    print(f"  TRCs covered:     {trcs_classified}/{trc_count}", flush=True)
    print(f"  Throughput:       {throughput:.1f} tickets/min", flush=True)
    print(f"  Batches:          {batches_completed} OK, {batches_failed} failed",
          flush=True)
    print(f"  Cost:             ${token_row['total_cost']:.4f}", flush=True)
    print(f"  Analyst reports:  {analyst_count}", flush=True)
    print(f"  Meta findings:    {findings_count}", flush=True)
    print(f"  Validation:       {'PASS' if not validation_errors else 'FAIL'}",
          flush=True)

    # Overall pass/fail
    passed = (
        classified_total >= ticket_count * 0.98  # at least 98% classified
        and batches_failed <= batches_total * 0.10  # at most 10% batch failures
        and not validation_errors
    )
    status_label = "[OK] FULL NLP PIPELINE PASSED" if passed else "[WARN] PARTIAL"
    print(f"  STATUS:           {status_label}", flush=True)
    print("=" * 70, flush=True)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
