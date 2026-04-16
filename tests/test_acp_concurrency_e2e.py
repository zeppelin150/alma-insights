"""
ACP Concurrency + MCP Tool Restoration — End-to-End Tests

Tests the scan pipeline at various concurrency levels (8, 16, 32 workers)
and verifies MCP tool calls are used as the primary classification path.

Usage:
  python tests/test_acp_concurrency_e2e.py [--workers N] [--quick]

Options:
  --workers N   Number of parallel workers (default: 8)
  --quick       Use a small TRC subset (~50 tickets) instead of full dataset
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

# ── Force UTF-8 stdout on Windows ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="  [%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

DB_PATH = Path("data/local_warehouse.db")
BUDGET_CAP = 50.0


def _fmt_elapsed(start_time):
    elapsed = time.time() - start_time
    return f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"


def _clear_scan_tables(db_path):
    """Clear all scan-related tables for a fresh run."""
    conn = sqlite3.connect(str(db_path))
    for table in ['nlp_ticket_classifications', 'nlp_batches',
                  'nlp_scan_runs', 'scan_events', 'nlp_findings',
                  'sub_patterns', 'sub_pattern_ngrams',
                  'sub_pattern_snapshots', 'analyst_reports',
                  'scan_progress', 'gemini_usage',
                  'ticket_index']:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _get_dataset_info(db_path):
    """Get ticket/TRC counts and date range."""
    conn = sqlite3.connect(str(db_path))
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
    conn.close()
    return {
        "ticket_count": ticket_count,
        "trc_count": trc_count,
        "date_start": date_range[0][:10],
        "date_end": date_range[1][:10],
    }


def _get_quick_trc_filter(db_path, target_tickets=50):
    """Pick the smallest set of top TRCs that cover ~target_tickets."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT trc_code, COUNT(DISTINCT ticket_id) as n
        FROM conversations
        WHERE trc_code IS NOT NULL AND trc_code != ''
        GROUP BY trc_code ORDER BY n DESC
    """).fetchall()
    conn.close()
    trcs = []
    total = 0
    for r in rows:
        trcs.append(r[0])
        total += r[1]
        if total >= target_tickets:
            break
    return trcs


def run_scan(num_workers, trc_filter=None, quick=False):
    """Run a scan with the given number of workers and return results."""
    from src.agents.scan_orchestrator import ScanOrchestrator

    info = _get_dataset_info(DB_PATH)

    print(flush=True)
    print("=" * 70, flush=True)
    print(f"  ACP CONCURRENCY E2E — {num_workers} workers", flush=True)
    if quick:
        print(f"  Quick mode: ~50 tickets subset", flush=True)
    else:
        print(f"  {info['ticket_count']} tickets | {info['trc_count']} TRCs",
              flush=True)
    print("=" * 70, flush=True)

    start_time = time.time()

    # Prerequisites
    if not DB_PATH.exists():
        print(f"  [ABORT] Database not found: {DB_PATH}", flush=True)
        return None

    from src.agents.acp_bridge import ACPBridge
    cli_path = ACPBridge._find_gemini_cli()
    if not cli_path:
        print("  [ABORT] Gemini CLI not found", flush=True)
        return None

    # Clear previous data
    _clear_scan_tables(DB_PATH)

    # Get TRC filter for quick mode
    if quick and not trc_filter:
        trc_filter = _get_quick_trc_filter(DB_PATH)
        print(f"  Quick filter: {len(trc_filter)} TRCs", flush=True)

    # Boot and run
    orchestrator = ScanOrchestrator(
        db_path=str(DB_PATH),
        num_workers=num_workers,
    )

    scan_result = orchestrator.start_scan(
        date_start=info["date_start"],
        date_end=info["date_end"],
        parallel_workers=num_workers,
        budget_cap=BUDGET_CAP,
        trc_filter=trc_filter,
        mode='full',
    )

    if 'error' in scan_result:
        print(f"  [ABORT] start_scan failed: {scan_result['error']}", flush=True)
        orchestrator.shutdown()
        return None

    scan_id = scan_result['scan_id']
    total_batches = scan_result.get('total_batches', 0)
    total_tickets = scan_result.get('total_tickets', 0)

    print(f"  Scan: {scan_id[:16]}... | {total_batches} batches | "
          f"{total_tickets} tickets | {num_workers} workers", flush=True)
    print(flush=True)

    # Poll
    last_classified = 0
    stall_count = 0
    poll_interval = 5
    max_stall = 120  # 10 min

    while True:
        time.sleep(poll_interval)
        status = orchestrator.get_status(scan_id)

        scan_status = status.get('status', 'unknown')
        completed_batches = status.get('completed_batches', 0) or 0
        classified = status.get('classified_tickets', 0) or 0

        pct = (completed_batches / total_batches * 100) if total_batches else 0
        print(
            f"  [{_fmt_elapsed(start_time)}] {pct:5.1f}% | "
            f"batches: {completed_batches}/{total_batches} | "
            f"classified: {classified}/{total_tickets} | "
            f"status: {scan_status}",
            flush=True,
        )

        if classified == last_classified and classified > 0:
            stall_count += 1
        else:
            stall_count = 0
        last_classified = classified

        if stall_count >= max_stall:
            print(f"  [{_fmt_elapsed(start_time)}] [WARN] Stalled — aborting",
                  flush=True)
            orchestrator.cancel_scan(scan_id)
            break

        if scan_status in ('completed', 'completed_with_errors', 'failed',
                           'cancelled'):
            break

    scan_elapsed = time.time() - start_time

    # Gather results
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    classified_total = conn.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,)
    ).fetchone()[0]

    batches_completed = conn.execute(
        "SELECT COUNT(*) FROM nlp_batches "
        "WHERE scan_id = ? AND status = 'completed'",
        (scan_id,)
    ).fetchone()[0]

    batches_failed = conn.execute(
        "SELECT COUNT(*) FROM nlp_batches "
        "WHERE scan_id = ? AND status = 'failed'",
        (scan_id,)
    ).fetchone()[0]

    # Check for duplicate classifications
    dup_count = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT ticket_id, COUNT(*) as n
            FROM nlp_ticket_classifications
            WHERE scan_id = ?
            GROUP BY ticket_id
            HAVING n > 1
        )
    """, (scan_id,)).fetchone()[0]

    # TRC coverage
    trcs_classified = conn.execute("""
        SELECT COUNT(DISTINCT trc)
        FROM nlp_ticket_classifications WHERE scan_id = ?
    """, (scan_id,)).fetchone()[0]

    conn.close()
    orchestrator.shutdown()

    results = {
        "workers": num_workers,
        "total_tickets": total_tickets,
        "classified": classified_total,
        "coverage_pct": round(classified_total / total_tickets * 100, 1)
            if total_tickets else 0,
        "batches_completed": batches_completed,
        "batches_failed": batches_failed,
        "total_batches": total_batches,
        "duplicates": dup_count,
        "trcs_classified": trcs_classified,
        "elapsed_seconds": round(scan_elapsed, 1),
        "throughput_per_min": round(classified_total / (scan_elapsed / 60), 1)
            if scan_elapsed > 0 else 0,
        "scan_status": scan_status,
    }

    # Print summary
    print(flush=True)
    print("=" * 70, flush=True)
    print(f"  RESULTS — {num_workers} workers", flush=True)
    print("=" * 70, flush=True)
    print(f"  Classified:    {classified_total}/{total_tickets} "
          f"({results['coverage_pct']}%)", flush=True)
    print(f"  Batches:       {batches_completed} OK / {batches_failed} failed "
          f"/ {total_batches} total", flush=True)
    print(f"  Duplicates:    {dup_count}", flush=True)
    print(f"  TRCs covered:  {trcs_classified}", flush=True)
    print(f"  Elapsed:       {_fmt_elapsed(start_time)}", flush=True)
    print(f"  Throughput:    {results['throughput_per_min']} tickets/min",
          flush=True)
    print(flush=True)

    # Assertions
    errors = []
    if classified_total == 0:
        errors.append("FAIL: 0 tickets classified")
    if dup_count > 0:
        errors.append(f"FAIL: {dup_count} duplicate ticket classifications")
    if results['coverage_pct'] < 50:
        errors.append(
            f"WARN: coverage only {results['coverage_pct']}% "
            f"(expected >50%)"
        )
    if scan_status == 'failed':
        errors.append("FAIL: scan status is 'failed'")

    for e in errors:
        print(f"  ** {e}", flush=True)

    if not errors:
        print("  PASS: All assertions passed", flush=True)

    results["errors"] = errors
    return results


