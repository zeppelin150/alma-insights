"""
Alma Insights — Guru Effectiveness Tracker (Phase 4, T5)

Measures whether Guru article changes actually reduce ticket volume.
**Source-agnostic** — queries ``source_trc_daily`` which has data from
all configured sources (Zendesk, future Intercom/Jira, etc.).

Usage:
    tracker = GuruEffectivenessTracker(db_manager)
    tracker.record_baseline("card_123", "billing_friction")
    results = tracker.measure_effectiveness(days_since_change=14)
    report = tracker.get_effectiveness_report()
"""

import logging
import math
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("alma.guru_effectiveness")


class GuruEffectivenessTracker:
    """Measures whether Guru changes reduce ticket volume."""

    def __init__(self, db_manager):
        self.db = db_manager

    # ── Baseline Recording ──────────────────────────────────────

    def record_baseline(self, card_id: str, friction_type: str,
                        pre_window_days: int = 14):
        """Record pre-change volume baseline when a draft is pushed.

        Queries ``source_trc_daily`` (all sources) for avg daily count
        of tickets matching this friction type's TRC code.
        """
        # Get TRC code(s) for this friction type
        trc_codes = self._get_trc_for_friction(friction_type)
        if not trc_codes:
            logger.warning(
                "No TRC codes found for friction_type=%s", friction_type
            )
            return

        # Calculate avg daily volume over the pre-window
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=pre_window_days)

        pre_volume = self._avg_daily_volume(
            trc_codes, start_date, end_date
        )

        now = end_date.isoformat()

        self.db.conn.execute("""
            INSERT INTO guru_effectiveness
                (card_id, friction_type, measurement_date, source,
                 pre_volume, pre_window_days, created_at)
            VALUES (?, ?, ?, 'all', ?, ?, ?)
        """, (
            card_id, friction_type, now, pre_volume,
            pre_window_days, now,
        ))
        self.db.conn.commit()
        logger.info(
            "Recorded baseline for %s/%s: %.2f avg daily tickets",
            card_id, friction_type, pre_volume,
        )

    # ── Post-Change Measurement ─────────────────────────────────

    def measure_effectiveness(self,
                              days_since_change: int = 14) -> list[dict]:
        """Measure post-change volume for all tracked changes.

        Finds baselines that are at least ``days_since_change`` days
        old and haven't been measured yet (post_volume = 0).
        Computes delta_pct and runs Poisson significance test.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_since_change)
        ).isoformat()

        rows = self.db.conn.execute("""
            SELECT id, card_id, friction_type, measurement_date,
                   pre_volume, pre_window_days
            FROM guru_effectiveness
            WHERE post_volume = 0.0
              AND measurement_date <= ?
        """, (cutoff,)).fetchall()

        results = []
        for row in rows:
            eff_id = row[0]
            card_id = row[1]
            friction_type = row[2]
            change_date_str = row[3]
            pre_volume = row[4]
            pre_window = row[5]

            trc_codes = self._get_trc_for_friction(friction_type)
            if not trc_codes:
                continue

            # Measure post-change volume
            try:
                change_date = datetime.fromisoformat(change_date_str)
            except (ValueError, TypeError):
                continue

            post_start = change_date
            post_end = change_date + timedelta(days=days_since_change)

            post_volume = self._avg_daily_volume(
                trc_codes, post_start, post_end
            )

            # Compute delta
            if pre_volume > 0:
                delta_pct = round(
                    (post_volume - pre_volume) / pre_volume * 100, 2
                )
            else:
                delta_pct = 0.0

            # Significance test (Poisson)
            significant = _poisson_significance(
                pre_volume, post_volume, days_since_change
            )

            # Update record
            self.db.conn.execute("""
                UPDATE guru_effectiveness
                SET post_volume = ?, post_window_days = ?,
                    delta_pct = ?, is_significant = ?
                WHERE id = ?
            """, (
                post_volume, days_since_change,
                delta_pct, int(significant), eff_id,
            ))

            results.append({
                "id": eff_id,
                "card_id": card_id,
                "friction_type": friction_type,
                "pre_volume": pre_volume,
                "post_volume": post_volume,
                "delta_pct": delta_pct,
                "is_significant": significant,
                "days_measured": days_since_change,
            })

        self.db.conn.commit()
        return results

    # ── Report ──────────────────────────────────────────────────

    def get_effectiveness_report(self) -> list[dict]:
        """Summary of all effectiveness measurements.

        Returns: card_title, friction_type, pre/post volume, delta,
        significant.
        """
        rows = self.db.conn.execute("""
            SELECT
                ge.card_id,
                COALESCE(ga.title, ge.card_id) AS card_title,
                ge.friction_type,
                ge.pre_volume,
                ge.post_volume,
                ge.delta_pct,
                ge.is_significant,
                ge.measurement_date,
                ge.pre_window_days,
                ge.post_window_days
            FROM guru_effectiveness ge
            LEFT JOIN guru_articles ga ON ge.card_id = ga.card_id
            ORDER BY ge.measurement_date DESC
        """).fetchall()

        return [
            {
                "card_id": r[0],
                "card_title": r[1],
                "friction_type": r[2],
                "pre_volume": r[3],
                "post_volume": r[4],
                "delta_pct": r[5],
                "is_significant": bool(r[6]),
                "measurement_date": r[7],
                "pre_window_days": r[8],
                "post_window_days": r[9],
            }
            for r in rows
        ]

    # ── Internals ───────────────────────────────────────────────

    def _get_trc_for_friction(self, friction_type: str) -> list[str]:
        """Get TRC code(s) associated with a friction type."""
        rows = self.db.conn.execute(
            "SELECT DISTINCT trc FROM sub_patterns "
            "WHERE friction_type = ? AND merged_into IS NULL",
            (friction_type,),
        ).fetchall()
        return [r[0] for r in rows]

    def _avg_daily_volume(self, trc_codes: list[str],
                          start: datetime, end: datetime) -> float:
        """Average daily ticket volume from source_trc_daily.

        Source-agnostic: sums across all sources.
        """
        if not trc_codes:
            return 0.0

        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        placeholders = ",".join("?" for _ in trc_codes)
        params = trc_codes + [start_str, end_str]

        row = self.db.conn.execute(f"""
            SELECT COALESCE(SUM(count), 0) AS total,
                   COUNT(DISTINCT day_bucket) AS days
            FROM source_trc_daily
            WHERE trc_code IN ({placeholders})
              AND day_bucket >= ?
              AND day_bucket <= ?
        """, params).fetchone()

        total = row[0] if row else 0
        days = row[1] if row and row[1] > 0 else 1
        return round(total / days, 3)


def _poisson_significance(pre_avg: float, post_avg: float,
                          window_days: int,
                          alpha: float = 0.05) -> bool:
    """Poisson significance test for volume reduction.

    Tests whether the post-change volume is significantly different
    from the pre-change volume, assuming Poisson-distributed counts.

    Uses the Poisson CDF: if P(X <= observed | lambda=pre_avg*window)
    < alpha, the reduction is significant.
    """
    if pre_avg <= 0 or window_days <= 0:
        return False

    expected_total = pre_avg * window_days
    observed_total = post_avg * window_days

    if observed_total >= expected_total:
        return False  # No reduction

    # Poisson CDF: P(X <= k | lambda)
    p_value = _poisson_cdf(int(observed_total), expected_total)
    return p_value < alpha


def _poisson_cdf(k: int, lam: float) -> float:
    """Compute Poisson CDF P(X <= k | lambda).

    Pure-Python implementation to avoid hard scipy dependency
    in the Guru pipeline (scipy is available but not imported here
    for lightweight startup).
    """
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0

    # Sum P(X = i) for i in 0..k
    # P(X = i) = e^(-lam) * lam^i / i!
    # Use log to avoid overflow
    total = 0.0
    log_lam = math.log(lam)
    log_factorial = 0.0  # log(0!)

    for i in range(k + 1):
        if i > 0:
            log_factorial += math.log(i)
        log_prob = -lam + i * log_lam - log_factorial
        total += math.exp(log_prob)

    return min(1.0, total)
