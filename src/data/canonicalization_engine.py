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

import hashlib
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
    # ── Phase 6 telemetry flags (plan §6) ──
    # All default False so existing tests + gate re-scoring are unchanged.
    # Each enables a post-assignment stage inside run_canonicalization:
    "telemetry_drift_enabled": False,
    "telemetry_fission_enabled": False,
    "telemetry_fission_commit_splits": False,  # even when fission detected,
                                               # don't reassign tickets (Phase 6
                                               # is observational; splits
                                               # commit later via HITL queue).
    "telemetry_dormancy_enabled": False,
    # Stability bands for cluster_drift_events.stability_band
    "drift_band_stable_max": 0.05,      # cos distance
    "drift_band_normal_max": 0.15,
    "drift_band_drifting_max": 0.30,    # above this → 'unstable'; cluster tier → 'drifting'
    # Fission triggers
    "fission_variance_threshold": 0.15,      # mean cos distance from centroid
    "fission_silhouette_threshold": 0.30,    # below this triggers
    "fission_member_growth_multiplier": 2.0, # 2x growth since last scan
    "fission_subhdbscan_min_cluster_size": 3,
    "fission_silhouette_improvement_min": 0.10,
    "fission_llm_commit_score": 4.0,         # score >= this → commit (when commit_splits enabled)
    # Dormancy
    "dormancy_threshold_scans": 3,           # active → dormant after N consecutive empty scans
    "retirement_threshold_scans": 10,        # dormant → retired after N consecutive empty scans
    "resurrection_min_members": 3,           # dormant → active when this many NEW members land
    # ── Phase 7 enrichment rollup (S10.1) ──
    # Populates canonical_cluster_enrichment at end of run_canonicalization.
    # Default ON — this is core data for the analyst report. Flip off only
    # in tuning harnesses / tests where the rollup isn't exercised.
    "enrichment_rollup_enabled": True,
    # ── Member snapshots (S10.4) — default ON, cheap + unblocks Phase 6 snippets_then ──
    "member_snapshots_enabled": True,
    "member_snapshots_cap": 200,
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
    llm_client=None,
) -> CanonicalizationResult:
    """Run canonicalization across all TRCs (or one TRC if `trc` is given).

    When `force_recluster=True`, existing centroids are ignored (stage 1 skipped)
    — used by the tuning harness for clean grid-search runs. The persisted
    canonical_clusters rows remain; this just changes assignment for THIS run.

    `llm_client` is consumed only by the optional Phase 6 fission stage (per
    `params["telemetry_fission_enabled"]`); it's plumbed through here so
    callers don't have to re-invoke a separate function.
    """
    import time
    t0 = time.perf_counter()

    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    # ── Phase 6: snapshot prior state for drift + dormancy calcs ──
    prior_state: dict[str, dict] = {}
    if p.get("telemetry_drift_enabled") or p.get("telemetry_dormancy_enabled"):
        try:
            prior_state = _snapshot_prior_state(conn)
        except Exception as exc:
            logger.warning("Phase 6 snapshot failed, telemetry stages will be skipped: %s", exc)
            prior_state = {}

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

    # ── Phase 6 telemetry stages (all optional; each wrapped so an
    # observational bug can't fail the whole scan) ──
    telemetry_summary: dict[str, int] = {}
    if p.get("telemetry_drift_enabled") and prior_state:
        try:
            n_drift = _detect_and_log_drift(conn, scan_id, prior_state, p)
            telemetry_summary["drift_events"] = n_drift
        except Exception:
            logger.exception("Phase 6 drift detection failed (non-fatal)")

    if p.get("telemetry_fission_enabled"):
        try:
            n_fission = _detect_and_execute_fission(conn, scan_id, p, llm_client=llm_client)
            telemetry_summary["fission_events"] = n_fission
        except Exception:
            logger.exception("Phase 6 fission detection failed (non-fatal)")

    if p.get("telemetry_dormancy_enabled"):
        try:
            n_dormancy = _detect_dormancy_and_resurrection(conn, scan_id, prior_state, p)
            telemetry_summary["dormancy_events"] = n_dormancy
        except Exception:
            logger.exception("Phase 6 dormancy detection failed (non-fatal)")

    if telemetry_summary:
        logger.info("Phase 6 telemetry for scan %s: %s", scan_id, telemetry_summary)

    # ── Phase 7 enrichment rollup + S10.4 member snapshots ──
    # Both run AFTER assignments land but BEFORE commit so a failure rolls
    # the whole scan back cleanly. Each is independently guarded; either
    # being disabled or missing its migration is a no-op, not a failure.
    if p.get("enrichment_rollup_enabled", True):
        try:
            from src.data.cluster_enrichment_rollup import compute_enrichment_rollups
            rr = compute_enrichment_rollups(conn, scan_id, persist=True)
            if rr.rows_written:
                logger.info(
                    "Phase 7 rollup for scan %s: %d rows (%d ms)",
                    scan_id, rr.rows_written, rr.wall_time_ms,
                )
        except Exception:
            logger.exception("Phase 7 enrichment rollup failed (non-fatal)")

    if p.get("member_snapshots_enabled", True):
        try:
            n_snap = _write_member_snapshots(
                conn, scan_id=scan_id,
                cap=int(p.get("member_snapshots_cap", 200)),
            )
            if n_snap:
                logger.info("S10.4 member snapshots for scan %s: %d rows", scan_id, n_snap)
        except Exception:
            logger.exception("S10.4 member snapshot write failed (non-fatal)")

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


