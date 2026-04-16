"""Tests for Phase 8 threshold calibrator (S10.2)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from src.data.db_manager import DatabaseManager
from src.data.threshold_calibrator import (
    LITERATURE_DEFAULTS,
    CalibrationResult,
    calibrate_thresholds,
    get_threshold,
)


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
# Literature stage
# ──────────────────────────────────────────────────────────────────────────

def test_literature_seeds_all_defaults(fresh_db):
    result = calibrate_thresholds(fresh_db, "literature")
    assert isinstance(result, CalibrationResult)
    assert result.stage == "literature"
    assert len(result.thresholds_updated) == len(LITERATURE_DEFAULTS)
    assert len(result.thresholds_unchanged) == 0

    # Verify every key landed in threshold_values_current
    rows = {r[0]: r[1] for r in fresh_db.execute(
        "SELECT setting_key, value FROM threshold_values_current"
    ).fetchall()}
    for key, val in LITERATURE_DEFAULTS.items():
        assert rows.get(key) == pytest.approx(val)


def test_literature_idempotent(fresh_db):
    r1 = calibrate_thresholds(fresh_db, "literature")
    r2 = calibrate_thresholds(fresh_db, "literature")
    assert len(r1.thresholds_updated) > 0
    assert len(r2.thresholds_updated) == 0
    assert len(r2.thresholds_unchanged) == len(LITERATURE_DEFAULTS)


def test_literature_writes_change_log_rows(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM threshold_change_log WHERE changed_by = 'literature'"
    ).fetchone()[0]
    assert n == len(LITERATURE_DEFAULTS)


def test_get_threshold_reads_seeded_value(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    assert get_threshold(fresh_db, "silhouette_mean_min") == pytest.approx(0.30)


def test_get_threshold_missing_returns_default(fresh_db):
    assert get_threshold(fresh_db, "nonexistent_key", default=0.99) == 0.99


# ──────────────────────────────────────────────────────────────────────────
# Empirical stage
# ──────────────────────────────────────────────────────────────────────────

def test_empirical_skips_when_insufficient_scans(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    # Only 1 scan of data → should no-op
    fresh_db.execute(
        """INSERT INTO data_integrity_scores
             (scan_id, dimension, metric, value, threshold, passed)
           VALUES ('s1', 'coherence', 'silhouette_mean', 0.45, 0.30, 1)"""
    )
    fresh_db.commit()
    result = calibrate_thresholds(fresh_db, "empirical")
    assert len(result.thresholds_updated) == 0
    assert any("need ≥3 scans" in n for n in result.notes)


def test_empirical_reseats_thresholds_after_3_scans(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    # 3 scans with observed silhouette_mean values 0.4, 0.5, 0.6
    for scan, val in [("s1", 0.4), ("s2", 0.5), ("s3", 0.6)]:
        fresh_db.execute(
            """INSERT INTO data_integrity_scores
                 (scan_id, dimension, metric, value, threshold, passed)
               VALUES (?, 'coherence', 'silhouette_mean', ?, 0.30, 1)""",
            (scan, val),
        )
    fresh_db.commit()
    result = calibrate_thresholds(fresh_db, "empirical")
    # p10 of [0.4, 0.5, 0.6] = 0.42 (new floor should be lower than old max observation)
    new_val = get_threshold(fresh_db, "silhouette_mean_min")
    assert new_val is not None
    # It's a _min threshold so it reseats at p10
    assert new_val == pytest.approx(0.42, abs=0.01)
    assert "silhouette_mean_min" in result.thresholds_updated


def test_empirical_lower_better_uses_p90(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    # 3 scans with noise_rate 0.05, 0.10, 0.15
    for scan, val in [("s1", 0.05), ("s2", 0.10), ("s3", 0.15)]:
        fresh_db.execute(
            """INSERT INTO data_integrity_scores
                 (scan_id, dimension, metric, value, threshold, passed)
               VALUES (?, 'assignment_precision', 'noise_rate', ?, 0.20, 1)""",
            (scan, val),
        )
    fresh_db.commit()
    calibrate_thresholds(fresh_db, "empirical")
    # p90 of [0.05, 0.10, 0.15] = 0.14
    new_val = get_threshold(fresh_db, "noise_rate_max")
    assert new_val == pytest.approx(0.14, abs=0.01)


def test_empirical_writes_change_log(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    for scan, val in [("s1", 0.4), ("s2", 0.5), ("s3", 0.6)]:
        fresh_db.execute(
            """INSERT INTO data_integrity_scores
                 (scan_id, dimension, metric, value, threshold, passed)
               VALUES (?, 'coherence', 'silhouette_mean', ?, 0.30, 1)""",
            (scan, val),
        )
    fresh_db.commit()
    calibrate_thresholds(fresh_db, "empirical")
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM threshold_change_log WHERE changed_by = 'empirical'"
    ).fetchone()[0]
    assert n > 0


# ──────────────────────────────────────────────────────────────────────────
# Golden-set stage
# ──────────────────────────────────────────────────────────────────────────

def test_golden_set_empty_queue_note(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    result = calibrate_thresholds(fresh_db, "golden_set")
    assert any("no golden_set pairs" in n for n in result.notes)


def test_golden_set_passes_when_precision_high(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    # Seed 10 pairs — all "same_concept=True" pairs actually land in the
    # same canonical_issue_id
    for i in range(10):
        a, b = f"t{i}a", f"t{i}b"
        fresh_db.execute(
            """INSERT INTO ticket_index
                 (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
                  first_seen_date, ticket_created_date, canonical_issue_id)
               VALUES (?, 'BILLING', 's1', 's1', '2026-04-15T00:00:00', '2026-04-15', ?)""",
            (a, "cluster-1"),
        )
        fresh_db.execute(
            """INSERT INTO ticket_index
                 (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id,
                  first_seen_date, ticket_created_date, canonical_issue_id)
               VALUES (?, 'BILLING', 's1', 's1', '2026-04-15T00:00:00', '2026-04-15', ?)""",
            (b, "cluster-1"),
        )
        fresh_db.execute(
            """INSERT INTO canonicalization_golden_set
                 (pair_id, ticket_a_id, ticket_b_id, same_concept, source)
               VALUES (?, ?, ?, 1, 'test')""",
            (uuid.uuid4().hex, a, b),
        )
    fresh_db.commit()

    result = calibrate_thresholds(fresh_db, "golden_set")
    # Precision should be 1.0 → no HITL suggestion enqueued
    assert not any("queued threshold_suggestion" in n for n in result.notes)
    # A marker log row should exist
    n = fresh_db.execute(
        """SELECT COUNT(*) FROM threshold_change_log
            WHERE changed_by = 'golden_set'
              AND setting_key = '__calibration_marker__'"""
    ).fetchone()[0]
    assert n == 1


def test_golden_set_enqueues_when_precision_low(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    # 5 pairs same_concept=True but tickets land in DIFFERENT clusters (all FN)
    # + 5 pairs same_concept=False but tickets land in the SAME cluster (all FP)
    for i in range(5):
        a, b = f"sa{i}", f"sb{i}"
        fresh_db.execute(
            "INSERT INTO ticket_index (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id, first_seen_date, ticket_created_date, canonical_issue_id) VALUES (?, 'BILLING', 's1', 's1', '2026-04-15T00:00:00', '2026-04-15', ?)",
            (a, f"cA-{i}"),  # different clusters
        )
        fresh_db.execute(
            "INSERT INTO ticket_index (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id, first_seen_date, ticket_created_date, canonical_issue_id) VALUES (?, 'BILLING', 's1', 's1', '2026-04-15T00:00:00', '2026-04-15', ?)",
            (b, f"cB-{i}"),
        )
        fresh_db.execute(
            """INSERT INTO canonicalization_golden_set
                 (pair_id, ticket_a_id, ticket_b_id, same_concept, source)
               VALUES (?, ?, ?, 1, 'test')""",
            (uuid.uuid4().hex, a, b),
        )
    for i in range(5):
        a, b = f"da{i}", f"db{i}"
        fresh_db.execute(
            "INSERT INTO ticket_index (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id, first_seen_date, ticket_created_date, canonical_issue_id) VALUES (?, 'BILLING', 's1', 's1', '2026-04-15T00:00:00', '2026-04-15', ?)",
            (a, "cShared"),
        )
        fresh_db.execute(
            "INSERT INTO ticket_index (ticket_id, trc_code, first_seen_scan_id, last_seen_scan_id, first_seen_date, ticket_created_date, canonical_issue_id) VALUES (?, 'BILLING', 's1', 's1', '2026-04-15T00:00:00', '2026-04-15', ?)",
            (b, "cShared"),
        )
        fresh_db.execute(
            """INSERT INTO canonicalization_golden_set
                 (pair_id, ticket_a_id, ticket_b_id, same_concept, source)
               VALUES (?, ?, ?, 0, 'test')""",
            (uuid.uuid4().hex, a, b),
        )
    fresh_db.commit()

    result = calibrate_thresholds(fresh_db, "golden_set")
    # precision = 0.0 → should enqueue
    assert any("queued threshold_suggestion" in n for n in result.notes)
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM integrity_review_queue WHERE item_type = 'threshold_suggestion'"
    ).fetchone()[0]
    assert n == 1


# ──────────────────────────────────────────────────────────────────────────
# HITL stage
# ──────────────────────────────────────────────────────────────────────────

def test_hitl_applies_approved_overrides(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    # Create an approved threshold_suggestion with an override
    review_id = uuid.uuid4().hex
    fresh_db.execute(
        """INSERT INTO integrity_review_queue
             (review_id, item_type, payload_json, decision, reviewed_at, reviewed_by)
           VALUES (?, 'threshold_suggestion', ?, 'apply', CURRENT_TIMESTAMP, 'test-user')""",
        (review_id, json.dumps({"threshold_overrides": {"silhouette_mean_min": 0.45}})),
    )
    fresh_db.commit()

    result = calibrate_thresholds(fresh_db, "hitl")
    assert "silhouette_mean_min" in result.thresholds_updated
    # New value should have replaced the literature default
    assert get_threshold(fresh_db, "silhouette_mean_min") == pytest.approx(0.45)
    # And change log recorded it
    n = fresh_db.execute(
        "SELECT COUNT(*) FROM threshold_change_log WHERE changed_by = 'hitl'"
    ).fetchone()[0]
    assert n == 1


def test_hitl_skips_unreviewed_items(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    fresh_db.execute(
        """INSERT INTO integrity_review_queue
             (review_id, item_type, payload_json)
           VALUES (?, 'threshold_suggestion', ?)""",
        (uuid.uuid4().hex, json.dumps({"threshold_overrides": {"silhouette_mean_min": 0.99}})),
    )
    fresh_db.commit()
    result = calibrate_thresholds(fresh_db, "hitl")
    assert len(result.thresholds_updated) == 0
    # threshold unchanged
    assert get_threshold(fresh_db, "silhouette_mean_min") == pytest.approx(0.30)


# ──────────────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────────────

def test_invalid_stage_raises(fresh_db):
    calibrate_thresholds(fresh_db, "literature")
    with pytest.raises(ValueError):
        calibrate_thresholds(fresh_db, "nonsense_stage")


def test_migration_missing_noop(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "raw.db"))
    result = calibrate_thresholds(conn, "literature")
    assert isinstance(result, CalibrationResult)
    assert len(result.thresholds_updated) == 0
    assert any("migration 024" in n for n in result.notes)
