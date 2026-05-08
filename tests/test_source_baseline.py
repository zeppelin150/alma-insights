"""
Tests for src/data/source_baseline.py — rate-per-hour baseline algorithm.

Coverage map
────────────
  - RateBaseline dataclass: length invariant, is_empty, spike_count
  - compute_rate_baseline:
      * happy path on seeded_rate_db (synthetic spike is detected)
      * cold-start fallback (< 5 days of data → permissive band)
      * empty database (no source_trc_hourly rows)
      * aggregate vs per-TRC (trc_code=None sums across TRCs)
      * window_hours validation (must be > 0)
      * baseline_days validation (must be > 0)
      * deterministic ordering (oldest → newest)
      * std_floor protects against zero-width bands
      * bucket key alignment with SourceWarehouse.update_rollups()
      * sparse TRC (some baseline samples are 0)
  - list_active_trcs: ordering by volume, lookback filter
  - _bucket_key: hour-precision rounding
  - _gather_baseline_samples: 7 prior-day same-hour values
  - _mean_and_std: Bessel correction, NaN guard, single-sample fallback

The seeded_rate_db fixture in conftest.py provides 8 days × 24h × 2 TRCs
of baseline data plus a synthetic spike at (rate_baseline_now - 1h) for
TRC-100. Most tests anchor "now" to ``rate_baseline_now`` for
determinism.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.data.source_baseline import (
    BASELINE_DAYS,
    COLD_START_BAND_MULT,
    COLD_START_DAYS,
    DEFAULT_SPIKE_SIGMA,
    DEFAULT_STD_FLOOR,
    DEFAULT_WINDOW_HOURS,
    RateBaseline,
    _bucket_key,
    _count_days_with_data,
    _fetch_bucket_counts,
    _gather_baseline_samples,
    _mean_and_std,
    compute_rate_baseline,
    list_active_trcs,
)


# ═══════════════════════════════════════════════════════════════════
#  RateBaseline dataclass invariants
# ═══════════════════════════════════════════════════════════════════

class TestRateBaselineDataclass:
    """Verify the dataclass length invariants and convenience properties."""

    def _make(self, n: int = 3, spikes: int = 0) -> RateBaseline:
        """Construct a RateBaseline with ``n`` aligned arrays."""
        spike_flags = [True] * spikes + [False] * (n - spikes)
        return RateBaseline(
            hours=[f"2026-05-07T{h:02d}:00:00" for h in range(n)],
            rates=[1] * n,
            baseline_mean=[2.0] * n,
            baseline_std=[1.0] * n,
            upper_band=[4.0] * n,
            is_spike=spike_flags,
            cold_start=False,
            days_with_data=7,
            source="zendesk",
            trc_code=None,
            computed_at="2026-05-07T14:00:00",
        )

    def test_length_invariant_passes_when_aligned(self):
        """Construction succeeds when all arrays are the same length."""
        bl = self._make(n=5)
        assert len(bl.rates) == 5

    def test_length_invariant_fails_when_misaligned(self):
        """ValueError raised on length mismatch — defensive guard."""
        with pytest.raises(ValueError, match="length mismatch"):
            RateBaseline(
                hours=["a", "b"],
                rates=[1],  # one short
                baseline_mean=[2.0, 2.0],
                baseline_std=[1.0, 1.0],
                upper_band=[4.0, 4.0],
                is_spike=[False, False],
                cold_start=False,
                days_with_data=7,
                source="zendesk",
                trc_code=None,
                computed_at="2026-05-07T14:00:00",
            )

    def test_is_empty_true_for_zero_length(self):
        bl = self._make(n=0)
        assert bl.is_empty is True
        assert bl.spike_count == 0

    def test_is_empty_false_for_populated(self):
        bl = self._make(n=3)
        assert bl.is_empty is False

    def test_spike_count_matches_is_spike_flags(self):
        bl = self._make(n=10, spikes=3)
        assert bl.spike_count == 3


# ═══════════════════════════════════════════════════════════════════
#  Bucket key formatting
# ═══════════════════════════════════════════════════════════════════

class TestBucketKey:
    """_bucket_key must match the format SourceWarehouse writes."""

    def test_format_is_iso_hour_precision(self):
        dt = datetime(2026, 5, 7, 14, 0, 0, tzinfo=timezone.utc)
        assert _bucket_key(dt) == "2026-05-07T14:00:00"

    def test_strips_minutes_and_seconds_defensively(self):
        """Even if caller passes a mid-hour datetime we round to top-of-hour."""
        dt = datetime(2026, 5, 7, 14, 37, 22, tzinfo=timezone.utc)
        assert _bucket_key(dt) == "2026-05-07T14:00:00"

    def test_naive_datetime_works(self):
        """No timezone metadata is encoded into the key."""
        dt = datetime(2026, 5, 7, 14, 0, 0)
        assert _bucket_key(dt) == "2026-05-07T14:00:00"


# ═══════════════════════════════════════════════════════════════════
#  Mean / stddev helper
# ═══════════════════════════════════════════════════════════════════

class TestMeanAndStd:
    """_mean_and_std handles the degenerate cases the algorithm cares about."""

    def test_normal_seven_samples(self):
        """Seven equal-spaced samples → known mean/stddev."""
        # Variance of [1,2,3,4,5,6,7] with Bessel = 4.667 → std = 2.16
        mean, std = _mean_and_std([1, 2, 3, 4, 5, 6, 7], std_floor=0.0)
        assert mean == pytest.approx(4.0)
        assert std == pytest.approx(2.160, abs=0.01)

    def test_single_sample_returns_floor_for_std(self):
        """n=1 has no spread, so std falls back to the floor."""
        mean, std = _mean_and_std([5], std_floor=1.5)
        assert mean == 5.0
        assert std == 1.5

    def test_empty_returns_zero_with_floor(self):
        """Defensive: caller shouldn't pass empty, but if they do, floor is used."""
        mean, std = _mean_and_std([], std_floor=2.0)
        assert mean == 0.0
        assert std == 2.0

    def test_all_zero_returns_floor(self):
        """All-zero baseline must not produce a zero-width band."""
        mean, std = _mean_and_std([0, 0, 0, 0, 0, 0, 0], std_floor=1.0)
        assert mean == 0.0
        assert std == 1.0

    def test_constant_samples_return_floor(self):
        """All-equal samples → stdev = 0, must be floored."""
        mean, std = _mean_and_std([5, 5, 5, 5, 5], std_floor=1.0)
        assert mean == 5.0
        assert std == 1.0  # not 0!

    def test_floor_does_not_clamp_real_spread(self):
        """When real stddev > floor, real value wins."""
        mean, std = _mean_and_std([0, 0, 0, 0, 100], std_floor=1.0)
        assert std > 1.0


