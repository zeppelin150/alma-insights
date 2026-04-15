"""Phase 7 validation — run concept linking against cached gate DB and
re-score the golden set at the concept level.

Fast iteration loop: assumes `data/phase3_gate_test.db` already has
1780 classifications + 19 canonical clusters from the Phase 3 gate run.

Usage:
    python scripts/phase3_concept_sweep.py
    python scripts/phase3_concept_sweep.py --golden path/to/golden.csv --skip-llm

By default, calls Gemini once (≈$0.05) to produce concept groupings; pass
--skip-llm to use the existing concept_id values (for repeat scoring).

Plan reference: phase-5-7-session-kernel.md (Phase 7 — the recall fix).
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from src.data.connection_factory import get_connection
from src.data.canonical_label_generator import generate_labels
from src.data.canonicalization.golden_set import load_pairs
from src.data.canonicalization_engine import (
    score_golden_set,
    score_golden_set_by_concept,
)
from src.data.concept_linker import run_concept_linking
from src.gemini.client_factory import build_client_for_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="data/phase3_gate_test.db",
        help="SQLite DB with cached classifications + clusters.",
    )
    parser.add_argument(
        "--golden",
        default="C:/Users/Chris/Downloads/alma_test_10000_1_golden_set.csv",
        help="Golden-set CSV path.",
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Skip Gemini labeling + concept linking; score existing state.",
    )
    parser.add_argument(
        "--skip-labels", action="store_true",
        help="Skip Phase 5 label regeneration (use existing canonical_label values).",
    )
    parser.add_argument(
        "--scan-id", default="phase7-sweep",
        help="Scan id stored in audit rows.",
    )
    parser.add_argument(
        "--log-level", default="INFO",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
    )
    log = logging.getLogger("phase7-sweep")

    db_path = Path(args.db)
    if not db_path.exists():
        log.error("DB not found: %s", db_path)
        return 2
    golden_path = Path(args.golden)
    if not golden_path.exists():
        log.error("Golden CSV not found: %s", golden_path)
        return 2

    conn = get_connection(str(db_path))

    client = None if args.skip_llm else build_client_for_task("report_generation")
    if not args.skip_llm and client is None:
        log.error("Could not build Gemini client (check settings); aborting")
        return 3

    # ── Phase 5: label generation (optional) ───────────────────────────
    if not args.skip_llm and not args.skip_labels:
        log.info("Phase 5 — regenerating labels (force=True)")
        t0 = time.perf_counter()
        r5 = generate_labels(conn, scan_id=args.scan_id, llm_client=client, force=True)
        log.info("Phase 5 done in %.1fs: %s", time.perf_counter() - t0, r5.source_counts)

    # ── Phase 7: concept linking ─────────────────────────────────────
    if not args.skip_llm:
        log.info("Phase 7 — running concept linking")
        t0 = time.perf_counter()
        r7 = run_concept_linking(conn, scan_id=args.scan_id, llm_client=client)
        log.info(
            "Phase 7 done in %.1fs: %d clusters → %d concepts (orphans=%d)",
            time.perf_counter() - t0, r7.cluster_count_input,
            r7.concept_count_output, r7.unlinked_cluster_count,
        )
    else:
        existing = conn.execute(
            "SELECT COUNT(*) FROM canonical_concepts"
        ).fetchone()[0]
        log.info("Skipping LLM; using %d existing concepts", existing)

    # ── Scoring: cluster vs concept ──────────────────────────────────
    pairs = load_pairs(golden_path)
    log.info("Loaded %d golden pairs", len(pairs))

    scores_cluster = score_golden_set(conn, pairs)
    scores_concept = score_golden_set_by_concept(conn, pairs)

    print("\n" + "=" * 70)
    print("Phase 3 (cluster-level)    vs   Phase 7 (concept-level)")
    print("=" * 70)
    print(f"{'':20s}  cluster     concept    delta")
    for k in ("precision", "recall", "f1"):
        c = scores_cluster[k]
        p = scores_concept[k]
        print(f"{k:20s}  {c:.4f}      {p:.4f}     {p - c:+.4f}")
    print(f"{'tp':20s}  {scores_cluster['tp']:<10d}  {scores_concept['tp']:<10d}")
    print(f"{'fp':20s}  {scores_cluster['fp']:<10d}  {scores_concept['fp']:<10d}")
    print(f"{'fn':20s}  {scores_cluster['fn']:<10d}  {scores_concept['fn']:<10d}")
    print(f"{'tn':20s}  {scores_cluster['tn']:<10d}  {scores_concept['tn']:<10d}")
    print("=" * 70)

    gate_pass = scores_concept["recall"] >= 0.80 and scores_concept["precision"] >= 0.80
    print(f"Gate: recall >= 0.80 AND precision >= 0.80  ->  {'PASS' if gate_pass else 'FAIL'}")

    # Show top concepts
    rows = conn.execute(
        """
        SELECT concept_id, concept_label, member_cluster_count, lifetime_tickets
        FROM canonical_concepts
        ORDER BY lifetime_tickets DESC
        LIMIT 20
        """
    ).fetchall()
    print(f"\nTop {len(rows)} concepts by lifetime_tickets:")
    for r in rows:
        print(f"  {r[3]:5d}  [{r[2]} clusters]  {r[1]}  <- {r[0]}")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
