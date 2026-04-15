"""Mini Phase 3 gate: 200-ticket subsample for fast dev validation.

Same flow as run_phase3_gate.py but pulls only the first 200 tickets from
the enriched CSV before ingesting. Validates the full code path
(ingest -> embed -> canonicalize -> score) without the 60+ min cost of
the full 1788-ticket run on CPU dev hardware.

Real Phase 3 gate runs against the full 1788 set on M1 production hardware
where MPS makes Qwen3 tractable.
"""

from __future__ import annotations

import csv
import logging
import sys
import tempfile
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
from src.data.csv_ingestion import ingest_csv
from src.data.db_manager import DatabaseManager
from src.data.embedding.builder import build_embeddings

log = logging.getLogger("phase3_mini")


def _make_subsample_csv(src: Path, dst: Path, n_tickets: int) -> int:
    """Copy the first N unique ticket_ids' worth of rows into dst."""
    seen: set[str] = set()
    rows_kept: list[list[str]] = []
    header: list[str] = []
    ticket_id_col = None
    with src.open(encoding="utf-8", newline="") as f:
        rdr = csv.reader(f)
        header = next(rdr)
        # Find the column whose header includes "Ticket ID"
        for i, h in enumerate(header):
            if "ticket id" in h.lower():
                ticket_id_col = i
                break
        if ticket_id_col is None:
            raise RuntimeError("Could not find Ticket ID column in CSV header")
        for row in rdr:
            tid = row[ticket_id_col]
            if tid not in seen:
                if len(seen) >= n_tickets:
                    break
                seen.add(tid)
            rows_kept.append(row)
    with dst.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows_kept)
    return len(seen)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    src_csv = Path(r"C:/Users/Chris/Downloads/alma_test_10000_1_enriched.csv")
    golden = Path(r"C:/Users/Chris/Downloads/alma_test_10000_1_golden_set.csv")
    db_path = _ROOT / "data" / "phase3_mini_test.db"
    if db_path.exists():
        db_path.unlink()

    n_target = 200

    log.info("Building %d-ticket subsample CSV...", n_target)
    sub_csv = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".csv").name)
    n_actual = _make_subsample_csv(src_csv, sub_csv, n_target)
    log.info("Subsample built: %d unique tickets at %s", n_actual, sub_csv)

    t_total = time.perf_counter()

    log.info("── Step 1: Initialize DB ──")
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()

    log.info("── Step 2: Ingest subsample ──")
    t = time.perf_counter()
    stats = ingest_csv(sub_csv, mgr)
    log.info("   Ingestion: %d tickets in %.1fs",
             stats["tickets_created"], time.perf_counter() - t)

    log.info("── Step 3: Embed (Qwen3, ~2-5 min for 200 tickets on dev CPU) ──")
    t = time.perf_counter()
    written = build_embeddings(conn)
    log.info("   Embeddings written: %d in %.1fs", written, time.perf_counter() - t)

    log.info("── Step 4: Canonicalize ──")
    t = time.perf_counter()
    res = run_canonicalization(conn, scan_id="mini")
    log.info("   Canonicalize: %s in %.1fs", res.summary(), time.perf_counter() - t)

    log.info("── Step 5: Score golden subset ──")
    pairs_all = load_pairs(golden)
    # Filter golden pairs to ticket IDs present in the subsample
    present = {r[0] for r in conn.execute("SELECT ticket_id FROM ticket_index").fetchall()}
    pairs_sub = [(a, b, s) for a, b, s in pairs_all if a in present and b in present]
    log.info("   Golden subset: %d pairs (of %d total) reference subsampled tickets",
             len(pairs_sub), len(pairs_all))
    if pairs_sub:
        scores = score_golden_set(conn, pairs_sub)
        log.info("   tp=%d fp=%d fn=%d tn=%d  P=%.3f R=%.3f F1=%.3f",
                 scores["tp"], scores["fp"], scores["fn"], scores["tn"],
                 scores["precision"], scores["recall"], scores["f1"])
    else:
        log.warning("   No golden pairs reference the subsampled tickets — "
                    "code-path validated but no metric available.")

    n_merge = conn.execute("SELECT COUNT(*) FROM cluster_merge_events").fetchone()[0]
    log.info("── Audit: %d cluster_merge_events recorded ──", n_merge)
    log.info("── Total wall: %.1fs ──", time.perf_counter() - t_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
