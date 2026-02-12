"""
Alma Insights — Incident Engine

Statistical process control for TRC ticket velocity.
Computes rolling baselines of tickets-per-hour per TRC, draws 1θ (±1σ)
and 2θ (±2σ) control bands, and flags hours where the observed rate
breaches a band.

The model:
  For TRC X over the last N days:
    1. Collect hourly ticket counts (24 values per day)
    2. Compute mean and std of the hourly rate
    3. θ₁ bands = mean ± 1σ
    4. θ₂ bands = mean ± 2σ
    5. When current hour's count > θ₂ upper → 2θ incident
    6. When current hour's count > θ₁ upper → 1θ watch
    7. Below-band breaches are tracked too (unusual quiet)
"""

import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict


# ── Configuration ──
BASELINE_WINDOW_DAYS = 30      # Rolling window for computing "normal"
MIN_DAYS_FOR_BASELINE = 7      # Don't flag until we have this much history
THETA_1 = 1.0                  # 1σ threshold
THETA_2 = 2.0                  # 2σ threshold


def run_incident_scan(
    db,
    target_date: str = None,
    progress_callback=None,
) -> dict:
    """
    Run a full incident scan: compute baselines, check all TRCs,
    flag breaches.

    Args:
        db: DatabaseManager instance
        target_date: YYYY-MM-DD. Default = today.
        progress_callback: callable(msg, pct)

    Returns:
        {
            "scan_date": str,
            "trcs_scanned": int,
            "trc_statuses": [...],
            "new_flags": [...],
            "open_flags_total": int,
            "theta_1_count": int,
            "theta_2_count": int,
        }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    _progress = progress_callback or (lambda msg, pct: None)

    # ── Get distinct TRCs ──
    _progress("Loading TRC list...", 5)
    trc_codes = [r["code"] for r in db.get_trc_codes()]

    if not trc_codes:
        return {"scan_date": target_date, "trcs_scanned": 0,
                "trc_statuses": [], "new_flags": [], "open_flags_total": 0,
                "theta_1_count": 0, "theta_2_count": 0}

    # ── Compute baseline window ──
    window_start = (datetime.strptime(target_date, "%Y-%m-%d")
                    - timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")

    trc_statuses = []
    new_flags = []

    for i, trc in enumerate(trc_codes):
        _progress(f"Scanning {trc}...", 10 + int(80 * i / len(trc_codes)))

        status = _analyze_trc(db, trc, target_date, window_start)
        trc_statuses.append(status)

        # Persist baseline
        _save_baseline(db, trc, status)

        # Flag if breached
        if status["flag_level"] > 0:
            flag = _create_flag(db, trc, target_date, status)
            if flag:
                new_flags.append(flag)

    # ── Count open flags ──
    _progress("Finalizing...", 95)
    open_count = db.conn.execute(
        "SELECT COUNT(*) as cnt FROM incident_flags WHERE status = 'open'"
    ).fetchone()["cnt"]

    theta_1_count = sum(1 for s in trc_statuses if s["flag_level"] == 1)
    theta_2_count = sum(1 for s in trc_statuses if s["flag_level"] == 2)

    _progress("Scan complete.", 100)

    return {
        "scan_date": target_date,
        "trcs_scanned": len(trc_codes),
        "trc_statuses": trc_statuses,
        "new_flags": new_flags,
        "open_flags_total": open_count,
        "theta_1_count": theta_1_count,
        "theta_2_count": theta_2_count,
    }


def _analyze_trc(db, trc_code, target_date, window_start):
    """
    Compute baseline stats and current status for a single TRC.
    """
    # Get all hourly counts in the baseline window
    baseline_rows = db.get_hourly_series(trc_code, window_start, target_date)

    # Separate baseline data (everything before target_date) from target day
    baseline_counts = []
    target_counts = {}
    chart_rows = []

    chart_start = (datetime.strptime(target_date, "%Y-%m-%d")
                   - timedelta(days=7)).strftime("%Y-%m-%d")

    for r in baseline_rows:
        if r["date"] < target_date:
            baseline_counts.append(r["ticket_count"])
        elif r["date"] == target_date:
            target_counts[r["hour"]] = r["ticket_count"]

        # Collect last 7 days for chart display
        if r["date"] >= chart_start:
            chart_rows.append(r)

    # Compute baseline statistics
    if len(baseline_counts) < MIN_DAYS_FOR_BASELINE * 8:
        # Not enough data — need at least ~8 hours × 7 days worth
        return {
            "trc_code": trc_code,
            "flag_level": 0,
            "flag_direction": "normal",
            "current_rate": 0.0,
            "hourly_mean": 0.0,
            "hourly_std": 0.0,
            "theta_1_upper": 0.0,
            "theta_2_upper": 0.0,
            "theta_1_lower": 0.0,
            "theta_2_lower": 0.0,
            "z_score": 0.0,
            "baseline_days": 0,
            "hourly_series": chart_rows,
            "insufficient_data": True,
        }

    arr = np.array(baseline_counts, dtype=float)
    hourly_mean = float(np.mean(arr))
    hourly_std = float(np.std(arr))

    # Prevent division by zero — if std is 0 (perfectly uniform),
    # use 10% of the mean as a synthetic std
    if hourly_std < 0.01:
        hourly_std = max(0.1, hourly_mean * 0.1)

    theta_1_upper = hourly_mean + THETA_1 * hourly_std
    theta_2_upper = hourly_mean + THETA_2 * hourly_std
    theta_1_lower = max(0, hourly_mean - THETA_1 * hourly_std)
    theta_2_lower = max(0, hourly_mean - THETA_2 * hourly_std)

    # Determine current rate: use the most recent hour with data
    current_rate = 0.0
    if target_counts:
        latest_hour = max(target_counts.keys())
        current_rate = float(target_counts[latest_hour])
    elif baseline_counts:
        # No target-day data yet — use last known hour
        current_rate = float(baseline_counts[-1])

    # Compute z-score
    z_score = (current_rate - hourly_mean) / hourly_std

    # Determine flag level
    flag_level = 0
    flag_direction = "normal"

    if z_score >= THETA_2:
        flag_level = 2
        flag_direction = "above"
    elif z_score >= THETA_1:
        flag_level = 1
        flag_direction = "above"
    elif z_score <= -THETA_2:
        flag_level = 2
        flag_direction = "below"
    elif z_score <= -THETA_1:
        flag_level = 1
        flag_direction = "below"

    baseline_days = len(set(r["date"] for r in baseline_rows if r["date"] < target_date))

    return {
        "trc_code": trc_code,
        "flag_level": flag_level,
        "flag_direction": flag_direction,
        "current_rate": current_rate,
        "hourly_mean": round(hourly_mean, 2),
        "hourly_std": round(hourly_std, 2),
        "theta_1_upper": round(theta_1_upper, 2),
        "theta_2_upper": round(theta_2_upper, 2),
        "theta_1_lower": round(theta_1_lower, 2),
        "theta_2_lower": round(theta_2_lower, 2),
        "z_score": round(z_score, 2),
        "baseline_days": baseline_days,
        "hourly_series": chart_rows,
        "insufficient_data": False,
    }


def _save_baseline(db, trc_code, status):
    """Persist computed baseline to trc_baselines table."""
    if status.get("insufficient_data"):
        return
    now = datetime.now().isoformat()
    db.conn.execute("""
        INSERT OR REPLACE INTO trc_baselines
            (trc_code, hourly_mean, hourly_std,
             theta_1_upper, theta_2_upper, theta_1_lower, theta_2_lower,
             sample_hours, window_days, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trc_code, status["hourly_mean"], status["hourly_std"],
        status["theta_1_upper"], status["theta_2_upper"],
        status["theta_1_lower"], status["theta_2_lower"],
        0, status["baseline_days"], now
    ))
    db.conn.commit()


