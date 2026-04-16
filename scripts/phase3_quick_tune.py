"""Quick tuning against the gate DB (embeddings already cached).

Iterates a small grid over min_cluster_size and cluster_selection_epsilon,
re-runs canonicalization each time (force_recluster), scores against
the golden set.
"""
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

import os

DB = _ROOT / "data" / "phase3_gate_test.db"
GOLDEN = Path(os.environ.get(
    "ALMA_GOLDEN_SET_CSV",
    str(_ROOT / "data" / "test_fixtures" / "alma_test_10000_1_golden_set.csv"),
))

def _reset(conn):
    for stmt in (
        "DELETE FROM ticket_canonical_assignments",
        "DELETE FROM assignment_transitions",
        "DELETE FROM canonical_clusters",
    ):
        try: conn.execute(stmt)
        except sqlite3.OperationalError: pass
    conn.execute("""
        UPDATE ticket_index
           SET canonical_issue_id=NULL, canonical_confidence=NULL,
               assignment_method=NULL, hdbscan_membership_prob=NULL,
               canonicalized_at=NULL
    """)
    conn.commit()


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    pairs = load_pairs(GOLDEN)

    # Round 2: aggressive noise absorption — lower KNN threshold
    grid = []
    for mcs in (10, 15, 20):
        for knn in (0.30, 0.40, 0.50, 0.55, 0.60):
            grid.append({
                "min_cluster_size": mcs,
                "cluster_selection_epsilon": 0.0,
                "knn_threshold": knn,
                "existing_match_threshold": knn,
            })

    print(f"{'mcs':>4} {'knn':>5} {'n_clust':>8} {'unclus':>7} {'P':>6} {'R':>6} {'F1':>6}")
    print("-" * 50)

    best = None
    for cell in grid:
        _reset(conn)
        params = {**DEFAULT_PARAMS, **cell}
        res = run_canonicalization(conn, scan_id="qt", force_recluster=True, params=params)
        scores = score_golden_set(conn, pairs)
        print(f"{cell['min_cluster_size']:>4} "
              f"{cell['knn_threshold']:>5.2f} "
              f"{len(res.clusters):>8} "
              f"{res.tickets_unclustered:>7} "
              f"{scores['precision']:>6.3f} "
              f"{scores['recall']:>6.3f} "
              f"{scores['f1']:>6.3f}")
        if best is None or scores["f1"] > best[1]["f1"]:
            best = (cell, scores, len(res.clusters), res.tickets_unclustered)

    print("-" * 50)
    if best:
        cell, scores, n_c, nu = best
        print(f"BEST: {cell}")
        print(f"   n_clusters={n_c} unclustered={nu} P={scores['precision']:.3f} "
              f"R={scores['recall']:.3f} F1={scores['f1']:.3f}")

if __name__ == "__main__":
    main()
