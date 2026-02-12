"""
Alma Insights — TRC Analytics Compute Layer
Queries the database and computes all metrics needed by the TRC Analytics page.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def compute_trc_analytics(conn, date_start, date_end, trc_filter=None, status_filter=None):
    """
    Compute all TRC analytics metrics for the given filters.

    Args:
        conn: A sqlite3.Connection (thread-safe — created in the worker thread).

    Returns a dict with keys:
        - summary: {total_tickets, avg_resolution, avg_first_reply, avg_csat}
        - volume_by_trc: [(trc_code, count), ...] sorted desc
        - resolution_by_trc: {trc_code: [resolution_hours, ...]}
        - csat_heatmap: {y_labels, x_labels, values}
        - metrics_table: list of row dicts
    """
    # ── Build query ──
    conditions = ["c.created_at >= ?", "c.created_at <= ?"]
    params = [date_start, date_end]

    if trc_filter and trc_filter != "All TRCs":
        conditions.append("c.trc_code = ?")
        params.append(trc_filter)

    if status_filter and status_filter != "All":
        conditions.append("t.status = ?")
        params.append(status_filter.lower())

    where = " AND ".join(conditions)

    # Query joining conversations + tickets for resolution times
    query = f"""
        SELECT c.ticket_id, c.trc_code, c.status, c.csat_score,
               c.created_at, c.message_count, c.client_messages, c.agent_messages,
               t.assignment_to_resolution_hours, t.total_resolution_hours, t.first_reply_hours
        FROM conversations c
        LEFT JOIN tickets t ON c.ticket_id = t.ticket_id
        WHERE {where}
    """

    rows = conn.execute(query, params).fetchall()
    if not rows:
        return _empty_result()

    df = pd.DataFrame([dict(r) for r in rows])

    # ── Summary KPIs ──
    total_tickets = len(df)

    avg_resolution = None
    if "assignment_to_resolution_hours" in df.columns:
        res_vals = df["assignment_to_resolution_hours"].dropna()
        if len(res_vals) > 0:
            avg_resolution = float(res_vals.mean())

    avg_first_reply = None
    if "first_reply_hours" in df.columns:
        frt_vals = df["first_reply_hours"].dropna()
        if len(frt_vals) > 0:
            avg_first_reply = float(frt_vals.mean())

    avg_csat = None
    csat_vals = df["csat_score"].dropna()
    if len(csat_vals) > 0:
        avg_csat = float(csat_vals.mean())

    summary = {
        "total_tickets": total_tickets,
        "avg_resolution": avg_resolution,
        "avg_first_reply": avg_first_reply,
        "avg_csat": avg_csat,
    }

    # ── Volume by TRC ──
    trc_counts = df[df["trc_code"] != ""].groupby("trc_code").size().sort_values(ascending=False)
    volume_by_trc = [(trc, int(cnt)) for trc, cnt in trc_counts.items()]

    # ── Resolution time distribution by TRC ──
    resolution_by_trc = {}
    if "total_resolution_hours" in df.columns:
        for trc, grp in df[df["trc_code"] != ""].groupby("trc_code"):
            vals = grp["total_resolution_hours"].dropna().tolist()
            if vals:
                resolution_by_trc[trc] = vals

    # ── CSAT Heatmap ──
    csat_heatmap = _compute_csat_heatmap(df, date_start, date_end)

    # ── Metrics Table ──
    metrics_table = _compute_metrics_table(df)

    return {
        "summary": summary,
        "volume_by_trc": volume_by_trc,
        "resolution_by_trc": resolution_by_trc,
        "csat_heatmap": csat_heatmap,
        "metrics_table": metrics_table,
    }


def _compute_csat_heatmap(df, date_start, date_end):
    """Build CSAT heatmap data: TRC (Y) x time period (X)."""
    # Filter to rows with CSAT and TRC
    hm_df = df[(df["csat_score"].notna()) & (df["trc_code"] != "")].copy()

    if hm_df.empty:
        return {"y_labels": [], "x_labels": [], "values": {}}

    # Parse dates
    hm_df["date"] = pd.to_datetime(hm_df["created_at"], errors="coerce")
    hm_df = hm_df.dropna(subset=["date"])

    if hm_df.empty:
        return {"y_labels": [], "x_labels": [], "values": {}}

    # Determine granularity
    try:
        d_start = datetime.strptime(date_start[:10], "%Y-%m-%d")
        d_end = datetime.strptime(date_end[:10], "%Y-%m-%d")
        span_days = (d_end - d_start).days
    except (ValueError, TypeError):
        span_days = 90

    if span_days <= 60:
        freq = "W"
        fmt = "%b %d"
    else:
        freq = "MS"  # month start
        fmt = "%b %Y"

    hm_df["period"] = hm_df["date"].dt.to_period(freq[0])

    # Only TRCs with >= 3 rated tickets
    trc_counts = hm_df.groupby("trc_code").size()
    valid_trcs = trc_counts[trc_counts >= 3].index.tolist()
    hm_df = hm_df[hm_df["trc_code"].isin(valid_trcs)]

    if hm_df.empty:
        return {"y_labels": [], "x_labels": [], "values": {}}

    # Pivot: avg CSAT per TRC x period
    pivot = hm_df.groupby(["trc_code", "period"])["csat_score"].mean()

    periods_sorted = sorted(hm_df["period"].unique())
    trcs_sorted = sorted(valid_trcs)

    x_labels = [str(p) for p in periods_sorted]
    y_labels = trcs_sorted

    values = {}
    for yi, trc in enumerate(trcs_sorted):
        for xi, period in enumerate(periods_sorted):
            if (trc, period) in pivot.index:
                values[(yi, xi)] = float(pivot.loc[(trc, period)])

    return {"y_labels": y_labels, "x_labels": x_labels, "values": values}


def _compute_metrics_table(df):
    """Compute per-TRC metrics table."""
    trc_df = df[df["trc_code"] != ""]
    if trc_df.empty:
        return []

    rows = []
    for trc, grp in trc_df.groupby("trc_code"):
        count = len(grp)

        # Resolution times
        avg_resolution = None
        median_resolution = None
        p95_resolution = None
        if "total_resolution_hours" in grp.columns:
            res = grp["total_resolution_hours"].dropna()
            if len(res) > 0:
                avg_resolution = float(res.mean())
                median_resolution = float(res.median())
                p95_resolution = float(np.percentile(res, 95))

        # First reply
        avg_first_reply = None
        if "first_reply_hours" in grp.columns:
            frt = grp["first_reply_hours"].dropna()
            if len(frt) > 0:
                avg_first_reply = float(frt.mean())

        # CSAT
        avg_csat = None
        csat = grp["csat_score"].dropna()
        if len(csat) > 0:
            avg_csat = float(csat.mean())

        # % Solved
        solved_count = len(grp[grp["status"] == "solved"])
        pct_solved = (solved_count / count * 100) if count > 0 else 0

        # Avg messages
        avg_messages = float(grp["message_count"].mean()) if "message_count" in grp.columns else None

        # Agent:Customer ratio
        agent_customer_ratio = None
        if "agent_messages" in grp.columns and "client_messages" in grp.columns:
            total_agent = grp["agent_messages"].sum()
            total_client = grp["client_messages"].sum()
            if total_client > 0:
                agent_customer_ratio = float(total_agent) / float(total_client)

        rows.append({
            "trc_code": trc,
            "ticket_count": count,
            "avg_resolution": avg_resolution,
            "median_resolution": median_resolution,
            "p95_resolution": p95_resolution,
            "avg_first_reply": avg_first_reply,
            "avg_csat": avg_csat,
            "pct_solved": pct_solved,
            "avg_messages": avg_messages,
            "agent_customer_ratio": agent_customer_ratio,
        })

    # Sort by ticket count descending
    rows.sort(key=lambda r: r["ticket_count"], reverse=True)
    return rows


def _empty_result():
    return {
        "summary": {
            "total_tickets": 0,
            "avg_resolution": None,
            "avg_first_reply": None,
            "avg_csat": None,
        },
        "volume_by_trc": [],
        "resolution_by_trc": {},
        "csat_heatmap": {"y_labels": [], "x_labels": [], "values": {}},
        "metrics_table": [],
    }
