"""Tests for Phase 8 data integrity scorer (S10.2).

Covers score_integrity(), dimension aggregation, status bands, and review
queue enqueue for failed metrics.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from src.data.data_integrity_scorer import (
    DIMENSION_WEIGHTS,
    IntegrityDimension,
    IntegrityMetric,
    IntegrityReport,
    _band_for,
    _composite,
    _dimension_score,
    _metric_passed,
    score_integrity,
)
from src.data.db_manager import DatabaseManager
from src.data.threshold_calibrator import calibrate_thresholds


@pytest.fixture
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    try:
        conn.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────

def test_metric_passed_higher_better():
    assert _metric_passed("silhouette_mean", 0.5, 0.3) is True
    assert _metric_passed("silhouette_mean", 0.2, 0.3) is False
    assert _metric_passed("silhouette_mean", None, 0.3) is False
    assert _metric_passed("silhouette_mean", 0.5, None) is False


def test_metric_passed_lower_better():
    assert _metric_passed("noise_rate", 0.1, 0.2) is True
    assert _metric_passed("noise_rate", 0.3, 0.2) is False


def test_band_boundaries():
    assert _band_for(100.0) == "HEALTHY"
    assert _band_for(90.0) == "HEALTHY"
    assert _band_for(89.99) == "ACCEPTABLE"
    assert _band_for(80.0) == "ACCEPTABLE"
    assert _band_for(79.99) == "DEGRADED"
    assert _band_for(70.0) == "DEGRADED"
    assert _band_for(69.99) == "UNHEALTHY"
    assert _band_for(0.0) == "UNHEALTHY"


def test_dimension_score_all_pass():
    metrics = [
        IntegrityMetric("silhouette_mean", 0.5, 0.3, True),
        IntegrityMetric("noise_rate", 0.1, 0.2, True),
    ]
    assert _dimension_score(metrics) == 100.0


def test_dimension_score_all_fail():
    metrics = [
        IntegrityMetric("silhouette_mean", 0.1, 0.3, False),
        IntegrityMetric("noise_rate", 0.5, 0.2, False),
    ]
    assert _dimension_score(metrics) == 0.0


def test_dimension_score_partial():
    metrics = [
        IntegrityMetric("silhouette_mean", 0.5, 0.3, True),
        IntegrityMetric("noise_rate", 0.5, 0.2, False),
        IntegrityMetric("low_conf_pct", 0.05, 0.15, True),
    ]
    # 2 of 3 pass → 66.67
    assert _dimension_score(metrics) == pytest.approx(200.0 / 3, abs=1e-4)


def test_dimension_score_skips_missing_data():
    metrics = [
        IntegrityMetric("silhouette_mean", None, 0.3, False),
        IntegrityMetric("noise_rate", 0.1, 0.2, True),
    ]
    # Only one scored metric, and it passed → 100
    assert _dimension_score(metrics) == 100.0


def test_dimension_score_all_missing_returns_neutral():
    metrics = [
        IntegrityMetric("silhouette_mean", None, None, False),
        IntegrityMetric("noise_rate", None, None, False),
    ]
    # All None → neutral 70 (borderline DEGRADED)
    assert _dimension_score(metrics) == 70.0


def test_composite_weighted_sum():
    dims = [
        IntegrityDimension("coherence", [], 100.0, 0.25),
        IntegrityDimension("label_fidelity", [], 50.0, 0.20),
        IntegrityDimension("assignment_precision", [], 80.0, 0.20),
        IntegrityDimension("temporal_stability", [], 100.0, 0.20),
        IntegrityDimension("population_health", [], 60.0, 0.15),
    ]
    # (100*0.25 + 50*0.20 + 80*0.20 + 100*0.20 + 60*0.15) / 1.0 = 25+10+16+20+9 = 80
    assert _composite(dims) == pytest.approx(80.0, abs=1e-4)


def test_composite_zero_weight_returns_zero():
    dims = [IntegrityDimension("x", [], 100.0, 0.0)]
    assert _composite(dims) == 0.0


# ──────────────────────────────────────────────────────────────────────────
# Integration: score_integrity on fresh DB
# ──────────────────────────────────────────────────────────────────────────

def test_score_integrity_empty_db(fresh_db):
    """Empty DB produces a report — dimensions with no data get neutral 70."""
    rep = score_integrity(fresh_db, scan_id="s1")
    assert isinstance(rep, IntegrityReport)
    assert rep.scan_id == "s1"
    assert len(rep.dimensions) == 5
    names = {d.name for d in rep.dimensions}
    assert names == set(DIMENSION_WEIGHTS)
    # Composite should be in [0, 100]
    assert 0.0 <= rep.composite_score <= 100.0


def test_score_integrity_seeds_literature_thresholds(fresh_db):
    score_integrity(fresh_db, scan_id="s1")
    # After first call, threshold_values_current should be seeded
    n = fresh_db.execute("SELECT COUNT(*) FROM threshold_values_current").fetchone()[0]
    assert n > 0
    # And a literature-stage change log row exists
    n_log = fresh_db.execute(
        "SELECT COUNT(*) FROM threshold_change_log WHERE changed_by = 'literature'"
    ).fetchone()[0]
    assert n_log > 0


def test_score_integrity_writes_score_rows(fresh_db):
    score_integrity(fresh_db, scan_id="s1")
    rows = fresh_db.execute(
        "SELECT dimension, metric FROM data_integrity_scores WHERE scan_id = 's1'"
    ).fetchall()
    # 5 dimensions × 3 metrics each = 15 rows
    assert len(rows) == 15
    dims = {r[0] for r in rows}
    assert dims == set(DIMENSION_WEIGHTS)


def test_score_integrity_rerun_replaces_rows(fresh_db):
    score_integrity(fresh_db, scan_id="s1")
    n1 = fresh_db.execute(
        "SELECT COUNT(*) FROM data_integrity_scores WHERE scan_id = 's1'"
    ).fetchone()[0]
    score_integrity(fresh_db, scan_id="s1")
    n2 = fresh_db.execute(
        "SELECT COUNT(*) FROM data_integrity_scores WHERE scan_id = 's1'"
    ).fetchone()[0]
    assert n1 == n2


def test_score_integrity_different_scans_coexist(fresh_db):
    score_integrity(fresh_db, scan_id="s1")
    score_integrity(fresh_db, scan_id="s2")
    scans = {r[0] for r in fresh_db.execute(
        "SELECT DISTINCT scan_id FROM data_integrity_scores"
    ).fetchall()}
    assert scans == {"s1", "s2"}


def test_score_integrity_status_band_matches_composite(fresh_db):
    rep = score_integrity(fresh_db, scan_id="s1")
    assert rep.status_band == _band_for(rep.composite_score)


def test_score_integrity_failed_metrics_enqueue_review(fresh_db):
    # Seed a scenario with an obvious failure: add a cluster with
    # noise rate = 100% (all unclustered)
    fresh_db.execute(
        """INSERT INTO ticket_index
             (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
              first_seen_date, ticket_created_date,
              canonical_issue_id, assignment_method, canonical_confidence)
           VALUES ('t1', 'BILLING', 's1', 's1',
                   '2026-04-15T00:00:00', '2026-04-15',
                   NULL, 'unclustered', 0.0)"""
    )
    fresh_db.commit()
    rep = score_integrity(fresh_db, scan_id="s1")
    # After scoring, review queue should have failure items
    n = fresh_db.execute("SELECT COUNT(*) FROM integrity_review_queue").fetchone()[0]
    assert n > 0
    # rep.items_for_review should match queue count for this scan
    assert len(rep.items_for_review) == n


def test_score_integrity_persist_false_no_db_writes(fresh_db):
    score_integrity(fresh_db, scan_id="s1", persist=False)
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM data_integrity_scores WHERE scan_id = 's1'"
    ).fetchone()[0]
    assert n == 0


def test_score_integrity_dimension_status_bands(fresh_db):
    rep = score_integrity(fresh_db, scan_id="s1")
    # Each dimension has a status band assigned
    for dim in rep.dimensions:
        assert dim.status_band in ("HEALTHY", "ACCEPTABLE", "DEGRADED", "UNHEALTHY")
        assert dim.status_band == _band_for(dim.raw_score)


def test_score_integrity_composite_deterministic(fresh_db):
    rep1 = score_integrity(fresh_db, scan_id="s1")
    rep2 = score_integrity(fresh_db, scan_id="s1")
    assert rep1.composite_score == rep2.composite_score


def test_score_integrity_migration_missing_graceful(tmp_path: Path):
    """Raw sqlite DB without our migrations → scorer doesn't crash."""
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    rep = score_integrity(conn, scan_id="s1")
    assert isinstance(rep, IntegrityReport)
    assert rep.composite_score >= 0.0
