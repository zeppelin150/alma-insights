"""Fast-path chat tool handlers (<1s response).

All handlers share the same signature:
    handler(conn, args, session_filters) -> dict

Session filters are pre-merged by the dispatcher. These handlers
call build_filter_query() for consistent filtering.
"""

from __future__ import annotations

import json
import logging

from src.data.filter_engine import build_filter_query

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
#  NEW TOOLS (Session 2)
# ═══════════════════════════════════════


def handle_query_classifications(conn, args: dict, session_filters: dict) -> dict:
    """Count and group tickets by a classification field."""
    group_by = args.get("group_by", "trc_code")
    valid_groups = {
        "trc_code", "friction_type", "sub_pattern",
        "sentiment_polarity", "anomaly_flag",
    }
    if group_by not in valid_groups:
        return {"error": f"Invalid group_by: {group_by}. Use: {sorted(valid_groups)}"}

    # Merge explicit filters from args into session_filters
    filters = dict(session_filters)
    if args.get("filters"):
        filters.update(args["filters"])

    # ticket_ids scoping — direct SQL since filter_engine doesn't support it
    ticket_ids = args.get("ticket_ids", [])
    if ticket_ids:
        placeholders = ",".join(["?"] * len(ticket_ids))
        sql = (
            f"SELECT ti.{group_by}, COUNT(*) AS ticket_count "
            f"FROM ticket_index ti "
            f"WHERE ti.ticket_id IN ({placeholders}) "
            f"GROUP BY ti.{group_by} ORDER BY ticket_count DESC"
        )
        params = list(ticket_ids)
    else:
        sql, params = build_filter_query(
            filters=filters,
            select_columns=[f"ti.{group_by}", "COUNT(*) AS ticket_count"],
            base_table="ticket_index",
        )
        # Inject GROUP BY before ORDER BY / LIMIT
        sql += f"\nGROUP BY ti.{group_by}\nORDER BY ticket_count DESC"

    rows = conn.execute(sql, params).fetchall()
    return {
        "groups": [{"value": r[0], "count": r[1]} for r in rows],
        "total": sum(r[1] for r in rows),
        "group_by": group_by,
    }


def handle_list_tickets(conn, args: dict, session_filters: dict) -> dict:
    """List individual tickets matching filters with summaries."""
    limit = min(args.get("limit", 20), 50)
    sort_map = {
        "date": "ti.ticket_created_date DESC",
        "sentiment": "ti.sentiment_intensity DESC",
        "csat": "ti.csat_score ASC",
    }
    sort = sort_map.get(args.get("sort", "date"), "ti.ticket_created_date DESC")

    sql, params = build_filter_query(
        filters=session_filters,
        select_columns=[
            "ti.ticket_id", "ti.trc_code", "ti.friction_type",
            "ti.sub_pattern", "ti.sentiment_polarity",
            "ti.anomaly_flag", "ti.issue_snippet",
            "ti.ticket_created_date",
        ],
        base_table="ticket_index",
        order_by=sort,
        limit=limit,
    )
    rows = conn.execute(sql, params).fetchall()
    return {
        "tickets": [_row_to_dict(r) for r in rows],
        "count": len(rows),
    }


def handle_query_findings(conn, args: dict, session_filters: dict) -> dict:
    """Retrieve NLP scan findings for the current scope."""
    finding_type = args.get("finding_type")
    min_impact = args.get("min_impact")

    conditions = []
    params = []

    # Scope to latest scan by default
    scan_row = conn.execute(
        "SELECT scan_id FROM nlp_scan_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if scan_row:
        conditions.append("scan_id = ?")
        params.append(scan_row[0])

    if finding_type:
        conditions.append("finding_type = ?")
        params.append(finding_type)

    if min_impact is not None:
        conditions.append("impact_score >= ?")
        params.append(float(min_impact))

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"SELECT finding_id, finding_type, scope, title, description, "
        f"ticket_count, pct_of_scanned, dominant_friction_type, "
        f"top_trcs, top_sub_patterns, impact_score "
        f"FROM nlp_findings{where} "
        f"ORDER BY impact_score DESC LIMIT 20",
        params,
    ).fetchall()

    return {
        "findings": [_row_to_dict(r) for r in rows],
        "count": len(rows),
    }