# ═══════════════════════════════════════════════════════════════════
#  Baseline-sample gathering
# ═══════════════════════════════════════════════════════════════════

class TestGatherBaselineSamples:
    """_gather_baseline_samples pulls 7 prior-day same-hour counts."""

    def test_seven_prior_days(self):
        """For a foreground bucket at hour h, returns h on each of D-1..D-7."""
        fg = datetime(2026, 5, 7, 14, 0, 0, tzinfo=timezone.utc)
        bucket_counts = {
            _bucket_key(fg - timedelta(days=d)): d * 10
            for d in range(1, 8)
        }
        samples = _gather_baseline_samples(fg, 7, bucket_counts)
        # Ordered oldest → newest: day 7 ago first, day 1 ago last.
        assert samples == [70, 60, 50, 40, 30, 20, 10]

    def test_missing_buckets_default_to_zero(self):
        """A quiet day with no rows → count of 0 in the sample list."""
        fg = datetime(2026, 5, 7, 14, 0, 0, tzinfo=timezone.utc)
        # Only seed day-3 ago.
        bucket_counts = {
            _bucket_key(fg - timedelta(days=3)): 99,
        }
        samples = _gather_baseline_samples(fg, 7, bucket_counts)
        # Expect 7 entries, exactly one of them 99, rest zeros.
        assert len(samples) == 7
        assert samples.count(99) == 1
        assert samples.count(0) == 6

    def test_baseline_days_parameter_respected(self):
        """baseline_days=3 → only 3 samples returned."""
        fg = datetime(2026, 5, 7, 14, 0, 0, tzinfo=timezone.utc)
        samples = _gather_baseline_samples(fg, 3, {})
        assert len(samples) == 3