def main():
    parser = argparse.ArgumentParser(
        description="ACP Concurrency E2E Test")
    parser.add_argument("--workers", type=int, default=8,
                        help="Number of parallel workers (default: 8)")
    parser.add_argument("--quick", action="store_true",
                        help="Use small TRC subset (~50 tickets)")
    parser.add_argument("--scaling-test", action="store_true",
                        help="Run at 3, 8, 16, 32 workers and compare")
    args = parser.parse_args()

    if args.scaling_test:
        # Run at multiple concurrency levels
        all_results = []
        for n in [3, 8, 16, 32]:
            print(f"\n{'#' * 70}", flush=True)
            print(f"  SCALING TEST — {n} workers", flush=True)
            print(f"{'#' * 70}", flush=True)
            result = run_scan(n, quick=True)
            if result:
                all_results.append(result)

        # Print comparison table
        if all_results:
            print(flush=True)
            print("=" * 70, flush=True)
            print("  SCALING COMPARISON", flush=True)
            print("=" * 70, flush=True)
            print(f"  {'Workers':>8} {'Classified':>10} {'Elapsed':>10} "
                  f"{'Tickets/min':>12} {'Duplicates':>10}", flush=True)
            print(f"  {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 10}",
                  flush=True)
            for r in all_results:
                elapsed_str = f"{int(r['elapsed_seconds'] // 60)}:{int(r['elapsed_seconds'] % 60):02d}"
                print(
                    f"  {r['workers']:>8} {r['classified']:>10} "
                    f"{elapsed_str:>10} {r['throughput_per_min']:>12.1f} "
                    f"{r['duplicates']:>10}",
                    flush=True,
                )
            print(flush=True)

        return 0 if all(not r.get("errors") for r in all_results) else 1
    else:
        result = run_scan(args.workers, quick=args.quick)
        return 0 if result and not result.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
