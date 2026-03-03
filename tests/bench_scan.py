"""
Build 6.1 NLP Scanner Benchmark
Runs full scan + polls in a single process so bridge subprocesses stay alive.
"""
import sys, os, time, sqlite3, threading, json
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path("data/local_warehouse.db")
DATE_START = "2025-01-01"
DATE_END = "2025-03-12"


def get_conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def main():
    from src.agents.scan_orchestrator import ScanOrchestrator

    orch = ScanOrchestrator(str(DB_PATH))

    print("=" * 65)
    print("  BUILD 6.1 — NLP SCANNER BENCHMARK")
    print("=" * 65)
    print(f"  Date range: {DATE_START} to {DATE_END}")

    # ── Launch scan ──
    t0 = time.time()
    result = orch.start_scan(DATE_START, DATE_END, budget_cap=50.0)
    scan_id = result.get("scan_id", "")
    total_b = result.get("total_batches", 0)
    total_t = result.get("total_tickets", 0)
    print(f"  Scan ID:    {scan_id[:12]}")
    print(f"  Batches:    {total_b}")
    print(f"  Tickets:    {total_t}")
    print(f"  Workers:    {result.get('num_workers', 0)}")
    print(f"  Est cost:   ${result.get('estimated_cost', 0):.2f}")
    print("-" * 65)

    # ── Poll loop ──
    prev_completed = -1
    while True:
        time.sleep(15)
        conn = get_conn()
        scan = conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not scan:
            print("ERROR: scan row not found")
            conn.close()
            break

        status = scan["status"]
        completed = scan["completed_batches"] or 0
        cost = scan["actual_cost_usd"] or 0
        elapsed = time.time() - t0

        cls = conn.execute(
            "SELECT COUNT(DISTINCT ticket_id) "
            "FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()[0]

        failed_n = conn.execute(
            "SELECT COUNT(*) FROM nlp_batches "
            "WHERE scan_id = ? AND status = 'failed'",
            (scan_id,),
        ).fetchone()[0]

        running_n = conn.execute(
            "SELECT COUNT(*) FROM nlp_batches "
            "WHERE scan_id = ? AND status = 'running'",
            (scan_id,),
        ).fetchone()[0]

        conn.close()

        if completed != prev_completed or status not in ("running",):
            pct = cls / max(total_t, 1) * 100
            print(
                f"  [{elapsed:5.0f}s]  {status:22s}  "
                f"batches={completed}/{total_b}  run={running_n}  "
                f"cls={cls} ({pct:.0f}%)  fail={failed_n}  "
                f"${cost:.3f}"
            )
            prev_completed = completed

        terminal = (
            "completed", "completed_with_errors", "scan_complete",
            "analysis_complete", "failed", "cancelled",
            "budget_exceeded", "quota_exhausted",
        )
        if status in terminal:
            break

    total_time = time.time() - t0

    # ── Final report ──
    conn = get_conn()
    scan = conn.execute(
        "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
    ).fetchone()
    cls = conn.execute(
        "SELECT COUNT(DISTINCT ticket_id) "
        "FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()[0]

    print()
    print("=" * 65)
    print("  FINAL RESULTS")
    print("=" * 65)
    print(f"  Status:       {scan['status']}")
    print(f"  Total time:   {total_time:.0f}s  ({total_time / 60:.1f} min)")
    print(f"  Batches:      {scan['completed_batches']}/{scan['total_batches']}")
    pct = cls / max(scan["total_tickets"], 1) * 100
    print(f"  Classified:   {cls}/{scan['total_tickets']}  ({pct:.1f}%)")
    print(f"  Cost:         ${scan['actual_cost_usd'] or 0:.4f}")
    if scan["error_log"]:
        print(f"  Error log:    {scan['error_log']}")

    # Failed batches
    failed = conn.execute(
        "SELECT batch_number, trc, error_message, retry_count "
        "FROM nlp_batches WHERE scan_id = ? AND status = 'failed' "
        "ORDER BY batch_number",
        (scan_id,),
    ).fetchall()
    if failed:
        print(f"\n  FAILED BATCHES ({len(failed)}):")
        for b in failed:
            err = (b["error_message"] or "(none)")[:80]
            print(
                f"    B{b['batch_number']:2d}  retries={b['retry_count']}  "
                f"err={err}"
            )
            print(f"         trc={b['trc'][:55]}")
    else:
        print(f"  Failed:       0  (ALL SUCCEEDED)")

    # Batch timing
    batches = conn.execute(
        "SELECT batch_number, trc, status, ticket_count, latency_ms, "
        "retry_count, error_message "
        "FROM nlp_batches WHERE scan_id = ? ORDER BY batch_number",
        (scan_id,),
    ).fetchall()

    latencies = [
        b["latency_ms"] for b in batches if b["latency_ms"] and b["latency_ms"] > 0
    ]
    if latencies:
        print(f"\n  BATCH TIMING ({len(batches)} batches):")
        print(f"    Avg:        {sum(latencies) / len(latencies) / 1000:.1f}s")
        print(f"    Min:        {min(latencies) / 1000:.1f}s")
        print(f"    Max:        {max(latencies) / 1000:.1f}s")
        srt = sorted(latencies)
        print(f"    Median:     {srt[len(srt) // 2] / 1000:.1f}s")
        ok_tix = sum(
            b["ticket_count"] or 0
            for b in batches
            if b["status"] == "completed"
        )
        print(f"    Throughput: {ok_tix / max(total_time, 1):.2f} tickets/sec")

    print(f"\n  PER-BATCH DETAIL:")
    for b in batches:
        lat = f"{b['latency_ms'] / 1000:.1f}s" if b["latency_ms"] else "---"
        ret = f" R{b['retry_count']}" if b["retry_count"] else ""
        err = " *ERR*" if b["error_message"] else ""
        print(
            f"    B{b['batch_number']:2d} [{b['status']:22s}] "
            f"tix={b['ticket_count'] or 0:3d}  "
            f"time={lat:>7s}{ret}{err}  "
            f"{b['trc'][:50]}"
        )

    # Retry / sweep events
    events = conn.execute(
        "SELECT timestamp, event_type, status, message "
        "FROM scan_events WHERE scan_id = ? AND "
        "(message LIKE '%retry%' OR message LIKE '%sweep%' OR "
        " message LIKE '%Retry%' OR message LIKE '%Sweep%') "
        "ORDER BY timestamp",
        (scan_id,),
    ).fetchall()
    if events:
        print(f"\n  RETRY / SWEEP EVENTS:")
        for e in events:
            print(f"    {e['timestamp']}  [{e['event_type']}]  {e['message']}")
    else:
        print(f"\n  RETRY / SWEEP EVENTS: (none)")

    # Error capture verification
    f_with = conn.execute(
        "SELECT COUNT(*) FROM nlp_batches "
        "WHERE scan_id = ? AND status = 'failed' "
        "AND error_message IS NOT NULL AND error_message != ''",
        (scan_id,),
    ).fetchone()[0]
    f_without = conn.execute(
        "SELECT COUNT(*) FROM nlp_batches "
        "WHERE scan_id = ? AND status = 'failed' "
        "AND (error_message IS NULL OR error_message = '')",
        (scan_id,),
    ).fetchone()[0]
    print(f"\n  ERROR CAPTURE VERIFICATION:")
    print(f"    Failed with error_message:    {f_with}")
    print(f"    Failed WITHOUT error_message: {f_without}")
    if f_without == 0 and len(failed) > 0:
        print(f"    --> BUILD 6.1 ERROR CAPTURE: WORKING")
    elif len(failed) == 0:
        print(f"    --> No failures (all batches succeeded)")
    else:
        print(f"    --> BUILD 6.1 ERROR CAPTURE: PARTIAL ({f_without} missing)")

    print("=" * 65)
    conn.close()


if __name__ == "__main__":
    main()