# ═══════════════════════════════════════════════════════════════════
#  Day-with-data counter
# ═══════════════════════════════════════════════════════════════════

class TestCountDaysWithData:
    """_count_days_with_data drives the cold-start signal."""

    def test_zero_days_for_empty_dict(self):
        assert _count_days_with_data({}) == 0

    def test_zero_days_when_all_counts_zero(self):
        """A day with only zero-count buckets doesn't count as data."""
        counts = {
            "2026-05-01T14:00:00": 0,
            "2026-05-02T14:00:00": 0,
        }
        assert _count_days_with_data(counts) == 0

    def test_three_distinct_dates(self):
        counts = {
            "2026-05-01T14:00:00": 5,
            "2026-05-01T15:00:00": 3,  # same date as above
            "2026-05-02T14:00:00": 2,
            "2026-05-03T09:00:00": 1,
        }
        assert _count_days_with_data(counts) == 3


# ═══════════════════════════════════════════════════════════════════
#  compute_rate_baseline — happy path & spike detection
# ═══════════════════════════════════════════════════════════════════

class TestComputeRateBaselineHappyPath:
    """Full algorithm against seeded_rate_db (8 days of data with one spike)."""

    def test_per_trc_detects_seeded_spike(self, seeded_rate_conn,
                                           rate_baseline_now):
        """TRC-100 has a 30-ticket bucket at (now-1h) — must be flagged."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            trc_code="TRC-100",
            now=rate_baseline_now,
        )
        assert bl.cold_start is False
        assert bl.spike_count >= 1
        # The spike sits at the second-to-last bucket (now-1h, since we
        # walk from now-window_hours back to now-1).
        assert bl.is_spike[-1] is True
        assert bl.rates[-1] == 30

    def test_per_trc_quiet_has_no_spike(self, seeded_rate_conn,
                                        rate_baseline_now):
        """TRC-200 has no spike — every bucket should be normal."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            trc_code="TRC-200",
            now=rate_baseline_now,
        )
        assert bl.cold_start is False
        assert bl.spike_count == 0
        assert all(r == 5 for r in bl.rates)

    def test_aggregate_view_sums_across_trcs(self, seeded_rate_conn,
                                              rate_baseline_now):
        """trc_code=None sums TRC-100 + TRC-200 per bucket."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            trc_code=None,
            now=rate_baseline_now,
        )
        # Most buckets: 5 + 5 = 10 tickets.
        # Spike bucket: 30 + 5 = 35 tickets.
        assert max(bl.rates) == 35
        assert bl.rates.count(10) >= 20  # most of the window

    def test_window_hours_24_default(self, seeded_rate_conn,
                                      rate_baseline_now):
        """Default window covers exactly 24 buckets."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            trc_code="TRC-100",
            now=rate_baseline_now,
        )
        assert len(bl.rates) == 24
        assert bl.window_hours == 24

    def test_window_hours_48_toggle(self, seeded_rate_conn,
                                     rate_baseline_now):
        """User can toggle to 48 — function must produce 48 buckets."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            trc_code="TRC-100",
            window_hours=48,
            now=rate_baseline_now,
        )
        assert len(bl.rates) == 48
        assert bl.window_hours == 48

    def test_arrays_are_length_aligned(self, seeded_rate_conn,
                                        rate_baseline_now):
        """Defensive — every output array must have len = window_hours."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            now=rate_baseline_now,
        )
        n = len(bl.hours)
        assert len(bl.rates) == n
        assert len(bl.baseline_mean) == n
        assert len(bl.baseline_std) == n
        assert len(bl.upper_band) == n
        assert len(bl.is_spike) == n

    def test_hours_ordered_oldest_to_newest(self, seeded_rate_conn,
                                             rate_baseline_now):
        """ISO timestamps must be strictly increasing."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            now=rate_baseline_now,
        )
        for i in range(1, len(bl.hours)):
            assert bl.hours[i] > bl.hours[i - 1]

    def test_baseline_mean_around_5_for_quiet_trc(self, seeded_rate_conn,
                                                   rate_baseline_now):
        """TRC-200 has 5/hour for 7 days → baseline mean ≈ 5."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            trc_code="TRC-200",
            now=rate_baseline_now,
        )
        # Allow tiny drift from sample noise; should be within 0.1 of 5.
        assert all(abs(m - 5.0) < 0.1 for m in bl.baseline_mean)

    def test_upper_band_above_baseline_mean(self, seeded_rate_conn,
                                             rate_baseline_now):
        """upper_band[i] = mean + 2σ — must always be ≥ mean."""
        bl = compute_rate_baseline(
            seeded_rate_conn,
            "zendesk",
            now=rate_baseline_now,
        )
        for mean, upper in zip(bl.baseline_mean, bl.upper_band):
            assert upper >= mean


