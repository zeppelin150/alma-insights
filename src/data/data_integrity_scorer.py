"""Phase 8 data integrity scorer (S10.2).

Produces a single `IntegrityReport` per scan that combines 5 dimensions
into a 0-100 composite with status bands. All dimension metrics are
queried from existing Phase 5/6/7 tables (no scratch re-computation),
making this a pure read-then-aggregate stage.

The scorer:
  * writes per-metric rows to `data_integrity_scores`
  * writes triage items to `integrity_review_queue` for any failed metric
  * returns an `IntegrityReport` for in-memory consumers (analyst report,
    main window status indicator)

Dimensions + default weights (plan §8.1):
    coherence             — 0.25
    label_fidelity        — 0.20
    assignment_precision  — 0.20
    temporal_stability    — 0.20
    population_health     — 0.15

Status bands (composite score):
    ≥90  HEALTHY
    ≥80  ACCEPTABLE
    ≥70  DEGRADED
    <70  UNHEALTHY

Plan reference: canonicalization-enrichment.md §8, s10-backend-foundation-kernel.md §S10.2.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────

DIMENSION_WEIGHTS: dict[str, float] = {
    "coherence": 0.25,
    "label_fidelity": 0.20,
    "assignment_precision": 0.20,
    "temporal_stability": 0.20,
    "population_health": 0.15,
}

STATUS_BANDS: list[tuple[float, str]] = [
    (90.0, "HEALTHY"),
    (80.0, "ACCEPTABLE"),
    (70.0, "DEGRADED"),
    (0.0, "UNHEALTHY"),
]


# Higher-is-better metrics (value ≥ threshold → pass)
_HIGHER_BETTER = {
    "silhouette_mean", "llm_coherence_sample",
    "grounded_phrase_rate", "llm_2pass_agreement",
    "mean_confidence",
    "scan_over_scan_ari",
    "active_tier_ratio",
}
# Lower-is-better metrics (value ≤ threshold → pass)
_LOWER_BETTER = {
    "davies_bouldin", "medoid_fallback_rate",
    "noise_rate", "low_conf_pct",
    "unexplained_drift_pct", "label_churn_rate",
    "orphan_pct", "resurrection_count",
}


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class IntegrityMetric:
    name: str
    value: Optional[float]
    threshold: Optional[float]
    passed: bool
    weight: float = 1.0


@dataclass
class IntegrityDimension:
    name: str
    metrics: list[IntegrityMetric]
    raw_score: float
    weight: float
    status_band: str = "UNHEALTHY"


@dataclass
class IntegrityReport:
    scan_id: str
    composite_score: float
    status_band: str
    dimensions: list[IntegrityDimension]
    items_for_review: list[dict] = field(default_factory=list)
    wall_time_ms: int = 0


# ──────────────────────────────────────────────────────────────────────────
# Metric helpers (read-only over existing tables)
# ──────────────────────────────────────────────────────────────────────────

def _safe_scalar(conn, sql: str, params: tuple = ()) -> Optional[float]:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    val = row[0]
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _metric_passed(name: str, value: Optional[float], threshold: Optional[float]) -> bool:
    if value is None or threshold is None:
        return False
    if name in _HIGHER_BETTER:
        return value >= threshold
    if name in _LOWER_BETTER:
        return value <= threshold
    # Unknown metric → assume higher-is-better
    return value >= threshold


# ──────────────────────────────────────────────────────────────────────────
# Dimension scorers
# ──────────────────────────────────────────────────────────────────────────

def _score_coherence(conn, scan_id: str, thresholds: dict) -> IntegrityDimension:
    # silhouette_mean: from cluster_drift_events.silhouette_after for this scan
    silhouette = _safe_scalar(
        conn,
        """SELECT AVG(silhouette_after) FROM cluster_drift_events
            WHERE scan_id = ? AND silhouette_after IS NOT NULL""",
        (scan_id,),
    )
    # davies_bouldin proxy: mean variance_after (lower = tighter clusters)
    davies = _safe_scalar(
        conn,
        """SELECT AVG(variance_after) FROM cluster_drift_events
            WHERE scan_id = ? AND variance_after IS NOT NULL""",
        (scan_id,),
    )
    # llm_coherence_sample: mean verdict_score from regression reports this scan
    llm_sample = _safe_scalar(
        conn,
        """SELECT AVG(verdict_score) FROM cluster_regression_reports
            WHERE scan_id = ? AND verdict_score IS NOT NULL""",
        (scan_id,),
    )

    metrics = [
        IntegrityMetric(
            "silhouette_mean", silhouette,
            thresholds.get("silhouette_mean_min"),
            _metric_passed("silhouette_mean", silhouette,
                           thresholds.get("silhouette_mean_min")),
        ),
        IntegrityMetric(
            "davies_bouldin", davies,
            thresholds.get("davies_bouldin_max"),
            _metric_passed("davies_bouldin", davies,
                           thresholds.get("davies_bouldin_max")),
        ),
        IntegrityMetric(
            "llm_coherence_sample", llm_sample,
            thresholds.get("llm_coherence_sample_min"),
            _metric_passed("llm_coherence_sample", llm_sample,
                           thresholds.get("llm_coherence_sample_min")),
        ),
    ]
    return IntegrityDimension(
        name="coherence",
        metrics=metrics,
        raw_score=_dimension_score(metrics),
        weight=DIMENSION_WEIGHTS["coherence"],
    )


def _score_label_fidelity(conn, scan_id: str, thresholds: dict) -> IntegrityDimension:
    # grounded_phrase_rate: from canonical_label_runs if present (Phase 5)
    grounded = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN label_source IN ('grounded','llm_generated_grounded')
                           THEN 1.0 ELSE 0.0 END)
             FROM canonical_clusters
            WHERE tier IN ('active', 'probationary', 'drifting')""",
    )
    # llm_2pass_agreement: from label_runs table if we have it; else fallback
    two_pass = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN validator_agreed = 1 THEN 1.0 ELSE 0.0 END)
             FROM canonical_label_runs""",
    )
    # medoid_fallback_rate: % of clusters using medoid label vs grounded
    medoid = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN label_source = 'medoid' THEN 1.0 ELSE 0.0 END)
             FROM canonical_clusters
            WHERE tier IN ('active', 'probationary', 'drifting')""",
    )
    metrics = [
        IntegrityMetric(
            "grounded_phrase_rate", grounded,
            thresholds.get("grounded_phrase_rate_min"),
            _metric_passed("grounded_phrase_rate", grounded,
                           thresholds.get("grounded_phrase_rate_min")),
        ),
        IntegrityMetric(
            "llm_2pass_agreement", two_pass,
            thresholds.get("llm_2pass_agreement_min"),
            _metric_passed("llm_2pass_agreement", two_pass,
                           thresholds.get("llm_2pass_agreement_min")),
        ),
        IntegrityMetric(
            "medoid_fallback_rate", medoid,
            thresholds.get("medoid_fallback_rate_max"),
            _metric_passed("medoid_fallback_rate", medoid,
                           thresholds.get("medoid_fallback_rate_max")),
        ),
    ]
    return IntegrityDimension(
        name="label_fidelity",
        metrics=metrics,
        raw_score=_dimension_score(metrics),
        weight=DIMENSION_WEIGHTS["label_fidelity"],
    )