# ══════════════════════════════════════════════════════════════════════════
# Phase 6 — Drift + Fission + Dormancy telemetry
# ══════════════════════════════════════════════════════════════════════════
#
# All three stages are observational: they read the current DB state, compare
# it to a snapshot taken at the top of run_canonicalization, and write rows
# to the telemetry tables (cluster_drift_events, cluster_fission_events,
# dormancy_events). Stages never re-assign tickets or delete clusters on
# their own; the one exception is fission-commit, gated by
# `params["telemetry_fission_commit_splits"]` which defaults False.
#
# Plan reference: canonicalization-enrichment.md §6.
# Kernel reference: phase-6-9-session-kernel.md "Session N+1".


def _extract_centroid_blob(row_centroid: Optional[bytes]) -> Optional[np.ndarray]:
    """Safe blob → vector parse for telemetry snapshots. Returns None if
    the row has no centroid yet (new probationary cluster pre-upsert)."""
    if row_centroid is None:
        return None
    try:
        v = np.frombuffer(row_centroid, dtype=np.float32).copy()
        if v.size == 0:
            return None
        return _l2_normalize(v)
    except Exception:
        return None


def _cluster_variance(embeds: np.ndarray, centroid: np.ndarray) -> float:
    """Mean cosine distance of members from centroid. Both inputs assumed
    L2-normalized. Returns 0.0 for empty input (caller guards)."""
    if len(embeds) == 0:
        return 0.0
    return float(np.mean(1.0 - (embeds @ centroid)))


def _compute_silhouette_map(
    embeds_by_cluster: dict[str, np.ndarray],
) -> dict[str, float]:
    """Per-cluster mean silhouette across all labeled members.

    sklearn.silhouette_samples requires ≥ 2 unique labels with ≥ 1 sample
    each. For single-cluster inputs we return {} (silhouette undefined).
    Clusters with < 2 members get None silhouette.
    """
    clean = {cid: e for cid, e in embeds_by_cluster.items() if len(e) > 0}
    if len(clean) < 2:
        return {}
    X_parts = []
    labels = []
    order: list[str] = []
    for cid, e in clean.items():
        order.append(cid)
        X_parts.append(e)
        labels.extend([cid] * len(e))
    X = np.vstack(X_parts).astype(np.float32)
    try:
        from sklearn.metrics import silhouette_samples  # lazy import
    except Exception as exc:  # pragma: no cover
        logger.warning("silhouette unavailable (sklearn missing?): %s", exc)
        return {}
    try:
        samples = silhouette_samples(X, np.asarray(labels), metric="cosine")
    except Exception as exc:
        logger.warning("silhouette_samples failed: %s", exc)
        return {}
    out: dict[str, float] = {}
    start = 0
    for cid in order:
        n = len(clean[cid])
        sl = samples[start:start + n]
        out[cid] = float(np.mean(sl)) if n else float("nan")
        start += n
    return out


