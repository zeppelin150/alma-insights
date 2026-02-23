"""
Alma Insights — Incident Engine

Two-tier statistical process control for TRC ticket rates.

Tier 1 (all TRCs): Daily Poisson monitoring
  - Model: ticket count per day ~ Poisson(λ)
  - λ = rolling 30-day mean of daily ticket count
  - 1θ flag = daily count exceeds Poisson 90th percentile
  - 2θ flag = daily count exceeds Poisson 97.5th percentile
  - p-value = P(X ≥ observed | λ) via Poisson survival function

Tier 2 (TRCs with daily avg ≥ 20): Hour-of-day Poisson monitoring
  - Model: ticket count per hour-slot ~ Poisson(λ_h)
  - 24 separate λ_h values per TRC (one per hour of day)
  - Same percentile thresholds applied per hour-slot

CUSUM (all TRCs): Cumulative sum drift detection
  - Detects sustained elevation that single-day checks miss
  - S_n = max(0, S_{n-1} + (x_n - λ - k))
  - k = allowance (slack) = 0.5 * λ
  - h = decision threshold = 5.0 * sqrt(λ)
  - Alert fires when S_n > h

Dependencies: scipy.stats.poisson, numpy
"""

import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from scipy.stats import poisson


# ── Configuration ──
BASELINE_WINDOW_DAYS = 30
MIN_DAYS_FOR_BASELINE = 7
MIN_NONZERO_BASELINE_DAYS = 3  # need ≥3 days with actual tickets before flagging
TIER_2_DAILY_THRESHOLD = 20    # need ≥20 tickets/day avg to qualify for Tier 2
THETA_1_PERCENTILE = 0.90      # 1θ = 90th percentile
THETA_2_PERCENTILE = 0.975     # 2θ = 97.5th percentile
CUSUM_K_FACTOR = 0.5           # allowance as fraction of λ
CUSUM_H_FACTOR = 5.0           # threshold as multiple of sqrt(λ)


