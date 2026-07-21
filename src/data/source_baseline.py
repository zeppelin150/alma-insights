"""
Alma Insights — Source Rate Baseline (2026-05-07 redesign)

Computes a per-hour-of-day trailing-mean baseline for ticket arrival rates
from any data source (Zendesk, Intercom, Jira, …). Powers the rate-per-hour
chart that replaces the old "Live Feed" ticket card scroll on the Source
Monitor page.

Design intent
─────────────
The old TRC Spike detector compared a 60-minute recent window against a
60-minute prior window with a flat +25% threshold. Two problems:
  1. Diurnal load patterns are ignored — a 9 AM Monday spike isn't a spike
     if it happens every 9 AM.
  2. A flat percentage threshold is too aggressive on quiet TRCs (small
     swings look huge) and too lenient on busy TRCs (real swings get lost
     in the noise floor).

This module replaces both problems with an empirical per-hour-of-day
baseline: for each foreground bucket at hour h on day D, compare against
the same hour-of-day on the previous 7 days. Spike threshold is then
``baseline_mean + 2 * baseline_std`` — self-tuning to each TRC's noise
profile.

Data flow
─────────
::

    SourceWarehouse.update_rollups()
        └──> source_trc_hourly  (one row per source × trc × hour bucket)
                └──> compute_rate_baseline()  ← THIS MODULE
                        └──> RateBaseline (dataclass) ── consumed by
                              RateChartWidget on the Live Feed tab

We read directly from ``source_trc_hourly`` rather than recomputing from
``source_events`` because the rollup is the source of truth for hourly
counts and is already pruned to 7 days by ``rollup_maintenance()``.

Math reference
──────────────
For each foreground bucket B at hour-of-day h on date D:
  - baseline_samples = counts in source_trc_hourly for the same (source,
    trc_code, hour-of-day h) on dates {D-7, D-6, …, D-1}
  - baseline_mean    = mean(baseline_samples)
  - baseline_std     = stdev(baseline_samples) with Bessel correction
                       (n-1 denominator), floored at ``std_floor`` so
                       all-zero baselines don't produce zero-width bands
  - upper_band       = baseline_mean + spike_sigma * baseline_std
  - is_spike         = (rate[B] > upper_band[B])

Cold start
──────────
If the earliest hour_bucket on file for the (source, trc) is < 5 days old,
or if fewer than 5 distinct days contain any data for that pair, we set
``cold_start=True`` and use a deliberately permissive fallback band:
``upper_band = max(rate_so_far) * 1.5``. The UI surfaces a "Building
baseline (day N of 5)" badge in this state.

Settings keys
─────────────
- ``source_monitor.rate_chart.window_hours``  (24 | 48; default 24)
- ``source_monitor.rate_chart.baseline_days`` (fixed 7 per the
  2026-05-07 design decision; reserved for future tuning)
- ``source_monitor.rate_chart.default_view``  ("aggregate" | "per_trc")

Threading
─────────
Pure-CPU, pure-SQL function. The single SELECT pulls ≤ 192 rows
(8 days × 24 hours) so the call comfortably finishes in < 5 ms on a warm
SQLite connection. Safe to call from the Qt main thread; ``RateTab``
debounces calls to once per 2 seconds anyway.

Author: 2026-05-07 source-monitor redesign
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

logger = logging.getLogger("alma.source_baseline")

# ─── Module constants ──────────────────────────────────────────────

#: Default hours visible in the foreground (rate) window.
#: User can toggle to 48 in the UI.
DEFAULT_WINDOW_HOURS: int = 24

#: Trailing window length for baseline statistics.
#: Fixed at 7 per the 2026-05-07 design decision (matches
#: source_trc_hourly's 7-day prune horizon).
BASELINE_DAYS: int = 7

#: Sigma multiplier for the upper spike band.
#: 2σ ≈ 97.5th percentile under a Gaussian assumption; under Poisson
#: counts at the rates we typically see (5–50/hr), the empirical false
#: positive rate at 2σ is roughly 4–6%, which the user feedback loop
#: (Confirm/Dismiss on alerts) is well-suited to absorb.
DEFAULT_SPIKE_SIGMA: float = 2.0

#: Lower bound for baseline_std. Without a floor, an all-zero or
#: all-equal baseline produces a zero-width band and every nonzero
#: incoming bucket trips a spike. 1.0 ticket/hour is the smallest
#: meaningful unit.
DEFAULT_STD_FLOOR: float = 1.0

#: Minimum days of history required to skip the cold-start fallback.
COLD_START_DAYS: int = 5

#: Cold-start fallback multiplier on max-rate-so-far for the upper band.
#: Permissive on purpose — we'd rather miss a spike on day 2 than fire
#: spurious alerts before the baseline has settled.
COLD_START_BAND_MULT: float = 1.5


# ─── Public dataclass ──────────────────────────────────────────────

@dataclass(frozen=True)
class RateBaseline:
    """Result of a rate-baseline computation for one (source, trc) pair.

    Attributes are length-aligned arrays — ``len(rates) == len(hours) ==
    len(baseline_mean) == len(baseline_std) == len(upper_band) ==
    len(is_spike)`` is enforced by ``compute_rate_baseline``. The arrays
    are ordered oldest → newest along the foreground window.

    Attributes:
        hours: ISO 8601 hour-precision timestamps, e.g.
            ``"2026-05-07T14:00:00"``. One entry per foreground bucket.
        rates: Tickets observed in the corresponding bucket (count).
        baseline_mean: Mean of the same hour-of-day across the trailing
            ``BASELINE_DAYS`` days. Same length as ``rates``.
        baseline_std: Population stddev (Bessel-corrected, floored at
            ``DEFAULT_STD_FLOOR``). Same length as ``rates``.
        upper_band: ``baseline_mean[i] + spike_sigma * baseline_std[i]``.
        is_spike: ``rates[i] > upper_band[i]``. Same length as ``rates``.
        cold_start: True when fewer than ``COLD_START_DAYS`` days of
            history are available; ``upper_band`` then falls back to a
            permissive flat band — see module docstring.
        days_with_data: Distinct dates in the baseline window with at
            least one non-zero bucket for this (source, trc). Used by
            the UI to render "Day N of 5" badges.
        source: Source identifier (e.g. ``"zendesk"``).
        trc_code: TRC filter or ``None`` for the aggregate view.
        computed_at: UTC ISO timestamp of when this baseline was built.
        spike_sigma: Sigma multiplier actually used.
        window_hours: Foreground window length actually used.
    """

    hours: list[str]
    rates: list[int]
    baseline_mean: list[float]
    baseline_std: list[float]
    upper_band: list[float]
    is_spike: list[bool]
    cold_start: bool
    days_with_data: int
    source: str
    trc_code: str | None
    computed_at: str
    spike_sigma: float = DEFAULT_SPIKE_SIGMA
    window_hours: int = DEFAULT_WINDOW_HOURS

    def __post_init__(self) -> None:
        """Defensive length check.

        Catches programmer errors at construction time rather than
        causing an off-by-one render bug deep in QPainter code.
        """
        n = len(self.hours)
        for name in ("rates", "baseline_mean", "baseline_std",
                     "upper_band", "is_spike"):
            if len(getattr(self, name)) != n:
                raise ValueError(
                    f"RateBaseline length mismatch: hours has {n} but "
                    f"{name} has {len(getattr(self, name))}"
                )

    @property
    def is_empty(self) -> bool:
        """True when the foreground window has zero buckets.

        Used by ``RateChartWidget`` to draw an "awaiting data" state.
        """
        return len(self.hours) == 0

    @property
    def spike_count(self) -> int:
        """Number of foreground buckets currently flagged as a spike.

        Used by the live feed sidebar to show a small badge.
        """
        return sum(1 for s in self.is_spike if s)


# ─── Public API ────────────────────────────────────────────────────

def compute_rate_baseline(
    conn,
    source: str,
    trc_code: str | None = None,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    baseline_days: int = BASELINE_DAYS,
    spike_sigma: float = DEFAULT_SPIKE_SIGMA,
    std_floor: float = DEFAULT_STD_FLOOR,
    now: datetime | None = None,
) -> RateBaseline:
    """Compute a rate baseline for one (source, trc) pair.

    Args:
        conn: Live ``sqlite3.Connection`` (or db_manager-returned wrapper)
            with read access to ``source_trc_hourly``. Caller is
            responsible for connection lifecycle.
        source: Source identifier (e.g. ``"zendesk"``). Required —
            ``source_trc_hourly`` is keyed on this column.
        trc_code: TRC filter. ``None`` aggregates across all TRCs by
            summing per-bucket counts. Empty string is treated like
            ``None`` (defensive; old code occasionally passed ``""``).
        window_hours: Foreground window length in hours. Must be
            positive. The UI accepts 24 or 48; values outside that
            range are technically valid but not tested in CI.
        baseline_days: Trailing-window length in days for the per-hour
            -of-day mean/stddev calculation. Defaults to 7. Reserved
            for future tuning; the UI doesn't expose this.
        spike_sigma: Sigma multiplier for the upper band. Defaults to
            2.0 (≈ 97.5th percentile under Gaussian).
        std_floor: Minimum value for ``baseline_std`` after stddev is
            computed. Prevents zero-width bands when the baseline
            samples are constant or all-zero. Defaults to 1.0.
        now: Override the "current time" anchor. Tests pass a fixed
            ``datetime`` so the bucket math is deterministic. In
            production this defaults to ``datetime.now(timezone.utc)``.

    Returns:
        Populated ``RateBaseline`` dataclass. ``len(rates)`` is equal
        to ``window_hours`` (foreground buckets are dense — missing
        rows in ``source_trc_hourly`` are padded with count=0).

    Raises:
        ValueError: If ``window_hours <= 0`` or ``baseline_days <= 0``.

    Example:
        >>> conn = get_connection(db_path, readonly=True)
        >>> baseline = compute_rate_baseline(conn, "zendesk")
        >>> baseline.spike_count
        2
        >>> baseline.cold_start
        False
    """
    if window_hours <= 0:
        raise ValueError(f"window_hours must be > 0, got {window_hours}")
    if baseline_days <= 0:
        raise ValueError(f"baseline_days must be > 0, got {baseline_days}")

    # Normalize empty-string TRC to None for cleaner SQL branching below.
    trc_filter = trc_code if trc_code else None

    # Anchor "now" to the top of the current hour. Otherwise the
    # foreground window edges fall mid-bucket and the SQL range query
    # has to use sub-hour comparisons, which fights the bucket model.
    now = now or datetime.now(timezone.utc)
    now_floor = now.replace(minute=0, second=0, microsecond=0)

    # ── Build the foreground bucket timeline ────────────────────────
    # The foreground spans [now_floor - window_hours, now_floor],
    # exclusive of the right edge so we don't include a half-formed
    # current bucket. Buckets are stored oldest → newest.
    foreground_buckets = [
        now_floor - timedelta(hours=h)
        for h in range(window_hours, 0, -1)
    ]

    # ── Build the baseline bucket timeline ──────────────────────────
    # For each foreground bucket B at hour-of-day h on date D, the
    # baseline samples are at the same hour-of-day on dates
    # {D - baseline_days, …, D - 1}. We accumulate every distinct
    # (date, hour-of-day) the SQL query needs to fetch in one shot.
    baseline_buckets: list[datetime] = []
    for fg in foreground_buckets:
        for offset_days in range(1, baseline_days + 1):
            baseline_buckets.append(fg - timedelta(days=offset_days))

    # The earliest timestamp we need to query — drives the SQL filter.
    earliest = min(baseline_buckets) if baseline_buckets else now_floor

    # ── Fetch all relevant rows in a single SELECT ──────────────────
    # source_trc_hourly is keyed on (source, trc_code, hour_bucket).
    # When trc_code is None we SUM across all TRCs per hour_bucket.
    # Otherwise we filter by trc_code and treat each row as the count
    # for that bucket.
    bucket_counts: dict[str, int] = _fetch_bucket_counts(
        conn, source, trc_filter, earliest, now_floor
    )

    # ── Detect cold start ────────────────────────────────────────────
    days_with_data = _count_days_with_data(bucket_counts)
    cold_start = days_with_data < COLD_START_DAYS

    # ── Build the foreground rates array ────────────────────────────
    rates: list[int] = []
    hours: list[str] = []
    for fg in foreground_buckets:
        key = _bucket_key(fg)
        hours.append(key)
        rates.append(bucket_counts.get(key, 0))

    # ── Compute per-bucket baseline statistics ──────────────────────
    baseline_mean: list[float] = []
    baseline_std: list[float] = []
    upper_band: list[float] = []
    is_spike: list[bool] = []

    if cold_start:
        # Cold-start fallback: flat permissive band based on the highest
        # rate we've seen so far. This deliberately under-fires; the
        # 2σ math takes over once we cross COLD_START_DAYS.
        peak = max(rates) if rates else 0
        flat_upper = max(peak * COLD_START_BAND_MULT, std_floor)
        flat_mean = peak / 2 if peak else 0.0
        for r in rates:
            baseline_mean.append(flat_mean)
            baseline_std.append(0.0)  # unused in cold-start render
            upper_band.append(flat_upper)
            is_spike.append(r > flat_upper)
    else:
        # Normal path: per-bucket per-hour-of-day stats.
        for fg in foreground_buckets:
            samples = _gather_baseline_samples(
                fg, baseline_days, bucket_counts
            )
            mean_val, std_val = _mean_and_std(samples, std_floor)
            band = mean_val + spike_sigma * std_val
            baseline_mean.append(mean_val)
            baseline_std.append(std_val)
            upper_band.append(band)
            # Spike check uses the foreground rate for THIS bucket
            # (parallel index with `foreground_buckets`).
            idx = len(baseline_mean) - 1
            is_spike.append(rates[idx] > band)

    return RateBaseline(
        hours=hours,
        rates=rates,
        baseline_mean=baseline_mean,
        baseline_std=baseline_std,
        upper_band=upper_band,
        is_spike=is_spike,
        cold_start=cold_start,
        days_with_data=days_with_data,
        source=source,
        trc_code=trc_filter,
        computed_at=now.isoformat(timespec="seconds"),
        spike_sigma=spike_sigma,
        window_hours=window_hours,
    )


def list_active_trcs(
    conn,
    source: str,
    *,
    lookback_hours: int = 48,
    now: datetime | None = None,
) -> list[str]:
    """Return TRC codes seen in the last ``lookback_hours`` for ``source``.

    Used to populate the per-TRC dropdown on the rate-chart tab.
    Sorted descending by ticket volume so busy TRCs show up first.

    Args:
        conn: Live SQLite connection.
        source: Source identifier.
        lookback_hours: How far back to look. 48 picks up TRCs that
            appeared yesterday but not today, which the user likely
            wants to filter by.
        now: Anchor time for the lookback window. ``None`` (the production
            default) uses SQLite's ``datetime('now')`` (UTC) and leaves the
            production query byte-identical. Tests pass a fixed ``now`` — the
            same ``rate_baseline_now`` the rest of this module accepts — so the
            window is deterministic against seeded data instead of the wall
            clock (otherwise the query silently drifts out of range as the
            fixed test anchor ages).

    Returns:
        TRC codes ordered most → least active. Empty list on error
        (logged at debug). Empty TRCs are filtered out.
    """
    try:
        if now is None:
            cur = conn.execute(
                """
                SELECT trc_code, SUM(count) AS total
                FROM source_trc_hourly
                WHERE source = ?
                  AND hour_bucket >= datetime('now', '-' || ? || ' hours')
                  AND trc_code != ''
                GROUP BY trc_code
                ORDER BY total DESC
                """,
                (source, lookback_hours),
            )
        else:
            # hour_bucket is stored as "%Y-%m-%dT%H:00:00"; format the cutoff to
            # match so the string comparison is well-defined.
            cutoff = (now - timedelta(hours=lookback_hours)).strftime(
                "%Y-%m-%dT%H:%M:%S")
            cur = conn.execute(
                """
                SELECT trc_code, SUM(count) AS total
                FROM source_trc_hourly
                WHERE source = ?
                  AND hour_bucket >= ?
                  AND trc_code != ''
                GROUP BY trc_code
                ORDER BY total DESC
                """,
                (source, cutoff),
            )
        return [row[0] for row in cur.fetchall()]
    except Exception as exc:  # pragma: no cover - debug-logged fallback
        logger.debug("list_active_trcs failed: %s", exc)
        return []


# ─── Internal helpers ──────────────────────────────────────────────

def _bucket_key(dt: datetime) -> str:
    """Format a UTC datetime as an hour-precision ISO 8601 string.

    Matches the format ``SourceWarehouse.update_rollups()`` writes to
    ``source_trc_hourly.hour_bucket``: ``"YYYY-MM-DDTHH:00:00"``.
    """
    # Strip minutes/seconds defensively in case caller passes a
    # mid-hour datetime — every key must round-trip identically.
    return dt.strftime("%Y-%m-%dT%H:00:00")


def _fetch_bucket_counts(
    conn,
    source: str,
    trc_code: str | None,
    earliest: datetime,
    latest: datetime,
) -> dict[str, int]:
    """Query ``source_trc_hourly`` and return ``{hour_bucket: count}``.

    Aggregates across TRCs when ``trc_code`` is ``None`` (sums per-hour).

    Args:
        conn: Live SQLite connection.
        source: Source identifier.
        trc_code: TRC filter or None for aggregate.
        earliest: Lower bound on hour_bucket (inclusive).
        latest: Upper bound on hour_bucket (inclusive).

    Returns:
        Dict mapping ``"YYYY-MM-DDTHH:00:00"`` → count. Missing buckets
        are absent (caller pads with 0).
    """
    earliest_key = _bucket_key(earliest)
    latest_key = _bucket_key(latest)

    try:
        if trc_code is None:
            # Aggregate view: SUM across all TRCs per hour_bucket.
            cur = conn.execute(
                """
                SELECT hour_bucket, SUM(count) AS total
                FROM source_trc_hourly
                WHERE source = ?
                  AND hour_bucket >= ?
                  AND hour_bucket <= ?
                GROUP BY hour_bucket
                """,
                (source, earliest_key, latest_key),
            )
        else:
            # Per-TRC view: one row per bucket already.
            cur = conn.execute(
                """
                SELECT hour_bucket, count
                FROM source_trc_hourly
                WHERE source = ?
                  AND trc_code = ?
                  AND hour_bucket >= ?
                  AND hour_bucket <= ?
                """,
                (source, trc_code, earliest_key, latest_key),
            )
        return {row[0]: int(row[1] or 0) for row in cur.fetchall()}
    except Exception as exc:
        # If the table doesn't exist (fresh DB before migration 002
        # has run) we treat that as "no data" rather than propagating —
        # the rate chart should still render an empty state.
        logger.debug("_fetch_bucket_counts failed: %s", exc)
        return {}


def _count_days_with_data(bucket_counts: dict[str, int]) -> int:
    """Count distinct dates in ``bucket_counts`` with a non-zero count.

    Used as the cold-start gating signal. A day with zero tickets does
    not count toward "we have history" because it's indistinguishable
    from a day where the warehouse hadn't yet started recording.
    """
    days: set[str] = set()
    for key, count in bucket_counts.items():
        if count > 0:
            # key is "YYYY-MM-DDTHH:00:00" — first 10 chars is the date.
            days.add(key[:10])
    return len(days)


def _gather_baseline_samples(
    fg_bucket: datetime,
    baseline_days: int,
    bucket_counts: dict[str, int],
) -> list[int]:
    """Pull the ``baseline_days`` baseline samples for one foreground bucket.

    For a foreground bucket at hour h on date D, the samples are
    counts at hour h on dates {D-baseline_days, …, D-1}.

    Missing buckets are returned as 0 (the same hour-of-day on a quiet
    day legitimately has zero tickets).

    Args:
        fg_bucket: The foreground datetime to gather a baseline for.
        baseline_days: Number of prior days to sample.
        bucket_counts: Pre-fetched ``{key: count}`` lookup.

    Returns:
        List of ``baseline_days`` integer counts, ordered oldest to newest.
    """
    samples: list[int] = []
    for offset_days in range(baseline_days, 0, -1):
        prior = fg_bucket - timedelta(days=offset_days)
        samples.append(bucket_counts.get(_bucket_key(prior), 0))
    return samples


def _mean_and_std(
    samples: Sequence[int],
    std_floor: float,
) -> tuple[float, float]:
    """Compute mean and Bessel-corrected stddev with floor.

    Uses ``statistics.pstdev`` rather than ``stdev`` when n < 2 to avoid
    a ``StatisticsError`` on degenerate baselines. The floor prevents
    zero-width bands when samples are all-equal.

    Args:
        samples: Numeric samples. Must be non-empty (caller's
            responsibility to skip empty cases).
        std_floor: Minimum return value for the stddev component.

    Returns:
        ``(mean, std)`` tuple. ``std >= std_floor``. Both are 0.0 if
        samples is empty (defensive — should not happen in practice).
    """
    if not samples:
        return 0.0, std_floor

    mean_val = statistics.fmean(samples)

    # statistics.stdev requires n >= 2. For n=1 we have no spread
    # information — return the floor.
    if len(samples) < 2:
        return mean_val, std_floor

    try:
        # Bessel correction (n-1) — we treat the baseline samples as a
        # sample of the underlying arrival process, not the population.
        std_val = statistics.stdev(samples)
    except statistics.StatisticsError:
        # Should be unreachable given the n<2 guard above, but guard
        # against future code paths that pass odd inputs.
        std_val = 0.0

    # NaN guard: pstdev/stdev of float values can return NaN if any
    # sample is NaN (we never construct NaN here, but float coercion
    # paranoia is cheap).
    if math.isnan(std_val):
        std_val = 0.0

    return mean_val, max(std_val, std_floor)
