"""
Alma Insights — alma_query CLI Tool (Build 11.0)

Argparse-based query interface that Gemini invokes via run_shell_command.
Queries the persistent ticket_index and analytical tables.
Returns JSON to stdout.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from src.data.connection_factory import get_connection

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "local_warehouse.db"


def _get_conn(db_path=None):
    path = db_path or str(DB_PATH)
    return get_connection(path)


def _json_out(data):
    print(json.dumps(data, indent=2, default=str))


# ═══════════════════════════════════════
#  SUBCOMMAND HANDLERS
# ═══════════════════════════════════════

def cmd_tickets(args, conn):
    """Filtered ticket retrieval from ticket_index."""
    conditions = []
    params = []

    if args.trc:
        conditions.append("trc_code = ?")
        params.append(args.trc)
    if args.friction_type:
        conditions.append("friction_type = ?")
        params.append(args.friction_type)
    if args.sub_pattern:
        conditions.append("sub_pattern = ?")
        params.append(args.sub_pattern)
    if getattr(args, "from", None):
        conditions.append("ticket_created_date >= ?")
        params.append(getattr(args, "from"))
    if args.to:
        conditions.append("ticket_created_date <= ?")
        params.append(args.to)
    if args.sentiment:
        conditions.append("sentiment_polarity = ?")
        params.append(args.sentiment)
    if args.anomaly_flag:
        conditions.append("anomaly_flag = ?")
        params.append(args.anomaly_flag)
    if args.keyword:
        conditions.append(
            "(issue_snippet LIKE ? OR subject_sanitized LIKE ? OR key_phrases LIKE ?)"
        )
        kw = f"%{args.keyword}%"
        params.extend([kw, kw, kw])

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    sort_map = {"date": "ticket_created_date", "sentiment": "sentiment_intensity", "csat": "csat_score"}
    order = sort_map.get(args.sort, "ticket_created_date")

    limit = min(args.limit, 100)

    rows = conn.execute(
        f"""SELECT ticket_id, trc_code, trc_label, subject_sanitized, issue_snippet,
                   friction_type, sub_pattern, sentiment_polarity, sentiment_intensity,
                   csat_score, anomaly_flag, anomaly_reason, ticket_created_date,
                   message_count, classification_method
            FROM ticket_index{where}
            ORDER BY {order} DESC LIMIT ?""",
        params + [limit],
    ).fetchall()

    _json_out([dict(r) for r in rows])


def cmd_ticket_detail(args, conn):
    """Full detail for one ticket."""
    row = conn.execute(
        "SELECT * FROM ticket_index WHERE ticket_id = ?", (args.id,)
    ).fetchone()

    if not row:
        _json_out({"error": f"Ticket {args.id} not found"})
        return

    result = dict(row)

    # Try to get full thread from warehouse
    try:
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        _wq = WarehouseQuery(conn, SourceRegistry(conn))
        _ft = _wq.get_full_threads([args.id])
        if _ft.get(args.id):
            result["full_thread_available"] = True
            result["full_thread"] = _ft[args.id]
        else:
            result["full_thread_available"] = False
            result["note"] = (
                f"Full conversation not loaded. Re-import data covering "
                f"{result.get('ticket_created_date', 'unknown date')} to view."
            )
    except Exception:
        result["full_thread_available"] = False

    _json_out(result)


def cmd_search(args, conn):
    """Full-text search across ticket content."""
    kw = f"%{args.query}%"

    # Try FTS5 first (via warehouse)
    try:
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        _wq2 = WarehouseQuery(conn, SourceRegistry(conn))
        _fts_results = _wq2.search_fts(args.query, limit=args.limit)
        if _fts_results:
            ids = [r["ticket_id"] for r in _fts_results]
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT ticket_id, trc_code, issue_snippet, friction_type, sub_pattern, "
                f"sentiment_polarity, anomaly_flag, ticket_created_date "
                f"FROM ticket_index WHERE ticket_id IN ({placeholders})",
                ids,
            ).fetchall()
            _json_out([dict(r) for r in rows])
            return
    except Exception:
        pass

    # Fallback to LIKE on ticket_index
    rows = conn.execute(
        """SELECT ticket_id, trc_code, issue_snippet, friction_type, sub_pattern,
                  sentiment_polarity, anomaly_flag, ticket_created_date
           FROM ticket_index
           WHERE issue_snippet LIKE ? OR subject_sanitized LIKE ? OR key_phrases LIKE ?
           LIMIT ?""",
        (kw, kw, kw, args.limit),
    ).fetchall()
    _json_out([dict(r) for r in rows])


def cmd_trends(args, conn):
    """Pre-computed trend data from scan_category_snapshots."""
    metric_col = {
        "volume": "ticket_count",
        "sentiment": "avg_sentiment",
        "csat": "avg_csat",
        "anomaly_rate": "anomaly_count",
    }.get(args.metric, "ticket_count")

    group_col = {
        "trc": "trc",
        "friction_type": "friction_type",
        "sub_pattern": "sub_pattern",
    }.get(args.by, "friction_type")

    conditions = []
    params = []
    if args.direction:
        conditions.append("1=1")  # placeholder — direction filtering on derived data

    rows = conn.execute(
        f"""SELECT {group_col} AS metric_key,
                   SUM({metric_col}) AS total_value,
                   scan_date
            FROM scan_category_snapshots
            GROUP BY {group_col}, scan_date
            ORDER BY scan_date DESC
            LIMIT 200""",
    ).fetchall()

    _json_out([dict(r) for r in rows])


def cmd_anomalies(args, conn):
    """Flagged anomalies from trend_snapshots and ticket_index."""
    conditions = []
    params = []

    if args.severity:
        conditions.append("severity = ?")
        params.append(args.severity)
    if args.engine:
        conditions.append("engine = ?")
        params.append(args.engine)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    limit = min(args.limit, 100)

    rows = conn.execute(
        f"""SELECT snapshot_date, engine, metric_key, metric_value,
                   direction, severity, pct_change, context
            FROM trend_snapshots{where}
            ORDER BY snapshot_date DESC LIMIT ?""",
        params + [limit],
    ).fetchall()

    _json_out([dict(r) for r in rows])


def cmd_compare(args, conn):
    """Period-over-period delta comparison."""
    dimension = {
        "trc": "trc",
        "friction_type": "friction_type",
        "sub_pattern": "sub_pattern",
    }.get(args.dimension, "friction_type")

    # Period A
    a_rows = conn.execute(
        f"""SELECT {dimension} AS dim_value, SUM(ticket_count) AS count_a
            FROM scan_category_snapshots
            WHERE scan_date BETWEEN ? AND ?
            GROUP BY {dimension}""",
        (args.period_a.split(",")[0] if "," in args.period_a else args.period_a,
         args.period_a.split(",")[1] if "," in args.period_a else args.period_a),
    ).fetchall()
    a_map = {r["dim_value"]: r["count_a"] for r in a_rows}

    # Period B
    b_rows = conn.execute(
        f"""SELECT {dimension} AS dim_value, SUM(ticket_count) AS count_b
            FROM scan_category_snapshots
            WHERE scan_date BETWEEN ? AND ?
            GROUP BY {dimension}""",
        (args.period_b.split(",")[0] if "," in args.period_b else args.period_b,
         args.period_b.split(",")[1] if "," in args.period_b else args.period_b),
    ).fetchall()

    results = []
    all_keys = set(a_map.keys()) | {r["dim_value"] for r in b_rows}
    b_map = {r["dim_value"]: r["count_b"] for r in b_rows}

    for key in sorted(all_keys):
        ca = a_map.get(key, 0)
        cb = b_map.get(key, 0)
        delta = cb - ca
        pct = ((delta / ca) * 100) if ca else (100 if cb else 0)
        results.append({
            "dimension_value": key,
            "period_a_count": ca,
            "period_b_count": cb,
            "delta": delta,
            "pct_change": round(pct, 1),
            "direction": "rising" if delta > 0 else ("falling" if delta < 0 else "stable"),
        })

    _json_out(results)


def cmd_insights(args, conn):
    """Query the insight ledger."""
    conditions = []
    params = []

    if args.status:
        conditions.append("status = ?")
        params.append(args.status)
    if args.type:
        conditions.append("insight_type = ?")
        params.append(args.type)
    if args.severity:
        conditions.append("severity = ?")
        params.append(args.severity)
    if args.since:
        conditions.append("date_identified >= ?")
        params.append(args.since)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    rows = conn.execute(
        f"""SELECT insight_id, date_identified, insight_type, title,
                   description, severity, status, supporting_ticket_ids
            FROM insight_ledger{where}
            ORDER BY date_identified DESC LIMIT 50""",
        params,
    ).fetchall()

    _json_out([dict(r) for r in rows])


def cmd_past_analysis(args, conn):
    """Prior report runs from analysis_runs."""
    conditions = []
    params = []

    if args.template:
        conditions.append("prompt_template LIKE ?")
        params.append(f"%{args.template}%")
    if args.trc:
        conditions.append("trc_filter LIKE ?")
        params.append(f"%{args.trc}%")
    if args.since:
        conditions.append("run_date >= ?")
        params.append(args.since)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    limit = min(args.limit, 20)

    rows = conn.execute(
        f"""SELECT run_id, run_date, prompt_template, trc_filter,
                   ticket_count, cost_usd, duration_sec,
                   SUBSTR(output_text, 1, 500) AS output_preview
            FROM analysis_runs{where}
            ORDER BY run_date DESC LIMIT ?""",
        params + [limit],
    ).fetchall()

    _json_out([dict(r) for r in rows])


def cmd_validate(args, conn):
    """Verify a quantitative claim against actual data."""
    import re

    # Extract numbers from the claim
    numbers = re.findall(r"(\d+\.?\d*)", args.claim)
    if not numbers:
        _json_out({"error": "No numbers found in claim"})
        return

    claimed_value = float(numbers[0])

    # Try to determine what metric the claim is about
    claim_lower = args.claim.lower()

    if "ticket" in claim_lower:
        actual = conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0]
        metric = "total_tickets"
    elif "anomal" in claim_lower:
        actual = conn.execute(
            "SELECT COUNT(*) FROM ticket_index WHERE anomaly_flag IS NOT NULL"
        ).fetchone()[0]
        metric = "anomaly_count"
    elif "pattern" in claim_lower:
        actual = conn.execute(
            "SELECT COUNT(DISTINCT sub_pattern) FROM ticket_index"
        ).fetchone()[0]
        metric = "pattern_count"
    else:
        actual = conn.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0]
        metric = "ticket_count_default"

    discrepancy = abs(actual - claimed_value)
    pct = (discrepancy / actual * 100) if actual else 0

    _json_out({
        "claim_valid": pct <= 5,
        "actual_value": actual,
        "expected_value": claimed_value,
        "discrepancy_pct": round(pct, 1),
        "metric": metric,
    })


# ═══════════════════════════════════════
#  MAIN — Argparse setup
# ═══════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="alma_query",
        description="Query Alma Insights persistent data store",
    )
    parser.add_argument("--db", type=str, default=None, help="Database path override")
    sub = parser.add_subparsers(dest="command")

    # tickets
    p = sub.add_parser("tickets", help="Filtered ticket retrieval")
    p.add_argument("--trc", type=str, default=None)
    p.add_argument("--friction-type", type=str, default=None, dest="friction_type")
    p.add_argument("--sub-pattern", type=str, default=None, dest="sub_pattern")
    p.add_argument("--from", type=str, default=None)
    p.add_argument("--to", type=str, default=None)
    p.add_argument("--sentiment", type=str, default=None)
    p.add_argument("--anomaly-flag", type=str, default=None, dest="anomaly_flag")
    p.add_argument("--keyword", type=str, default=None)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--sort", type=str, default="date", choices=["date", "sentiment", "csat"])

    # ticket-detail
    p = sub.add_parser("ticket-detail", help="Full ticket detail")
    p.add_argument("--id", type=str, required=True)

    # search
    p = sub.add_parser("search", help="Full-text search")
    p.add_argument("--query", type=str, required=True)
    p.add_argument("--from", type=str, default=None)
    p.add_argument("--to", type=str, default=None)
    p.add_argument("--limit", type=int, default=20)

    # trends
    p = sub.add_parser("trends", help="Pre-computed trend data")
    p.add_argument("--metric", type=str, default="volume",
                   choices=["volume", "sentiment", "csat", "anomaly_rate"])
    p.add_argument("--by", type=str, default="friction_type",
                   choices=["trc", "friction_type", "sub_pattern"])
    p.add_argument("--months", type=int, default=3)
    p.add_argument("--direction", type=str, default=None)

    # anomalies
    p = sub.add_parser("anomalies", help="Flagged anomalies")
    p.add_argument("--severity", type=str, default=None)
    p.add_argument("--engine", type=str, default=None)
    p.add_argument("--limit", type=int, default=20)

    # compare
    p = sub.add_parser("compare", help="Period-over-period delta")
    p.add_argument("--period-a", type=str, required=True, dest="period_a")
    p.add_argument("--period-b", type=str, required=True, dest="period_b")
    p.add_argument("--dimension", type=str, default="friction_type",
                   choices=["trc", "friction_type", "sub_pattern"])

    # insights
    p = sub.add_parser("insights", help="Query insight ledger")
    p.add_argument("--status", type=str, default=None)
    p.add_argument("--type", type=str, default=None)
    p.add_argument("--severity", type=str, default=None)
    p.add_argument("--since", type=str, default=None)

    # past-analysis
    p = sub.add_parser("past-analysis", help="Prior report runs")
    p.add_argument("--template", type=str, default=None)
    p.add_argument("--trc", type=str, default=None)
    p.add_argument("--since", type=str, default=None)
    p.add_argument("--limit", type=int, default=5)

    # validate
    p = sub.add_parser("validate", help="Verify a quantitative claim")
    p.add_argument("--claim", type=str, required=True)
    p.add_argument("--metric", type=str, default=None)
    p.add_argument("--filters", type=str, default="{}")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    conn = _get_conn(args.db)

    dispatch = {
        "tickets": cmd_tickets,
        "ticket-detail": cmd_ticket_detail,
        "search": cmd_search,
        "trends": cmd_trends,
        "anomalies": cmd_anomalies,
        "compare": cmd_compare,
        "insights": cmd_insights,
        "past-analysis": cmd_past_analysis,
        "validate": cmd_validate,
    }

    try:
        dispatch[args.command](args, conn)
    except Exception as e:
        _json_out({"error": str(e)})
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