def run_incident_scan(
    db,
    target_date: str = None,
    date_from: str = None,
    progress_callback=None,
) -> dict:
    """
    Full incident scan across the entire date range.

    Iterates day-by-day from ``date_from`` (or the baseline window start)
    through ``target_date``, evaluating Poisson thresholds and updating
    CUSUM accumulators progressively.  This ensures flags are created for
    *every* anomalous day, not only the final target date.

    The returned ``trc_results`` reflect the final day's state (with the
    full daily series for charting), while ``new_flags`` accumulates
    flags across all scanned days.

    Args:
        target_date: End date for analysis (defaults to today)
        date_from: Start date for scan range (defaults to baseline window)

    Returns:
        {
            "scan_date": str,
            "date_from": str,
            "trcs_scanned": int,
            "trc_results": [...],   # final-day analysis per TRC
            "new_flags": [...],     # all flags created across all days
            "open_flags_total": int,
        }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    _prog = progress_callback or (lambda msg, pct: None)

    # Get TRCs
    _prog("Loading TRC list...", 5)
    trc_codes = [r["code"] for r in db.get_trc_codes()]
    if not trc_codes:
        return {"scan_date": target_date, "date_from": date_from or target_date,
                "trcs_scanned": 0,
                "trc_results": [], "new_flags": [], "open_flags_total": 0}

    # Build the list of days to evaluate.
    # We need at least BASELINE_WINDOW_DAYS of history before the first
    # evaluation day, so the first evaluation day is date_from (or the
    # baseline window start relative to target_date).
    window_start = (datetime.strptime(target_date, "%Y-%m-%d")
                    - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

    # series_start = earliest date we need data for (chart display)
    series_start = min(date_from, window_start) if date_from else window_start

    # Determine the range of days that actually have ticket data
    data_dates = set()
    for trc in trc_codes:
        for row in db.get_daily_series(trc, series_start, target_date):
            data_dates.add(row["date"])
    if not data_dates:
        return {"scan_date": target_date, "date_from": date_from or window_start,
                "trcs_scanned": len(trc_codes),
                "trc_results": [], "new_flags": [], "open_flags_total": 0}

    # Evaluation days: every date from the earliest data date through target_date
    # that has at least BASELINE_WINDOW_DAYS before it.  We still evaluate
    # days even if they have 0 tickets for some TRCs, so walk the calendar.
    eval_start_dt = datetime.strptime(min(data_dates), "%Y-%m-%d") + timedelta(days=MIN_DAYS_FOR_BASELINE)
    eval_end_dt = datetime.strptime(target_date, "%Y-%m-%d")

    if eval_start_dt > eval_end_dt:
        # Not enough data for any evaluation, but still return series
        eval_days = [target_date]
    else:
        eval_days = []
        d = eval_start_dt
        while d <= eval_end_dt:
            eval_days.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

    # Reset CUSUM accumulators so the progressive scan is clean
    db.conn.execute("DELETE FROM trc_baselines")
    db.conn.commit()

    new_flags = []
    trc_results = []  # will hold the *last* day's results per TRC

    total_steps = len(eval_days) * len(trc_codes)
    step = 0

    for day_idx, eval_date in enumerate(eval_days):
        day_window_start = (datetime.strptime(eval_date, "%Y-%m-%d")
                            - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")
        is_last_day = (day_idx == len(eval_days) - 1)

        if is_last_day:
            trc_results = []  # rebuild for final day

        for trc in trc_codes:
            step += 1
            if step % 50 == 0 or is_last_day:
                pct = 5 + int(85 * step / max(total_steps, 1))
                _prog(f"Day {day_idx+1}/{len(eval_days)}: {trc[:40]}...", min(pct, 94))

            # On the last day, include the full series for charting
            s_start = series_start if is_last_day else day_window_start
            result = _analyze_trc(db, trc, eval_date, day_window_start, s_start)

            # Persist baseline (updates CUSUM accumulator)
            _save_baseline(db, result)

            # Create flags for breaches
            flags = _evaluate_flags(db, trc, eval_date, result)
            new_flags.extend(flags)

            if is_last_day:
                trc_results.append(result)

    _prog("Finalizing...", 95)

    # Count open flags
    open_count = db.conn.execute(
        "SELECT COUNT(*) as cnt FROM incident_flags WHERE status IN ('open','acknowledged')"
    ).fetchone()["cnt"]

    _prog("Scan complete.", 100)

    # Sort: flagged TRCs first, then by flag severity, then deviation
    trc_results.sort(key=lambda r: (
        -r["flag_level"],
        -(1 if r.get("cusum_alert") else 0),
        -abs(r["observed_today"] - r["lambda_daily"]) if r["lambda_daily"] > 0 else 0
    ))

    return {
        "scan_date": target_date,
        "date_from": date_from or window_start,
        "trcs_scanned": len(trc_codes),
        "trc_results": trc_results,
        "new_flags": new_flags,
        "open_flags_total": open_count,
    }


def _analyze_trc(db, trc_code, target_date, window_start, series_start=None):
    """
    Compute Poisson baseline and CUSUM state for one TRC.
    series_start: optional earlier start date for the chart display series.
    """
    # Fetch daily counts in baseline window (for stats computation)
    all_days = db.get_daily_series(trc_code, series_start or window_start, target_date)

    # Build lookup of dates that have data
    day_lookup = {d["date"]: d["ticket_count"] for d in all_days}

    target_day = [d for d in all_days if d["date"] == target_date]
    observed_today = target_day[0]["ticket_count"] if target_day else 0

    # Compute the number of *calendar* days in the baseline window.
    # The DB only stores days with tickets > 0, so we must count zero-
    # ticket days as well (a Poisson process has many zeros).
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    window_dt = datetime.strptime(window_start, "%Y-%m-%d")
    calendar_baseline_days = (target_dt - window_dt).days  # excludes target_date itself

    if calendar_baseline_days < MIN_DAYS_FOR_BASELINE:
        return {
            "trc_code": trc_code,
            "tier": 1,
            "lambda_daily": 0.0,
            "theta_1_daily": 0,
            "theta_2_daily": 0,
            "observed_today": observed_today,
            "p_value_daily": 1.0,
            "flag_level": 0,
            "flag_source": "none",
            "cusum_value": 0.0,
            "cusum_threshold": 0.0,
            "cusum_alert": False,
            "baseline_days": calendar_baseline_days,
            "daily_series": all_days,
            "hourly_detail": None,
            "insufficient_data": True,
        }

    # Count days with actual data in the baseline window
    baseline_rows = [d for d in all_days if d["date"] < target_date]
    nonzero_baseline_days = len(baseline_rows)

    if nonzero_baseline_days < MIN_NONZERO_BASELINE_DAYS:
        return {
            "trc_code": trc_code,
            "tier": 1,
            "lambda_daily": 0.0,
            "theta_1_daily": 0,
            "theta_2_daily": 0,
            "observed_today": observed_today,
            "p_value_daily": 1.0,
            "flag_level": 0,
            "flag_source": "none",
            "cusum_value": 0.0,
            "cusum_threshold": 0.0,
            "cusum_alert": False,
            "baseline_days": calendar_baseline_days,
            "daily_series": all_days,
            "hourly_detail": None,
            "insufficient_data": True,
        }

    # Compute Poisson λ (daily rate) from baseline.
    # Include zero-count days: total tickets in baseline / calendar days.
    baseline_total = sum(d["ticket_count"] for d in baseline_rows)
    lambda_daily = baseline_total / calendar_baseline_days

    # Guard: λ must be > 0 for Poisson
    if lambda_daily < 0.01:
        lambda_daily = 0.01

    # Poisson percentile thresholds
    theta_1_daily = int(poisson.ppf(THETA_1_PERCENTILE, lambda_daily))
    theta_2_daily = int(poisson.ppf(THETA_2_PERCENTILE, lambda_daily))

    # Floor guards: thresholds must be above λ
    theta_1_daily = max(theta_1_daily, int(np.ceil(lambda_daily)) + 1)
    theta_2_daily = max(theta_2_daily, theta_1_daily + 1)

    # p-value: exact Poisson survival probability
    p_value_daily = float(poisson.sf(observed_today - 1, lambda_daily)) if observed_today > 0 else 1.0

    # Flag level from Poisson
    flag_level = 0
    flag_source = "none"
    if observed_today >= theta_2_daily:
        flag_level = 2
        flag_source = "poisson_daily"
    elif observed_today >= theta_1_daily:
        flag_level = 1
        flag_source = "poisson_daily"

    # CUSUM drift detection
    k = CUSUM_K_FACTOR * lambda_daily
    h = CUSUM_H_FACTOR * np.sqrt(max(lambda_daily, 0.01))

    # Load previous CUSUM value
    prev_baseline = db.conn.execute(
        "SELECT cusum_value FROM trc_baselines WHERE trc_code = ?",
        (trc_code,)
    ).fetchone()
    cusum_prev = prev_baseline["cusum_value"] if prev_baseline else 0.0

    # Update CUSUM with today's observation
    cusum_value = max(0.0, cusum_prev + (observed_today - lambda_daily - k))
    cusum_alert = cusum_value > h

    # CUSUM can elevate flag_level
    if cusum_alert and flag_level < 1:
        flag_level = 1
        flag_source = "cusum"

    # Tier assignment
    tier = 2 if lambda_daily >= TIER_2_DAILY_THRESHOLD else 1

    # Tier 2: hourly analysis
    hourly_detail = None
    if tier == 2:
        hourly_detail = _analyze_trc_hourly(db, trc_code, target_date, window_start)
        if hourly_detail and hourly_detail.get("flag_level", 0) > flag_level:
            flag_level = hourly_detail["flag_level"]
            flag_source = "poisson_hourly"

    return {
        "trc_code": trc_code,
        "tier": tier,
        "lambda_daily": round(lambda_daily, 2),
        "theta_1_daily": theta_1_daily,
        "theta_2_daily": theta_2_daily,
        "observed_today": observed_today,
        "p_value_daily": round(p_value_daily, 4),
        "flag_level": flag_level,
        "flag_source": flag_source,
        "cusum_value": round(float(cusum_value), 2),
        "cusum_threshold": round(float(h), 2),
        "cusum_alert": cusum_alert,
        "baseline_days": calendar_baseline_days,
        "daily_series": all_days,
        "hourly_detail": hourly_detail,
        "insufficient_data": False,
    }


def _analyze_trc_hourly(db, trc_code, target_date, window_start):
    """
    Tier 2 only: hour-of-day Poisson analysis.
    Builds 24 separate baselines from historical data,
    then checks today's counts against each hour's baseline.
    """
    all_hours = db.get_hourly_series(trc_code, window_start, target_date)
    baseline_hours = [h for h in all_hours if h["date"] < target_date]
    target_hours = {h["hour"]: h["ticket_count"] for h in all_hours if h["date"] == target_date}

    # Need enough baseline data
    baseline_days_set = set(h["date"] for h in baseline_hours)
    if len(baseline_days_set) < MIN_DAYS_FOR_BASELINE:
        return None

    # Group baseline by hour-of-day
    hour_buckets = defaultdict(list)
    for h in baseline_hours:
        hour_buckets[h["hour"]].append(h["ticket_count"])

    # Compute per-hour baselines
    hourly_baselines = {}
    for hour in range(24):
        counts = hour_buckets.get(hour, [0])
        lam = float(np.mean(counts)) if counts else 0.0
        if lam < 0.01:
            lam = 0.01
        t1 = int(poisson.ppf(THETA_1_PERCENTILE, lam))
        t2 = int(poisson.ppf(THETA_2_PERCENTILE, lam))
        t1 = max(t1, int(np.ceil(lam)) + 1)
        t2 = max(t2, t1 + 1)
        hourly_baselines[hour] = {
            "lambda": round(lam, 2),
            "theta_1": t1,
            "theta_2": t2,
        }

    # Check today's hours
    worst_flag = 0
    worst_hour = None
    flagged_hours = []

    for hour, observed in target_hours.items():
        bl = hourly_baselines.get(hour, {"lambda": 0.01, "theta_1": 1, "theta_2": 2})
        if observed >= bl["theta_2"]:
            level = 2
        elif observed >= bl["theta_1"]:
            level = 1
        else:
            level = 0

        if level > 0:
            p_val = float(poisson.sf(observed - 1, bl["lambda"])) if observed > 0 else 1.0
            flagged_hours.append({
                "hour": hour,
                "observed": observed,
                "lambda": bl["lambda"],
                "theta_1": bl["theta_1"],
                "theta_2": bl["theta_2"],
                "flag_level": level,
                "p_value": round(p_val, 4),
            })

        if level > worst_flag:
            worst_flag = level
            worst_hour = hour

    # Persist hourly baselines
    for hour, bl in hourly_baselines.items():
        db.conn.execute("""
            INSERT OR REPLACE INTO hourly_baselines
                (trc_code, hour, lambda_hourly, theta_1_hourly, theta_2_hourly)
            VALUES (?, ?, ?, ?, ?)
        """, (trc_code, hour, bl["lambda"], bl["theta_1"], bl["theta_2"]))
    db.conn.commit()

    return {
        "flag_level": worst_flag,
        "worst_hour": worst_hour,
        "flagged_hours": flagged_hours,
        "hourly_baselines": hourly_baselines,
        "today_hours": target_hours,
    }


def _save_baseline(db, result):
    """Persist computed baseline to trc_baselines table."""
    if result.get("insufficient_data"):
        return
    now = datetime.now().isoformat()
    db.conn.execute("""
        INSERT OR REPLACE INTO trc_baselines
            (trc_code, tier, lambda_daily, theta_1_daily, theta_2_daily,
             cusum_value, cusum_threshold, cusum_slack,
             baseline_days, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["trc_code"], result["tier"],
        result["lambda_daily"], result["theta_1_daily"], result["theta_2_daily"],
        result["cusum_value"], result["cusum_threshold"],
        CUSUM_K_FACTOR * result["lambda_daily"],
        result["baseline_days"], now,
    ))
    db.conn.commit()


