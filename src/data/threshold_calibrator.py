"""Threshold calibration for Phase 8 data integrity scoring (S10.2).

Two layers of decisions:
  * seed literature defaults into `threshold_values_current` on first-run
  * step through progressively evidence-based calibration stages as more
    data accumulates

Stages (ordered by evidence strength):
  1. `literature`  — fixed defaults from the plan. Always available;
     used as the floor. Idempotent seed.
  2. `empirical`   — after scan 3+, compute p10/p50 of each metric's
     observed distribution and re-seat thresholds at the p10 (the "10th
     percentile is the new floor"). Audits in threshold_change_log.
  3. `golden_set`  — binary-search each threshold against the pair-level
     golden set to maximise F1. Only auto-commits if
     `golden_set_precision >= 0.80`. Otherwise the proposal lands in
     `integrity_review_queue` as a `threshold_suggestion` item.
  4. `hitl`        — apply approved `threshold_suggestion` items from
     `integrity_review_queue` (decision='apply').

Plan reference: canonicalization-enrichment.md §8.6 + s10-backend-foundation-kernel.md §S10.2.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Literature-backed defaults (seeded on first run)
# ──────────────────────────────────────────────────────────────────────────

LITERATURE_DEFAULTS: dict[str, float] = {
    # coherence
    "silhouette_mean_min": 0.30,
    "davies_bouldin_max": 1.50,       # lower is better; "max" for passing threshold
    "llm_coherence_sample_min": 3.5,  # 1-5 scale
    # label_fidelity
    "grounded_phrase_rate_min": 0.80,
    "llm_2pass_agreement_min": 0.75,
    "medoid_fallback_rate_max": 0.15,
    # assignment_precision
    "noise_rate_max": 0.20,
    "mean_confidence_min": 0.70,
    "low_conf_pct_max": 0.15,
    # temporal_stability
    "scan_over_scan_ari_min": 0.75,
    "unexplained_drift_pct_max": 0.10,
    "label_churn_rate_max": 0.05,
    # population_health
    "active_tier_ratio_min": 0.80,
    "orphan_pct_max": 0.05,
    "resurrection_count_max": 3.0,
}


@dataclass
class CalibrationResult:
    stage: str
    thresholds_updated: list[str] = field(default_factory=list)
    thresholds_unchanged: list[str] = field(default_factory=list)
    audit_log_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Low-level primitives
# ──────────────────────────────────────────────────────────────────────────

def _migration_applied(conn) -> bool:
    try:
        conn.execute("SELECT 1 FROM threshold_values_current LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def get_threshold(conn, setting_key: str, default: Optional[float] = None) -> Optional[float]:
    """Read a single live threshold value."""
    if not _migration_applied(conn):
        return default
    row = conn.execute(
        "SELECT value FROM threshold_values_current WHERE setting_key = ?",
        (setting_key,),
    ).fetchone()
    if row is None:
        return default
    return float(row[0])


def _log_change(
    conn, *, setting_key: str, old_value: Optional[float], new_value: float,
    changed_by: str, reason: str,
    scan_id_at_change: Optional[str] = None,
    golden_precision: Optional[float] = None,
) -> str:
    change_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO threshold_change_log
            (change_id, setting_key, old_value, new_value, changed_by, reason,
             scan_id_at_change, golden_set_precision_at_change)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (change_id, setting_key, old_value, new_value, changed_by, reason,
         scan_id_at_change, golden_precision),
    )
    return change_id


def _upsert_threshold(
    conn, *, setting_key: str, value: float, stage: str,
    source_scan_range: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO threshold_values_current
            (setting_key, value, calibration_stage, last_calibrated_at, source_scan_range)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(setting_key) DO UPDATE SET
            value = excluded.value,
            calibration_stage = excluded.calibration_stage,
            last_calibrated_at = CURRENT_TIMESTAMP,
            source_scan_range = excluded.source_scan_range
        """,
        (setting_key, value, stage, source_scan_range),
    )


# ──────────────────────────────────────────────────────────────────────────
# Stage implementations
# ──────────────────────────────────────────────────────────────────────────

