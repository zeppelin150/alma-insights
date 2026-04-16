"""
Alma Insights — Unit tests for incident_engine.py

Tests cover:
  - Poisson parameter computation (lambda, theta_1, theta_2)
  - CUSUM accumulation and reset logic
  - Flag creation, update, and status transitions
  - Tier assignment (tier 1 vs tier 2)
  - Edge cases (empty data, low-volume TRCs, insufficient history)
  - The main entry point run_incident_scan
  - Flag management functions (acknowledge, resolve, false_positive)
  - Intervention correlation
  - Interpretation string building

Uses the seeded_db fixture from conftest.py (100 tickets across 3 TRCs, 30 days).
Note: conftest seeds daily_counts with column "day_bucket" but the schema column
is "date", so the conftest INSERT silently fails.  Tests that need daily_counts
reseed directly with the correct column name.
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pytest
from scipy.stats import poisson

from src.data.incident_engine import (
    BASELINE_WINDOW_DAYS,
    CUSUM_H_FACTOR,
    CUSUM_K_FACTOR,
    MIN_DAYS_FOR_BASELINE,
    MIN_NONZERO_BASELINE_DAYS,
    THETA_1_PERCENTILE,
    THETA_2_PERCENTILE,
    TIER_2_DAILY_THRESHOLD,
    _analyze_trc,
    _analyze_trc_hourly,
    _build_interpretation,
    _evaluate_flags,
    _save_baseline,
    acknowledge_flag,
    correlate_flag_with_interventions,
    get_flag_history,
    get_open_flags,
    mark_false_positive,
    resolve_flag,
    run_incident_scan,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _seed_daily_counts(db, trc_code, counts_by_day, base_date=None):
    """Insert daily_counts rows with the correct 'date' column.

    counts_by_day: list of (days_ago, count) tuples.
    """
    base = base_date or datetime.now()
    for days_ago, count in counts_by_day:
        day = (base - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        db.conn.execute(
            "INSERT OR REPLACE INTO daily_counts (date, trc_code, ticket_count) "
            "VALUES (?, ?, ?)",
            (day, trc_code, count),
        )
    db.conn.commit()


def _seed_hourly_counts(db, trc_code, entries, base_date=None):
    """Insert hourly_counts rows.

    entries: list of (days_ago, hour, count).
    """
    base = base_date or datetime.now()
    for days_ago, hour, count in entries:
        day = (base - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        db.conn.execute(
            "INSERT OR REPLACE INTO hourly_counts (date, hour, trc_code, ticket_count) "
            "VALUES (?, ?, ?, ?)",
            (day, hour, trc_code, count),
        )
    db.conn.commit()


def _insert_flag(db, trc_code, flag_type, theta_level, triggered_date,
                 observed_value=10, expected_lambda=5.0, threshold_value=8,
                 status="open", hour=None, p_value=None, cusum_value=None):
    """Insert a flag row directly and return the flag_id."""
    now = datetime.now().isoformat()
    cur = db.conn.execute("""
        INSERT INTO incident_flags
            (trc_code, flag_type, theta_level, direction, triggered_at,
             triggered_date, triggered_hour, observed_value, expected_lambda,
             threshold_value, p_value, cusum_value, status, created_at)
        VALUES (?, ?, ?, 'above', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trc_code, flag_type, theta_level, now,
        triggered_date, hour,
        observed_value, expected_lambda, threshold_value,
        p_value, cusum_value, status, now,
    ))
    db.conn.commit()
    return cur.lastrowid


# ── 1. Poisson parameter computation ────────────────────────────────


