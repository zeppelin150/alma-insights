"""
Build 9.0 — Full VOC Pipeline End-to-End Test

Runs the COMPLETE multi-perspective VOC Root Cause Analysis pipeline
against the real 888-ticket / 127-TRC dataset with live Gemini bridge pool.

Phases:
  0. Plan (fast, no Gemini)
  1. Batched TRC analysis (~25-35 batched calls via 4-bridge pool, priority 0)
  2a. Accumulator (6-8 rounds pipelined into Phase 1 whitespace, priority 1)
  2b. Specialists (3 parallel calls after Phase 1, priority 0)
  3. Convergence (1 call via run_resilient)

Expected runtime: ~15-22 min with 4 bridges.
"""

import io
import sys
import time
import json
import logging
from pathlib import Path
from datetime import datetime

# ── Force UTF-8 stdout on Windows (Gemini output contains unicode) ──
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Logging: route internal logs to stdout so we see everything in one stream ──
logging.basicConfig(
    level=logging.INFO,
    format="  [%(name)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
# Quiet down noisy loggers but keep alma.* at INFO
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


def _fmt_elapsed(start_time):
    """Return MM:SS string from start_time."""
    elapsed = time.time() - start_time
    return f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"


def main():
    from src.agents.report_bridge_client import ReportBridgeClient
    from src.agents.report_orchestrator import ReportOrchestrator
    from src.data.db_manager import DatabaseManager
    from src.data.voc_builder import VOCBuilder

    print(flush=True)
    print("=" * 70, flush=True)
    print("  FULL VOC PIPELINE -- END-TO-END TEST  (Build 9.0)", flush=True)
    print("  888 tickets | 127 TRCs | 4 parallel bridges", flush=True)
    print("=" * 70, flush=True)
    start_time = time.time()

    # ── Step 1: Boot infrastructure ──
    print(flush=True)
    print("[1/5] Booting infrastructure...", flush=True)

    db = DatabaseManager(Path("data/local_warehouse.db"))
    db.initialize()

    ticket_count = db.conn.execute(
        "SELECT COUNT(*) FROM conversations"
    ).fetchone()[0]
    trc_count = db.conn.execute(
        "SELECT COUNT(DISTINCT trc_code) FROM conversations "
        "WHERE trc_code != ''"
    ).fetchone()[0]
    print(f"  DB: {ticket_count} tickets, {trc_count} TRCs", flush=True)

    # Build single client for synthesis + fallback
    client = ReportBridgeClient(
        model="gemini-2.5-flash",
        pii_redaction=True,
    )
    print(f"  Bridge available: {client.is_available()}", flush=True)
    if not client.is_available():
        print("  [ABORT] Bridge not available", flush=True)
        return 1

    # Boot orchestrator (4-bridge pool)
    print("  Booting orchestrator (4 bridges)...", flush=True)
    t_boot = time.time()
    orchestrator = None
    try:
        orchestrator = ReportOrchestrator(
            db_path=str(Path("data/local_warehouse.db")),
            model="gemini-2.5-flash",
            num_bridges=4,
        )
        orchestrator.boot()
        boot_elapsed = time.time() - t_boot
        print(f"  Orchestrator booted in {boot_elapsed:.1f}s", flush=True)
    except Exception as e:
        print(f"  [WARN] Orchestrator boot failed: {e}", flush=True)
        print("  Falling back to sequential (single bridge)", flush=True)

    # ── Step 2: Build VOC plan ──
    print(flush=True)
    print("[2/5] Building VOC plan...", flush=True)
    t_plan = time.time()

    progress_log = []
    last_pct = [-1]  # mutable for closure

    def on_progress(message, percent):
        progress_log.append({
            "time": time.time() - start_time,
            "message": message,
            "percent": percent,
        })
        ts = _fmt_elapsed(start_time)
        if percent is not None and percent != last_pct[0]:
            print(f"  [{ts}] {percent:3d}% | {message}", flush=True)
            last_pct[0] = percent
        elif percent is None:
            print(f"  [{ts}]  --  | {message}", flush=True)

    builder = VOCBuilder(
        db=db,
        gemini_client=client,
        progress_callback=on_progress,
        orchestrator=orchestrator,
    )

    date_range = db.get_date_range()
    plan = builder.plan(
        date_start=date_range[0],
        date_end=date_range[1],
    )
    plan_elapsed = time.time() - t_plan

    print(f"  Date range:     {date_range[0][:10]} to {date_range[1][:10]}",
          flush=True)
    print(f"  TRC plans:      {len(plan['trc_plans'])}", flush=True)
    print(f"  Total sampled:  {plan['total_sampled']} tickets", flush=True)
    print(f"  Gemini calls:   {plan['total_gemini_calls']}", flush=True)
    print(f"  Est cost:       ${plan['est_cost_usd']:.2f}", flush=True)
    print(f"  Est time:       {plan['est_time_min']:.1f} min", flush=True)
    print(f"  Has NLP data:   {plan.get('has_nlp_data', False)}", flush=True)
    print(f"  Plan built in:  {plan_elapsed:.1f}s", flush=True)

    # Show top 5 TRCs by ticket count
    print(flush=True)
    print("  Top 5 TRCs:", flush=True)
    sorted_trcs = sorted(
        plan["trc_plans"],
        key=lambda t: t["total_tickets"],
        reverse=True,
    )
    for tp in sorted_trcs[:5]:
        print(f"    {tp['trc'][:55]:<55} "
              f"tickets={tp['total_tickets']:>3}, sampled={tp['sampled']:>3}",
              flush=True)

    # ── Step 3: Run full pipeline ──
    print(flush=True)
    print("[3/5] Running full VOC pipeline (target: ~15-22 min)...",
          flush=True)
    print(flush=True)

    t_run = time.time()
    try:
        result = builder.run(
            date_start=date_range[0],
            date_end=date_range[1],
        )
        run_elapsed = time.time() - t_run
    except Exception as e:
        run_elapsed = time.time() - t_run
        print(f"\n  [FAIL] Pipeline failed after {run_elapsed:.1f}s: {e}",
              flush=True)
        import traceback
        traceback.print_exc()
        # Cleanup
        if orchestrator:
            orchestrator.shutdown()
        client.shutdown()
        db.close()
        return 1

    # ── Step 4: Analyze results ──
    print(flush=True)
    print("=" * 70, flush=True)
    ts = _fmt_elapsed(start_time)
    print(f"[4/5] [{ts}] Pipeline complete! Analyzing results...", flush=True)
    print("=" * 70, flush=True)

    total_elapsed = time.time() - start_time
    mins = int(total_elapsed // 60)
    secs = int(total_elapsed % 60)

    print(flush=True)
    print(f"  TIMING", flush=True)
    print(f"  ------", flush=True)
    print(f"  Total wall time:    {mins}m {secs}s ({total_elapsed:.1f}s)",
          flush=True)
    print(f"  Pipeline run time:  {run_elapsed:.1f}s", flush=True)
    print(f"  Plan build time:    {plan_elapsed:.1f}s", flush=True)
    if orchestrator:
        stats = orchestrator.get_stats()
        print(f"  Orchestrator stats: {json.dumps(stats, indent=4)}",
              flush=True)

    # Extract report content
    report_text = result.get("report_text", "")
    trc_analyses = result.get("trc_analyses", {})
    synthesis = report_text

    print(flush=True)
    print(f"  OUTPUT METRICS", flush=True)
    print(f"  --------------", flush=True)
    print(f"  TRC analyses:       {len(trc_analyses)}", flush=True)
    print(f"  Synthesis length:   {len(synthesis):,} chars", flush=True)
    print(f"  Full report length: {len(report_text):,} chars", flush=True)

    # Count successes/failures in TRC analyses
    success_count = 0
    error_count = 0
    total_analysis_chars = 0
    for trc, analysis in trc_analyses.items():
        if analysis and len(analysis) > 20:
            success_count += 1
            total_analysis_chars += len(analysis)
        else:
            error_count += 1

    print(f"  TRC successes:      {success_count}", flush=True)
    print(f"  TRC errors:         {error_count}", flush=True)
    print(f"  Avg analysis len:   "
          f"{total_analysis_chars // max(success_count, 1):,} chars",
          flush=True)

    # ── Progress timeline ──
    print(flush=True)
    print(f"  PROGRESS TIMELINE", flush=True)
    print(f"  -----------------", flush=True)
    for entry in progress_log:
        t = entry["time"]
        pct = entry["percent"]
        msg = entry["message"]
        pct_str = f"{pct:3d}%" if pct is not None else " -- "
        print(f"  [{int(t // 60):02d}:{int(t % 60):02d}] "
              f"{pct_str} | {msg}", flush=True)

    # ── Step 5: Show report quality ──
    print(flush=True)
    print("=" * 70, flush=True)
    print("[5/5] REPORT OUTPUT (synthesis)", flush=True)
    print("=" * 70, flush=True)
    print(flush=True)

    # Save full report to file (always, so it's not lost)
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"voc_report_{ts_stamp}.md"
    if synthesis:
        report_path.write_text(synthesis, encoding="utf-8")
        print(f"  Full report saved: {report_path}", flush=True)

    if synthesis:
        # Show first 3000 chars (enough to assess quality)
        display = synthesis[:3000]
        if len(synthesis) > 3000:
            display += (f"\n\n... [{len(synthesis) - 3000:,} more "
                        f"characters] ...")
        print(display, flush=True)
    else:
        print("  [NO SYNTHESIS GENERATED]", flush=True)
        # Show a sample TRC analysis instead
        for trc, analysis in list(trc_analyses.items())[:2]:
            print(f"\n  --- Sample TRC: {trc} ---", flush=True)
            print(analysis[:1000] if analysis else "[empty]", flush=True)

    # ── Cleanup ──
    print(flush=True)
    print("=" * 70, flush=True)
    print("  CLEANUP", flush=True)
    print("=" * 70, flush=True)
    if orchestrator:
        orchestrator.shutdown()
        print("  Orchestrator shutdown", flush=True)
    client.shutdown()
    print("  Bridge shutdown", flush=True)
    db.close()
    print("  DB closed", flush=True)

    # ── Final summary ──
    print(flush=True)
    print("=" * 70, flush=True)
    print("  FINAL SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"  Wall time:        {mins}m {secs}s", flush=True)
    print(f"  TRCs analyzed:    {success_count}/{len(trc_analyses)}",
          flush=True)
    print(f"  Synthesis:        "
          f"{'YES' if synthesis else 'NO'} ({len(synthesis):,} chars)",
          flush=True)
    print(f"  Report:           "
          f"{'YES' if report_text else 'NO'} ({len(report_text):,} chars)",
          flush=True)

    if success_count > 0 and synthesis:
        print(f"  STATUS:           [OK] FULL VOC PIPELINE PASSED",
              flush=True)
    else:
        print(f"  STATUS:           [PARTIAL] Some issues detected",
              flush=True)

    print("=" * 70, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