# ═══════════════════════════════════════════════════════════════════
#  compute_rate_baseline — cold start fallback
# ═══════════════════════════════════════════════════════════════════

class TestComputeRateBaselineColdStart:
    """When < 5 days of history exist, permissive flat band kicks in."""

    def test_empty_db_is_cold_start(self, empty_db, rate_baseline_now):
        """No rows in source_trc_hourly → cold_start=True."""
        bl = compute_rate_baseline(
            empty_db.conn,
            "zendesk",
            now=rate_baseline_now,
        )
        assert bl.cold_start is True
        assert bl.days_with_data == 0
        assert all(r == 0 for r in bl.rates)
        # No spikes when there's no data — flat band still applies.
        assert bl.spike_count == 0

    def test_short_history_is_cold_start(self, empty_db,
                                          rate_baseline_now):
        """Only 47 hours of data (touches 3 calendar dates with partial
        coverage) → cold_start=True, permissive band.

        47 hours back from 14:00 on 2026-05-06 spans 5/4 15:00 → 5/6 13:00,
        which is 3 distinct calendar dates with partial coverage. That's
        below the 5-day cold-start threshold.
        """
        cur = empty_db.conn
        for h in range(1, 48):
            bucket = (rate_baseline_now - timedelta(hours=h)).strftime(
                "%Y-%m-%dT%H:00:00"
            )
            cur.execute(
                """INSERT INTO source_trc_hourly
                   (source, trc_code, hour_bucket, count)
                   VALUES (?, ?, ?, ?)""",
                ("zendesk", "TRC-100", bucket, 5),
            )
        empty_db.conn.commit()

        bl = compute_rate_baseline(
            empty_db.conn,
            "zendesk",
            trc_code="TRC-100",
            now=rate_baseline_now,
        )
        assert bl.cold_start is True
        assert bl.days_with_data == 3
        # Flat upper band uses peak * mult (or floor).
        peak = max(bl.rates)
        expected_upper = max(peak * COLD_START_BAND_MULT, DEFAULT_STD_FLOOR)
        assert all(u == pytest.approx(expected_upper) for u in bl.upper_band)

    def test_cold_start_threshold_at_five_days(self, empty_db,
                                                 rate_baseline_now):
        """5 days exactly → NOT cold start; 4 days → cold start."""
        cur = empty_db.conn
        # Seed 5 distinct days with one bucket each.
        for d in range(1, 6):
            bucket = (rate_baseline_now - timedelta(days=d)).strftime(
                "%Y-%m-%dT%H:00:00"
            )
            cur.execute(
                """INSERT INTO source_trc_hourly
                   (source, trc_code, hour_bucket, count)
                   VALUES (?, ?, ?, ?)""",
                ("zendesk", "TRC-100", bucket, 5),
            )
        empty_db.conn.commit()

        bl = compute_rate_baseline(
            empty_db.conn,
            "zendesk",
            trc_code="TRC-100",
            now=rate_baseline_now,
        )
        assert bl.days_with_data == 5
        assert bl.cold_start is False  # at the boundary, not cold

        # Now drop one day → should flip to cold.
        cur.execute(
            """DELETE FROM source_trc_hourly
               WHERE hour_bucket LIKE ?""",
            ((rate_baseline_now - timedelta(days=1)).strftime(
                "%Y-%m-%d") + "%",),
        )
        empty_db.conn.commit()

        bl2 = compute_rate_baseline(
            empty_db.conn,
            "zendesk",
            trc_code="TRC-100",
            now=rate_baseline_now,
        )
        assert bl2.days_with_data == 4
        assert bl2.cold_start is True


