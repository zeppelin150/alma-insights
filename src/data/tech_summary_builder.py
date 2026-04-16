"""
Alma Insights — Technical Process Summary Builder (Phase 5.5A)

Aggregates existing DB metrics (gemini_usage, nlp_batches, nlp_scan_runs,
scan_events, probe_history, smart_report_runs) into structured summaries
for display across all reporting pages.

Usage:
    summary = build_tech_summary(db, scan_id="abc-123")
    md_text = format_tech_summary_as_markdown(summary)
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger("alma.tech_summary")


def build_tech_summary(db: object, scan_id: str | None = None,
                       report_run_id: int | None = None) -> dict:
    """Aggregate all existing DB metrics for a given scan or report run.

    Args:
        db: DatabaseManager instance (must have .conn attribute)
        scan_id: NLP scan ID (for scan-related metrics)
        report_run_id: smart_report_runs.run_id (for pipeline metrics)

    Returns dict with keys:
        gemini_usage, scan_stats, batch_breakdown, pipeline_stages,
        cost_breakdown, probe_stats, timing
    """
    conn = db.conn
    result = {
        "gemini_usage": _empty_usage(),
        "scan_stats": None,
        "batch_breakdown": [],
        "pipeline_stages": [],
        "cost_breakdown": {},
        "probe_stats": None,
        "timing": {},
    }

    # ── Gemini usage aggregation ──
    if scan_id:
        result["gemini_usage"] = _get_gemini_usage(conn, scan_id=scan_id)
    elif report_run_id:
        result["gemini_usage"] = _get_gemini_usage_for_run(conn, report_run_id)

    # ── Scan-level stats ──
    if scan_id:
        result["scan_stats"] = _get_scan_stats(conn, scan_id)
        result["batch_breakdown"] = _get_batch_breakdown(conn, scan_id)
        result["pipeline_stages"] = _get_scan_events(conn, scan_id)
        result["probe_stats"] = _get_probe_stats(conn, scan_id)

    # ── Smart report run stats ──
    if report_run_id:
        result["pipeline_stages"] = _get_report_run_stages(conn, report_run_id)

    # ── Cost breakdown ──
    usage = result["gemini_usage"]
    if usage["tokens_in"] or usage["tokens_out"]:
        from src.data.usage_tracker import UsageTracker
        model = usage.get("model", "gemini-2.5-flash")
        input_cost = (usage["tokens_in"] / 1_000_000) * _input_rate(model)
        output_cost = (usage["tokens_out"] / 1_000_000) * _output_rate(model)
        result["cost_breakdown"] = {
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "total": round(input_cost + output_cost, 6),
            "model": model,
        }

    # ── Timing ──
    if result["scan_stats"] and result["scan_stats"].get("created_at"):
        stats = result["scan_stats"]
        try:
            start = datetime.fromisoformat(stats["created_at"])
            end_str = stats.get("completed_at")
            if end_str:
                end = datetime.fromisoformat(end_str)
                delta = end - start
                result["timing"] = {
                    "start": stats["created_at"],
                    "end": end_str,
                    "duration_seconds": int(delta.total_seconds()),
                }
        except (ValueError, TypeError):
            pass

    return result


def format_tech_summary_as_markdown(summary: dict) -> str:
    """Render a tech summary dict as a formatted markdown string."""
    sections = []
    sections.append("## Technical Process Summary\n")

    # ── Overview table ──
    usage = summary.get("gemini_usage", _empty_usage())
    scan = summary.get("scan_stats")
    timing = summary.get("timing", {})
    cost = summary.get("cost_breakdown", {})

    rows = []
    if usage["api_calls"]:
        rows.append(("Total API Calls", f"{usage['api_calls']:,}"))
    if usage["tokens_in"]:
        rows.append(("Input Tokens", f"{usage['tokens_in']:,}"))
    if usage["tokens_out"]:
        rows.append(("Output Tokens", f"{usage['tokens_out']:,}"))
    if cost.get("total"):
        rows.append(("Estimated Cost", f"${cost['total']:.4f}"))
    if cost.get("input_cost"):
        rows.append(("  Input Cost", f"${cost['input_cost']:.4f}"))
    if cost.get("output_cost"):
        rows.append(("  Output Cost", f"${cost['output_cost']:.4f}"))
    if cost.get("model"):
        rows.append(("Model", cost["model"]))
    if timing.get("duration_seconds") is not None:
        rows.append(("Duration", _format_duration(timing["duration_seconds"])))

    if scan:
        if scan.get("batches_total"):
            completed = scan.get("batches_completed", 0)
            total = scan["batches_total"]
            rows.append(("Batches (completed/total)", f"{completed}/{total}"))
        if scan.get("tickets_total"):
            classified = scan.get("tickets_classified", 0)
            total = scan["tickets_total"]
            rows.append(("Tickets (classified/total)", f"{classified}/{total}"))
        if scan.get("findings_count"):
            rows.append(("Findings", f"{scan['findings_count']:,}"))
        if scan.get("critical_findings"):
            rows.append(("Critical Findings", f"{scan['critical_findings']:,}"))

    if rows:
        sections.append("| Metric | Value |")
        sections.append("|--------|-------|")
        for label, value in rows:
            sections.append(f"| {label} | {value} |")
        sections.append("")

    # ── Batch breakdown ──
    batches = summary.get("batch_breakdown", [])
    if batches:
        sections.append("### Batch Breakdown by TRC\n")
        sections.append("| TRC | Batches | Tickets | Avg Latency | Retries |")
        sections.append("|-----|---------|---------|-------------|---------|")
        for b in batches:
            latency = (
                f"{b['avg_latency_ms']:.0f}ms" if b.get("avg_latency_ms") else "—"
            )
            sections.append(
                f"| {b['trc']} | {b['batch_count']} | {b['ticket_count']} "
                f"| {latency} | {b.get('retry_count', 0)} |"
            )
        sections.append("")

    # ── Pipeline stages ──
    stages = summary.get("pipeline_stages", [])
    if stages:
        sections.append("### Pipeline Stages\n")
        sections.append("| Stage | Status | Duration |")
        sections.append("|-------|--------|----------|")
        for s in stages:
            dur = (
                f"{s['duration_ms']}ms" if s.get("duration_ms") else "—"
            )
            sections.append(
                f"| {s['event_type']} | {s['status']} | {dur} |"
            )
        sections.append("")

    # ── Probe stats ──
    probes = summary.get("probe_stats")
    if probes and probes.get("total"):
        sections.append("### Bridge Health\n")
        sections.append("| Metric | Value |")
        sections.append("|--------|-------|")
        sections.append(f"| Total Probes | {probes['total']} |")
        sections.append(f"| Successful | {probes['success']} |")
        sections.append(f"| Failed | {probes['failed']} |")
        if probes.get("avg_latency_ms"):
            sections.append(
                f"| Avg Probe Latency | {probes['avg_latency_ms']:.0f}ms |"
            )
        sections.append("")

    if not rows and not batches and not stages:
        sections.append("*No technical process data available for this run.*\n")

    return "\n".join(sections)


# ══════════════════════════════════════════════════════════════════════
# Internal query helpers
# ══════════════════════════════════════════════════════════════════════

def _empty_usage() -> dict:
    return {
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
        "api_calls": 0, "model": "",
    }


def _get_gemini_usage(conn, scan_id: str) -> dict:
    """Aggregate gemini_usage rows for a scan."""
    try:
        row = conn.execute("""
            SELECT COALESCE(SUM(tokens_in), 0)  AS tokens_in,
                   COALESCE(SUM(tokens_out), 0) AS tokens_out,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COALESCE(SUM(api_calls), 0)  AS api_calls,
                   MAX(model)                    AS model
            FROM gemini_usage WHERE scan_id = ?
        """, (scan_id,)).fetchone()
        if row:
            return dict(row)
    except Exception as e:
        logger.debug(f"gemini_usage query failed: {e}")
    return _empty_usage()


def _get_gemini_usage_for_run(conn, run_id: int) -> dict:
    """Aggregate gemini_usage by matching timestamp to report run window."""
    try:
        run = conn.execute(
            "SELECT started_at, completed_at FROM smart_report_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not run or not run["started_at"]:
            return _empty_usage()
        end = run["completed_at"] or datetime.now().isoformat()
        row = conn.execute("""
            SELECT COALESCE(SUM(tokens_in), 0)  AS tokens_in,
                   COALESCE(SUM(tokens_out), 0) AS tokens_out,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COALESCE(SUM(api_calls), 0)  AS api_calls,
                   MAX(model)                    AS model
            FROM gemini_usage
            WHERE created_at BETWEEN ? AND ?
        """, (run["started_at"], end)).fetchone()
        if row:
            return dict(row)
    except Exception as e:
        logger.debug(f"gemini_usage run query failed: {e}")
    return _empty_usage()


def _get_scan_stats(conn, scan_id: str) -> dict | None:
    """Fetch nlp_scan_runs row for a scan."""
    try:
        row = conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.debug(f"scan_stats query failed: {e}")
    return None


def _get_batch_breakdown(conn, scan_id: str) -> list:
    """Aggregate nlp_batches by TRC for a scan."""
    try:
        rows = conn.execute("""
            SELECT trc,
                   COUNT(*)                        AS batch_count,
                   SUM(ticket_count)               AS ticket_count,
                   AVG(
                       CASE WHEN completed_at IS NOT NULL AND created_at IS NOT NULL
                       THEN (julianday(completed_at) - julianday(created_at)) * 86400000
                       ELSE NULL END
                   )                               AS avg_latency_ms,
                   SUM(CASE WHEN status = 'retried' THEN 1 ELSE 0 END) AS retry_count
            FROM nlp_batches
            WHERE scan_id = ?
            GROUP BY trc
            ORDER BY batch_count DESC
        """, (scan_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"batch_breakdown query failed: {e}")
    return []


def _get_scan_events(conn, scan_id: str) -> list:
    """Fetch scan_events timeline for a scan."""
    try:
        rows = conn.execute("""
            SELECT event_type, status, duration_ms, timestamp
            FROM scan_events
            WHERE scan_id = ?
            ORDER BY timestamp
        """, (scan_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"scan_events query failed: {e}")
    return []


def _get_probe_stats(conn, scan_id: str) -> dict | None:
    """Aggregate probe_history for a scan."""
    try:
        row = conn.execute("""
            SELECT COUNT(*)                           AS total,
                   SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) AS failed,
                   AVG(CASE WHEN status='ok' THEN latency_ms END) AS avg_latency_ms
            FROM probe_history
            WHERE scan_id = ?
        """, (scan_id,)).fetchone()
        if row and row["total"]:
            return dict(row)
    except Exception as e:
        logger.debug(f"probe_stats query failed: {e}")
    return None


def _get_report_run_stages(conn, run_id: int) -> list:
    """Parse smart_report_runs result_details for stage info."""
    try:
        import json
        row = conn.execute(
            "SELECT result_details, config_snapshot FROM smart_report_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row or not row["result_details"]:
            return []
        details = json.loads(row["result_details"])
        if isinstance(details, dict) and "stages" in details:
            return details["stages"]
        if isinstance(details, list):
            return [
                {"event_type": s.get("stage", "unknown"),
                 "status": s.get("status", "unknown"),
                 "duration_ms": s.get("duration_ms")}
                for s in details
            ]
    except Exception as e:
        logger.debug(f"report_run stages query failed: {e}")
    return []


# ── Pricing helpers ──

def _input_rate(model: str) -> float:
    """Input cost per 1M tokens."""
    from src.data.usage_tracker import GEMINI_PLANS, _model_to_plan_key
    plan_key = _model_to_plan_key(model)
    plan = GEMINI_PLANS.get(plan_key, GEMINI_PLANS["flash_2.5"])
    return plan["input_cost_per_1m"]


def _output_rate(model: str) -> float:
    """Output cost per 1M tokens."""
    from src.data.usage_tracker import GEMINI_PLANS, _model_to_plan_key
    plan_key = _model_to_plan_key(model)
    plan = GEMINI_PLANS.get(plan_key, GEMINI_PLANS["flash_2.5"])
    return plan["output_cost_per_1m"]


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