def handle_query_stats(conn, args: dict, session_filters: dict) -> dict:
    """Query statistical engine outputs.

    stat_type:
      - anomalies: statistical anomaly_flags rows with z_score, theta_level,
        metric_type, notes (real anomaly-detection output, not ticket tags).
      - trends: week-by-week volume by dimension. Reads enriched_trends
        when available; falls back to ticket_index aggregation when empty.
      - baselines: trc_baselines snapshots.
      - csat: CSAT-score bucket breakdown over tickets (added 2026-04-23 F-6).

    Added bug-bash 2026-04-23 (F-3, F-4, F-6): the anomaly path used to
    read ticket_index.anomaly_flag (just the string tag) which had no
    date/z_score/severity context — Gemini invented narratives to fill
    the gap. It now reads anomaly_flags (the real statistical output).
    Trend path used to read enriched_trends only, which is empty on
    production DBs — it now falls back to a live aggregation.
    """
    stat_type = args.get("stat_type", "anomalies")

    if stat_type == "anomalies":
        return _query_anomaly_stats(conn, args)
    elif stat_type == "trends":
        return _query_enriched_trends(conn, args, session_filters)
    elif stat_type == "baselines":
        return _query_baselines(conn, args)
    elif stat_type == "csat":
        return _query_csat_stats(conn, args)
    elif stat_type == "friction_distribution":
        return _query_friction_distribution(conn, args)
    else:
        return {
            "error": (
                f"Unknown stat_type: {stat_type}. "
                "Use: anomalies, trends, baselines, csat, friction_distribution."
            )
        }


def _query_anomaly_stats(conn, args: dict) -> dict:
    """Query the real anomaly_flags table (statistical engine output).

    Supports:
      - severity: "severe" (theta_level >= 2), "minor" (theta_level == 1), or None/all
      - metric_type: "sentiment", "term_freq", etc.
      - trc_code: filter to a single TRC
      - date_range: "YYYY-MM-DD/YYYY-MM-DD"
      - limit: max rows (default 20, max 100)
    """
    severity = (args.get("severity") or "").lower().strip()
    metric_type = args.get("metric_type")
    trc_code = args.get("trc_code") or args.get("trc")
    date_range = args.get("date_range")
    limit = min(int(args.get("limit", 20)), 100)

    # Check table exists + has rows; fall back to ticket_index tags if not.
    try:
        has_real = conn.execute(
            "SELECT 1 FROM anomaly_flags LIMIT 1"
        ).fetchone() is not None
    except Exception:
        has_real = False

    if not has_real:
        # Legacy fallback: ticket-level tags from ticket_index.
        rows = conn.execute(
            "SELECT ticket_id, trc_code, anomaly_flag AS severity, "
            "anomaly_reason AS notes, ticket_created_date AS date "
            "FROM ticket_index "
            "WHERE anomaly_flag IS NOT NULL AND anomaly_flag != '' "
            "ORDER BY ticket_created_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {
            "anomalies": [_row_to_dict(r) for r in rows],
            "count": len(rows),
            "source": "ticket_index.anomaly_flag (fallback — anomaly_flags table empty)",
        }

    where_parts: list[str] = []
    params: list = []
    if severity in ("severe", "critical"):
        where_parts.append("theta_level >= 2")
    elif severity in ("minor", "mild"):
        where_parts.append("theta_level = 1")
    if metric_type:
        where_parts.append("metric_type = ?")
        params.append(metric_type)
    if trc_code:
        where_parts.append("trc_code = ?")
        params.append(trc_code)
    if date_range and "/" in date_range:
        try:
            start, end = date_range.split("/", 1)
            where_parts.append("date BETWEEN ? AND ?")
            params.extend([start.strip(), end.strip()])
        except Exception:
            pass
    where_clause = " AND ".join(where_parts) if where_parts else "1=1"

    sql = (
        "SELECT date, trc_code, metric_type, metric_key, "
        "observed_value, expected_mean, z_score, theta_level, "
        "CASE WHEN theta_level >= 2 THEN 'severe' "
        "     WHEN theta_level = 1 THEN 'minor' "
        "     ELSE 'none' END AS severity, "
        "COALESCE(notes, '') AS notes "
        f"FROM anomaly_flags WHERE {where_clause} "
        "ORDER BY ABS(z_score) DESC LIMIT ?"
    )
    rows = conn.execute(sql, params + [limit]).fetchall()
    anomalies = [_row_to_dict(r) for r in rows]
    # F-3b (bug-bash 2026-04-23): anomaly_flags.notes is empty on every
    # row in the production DB because the upstream VOC pipeline never
    # populated it. Gemini, given only z_score and a TRC code, fills in
    # narrative ('500 error critical') that doesn't exist. We synthesize
    # a deterministic description from the real fields so the LLM has
    # something concrete to quote.
    for a in anomalies:
        a["description"] = _synthesize_anomaly_description(a)
    return {
        "anomalies": anomalies,
        "count": len(anomalies),
        "source": "anomaly_flags",
    }