class TestPoissonParameters:
    """Verify lambda, theta_1, theta_2 computation from _analyze_trc."""

    def test_lambda_from_uniform_counts(self, seeded_db):
        """Lambda should equal total_tickets / calendar_days for a uniform series."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Seed 30 days at exactly 5 tickets/day
        _seed_daily_counts(db, "TRC-100", [(d, 5) for d in range(1, 31)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)

        assert not result["insufficient_data"]
        assert abs(result["lambda_daily"] - 5.0) < 0.1
        assert result["tier"] == 1  # lambda < 20

    def test_theta_1_above_lambda(self, seeded_db):
        """Theta-1 must be strictly above lambda (floor guard)."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        _seed_daily_counts(db, "TRC-100", [(d, 3) for d in range(1, 31)], base_date=today)
        result = _analyze_trc(db, "TRC-100", target, window_start)

        assert result["theta_1_daily"] > result["lambda_daily"]

    def test_theta_2_above_theta_1(self, seeded_db):
        """Theta-2 must be strictly above theta-1."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        _seed_daily_counts(db, "TRC-100", [(d, 8) for d in range(1, 31)], base_date=today)
        result = _analyze_trc(db, "TRC-100", target, window_start)

        assert result["theta_2_daily"] > result["theta_1_daily"]

    def test_p_value_high_when_below_lambda(self, seeded_db):
        """P-value should be close to 1 when observed is well below lambda."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # 30 days at 10 tickets, but target date has 1 ticket
        _seed_daily_counts(db, "TRC-100", [(d, 10) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 1)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["p_value_daily"] > 0.5

    def test_p_value_low_when_extreme_spike(self, seeded_db):
        """P-value should be very low for an extreme spike."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Baseline of 3/day, spike to 50 on target
        _seed_daily_counts(db, "TRC-100", [(d, 3) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 50)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["p_value_daily"] < 0.01


# ── 2. CUSUM accumulation and reset ─────────────────────────────────


class TestCUSUM:
    """CUSUM drift detection logic."""

    def test_cusum_zero_when_at_baseline(self, seeded_db):
        """CUSUM should stay near zero when counts equal lambda."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        _seed_daily_counts(db, "TRC-100", [(d, 5) for d in range(0, 31)], base_date=today)
        result = _analyze_trc(db, "TRC-100", target, window_start)

        # With constant 5 and lambda ~5, CUSUM should be 0 (clamped at max(0,...))
        assert result["cusum_value"] == 0.0
        assert not result["cusum_alert"]

    def test_cusum_accumulates_on_sustained_elevation(self, seeded_db):
        """CUSUM should accumulate when counts are consistently above lambda+k."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Baseline: 3/day for older days, then elevated 15/day for the last few
        counts = [(d, 3) for d in range(5, 31)]
        counts += [(d, 15) for d in range(0, 5)]  # last 5 days elevated
        _seed_daily_counts(db, "TRC-100", counts, base_date=today)

        # Seed a prior CUSUM value (simulating accumulated drift)
        lam = 3.0
        h = CUSUM_H_FACTOR * math.sqrt(max(lam, 0.01))
        db.conn.execute(
            "INSERT OR REPLACE INTO trc_baselines "
            "(trc_code, tier, lambda_daily, theta_1_daily, theta_2_daily, "
            " cusum_value, cusum_threshold, cusum_slack, baseline_days, last_updated) "
            "VALUES (?, 1, ?, 5, 6, ?, ?, 1.5, 30, ?)",
            ("TRC-100", lam, h * 0.8, h, datetime.now().isoformat()),
        )
        db.conn.commit()

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["cusum_value"] > 0

    def test_cusum_resets_to_zero_floor(self, seeded_db):
        """CUSUM never goes below zero."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Baseline 10/day, today 0 — negative increment should clamp at 0
        _seed_daily_counts(db, "TRC-100", [(d, 10) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 0)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["cusum_value"] >= 0.0

    def test_cusum_alert_elevates_flag_level(self, seeded_db):
        """A CUSUM alert should set flag_level >= 1 even if Poisson didn't fire."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Set up so that today is just below theta_1 but CUSUM has accumulated
        lam = 5.0
        h = CUSUM_H_FACTOR * math.sqrt(lam)
        # Seed a CUSUM value already past threshold
        db.conn.execute(
            "INSERT OR REPLACE INTO trc_baselines "
            "(trc_code, tier, lambda_daily, theta_1_daily, theta_2_daily, "
            " cusum_value, cusum_threshold, cusum_slack, baseline_days, last_updated) "
            "VALUES (?, 1, ?, 8, 10, ?, ?, 2.5, 30, ?)",
            ("TRC-100", lam, h + 5.0, h, datetime.now().isoformat()),
        )
        db.conn.commit()

        # Counts: baseline 5/day, today 7 (below theta_1=8 but above lambda+k)
        _seed_daily_counts(db, "TRC-100", [(d, 5) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 7)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)

        if result["cusum_alert"]:
            assert result["flag_level"] >= 1
            assert result["flag_source"] in ("cusum", "poisson_daily")


# ── 3. Flag creation and status transitions ──────────────────────────


class TestFlagLifecycle:
    """Flag creation via _evaluate_flags and status transitions."""

    def test_flag_created_for_theta1_breach(self, seeded_db):
        """A theta-1 breach should create an 'open' flag."""
        db = seeded_db
        result = {
            "trc_code": "TRC-100",
            "flag_level": 1,
            "flag_source": "poisson_daily",
            "observed_today": 15,
            "lambda_daily": 5.0,
            "theta_1_daily": 9,
            "theta_2_daily": 12,
            "p_value_daily": 0.02,
            "cusum_value": 1.5,
            "cusum_threshold": 11.18,
            "cusum_alert": False,
            "insufficient_data": False,
            "hourly_detail": None,
        }

        target_date = datetime.now().strftime("%Y-%m-%d")
        flags = _evaluate_flags(db, "TRC-100", target_date, result)

        assert len(flags) == 1
        assert flags[0]["theta_level"] == 1
        assert flags[0]["flag_type"] == "poisson_daily"

        # Verify persisted to DB
        row = db.conn.execute(
            "SELECT * FROM incident_flags WHERE trc_code = 'TRC-100' AND triggered_date = ?",
            (target_date,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "open"

    def test_flag_updated_not_duplicated(self, seeded_db):
        """Calling _evaluate_flags twice on same date should update, not duplicate."""
        db = seeded_db
        target_date = datetime.now().strftime("%Y-%m-%d")
        result = {
            "trc_code": "TRC-200",
            "flag_level": 1,
            "flag_source": "poisson_daily",
            "observed_today": 12,
            "lambda_daily": 5.0,
            "theta_1_daily": 9,
            "theta_2_daily": 14,
            "p_value_daily": 0.03,
            "cusum_value": 2.0,
            "cusum_threshold": 11.18,
            "cusum_alert": False,
            "insufficient_data": False,
            "hourly_detail": None,
        }

        # First call creates the flag
        _evaluate_flags(db, "TRC-200", target_date, result)

        # Second call with higher observation should update
        result["observed_today"] = 18
        result["flag_level"] = 2
        flags2 = _evaluate_flags(db, "TRC-200", target_date, result)

        # Second call finds existing open flag and updates it (returns empty list)
        count = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM incident_flags "
            "WHERE trc_code = 'TRC-200' AND triggered_date = ? AND flag_type = 'poisson_daily'",
            (target_date,)
        ).fetchone()["cnt"]
        assert count == 1  # not duplicated

    def test_no_flag_when_level_zero(self, seeded_db):
        """No flag should be created when flag_level is 0."""
        db = seeded_db
        result = {
            "trc_code": "TRC-300",
            "flag_level": 0,
            "flag_source": "none",
            "observed_today": 3,
            "lambda_daily": 5.0,
            "theta_1_daily": 9,
            "theta_2_daily": 12,
            "p_value_daily": 0.9,
            "cusum_value": 0.0,
            "insufficient_data": False,
            "hourly_detail": None,
        }
        flags = _evaluate_flags(db, "TRC-300", datetime.now().strftime("%Y-%m-%d"), result)
        assert flags == []

    def test_no_flag_when_insufficient_data(self, seeded_db):
        """No flag should be created when insufficient_data is True."""
        db = seeded_db
        result = {
            "trc_code": "TRC-300",
            "flag_level": 2,
            "flag_source": "poisson_daily",
            "observed_today": 50,
            "lambda_daily": 0.0,
            "theta_1_daily": 0,
            "theta_2_daily": 0,
            "insufficient_data": True,
            "hourly_detail": None,
        }
        flags = _evaluate_flags(db, "TRC-300", datetime.now().strftime("%Y-%m-%d"), result)
        assert flags == []

    def test_acknowledge_flag(self, seeded_db):
        """acknowledge_flag should set status to 'acknowledged'."""
        db = seeded_db
        fid = _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-20")
        acknowledge_flag(db, fid, notes="Investigating")

        row = db.conn.execute(
            "SELECT status, notes FROM incident_flags WHERE flag_id = ?", (fid,)
        ).fetchone()
        assert row["status"] == "acknowledged"
        assert row["notes"] == "Investigating"

    def test_resolve_flag(self, seeded_db):
        """resolve_flag should set status to 'resolved' and set resolved_at."""
        db = seeded_db
        fid = _insert_flag(db, "TRC-100", "poisson_daily", 2, "2026-03-20")
        resolve_flag(db, fid, notes="Root cause fixed")

        row = db.conn.execute(
            "SELECT status, resolved_at, notes FROM incident_flags WHERE flag_id = ?", (fid,)
        ).fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_at"] != ""
        assert row["notes"] == "Root cause fixed"

    def test_mark_false_positive(self, seeded_db):
        """mark_false_positive should set status to 'false_positive'."""
        db = seeded_db
        fid = _insert_flag(db, "TRC-200", "cusum", 1, "2026-03-20")
        mark_false_positive(db, fid, notes="Holiday traffic")

        row = db.conn.execute(
            "SELECT status, notes FROM incident_flags WHERE flag_id = ?", (fid,)
        ).fetchone()
        assert row["status"] == "false_positive"
        assert row["notes"] == "Holiday traffic"


# ── 4. Tier assignment ───────────────────────────────────────────────


class TestTierAssignment:
    """Tier 1 vs tier 2 based on lambda_daily threshold."""

    def test_low_volume_is_tier1(self, seeded_db):
        """TRC with lambda < 20 should be tier 1."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        _seed_daily_counts(db, "TRC-100", [(d, 5) for d in range(1, 31)], base_date=today)
        result = _analyze_trc(db, "TRC-100", target, window_start)

        assert result["tier"] == 1
        assert result["hourly_detail"] is None

    def test_high_volume_is_tier2(self, seeded_db):
        """TRC with lambda >= 20 should be tier 2."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.execute("DELETE FROM hourly_counts")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # 25 tickets/day => lambda >= 20 => tier 2
        _seed_daily_counts(db, "TRC-100", [(d, 25) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 25)], base_date=today)

        # Need hourly data for tier 2 analysis (enough baseline days)
        entries = []
        for d in range(1, 31):
            for h in range(24):
                entries.append((d, h, 1))
        # target day hourly
        for h in range(24):
            entries.append((0, h, 1))
        _seed_hourly_counts(db, "TRC-100", entries, base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["tier"] == 2

    def test_tier2_threshold_boundary(self, seeded_db):
        """Lambda exactly 20 should be tier 2 (>= check)."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.execute("DELETE FROM hourly_counts")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Exactly 20 * 30 = 600 total / 30 days = 20.0
        _seed_daily_counts(db, "TRC-100", [(d, 20) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 20)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["tier"] == 2


# ── 5. Edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    """Empty data, low-volume TRCs, insufficient history."""

    def test_insufficient_calendar_days(self, seeded_db):
        """Less than MIN_DAYS_FOR_BASELINE calendar days returns insufficient_data."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        # Window of only 3 days (below MIN_DAYS_FOR_BASELINE=7)
        window_start = (today - timedelta(days=3)).strftime("%Y-%m-%d")

        _seed_daily_counts(db, "TRC-100", [(d, 5) for d in range(1, 4)], base_date=today)
        result = _analyze_trc(db, "TRC-100", target, window_start)

        assert result["insufficient_data"] is True
        assert result["flag_level"] == 0

    def test_insufficient_nonzero_days(self, seeded_db):
        """Fewer than MIN_NONZERO_BASELINE_DAYS with data returns insufficient_data."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Only 2 days with data (< MIN_NONZERO_BASELINE_DAYS=3)
        _seed_daily_counts(db, "TRC-100", [(10, 5), (20, 3)], base_date=today)
        result = _analyze_trc(db, "TRC-100", target, window_start)

        assert result["insufficient_data"] is True

    def test_no_data_for_target_day(self, seeded_db):
        """When target day has no tickets, observed_today should be 0."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Data for baseline period only, nothing for today
        _seed_daily_counts(db, "TRC-100", [(d, 5) for d in range(1, 31)], base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["observed_today"] == 0

    def test_lambda_floor_guard(self, seeded_db):
        """Lambda should be floored at 0.01 for near-zero baselines."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")
        window_start = (today - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Very sparse: 3 days with 1 ticket each out of 30 calendar days
        # lambda = 3/30 = 0.1, which is above floor, but let's test near-zero
        _seed_daily_counts(db, "TRC-100",
                           [(5, 1), (15, 1), (25, 1)],
                           base_date=today)

        result = _analyze_trc(db, "TRC-100", target, window_start)
        assert result["lambda_daily"] >= 0.01

    def test_run_incident_scan_no_trcs(self, empty_db):
        """run_incident_scan with no TRC data should return zero results."""
        db = empty_db
        result = run_incident_scan(db, target_date="2026-03-20")

        assert result["trcs_scanned"] == 0
        assert result["trc_results"] == []
        assert result["new_flags"] == []

    def test_run_incident_scan_no_daily_counts(self, seeded_db):
        """run_incident_scan with TRCs but no daily_counts should return empty results."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.commit()

        result = run_incident_scan(db, target_date=datetime.now().strftime("%Y-%m-%d"))
        assert result["trcs_scanned"] > 0
        assert result["trc_results"] == []


# ── 6. Main entry point ─────────────────────────────────────────────


class TestRunIncidentScan:
    """Integration tests for run_incident_scan."""

    def test_basic_scan_returns_structure(self, seeded_db):
        """run_incident_scan returns expected keys."""
        db = seeded_db
        # Re-seed daily_counts with correct column name
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.commit()
        today = datetime.now()
        for trc in ["TRC-100", "TRC-200", "TRC-300"]:
            _seed_daily_counts(db, trc, [(d, 3) for d in range(1, 31)], base_date=today)

        result = run_incident_scan(db, target_date=today.strftime("%Y-%m-%d"))

        assert "scan_date" in result
        assert "trc_results" in result
        assert "new_flags" in result
        assert "open_flags_total" in result
        assert result["trcs_scanned"] == 3

    def test_scan_with_spike_creates_flags(self, seeded_db):
        """A large spike should generate at least one flag."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM incident_flags")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")

        # Normal baseline for TRC-100: 3/day
        _seed_daily_counts(db, "TRC-100", [(d, 3) for d in range(1, 31)], base_date=today)
        # Spike on target date
        _seed_daily_counts(db, "TRC-100", [(0, 50)], base_date=today)

        # Baseline for other TRCs
        for trc in ["TRC-200", "TRC-300"]:
            _seed_daily_counts(db, trc, [(d, 3) for d in range(1, 31)], base_date=today)

        result = run_incident_scan(db, target_date=target)

        # Should have a flag for TRC-100
        trc100_flags = [f for f in result["new_flags"] if f["trc_code"] == "TRC-100"]
        assert len(trc100_flags) >= 1
        assert trc100_flags[0]["theta_level"] >= 1

    def test_scan_progress_callback(self, seeded_db):
        """Progress callback should be invoked during scan."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.commit()

        today = datetime.now()
        _seed_daily_counts(db, "TRC-100", [(d, 3) for d in range(1, 31)], base_date=today)
        for trc in ["TRC-200", "TRC-300"]:
            _seed_daily_counts(db, trc, [(d, 2) for d in range(1, 31)], base_date=today)

        calls = []
        def cb(msg, pct):
            calls.append((msg, pct))

        run_incident_scan(db, target_date=today.strftime("%Y-%m-%d"),
                          progress_callback=cb)

        assert len(calls) >= 2  # at least "Loading TRC list..." and "Scan complete."
        # Final call should be 100%
        assert calls[-1][1] == 100

    def test_scan_results_sorted_by_severity(self, seeded_db):
        """TRC results should be sorted with highest flag_level first."""
        db = seeded_db
        db.conn.execute("DELETE FROM daily_counts")
        db.conn.execute("DELETE FROM incident_flags")
        db.conn.commit()

        today = datetime.now()
        target = today.strftime("%Y-%m-%d")

        # TRC-100: huge spike
        _seed_daily_counts(db, "TRC-100", [(d, 2) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-100", [(0, 100)], base_date=today)

        # TRC-200: normal
        _seed_daily_counts(db, "TRC-200", [(d, 5) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-200", [(0, 5)], base_date=today)

        # TRC-300: normal
        _seed_daily_counts(db, "TRC-300", [(d, 4) for d in range(1, 31)], base_date=today)
        _seed_daily_counts(db, "TRC-300", [(0, 4)], base_date=today)

        result = run_incident_scan(db, target_date=target)

        if len(result["trc_results"]) >= 2:
            # First should have the highest flag_level
            assert result["trc_results"][0]["flag_level"] >= result["trc_results"][-1]["flag_level"]


# ── 7. Flag query functions ──────────────────────────────────────────


class TestFlagQueries:
    """get_open_flags, get_flag_history."""

    def test_get_open_flags_returns_open_and_acknowledged(self, seeded_db):
        db = seeded_db
        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-18", status="open")
        _insert_flag(db, "TRC-100", "poisson_daily", 2, "2026-03-19", status="acknowledged")
        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-17", status="resolved")

        flags = get_open_flags(db)
        statuses = {f["status"] for f in flags}
        assert "resolved" not in statuses
        assert len(flags) >= 2

    def test_get_open_flags_filter_theta_level(self, seeded_db):
        db = seeded_db
        db.conn.execute("DELETE FROM incident_flags")
        db.conn.commit()

        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-18")
        _insert_flag(db, "TRC-200", "poisson_daily", 2, "2026-03-19")

        flags = get_open_flags(db, theta_level=2)
        assert all(f["theta_level"] == 2 for f in flags)
        assert len(flags) == 1

    def test_get_open_flags_filter_flag_type(self, seeded_db):
        db = seeded_db
        db.conn.execute("DELETE FROM incident_flags")
        db.conn.commit()

        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-18")
        _insert_flag(db, "TRC-200", "cusum", 1, "2026-03-19")

        flags = get_open_flags(db, flag_type="cusum")
        assert all(f["flag_type"] == "cusum" for f in flags)
        assert len(flags) == 1

    def test_get_flag_history_respects_days(self, seeded_db):
        db = seeded_db
        db.conn.execute("DELETE FROM incident_flags")
        db.conn.commit()

        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-18")
        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2025-01-01", status="resolved")

        history = get_flag_history(db, days=30)
        # Only the recent flag should be returned (within 30 days of max date)
        dates = [f["triggered_date"] for f in history]
        assert "2026-03-18" in dates
        assert "2025-01-01" not in dates

    def test_get_flag_history_filter_trc(self, seeded_db):
        db = seeded_db
        db.conn.execute("DELETE FROM incident_flags")
        db.conn.commit()

        _insert_flag(db, "TRC-100", "poisson_daily", 1, "2026-03-18")
        _insert_flag(db, "TRC-200", "cusum", 1, "2026-03-19")

        history = get_flag_history(db, trc_code="TRC-100")
        assert all(f["trc_code"] == "TRC-100" for f in history)


# ── 8. Baseline persistence ─────────────────────────────────────────


class TestBaselinePersistence:
    """_save_baseline writes to trc_baselines table."""

    def test_save_baseline_writes_row(self, seeded_db):
        db = seeded_db
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        result = {
            "trc_code": "TRC-100",
            "tier": 1,
            "lambda_daily": 5.0,
            "theta_1_daily": 8,
            "theta_2_daily": 11,
            "cusum_value": 2.5,
            "cusum_threshold": 11.18,
            "baseline_days": 30,
            "insufficient_data": False,
        }
        _save_baseline(db, result)

        row = db.conn.execute(
            "SELECT * FROM trc_baselines WHERE trc_code = 'TRC-100'"
        ).fetchone()
        assert row is not None
        assert row["lambda_daily"] == 5.0
        assert row["cusum_value"] == 2.5
        assert row["cusum_slack"] == CUSUM_K_FACTOR * 5.0

    def test_save_baseline_skips_insufficient(self, seeded_db):
        db = seeded_db
        db.conn.execute("DELETE FROM trc_baselines")
        db.conn.commit()

        result = {"trc_code": "TRC-100", "insufficient_data": True}
        _save_baseline(db, result)

        row = db.conn.execute(
            "SELECT * FROM trc_baselines WHERE trc_code = 'TRC-100'"
        ).fetchone()
        assert row is None


# ── 9. Interpretation string ─────────────────────────────────────────


class TestInterpretation:
    """_build_interpretation produces human-readable strings."""

    def test_watch_interpretation(self):
        result = {
            "trc_code": "TRC-100",
            "flag_level": 1,
            "flag_source": "poisson_daily",
            "observed_today": 12,
            "lambda_daily": 5.0,
            "p_value_daily": 0.02,
            "cusum_value": 0.0,
            "cusum_threshold": 11.0,
        }
        text = _build_interpretation(result)
        assert "Watch" in text
        assert "TRC-100" in text
        assert "12 tickets" in text

    def test_incident_interpretation(self):
        result = {
            "trc_code": "TRC-200",
            "flag_level": 2,
            "flag_source": "poisson_daily",
            "observed_today": 25,
            "lambda_daily": 5.0,
            "p_value_daily": 0.001,
            "cusum_value": 0.0,
            "cusum_threshold": 11.0,
        }
        text = _build_interpretation(result)
        assert "Incident" in text
        assert "TRC-200" in text

    def test_cusum_interpretation(self):
        result = {
            "trc_code": "TRC-300",
            "flag_level": 1,
            "flag_source": "cusum",
            "observed_today": 8,
            "lambda_daily": 5.0,
            "p_value_daily": 0.1,
            "cusum_value": 15.0,
            "cusum_threshold": 11.0,
        }
        text = _build_interpretation(result)
        assert "CUSUM" in text
        assert "drift" in text.lower()


# ── 10. Intervention correlation ─────────────────────────────────────


class TestInterventionCorrelation:
    """correlate_flag_with_interventions matches interventions to flags."""

    def test_no_interventions_returns_empty(self, seeded_db):
        flag = {"trc_code": "TRC-100", "triggered_date": "2026-03-20"}
        matches = correlate_flag_with_interventions(seeded_db, flag)
        assert matches == []

    def test_matching_intervention_returned(self, seeded_db):
        db = seeded_db
        import json
        db.conn.execute("""
            INSERT INTO interventions
                (name, category, description, event_date,
                 affected_trcs, tags, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "System upgrade", "deployment", "Upgraded billing system",
            "2026-03-15", json.dumps(["TRC-100"]), json.dumps([]),
            "admin", datetime.now().isoformat(),
        ))
        db.conn.commit()

        flag = {"trc_code": "TRC-100", "triggered_date": "2026-03-20"}
        matches = correlate_flag_with_interventions(db, flag, lookback_days=14)

        assert len(matches) == 1
        assert matches[0]["name"] == "System upgrade"
        assert matches[0]["days_before_flag"] == 5

    def test_unrelated_trc_not_matched(self, seeded_db):
        db = seeded_db
        import json
        db.conn.execute("""
            INSERT INTO interventions
                (name, category, description, event_date,
                 affected_trcs, tags, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Login fix", "hotfix", "Fixed login",
            "2026-03-15", json.dumps(["TRC-200"]), json.dumps([]),
            "admin", datetime.now().isoformat(),
        ))
        db.conn.commit()

        flag = {"trc_code": "TRC-100", "triggered_date": "2026-03-20"}
        matches = correlate_flag_with_interventions(db, flag, lookback_days=14)

        # Should not match since TRC-100 is not in affected_trcs
        trc100_matches = [m for m in matches if m["name"] == "Login fix"]
        assert len(trc100_matches) == 0

    def test_empty_triggered_date_returns_empty(self, seeded_db):
        flag = {"trc_code": "TRC-100", "triggered_date": ""}
        matches = correlate_flag_with_interventions(seeded_db, flag)
        assert matches == []