def _score_assignment_precision(conn, scan_id: str, thresholds: dict) -> IntegrityDimension:
    # noise_rate: fraction of tickets with assignment_method='unclustered'
    noise = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN assignment_method = 'unclustered'
                           THEN 1.0 ELSE 0.0 END)
             FROM ticket_index
            WHERE assignment_method IS NOT NULL""",
    )
    mean_conf = _safe_scalar(
        conn,
        """SELECT AVG(canonical_confidence) FROM ticket_index
            WHERE canonical_confidence IS NOT NULL""",
    )
    low_conf = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN canonical_confidence < 0.5 THEN 1.0 ELSE 0.0 END)
             FROM ticket_index
            WHERE canonical_confidence IS NOT NULL""",
    )
    metrics = [
        IntegrityMetric(
            "noise_rate", noise,
            thresholds.get("noise_rate_max"),
            _metric_passed("noise_rate", noise, thresholds.get("noise_rate_max")),
        ),
        IntegrityMetric(
            "mean_confidence", mean_conf,
            thresholds.get("mean_confidence_min"),
            _metric_passed("mean_confidence", mean_conf,
                           thresholds.get("mean_confidence_min")),
        ),
        IntegrityMetric(
            "low_conf_pct", low_conf,
            thresholds.get("low_conf_pct_max"),
            _metric_passed("low_conf_pct", low_conf,
                           thresholds.get("low_conf_pct_max")),
        ),
    ]
    return IntegrityDimension(
        name="assignment_precision",
        metrics=metrics,
        raw_score=_dimension_score(metrics),
        weight=DIMENSION_WEIGHTS["assignment_precision"],
    )


