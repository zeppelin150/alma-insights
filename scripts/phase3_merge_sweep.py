"""Sweep centroid_merge_threshold against the cached gate-run embeddings."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.data.canonicalization.golden_set import load_pairs
from src.data.canonicalization_engine import (
    DEFAULT_PARAMS, run_canonicalization, score_golden_set,
)

DB = _ROOT / "data" / "phase3_gate_test.db"
GOLDEN = Path(r"C:/Users/Chris/Downloads/alma_test_10000_1_golden_set.csv")


def _reset(conn):
    for stmt in (
        "DELETE FROM ticket_canonical_assignments",
        "DELETE FROM assignment_transitions",
        "DELETE FROM cluster_merge_events",
        "DELETE FROM canonical_clusters",
    ):
        try: conn.execute(stmt)
        except sqlite3.OperationalError: pass
    conn.execute("""UPDATE ticket_index SET canonical_issue_id=NULL,
        canonical_confidence=NULL, assignment_method=NULL,
        hdbscan_membership_prob=NULL, canonicalized_at=NULL""")
    conn.commit()


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    pairs = load_pairs(GOLDEN)

    # Sweep merge threshold — lower means more merges
    print(f"{'merge_t':>8} {'mcs':>4} {'knn':>5} {'n_clust':>8} {'merges':>7} {'unclus':>7} {'P':>6} {'R':>6} {'F1':>6}")
    print("-" * 70)

    cells = []
    for mt in (0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50):
        for mcs in (5, 10, 15, 20):
            for knn in (0.45, 0.55, 0.65):
                cells.append({
                    "centroid_merge_threshold": mt,
                    "min_cluster_size": mcs,
                    "knn_threshold": knn,
                    "existing_match_threshold": knn,
                })

    best = None
    for cell in cells:
        _reset(conn)
        params = {**DEFAULT_PARAMS, **cell}
        res = run_canonicalization(conn, scan_id="ms", force_recluster=True, params=params)
        scores = score_golden_set(conn, pairs)
        n_merges = conn.execute("SELECT COUNT(*) FROM cluster_merge_events").fetchone()[0]
        print(f"{cell['centroid_merge_threshold']:>8.2f} "
              f"{cell['min_cluster_size']:>4} "
              f"{cell['knn_threshold']:>5.2f} "
              f"{len(res.clusters):>8} {n_merges:>7} "
              f"{res.tickets_unclustered:>7} "
              f"{scores['precision']:>6.3f} "
              f"{scores['recall']:>6.3f} "
              f"{scores['f1']:>6.3f}")
        if best is None or scores["f1"] > best[1]["f1"]:
            best = (cell, scores, len(res.clusters), n_merges)

    print("-" * 70)
    if best:
        cell, scores, n_c, n_m = best
        print(f"BEST: {cell}")
        print(f"   n_clusters={n_c}  merges={n_m}  P={scores['precision']:.3f} "
              f"R={scores['recall']:.3f} F1={scores['f1']:.3f}")


if __name__ == "__main__":
    main()
