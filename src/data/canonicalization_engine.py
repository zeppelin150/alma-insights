"""Canonicalization engine — Phase 3.

Clusters ticket embeddings per TRC into persistent canonical groups using
HDBSCAN + KNN-assign, with a three-stage flow:

    1. Snap incoming tickets to existing canonical cluster centroids when
       cosine > existing_match_threshold (default 0.75).
    2. Run HDBSCAN on the remaining embeddings per TRC.
    3. Assign HDBSCAN noise points to their nearest centroid (any TRC cluster)
       via KNN when cosine > knn_threshold; else mark `unclustered`.

Every assignment is audited via `assignment_method` (hdbscan_core,
hdbscan_border, knn_fallback, unclustered, snapped_existing) and the
corresponding confidence number on `ticket_index`.

Centroids are confidence-weighted: each ticket's contribution to its cluster
centroid is weighted by `nlp_ticket_classifications.sub_cluster_confidence`
(NULL ⇒ 0.5). Centroids are L2-normalized before persistence so cosine
similarity reduces to a dot product.

Plan reference: canonicalization-enrichment.md §3 (lines 241–411).

Public API:
    run_canonicalization(conn, scan_id, *, trc=None, force_recluster=False,
                         params=None) -> CanonicalizationResult
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────
# Tuning defaults — canonicalization-enrichment.md §3.2 / §3.3
# ──────────────────────────────────────────────────────────────────────────

DEFAULT_PARAMS: dict = {
    "min_cluster_size": 5,
    "min_samples": 2,
    "cluster_selection_epsilon": 0.0,
    "metric": "euclidean",  # HDBSCAN: we feed L2-normalized vectors,
                            # for which Euclidean distance is a monotonic
                            # function of cosine distance. Using Euclidean
                            # avoids hdbscan's slower pairwise-cosine path.
    "existing_match_threshold": 0.75,  # cosine
    "knn_threshold": 0.75,             # cosine
    "hdbscan_core_prob": 0.8,
    "hdbscan_border_prob": 0.4,
    "allow_single_cluster": True,
    # Phase 4 — multi-label tier thresholds (plan §4.2)
    "multilabel_primary_threshold": 0.75,
    "multilabel_secondary_threshold": 0.65,
    "multilabel_tertiary_threshold": 0.55,
    "multilabel_enabled": True,
    # Centroid-merge post-pass — addresses HDBSCAN over-splitting (Phase 3
    # gate showed 101 clusters for ~15 ground-truth concepts, recall 0.58).
    # After per-TRC clustering, greedily merge two clusters if their
    # centroid cosine exceeds this threshold. Set to 0.0 to disable.
    "centroid_merge_threshold": 0.85,
}

# Embedding dimension (Qwen3-Embedding-0.6B) — confirmed at load time
_EMBED_DIM_FALLBACK = 1024

# Sentinel for tickets without sub_cluster_confidence in classifications
_DEFAULT_CONFIDENCE = 0.5


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ClusterStats:
    cluster_id: str
    trc: str
    member_count: int
    label_source: str
    canonical_label: Optional[str]
    new: bool
    representative_ticket_id: Optional[str]

    def as_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "trc": self.trc,
            "member_count": self.member_count,
            "label_source": self.label_source,
            "canonical_label": self.canonical_label,
            "new": self.new,
            "representative_ticket_id": self.representative_ticket_id,
        }


@dataclass
class CanonicalizationResult:
    scan_id: str
    trc_scope: Optional[str]  # None = all TRCs
    tickets_processed: int
    tickets_assigned: int
    tickets_unclustered: int
    method_counts: dict                       # {method: count}
    clusters: list[ClusterStats] = field(default_factory=list)
    wall_time_ms: int = 0

    def summary(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "trc_scope": self.trc_scope,
            "tickets_processed": self.tickets_processed,
            "tickets_assigned": self.tickets_assigned,
            "tickets_unclustered": self.tickets_unclustered,
            "method_counts": self.method_counts,
            "n_clusters": len(self.clusters),
            "wall_time_ms": self.wall_time_ms,
        }


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a 1D vector (or row-wise a 2D matrix)."""
    if vec.ndim == 1:
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            return vec
        return vec / norm
    norms = np.linalg.norm(vec, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vec / norms


def _slugify_trc(trc: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (trc or "unknown").lower()).strip("-") or "unknown"


def _new_cluster_id(trc: str) -> str:
    return f"{_slugify_trc(trc)}-{uuid.uuid4().hex[:12]}"


def _blob_to_vec(blob: bytes, dim: int) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.size != dim:
        # Defensive: mismatched dim — truncate/pad is worse than loud failure
        raise ValueError(f"Embedding blob has {arr.size} floats, expected {dim}")
    return arr.copy()


def _vec_to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _compute_medoid(embeddings: np.ndarray, centroid: np.ndarray) -> int:
    """Return index of the member closest to the centroid (max cosine)."""
    if len(embeddings) == 0:
        return -1
    sims = embeddings @ centroid
    return int(np.argmax(sims))


def _compute_confidence_weighted_centroid(
    embeddings: np.ndarray, confidences: np.ndarray,
) -> np.ndarray:
    """Confidence-weighted mean, L2-normalized.

    NULL confidences should arrive as `_DEFAULT_CONFIDENCE` already. All-zero
    weights fall back to uniform mean to avoid division by zero.
    """
    if len(embeddings) == 0:
        raise ValueError("Empty cluster")
    w = np.asarray(confidences, dtype=np.float32)
    if w.sum() <= 0.0:
        w = np.ones_like(w)
    w = w / w.sum()
    mean = (embeddings * w[:, None]).sum(axis=0)
    return _l2_normalize(mean)


# ──────────────────────────────────────────────────────────────────────────
# DB loaders
# ──────────────────────────────────────────────────────────────────────────

def _load_embeddings_for_trc(
    conn, trc: Optional[str],
) -> tuple[list[str], list[str], np.ndarray, np.ndarray]:
    """Return (ticket_ids, trcs, embedding_matrix, confidences).

    Loads ticket_index rows that have a matching ticket_embeddings row.
    Optional `trc` filter. Confidence pulled from
    nlp_ticket_classifications.sub_cluster_confidence (most recent per ticket).
    """
    params: list = []
    where_trc = ""
    if trc:
        where_trc = " AND ti.trc_code = ?"
        params.append(trc)

    # Subquery pulls the latest sub_cluster_confidence per ticket by batch_id
    # rowid (classifications are insert-only, so higher rowid = newer).
    sql = f"""
        SELECT ti.ticket_id,
               ti.trc_code,
               te.embedding_blob,
               te.dim_size,
               COALESCE(
                   (SELECT c.sub_cluster_confidence
                      FROM nlp_ticket_classifications c
                     WHERE c.ticket_id = ti.ticket_id
                  ORDER BY c.rowid DESC LIMIT 1),
                   ?
               ) AS conf
          FROM ticket_index ti
          JOIN ticket_embeddings te ON te.ticket_id = ti.ticket_id
         WHERE 1=1 {where_trc}
    """
    rows = conn.execute(sql, [_DEFAULT_CONFIDENCE] + params).fetchall()

    if not rows:
        return [], [], np.empty((0, _EMBED_DIM_FALLBACK), dtype=np.float32), np.empty(0, dtype=np.float32)

    ticket_ids: list[str] = []
    trcs: list[str] = []
    vecs: list[np.ndarray] = []
    confs: list[float] = []
    dim = _EMBED_DIM_FALLBACK
    for r in rows:
        tid, trc_code, blob, dim_size, conf = r[0], r[1], r[2], r[3], r[4]
        dim = int(dim_size or dim)
        try:
            vec = _blob_to_vec(blob, dim)
        except ValueError as exc:
            logger.warning("Skipping ticket %s: %s", tid, exc)
            continue
        ticket_ids.append(tid)
        trcs.append(trc_code or "unknown")
        vecs.append(vec)
        confs.append(float(conf) if conf is not None else _DEFAULT_CONFIDENCE)

    matrix = np.vstack(vecs).astype(np.float32)
    matrix = _l2_normalize(matrix)
    return ticket_ids, trcs, matrix, np.asarray(confs, dtype=np.float32)


def _load_existing_centroids(
    conn, trc: Optional[str],
) -> tuple[list[str], list[str], np.ndarray]:
    """Return (cluster_ids, trcs, centroid_matrix) for active clusters."""
    params: list = []
    where = "WHERE COALESCE(tier, 'probationary') NOT IN ('retired','split')"
    if trc:
        where += " AND trc = ?"
        params.append(trc)
    rows = conn.execute(
        f"SELECT cluster_id, trc, centroid_blob FROM canonical_clusters {where}",
        params,
    ).fetchall()
    if not rows:
        return [], [], np.empty((0, _EMBED_DIM_FALLBACK), dtype=np.float32)

    cids: list[str] = []
    trcs: list[str] = []
    mats: list[np.ndarray] = []
    dim = _EMBED_DIM_FALLBACK
    for cid, t, blob in rows:
        if blob is None:
            continue
        arr = np.frombuffer(blob, dtype=np.float32)
        if mats and arr.size != dim:
            continue
        dim = arr.size
        cids.append(cid)
        trcs.append(t or "unknown")
        mats.append(arr)
    if not mats:
        return [], [], np.empty((0, dim), dtype=np.float32)
    matrix = _l2_normalize(np.vstack(mats).astype(np.float32))
    return cids, trcs, matrix


# ──────────────────────────────────────────────────────────────────────────
# Core algorithms
# ──────────────────────────────────────────────────────────────────────────

def _match_to_existing_centroids(
    embeddings: np.ndarray,
    existing_centroids: np.ndarray,
    existing_cluster_ids: list[str],
    threshold: float,
) -> dict[int, tuple[str, float]]:
    """Snap each embedding to the best existing centroid above `threshold`.

    Returns {row_index: (cluster_id, cosine)}; rows not in the dict are unmatched.
    """
    if len(embeddings) == 0 or len(existing_centroids) == 0:
        return {}
    sims = embeddings @ existing_centroids.T  # (N, K)
    best_k = np.argmax(sims, axis=1)
    best_sim = sims[np.arange(len(sims)), best_k]
    out: dict[int, tuple[str, float]] = {}
    for i, (k, s) in enumerate(zip(best_k, best_sim)):
        if s >= threshold:
            out[i] = (existing_cluster_ids[int(k)], float(s))
    return out


def _hdbscan_per_trc(
    embeddings: np.ndarray, params: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """Run HDBSCAN on an embedding matrix.

    Returns (labels, membership_probs); labels of -1 are noise.
    Empty/tiny inputs short-circuit to all-noise.
    """
    n = len(embeddings)
    if n == 0:
        return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)

    min_cluster_size = max(2, int(params.get("min_cluster_size", 5)))
    if n < min_cluster_size:
        return (np.full(n, -1, dtype=np.int32), np.zeros(n, dtype=np.float32))

    # Lazy import so unit tests that don't exercise HDBSCAN don't require install.
    import hdbscan  # type: ignore

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=int(params.get("min_samples", 2)),
        cluster_selection_epsilon=float(params.get("cluster_selection_epsilon", 0.0)),
        metric=params.get("metric", "euclidean"),
        # allow_single_cluster lets HDBSCAN form ONE cluster when the data is
        # cohesive — otherwise it would always require ≥2 clusters and dump
        # everything into noise (label -1) for single-issue TRCs.
        allow_single_cluster=bool(params.get("allow_single_cluster", True)),
        prediction_data=False,
        core_dist_n_jobs=1,
    )
    labels = clusterer.fit_predict(embeddings.astype(np.float64))
    probs = clusterer.probabilities_
    return (
        np.asarray(labels, dtype=np.int32),
        np.asarray(probs, dtype=np.float32),
    )


