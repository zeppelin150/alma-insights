"""Run gate with different embedding composition modes — validates whether
LLM-enriched composite text closes the recall gap.

Uses the existing phase3_gate_test.db (CSV already ingested, NLP labels
already in ticket_index), re-embeds with each mode, re-canonicalizes,
scores against golden set.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.data.canonicalization.golden_set import load_pairs
from src.data.canonicalization_engine import (
    DEFAULT_PARAMS, run_canonicalization, score_golden_set,
)
from src.data.embedding.builder import build_embeddings

import os

DB = _ROOT / "data" / "phase3_gate_test.db"
# Override with the ALMA_GOLDEN_SET_CSV env var; fallback expects the CSV at
# data/test_fixtures/ (gitignored) or an explicit path the operator provides.
GOLDEN = Path(os.environ.get(
    "ALMA_GOLDEN_SET_CSV",
    str(_ROOT / "data" / "test_fixtures" / "alma_test_10000_1_golden_set.csv"),
))


def _reset_canon(conn):
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


def _reset_embeddings(conn):
    conn.execute("DELETE FROM ticket_embeddings")
    conn.commit()


def _sample_composition(conn, mode: str) -> str:
    """Quick visual spot-check — pull one composition for display."""
    from src.data.embedding.builder import _load_ticket_texts
    rows = _load_ticket_texts(conn, composition_mode=mode)
    if not rows: return "<no rows>"
    tid, text, _ = rows[0]
    return f"ticket_id={tid}\n---\n{text[:800]}\n---"


def main():
    conn = sqlite3.connect(str(DB))
    pairs = load_pairs(GOLDEN)

    # Show one sample per mode so user sees what's being embedded
    for mode in ("raw_body", "composite_A", "composite_C"):
        print(f"\n========= SAMPLE composition for mode={mode} =========")
        print(_sample_composition(conn, mode))

    print("\n\n========= GATE RESULTS =========")
    print(f"{'mode':>15} {'n_clust':>8} {'unclus':>7} {'P':>6} {'R':>6} {'F1':>6} {'embed_s':>8}")
    print("-" * 64)

    for mode in ("raw_body", "composite_A", "composite_C"):
        t0 = time.perf_counter()
        _reset_canon(conn)
        _reset_embeddings(conn)
        n = build_embeddings(conn, composition_mode=mode)
        embed_dt = time.perf_counter() - t0
        # Use best-known params from the earlier sweep
        params = {
            **DEFAULT_PARAMS,
            "min_cluster_size": 15,
            "knn_threshold": 0.45,
            "existing_match_threshold": 0.45,
            "centroid_merge_threshold": 0.70,
        }
        res = run_canonicalization(conn, scan_id=f"comp_{mode}",
                                    force_recluster=True, params=params)
        scores = score_golden_set(conn, pairs)
        print(f"{mode:>15} {len(res.clusters):>8} {res.tickets_unclustered:>7} "
              f"{scores['precision']:>6.3f} {scores['recall']:>6.3f} "
              f"{scores['f1']:>6.3f} {embed_dt:>8.1f}")

    conn.close()


if __name__ == "__main__":
    main()
