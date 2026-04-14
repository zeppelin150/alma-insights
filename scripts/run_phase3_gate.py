"""One-shot Phase 3 gate: import 10K CSV -> embed -> canonicalize -> score.

Run against a fresh temp DB so production data is untouched. Prints a
gate report at the end. Exit code 0 if precision >= 0.80 AND recall >= 0.80.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.data.canonicalization.golden_set import load_pairs, pair_stats
from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    run_canonicalization,
    score_golden_set,
)
from src.data.db_manager import DatabaseManager
from src.data.csv_ingestion import ingest_csv
from src.data.embedding.builder import build_embeddings

log = logging.getLogger("phase3_gate")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=r"C:/Users/Chris/Downloads/alma_test_10000_1_enriched.csv")
    p.add_argument("--golden", default=r"C:/Users/Chris/Downloads/alma_test_10000_1_golden_set.csv")
    p.add_argument("--db", default=str(_ROOT / "data" / "phase3_gate_test.db"))
    p.add_argument("--precision-gate", type=float, default=0.80)
    p.add_argument("--recall-gate", type=float, default=0.80)
    p.add_argument("--keep-db", action="store_true",
                   help="Leave the gate DB on disk for inspection")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()

    csv_path = Path(args.csv)
    golden_path = Path(args.golden)
    db_path = Path(args.db)

    for p in (csv_path, golden_path):
        if not p.is_file():
            log.error("Missing input: %s", p)
            return 2

    if db_path.exists():
        log.info("Removing prior gate DB: %s", db_path)
        db_path.unlink()

    t_total = time.perf_counter()

    # ── 1. Initialize fresh DB ──────────────────────────────
    log.info("── Step 1: Initialize DB (migrations 001..018) ──")
    t = time.perf_counter()
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    log.info("   DB initialized in %.1fs", time.perf_counter() - t)

    # ── 2. Ingest CSV ───────────────────────────────────────
    log.info("── Step 2: Ingest enriched CSV ──")
    t = time.perf_counter()
    stats = ingest_csv(csv_path, mgr)
    log.info("   Ingestion stats: %s", stats)
    log.info("   Ingestion wall: %.1fs", time.perf_counter() - t)

    n_tickets = conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0]
    n_convos = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    log.info("   ticket_index rows: %d | conversations rows: %d", n_tickets, n_convos)
    if n_tickets == 0:
        log.error("No tickets ingested — aborting")
        return 3

    # ── 3. Build embeddings (Phase 2 raw-body composition) ──
    log.info("── Step 3: Build Qwen3 embeddings ──")
    t = time.perf_counter()
    written = build_embeddings(conn)
    log.info("   Embeddings written: %d in %.1fs", written, time.perf_counter() - t)
    n_embed = conn.execute("SELECT COUNT(*) FROM ticket_embeddings").fetchone()[0]
    coverage = n_embed / n_tickets if n_tickets else 0.0
    log.info("   Coverage: %.1f%% (%d/%d)", 100 * coverage, n_embed, n_tickets)

    # ── 4. Run canonicalization (Phase 3) ───────────────────
    log.info("── Step 4: Run canonicalization ──")
    t = time.perf_counter()
    res = run_canonicalization(conn, scan_id="phase3_gate")
    log.info("   Canonicalization summary: %s", res.summary())
    log.info("   Wall time: %.1fs", time.perf_counter() - t)

    # ── 5. Score against golden set ─────────────────────────
    log.info("── Step 5: Score against golden set ──")
    pairs = load_pairs(golden_path)
    gs_stats = pair_stats(pairs)
    log.info("   Golden set: %s", gs_stats)
    scores = score_golden_set(conn, pairs)
    log.info("   tp=%d fp=%d fn=%d tn=%d",
             scores["tp"], scores["fp"], scores["fn"], scores["tn"])
    log.info("   precision=%.3f recall=%.3f f1=%.3f",
             scores["precision"], scores["recall"], scores["f1"])

    # ── 6. Gate decision ────────────────────────────────────
    total_wall = time.perf_counter() - t_total
    log.info("── GATE REPORT ──")
    log.info("   Tickets: %d  Embeddings: %d  Clusters: %d",
             n_tickets, n_embed, len(res.clusters))
    log.info("   Methods: %s", res.method_counts)
    log.info("   Precision: %.3f (gate >= %.2f)", scores["precision"], args.precision_gate)
    log.info("   Recall:    %.3f (gate >= %.2f)", scores["recall"], args.recall_gate)
    log.info("   F1:        %.3f", scores["f1"])
    log.info("   Total wall: %.1fs", total_wall)

    passed = (scores["precision"] >= args.precision_gate and
              scores["recall"] >= args.recall_gate)
    log.info("   VERDICT: %s", "PASS" if passed else "FAIL")

    if not args.keep_db:
        try:
            conn.close()
        except Exception:
            pass

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