def _synthesize_anomaly_description(row: dict) -> str:
    """Deterministic one-line description from anomaly_flags fields.

    Never invents facts — only reshapes the existing row fields into
    natural-language form for the LLM to quote. If notes is populated
    we prefer it; otherwise we describe the metric mechanically."""
    notes = (row.get("notes") or "").strip()
    if notes:
        return notes

    severity = row.get("severity", "?")
    metric_type = row.get("metric_type", "")
    z = row.get("z_score")
    date = row.get("date", "")
    trc = row.get("trc_code", "")
    metric_key = (row.get("metric_key") or "").strip()

    if metric_type == "sentiment":
        direction = "crashed" if (z or 0) < 0 else "surged"
        return (
            f"{severity.capitalize()} sentiment {direction} (z={z:.2f}) on "
            f"{date} for TRC '{trc}'."
        )
    if metric_type == "term_freq":
        direction = "spike" if (z or 0) > 0 else "drop"
        return (
            f"{severity.capitalize()} term-frequency {direction} for "
            f"'{metric_key}' (z={z:.2f}) on {date} in TRC '{trc}'."
        )
    if metric_type == "volume":
        direction = "surge" if (z or 0) > 0 else "dip"
        return (
            f"{severity.capitalize()} volume {direction} (z={z:.2f}) on "
            f"{date} for TRC '{trc}'."
        )
    return (
        f"{severity.capitalize()} {metric_type} anomaly (z={z:.2f}) on "
        f"{date} for TRC '{trc}'."
    )


def _query_enriched_trends(conn, args: dict, session_filters: dict) -> dict:
    """Return weekly volume trends for a dimension.

    Prefers the pre-computed enriched_trends table; falls back to an
    on-the-fly aggregation over ticket_index when enriched_trends is
    empty (which is the case on every production DB that hasn't had
    the enriched-trends pipeline run — bug-bash 2026-04-23 F-4).

    Supported dimensions:
        trc, trc_code, friction_type, sub_cluster, sub_pattern,
        payer (insurance_payer), provider (provider_id), cluster (alias of sub_cluster)
    """
    dimension = args.get("dimension", "friction_type")
    # Aliases
    dimension_column_map = {
        "trc": "trc_code",
        "trc_code": "trc_code",
        "friction_type": "friction_type",
        "sub_cluster": "sub_pattern",   # sub_cluster ≈ sub_pattern in ticket_index
        "cluster": "sub_pattern",
        "sub_pattern": "sub_pattern",
        "payer": "insurance_payer",
        "insurance_payer": "insurance_payer",
        "provider": "provider_id",
        "provider_id": "provider_id",
    }
    column = dimension_column_map.get(dimension)
    if column is None:
        return {
            "error": (
                f"Unsupported dimension={dimension!r}. Valid: "
                f"{sorted(set(dimension_column_map))}"
            )
        }

    # Try enriched_trends first (fast path)
    try:
        rows = conn.execute(
            "SELECT dimension, dimension_value, period, ticket_count, "
            "pct_of_total, velocity, trc_breakdown "
            "FROM enriched_trends WHERE dimension = ? "
            "ORDER BY period DESC, ticket_count DESC LIMIT 50",
            (dimension,),
        ).fetchall()
        if rows:
            return {
                "trends": [_row_to_dict(r) for r in rows],
                "count": len(rows),
                "source": "enriched_trends",
            }
    except Exception:
        pass

    # Fallback: compute trends on-the-fly from ticket_index
    limit = min(int(args.get("limit", 100)), 500)
    sql = (
        f"SELECT '{dimension}' AS dimension, "
        f"{column} AS dimension_value, "
        "strftime('%Y-W%W', ticket_created_date) AS period, "
        "COUNT(*) AS ticket_count "
        "FROM ticket_index "
        f"WHERE ticket_created_date IS NOT NULL AND {column} IS NOT NULL "
        "GROUP BY dimension_value, period "
        "ORDER BY period DESC, ticket_count DESC "
        f"LIMIT {limit}"
    )
    rows = conn.execute(sql).fetchall()
    return {
        "trends": [_row_to_dict(r) for r in rows],
        "count": len(rows),
        "source": "ticket_index (live aggregation — enriched_trends empty)",
    }