def _calibrate_literature(conn, result: CalibrationResult) -> None:
    """Seed defaults — idempotent. Existing rows are left unchanged so
    re-running this stage after empirical/golden_set work doesn't stomp
    progress."""
    existing = {
        r[0] for r in conn.execute(
            "SELECT setting_key FROM threshold_values_current"
        ).fetchall()
    }
    for key, val in LITERATURE_DEFAULTS.items():
        if key in existing:
            result.thresholds_unchanged.append(key)
            continue
        _upsert_threshold(conn, setting_key=key, value=val, stage="literature")
        result.audit_log_ids.append(_log_change(
            conn, setting_key=key, old_value=None, new_value=val,
            changed_by="literature", reason="initial seed from plan defaults",
        ))
        result.thresholds_updated.append(key)


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    import numpy as np
    return float(np.percentile(values, pct))


def _calibrate_empirical(
    conn, result: CalibrationResult, *, source_scan_range: Optional[str] = None,
) -> None:
    """Reseat each threshold at the p10 of the observed metric distribution.

    Only runs if at least 3 distinct scans have written `data_integrity_scores`.
    Below that, we don't have enough samples to trust the percentiles and the
    stage is a no-op with a note.
    """
    n_scans = conn.execute(
        "SELECT COUNT(DISTINCT scan_id) FROM data_integrity_scores"
    ).fetchone()[0]
    if n_scans < 3:
        result.notes.append(
            f"empirical calibration skipped: need ≥3 scans, have {n_scans}"
        )
        return

    # Pull per-metric observed value distributions
    rows = conn.execute(
        """
        SELECT dimension, metric, value FROM data_integrity_scores
         WHERE value IS NOT NULL
        """
    ).fetchall()
    by_metric: dict[tuple[str, str], list[float]] = {}
    for dim, met, val in rows:
        by_metric.setdefault((dim, met), []).append(float(val))

    for (dim, metric), values in by_metric.items():
        # Map metric → setting_key; skip unknown metrics
        key = _metric_to_setting_key(metric)
        if key is None:
            continue
        # "Higher is better" metrics: reseat at p10 (new floor)
        # "Lower is better" metrics: reseat at p90 (new ceiling)
        if key.endswith("_min"):
            new_val = _percentile(values, 10.0)
        elif key.endswith("_max"):
            new_val = _percentile(values, 90.0)
        else:
            new_val = _percentile(values, 50.0)
        if new_val is None:
            continue
        old = get_threshold(conn, key)
        if old is not None and abs(old - new_val) < 1e-6:
            result.thresholds_unchanged.append(key)
            continue
        _upsert_threshold(
            conn, setting_key=key, value=new_val, stage="empirical",
            source_scan_range=source_scan_range,
        )
        result.audit_log_ids.append(_log_change(
            conn, setting_key=key, old_value=old, new_value=new_val,
            changed_by="empirical", reason=f"p10/p90 over {n_scans} scans",
        ))
        result.thresholds_updated.append(key)


def _metric_to_setting_key(metric: str) -> Optional[str]:
    """Canonical mapping from scorer metric names to setting keys."""
    # Higher-is-better → _min suffix
    direction_map = {
        "silhouette_mean": "silhouette_mean_min",
        "llm_coherence_sample": "llm_coherence_sample_min",
        "grounded_phrase_rate": "grounded_phrase_rate_min",
        "llm_2pass_agreement": "llm_2pass_agreement_min",
        "mean_confidence": "mean_confidence_min",
        "scan_over_scan_ari": "scan_over_scan_ari_min",
        "active_tier_ratio": "active_tier_ratio_min",
        # Lower-is-better → _max suffix
        "davies_bouldin": "davies_bouldin_max",
        "medoid_fallback_rate": "medoid_fallback_rate_max",
        "noise_rate": "noise_rate_max",
        "low_conf_pct": "low_conf_pct_max",
        "unexplained_drift_pct": "unexplained_drift_pct_max",
        "label_churn_rate": "label_churn_rate_max",
        "orphan_pct": "orphan_pct_max",
        "resurrection_count": "resurrection_count_max",
    }
    return direction_map.get(metric)