def _score_temporal_stability(conn, scan_id: str, thresholds: dict) -> IntegrityDimension:
    # scan_over_scan_ari: fraction of tickets whose cluster didn't churn
    # (proxy: count assignment_transitions with reason != 'new_ticket'
    # divided by total assigned tickets this scan)
    total_assigned = _safe_scalar(
        conn,
        """SELECT COUNT(*) FROM ticket_index WHERE canonical_issue_id IS NOT NULL""",
    )
    churned = _safe_scalar(
        conn,
        """SELECT COUNT(*) FROM assignment_transitions
            WHERE scan_id = ? AND transition_reason != 'new_ticket'""",
        (scan_id,),
    )
    if total_assigned and total_assigned > 0 and churned is not None:
        ari = 1.0 - (churned / total_assigned)
    else:
        ari = None
    # unexplained_drift_pct: fraction of drift_events with stability_band='unstable'
    total_drift = _safe_scalar(
        conn,
        """SELECT COUNT(*) FROM cluster_drift_events WHERE scan_id = ?""",
        (scan_id,),
    )
    unstable = _safe_scalar(
        conn,
        """SELECT COUNT(*) FROM cluster_drift_events
            WHERE scan_id = ? AND stability_band = 'unstable'""",
        (scan_id,),
    )
    unexplained = (
        (unstable / total_drift) if (total_drift and total_drift > 0 and unstable is not None) else None
    )
    # label_churn_rate: fraction of clusters that changed canonical_label this scan
    churn = _safe_scalar(
        conn,
        """SELECT COUNT(DISTINCT cluster_id) * 1.0 /
                  MAX(1, (SELECT COUNT(*) FROM canonical_clusters))
             FROM cluster_label_changes
            WHERE scan_id = ?""",
        (scan_id,),
    )
    metrics = [
        IntegrityMetric(
            "scan_over_scan_ari", ari,
            thresholds.get("scan_over_scan_ari_min"),
            _metric_passed("scan_over_scan_ari", ari,
                           thresholds.get("scan_over_scan_ari_min")),
        ),
        IntegrityMetric(
            "unexplained_drift_pct", unexplained,
            thresholds.get("unexplained_drift_pct_max"),
            _metric_passed("unexplained_drift_pct", unexplained,
                           thresholds.get("unexplained_drift_pct_max")),
        ),
        IntegrityMetric(
            "label_churn_rate", churn,
            thresholds.get("label_churn_rate_max"),
            _metric_passed("label_churn_rate", churn,
                           thresholds.get("label_churn_rate_max")),
        ),
    ]
    return IntegrityDimension(
        name="temporal_stability",
        metrics=metrics,
        raw_score=_dimension_score(metrics),
        weight=DIMENSION_WEIGHTS["temporal_stability"],
    )