def _query_baselines(conn, args: dict) -> dict:
    """Query TRC baselines."""
    rows = conn.execute(
        "SELECT * FROM trc_baselines ORDER BY rowid DESC LIMIT 20"
    ).fetchall()
    return {"baselines": [_row_to_dict(r) for r in rows], "count": len(rows)}


def _query_friction_distribution(conn, args: dict) -> dict:
    """Aggregate ticket_index.friction_type for product-bug / friction-pattern questions.

    Args:
        top_k: int (default 10, max 20) — top N friction types
        trc / trc_code: substring filter on TRC (mirrors F-2b semantics)
        payer: substring filter on insurance_payer
        date_range: 'YYYY-MM-DD/YYYY-MM-DD'
        cross_dim: 'trc' | 'payer' | None — when set, each row gets a nested
                   per-{cross_dim} breakdown
        friction_type: filter to a single friction type (partial-gate via
                       canonical_taxonomy → coerce or warn)

    Returns:
        {
          "friction_distribution": [
              {"friction_type": "incorrect_charge", "ticket_count": 423,
               "pct_of_total": 0.237, "by_<cross_dim>": [...]},
              ...
          ],
          "scope": {"total_tickets": N, "filters": {...}},
          "source": "ticket_index.friction_type",
          "canonical_friction_types": [...12 known...],
          "gate_warning": "..."   # only if filter value was novel
        }

    Added bug-bash 2026-04-23 (R-2). Existing tools could not produce a
    raw friction-type aggregate — Gemini had to fall back on
    semantic_search of "product bug" which hit only 10 tickets,
    producing dramatic undercounts (Q12: reported 3 vs ground truth 280).
    """
    from src.data.chat_tools.canonical_taxonomy import (
        CANONICAL_FRICTION_TYPES,
        coerce_or_warn,
    )

    top_k = max(1, min(int(args.get("top_k", 10)), 20))
    cross_dim = args.get("cross_dim")
    if cross_dim is not None and cross_dim not in ("trc", "payer"):
        return {
            "error": (
                f"Unsupported cross_dim={cross_dim!r}. Valid: 'trc', 'payer', or omit."
            )
        }

    # Partial-gate the friction_type filter
    friction_filter, gate_warning = coerce_or_warn(
        args.get("friction_type"),
        CANONICAL_FRICTION_TYPES,
        column_name="friction_type",
    )

    # Build filter clauses (shared between scope + breakdown queries)
    where_parts: list[str] = ["friction_type IS NOT NULL", "friction_type != ''"]
    params: list = []
    filters_applied: dict = {}

    trc = args.get("trc") or args.get("trc_code")
    if trc:
        where_parts.append(
            "(LOWER(trc_code) LIKE LOWER(?) OR LOWER(COALESCE(trc_label,'')) LIKE LOWER(?))"
        )
        like = f"%{trc}%"
        params.extend([like, like])
        filters_applied["trc"] = trc

    payer = args.get("payer")
    if payer:
        where_parts.append("LOWER(COALESCE(insurance_payer,'')) LIKE LOWER(?)")
        params.append(f"%{payer}%")
        filters_applied["payer"] = payer

    date_range = args.get("date_range")
    if date_range and "/" in date_range:
        try:
            start, end = date_range.split("/", 1)
            where_parts.append("SUBSTR(ticket_created_date, 1, 10) BETWEEN ? AND ?")
            params.extend([start.strip(), end.strip()])
            filters_applied["date_range"] = date_range
        except ValueError:
            pass

    if friction_filter:
        where_parts.append("friction_type = ?")
        params.append(friction_filter)
        filters_applied["friction_type"] = friction_filter

    where_clause = " AND ".join(where_parts)

    # Scope total — same WHERE without the friction_type filter (so pct_of_total
    # is meaningful even when friction_type filter is applied: "X is N% of all
    # in-scope tickets")
    scope_where = " AND ".join(
        p for p in where_parts if not p.startswith("friction_type = ?")
    )
    scope_params = [p for i, p in enumerate(params)
                    if not (where_parts[len(where_parts) - 1].startswith("friction_type = ?")
                            and i == len(params) - 1)]
    # Simpler: just rebuild without the friction_type clause
    scope_where_parts = [p for p in where_parts if not p.startswith("friction_type = ?")]
    scope_where = " AND ".join(scope_where_parts) if scope_where_parts else "1=1"
    scope_params_clean = list(params[:len(scope_params)]) if friction_filter else list(params)
    # If friction_type filter present, drop its trailing param
    if friction_filter:
        scope_params_clean = list(params[:-1])
    else:
        scope_params_clean = list(params)

    total = conn.execute(
        f"SELECT COUNT(*) FROM ticket_index WHERE {scope_where}",
        scope_params_clean,
    ).fetchone()[0] or 0

    # Top-K friction types
    rows = conn.execute(
        f"""SELECT friction_type, COUNT(*) AS ticket_count
            FROM ticket_index WHERE {where_clause}
            GROUP BY friction_type
            ORDER BY ticket_count DESC LIMIT ?""",
        params + [top_k],
    ).fetchall()

    distribution: list[dict] = []
    for r in rows:
        d = _row_to_dict(r)
        d["pct_of_total"] = round((d["ticket_count"] / total) if total else 0.0, 4)
        distribution.append(d)

    # Cross-dim breakdown (one extra query, scoped to the top_k friction types
    # we just returned)
    if cross_dim and distribution:
        cross_col = "trc_code" if cross_dim == "trc" else "insurance_payer"
        ftype_list = [d["friction_type"] for d in distribution]
        ftype_placeholders = ",".join("?" * len(ftype_list))
        cross_where = where_clause + (
            f" AND friction_type IN ({ftype_placeholders}) AND {cross_col} IS NOT NULL"
        )
        cross_rows = conn.execute(
            f"""SELECT friction_type, {cross_col} AS dim_value, COUNT(*) AS n
                FROM ticket_index
                WHERE {cross_where}
                GROUP BY friction_type, {cross_col}
                ORDER BY friction_type, n DESC""",
            params + ftype_list,
        ).fetchall()
        # Nest: friction_type -> [{dim_value, n}, ...] (top 5 per type)
        nested: dict[str, list[dict]] = {f: [] for f in ftype_list}
        for cr in cross_rows:
            cd = _row_to_dict(cr)
            ft = cd.pop("friction_type")
            if len(nested[ft]) < 5:
                nested[ft].append({cross_dim: cd["dim_value"], "n": cd["n"]})
        for d in distribution:
            d[f"by_{cross_dim}"] = nested.get(d["friction_type"], [])

    result: dict = {
        "friction_distribution": distribution,
        "scope": {
            "total_tickets": int(total),
            "filters": filters_applied,
        },
        "source": "ticket_index.friction_type",
        "canonical_friction_types": list(CANONICAL_FRICTION_TYPES),
    }
    if gate_warning:
        result["gate_warning"] = gate_warning
    return result


