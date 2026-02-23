"""
Alma Insights — Theta Anomaly Detection Engine

Statistical process control for RCM ticket metrics.
Computes rolling baselines and flags day-over-day variance that exceeds
1σ (watch) or 2σ (incident) thresholds.

Metrics tracked per TRC per day:
  - sentiment: average VADER compound score
  - term_freq: TF-IDF score of each significant term

Note: Ticket volume/rate monitoring handled by incident_engine.py
(Poisson CDF + CUSUM) — the statistically correct model for count data.

The engine maintains rolling statistics (mean + std) using an exponentially
weighted moving average (EWMA) so recent days have more influence on "normal".
"""

import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict


# ── Configuration ──
ROLLING_WINDOW_DAYS = 30
EWMA_ALPHA = 0.15
MIN_DAYS_FOR_BASELINE = 7
THETA_1 = 1.0
THETA_2 = 2.0
TOP_TERMS_TO_TRACK = 50


def run_theta_scan(conn, target_date=None, progress_callback=None):
    """
    Run the theta anomaly detection scan for a specific date.

    Steps:
    1. Compute daily metrics for target_date (volume, sentiment, terms)
    2. Load or compute rolling baselines from historical data
    3. Compare target_date metrics against baselines
    4. Flag anomalies at 1θ and 2θ levels
    5. Persist baselines and flags to database

    Args:
        conn: SQLite connection (row_factory=sqlite3.Row expected)
        target_date: YYYY-MM-DD string. Default = today.
        progress_callback: callable(step, total_steps, message)

    Returns dict with date, flags, theta counts, scan metadata.
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    total_steps = 5

    def _progress(step, msg):
        if progress_callback:
            progress_callback(step, total_steps, msg)

    # ── Step 1: Compute daily metrics ──
    _progress(1, "Computing daily metrics...")
    daily_metrics = _compute_daily_metrics(conn, target_date)

    if not daily_metrics:
        return {
            "date": target_date,
            "flags": [],
            "theta_1_count": 0,
            "theta_2_count": 0,
            "trcs_scanned": 0,
            "baseline_days": 0,
        }

    # Persist today's metrics
    _persist_daily_baselines(conn, target_date, daily_metrics)

    # ── Step 2: Compute rolling baselines ──
    _progress(2, "Computing rolling baselines...")
    baselines = _compute_rolling_baselines(conn, target_date)

    # ── Step 3: Compare and flag ──
    _progress(3, "Detecting anomalies...")
    flags = []

    for trc_code, metrics in daily_metrics.items():
        trc_baselines = baselines.get(trc_code, {})

        # Sentiment
        baseline = trc_baselines.get("sentiment")
        flag = _check_threshold(trc_code, "sentiment", "", metrics["sentiment"], baseline)
        if flag:
            flags.append(flag)

        # Top term frequencies
        for term, score in metrics.get("terms", {}).items():
            key = f"term_freq:{term}"
            baseline = trc_baselines.get(key)
            flag = _check_threshold(trc_code, "term_freq", term, score, baseline)
            if flag:
                flags.append(flag)

    # ── Step 4: Persist flags ──
    _progress(4, "Saving anomaly flags...")
    _persist_flags(conn, target_date, flags)

    # ── Step 5: Update rolling stats ──
    _progress(5, "Updating rolling statistics...")
    _update_rolling_stats(conn, target_date, daily_metrics)

    return {
        "date": target_date,
        "flags": flags,
        "theta_1_count": sum(1 for f in flags if f["theta_level"] == 1),
        "theta_2_count": sum(1 for f in flags if f["theta_level"] == 2),
        "trcs_scanned": len(daily_metrics),
        "baseline_days": ROLLING_WINDOW_DAYS,
    }


def run_theta_scan_range(conn, progress_callback=None):
    """
    Run theta scan across the FULL date range in the conversations table.

    Iterates day-by-day so EWMA baselines build naturally:
      - Days 1..MIN_DAYS_FOR_BASELINE: build baseline only (no flags)
      - Subsequent days: compare against rolling baseline, flag anomalies

    Args:
        conn: SQLite connection (row_factory=sqlite3.Row expected)
        progress_callback: callable(step, total_steps, message)

    Returns dict with combined flags, counts, date range, and scan metadata.
    """
    # Discover actual date range in the data
    row = conn.execute("""
        SELECT MIN(substr(created_at, 1, 10)) AS min_d,
               MAX(substr(created_at, 1, 10)) AS max_d
        FROM conversations
        WHERE trc_code != ''
    """).fetchone()

    if not row:
        return {
            "date_range": "",
            "days_scanned": 0,
            "flags": [],
            "theta_1_count": 0,
            "theta_2_count": 0,
            "trcs_scanned": 0,
            "baseline_days": ROLLING_WINDOW_DAYS,
        }

    min_d = row["min_d"] if hasattr(row, "keys") else row[0]
    max_d = row["max_d"] if hasattr(row, "keys") else row[1]

    if not min_d or not max_d:
        return {
            "date_range": "",
            "days_scanned": 0,
            "flags": [],
            "theta_1_count": 0,
            "theta_2_count": 0,
            "trcs_scanned": 0,
            "baseline_days": ROLLING_WINDOW_DAYS,
        }

    start_date = datetime.strptime(min_d, "%Y-%m-%d")
    end_date = datetime.strptime(max_d, "%Y-%m-%d")
    total_days = (end_date - start_date).days + 1

    all_flags = []
    all_trcs = set()

    for day_offset in range(total_days):
        current_date = start_date + timedelta(days=day_offset)
        day_str = current_date.strftime("%Y-%m-%d")

        if progress_callback:
            progress_callback(
                day_offset + 1, total_days,
                f"Scanning {day_str} ({day_offset + 1}/{total_days})..."
            )

        result = run_theta_scan(conn, target_date=day_str)
        day_flags = result.get("flags", [])

        # Tag each flag with the date for display
        for f in day_flags:
            f["date"] = day_str

        all_flags.extend(day_flags)
        if result.get("trcs_scanned", 0) > 0:
            all_trcs.update(
                f["trc_code"] for f in day_flags
            )

    return {
        "date_range": f"{min_d} to {max_d}",
        "days_scanned": total_days,
        "flags": all_flags,
        "theta_1_count": sum(1 for f in all_flags if f["theta_level"] == 1),
        "theta_2_count": sum(1 for f in all_flags if f["theta_level"] == 2),
        "trcs_scanned": len(all_trcs),
        "baseline_days": ROLLING_WINDOW_DAYS,
    }


def _compute_daily_metrics(conn, date):
    """
    Compute metrics for a single day, grouped by TRC.

    Returns: {
        trc_code: {volume: int, sentiment: float, terms: {term: score, ...}}
    }
    """
    # Get conversations created on this date
    rows = conn.execute("""
        SELECT ticket_id, trc_code, full_thread, csat_score, created_at
        FROM conversations
        WHERE created_at LIKE ? AND trc_code != ''
    """, (date + "%",)).fetchall()

    if not rows:
        return {}

    # Group by TRC
    trc_groups = defaultdict(list)
    for r in rows:
        trc = r["trc_code"] if hasattr(r, "keys") else r[1]
        trc_groups[trc].append(dict(r) if hasattr(r, "keys") else r)

    # Sentiment scoring
    try:
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
        sia = SentimentIntensityAnalyzer()
    except Exception:
        sia = None

    metrics = {}
    for trc, convs in trc_groups.items():
        volume = len(convs)

        # Compute volume rate (tickets per active hour)
        active_hours = set()
        for c in convs:
            created_at = c.get("created_at", "") if isinstance(c, dict) else ""
            if created_at and len(created_at) >= 13:
                try:
                    # Extract HH from ISO timestamp (YYYY-MM-DDTHH or YYYY-MM-DD HH)
                    hour_str = created_at[11:13]
                    if hour_str.isdigit():
                        active_hours.add(hour_str)
                except (IndexError, ValueError):
                    pass
        n_active_hours = len(active_hours) if active_hours else 1
        volume_per_hour = volume / n_active_hours

        # Sentiment
        sentiments = []
        texts = []
        for c in convs:
            thread = c.get("full_thread", "") if isinstance(c, dict) else ""
            if thread and sia:
                score = sia.polarity_scores(thread)["compound"]
                sentiments.append(score)
                texts.append(thread)

        avg_sentiment = float(np.mean(sentiments)) if sentiments else 0.0

        # Term frequencies (TF-IDF on this day's conversations for this TRC)
        terms = {}
        if len(texts) >= 2:
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer
                vec = TfidfVectorizer(
                    ngram_range=(1, 3), max_features=TOP_TERMS_TO_TRACK,
                    min_df=1, max_df=0.95,
                )
                matrix = vec.fit_transform(texts)
                feature_names = vec.get_feature_names_out()
                avg_scores = np.asarray(matrix.mean(axis=0)).flatten()
                for idx in avg_scores.argsort()[-TOP_TERMS_TO_TRACK:][::-1]:
                    if avg_scores[idx] > 0.01:
                        terms[feature_names[idx]] = float(avg_scores[idx])
            except (ValueError, Exception):
                pass

        metrics[trc] = {
            "active_hours": n_active_hours,
            "sentiment": avg_sentiment,
            "terms": terms,
        }

    return metrics


def _check_threshold(trc_code, metric_type, metric_key, observed, baseline):
    """
    Check if an observed value exceeds the baseline by 1θ or 2θ.
    Returns a flag dict or None if within normal range.
    """
    if baseline is None:
        return None

    mean = baseline["mean"]
    std = baseline["std"]
    n = baseline["sample_count"]

    if n < MIN_DAYS_FOR_BASELINE:
        return None

    if std < 0.001:
        # No meaningful variance — can't detect anomalies
        if abs(observed - mean) > abs(mean) * 0.3 and abs(observed - mean) > 0.1:
            std = max(abs(mean) * 0.1, 0.1)
        else:
            return None

    z_score = (observed - mean) / std

    if abs(z_score) >= THETA_2:
        theta_level = 2
    elif abs(z_score) >= THETA_1:
        theta_level = 1
    else:
        return None

    direction = "above" if z_score > 0 else "below"
    display_key = metric_key.replace("_", " ") if metric_key else metric_type

    if metric_type == "sentiment":
        interpretation = (
            f"{trc_code}: sentiment is {abs(z_score):.1f}σ {direction} normal "
            f"({observed:.2f} vs avg {mean:.2f} ± {std:.2f})"
        )
    elif metric_type == "term_freq":
        interpretation = (
            f"{trc_code}: '{display_key}' frequency is {abs(z_score):.1f}σ {direction} normal "
            f"({observed:.3f} vs avg {mean:.3f} ± {std:.3f})"
        )
    else:
        interpretation = (
            f"{trc_code}: {display_key} is {abs(z_score):.1f}σ {direction} normal"
        )

    return {
        "trc_code": trc_code,
        "metric_type": metric_type,
        "metric_key": metric_key,
        "observed": float(observed),
        "expected_mean": float(mean),
        "expected_std": float(std),
        "z_score": round(float(z_score), 2),
        "theta_level": theta_level,
        "direction": direction,
        "interpretation": interpretation,
    }


def _persist_daily_baselines(conn, date, daily_metrics):
    """Write today's raw metric values to the baselines table."""
    for trc, metrics in daily_metrics.items():
        conn.execute("""
            INSERT OR REPLACE INTO daily_baselines (date, trc_code, metric_type, metric_key, value)
            VALUES (?, ?, 'sentiment', '', ?)
        """, (date, trc, metrics["sentiment"]))

        for term, score in metrics.get("terms", {}).items():
            conn.execute("""
                INSERT OR REPLACE INTO daily_baselines (date, trc_code, metric_type, metric_key, value)
                VALUES (?, ?, 'term_freq', ?, ?)
            """, (date, trc, term, score))

    conn.commit()