def _score_population_health(conn, scan_id: str, thresholds: dict) -> IntegrityDimension:
    # active_tier_ratio: clusters in tier='active' vs all non-retired
    active = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN tier = 'active' THEN 1.0 ELSE 0.0 END)
             FROM canonical_clusters
            WHERE tier IS NOT NULL AND tier != 'retired' AND tier != 'split'""",
    )
    # orphan_pct: clusters with 0 members this scan
    orphan = _safe_scalar(
        conn,
        """SELECT AVG(CASE WHEN member_count = 0 OR member_count IS NULL
                           THEN 1.0 ELSE 0.0 END)
             FROM canonical_clusters
            WHERE tier IN ('active', 'probationary', 'drifting')""",
    )
    # resurrection_count: number of dormancy_events with transition='*->active' this scan
    resurrection = _safe_scalar(
        conn,
        """SELECT COUNT(*) FROM dormancy_events
            WHERE scan_id = ?
              AND tier_transition IN ('dormant->active','retired->active')""",
        (scan_id,),
    )
    metrics = [
        IntegrityMetric(
            "active_tier_ratio", active,
            thresholds.get("active_tier_ratio_min"),
            _metric_passed("active_tier_ratio", active,
                           thresholds.get("active_tier_ratio_min")),
        ),
        IntegrityMetric(
            "orphan_pct", orphan,
            thresholds.get("orphan_pct_max"),
            _metric_passed("orphan_pct", orphan,
                           thresholds.get("orphan_pct_max")),
        ),
        IntegrityMetric(
            "resurrection_count", resurrection,
            thresholds.get("resurrection_count_max"),
            _metric_passed("resurrection_count", resurrection,
                           thresholds.get("resurrection_count_max")),
        ),
    ]
    return IntegrityDimension(
        name="population_health",
        metrics=metrics,
        raw_score=_dimension_score(metrics),
        weight=DIMENSION_WEIGHTS["population_health"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Score aggregation
# ──────────────────────────────────────────────────────────────────────────

def _dimension_score(metrics: list[IntegrityMetric]) -> float:
    """Return a 0-100 score for a dimension.

    Only metrics with a value AND threshold contribute. If every metric is
    missing data, the dimension gets a neutral 70 (borderline DEGRADED) —
    otherwise a missing-metric scan would inflate to 100.
    """
    scored = [m for m in metrics if m.value is not None and m.threshold is not None]
    if not scored:
        return 70.0
    passed = sum(1 for m in scored if m.passed)
    # Give partial credit: each metric contributes 100/N if passed, 0 if failed
    return (passed / len(scored)) * 100.0


def _band_for(score: float) -> str:
    for threshold, name in STATUS_BANDS:
        if score >= threshold:
            return name
    return "UNHEALTHY"


def _composite(dimensions: list[IntegrityDimension]) -> float:
    total_w = sum(d.weight for d in dimensions)
    if total_w == 0:
        return 0.0
    weighted = sum(d.raw_score * d.weight for d in dimensions)
    return weighted / total_w


# ──────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────

def _persist_score_rows(conn, scan_id: str, dimensions: list[IntegrityDimension]) -> None:
    conn.execute("DELETE FROM data_integrity_scores WHERE scan_id = ?", (scan_id,))
    rows = []
    for dim in dimensions:
        for m in dim.metrics:
            rows.append((
                scan_id, dim.name, m.name, m.value, m.threshold,
                1 if m.passed else 0, m.weight,
            ))
    if rows:
        conn.executemany(
            """INSERT INTO data_integrity_scores
                 (scan_id, dimension, metric, value, threshold, passed, weight)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