def _query_csat_stats(conn, args: dict) -> dict:
    """CSAT bucket breakdown (added 2026-04-23 F-6).

    Returns counts for low (1-2), mid (3), high (4-5), and null buckets
    plus the weighted mean across non-null scores."""
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN csat_score IS NULL THEN 1 ELSE 0 END) AS null_count,
             SUM(CASE WHEN csat_score IS NOT NULL AND csat_score <= 2 THEN 1 ELSE 0 END) AS low,
             SUM(CASE WHEN csat_score = 3 THEN 1 ELSE 0 END) AS mid,
             SUM(CASE WHEN csat_score >= 4 THEN 1 ELSE 0 END) AS high,
             COUNT(*) AS total,
             AVG(csat_score) AS mean_score
           FROM tickets"""
    ).fetchone()
    d = _row_to_dict(row)
    scored = (d.get("low") or 0) + (d.get("mid") or 0) + (d.get("high") or 0)
    d["scored_count"] = scored
    d["null_rate"] = round((d.get("null_count") or 0) / max(d.get("total") or 1, 1), 4)
    return {"csat": d, "source": "tickets.csat_score"}


# ═══════════════════════════════════════
#  LEGACY TOOL WRAPPERS (backward-compat)
# ═══════════════════════════════════════
# These preserve the old tool signatures so existing prompts keep working.
# They delegate to the same DB queries as the original chat_engine handlers.


def handle_legacy_query_tickets(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: query_tickets — maps old args to build_filter_query."""
    filters = dict(session_filters)
    if args.get("trc"):
        filters["trc_codes"] = [args["trc"]]
    if args.get("friction_type"):
        filters["friction_types"] = [args["friction_type"]]
    if args.get("from"):
        filters["date_start"] = args["from"]
    if args.get("to"):
        filters["date_end"] = args["to"]
    if args.get("keyword"):
        filters["keyword"] = args["keyword"]

    limit = min(args.get("limit", 10), 50)

    sql, params = build_filter_query(
        filters=filters,
        select_columns=[
            "ti.ticket_id", "ti.trc_code", "ti.friction_type",
            "ti.sub_pattern", "ti.sentiment_polarity",
            "ti.anomaly_flag", "ti.issue_snippet",
            "ti.ticket_created_date",
        ],
        base_table="ticket_index",
        order_by="ti.ticket_created_date DESC",
        limit=limit,
    )
    rows = conn.execute(sql, params).fetchall()
    return {"tickets": [_row_to_dict(r) for r in rows], "count": len(rows)}