def _snapshot_prior_state(conn) -> dict[str, dict]:
    """Capture cluster state *before* run_canonicalization mutates anything.

    Returns {cluster_id: {centroid, members, member_count, tier,
                          last_seen_scan_id, scans_without_members,
                          variance, silhouette}}.

    Empty dict if no canonical_clusters exist yet (first scan).
    """
    rows = conn.execute(
        """
        SELECT cluster_id, centroid_blob, tier, last_seen_scan_id,
               COALESCE(scans_without_members, 0)
          FROM canonical_clusters
        """
    ).fetchall()
    if not rows:
        return {}

    # Members per cluster
    members_by_cluster: dict[str, set[str]] = {}
    for cid, tid in conn.execute(
        """
        SELECT canonical_issue_id, ticket_id FROM ticket_index
         WHERE canonical_issue_id IS NOT NULL
        """
    ).fetchall():
        members_by_cluster.setdefault(cid, set()).add(tid)

    # Load embeddings only for tickets that are assigned (for variance/sil)
    all_members = {t for s in members_by_cluster.values() for t in s}
    embed_by_tid: dict[str, np.ndarray] = {}
    if all_members:
        placeholders = ",".join("?" * len(all_members))
        for tid, blob in conn.execute(
            f"SELECT ticket_id, embedding_blob FROM ticket_embeddings "
            f"WHERE ticket_id IN ({placeholders})",
            tuple(all_members),
        ).fetchall():
            try:
                v = _l2_normalize(np.frombuffer(blob, dtype=np.float32).copy())
                embed_by_tid[tid] = v
            except Exception:
                pass

    snapshot: dict[str, dict] = {}
    embeds_by_cluster: dict[str, np.ndarray] = {}
    centroids_by_cluster: dict[str, np.ndarray] = {}
    for cid, centroid_blob, tier, last_seen, scans_without in rows:
        centroid = _extract_centroid_blob(centroid_blob)
        members = members_by_cluster.get(cid, set())
        snapshot[cid] = {
            "centroid": centroid,
            "members": members,
            "member_count": len(members),
            "tier": tier or "probationary",
            "last_seen_scan_id": last_seen,
            "scans_without_members": int(scans_without),
            "variance": None,
            "silhouette": None,
        }
        if centroid is not None and members:
            emats = [embed_by_tid[t] for t in members if t in embed_by_tid]
            if emats:
                mat = np.vstack(emats)
                embeds_by_cluster[cid] = mat
                centroids_by_cluster[cid] = centroid
                snapshot[cid]["variance"] = _cluster_variance(mat, centroid)

    # Silhouette requires ≥ 2 populated clusters
    sil = _compute_silhouette_map(embeds_by_cluster)
    for cid, s in sil.items():
        snapshot[cid]["silhouette"] = s

    return snapshot


def _classify_stability_band(drift_cos: float, params: dict) -> str:
    if drift_cos < float(params.get("drift_band_stable_max", 0.05)):
        return "stable"
    if drift_cos < float(params.get("drift_band_normal_max", 0.15)):
        return "normal"
    if drift_cos < float(params.get("drift_band_drifting_max", 0.30)):
        return "drifting"
    return "unstable"


def _load_current_cluster_state(conn) -> dict[str, dict]:
    """Post-scan snapshot. Same shape as _snapshot_prior_state but reflects
    the state AFTER run_canonicalization wrote assignments and centroids."""
    return _snapshot_prior_state(conn)