def _enqueue_failures(
    conn, scan_id: str, dimensions: list[IntegrityDimension],
) -> list[dict]:
    """Walk every failed metric, write an integrity_review_queue item and
    return a list of payload dicts (for in-memory callers)."""
    items: list[dict] = []
    for dim in dimensions:
        for m in dim.metrics:
            if m.value is None or m.threshold is None:
                continue
            if m.passed:
                continue
            item_type = _item_type_for(m.name)
            payload = {
                "metric": m.name,
                "dimension": dim.name,
                "value": m.value,
                "threshold": m.threshold,
            }
            review_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO integrity_review_queue
                     (review_id, item_type, payload_json, priority, scan_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (review_id, item_type, json.dumps(payload),
                 _priority_for(dim.name), scan_id),
            )
            items.append({"review_id": review_id, **payload, "item_type": item_type})
    return items


def _item_type_for(metric: str) -> str:
    if metric in ("silhouette_mean", "davies_bouldin", "llm_coherence_sample"):
        return "low_coherence"
    if metric in ("label_churn_rate",):
        return "label_churn"
    if metric in ("resurrection_count",):
        return "dormancy_resurrection"
    return "threshold_suggestion"


def _priority_for(dimension: str) -> int:
    # lower number = higher priority
    return {
        "coherence": 2,
        "label_fidelity": 3,
        "assignment_precision": 2,
        "temporal_stability": 4,
        "population_health": 4,
    }.get(dimension, 5)


# ──────────────────────────────────────────────────────────────────────────
# Public entry
# ──────────────────────────────────────────────────────────────────────────

def _migration_applied(conn) -> bool:
    try:
        conn.execute("SELECT 1 FROM data_integrity_scores LIMIT 0")
        conn.execute("SELECT 1 FROM threshold_values_current LIMIT 0")
        conn.execute("SELECT 1 FROM integrity_review_queue LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _load_thresholds(conn) -> dict[str, float]:
    try:
        rows = conn.execute(
            "SELECT setting_key, value FROM threshold_values_current"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: float(r[1]) for r in rows}


def score_integrity(
    conn, scan_id: str, *, persist: bool = True,
) -> IntegrityReport:
    """Compute a 5-dimension integrity report for a scan. Writes per-metric
    rows + review queue items when `persist=True`.

    Gracefully degrades when migration 024 or thresholds are absent: returns
    a report with raw scores but doesn't touch the DB.
    """
    t0 = time.perf_counter()

    # Seed literature defaults if missing (same safe idempotent path the
    # calibrator uses) — so a brand-new DB gets meaningful thresholds on
    # first score call rather than all-None.
    if _migration_applied(conn):
        try:
            from src.data.threshold_calibrator import calibrate_thresholds
            calibrate_thresholds(conn, "literature")
        except Exception:
            logger.exception("literature seed from scorer failed (non-fatal)")

    thresholds = _load_thresholds(conn)

    dimensions = [
        _score_coherence(conn, scan_id, thresholds),
        _score_label_fidelity(conn, scan_id, thresholds),
        _score_assignment_precision(conn, scan_id, thresholds),
        _score_temporal_stability(conn, scan_id, thresholds),
        _score_population_health(conn, scan_id, thresholds),
    ]
    for d in dimensions:
        d.status_band = _band_for(d.raw_score)

    composite = _composite(dimensions)
    composite = round(composite, 2)
    band = _band_for(composite)

    items: list[dict] = []
    if persist and _migration_applied(conn):
        try:
            _persist_score_rows(conn, scan_id, dimensions)
            items = _enqueue_failures(conn, scan_id, dimensions)
        except Exception:
            logger.exception("score_integrity persistence failed (non-fatal)")

    wall_ms = int((time.perf_counter() - t0) * 1000)
    return IntegrityReport(
        scan_id=scan_id,
        composite_score=composite,
        status_band=band,
        dimensions=dimensions,
        items_for_review=items,
        wall_time_ms=wall_ms,
    )