# ═══════════════════════════════════════════════════════════════════
#  compute_rate_baseline — input validation
# ═══════════════════════════════════════════════════════════════════

class TestComputeRateBaselineValidation:
    """Boundary checks on numeric arguments."""

    def test_zero_window_raises(self, empty_db, rate_baseline_now):
        with pytest.raises(ValueError, match="window_hours"):
            compute_rate_baseline(
                empty_db.conn, "zendesk",
                window_hours=0, now=rate_baseline_now,
            )

    def test_negative_window_raises(self, empty_db, rate_baseline_now):
        with pytest.raises(ValueError, match="window_hours"):
            compute_rate_baseline(
                empty_db.conn, "zendesk",
                window_hours=-1, now=rate_baseline_now,
            )

    def test_zero_baseline_days_raises(self, empty_db, rate_baseline_now):
        with pytest.raises(ValueError, match="baseline_days"):
            compute_rate_baseline(
                empty_db.conn, "zendesk",
                baseline_days=0, now=rate_baseline_now,
            )

    def test_empty_string_trc_treated_as_aggregate(self, seeded_rate_conn,
                                                    rate_baseline_now):
        """Defensive normalization — old code occasionally passed ''."""
        bl_empty = compute_rate_baseline(
            seeded_rate_conn, "zendesk",
            trc_code="", now=rate_baseline_now,
        )
        bl_none = compute_rate_baseline(
            seeded_rate_conn, "zendesk",
            trc_code=None, now=rate_baseline_now,
        )
        # Same rates because both go through aggregate path.
        assert bl_empty.rates == bl_none.rates
        assert bl_empty.trc_code is None
        assert bl_none.trc_code is None


# ═══════════════════════════════════════════════════════════════════
#  compute_rate_baseline — bucket alignment with warehouse
# ═══════════════════════════════════════════════════════════════════

class TestBucketAlignment:
    """Verify keys produced by the algorithm match SourceWarehouse output."""

    def test_warehouse_writes_match_baseline_reads(self, empty_db,
                                                    rate_baseline_now):
        """SourceWarehouse.update_rollups() writes hour_bucket in
        ``YYYY-MM-DDTHH:00:00`` format. _bucket_key must produce the
        identical string so reads find writes."""
        from src.data.source_warehouse import SourceWarehouse

        warehouse = SourceWarehouse(empty_db)
        # Use a deterministic ISO timestamp.
        ts = (rate_baseline_now - timedelta(hours=1)).isoformat()
        warehouse.update_rollups("zendesk", "TRC-100", ts)

        bl = compute_rate_baseline(
            empty_db.conn,
            "zendesk",
            trc_code="TRC-100",
            now=rate_baseline_now,
        )
        # That row should appear in the foreground window with count=1.
        # Cold start will be True (only 1 day of data) but rate must be 1.
        non_zero = [r for r in bl.rates if r > 0]
        assert non_zero == [1]


# ═══════════════════════════════════════════════════════════════════
#  list_active_trcs
# ═══════════════════════════════════════════════════════════════════