def _knn_assign_noise(
    noise_embeddings: np.ndarray,
    all_centroids: np.ndarray,
    all_cluster_ids: list[str],
    threshold: float,
) -> list[Optional[tuple[str, float]]]:
    """For each noise embedding, return (cluster_id, cosine) or None."""
    if len(noise_embeddings) == 0 or len(all_centroids) == 0:
        return [None] * len(noise_embeddings)
    sims = noise_embeddings @ all_centroids.T
    best_k = np.argmax(sims, axis=1)
    best_sim = sims[np.arange(len(sims)), best_k]
    out: list[Optional[tuple[str, float]]] = []
    for k, s in zip(best_k, best_sim):
        if s >= threshold:
            out.append((all_cluster_ids[int(k)], float(s)))
        else:
            out.append(None)
    return out


@dataclass
class MergeEvent:
    """One centroid-merge event for the audit log."""
    kept_label: int
    absorbed_label: int
    kept_count_before: int
    kept_count_after: int
    absorbed_count: int
    cosine_at_merge: float


def _merge_close_clusters(
    new_clusters: dict[int, list[int]],
    embeddings: np.ndarray,
    confidences: np.ndarray,
    threshold: float,
) -> tuple[dict[int, list[int]], dict[int, np.ndarray], list[MergeEvent]]:
    """Greedy agglomerative merge of HDBSCAN clusters with cosine > threshold.

    HDBSCAN often over-splits cohesive concepts into sub-clusters based on
    fine-grained wording. This pass collapses any pair of new clusters whose
    confidence-weighted centroids sit closer than `threshold` (cosine).

    Args:
        new_clusters: {hdbscan_label: [member_indices]} — labels are merged in
            place; the lower label number wins on collision.
        embeddings: L2-normalized embedding matrix indexed by member_indices.
        confidences: per-row confidence weights for centroid computation.
        threshold: cosine threshold above which two clusters merge. <=0 disables.

    Returns:
        (merged_clusters, label_to_centroid_cache, merge_events).
    """
    # Always compute initial per-label centroids
    centroids = {
        lab: _compute_confidence_weighted_centroid(
            embeddings[members], confidences[members],
        )
        for lab, members in new_clusters.items()
    }
    events: list[MergeEvent] = []

    if threshold <= 0.0 or len(new_clusters) < 2:
        return dict(new_clusters), centroids, events

    # Working copies
    clusters = {lab: list(members) for lab, members in new_clusters.items()}

    while len(centroids) >= 2:
        labels = sorted(centroids.keys())
        mat = np.vstack([centroids[lab] for lab in labels])
        sims = mat @ mat.T
        np.fill_diagonal(sims, -1.0)
        i, j = np.unravel_index(np.argmax(sims), sims.shape)
        max_sim = float(sims[i, j])
        if max_sim < threshold:
            break
        keep, drop = labels[i], labels[j]
        if keep == drop:
            break
        if drop < keep:
            keep, drop = drop, keep
        kept_before = len(clusters[keep])
        absorbed_count = len(clusters[drop])
        # Merge drop into keep
        clusters[keep] = clusters[keep] + clusters[drop]
        centroids[keep] = _compute_confidence_weighted_centroid(
            embeddings[clusters[keep]], confidences[clusters[keep]],
        )
        events.append(MergeEvent(
            kept_label=keep, absorbed_label=drop,
            kept_count_before=kept_before,
            kept_count_after=len(clusters[keep]),
            absorbed_count=absorbed_count,
            cosine_at_merge=max_sim,
        ))
        del clusters[drop]
        del centroids[drop]

    return clusters, centroids, events