def _calibrate_golden_set(conn, result: CalibrationResult) -> None:
    """Binary-search each precision-relevant threshold against the pair
    golden set; auto-commit if resulting precision ≥ 0.80, else enqueue
    as a threshold_suggestion for HITL review.

    Simplified implementation: computes current precision/recall on the
    golden set using current thresholds, and either confirms or proposes
    a single suggestion (rather than sweeping all thresholds — the
    sweep lives in a production tuning script rather than here).
    """
    try:
        gs_count = conn.execute(
            "SELECT COUNT(*) FROM canonicalization_golden_set"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        gs_count = 0
    if gs_count == 0:
        result.notes.append("golden_set calibration skipped: no golden_set pairs loaded")
        return

    # Load current assignments for pair evaluation
    try:
        pairs = conn.execute(
            """
            SELECT g.pair_id, g.ticket_a_id, g.ticket_b_id, g.same_concept,
                   ta.canonical_issue_id AS cid_a,
                   tb.canonical_issue_id AS cid_b
              FROM canonicalization_golden_set g
              LEFT JOIN ticket_index ta ON ta.ticket_id = g.ticket_a_id
              LEFT JOIN ticket_index tb ON tb.ticket_id = g.ticket_b_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        result.notes.append("golden_set calibration: ticket_index join failed")
        return

    tp = fp = fn = tn = 0
    for _pid, _a, _b, same_concept, cid_a, cid_b in pairs:
        if cid_a is None or cid_b is None:
            continue
        predicted_same = cid_a == cid_b
        actual_same = bool(same_concept)
        if predicted_same and actual_same:
            tp += 1
        elif predicted_same and not actual_same:
            fp += 1
        elif not predicted_same and actual_same:
            fn += 1
        else:
            tn += 1

    total = tp + fp + fn + tn
    if total == 0:
        result.notes.append("golden_set calibration: no evaluable pairs")
        return
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    result.notes.append(
        f"golden_set precision={precision:.3f} recall={recall:.3f} (n_pairs={total})"
    )

    if precision >= 0.80:
        # Current thresholds are healthy — stamp the log
        result.audit_log_ids.append(_log_change(
            conn, setting_key="__calibration_marker__",
            old_value=None, new_value=precision,
            changed_by="golden_set",
            reason=f"golden set pass; precision {precision:.3f} ≥ 0.80",
            golden_precision=precision,
        ))
        return

    # Enqueue a threshold_suggestion item for HITL
    review_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO integrity_review_queue
             (review_id, item_type, payload_json, priority)
           VALUES (?, 'threshold_suggestion', ?, 3)""",
        (review_id, json.dumps({
            "precision": precision,
            "recall": recall,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "suggestion": "tighten similarity thresholds to raise precision",
        })),
    )
    result.notes.append(f"queued threshold_suggestion for HITL (review_id={review_id})")


def _calibrate_hitl(conn, result: CalibrationResult) -> None:
    """Apply approved threshold_suggestion items from integrity_review_queue.

    A 'threshold_suggestion' item with decision='apply' and a
    payload_json key `threshold_overrides` = {setting_key: value} will be
    committed and the review item marked reviewed.
    """
    try:
        rows = conn.execute(
            """
            SELECT review_id, payload_json FROM integrity_review_queue
             WHERE item_type = 'threshold_suggestion'
               AND decision  = 'apply'
               AND reviewed_at IS NOT NULL
            """
        ).fetchall()
    except sqlite3.OperationalError:
        result.notes.append("hitl calibration skipped: queue table missing")
        return

    for review_id, payload_raw in rows:
        try:
            payload = json.loads(payload_raw or "{}")
        except Exception:
            continue
        overrides = payload.get("threshold_overrides") or {}
        for key, val in overrides.items():
            try:
                new_val = float(val)
            except (TypeError, ValueError):
                continue
            old = get_threshold(conn, key)
            _upsert_threshold(conn, setting_key=key, value=new_val, stage="hitl")
            result.audit_log_ids.append(_log_change(
                conn, setting_key=key, old_value=old, new_value=new_val,
                changed_by="hitl", reason=f"HITL review {review_id}",
            ))
            result.thresholds_updated.append(key)


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────

def calibrate_thresholds(
    conn, stage: str, *, source_scan_range: Optional[str] = None,
) -> CalibrationResult:
    """Run a single calibration stage. Returns what changed.

    Stage = 'literature' | 'empirical' | 'golden_set' | 'hitl'.
    Unknown stages raise ValueError.
    """
    if not _migration_applied(conn):
        logger.warning(
            "calibrate_thresholds: migration 024 not applied; skipping %s", stage,
        )
        return CalibrationResult(stage=stage, notes=["migration 024 not applied"])

    result = CalibrationResult(stage=stage)
    if stage == "literature":
        _calibrate_literature(conn, result)
    elif stage == "empirical":
        _calibrate_empirical(conn, result, source_scan_range=source_scan_range)
    elif stage == "golden_set":
        _calibrate_golden_set(conn, result)
    elif stage == "hitl":
        _calibrate_hitl(conn, result)
    else:
        raise ValueError(
            f"Unknown calibration stage {stage!r}; "
            "expected 'literature' | 'empirical' | 'golden_set' | 'hitl'"
        )
    return result