def _detect_and_log_drift(
    conn, scan_id: str, prior_state: dict[str, dict], params: dict,
) -> int:
    """Write cluster_drift_events for clusters that existed in prior_state
    and still exist post-scan. Returns rows inserted."""
    current = _load_current_cluster_state(conn)
    events = 0
    tier_updates: list[tuple[str, str]] = []
    for cid, prev in prior_state.items():
        cur = current.get(cid)
        if cur is None:
            continue
        cv = cur.get("centroid")
        pv = prev.get("centroid")
        if cv is None or pv is None:
            continue  # can't compute drift without both

        drift_cos = float(max(0.0, min(2.0, 1.0 - float(pv @ cv))))
        band = _classify_stability_band(drift_cos, params)

        prior_members: set[str] = prev.get("members", set())
        cur_members: set[str] = cur.get("members", set())
        retained = prior_members & cur_members
        added = cur_members - prior_members
        lost = prior_members - cur_members

        conn.execute(
            """
            INSERT INTO cluster_drift_events
              (event_id, cluster_id, scan_id, prev_scan_id,
               centroid_before_blob, centroid_after_blob, drift_cosine,
               members_added, members_lost, members_retained,
               member_count_before, member_count_after,
               variance_before, variance_after,
               silhouette_before, silhouette_after,
               stability_band)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                cid,
                scan_id,
                prev.get("last_seen_scan_id"),
                _vec_to_blob(pv), _vec_to_blob(cv), drift_cos,
                len(added), len(lost), len(retained),
                prev.get("member_count"), cur.get("member_count"),
                prev.get("variance"), cur.get("variance"),
                prev.get("silhouette"), cur.get("silhouette"),
                band,
            ),
        )
        events += 1

        if band == "unstable" and (cur.get("tier") or "") != "drifting":
            tier_updates.append((cid, "drifting"))

    for cid, new_tier in tier_updates:
        conn.execute(
            "UPDATE canonical_clusters SET tier = ? WHERE cluster_id = ?",
            (new_tier, cid),
        )

    return events


def _extract_json_object(raw: str) -> Optional[dict]:
    """Best-effort JSON extraction — strips markdown fences and trailing prose."""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        # strip ```json ... ``` fences
        lines = [ln for ln in txt.splitlines() if not ln.strip().startswith("```")]
        txt = "\n".join(lines).strip()
    # find first { and matching balanced } — simple bracket counter
    start = txt.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(txt)):
        ch = txt[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(txt[start:i + 1])
                except Exception:
                    return None
    return None


def _load_cluster_member_embeddings(
    conn, cluster_id: str,
) -> tuple[list[str], np.ndarray]:
    """Load (ticket_ids, embedding_matrix L2-normalized) for all tickets
    currently assigned to cluster_id. Empty arrays if none."""
    rows = conn.execute(
        """
        SELECT ti.ticket_id, te.embedding_blob
          FROM ticket_index ti
          JOIN ticket_embeddings te ON te.ticket_id = ti.ticket_id
         WHERE ti.canonical_issue_id = ?
        """,
        (cluster_id,),
    ).fetchall()
    if not rows:
        return [], np.empty((0, _EMBED_DIM_FALLBACK), dtype=np.float32)
    tids = [r[0] for r in rows]
    mats = []
    for _, blob in rows:
        try:
            mats.append(_l2_normalize(np.frombuffer(blob, dtype=np.float32).copy()))
        except Exception:
            pass
    if not mats:
        return tids, np.empty((0, _EMBED_DIM_FALLBACK), dtype=np.float32)
    return tids, np.vstack(mats).astype(np.float32)


def _fission_triggers(
    cur: dict, prev: Optional[dict], params: dict,
) -> list[str]:
    """Return list of trigger names; empty if cluster is stable."""
    triggers: list[str] = []
    var = cur.get("variance")
    sil = cur.get("silhouette")
    if var is not None and var > float(params.get("fission_variance_threshold", 0.15)):
        triggers.append("variance_threshold")
    if sil is not None and sil < float(params.get("fission_silhouette_threshold", 0.30)):
        triggers.append("silhouette_drop")
    if prev is not None:
        prev_n = prev.get("member_count") or 0
        cur_n = cur.get("member_count") or 0
        mult = float(params.get("fission_member_growth_multiplier", 2.0))
        if prev_n >= 3 and cur_n >= prev_n * mult:
            triggers.append("member_count_doubled")
    return triggers


def _sub_hdbscan(
    embeds: np.ndarray, params: dict,
) -> tuple[np.ndarray, np.ndarray]:
    """HDBSCAN with Phase 6 defaults for within-cluster fission detection."""
    sub_params = dict(params)
    sub_params["min_cluster_size"] = int(params.get("fission_subhdbscan_min_cluster_size", 3))
    sub_params["min_samples"] = 1
    sub_params["allow_single_cluster"] = False  # we WANT to see multiple children
    return _hdbscan_per_trc(embeds, sub_params)


def _detect_and_execute_fission(
    conn, scan_id: str, params: dict, *, llm_client=None,
) -> int:
    """Detect over-merged clusters, score with LLM (if client provided),
    and log cluster_fission_events. Returns rows inserted.

    Commit of actual splits is gated on `params["telemetry_fission_commit_splits"]`
    (default False — Phase 6 is observational)."""
    current = _load_current_cluster_state(conn)
    # Prior state isn't available to this helper when called from
    # run_canonicalization (it lives in the caller's scope); we reconstruct
    # a partial prior from the most recent drift event for each cluster so
    # member_count_doubled can still fire.
    prev_counts: dict[str, int] = {}
    try:
        for cid, prev_cnt in conn.execute(
            """
            SELECT cluster_id, member_count_before FROM cluster_drift_events
             WHERE event_id IN (
               SELECT event_id FROM cluster_drift_events e1
                WHERE e1.cluster_id = cluster_drift_events.cluster_id
                ORDER BY created_at DESC LIMIT 1
             )
            """
        ).fetchall():
            if prev_cnt is not None:
                prev_counts[cid] = int(prev_cnt)
    except sqlite3.OperationalError:
        pass  # telemetry table absent — first-scan case

    # Identify candidates
    candidates: list[dict] = []
    for cid, cur in current.items():
        if cur["member_count"] < max(6, int(params.get("fission_subhdbscan_min_cluster_size", 3)) * 2):
            continue  # too small to split meaningfully
        prev_like = {"member_count": prev_counts.get(cid)} if cid in prev_counts else None
        triggers = _fission_triggers(cur, prev_like, params)
        if not triggers:
            continue

        tids, mat = _load_cluster_member_embeddings(conn, cid)
        if len(mat) < 2 * int(params.get("fission_subhdbscan_min_cluster_size", 3)):
            continue

        sub_labels, _probs = _sub_hdbscan(mat, params)
        unique = sorted({int(l) for l in sub_labels if l >= 0})
        if len(unique) < 2:
            continue  # HDBSCAN didn't find ≥ 2 sub-groups

        # Silhouette after proposed split (only non-noise points)
        mask = sub_labels >= 0
        X = mat[mask]
        y = sub_labels[mask]
        sil_after: Optional[float] = None
        if len(set(y.tolist())) >= 2 and len(X) >= 2:
            try:
                from sklearn.metrics import silhouette_score
                sil_after = float(silhouette_score(X, y, metric="cosine"))
            except Exception:
                sil_after = None

        sil_before = cur.get("silhouette")
        sil_improve: Optional[float] = None
        if sil_before is not None and sil_after is not None:
            sil_improve = sil_after - sil_before
        min_improve = float(params.get("fission_silhouette_improvement_min", 0.10))
        if sil_improve is not None and sil_improve < min_improve:
            # Sub-structure too weak; record the rejection and move on
            conn.execute(
                """
                INSERT INTO cluster_fission_events
                  (event_id, parent_cluster_id, scan_id, triggered_by,
                   child_cluster_ids_json, member_count_parent,
                   tickets_reassigned_count, variance_before,
                   silhouette_before, silhouette_after, silhouette_improvement,
                   llm_gate_score, llm_gate_reasoning, committed)
                VALUES (?, ?, ?, ?, NULL, ?, 0, ?, ?, ?, ?, NULL, 'silhouette_below_min', 0)
                """,
                (
                    uuid.uuid4().hex, cid, scan_id, ",".join(triggers),
                    cur["member_count"], cur.get("variance"),
                    sil_before, sil_after, sil_improve,
                ),
            )
            continue

        # Compose child snippets from representative ticket subjects where possible
        child_snippets: dict[int, list[str]] = {}
        for lbl in unique:
            members_lbl = [tids[i] for i, l in enumerate(sub_labels) if l == lbl]
            snippets = []
            for tid in members_lbl[:3]:
                row = conn.execute(
                    "SELECT subject_sanitized FROM ticket_index WHERE ticket_id = ?",
                    (tid,),
                ).fetchone()
                if row and row[0]:
                    snippets.append(str(row[0])[:240])
            child_snippets[lbl] = snippets

        candidates.append({
            "parent_cluster_id": cid,
            "triggers": triggers,
            "member_count": cur["member_count"],
            "variance_before": cur.get("variance"),
            "silhouette_before": sil_before,
            "silhouette_after": sil_after,
            "silhouette_improvement": sil_improve,
            "sub_labels": sub_labels.tolist(),
            "sub_unique": unique,
            "child_snippets": child_snippets,
            "ticket_ids": tids,
        })

    if not candidates:
        return 0

    # LLM gate — one batched call over all candidates
    scores: dict[str, tuple[Optional[float], Optional[str], str]] = {}
    if llm_client is not None:
        try:
            prompt_path = (
                __file__.rsplit("src", 1)[0] + "config/prompts/fission_gate.txt"
            )
            tpl = open(prompt_path, "r", encoding="utf-8").read()
        except Exception as exc:
            logger.warning("fission_gate prompt unavailable: %s", exc)
            tpl = None
        if tpl:
            payload = []
            for cand in candidates:
                children = []
                cur = current[cand["parent_cluster_id"]]
                for lbl_idx, lbl in enumerate(cand["sub_unique"]):
                    members_lbl = [
                        cand["ticket_ids"][i]
                        for i, l in enumerate(cand["sub_labels"]) if l == lbl
                    ]
                    children.append({
                        "child_index": lbl_idx,
                        "member_count": len(members_lbl),
                        "representative_snippets": cand["child_snippets"].get(lbl, []),
                    })
                # Parent label lookup
                plabel_row = conn.execute(
                    "SELECT canonical_label FROM canonical_clusters WHERE cluster_id = ?",
                    (cand["parent_cluster_id"],),
                ).fetchone()
                payload.append({
                    "parent_cluster_id": cand["parent_cluster_id"],
                    "parent_label": (plabel_row[0] if plabel_row else None) or "",
                    "parent_member_count": cand["member_count"],
                    "parent_variance": cand["variance_before"],
                    "parent_silhouette": cand["silhouette_before"],
                    "proposed_children": children,
                    "silhouette_improvement": cand["silhouette_improvement"],
                })
            prompt = tpl.replace("{candidates_json}", json.dumps(payload, indent=2))
            try:
                raw = llm_client.generate(prompt, timeout=180)
                parsed = _extract_json_object(raw or "")
                if parsed and isinstance(parsed.get("decisions"), list):
                    for item in parsed["decisions"]:
                        pcid = str(item.get("parent_cluster_id") or "").strip()
                        if not pcid:
                            continue
                        try:
                            sc = float(item.get("score"))
                        except Exception:
                            sc = None
                        reasoning = str(item.get("reasoning") or "")[:500]
                        rec = str(item.get("recommendation") or "defer")
                        scores[pcid] = (sc, reasoning, rec)
            except Exception as exc:
                logger.warning("Fission LLM call failed (non-fatal): %s", exc)

    # Persist fission events (committed=0 unless commit_splits flag set & score passes)
    commit_splits = bool(params.get("telemetry_fission_commit_splits", False))
    commit_score = float(params.get("fission_llm_commit_score", 4.0))
    rows_inserted = 0
    for cand in candidates:
        pcid = cand["parent_cluster_id"]
        gate_score, reasoning, _rec = scores.get(pcid, (None, None, None))
        should_commit = bool(
            commit_splits and gate_score is not None and gate_score >= commit_score
        )

        # Default: not committed, no children, no reassignments
        child_cids_json: Optional[str] = None
        tickets_reassigned = 0
        committed_flag = 0

        if should_commit:
            # Attempt the commit atomically. Any failure rolls back via the
            # enclosing run_canonicalization transaction; we also fall back
            # to a non-committed event record so the audit trail reflects
            # the attempt.
            try:
                child_ids, reassigned = _commit_fission_split(
                    conn,
                    parent_cluster_id=pcid,
                    sub_labels=cand["sub_labels"],
                    ticket_ids=cand["ticket_ids"],
                    scan_id=scan_id,
                    params=params,
                )
                child_cids_json = json.dumps(child_ids)
                tickets_reassigned = reassigned
                committed_flag = 1
            except Exception as exc:
                logger.warning(
                    "Fission commit failed for parent %s (non-fatal, event recorded uncommitted): %s",
                    pcid, exc,
                )
                child_cids_json = None
                tickets_reassigned = 0
                committed_flag = 0

        conn.execute(
            """
            INSERT INTO cluster_fission_events
              (event_id, parent_cluster_id, scan_id, triggered_by,
               child_cluster_ids_json, member_count_parent,
               tickets_reassigned_count, variance_before,
               silhouette_before, silhouette_after, silhouette_improvement,
               llm_gate_score, llm_gate_reasoning, committed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, pcid, scan_id, ",".join(cand["triggers"]),
                child_cids_json, cand["member_count"],
                tickets_reassigned, cand["variance_before"],
                cand["silhouette_before"], cand["silhouette_after"],
                cand["silhouette_improvement"],
                gate_score, reasoning,
                committed_flag,
            ),
        )
        rows_inserted += 1

    return rows_inserted


