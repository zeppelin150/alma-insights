"""
Alma Insights — Post-Scan Persistence Writer (Build 11.0)

Runs after the meta-analyzer completes.  Reads from ticket_index
(which was populated during the scan) and writes:
  1. scan_category_snapshots — per-TRC/friction distributions
  2. trend_snapshots         — deltas vs. prior scan (>20% change)
  3. insight_ledger          — significant new findings
"""

import json
import logging
import uuid
from datetime import datetime

logger = logging.getLogger("alma.post_scan_persist")


def write_scan_category_snapshot(scan_id: str, scan_date: str, conn):
    """Compute category distributions from ticket_index for this scan."""
    rows = conn.execute(
        """
        SELECT trc_code, friction_type, sub_pattern,
               COUNT(*) AS ticket_count,
               AVG(sentiment_intensity) AS avg_sentiment,
               AVG(csat_score) AS avg_csat,
               SUM(CASE WHEN anomaly_flag IS NOT NULL THEN 1 ELSE 0 END) AS anomaly_count
        FROM ticket_index
        WHERE last_seen_scan_id = ?
        GROUP BY trc_code, friction_type, sub_pattern
        """,
        (scan_id,),
    ).fetchall()

    for row in rows:
        trc = row[0]
        friction = row[1]
        sub_pat = row[2]

        samples = conn.execute(
            """
            SELECT ticket_id FROM ticket_index
            WHERE last_seen_scan_id = ? AND trc_code IS ? AND friction_type IS ? AND sub_pattern IS ?
            LIMIT 5
            """,
            (scan_id, trc, friction, sub_pat),
        ).fetchall()

        conn.execute(
            """
            INSERT INTO scan_category_snapshots
            (scan_id, scan_date, trc, friction_type, sub_pattern,
             ticket_count, avg_sentiment, avg_csat, anomaly_count, sample_ticket_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id, scan_date, trc, friction, sub_pat,
                row[3], row[4], row[5], row[6],
                json.dumps([s[0] for s in samples]),
            ),
        )

    conn.commit()
    logger.info("Wrote %d category snapshot rows for scan %s", len(rows), scan_id)
    return len(rows)


def write_trend_deltas(scan_id: str, conn):
    """Compare current snapshot vs most recent prior, write trend_snapshots."""
    # Find the most recent prior scan_id
    prior = conn.execute(
        """
        SELECT DISTINCT scan_id FROM scan_category_snapshots
        WHERE scan_id != ?
        ORDER BY scan_date DESC LIMIT 1
        """,
        (scan_id,),
    ).fetchone()

    if prior is None:
        logger.info("No prior scan snapshot — skipping trend deltas")
        return 0

    prior_scan_id = prior[0]
    now = datetime.utcnow().isoformat()

    # Get current and prior counts by friction_type
    current_rows = conn.execute(
        """
        SELECT friction_type, SUM(ticket_count) AS total,
               AVG(avg_sentiment) AS avg_sent
        FROM scan_category_snapshots WHERE scan_id = ?
        GROUP BY friction_type
        """,
        (scan_id,),
    ).fetchall()

    prior_map = {}
    for row in conn.execute(
        """
        SELECT friction_type, SUM(ticket_count) AS total,
               AVG(avg_sentiment) AS avg_sent
        FROM scan_category_snapshots WHERE scan_id = ?
        GROUP BY friction_type
        """,
        (prior_scan_id,),
    ).fetchall():
        prior_map[row[0]] = (row[1], row[2])

    written = 0
    for row in current_rows:
        friction = row[0]
        current_count = row[1]
        current_sent = row[2]

        if friction in prior_map:
            prior_count, prior_sent = prior_map[friction]
            if prior_count and prior_count > 0:
                pct_change = ((current_count - prior_count) / prior_count) * 100
            else:
                pct_change = 100.0
        else:
            prior_count = 0
            pct_change = 100.0

        # Only write if >20% change or new
        if abs(pct_change) < 20 and friction in prior_map:
            continue

        if pct_change > 0:
            direction = "rising"
        elif pct_change < 0:
            direction = "falling"
        else:
            direction = "stable"

        severity = "info"
        if abs(pct_change) > 50:
            severity = "warning"
        if abs(pct_change) > 100 or (friction not in prior_map and current_count > 10):
            severity = "critical"

        conn.execute(
            """
            INSERT INTO trend_snapshots
            (snapshot_date, engine, metric_key, metric_value,
             direction, severity, pct_change, baseline_value, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now, "scan_diff", friction or "unknown", current_count,
                direction, severity, round(pct_change, 1), prior_count,
                json.dumps({"scan_id": scan_id, "prior_scan_id": prior_scan_id}),
            ),
        )
        written += 1

    conn.commit()
    logger.info("Wrote %d trend delta rows for scan %s", written, scan_id)
    return written