def _classify_method(prob: float, params: dict) -> str:
    core_p = float(params.get("hdbscan_core_prob", 0.8))
    border_p = float(params.get("hdbscan_border_prob", 0.4))
    if prob >= core_p:
        return "hdbscan_core"
    if prob >= border_p:
        return "hdbscan_border"
    return "hdbscan_border"  # prob < border gets clipped (noise is -1 branch)


# ──────────────────────────────────────────────────────────────────────────
# DB writers
# ──────────────────────────────────────────────────────────────────────────

def _upsert_cluster(
    conn, *, cluster_id: str, trc: str, centroid: np.ndarray,
    rep_ticket: Optional[str], member_count: int, scan_id: str, new: bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    if new:
        conn.execute(
            """
            INSERT INTO canonical_clusters
              (cluster_id, trc, centroid_blob, representative_ticket_id,
               member_count, lifetime_tickets, lifetime_scans, tier,
               discovered_scan_id, first_seen_at, last_seen_scan_id, last_seen_at,
               label_source)
            VALUES (?, ?, ?, ?, ?, ?, 1, 'probationary', ?, ?, ?, ?, 'medoid')
            """,
            (
                cluster_id, trc, _vec_to_blob(centroid), rep_ticket,
                member_count, member_count, scan_id, now, scan_id, now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE canonical_clusters
               SET centroid_blob = ?,
                   representative_ticket_id = COALESCE(?, representative_ticket_id),
                   member_count = ?,
                   lifetime_tickets = lifetime_tickets + ?,
                   lifetime_scans = lifetime_scans + 1,
                   last_seen_scan_id = ?,
                   last_seen_at = ?
             WHERE cluster_id = ?
            """,
            (
                _vec_to_blob(centroid), rep_ticket, member_count, member_count,
                scan_id, now, cluster_id,
            ),
        )


def _write_merge_events(
    conn, *, scan_id: str, trc: str,
    events: list[MergeEvent],
    label_to_cluster_id: dict[int, str],
    threshold: float,
) -> None:
    """Persist merge audit rows. Best-effort: silently no-ops if migration
    019 hasn't been applied (kept_cluster_id FK references canonical_clusters)."""
    if not events:
        return
    try:
        for ev in events:
            kept_id = label_to_cluster_id.get(ev.kept_label)
            if kept_id is None:
                continue
            conn.execute(
                """INSERT INTO cluster_merge_events
                     (scan_id, trc, kept_cluster_id,
                      kept_member_count_before, kept_member_count_after,
                      absorbed_member_count, cosine_at_merge,
                      centroid_merge_threshold)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (scan_id, trc, kept_id,
                 ev.kept_count_before, ev.kept_count_after,
                 ev.absorbed_count, ev.cosine_at_merge, threshold),
            )
    except sqlite3.OperationalError:
        # Migration 019 not applied — older schema, audit silently skipped
        pass


def _compute_multilabel_ranks(
    embedding: np.ndarray,
    all_cluster_ids: list[str],
    all_centroids: np.ndarray,
    all_cluster_trcs: list[str],
    ticket_trc: str,
    params: dict,
) -> list[tuple[str, int, float, str]]:
    """Return top-3 ranked assignments as (cluster_id, rank, cosine, tier).

    Candidates scoped to the ticket's TRC first, then cross-TRC allowed for
    fill. Each rank has its own tier threshold; rank-1 above primary_threshold
    becomes 'primary', etc. Rows below their tier threshold are dropped.
    """
    if len(all_centroids) == 0:
        return []

    primary_t = float(params.get("multilabel_primary_threshold", 0.75))
    secondary_t = float(params.get("multilabel_secondary_threshold", 0.65))
    tertiary_t = float(params.get("multilabel_tertiary_threshold", 0.55))
    thresholds = (primary_t, secondary_t, tertiary_t)
    tier_names = ("primary", "secondary", "tertiary")

    sims = embedding @ all_centroids.T  # shape (K,)
    # Prefer same-TRC candidates; fall back to cross-TRC for later ranks
    same_trc_mask = np.asarray([t == ticket_trc for t in all_cluster_trcs], dtype=bool)
    # Sort all candidates by similarity desc
    order = np.argsort(-sims)

    # Partition: same-TRC first (in score order), then cross-TRC (in score order)
    same_order = [i for i in order if same_trc_mask[i]]
    cross_order = [i for i in order if not same_trc_mask[i]]
    final_order = same_order + cross_order

    out: list[tuple[str, int, float, str]] = []
    seen: set[str] = set()
    for rank in (1, 2, 3):
        t = thresholds[rank - 1]
        tier = tier_names[rank - 1]
        # Take the next best unseen candidate meeting this rank's threshold
        picked = None
        for k in final_order:
            cid = all_cluster_ids[k]
            if cid in seen:
                continue
            if float(sims[k]) < t:
                continue
            picked = (cid, rank, float(sims[k]), tier)
            break
        if picked is None:
            break
        out.append(picked)
        seen.add(picked[0])
    return out


def _write_multilabel_assignments(
    conn, *, ticket_id: str, ranked: list[tuple[str, int, float, str]],
    method: str, scan_id: str,
) -> None:
    """Replace-all semantics: delete prior assignments for this ticket, write new."""
    conn.execute(
        "DELETE FROM ticket_canonical_assignments WHERE ticket_id = ?",
        (ticket_id,),
    )
    for cid, rank, cos, tier in ranked:
        conn.execute(
            """INSERT INTO ticket_canonical_assignments
                 (ticket_id, cluster_id, rank, cosine_similarity,
                  assignment_tier, assignment_method, assigned_in_scan_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticket_id, cid, rank, cos, tier, method, scan_id),
        )


def _log_transition(
    conn, *, ticket_id: str, scan_id: str,
    old_primary: Optional[str], new_primary: Optional[str],
    old_cos: Optional[float], new_cos: Optional[float],
    reason: str,
) -> None:
    conn.execute(
        """INSERT INTO assignment_transitions
             (ticket_id, scan_id, old_primary_cluster_id, new_primary_cluster_id,
              old_cosine, new_cosine, transition_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ticket_id, scan_id, old_primary, new_primary, old_cos, new_cos, reason),
    )


def _write_ticket_assignment(
    conn, *, ticket_id: str, cluster_id: Optional[str],
    confidence: Optional[float], method: str, membership_prob: Optional[float],
    now_iso: str,
) -> None:
    conn.execute(
        """
        UPDATE ticket_index
           SET canonical_issue_id = ?,
               canonical_confidence = ?,
               assignment_method = ?,
               hdbscan_membership_prob = ?,
               canonicalized_at = ?
         WHERE ticket_id = ?
        """,
        (cluster_id, confidence, method, membership_prob, now_iso, ticket_id),
    )


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────

def run_canonicalization(
    conn,
    scan_id: str,
    *,
    trc: Optional[str] = None,
    force_recluster: bool = False,
    params: Optional[dict] = None,
) -> CanonicalizationResult:
    """Run canonicalization across all TRCs (or one TRC if `trc` is given).

    When `force_recluster=True`, existing centroids are ignored (stage 1 skipped)
    — used by the tuning harness for clean grid-search runs. The persisted
    canonical_clusters rows remain; this just changes assignment for THIS run.
    """
    import time
    t0 = time.perf_counter()

    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    ticket_ids, ticket_trcs, embeddings, confidences = _load_embeddings_for_trc(conn, trc)
    if not ticket_ids:
        return CanonicalizationResult(
            scan_id=scan_id, trc_scope=trc, tickets_processed=0,
            tickets_assigned=0, tickets_unclustered=0,
            method_counts={}, clusters=[], wall_time_ms=0,
        )

    # Group by TRC so each TRC is clustered independently
    trc_to_indices: dict[str, list[int]] = {}
    for i, t in enumerate(ticket_trcs):
        trc_to_indices.setdefault(t, []).append(i)

    method_counts: dict[str, int] = {}
    cluster_stats: list[ClusterStats] = []
    assignments: dict[int, tuple[Optional[str], Optional[float], str, Optional[float]]] = {}
    # index -> (cluster_id|None, confidence|None, method, membership_prob|None)

    now_iso = datetime.now(timezone.utc).isoformat()

    for trc_code, indices in trc_to_indices.items():
        trc_idx = np.asarray(indices, dtype=np.int64)
        sub_embeds = embeddings[trc_idx]
        sub_confs = confidences[trc_idx]

        # ── stage 1: snap to existing centroids ──
        if not force_recluster:
            ex_cids, ex_trcs, ex_mat = _load_existing_centroids(conn, trc_code)
        else:
            ex_cids, ex_trcs, ex_mat = [], [], np.empty((0, sub_embeds.shape[1]), dtype=np.float32)

        matched = _match_to_existing_centroids(
            sub_embeds, ex_mat, ex_cids, threshold=p["existing_match_threshold"],
        )
        remaining_mask = np.ones(len(sub_embeds), dtype=bool)
        for row_i, (cid, cos) in matched.items():
            orig_i = int(trc_idx[row_i])
            assignments[orig_i] = (cid, cos, "snapped_existing", None)
            remaining_mask[row_i] = False
            method_counts["snapped_existing"] = method_counts.get("snapped_existing", 0) + 1

        remaining_idx = np.where(remaining_mask)[0]
        remaining_embeds = sub_embeds[remaining_idx]
        remaining_confs = sub_confs[remaining_idx]

        # ── stage 2: HDBSCAN on remaining ──
        labels, probs = _hdbscan_per_trc(remaining_embeds, p)

        # Build per-new-cluster groupings
        new_clusters: dict[int, list[int]] = {}
        for local_i, lab in enumerate(labels):
            if lab >= 0:
                new_clusters.setdefault(int(lab), []).append(local_i)

        # Centroid-merge post-pass — collapse over-split sub-clusters
        merge_threshold = float(p.get("centroid_merge_threshold", 0.0))
        merged_clusters, label_centroids, merge_events = _merge_close_clusters(
            new_clusters, remaining_embeds, remaining_confs,
            threshold=merge_threshold,
        )
        # Build label->surviving-label map (for original HDBSCAN labels that
        # got absorbed into another). After merge, only labels in
        # `merged_clusters` remain as keys; for the others, we need to know
        # which kept-label they belong to so per-row assignments still work.
        label_to_kept: dict[int, int] = {}
        for kept_lab, members in merged_clusters.items():
            for orig_lab in new_clusters.keys():
                if orig_lab == kept_lab:
                    label_to_kept[orig_lab] = kept_lab
                    continue
                # If every member of orig_lab now appears in kept_lab's list,
                # orig_lab was absorbed.
                if all(m in members for m in new_clusters[orig_lab]) and orig_lab not in label_to_kept:
                    label_to_kept[orig_lab] = kept_lab
        # Sanity: ensure every original label maps to something
        for orig_lab in new_clusters.keys():
            label_to_kept.setdefault(orig_lab, orig_lab)

        # Compute + persist new cluster centroids (post-merge)
        new_cluster_id_for_label: dict[int, str] = {}
        centroid_cache: dict[str, np.ndarray] = {}
        for lab, members in merged_clusters.items():
            member_embeds = remaining_embeds[members]
            member_confs = remaining_confs[members]
            centroid = label_centroids[lab]
            medoid_local = _compute_medoid(member_embeds, centroid)
            rep_ticket = ticket_ids[int(trc_idx[remaining_idx[members[medoid_local]]])] if medoid_local >= 0 else None
            cid = _new_cluster_id(trc_code)
            new_cluster_id_for_label[lab] = cid
            centroid_cache[cid] = centroid
            _upsert_cluster(
                conn, cluster_id=cid, trc=trc_code, centroid=centroid,
                rep_ticket=rep_ticket, member_count=len(members),
                scan_id=scan_id, new=True,
            )
            cluster_stats.append(ClusterStats(
                cluster_id=cid, trc=trc_code, member_count=len(members),
                label_source="medoid", canonical_label=None, new=True,
                representative_ticket_id=rep_ticket,
            ))

        # Persist merge audit events (after kept clusters have IDs)
        _write_merge_events(
            conn, scan_id=scan_id, trc=trc_code, events=merge_events,
            label_to_cluster_id=new_cluster_id_for_label,
            threshold=merge_threshold,
        )

        # Record assignments for HDBSCAN-clustered (non-noise) members.
        # `labels` holds the ORIGINAL HDBSCAN label per row; map through
        # label_to_kept so absorbed-cluster members point at the surviving cid.
        for local_i, lab in enumerate(labels):
            if lab < 0:
                continue
            orig_i = int(trc_idx[remaining_idx[local_i]])
            kept_lab = label_to_kept.get(int(lab), int(lab))
            cid = new_cluster_id_for_label[kept_lab]
            prob = float(probs[local_i])
            cos = float(remaining_embeds[local_i] @ centroid_cache[cid])
            method = _classify_method(prob, p)
            assignments[orig_i] = (cid, cos, method, prob)
            method_counts[method] = method_counts.get(method, 0) + 1

        # ── stage 3: KNN-assign HDBSCAN noise ──
        noise_local = [i for i, lab in enumerate(labels) if lab < 0]
        if noise_local:
            # All centroids visible across TRCs — per plan §3 noise can snap
            # to any cluster regardless of TRC. We keep TRC scoping by
            # convention but allow cross-TRC fallback matches here.
            all_cids, _all_trcs, all_mat = _load_existing_centroids(conn, trc=None)
            noise_embeds = remaining_embeds[noise_local]
            knn_results = _knn_assign_noise(
                noise_embeds, all_mat, all_cids, threshold=p["knn_threshold"],
            )
            for local_i, res in zip(noise_local, knn_results):
                orig_i = int(trc_idx[remaining_idx[local_i]])
                prob = float(probs[local_i]) if len(probs) else 0.0
                if res is None:
                    assignments[orig_i] = (None, None, "unclustered", prob)
                    method_counts["unclustered"] = method_counts.get("unclustered", 0) + 1
                else:
                    cid, cos = res
                    assignments[orig_i] = (cid, cos, "knn_fallback", prob)
                    method_counts["knn_fallback"] = method_counts.get("knn_fallback", 0) + 1

    # ── Phase 4: load prior primary assignments for transition logging ──
    prior_primary: dict[str, tuple[Optional[str], Optional[float]]] = {}
    if p.get("multilabel_enabled", True):
        try:
            for r in conn.execute(
                """SELECT ticket_id, cluster_id, cosine_similarity
                     FROM ticket_canonical_assignments
                    WHERE assignment_tier = 'primary'"""
            ).fetchall():
                prior_primary[r[0]] = (r[1], r[2])
        except Exception:
            # Multi-label table might not be present (migration 018 not applied)
            prior_primary = {}

    # Load ALL centroids (across TRCs) once for multi-label scoring
    ml_cids, ml_trcs, ml_centroids = _load_existing_centroids(conn, trc=None)

    # ── write ticket_index assignments + multi-label rows ──
    assigned_count = 0
    unclustered_count = 0
    multilabel_enabled = p.get("multilabel_enabled", True) and len(ml_centroids) > 0

    for i in range(len(ticket_ids)):
        cid, conf, method, prob = assignments.get(
            i, (None, None, "unclustered", None),
        )
        _write_ticket_assignment(
            conn, ticket_id=ticket_ids[i], cluster_id=cid, confidence=conf,
            method=method, membership_prob=prob, now_iso=now_iso,
        )
        if cid is not None:
            assigned_count += 1
        else:
            unclustered_count += 1

        # Multi-label write — skip silently if migration 018 isn't applied
        if multilabel_enabled:
            try:
                ranked = _compute_multilabel_ranks(
                    embeddings[i], ml_cids, ml_centroids, ml_trcs,
                    ticket_trc=ticket_trcs[i], params=p,
                )
                # Invariant: if the engine picked a primary cluster (cid),
                # that cluster MUST be rank 1 in ticket_canonical_assignments
                # — otherwise the two tables disagree. Threshold gating
                # applies only to secondary/tertiary candidates.
                if cid is not None and (not ranked or ranked[0][0] != cid):
                    existing_cos = conf if conf is not None else 0.0
                    new_ranked: list[tuple[str, int, float, str]] = [
                        (cid, 1, float(existing_cos), "primary"),
                    ]
                    for old_cid, _old_rank, old_cos, _old_tier in ranked:
                        if old_cid == cid or len(new_ranked) >= 3:
                            continue
                        rank = len(new_ranked) + 1
                        tier = ("secondary", "tertiary")[rank - 2]
                        new_ranked.append((old_cid, rank, old_cos, tier))
                    ranked = new_ranked
                _write_multilabel_assignments(
                    conn, ticket_id=ticket_ids[i], ranked=ranked,
                    method=method, scan_id=scan_id,
                )
                # Transition log
                old = prior_primary.get(ticket_ids[i])
                new_primary = ranked[0] if ranked else None
                new_cid = new_primary[0] if new_primary else None
                new_cos = new_primary[2] if new_primary else None
                if old is None and new_cid is not None:
                    _log_transition(
                        conn, ticket_id=ticket_ids[i], scan_id=scan_id,
                        old_primary=None, new_primary=new_cid,
                        old_cos=None, new_cos=new_cos,
                        reason="new_ticket",
                    )
                elif old is not None and new_cid != old[0]:
                    _log_transition(
                        conn, ticket_id=ticket_ids[i], scan_id=scan_id,
                        old_primary=old[0], new_primary=new_cid,
                        old_cos=old[1], new_cos=new_cos,
                        reason="centroid_shift",
                    )
            except sqlite3.OperationalError:
                # Migration 018 not applied — silently skip multi-label.
                multilabel_enabled = False

    conn.commit()

    wall_ms = int((time.perf_counter() - t0) * 1000)
    return CanonicalizationResult(
        scan_id=scan_id, trc_scope=trc, tickets_processed=len(ticket_ids),
        tickets_assigned=assigned_count, tickets_unclustered=unclustered_count,
        method_counts=method_counts, clusters=cluster_stats, wall_time_ms=wall_ms,
    )


# ──────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────

def _load_centroid_vec(conn, cluster_id: str) -> np.ndarray:
    """Fetch a single centroid vector by cluster_id (L2-normalized)."""
    row = conn.execute(
        "SELECT centroid_blob FROM canonical_clusters WHERE cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if row is None or row[0] is None:
        raise KeyError(cluster_id)
    vec = np.frombuffer(row[0], dtype=np.float32).copy()
    return _l2_normalize(vec)


def score_golden_set(
    conn, golden_pairs: Iterable[tuple[str, str, bool]],
) -> dict:
    """Score canonicalization against a golden set of (ticket_a, ticket_b, same_issue?) triples.

    Precision = (same-cluster ∩ same-issue) / same-cluster
    Recall    = (same-cluster ∩ same-issue) / same-issue
    """
    # Build ticket_id -> cluster_id map
    rows = conn.execute(
        "SELECT ticket_id, canonical_issue_id FROM ticket_index WHERE canonical_issue_id IS NOT NULL"
    ).fetchall()
    clust_map = {r[0]: r[1] for r in rows}

    tp = fp = fn = tn = 0
    for a, b, same_issue in golden_pairs:
        ca = clust_map.get(a)
        cb = clust_map.get(b)
        if ca is None or cb is None:
            # Unclustered pair; count as negative prediction
            if same_issue:
                fn += 1
            else:
                tn += 1
            continue
        pred_same = (ca == cb)
        if pred_same and same_issue:
            tp += 1
        elif pred_same and not same_issue:
            fp += 1
        elif not pred_same and same_issue:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def score_golden_set_by_concept(
    conn, golden_pairs: Iterable[tuple[str, str, bool]],
) -> dict:
    """Score canonicalization against a golden set at the *concept* level.

    Phase 7 introduced `canonical_concepts`: a Level-2 grouping of clusters
    produced by one batched LLM call per scan. Two tickets are considered
    "same" at this level if they land in clusters linked to the same
    concept_id. This addresses the pairwise-recall ceiling (~0.65) we hit
    with cluster-level scoring in Phase 3.

    The traversal is:
      ticket_index.canonical_issue_id  (cluster)
        → canonical_clusters.concept_id  (concept)

    Tickets whose cluster has NULL concept_id fall back to cluster_id so
    they aren't silently dropped.
    """
    rows = conn.execute(
        """
        SELECT ti.ticket_id,
               COALESCE(cc.concept_id, 'cluster:' || ti.canonical_issue_id) AS effective_group
        FROM ticket_index ti
        LEFT JOIN canonical_clusters cc ON cc.cluster_id = ti.canonical_issue_id
        WHERE ti.canonical_issue_id IS NOT NULL
        """
    ).fetchall()
    group_map = {r[0]: r[1] for r in rows}

    tp = fp = fn = tn = 0
    for a, b, same_issue in golden_pairs:
        ga = group_map.get(a)
        gb = group_map.get(b)
        if ga is None or gb is None:
            if same_issue:
                fn += 1
            else:
                tn += 1
            continue
        pred_same = (ga == gb)
        if pred_same and same_issue:
            tp += 1
        elif pred_same and not same_issue:
            fp += 1
        elif not pred_same and same_issue:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "scoring_level": "concept",
    }


def record_tuning_run(
    conn, *, params: dict, scores: dict, scan_id: Optional[str] = None,
    trc: Optional[str] = None, wall_time_ms: int = 0, notes: str = "",
) -> str:
    """Persist a tuning-harness row; return the generated run_id."""
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO canonicalization_tuning_runs
          (run_id, params_json, silhouette, davies_bouldin,
           golden_precision, golden_recall, golden_f1,
           noise_pct, n_clusters, gemini_coherence, wall_time_ms,
           scan_id, trc, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, json.dumps(params),
            scores.get("silhouette"), scores.get("davies_bouldin"),
            scores.get("precision"), scores.get("recall"), scores.get("f1"),
            scores.get("noise_pct"), scores.get("n_clusters"),
            scores.get("gemini_coherence"), wall_time_ms,
            scan_id, trc, notes,
        ),
    )
    conn.commit()
    return run_id