def handle_legacy_ticket_detail(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: ticket_detail — single ticket lookup."""
    ticket_id = args.get("id", "")
    row = conn.execute(
        "SELECT * FROM ticket_index WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
    return _row_to_dict(row) if row else {"error": f"Ticket {ticket_id} not found"}


def handle_legacy_query_trends(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: query_trends — monthly volume grouped by TRC."""
    months = args.get("months", 3)
    rows = conn.execute("""
        SELECT trc_code, friction_type,
               COUNT(*) as ticket_count,
               SUBSTR(ticket_created_date, 1, 7) as month
        FROM ticket_index
        WHERE ticket_created_date >= date('now', ? || ' months')
        GROUP BY trc_code, friction_type, month
        ORDER BY month DESC, ticket_count DESC LIMIT 100
    """, (f"-{months}",)).fetchall()
    return {"trends": [_row_to_dict(r) for r in rows]}


def handle_legacy_query_anomalies(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: query_anomalies."""
    return _query_anomaly_stats(conn, args)


def handle_legacy_compare_periods(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: compare_periods."""
    pa = args.get("period_a", "")
    pb = args.get("period_b", "")
    results = {}
    for label, period in [("period_a", pa), ("period_b", pb)]:
        parts = period.split(":")
        if len(parts) != 2:
            results[label] = {"error": f"Invalid format: {period}. Use start:end"}
            continue
        start, end = parts
        row = conn.execute(
            "SELECT COUNT(*) as total, COUNT(DISTINCT trc_code) as trcs "
            "FROM ticket_index WHERE ticket_created_date >= ? "
            "AND ticket_created_date <= ?",
            (start, end),
        ).fetchone()
        results[label] = _row_to_dict(row)
    return results


def handle_legacy_query_insights(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: query_insights."""
    try:
        status = args.get("status", "active")
        rows = conn.execute(
            "SELECT * FROM insight_ledger WHERE status = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (status,),
        ).fetchall()
        return {"insights": [_row_to_dict(r) for r in rows], "count": len(rows)}
    except Exception:
        return {"insights": [], "count": 0, "note": "insight_ledger not available"}


def handle_legacy_search_conversations(conn, args: dict, session_filters: dict) -> dict:
    """Legacy: search_conversations via FTS5 (routed to warehouse)."""
    from src.data.source_registry import SourceRegistry
    from src.data.warehouse_query import WarehouseQuery
    registry = SourceRegistry(conn)
    wq = WarehouseQuery(conn, registry)

    query = args.get("query", "")
    limit = min(args.get("limit", 10), 20)
    if not query:
        return {"error": "query arg is required"}
    try:
        # Use warehouse FTS search
        results = wq.search_fts(query, limit=limit)
        return {"conversations": results, "count": len(results)}
    except Exception:
        # Fallback to LIKE search across warehouse
        rows = wq.query_conversations_raw("""
            SELECT ticket_id, subject, trc_code, status,
                   created_at, message_count, thread_preview
            FROM {table} WHERE full_thread LIKE ? OR subject LIKE ?
            ORDER BY created_at DESC LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit))
        _cols = ["ticket_id", "subject", "trc_code", "status", "created_at", "message_count", "thread_preview"]
        return {"conversations": [dict(zip(_cols, r)) for r in rows], "count": len(rows)}


# ═══════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row or tuple to dict."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    # Fallback for plain tuples (when row_factory not set)
    return {f"col_{i}": v for i, v in enumerate(row)}


# ═══════════════════════════════════════
#  ENTITY TOOLS (Session 3 — Addendum)
# ═══════════════════════════════════════

def handle_query_entities(conn, args: dict, session_filters: dict) -> dict:
    """Query tickets by entity (payer, product_area, feature).

    Args:
        entity_type: str — required
        entity_value: str — optional (if omitted and list_values=True, returns distribution)
        list_values: bool — if True, returns entity distribution
    """
    entity_type = args.get("entity_type")
    if not entity_type:
        return {"error": "entity_type is required"}

    list_values = args.get("list_values", False)

    # Check table exists
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='ticket_entities_normalized'"
    ).fetchone()
    if not exists:
        return {"error": "Entity normalization table not yet created. Run a scan first."}

    if list_values:
        from src.data.entity_normalizer import get_entity_distribution
        dist = get_entity_distribution(conn, entity_type)
        return {
            "entity_type": entity_type,
            "values": [{"value": v, "count": c} for v, c in dist[:50]],
            "total_unique": len(dist),
        }

    entity_value = args.get("entity_value")
    if not entity_value:
        return {"error": "entity_value is required when list_values is False"}

    from src.data.entity_normalizer import query_by_entity
    ticket_ids = query_by_entity(conn, entity_type, entity_value)

    # Return enriched ticket data + classification breakdown so the model
    # can answer directly without needing a second tool call
    tickets = []
    trc_counts = {}
    friction_counts = {}
    if ticket_ids:
        placeholders = ",".join(["?"] * min(len(ticket_ids), 50))
        rows = conn.execute(
            f"SELECT ticket_id, trc_code, trc_label, friction_type, "
            f"sub_pattern, sentiment_polarity, issue_snippet, "
            f"ticket_created_date "
            f"FROM ticket_index WHERE ticket_id IN ({placeholders}) "
            f"ORDER BY ticket_created_date DESC",
            ticket_ids[:50],
        ).fetchall()
        tickets = [_row_to_dict(r) for r in rows]

        # Pre-compute classification breakdowns so model doesn't need
        # a separate query_ticket_classifications call
        for t in tickets:
            trc = t.get("trc_code", "Unknown")
            friction = t.get("friction_type", "Unknown")
            trc_counts[trc] = trc_counts.get(trc, 0) + 1
            friction_counts[friction] = friction_counts.get(friction, 0) + 1

    return {
        "entity_type": entity_type,
        "entity_value": entity_value,
        "ticket_ids": ticket_ids[:100],
        "total_matches": len(ticket_ids),
        "tickets": tickets,
        "trc_breakdown": sorted(trc_counts.items(), key=lambda x: -x[1]),
        "friction_breakdown": sorted(friction_counts.items(), key=lambda x: -x[1]),
    }