# ──────────────────────────────────────────────────────────────────────────
# S10.3 — Fission split commit
# ──────────────────────────────────────────────────────────────────────────

def _commit_fission_split(
    conn, *,
    parent_cluster_id: str,
    sub_labels,
    ticket_ids: list[str],
    scan_id: str,
    params: dict,
) -> tuple[list[str], int]:
    """Execute a fission split: create N child clusters, reassign tickets,
    retire the parent by setting tier='split' + split_into_json.

    Runs within the caller's transaction. On any error, raises — caller
    records an uncommitted fission_event and lets run_canonicalization
    rollback if needed.

    Returns (child_cluster_ids, tickets_reassigned_count).
    """
    # Resolve parent TRC (child cluster IDs inherit it)
    prow = conn.execute(
        "SELECT trc FROM canonical_clusters WHERE cluster_id = ?",
        (parent_cluster_id,),
    ).fetchone()
    if prow is None:
        raise RuntimeError(f"parent cluster {parent_cluster_id} not found")
    parent_trc = prow[0] or "unknown"

    sub_labels_arr = np.asarray(sub_labels)
    unique_labels = sorted({int(l) for l in sub_labels_arr if l >= 0})
    if len(unique_labels) < 2:
        raise RuntimeError(
            f"fission commit requires ≥2 sub-clusters, got {len(unique_labels)}"
        )

    # Group tickets by sub_label (skip noise=-1)
    label_to_tids: dict[int, list[str]] = {}
    for tid, lab in zip(ticket_ids, sub_labels_arr):
        lab = int(lab)
        if lab < 0:
            continue
        label_to_tids.setdefault(lab, []).append(tid)

    # Pull embeddings for all members (once, reuse per sub-cluster)
    # Confidence-weighted centroid per sub-label
    from collections import OrderedDict
    emb_by_tid: dict[str, np.ndarray] = OrderedDict()
    conf_by_tid: dict[str, float] = {}
    for tid in ticket_ids:
        row = conn.execute(
            "SELECT embedding_blob, dim_size FROM ticket_embeddings WHERE ticket_id = ?",
            (tid,),
        ).fetchone()
        if row is None or row[0] is None:
            continue
        dim = int(row[1] or _EMBED_DIM_FALLBACK)
        try:
            emb_by_tid[tid] = _blob_to_vec(row[0], dim)
        except ValueError:
            continue
        # Pull most recent sub_cluster_confidence
        crow = conn.execute(
            """SELECT sub_cluster_confidence FROM nlp_ticket_classifications
                WHERE ticket_id = ?
             ORDER BY rowid DESC LIMIT 1""",
            (tid,),
        ).fetchone()
        conf_by_tid[tid] = (
            float(crow[0]) if crow and crow[0] is not None else _DEFAULT_CONFIDENCE
        )

    # Create child clusters + reassign members
    child_cids: list[str] = []
    total_reassigned = 0
    now = datetime.now(timezone.utc).isoformat()
    for lab in unique_labels:
        member_tids = [t for t in label_to_tids[lab] if t in emb_by_tid]
        if not member_tids:
            raise RuntimeError(f"sub-label {lab} has no embedable members")
        member_embeds = np.vstack([
            _l2_normalize(emb_by_tid[t]) for t in member_tids
        ]).astype(np.float32)
        member_confs = np.asarray(
            [conf_by_tid.get(t, _DEFAULT_CONFIDENCE) for t in member_tids],
            dtype=np.float32,
        )
        child_centroid = _compute_confidence_weighted_centroid(member_embeds, member_confs)
        medoid_idx = _compute_medoid(member_embeds, child_centroid)
        rep_ticket = member_tids[medoid_idx] if medoid_idx >= 0 else None

        child_cid = _new_cluster_id(parent_trc)
        _upsert_cluster(
            conn,
            cluster_id=child_cid,
            trc=parent_trc,
            centroid=child_centroid,
            rep_ticket=rep_ticket,
            member_count=len(member_tids),
            scan_id=scan_id,
            new=True,
        )
        # Tier starts 'active' since it inherits a proven parent cohort
        conn.execute(
            "UPDATE canonical_clusters SET tier = 'active' WHERE cluster_id = ?",
            (child_cid,),
        )
        child_cids.append(child_cid)

        # Reassign tickets to this child
        for tid in member_tids:
            cos_to_child = float(emb_by_tid[tid] @ child_centroid)
            _write_ticket_assignment(
                conn,
                ticket_id=tid,
                cluster_id=child_cid,
                confidence=cos_to_child,
                method="fission_split",
                membership_prob=None,
                now_iso=now,
            )
            total_reassigned += 1

    # Retire parent — tier='split', record descendants
    conn.execute(
        """UPDATE canonical_clusters
              SET tier = 'split',
                  split_into_json = ?,
                  merged_into = NULL,
                  last_seen_scan_id = ?,
                  last_seen_at = ?
            WHERE cluster_id = ?""",
        (json.dumps(child_cids), scan_id, now, parent_cluster_id),
    )

    return child_cids, total_reassigned


