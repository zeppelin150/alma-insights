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
    """Query statistical engine outputs (anomalies, trends, baselines)."""
    stat_type = args.get("stat_type", "anomalies")

    if stat_type == "anomalies":
        return _query_anomaly_stats(conn, args)
    elif stat_type == "trends":
        return _query_enriched_trends(conn, args, session_filters)
    elif stat_type == "baselines":
        return _query_baselines(conn, args)
    else:
        return {"error": f"Unknown stat_type: {stat_type}. Use: anomalies, trends, baselines"}


def _query_anomaly_stats(conn, args: dict) -> dict:
    """Query anomaly flags with optional severity filter."""
    severity = args.get("severity")
    if severity:
        rows = conn.execute(
            "SELECT ticket_id, trc_code, anomaly_flag, anomaly_reason, "
            "ticket_created_date FROM ticket_index "
            "WHERE anomaly_flag = ? ORDER BY ticket_created_date DESC LIMIT 20",
            (severity,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ticket_id, trc_code, anomaly_flag, anomaly_reason, "
            "ticket_created_date FROM ticket_index "
            "WHERE anomaly_flag IS NOT NULL "
            "ORDER BY ticket_created_date DESC LIMIT 20"
        ).fetchall()
    return {"anomalies": [_row_to_dict(r) for r in rows], "count": len(rows)}


def _query_enriched_trends(conn, args: dict, session_filters: dict) -> dict:
    """Query pre-computed enriched trends."""
    dimension = args.get("dimension", "friction_type")
    rows = conn.execute(
        "SELECT dimension, dimension_value, period, ticket_count, "
        "pct_of_total, velocity, trc_breakdown "
        "FROM enriched_trends WHERE dimension = ? "
        "ORDER BY period DESC, ticket_count DESC LIMIT 50",
        (dimension,),
    ).fetchall()
    return {"trends": [_row_to_dict(r) for r in rows], "count": len(rows)}


def _query_baselines(conn, args: dict) -> dict:
    """Query TRC baselines."""
    rows = conn.execute(
        "SELECT * FROM trc_baselines ORDER BY rowid DESC LIMIT 20"
    ).fetchall()
    return {"baselines": [_row_to_dict(r) for r in rows], "count": len(rows)}


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