def _evaluate_flags(db, trc_code, target_date, result):
    """Create incident_flags rows for any breaches detected."""
    if result.get("insufficient_data") or result["flag_level"] == 0:
        return []

    now = datetime.now().isoformat()
    flags = []

    # Daily Poisson or CUSUM flag
    if result["flag_source"] in ("poisson_daily", "cusum"):
        existing = db.conn.execute("""
            SELECT flag_id FROM incident_flags
            WHERE trc_code = ? AND triggered_date = ? AND flag_type = ?
                  AND status IN ('open', 'acknowledged')
        """, (trc_code, target_date, result["flag_source"])).fetchone()

        if existing:
            db.conn.execute("""
                UPDATE incident_flags
                SET observed_value = ?, theta_level = ?, p_value = ?,
                    cusum_value = ?
                WHERE flag_id = ?
            """, (result["observed_today"], result["flag_level"],
                  result.get("p_value_daily"), result.get("cusum_value"),
                  existing["flag_id"]))
            db.conn.commit()
        else:
            threshold = result["theta_2_daily"] if result["flag_level"] == 2 else result["theta_1_daily"]
            db.conn.execute("""
                INSERT INTO incident_flags
                    (trc_code, flag_type, theta_level, direction, triggered_at,
                     triggered_date, triggered_hour, observed_value, expected_lambda,
                     threshold_value, p_value, cusum_value, status, created_at)
                VALUES (?, ?, ?, 'above', ?, ?, NULL, ?, ?, ?, ?, ?, 'open', ?)
            """, (
                trc_code, result["flag_source"], result["flag_level"],
                now, target_date,
                result["observed_today"], result["lambda_daily"],
                threshold,
                result.get("p_value_daily"),
                result.get("cusum_value"),
                now,
            ))
            db.conn.commit()

            flags.append({
                "trc_code": trc_code,
                "flag_type": result["flag_source"],
                "theta_level": result["flag_level"],
                "observed": result["observed_today"],
                "lambda": result["lambda_daily"],
                "p_value": result.get("p_value_daily"),
                "interpretation": _build_interpretation(result),
            })

    # Hourly flags (Tier 2 only)
    if result.get("hourly_detail") and result["hourly_detail"].get("flagged_hours"):
        for fh in result["hourly_detail"]["flagged_hours"]:
            existing = db.conn.execute("""
                SELECT flag_id FROM incident_flags
                WHERE trc_code = ? AND triggered_date = ? AND triggered_hour = ?
                      AND flag_type = 'poisson_hourly'
                      AND status IN ('open', 'acknowledged')
            """, (trc_code, target_date, fh["hour"])).fetchone()

            if not existing:
                threshold = fh["theta_2"] if fh["flag_level"] == 2 else fh["theta_1"]
                db.conn.execute("""
                    INSERT INTO incident_flags
                        (trc_code, flag_type, theta_level, direction, triggered_at,
                         triggered_date, triggered_hour, observed_value, expected_lambda,
                         threshold_value, p_value, status, created_at)
                    VALUES (?, 'poisson_hourly', ?, 'above', ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """, (
                    trc_code, fh["flag_level"], now, target_date, fh["hour"],
                    fh["observed"], fh["lambda"],
                    threshold,
                    fh["p_value"], now,
                ))
                db.conn.commit()

    return flags