def _detect_dormancy_and_resurrection(
    conn, scan_id: str, prior_state: dict[str, dict], params: dict,
) -> int:
    """Update cluster tier based on scans_without_members + resurrection rule.
    Writes dormancy_events for every transition. Returns rows inserted."""
    dormancy_threshold = int(params.get("dormancy_threshold_scans", 3))
    retirement_threshold = int(params.get("retirement_threshold_scans", 10))
    resurrection_min = int(params.get("resurrection_min_members", 3))

    # Fresh current member sets per cluster
    current_members_by_cluster: dict[str, set[str]] = {}
    for cid, tid in conn.execute(
        """
        SELECT canonical_issue_id, ticket_id FROM ticket_index
         WHERE canonical_issue_id IS NOT NULL
        """
    ).fetchall():
        current_members_by_cluster.setdefault(cid, set()).add(tid)

    # Walk every known cluster (including those with 0 current members)
    rows = conn.execute(
        """
        SELECT cluster_id, tier, COALESCE(scans_without_members, 0), centroid_blob
          FROM canonical_clusters
        """
    ).fetchall()

    events = 0
    for cid, tier, scans_without, centroid_blob in rows:
        tier = tier or "probationary"
        scans_without = int(scans_without or 0)

        cur_members = current_members_by_cluster.get(cid, set())
        cur_count = len(cur_members)
        prior_members: set[str] = set()
        if cid in prior_state:
            prior_members = prior_state[cid].get("members", set()) or set()
        new_members = cur_members - prior_members
        new_count = len(new_members)

        # Counter update: +1 if empty this scan, else reset to 0
        new_counter = scans_without + 1 if cur_count == 0 else 0

        new_tier = tier
        transition: Optional[str] = None
        confirmation_gate: Optional[bool] = None
        resurrection_cos: Optional[float] = None
        resurrecting_ids: Optional[list[str]] = None

        # Resurrection (dormant/retired → active on ≥ N NEW members)
        if tier in ("dormant", "retired") and new_count >= resurrection_min:
            new_tier = "active"
            new_counter = 0
            transition = f"{tier}->active"
            confirmation_gate = True
            resurrecting_ids = sorted(list(new_members))[:50]
            # Avg cosine of new members to the cluster centroid
            cv = _extract_centroid_blob(centroid_blob)
            if cv is not None:
                embeds = []
                for tid in resurrecting_ids:
                    row = conn.execute(
                        "SELECT embedding_blob FROM ticket_embeddings WHERE ticket_id = ?",
                        (tid,),
                    ).fetchone()
                    if row and row[0]:
                        try:
                            embeds.append(_l2_normalize(np.frombuffer(row[0], dtype=np.float32).copy()))
                        except Exception:
                            pass
                if embeds:
                    mat = np.vstack(embeds)
                    resurrection_cos = float(np.mean(mat @ cv))
        # Active → dormant
        elif tier == "active" and new_counter >= dormancy_threshold:
            new_tier = "dormant"
            transition = "active->dormant"
        # Dormant → retired
        elif tier == "dormant" and new_counter >= retirement_threshold:
            new_tier = "retired"
            transition = "dormant->retired"

        # Persist counter + tier updates
        if new_tier != tier or new_counter != scans_without:
            conn.execute(
                "UPDATE canonical_clusters SET tier = ?, scans_without_members = ? WHERE cluster_id = ?",
                (new_tier, new_counter, cid),
            )

        if transition:
            conn.execute(
                """
                INSERT INTO dormancy_events
                  (event_id, cluster_id, scan_id, tier_transition,
                   scans_without_members, resurrecting_ticket_ids_json,
                   avg_resurrection_cosine, confirmation_gate_passed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex, cid, scan_id, transition,
                    scans_without,  # counter AT transition time (before reset)
                    json.dumps(resurrecting_ids) if resurrecting_ids else None,
                    resurrection_cos,
                    confirmation_gate,
                ),
            )
            events += 1

    return events


# ──────────────────────────────────────────────────────────────────────────
# S10.4 — historical member snapshots
# ──────────────────────────────────────────────────────────────────────────
#
# After each scan, snapshot a sample of member ticket_ids per cluster so the
# Phase 6 regression prompt can later hydrate `snippets_then` — the "what did
# this cluster look like last scan?" context. PHI-free (stores ticket_ids
# only; subjects/bodies hydrated at regression time via the normal path).
#
# Sampling is deterministic (seeded by scan_id+cluster_id) so snapshots are
# reproducible across re-runs.

def _write_member_snapshots(conn, *, scan_id: str, cap: int = 200) -> int:
    """Write one cluster_member_snapshots row per cluster with ≥1 member.

    Cap at `cap` ticket_ids per cluster; for larger clusters, sample
    deterministically seeded by (scan_id, cluster_id).

    No-op if migration 025 hasn't landed (caller catches and logs).
    Returns number of rows written.
    """
    # Fast schema probe
    try:
        conn.execute("SELECT 1 FROM cluster_member_snapshots LIMIT 0")
    except sqlite3.OperationalError:
        return 0  # migration 025 not applied

    # Pull members grouped by cluster
    cluster_members: dict[str, list[str]] = {}
    for cid, tid in conn.execute(
        """
        SELECT canonical_issue_id, ticket_id FROM ticket_index
         WHERE canonical_issue_id IS NOT NULL
         ORDER BY canonical_issue_id, ticket_id
        """
    ).fetchall():
        cluster_members.setdefault(cid, []).append(tid)

    if not cluster_members:
        return 0

    # Idempotent per-scan: replace prior rows for this scan_id
    conn.execute(
        "DELETE FROM cluster_member_snapshots WHERE scan_id = ?",
        (scan_id,),
    )

    rows = []
    for cid, members in cluster_members.items():
        member_count = len(members)
        if member_count > cap:
            # Deterministic sample: seed on (scan_id, cluster_id) — same scan
            # run twice yields the same sample, but different clusters in the
            # same scan get distinct samples.
            seed_bytes = hashlib.sha256(
                f"{scan_id}:{cid}".encode("utf-8")
            ).digest()
            seed = int.from_bytes(seed_bytes[:8], "big")
            rng = np.random.default_rng(seed)
            idx = rng.choice(member_count, size=cap, replace=False)
            sampled = [members[int(i)] for i in sorted(idx)]
        else:
            sampled = members
        rows.append((
            cid,
            scan_id,
            json.dumps(sampled),
            member_count,
        ))

    conn.executemany(
        """INSERT INTO cluster_member_snapshots
             (cluster_id, scan_id, member_ticket_ids_json, member_count)
           VALUES (?, ?, ?, ?)""",
        rows,
    )
    return len(rows)
