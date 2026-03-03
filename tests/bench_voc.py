"""
Build 6.3 VOC Report Benchmark
Runs VOC Root Cause Analysis pipeline programmatically to verify:
  1. VOC pipeline runs end-to-end with Gemini
  2. {model_health_context} token populated from probe_history + scan_events
  3. Report saved to history
"""
import sys, os, time, sqlite3, json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path("data/local_warehouse.db")
DATE_START = "2025-01-01"
DATE_END = "2025-03-12"


def get_conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def progress_cb(msg, pct):
    print(f"  [{pct:3d}%] {msg}")


def main():
    import yaml
    from src.data.db_manager import DatabaseManager
    from src.gemini.gemini_client import GeminiClient
    from src.data.voc_builder import VOCBuilder

    # ── Load Gemini config ──
    config_path = Path("config/settings.yaml")
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    gemini_cfg = cfg.get("gemini", {})

    gc = GeminiClient(
        cli_path=gemini_cfg.get("cli_path", ""),
        model=gemini_cfg.get("model", "gemini-2.5-flash"),
        temperature=gemini_cfg.get("temperature", 0.2),
        pii_redaction=gemini_cfg.get("pii_redaction", True),
    )

    if not gc.is_available():
        print("ERROR: Gemini CLI not available")
        sys.exit(1)

    # ── Init DB ──
    db = DatabaseManager(DB_PATH)
    db.initialize()

    print("=" * 65)
    print("  BUILD 6.3 -- VOC REPORT BENCHMARK")
    print("=" * 65)
    print(f"  Date range:  {DATE_START} to {DATE_END}")
    print(f"  Model:       {gc.model}")
    print(f"  DB:          {DB_PATH}")

    # ── Pre-flight: check probe data ──
    conn = get_conn()
    try:
        probe_count = conn.execute(
            "SELECT COUNT(*) FROM probe_history"
        ).fetchone()[0]
        print(f"  Probes in DB: {probe_count}")
    except Exception as e:
        print(f"  Probes in DB: (table missing: {e})")
        probe_count = 0

    # Latest scan
    scan_row = conn.execute(
        "SELECT scan_id, status, total_tickets, completed_batches "
        "FROM nlp_scan_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if scan_row:
        print(f"  Latest scan: {scan_row['scan_id'][:12]} "
              f"[{scan_row['status']}] "
              f"{scan_row['total_tickets']} tickets")
    else:
        print("  Latest scan: (none)")
    conn.close()

    # ── Phase 0: Plan (dry run) ──
    print("\n" + "-" * 65)
    print("  PHASE 0: PLANNING")
    print("-" * 65)

    builder = VOCBuilder(db, gc, progress_callback=progress_cb)
    plan = builder.plan(DATE_START, DATE_END)

    print(f"  TRCs:           {len(plan['trc_plans'])}")
    print(f"  Total tickets:  {plan['total_tickets']}")
    print(f"  Sampled:        {plan['total_sampled']}")
    print(f"  Gemini calls:   {plan['total_gemini_calls']}")
    print(f"  Est cost:       ${plan['est_cost_usd']:.2f}")
    print(f"  Est time:       {plan['est_time_min']:.1f} min")
    print(f"  Has NLP data:   {plan['has_nlp_data']}")
    print(f"  Scan ID:        {plan.get('scan_id', '(none)')}")

    if not plan["trc_plans"]:
        print("\n  ERROR: No TRC plans -- no data in date range?")
        db.close()
        return

    # ── Verify model health context BEFORE run ──
    print("\n" + "-" * 65)
    print("  MODEL HEALTH CONTEXT (pre-check)")
    print("-" * 65)

    ctx = builder._build_model_health_context(plan.get("scan_id"))
    if ctx:
        for line in ctx.split("\n"):
            print(f"  {line}")
    else:
        print("  (No model health context -- probe_history may be empty)")

    # ── Phase 1+2: Full VOC run ──
    print("\n" + "-" * 65)
    print("  RUNNING FULL VOC PIPELINE")
    print("-" * 65)

    t0 = time.time()
    try:
        result = builder.run(DATE_START, DATE_END)
    except Exception as e:
        import traceback
        print(f"\n  ERROR: VOC pipeline failed")
        traceback.print_exc()
        db.close()
        return

    elapsed = time.time() - t0

    # ── Results ──
    print("\n" + "=" * 65)
    print("  VOC REPORT RESULTS")
    print("=" * 65)
    print(f"  Duration:      {elapsed:.0f}s ({elapsed / 60:.1f} min)")

    report_text = result.get("report_text", "")
    trc_analyses = result.get("trc_analyses", {})
    stats = result.get("stats", {})

    print(f"  Report length: {len(report_text)} chars")
    print(f"  TRC analyses:  {len(trc_analyses)}")
    print(f"  Gemini calls:  {stats.get('total_gemini_calls', '?')}")

    # Show first 500 chars of report
    print("\n  REPORT PREVIEW (first 500 chars):")
    print("  " + "-" * 60)
    for line in report_text[:500].split("\n"):
        print(f"  {line}")
    print("  " + "-" * 60)

    # ── Verify model health context was injected ──
    print("\n" + "-" * 65)
    print("  MODEL HEALTH CONTEXT VERIFICATION")
    print("-" * 65)

    # Check if the context was built
    ctx_after = builder._build_model_health_context(plan.get("scan_id"))
    if ctx_after:
        print("  Context built: YES")
        for line in ctx_after.split("\n"):
            print(f"    {line}")
    else:
        print("  Context built: NO (probe_history empty or missing)")

    # Check if report mentions model health
    health_keywords = ["model health", "probe", "latency", "stall", "bridge"]
    found = [kw for kw in health_keywords if kw.lower() in report_text.lower()]
    if found:
        print(f"  Health keywords in report: {', '.join(found)}")
    else:
        print("  Health keywords in report: (none found)")

    # ── Check report was persisted ──
    print("\n" + "-" * 65)
    print("  PERSISTENCE CHECK")
    print("-" * 65)

    conn = get_conn()
    latest_report = conn.execute(
        "SELECT id, page, report_type, ticket_count, duration_ms, "
        "LENGTH(full_results) as result_len, created_at "
        "FROM report_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if latest_report:
        print(f"  Report ID:     {latest_report['id']}")
        print(f"  Type:          {latest_report['report_type']}")
        print(f"  Ticket count:  {latest_report['ticket_count']}")
        print(f"  Duration:      {(latest_report['duration_ms'] or 0) / 1000:.1f}s")
        print(f"  Result size:   {latest_report['result_len']} chars")
        print(f"  Created:       {latest_report['created_at']}")
    else:
        print("  (No report found in history)")
    conn.close()

    # ── Per-TRC analysis summary ──
    print("\n" + "-" * 65)
    print("  PER-TRC ANALYSIS SUMMARY")
    print("-" * 65)
    for trc, analysis in trc_analyses.items():
        if isinstance(analysis, str):
            preview = analysis[:80].replace("\n", " ")
        else:
            preview = str(analysis)[:80]
        print(f"  {trc[:40]:<42s} {len(str(analysis)):>6d} chars  {preview[:40]}")

    print("\n" + "=" * 65)
    print("  BENCHMARK COMPLETE")
    print("=" * 65)

    db.close()


if __name__ == "__main__":
    main()
