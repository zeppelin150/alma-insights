"""
Build 7.0 + 8.0 — Live Pipeline Integration Tests

TEST A: VOC Submission Route (Build 7.0)
  - Boot ReportBridgeClient
  - Build VOC plan against real DB (888 tickets, 127 TRCs)
  - Run a single-TRC analysis call through the bridge
  - Verify response is valid text

TEST B: Agentic CSV Reformatter with Bad Column Maps (Build 8.0)
  - Create CSVs with deliberately awful column names
  - Run CSVReformatter.analyze_csv() with live Gemini bridge
  - Verify Gemini can figure out the mapping
  - Ingest the "bad" CSV with the derived column_override
  - Verify DB contents

Usage:
  python tests/test_voc_pipeline_live.py
"""

import sys
import csv
import json
import time
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _separator(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _subsection(title):
    print()
    print(f"  --- {title} ---")


def test_a_voc_submission_route():
    """Test the full VOC submission route (Build 7.0)."""
    _separator("TEST A: VOC Submission Route")

    from src.agents.report_bridge_client import ReportBridgeClient
    from src.data.db_manager import DatabaseManager
    from src.data.voc_builder import VOCBuilder

    # ── Step 1: Boot bridge ──
    _subsection("Step 1: Boot ReportBridgeClient")
    client = ReportBridgeClient(
        model="gemini-2.5-flash",
        pii_redaction=True,
    )
    print(f"  is_available: {client.is_available()}")
    if not client.is_available():
        print("  [SKIP] Bridge not available - cannot run live VOC test")
        return False

    # ── Step 2: Connect to real DB ──
    _subsection("Step 2: Connect to DB")
    db_path = Path("data/local_warehouse.db")
    db = DatabaseManager(db_path)
    db.initialize()

    ticket_count = db.conn.execute(
        "SELECT COUNT(*) FROM conversations"
    ).fetchone()[0]
    trc_count = db.conn.execute(
        "SELECT COUNT(DISTINCT trc_code) FROM conversations "
        "WHERE trc_code != ''"
    ).fetchone()[0]
    print(f"  Tickets: {ticket_count}")
    print(f"  TRCs:    {trc_count}")

    # ── Step 3: Build VOC plan ──
    _subsection("Step 3: Build VOC Plan (no Gemini call)")
    builder = VOCBuilder(db=db, gemini_client=client)

    date_range = db.get_date_range()
    print(f"  Date range: {date_range}")

    plan = builder.plan(
        date_start=date_range[0],
        date_end=date_range[1],
    )
    print(f"  Plan keys:          {sorted(plan.keys())}")
    print(f"  TRC plans:          {len(plan.get('trc_plans', []))}")
    print(f"  Total Gemini calls: {plan.get('total_gemini_calls', '?')}")
    print(f"  Est cost:           ${plan.get('est_cost_usd', '?'):.2f}")
    print(f"  Est time:           {plan.get('est_time_min', '?'):.1f} min")

    # Show a few TRC plans
    for tp in plan.get("trc_plans", [])[:3]:
        print(f"    TRC: {tp['trc'][:50]}... "
              f"total={tp['total_tickets']}, sampled={tp['sampled']}")

    assert len(plan.get("trc_plans", [])) > 0, "Plan should have TRC plans"

    # ── Step 4: Single TRC analysis call ──
    _subsection("Step 4: Single TRC Analysis (live Gemini call)")

    # Pick the smallest TRC for a quick test
    smallest_trc_plan = min(
        plan["trc_plans"],
        key=lambda t: t["total_tickets"],
    )
    trc_code = smallest_trc_plan["trc"]
    print(f"  Target TRC:    {trc_code}")
    print(f"  Ticket count:  {smallest_trc_plan['total_tickets']}")

    # Build the analysis prompt for this TRC
    t0 = time.time()
    try:
        # Use the builder's internal method to build a prompt
        # Signature: _build_trc_analysis_prompt(trc, trc_plan, plan, date_start, date_end)
        analysis_prompt = builder._build_trc_analysis_prompt(
            trc_code, smallest_trc_plan, plan,
            date_range[0], date_range[1]
        )
        print(f"  Prompt length: {len(analysis_prompt)} chars")

        # Call through bridge
        response = client.generate(
            analysis_prompt,
            system_prompt="You are a customer service analyst.",
            timeout=120,
        )
        elapsed = time.time() - t0

        print(f"  Response time: {elapsed:.1f}s")
        print(f"  Response len:  {len(response)} chars")
        print(f"  First 200ch:   {response[:200]}...")

        assert len(response) > 50, "Response should be substantial"
        print("  [OK] Single TRC analysis succeeded")

    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [FAIL] Analysis call failed after {elapsed:.1f}s: {e}")
        db.close()
        client.shutdown()
        return False

    # ── Step 5: Cleanup ──
    _subsection("Step 5: Cleanup")
    db.close()
    client.shutdown()
    print("  Bridge shutdown complete")
    print("  [OK] TEST A PASSED")
    return True


def test_b_agentic_csv_reformatter():
    """Test the agentic CSV reformatter with bad column maps (Build 8.0)."""
    _separator("TEST B: Agentic CSV Reformatter (Bad Column Maps)")

    from src.agents.report_bridge_client import ReportBridgeClient
    from src.agents.csv_reformatter import CSVReformatter, MappingResult
    from src.data.db_manager import DatabaseManager
    from src.data.csv_ingestion import ingest_csv

    tmpdir = tempfile.mkdtemp()

    # ── Step 1: Boot bridge ──
    _subsection("Step 1: Boot bridge for Gemini analysis")
    client = ReportBridgeClient(
        model="gemini-2.5-flash",
        pii_redaction=False,  # We pre-mask PII in samples
    )
    print(f"  is_available: {client.is_available()}")
    if not client.is_available():
        print("  [SKIP] Bridge not available - cannot run live reformatter test")
        return False

    # ── Step 2: Create CSVs with deliberately bad column names ──
    _subsection("Step 2: Create CSVs with terrible column names")

    # CSV A: Spanish column names
    csv_a = Path(tmpdir) / "spanish_export.csv"
    with open(csv_a, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Numero de Caso", "Asunto", "Texto del Mensaje",
            "Categoria", "Estado", "Puntuacion", "Fecha de Creacion",
            "Rol del Autor"
        ])
        w.writerow(["SP001", "Problema de facturacion",
                     "Me cobraron dos veces por mi suscripcion",
                     "BILLING", "abierto", "2", "2025-10-15", "cliente"])
        w.writerow(["SP001", "Problema de facturacion",
                     "Estamos revisando su caso",
                     "BILLING", "abierto", "2", "2025-10-15", "agente"])
        w.writerow(["SP002", "Acceso a cuenta",
                     "No puedo iniciar sesion en mi cuenta",
                     "TECH", "resuelto", "4", "2025-10-16", "cliente"])
    print(f"  CSV A (Spanish): {csv_a.name}")

    # CSV B: Abbreviated/cryptic column names
    csv_b = Path(tmpdir) / "cryptic_export.csv"
    with open(csv_b, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "tid", "subj", "msg", "cat", "st", "score", "dt", "role"
        ])
        w.writerow(["CR001", "Payout dispute",
                     "My payout was $50 less than expected",
                     "PAYOUT", "open", "1", "2025-11-01", "end-user"])
        w.writerow(["CR001", "Payout dispute",
                     "We are reviewing the calculation",
                     "PAYOUT", "open", "1", "2025-11-01", "agent"])
        w.writerow(["CR002", "Cancel policy",
                     "I want to cancel my policy effective immediately",
                     "CANCEL", "pending", "3", "2025-11-02", "end-user"])
        w.writerow(["CR003", "Audit question",
                     "Why was my claim audited?",
                     "AUDIT", "solved", "4", "2025-11-03", "end-user"])
    print(f"  CSV B (Cryptic): {csv_b.name}")

    # CSV C: Completely wrong/misleading column names
    csv_c = Path(tmpdir) / "misleading_export.csv"
    with open(csv_c, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Reference #", "Summary Line", "Full Narrative",
            "Issue Bucket", "Workflow Stage", "Customer Happiness",
            "Logged On", "Who Said It"
        ])
        w.writerow(["ML001", "Enrollment denied",
                     "My enrollment application was rejected with no reason given",
                     "ENROLLMENT", "in_review", "1", "2025-12-01", "member"])
        w.writerow(["ML001", "Enrollment denied",
                     "We will escalate your case to the enrollment team",
                     "ENROLLMENT", "in_review", "1", "2025-12-01", "support_agent"])
        w.writerow(["ML002", "Prior auth delay",
                     "My prior authorization has been pending for 3 weeks",
                     "AUTH", "blocked", "2", "2025-12-02", "member"])
    print(f"  CSV C (Misleading): {csv_c.name}")

    reformatter = CSVReformatter(cache_dir=tmpdir)
    all_passed = True

    # ── Step 3: Analyze each CSV ──
    for label, csv_path in [("A-Spanish", csv_a), ("B-Cryptic", csv_b),
                            ("C-Misleading", csv_c)]:
        _subsection(f"Step 3{label[0]}: Analyze CSV {label}")

        t0 = time.time()
        try:
            result = reformatter.analyze_csv(str(csv_path),
                                            bridge_client=client)
            elapsed = time.time() - t0

            print(f"  Source:          {result.source}")
            print(f"  Is valid:        {result.is_valid}")
            print(f"  All high conf:   {result.all_high_confidence}")
            print(f"  Analysis time:   {elapsed:.1f}s")
            print(f"  Mapped fields:   {result.get_mapped_field_count()}")
            print(f"  Warnings:        {result.warnings}")

            # Show each mapping
            for m in result.mappings:
                conf_mark = {"high": "+", "medium": "~", "low": "-"}.get(
                    m["confidence"], "?"
                )
                print(f"    [{conf_mark}] {m['source_column']}"
                      f" -> {m['target_field']}"
                      f" ({m['confidence']})")

            if result.unmapped_source:
                print(f"  Unmapped src:    {result.unmapped_source}")
            if result.unmapped_target:
                print(f"  Unmapped tgt:    {result.unmapped_target[:5]}...")

            # Check if required fields mapped
            mapped_targets = {m["target_field"] for m in result.mappings}
            has_tid = "ticket_id" in mapped_targets
            has_body = "comment_body" in mapped_targets
            print(f"  ticket_id:       {'MAPPED' if has_tid else 'MISSING'}")
            print(f"  comment_body:    {'MAPPED' if has_body else 'MISSING'}")

            if not has_tid or not has_body:
                print(f"  [WARN] Required fields not mapped for {label}")
                all_passed = False
                continue

            # ── Step 4: Ingest with override ──
            print(f"  Ingesting with column_override...")
            override = result.get_column_override()
            db_path = Path(tmpdir) / f"test_{label}.db"
            db = DatabaseManager(db_path)
            db.initialize()

            stats = ingest_csv(str(csv_path), db, column_override=override)
            print(f"  Tickets created: {stats['tickets_created']}")
            print(f"  Comments stored: {stats['comments_stored']}")
            print(f"  Fields mapped:   {stats['mapped_fields']}")

            # Verify DB
            convos = db.conn.execute(
                "SELECT ticket_id, subject, trc_code "
                "FROM conversations ORDER BY ticket_id"
            ).fetchall()
            for c in convos:
                print(f"    {c[0]}: {c[1]!r} (trc={c[2]})")

            db.close()

            assert stats["tickets_created"] > 0, "Should create tickets"
            print(f"  [OK] CSV {label} - analysis + ingest succeeded")

        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [FAIL] CSV {label} failed after {elapsed:.1f}s: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False

    # ── Step 5: Cache verification ──
    _subsection("Step 5: Verify mapping cache")
    cache_path = Path(tmpdir) / "mapping_cache.json"
    if cache_path.exists():
        with open(cache_path) as f:
            cache = json.load(f)
        print(f"  Cache entries: {len(cache)}")
        for fp, entry in cache.items():
            print(f"    {fp[:16]}... headers={entry.get('source_headers', [])[:2]}...")
    else:
        print("  [WARN] No cache file found")

    # ── Cleanup ──
    _subsection("Step 6: Cleanup")
    client.shutdown()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("  Bridge shutdown + temp cleanup complete")

    if all_passed:
        print("  [OK] TEST B PASSED")
    else:
        print("  [PARTIAL] Some CSVs had mapping issues")
    return all_passed


def main():
    print()
    print("#" * 70)
    print("#  BUILD 7.0 + 8.0 LIVE PIPELINE TESTS")
    print("#" * 70)

    results = {}

    # Test A: VOC route
    try:
        results["A"] = test_a_voc_submission_route()
    except Exception as e:
        print(f"\n  [FAIL] TEST A crashed: {e}")
        import traceback
        traceback.print_exc()
        results["A"] = False

    # Test B: Agentic CSV reformatter
    try:
        results["B"] = test_b_agentic_csv_reformatter()
    except Exception as e:
        print(f"\n  [FAIL] TEST B crashed: {e}")
        import traceback
        traceback.print_exc()
        results["B"] = False

    # Summary
    print()
    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for test_id, passed in results.items():
        status = "[OK]" if passed else "[FAIL/SKIP]"
        print(f"  Test {test_id}: {status}")

    all_ok = all(results.values())
    print()
    if all_ok:
        print("  ALL LIVE PIPELINE TESTS PASSED")
    else:
        print("  SOME TESTS FAILED OR WERE SKIPPED")
    print("=" * 70)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