def _compute_rolling_baselines(conn, target_date):
    """
    Compute rolling mean + std for each metric from historical daily_baselines.
    Uses EWMA (exponentially weighted) so recent days matter more.

    Returns: {
        trc_code: {
            "volume": {"mean": float, "std": float, "sample_count": int},
            "sentiment": {"mean": ..., ...},
            "term_freq:some_term": {"mean": ..., ...},
        }
    }
    """
    cutoff = (
        datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=ROLLING_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT date, trc_code, metric_type, metric_key, value
        FROM daily_baselines
        WHERE date >= ? AND date < ?
        ORDER BY date ASC
    """, (cutoff, target_date)).fetchall()

    if not rows:
        return {}

    # Group by (trc, metric_type, metric_key)
    grouped = defaultdict(list)
    for r in rows:
        trc = r["trc_code"] if hasattr(r, "keys") else r[1]
        mtype = r["metric_type"] if hasattr(r, "keys") else r[2]
        mkey = r["metric_key"] if hasattr(r, "keys") else r[3]
        value = r["value"] if hasattr(r, "keys") else r[4]
        key = (trc, mtype, mkey)
        grouped[key].append(value)

    baselines = defaultdict(dict)
    for (trc, mtype, mkey), values in grouped.items():
        vals = np.array(values, dtype=float)
        n = len(vals)

        if n < MIN_DAYS_FOR_BASELINE:
            continue

        # EWMA weights
        weights = np.array([
            EWMA_ALPHA * (1 - EWMA_ALPHA) ** (n - 1 - i) for i in range(n)
        ])
        weights /= weights.sum()

        ewma_mean = float(np.average(vals, weights=weights))
        ewma_var = float(np.average((vals - ewma_mean) ** 2, weights=weights))
        ewma_std = float(np.sqrt(ewma_var))

        composite_key = f"{mtype}:{mkey}" if mkey else mtype
        baselines[trc][composite_key] = {
            "mean": ewma_mean,
            "std": ewma_std,
            "sample_count": n,
        }

    return dict(baselines)


def _update_rolling_stats(conn, date, daily_metrics):
    """Update the rolling_stats table with latest computed baselines."""
    baselines = _compute_rolling_baselines(conn, date)
    now = datetime.now().isoformat()

    for trc, metrics in baselines.items():
        for composite_key, stats in metrics.items():
            parts = composite_key.split(":", 1)
            mtype = parts[0]
            mkey = parts[1] if len(parts) > 1 else ""

            conn.execute("""
                INSERT OR REPLACE INTO rolling_stats
                    (trc_code, metric_type, metric_key, rolling_mean, rolling_std,
                     sample_count, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (trc, mtype, mkey, stats["mean"], stats["std"],
                  stats["sample_count"], now))

    conn.commit()


def _persist_flags(conn, date, flags):
    """Write anomaly flags to the database."""
    now = datetime.now().isoformat()
    for f in flags:
        existing = conn.execute("""
            SELECT flag_id FROM anomaly_flags
            WHERE date = ? AND trc_code = ? AND metric_type = ? AND metric_key = ?
        """, (date, f["trc_code"], f["metric_type"], f["metric_key"])).fetchone()

        if existing:
            fid = existing["flag_id"] if hasattr(existing, "keys") else existing[0]
            conn.execute("""
                UPDATE anomaly_flags
                SET observed_value = ?, expected_mean = ?, expected_std = ?,
                    z_score = ?, theta_level = ?
                WHERE flag_id = ?
            """, (f["observed"], f["expected_mean"], f["expected_std"],
                  f["z_score"], f["theta_level"], fid))
        else:
            conn.execute("""
                INSERT INTO anomaly_flags
                    (date, trc_code, metric_type, metric_key, observed_value,
                     expected_mean, expected_std, z_score, theta_level, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """, (date, f["trc_code"], f["metric_type"], f["metric_key"],
                  f["observed"], f["expected_mean"], f["expected_std"],
                  f["z_score"], f["theta_level"], now))

    conn.commit()


# ─── Query functions for UI ───

def get_open_flags(conn, theta_level=None, limit=100):
    """Get open anomaly flags, optionally filtered by theta level."""
    query = "SELECT * FROM anomaly_flags WHERE status = 'open'"
    params = []
    if theta_level is not None:
        query += " AND theta_level = ?"
        params.append(theta_level)
    query += " ORDER BY date DESC, theta_level DESC, abs(z_score) DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_flag_history(conn, trc_code=None, days=30, limit=200):
    """Get anomaly flag history.

    Uses the most recent flag date as reference (not datetime.now()) so
    historical data is visible even when imported months after creation.
    """
    # Use max flag date as reference instead of today
    max_row = conn.execute("SELECT MAX(date) AS max_d FROM anomaly_flags").fetchone()
    if max_row:
        ref_str = max_row["max_d"] if hasattr(max_row, "keys") else max_row[0]
    else:
        ref_str = None
    if ref_str:
        ref_date = datetime.strptime(ref_str, "%Y-%m-%d")
    else:
        ref_date = datetime.now()
    cutoff = (ref_date - timedelta(days=days)).strftime("%Y-%m-%d")
    query = "SELECT * FROM anomaly_flags WHERE date >= ?"
    params = [cutoff]
    if trc_code:
        query += " AND trc_code = ?"
        params.append(trc_code)
    query += " ORDER BY date DESC, theta_level DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def acknowledge_flag(conn, flag_id, notes=""):
    """Mark a flag as acknowledged."""
    conn.execute(
        "UPDATE anomaly_flags SET status = 'acknowledged', notes = ? WHERE flag_id = ?",
        (notes, flag_id)
    )
    conn.commit()


def resolve_flag(conn, flag_id, notes=""):
    """Mark a flag as resolved."""
    conn.execute(
        "UPDATE anomaly_flags SET status = 'resolved', resolved_at = ?, notes = ? WHERE flag_id = ?",
        (datetime.now().isoformat(), notes, flag_id)
    )
    conn.commit()


def mark_false_positive(conn, flag_id, notes=""):
    """Mark a flag as a false positive."""
    conn.execute(
        "UPDATE anomaly_flags SET status = 'false_positive', notes = ? WHERE flag_id = ?",
        (notes, flag_id)
    )
    conn.commit()


# ═══════════════════════════════════════════
#  SUB-PATTERN SHARE TRACKING (Pass 4.0)
# ═══════════════════════════════════════════

def compute_sub_pattern_shares(conn, date):
    """
    For each active sub-pattern, compute daily share of parent TRC
    and feed to θ-EWMA as metric_type='sub_pattern_share'.

    share = (confirmed + provisional tickets for pattern on date)
            / (total tickets for parent TRC on date)

    Uses existing _check_threshold() and _persist_daily_baselines()
    machinery — just with metric_type='sub_pattern_share' and
    metric_key=pattern_id.
    """
    # Get all active sub-patterns
    active_patterns = conn.execute("""
        SELECT pattern_id, trc, label FROM sub_patterns
        WHERE tier = 'active' AND merged_into IS NULL
    """).fetchall()

    if not active_patterns:
        return []

    shares = {}
    flags = []

    for pat in active_patterns:
        pid = pat[0] if not hasattr(pat, 'keys') else pat["pattern_id"]
        trc = pat[1] if not hasattr(pat, 'keys') else pat["trc"]
        label = pat[2] if not hasattr(pat, 'keys') else pat["label"]

        # Count tickets for this pattern on this date
        # (from confirmed Gemini classifications + provisional n-gram matches)
        pattern_count = 0

        # Confirmed classifications
        row = conn.execute("""
            SELECT COUNT(*) FROM nlp_ticket_classifications tc
            JOIN conversations c ON tc.ticket_id = c.ticket_id
            WHERE tc.trc = ? AND tc.sub_cluster = ?
              AND SUBSTR(c.created_at, 1, 10) = ?
        """, (trc, label, date)).fetchone()
        if row:
            pattern_count += row[0]

        # Provisional classifications
        row = conn.execute("""
            SELECT COUNT(*) FROM provisional_classifications pc
            JOIN conversations c ON pc.ticket_id = c.ticket_id
            WHERE pc.matched_pattern_id = ? AND pc.is_confirmed = 0
              AND SUBSTR(c.created_at, 1, 10) = ?
        """, (pid, date)).fetchone()
        if row:
            pattern_count += row[0]

        # Total tickets for parent TRC on this date
        total_row = conn.execute("""
            SELECT COUNT(*) FROM conversations
            WHERE trc_code = ? AND SUBSTR(created_at, 1, 10) = ?
        """, (trc, date)).fetchone()
        total_trc = total_row[0] if total_row else 0

        if total_trc == 0:
            continue

        share = pattern_count / total_trc

        # Persist daily baseline
        conn.execute("""
            INSERT OR REPLACE INTO daily_baselines
                (date, trc_code, metric_type, metric_key, value)
            VALUES (?, ?, 'sub_pattern_share', ?, ?)
        """, (date, trc, pid, share))

        shares[pid] = share

    conn.commit()

    # Check thresholds against rolling baselines
    for pid, share in shares.items():
        # Get the TRC for this pattern
        pat_row = conn.execute(
            "SELECT trc FROM sub_patterns WHERE pattern_id = ?", (pid,)
        ).fetchone()
        if not pat_row:
            continue
        trc = pat_row[0] if not hasattr(pat_row, 'keys') else pat_row["trc"]

        # Load rolling baseline for this metric
        cutoff = (
            datetime.strptime(date, "%Y-%m-%d") - timedelta(days=ROLLING_WINDOW_DAYS)
        ).strftime("%Y-%m-%d")

        hist_rows = conn.execute("""
            SELECT value FROM daily_baselines
            WHERE trc_code = ? AND metric_type = 'sub_pattern_share'
              AND metric_key = ? AND date >= ? AND date < ?
            ORDER BY date ASC
        """, (trc, pid, cutoff, date)).fetchall()

        if len(hist_rows) < MIN_DAYS_FOR_BASELINE:
            continue

        vals = np.array([r[0] for r in hist_rows], dtype=float)

        # EWMA computation
        weights = np.array([(1 - EWMA_ALPHA) ** i for i in range(len(vals) - 1, -1, -1)])
        weights /= weights.sum()
        ewma_mean = np.dot(weights, vals)
        ewma_std = max(np.sqrt(np.dot(weights, (vals - ewma_mean) ** 2)), 0.001)

        z_score = (share - ewma_mean) / ewma_std

        # Check thresholds
        theta_level = 0
        if abs(z_score) >= THETA_2:
            theta_level = 2
        elif abs(z_score) >= THETA_1:
            theta_level = 1

        if theta_level > 0:
            flags.append({
                "trc_code": trc,
                "metric_type": "sub_pattern_share",
                "metric_key": pid,
                "observed": share,
                "expected_mean": ewma_mean,
                "expected_std": ewma_std,
                "z_score": z_score,
                "theta_level": theta_level,
            })

        # Update rolling stats
        conn.execute("""
            INSERT OR REPLACE INTO rolling_stats
                (trc_code, metric_type, metric_key, rolling_mean,
                 rolling_std, sample_count, last_updated)
            VALUES (?, 'sub_pattern_share', ?, ?, ?, ?, ?)
        """, (trc, pid, float(ewma_mean), float(ewma_std),
              len(vals), datetime.now().isoformat()))

    conn.commit()

    # Persist flags
    if flags:
        _persist_flags(conn, date, flags)

    return flags