def write_scan_insights(scan_id: str, conn):
    """Check significance thresholds and write to insight_ledger."""
    now = datetime.utcnow().isoformat()
    written = 0

    # New patterns with >10 tickets
    new_patterns = conn.execute(
        """
        SELECT sub_pattern, COUNT(*) AS cnt, GROUP_CONCAT(ticket_id) AS tids
        FROM ticket_index
        WHERE last_seen_scan_id = ? AND first_seen_scan_id = ?
        GROUP BY sub_pattern
        HAVING cnt >= 10
        """,
        (scan_id, scan_id),
    ).fetchall()

    for row in new_patterns:
        pattern_name = row[0] or "unknown"
        ticket_ids = (row[2] or "").split(",")[:5]
        conn.execute(
            """
            INSERT INTO insight_ledger
            (insight_id, date_identified, source_run_id, insight_type,
             title, description, severity, supporting_ticket_ids, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), now, scan_id, "trend",
                f"New pattern: {pattern_name}",
                f"New pattern '{pattern_name}' appeared with {row[1]} tickets in scan {scan_id}.",
                "moderate" if row[1] < 25 else "high",
                json.dumps(ticket_ids),
                "new",
            ),
        )
        written += 1

    # Critical anomalies
    critical = conn.execute(
        """
        SELECT COUNT(*) FROM ticket_index
        WHERE last_seen_scan_id = ? AND anomaly_flag = 'critical'
        """,
        (scan_id,),
    ).fetchone()[0]

    if critical and critical >= 5:
        conn.execute(
            """
            INSERT INTO insight_ledger
            (insight_id, date_identified, source_run_id, insight_type,
             title, description, severity, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), now, scan_id, "anomaly",
                f"Critical anomaly cluster: {critical} tickets",
                f"Scan {scan_id} flagged {critical} tickets as critical anomalies.",
                "critical",
                "new",
            ),
        )
        written += 1

    conn.commit()
    logger.info("Wrote %d insight ledger entries for scan %s", written, scan_id)
    return written


def run_post_scan_persistence(scan_id: str, scan_date: str, conn):
    """Orchestrate all post-scan persistence writes."""
    snap_count = write_scan_category_snapshot(scan_id, scan_date, conn)
    trend_count = write_trend_deltas(scan_id, conn)
    insight_count = write_scan_insights(scan_id, conn)

    # Compute enriched trends (Session 3 — hybrid chat)
    enriched_count = 0
    try:
        from src.data.post_nlp import compute_enriched_trends
        enriched_count = compute_enriched_trends(conn, scan_id)
    except Exception as e:
        logger.warning("Enriched trends computation failed (non-fatal): %s", e)

    logger.info(
        "Post-scan persistence complete: %d snapshots, %d trends, %d insights, %d enriched",
        snap_count, trend_count, insight_count, enriched_count,
    )
    return {
        "snapshots": snap_count,
        "trends": trend_count,
        "insights": insight_count,
        "enriched_trends": enriched_count,
    }