def _create_flag(db, trc_code, target_date, status):
    """Create an incident flag if one doesn't already exist for this TRC+date+hour."""
    now = datetime.now()

    # Determine the trigger hour (most recent hour with data on target date)
    trigger_hour = now.hour
    # If we have hourly series data for the target date, use the latest hour
    for r in reversed(status.get("hourly_series", [])):
        if r["date"] == target_date:
            trigger_hour = r["hour"]
            break

    # Check for duplicate
    existing = db.conn.execute("""
        SELECT flag_id FROM incident_flags
        WHERE trc_code = ? AND triggered_date = ? AND triggered_hour = ?
              AND status IN ('open', 'acknowledged')
    """, (trc_code, target_date, trigger_hour)).fetchone()

    if existing:
        # Update z-score in case it changed
        db.conn.execute("""
            UPDATE incident_flags
            SET observed_rate = ?, z_score = ?, theta_level = ?
            WHERE flag_id = ?
        """, (status["current_rate"], status["z_score"],
              status["flag_level"], existing["flag_id"]))
        db.conn.commit()
        return None

    db.conn.execute("""
        INSERT INTO incident_flags
            (trc_code, theta_level, direction, triggered_at, triggered_date,
             triggered_hour, observed_rate, expected_mean, expected_std,
             z_score, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
    """, (
        trc_code, status["flag_level"], status["flag_direction"],
        now.isoformat(), target_date, trigger_hour,
        status["current_rate"], status["hourly_mean"], status["hourly_std"],
        status["z_score"], now.isoformat()
    ))
    db.conn.commit()

    return {
        "trc_code": trc_code,
        "theta_level": status["flag_level"],
        "direction": status["flag_direction"],
        "observed_rate": status["current_rate"],
        "z_score": status["z_score"],
        "interpretation": _build_interpretation(trc_code, status),
    }


def _build_interpretation(trc_code, status):
    """Human-readable flag description."""
    direction = "above" if status["flag_direction"] == "above" else "below"
    severity = "Watch" if status["flag_level"] == 1 else "Incident"
    return (
        f"{severity}: {trc_code} ticket rate is {abs(status['z_score']):.1f}s "
        f"{direction} normal ({status['current_rate']:.0f}/hr vs "
        f"avg {status['hourly_mean']:.1f} +/- {status['hourly_std']:.1f})"
    )


# ─── Flag Management ───

def get_open_flags(db, theta_level=None, limit=100):
    """Get open incident flags, optionally filtered by theta level."""
    query = "SELECT * FROM incident_flags WHERE status IN ('open', 'acknowledged')"
    params = []
    if theta_level:
        query += " AND theta_level = ?"
        params.append(theta_level)
    query += " ORDER BY triggered_at DESC, theta_level DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.conn.execute(query, params).fetchall()]


def get_flag_history(db, trc_code=None, days=30, limit=200):
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
