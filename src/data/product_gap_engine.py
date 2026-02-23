"""
Alma Insights — Product Gap Detection Engine (Pass 3.0)
Detects product areas with cross-cutting issues: high TRC spread, rising volume,
declining sentiment, repeat contacts.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np


def detect_product_gaps(db, date_start, date_end):
    """Detect product gaps by cross-cutting product area analysis.

    A product area is "flagged" when:
    - TRC spread >= 3 (issue cuts across multiple reason codes)
    - Volume is rising (compared to prior equal-length window)
    - Sentiment is declining

    Returns list of dicts: [
        {
            "product_area": str,
            "trc_count": int,
            "trcs": [str],
            "total_tickets": int,
            "volume_velocity": float,  # % change vs prior period
            "sentiment_current": float,
            "sentiment_prior": float,
            "sentiment_delta": float,
            "repeat_pct": float,
            "gap_score": float,
            "is_flagged": bool,
        }, ...
    ]
    """
    # ── Get entity-product associations ──
    product_tickets = _get_product_area_tickets(db, date_start, date_end)
    if not product_tickets:
        return []

    # ── Compute prior period for velocity ──
    try:
        d_start = datetime.strptime(date_start[:10], "%Y-%m-%d")
        d_end = datetime.strptime(date_end[:10], "%Y-%m-%d")
        period_days = (d_end - d_start).days or 1
        prior_start = (d_start - timedelta(days=period_days)).strftime("%Y-%m-%d")
        prior_end = (d_start - timedelta(days=1)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        prior_start = date_start
        prior_end = date_start

    prior_product_tickets = _get_product_area_tickets(db, prior_start, prior_end)

    # ── Analyze each product area ──
    results = []
    for product_area, tickets in product_tickets.items():
        ticket_ids = [t["ticket_id"] for t in tickets]
        total = len(ticket_ids)

        # TRC spread
        trcs = set(t.get("trc_code", "") for t in tickets if t.get("trc_code"))
        trc_count = len(trcs)

        # Volume velocity
        prior_total = len(prior_product_tickets.get(product_area, []))
        if prior_total > 0:
            volume_velocity = (total - prior_total) / prior_total
        elif total > 0:
            volume_velocity = 1.0  # New area, treat as 100% rise
        else:
            volume_velocity = 0.0

        # Sentiment
        sentiments = _compute_sentiment_for_tickets(db, ticket_ids, date_start, date_end)
        prior_ids = [t["ticket_id"] for t in prior_product_tickets.get(product_area, [])]
        prior_sentiments = _compute_sentiment_for_tickets(db, prior_ids, prior_start, prior_end)

        sentiment_current = float(np.mean(sentiments)) if sentiments else 0.0
        sentiment_prior = float(np.mean(prior_sentiments)) if prior_sentiments else 0.0
        sentiment_delta = sentiment_current - sentiment_prior

        # Repeat contact detection
        repeat_pct = _compute_repeat_pct(db, ticket_ids)

        # Gap score
        gap_score = _compute_gap_score(
            trc_count, volume_velocity, sentiment_delta, repeat_pct
        )

        # Flagging threshold
        is_flagged = (
            trc_count >= 3
            and (volume_velocity > 0.1 or sentiment_delta < -0.05)
            and gap_score >= 3.0
        )

        results.append({
            "product_area": product_area,
            "trc_count": trc_count,
            "trcs": sorted(trcs),
            "total_tickets": total,
            "volume_velocity": volume_velocity,
            "sentiment_current": round(sentiment_current, 4),
            "sentiment_prior": round(sentiment_prior, 4),
            "sentiment_delta": round(sentiment_delta, 4),
            "repeat_pct": round(repeat_pct, 2),
            "gap_score": round(gap_score, 1),
            "is_flagged": is_flagged,
        })

    # Sort by gap_score descending
    results.sort(key=lambda x: x["gap_score"], reverse=True)
    return results


def _compute_gap_score(trc_spread, volume_velocity, sentiment_delta, repeat_pct):
    """Compute composite gap score (0-10 scale).

    Components:
    - TRC spread: +1 per TRC above 2 (max 3 points)
    - Volume velocity: +2 if >20%, +1 if >10% (max 2 points)
    - Sentiment decline: +2 if delta < -0.15, +1 if < -0.05 (max 2 points)
    - Repeat contacts: +3 if >20%, +2 if >10%, +1 if >5% (max 3 points)
    """
    score = 0.0

    # TRC spread (max 3)
    spread_bonus = min(trc_spread - 2, 3)
    score += max(spread_bonus, 0)

    # Volume velocity (max 2)
    if volume_velocity > 0.2:
        score += 2
    elif volume_velocity > 0.1:
        score += 1

    # Sentiment decline (max 2)
    if sentiment_delta < -0.15:
        score += 2
    elif sentiment_delta < -0.05:
        score += 1

    # Repeat contacts (max 3)
    if repeat_pct > 20:
        score += 3
    elif repeat_pct > 10:
        score += 2
    elif repeat_pct > 5:
        score += 1

    return score


def _get_product_area_tickets(db, date_start, date_end):
    """Get tickets grouped by product area via entity extraction results."""
    try:
        rows = db.conn.execute("""
            SELECT te.ticket_id, te.entity_value, c.trc_code
            FROM ticket_entities te
            JOIN conversations c ON te.ticket_id = c.ticket_id
            WHERE te.entity_type = 'product_area'
              AND c.created_at >= ? AND c.created_at <= ?
        """, (date_start, date_end)).fetchall()
    except Exception:
        return {}

    product_map = defaultdict(list)
    for r in rows:
        product_map[r["entity_value"]].append({
            "ticket_id": r["ticket_id"],
            "trc_code": r["trc_code"],
        })
    return dict(product_map)


def _compute_sentiment_for_tickets(db, ticket_ids, date_start, date_end):
    """Compute VADER sentiment for a set of tickets."""
    if not ticket_ids:
        return []

    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        analyzer = SentimentIntensityAnalyzer()
    except ImportError:
        return []

    placeholders = ",".join("?" * min(len(ticket_ids), 500))
    ids = ticket_ids[:500]
    try:
        rows = db.conn.execute(f"""
            SELECT thread_preview FROM conversations
            WHERE ticket_id IN ({placeholders}) AND thread_preview != ''
        """, ids).fetchall()
    except Exception:
        return []

    return [analyzer.polarity_scores(r["thread_preview"])["compound"] for r in rows]


def _compute_repeat_pct(db, ticket_ids):
    """Compute percentage of tickets from repeat requesters (via requester_hash)."""
    if not ticket_ids:
        return 0.0

    placeholders = ",".join("?" * min(len(ticket_ids), 500))
    ids = ticket_ids[:500]
    try:
        rows = db.conn.execute(f"""
            SELECT t.requester_hash, COUNT(*) as cnt
            FROM tickets t
            WHERE t.ticket_id IN ({placeholders})
              AND t.requester_hash != '' AND t.requester_hash IS NOT NULL
            GROUP BY t.requester_hash
            HAVING cnt > 1
        """, ids).fetchall()

        repeat_contacts = sum(r["cnt"] for r in rows)
        return (repeat_contacts / len(ids)) * 100 if ids else 0.0
    except Exception:
        return 0.0
