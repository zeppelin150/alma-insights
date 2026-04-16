"""Grid-search tuning harness for canonicalization hyperparameters.

Per plan §3.4: iterate over (min_cluster_size, min_samples,
cluster_selection_epsilon, knn_threshold, existing_match_threshold),
canonicalize with each config, score against the golden set + silhouette +
Davies-Bouldin, persist results to `canonicalization_tuning_runs`.

Usage:
    python scripts/tune_canonicalization.py \\
        --db data/local_warehouse.db \\
        --golden data/test_fixtures/alma_test_10000_1_golden_set.csv \\
        [--trc BILLING] [--no-llm] [--jobs 4]

    # Or export ALMA_GOLDEN_SET_CSV to override the default path.

Design notes:
- Each grid cell operates on a clone of the DB (SQLite `.backup`) so runs
  don't stomp each other's cluster rows. For speed we copy once and reuse
  an in-memory connection per cell.
- `force_recluster=True` bypasses stage-1 snapping so every cell gets a
  clean cluster layout.
- Internal-quality metrics (silhouette, Davies-Bouldin) come from sklearn
  over the primary-cluster assignment of each run.
- `--no-llm` skips the Gemini coherence scoring — plan §3.4 mentions this
  as optional and expensive.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable

# Allow `python scripts/tune_canonicalization.py` from repo root
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np

from src.data.canonicalization.golden_set import load_pairs, pair_stats
from src.data.canonicalization_engine import (
    DEFAULT_PARAMS,
    record_tuning_run,
    run_canonicalization,
    score_golden_set,
)

logger = logging.getLogger("tune_canonicalization")


# ──────────────────────────────────────────────────────────────────────
# Default grid — 2×2×2×2×2 = 32 cells; extendable via CLI
# ──────────────────────────────────────────────────────────────────────

DEFAULT_GRID: dict[str, list] = {
    "min_cluster_size": [5, 8],
    "min_samples": [2, 3],
    "cluster_selection_epsilon": [0.0, 0.1],
    "knn_threshold": [0.70, 0.80],
    "existing_match_threshold": [0.70, 0.80],
}


# ──────────────────────────────────────────────────────────────────────
# Internal-quality metrics
# ──────────────────────────────────────────────────────────────────────

def _internal_metrics(conn) -> tuple[float | None, float | None, int, float]:
    """Return (silhouette, davies_bouldin, n_clusters, noise_pct).

    Pulls primary assignments from ticket_index and embeddings from
    ticket_embeddings; requires sklearn. Silhouette is computed on a
    random sample (up to 2000 points) to stay fast on large datasets.
    """
    try:
        from sklearn.metrics import silhouette_score, davies_bouldin_score
    except ImportError:
        logger.warning("sklearn unavailable, skipping internal metrics")
        return None, None, 0, 0.0

    rows = conn.execute(
        """
        SELECT ti.ticket_id, ti.canonical_issue_id, te.embedding_blob, te.dim_size
          FROM ticket_index ti
          JOIN ticket_embeddings te ON te.ticket_id = ti.ticket_id
         WHERE ti.canonical_issue_id IS NOT NULL
        """
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0]
    noise_pct = 1.0 - (len(rows) / total) if total else 0.0

    if len(rows) < 10:
        return None, None, 0, noise_pct

    vecs = []
    labels = []
    for r in rows:
        blob, dim, cid = r[2], int(r[3] or 1024), r[1]
        v = np.frombuffer(blob, dtype=np.float32).copy()
        if v.size != dim:
            continue
        vecs.append(v)
        labels.append(cid)
    if len(set(labels)) < 2:
        return None, None, len(set(labels)), noise_pct

    X = np.vstack(vecs).astype(np.float32)
    y_str = np.asarray(labels)
    # Map string cluster_ids → integer codes for sklearn
    _, y = np.unique(y_str, return_inverse=True)
    n_clusters = len(set(y.tolist()))

    # Subsample for silhouette if very large
    if len(X) > 2000:
        rng = np.random.default_rng(0)
        idx = rng.choice(len(X), size=2000, replace=False)
        X_s, y_s = X[idx], y[idx]
    else:
        X_s, y_s = X, y

    try:
        sil = float(silhouette_score(X_s, y_s, metric="cosine"))
    except Exception as exc:
        logger.warning("silhouette failed: %s", exc)
        sil = None
    try:
        db = float(davies_bouldin_score(X, y))
    except Exception as exc:
        logger.warning("davies_bouldin failed: %s", exc)
        db = None
    return sil, db, n_clusters, noise_pct


# ──────────────────────────────────────────────────────────────────────
# Grid expansion
# ──────────────────────────────────────────────────────────────────────

def _expand_grid(grid: dict[str, list]) -> list[dict]:
    keys = list(grid.keys())
    out = []
    for values in itertools.product(*[grid[k] for k in keys]):
        cell = {k: v for k, v in zip(keys, values)}
        out.append(cell)
    return out


# ──────────────────────────────────────────────────────────────────────
# Per-cell runner
# ──────────────────────────────────────────────────────────────────────

def _reset_assignments(conn) -> None:
    """Clear prior canonicalization state so a new cell starts fresh.

    Order matters for FK integrity: child tables first, then canonical_clusters.
    """
    # Child tables (may not exist on older schemas)
    for stmt in (
        "DELETE FROM ticket_canonical_assignments",
        "DELETE FROM assignment_transitions",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.execute("DELETE FROM canonical_clusters")
    conn.execute(
        """
        UPDATE ticket_index
           SET canonical_issue_id = NULL,
               canonical_confidence = NULL,
               assignment_method = NULL,
               hdbscan_membership_prob = NULL,
               canonicalized_at = NULL
        """
    )
    conn.commit()


def run_cell(
    conn, *, params: dict, pairs: list[tuple[str, str, bool]],
    scan_id: str, trc: str | None,
) -> dict:
    """Execute one grid cell end-to-end and return aggregated scores."""
    _reset_assignments(conn)
    t0 = time.perf_counter()
    res = run_canonicalization(conn, scan_id=scan_id, trc=trc,
                                force_recluster=True, params=params)
    wall = int((time.perf_counter() - t0) * 1000)

    golden = score_golden_set(conn, pairs)
    sil, dbi, n_clusters, noise_pct = _internal_metrics(conn)

    return {
        "silhouette": sil,
        "davies_bouldin": dbi,
        "precision": golden["precision"],
        "recall": golden["recall"],
        "f1": golden["f1"],
        "noise_pct": noise_pct,
        "n_clusters": n_clusters if n_clusters else len(res.clusters),
        "gemini_coherence": None,
        "wall_time_ms": wall,
        "method_counts": res.method_counts,
    }


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Canonicalization tuning harness")
    p.add_argument("--db", required=True, help="Path to SQLite DB")
    p.add_argument("--golden", required=True, help="Path to golden-set CSV")
    p.add_argument("--trc", default=None, help="Scope to a single TRC")
    p.add_argument("--scan-id", default="tuning", help="scan_id tag for output rows")
    p.add_argument("--notes", default="", help="Free-text notes per cell")
    p.add_argument("--no-llm", action="store_true",
                   help="(Reserved) skip Gemini coherence scoring")
    p.add_argument("--limit", type=int, default=None,
                   help="Only run the first N cells (for quick smoke tests)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    pairs = load_pairs(args.golden)
    stats = pair_stats(pairs)
    logger.info("Golden set: %d pairs (%d same, %d diff)",
                stats["total"], stats["same"], stats["different"])

    grid_cells = _expand_grid(DEFAULT_GRID)
    if args.limit:
        grid_cells = grid_cells[: args.limit]
    logger.info("Grid has %d cells", len(grid_cells))

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    best = None
    for i, cell in enumerate(grid_cells, 1):
        params = {**DEFAULT_PARAMS, **cell}
        logger.info("Cell %d/%d: %s", i, len(grid_cells), cell)
        try:
            scores = run_cell(
                conn, params=params, pairs=pairs,
                scan_id=args.scan_id, trc=args.trc,
            )
        except Exception as exc:   # noqa: BLE001
            logger.exception("Cell %d failed: %s", i, exc)
            continue
        run_id = record_tuning_run(
            conn, params=params, scores=scores,
            scan_id=args.scan_id, trc=args.trc,
            wall_time_ms=scores["wall_time_ms"],
            notes=f"{args.notes} cell {i}/{len(grid_cells)}",
        )
        logger.info("  run_id=%s f1=%.3f p=%.3f r=%.3f sil=%s n=%s noise=%.1f%% wall=%dms",
                    run_id, scores["f1"], scores["precision"], scores["recall"],
                    f"{scores['silhouette']:.3f}" if scores["silhouette"] is not None else "?",
                    scores["n_clusters"], 100 * scores["noise_pct"],
                    scores["wall_time_ms"])
        if best is None or scores["f1"] > best[1]["f1"]:
            best = (cell, scores, run_id)

    if best:
        cell, scores, run_id = best
        logger.info("BEST: run_id=%s f1=%.3f cell=%s", run_id, scores["f1"], cell)
    else:
        logger.warning("No cells completed successfully")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