def _build_interpretation(result):
    """Human-readable flag description."""
    severity = "Watch" if result["flag_level"] == 1 else "Incident"
    trc = result["trc_code"]
    obs = result["observed_today"]
    lam = result["lambda_daily"]
    p = result.get("p_value_daily", 1.0)

    parts = [f"{severity}: {trc} received {obs} tickets today (expected ~{lam:.1f})"]

    if result["flag_source"] == "poisson_daily":
        parts.append(f"p={p:.3f} — this count or higher occurs ~{p*100:.1f}% of days")
    elif result["flag_source"] == "cusum":
        parts.append(
            f"CUSUM drift detected: sustained elevation "
            f"(accumulator {result['cusum_value']:.1f} > threshold {result['cusum_threshold']:.1f})"
        )

    return " | ".join(parts)


# ─── Flag Management ───

def get_open_flags(db, theta_level=None, flag_type=None, limit=100):
    """Get open incident flags, optionally filtered."""
    query = "SELECT * FROM incident_flags WHERE status IN ('open', 'acknowledged')"
    params = []
    if theta_level:
        query += " AND theta_level = ?"
        params.append(theta_level)
    if flag_type:
        query += " AND flag_type = ?"
        params.append(flag_type)
    query += " ORDER BY triggered_at DESC, theta_level DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.conn.execute(query, params).fetchall()]