class TestListActiveTrcs:
    """The TRC dropdown source."""

    def test_returns_active_trcs_ordered_by_volume(self, seeded_rate_conn):
        """TRC-100 has the spike (30 extra tickets) → should rank first."""
        trcs = list_active_trcs(seeded_rate_conn, "zendesk")
        assert "TRC-100" in trcs
        assert "TRC-200" in trcs
        assert trcs[0] == "TRC-100"  # higher total volume

    def test_empty_db_returns_empty_list(self, empty_db):
        """No rows → empty list, not an exception."""
        trcs = list_active_trcs(empty_db.conn, "zendesk")
        assert trcs == []

    def test_filters_empty_trc_codes(self, empty_db, rate_baseline_now):
        """Empty-string TRCs should not show up in the dropdown."""
        cur = empty_db.conn
        bucket = (rate_baseline_now - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:00:00"
        )
        cur.execute(
            """INSERT INTO source_trc_hourly
               (source, trc_code, hour_bucket, count)
               VALUES (?, ?, ?, ?)""",
            ("zendesk", "", bucket, 5),
        )
        cur.execute(
            """INSERT INTO source_trc_hourly
               (source, trc_code, hour_bucket, count)
               VALUES (?, ?, ?, ?)""",
            ("zendesk", "TRC-X", bucket, 3),
        )
        empty_db.conn.commit()

        trcs = list_active_trcs(empty_db.conn, "zendesk")
        assert "" not in trcs
        assert "TRC-X" in trcs


# ═══════════════════════════════════════════════════════════════════
#  Sparse / edge-case data
# ═══════════════════════════════════════════════════════════════════

class TestSparseData:
    """Behaviour when the baseline window has gaps."""

    def test_sparse_baseline_does_not_crash(self, empty_db,
                                             rate_baseline_now):
        """A TRC with one bucket per day → algorithm still produces output."""
        cur = empty_db.conn
        # Seed 7 days — one bucket per day, all at hour 14.
        for d in range(1, 8):
            bucket = (
                rate_baseline_now - timedelta(days=d)
            ).replace(minute=0, second=0).strftime("%Y-%m-%dT14:00:00")
            cur.execute(
                """INSERT INTO source_trc_hourly
                   (source, trc_code, hour_bucket, count)
                   VALUES (?, ?, ?, ?)""",
                ("zendesk", "TRC-S", bucket, 4),
            )
        empty_db.conn.commit()

        bl = compute_rate_baseline(
            empty_db.conn,
            "zendesk",
            trc_code="TRC-S",
            now=rate_baseline_now,
        )
        # 24 buckets in the foreground; 23 of them have zero baseline
        # samples (no rows at those hours-of-day) and 1 has 7 samples
        # of 4 each.
        assert len(bl.rates) == 24
        # No NaN should leak through.
        assert all(not math.isnan(m) for m in bl.baseline_mean)
        assert all(not math.isnan(s) for s in bl.baseline_std)


# ═══════════════════════════════════════════════════════════════════
#  _fetch_bucket_counts internal
# ═══════════════════════════════════════════════════════════════════

class TestFetchBucketCounts:
    """Direct unit tests for the SQL path."""

    def test_returns_empty_dict_when_no_rows(self, empty_db,
                                              rate_baseline_now):
        out = _fetch_bucket_counts(
            empty_db.conn,
            "zendesk",
            trc_code=None,
            earliest=rate_baseline_now - timedelta(days=1),
            latest=rate_baseline_now,
        )
        assert out == {}

    def test_aggregate_sums_across_trcs(self, empty_db, rate_baseline_now):
        """trc_code=None → SUM(count) per hour bucket."""
        cur = empty_db.conn
        bucket = (rate_baseline_now - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:00:00"
        )
        cur.execute(
            """INSERT INTO source_trc_hourly
               (source, trc_code, hour_bucket, count)
               VALUES ('zendesk', 'A', ?, 3)""",
            (bucket,),
        )
        cur.execute(
            """INSERT INTO source_trc_hourly
               (source, trc_code, hour_bucket, count)
               VALUES ('zendesk', 'B', ?, 7)""",
            (bucket,),
        )
        empty_db.conn.commit()

        out = _fetch_bucket_counts(
            empty_db.conn,
            "zendesk",
            trc_code=None,
            earliest=rate_baseline_now - timedelta(hours=2),
            latest=rate_baseline_now,
        )
        assert out[bucket] == 10  # 3 + 7