def get_flag_history(db, trc_code=None, days=90, limit=300):
    """Get incident flag history."""
    # Use max flag date as reference so historical data is visible
    max_row = db.conn.execute(
        "SELECT MAX(triggered_date) AS max_d FROM incident_flags"
    ).fetchone()
    ref_str = max_row["max_d"] if max_row and max_row["max_d"] else None
    if ref_str:
        ref_date = datetime.strptime(ref_str, "%Y-%m-%d")
    else:
        ref_date = datetime.now()
    cutoff = (ref_date - timedelta(days=days)).strftime("%Y-%m-%d")

    query = "SELECT * FROM incident_flags WHERE triggered_date >= ?"
    params = [cutoff]
    if trc_code:
        query += " AND trc_code = ?"
        params.append(trc_code)
    query += " ORDER BY triggered_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.conn.execute(query, params).fetchall()]


def acknowledge_flag(db, flag_id, notes=""):
    db.conn.execute(
        "UPDATE incident_flags SET status = 'acknowledged', notes = ? WHERE flag_id = ?",
        (notes, flag_id))
    db.conn.commit()


def resolve_flag(db, flag_id, notes=""):
    db.conn.execute(
        "UPDATE incident_flags SET status = 'resolved', resolved_at = ?, notes = ? WHERE flag_id = ?",
        (datetime.now().isoformat(), notes, flag_id))
    db.conn.commit()


def mark_false_positive(db, flag_id, notes=""):
    db.conn.execute(
        "UPDATE incident_flags SET status = 'false_positive', notes = ? WHERE flag_id = ?",
        (notes, flag_id))
    db.conn.commit()


def correlate_flag_with_interventions(db, flag, lookback_days=14):
    """Check if a flag correlates with a recent intervention.

    Queries interventions within `lookback_days` before the flag's trigger date.
    Returns list of matching interventions (may be empty).
    """
    import json as _json

    triggered_date = flag.get("triggered_date", "")
    trc_code = flag.get("trc_code", "")
    if not triggered_date:
        return []

    window_start = (datetime.strptime(triggered_date, "%Y-%m-%d")
                    - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    try:
        interventions = db.get_interventions(
            date_from=window_start, date_to=triggered_date
        )
    except Exception:
        return []

    matches = []
    for iv in interventions:
        affected = iv.get("affected_trcs", "")
        if isinstance(affected, str):
            try:
                affected = _json.loads(affected)
            except (_json.JSONDecodeError, TypeError):
                affected = []

        # Match if no TRC restriction or if the flag's TRC is in the affected list
        if not affected or trc_code in affected:
            matches.append({
                "intervention_id": iv.get("intervention_id"),
                "name": iv.get("name", ""),
                "category": iv.get("category", ""),
                "event_date": iv.get("event_date", ""),
                "description": iv.get("description", ""),
                "days_before_flag": (
                    datetime.strptime(triggered_date, "%Y-%m-%d")
                    - datetime.strptime(iv["event_date"], "%Y-%m-%d")
                ).days,
            })

    return matches
