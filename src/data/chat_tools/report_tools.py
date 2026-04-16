"""Report chat tool handlers.

query_report: Reads existing analysis reports.
run_report: Proposes new reports (Session 2: propose-only, Session 5: async execution).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_VALID_TEMPLATES = {
    "general_trend", "trc_deep_dive", "friction_analysis",
    "anomaly_investigation", "cross_trc_synthesis",
}

_VALID_SECTIONS = {"findings", "summary_stats", "recommendations"}


def handle_query_report(conn, args: dict, session_filters: dict) -> dict:
    """Read a previously generated analysis report.

    If report_id is omitted, returns the latest report.
    If section is provided, returns just that section.
    """
    report_id = args.get("report_id")
    section = args.get("section")

    if section and section not in _VALID_SECTIONS:
        return {"error": f"Invalid section: {section}. Use: {sorted(_VALID_SECTIONS)}"}

    if report_id:
        row = conn.execute(
            "SELECT run_id, run_date, prompt_template, trc_filter, "
            "date_start, date_end, ticket_count, output_text, "
            "output_structured FROM analysis_runs WHERE run_id = ?",
            (report_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT run_id, run_date, prompt_template, trc_filter, "
            "date_start, date_end, ticket_count, output_text, "
            "output_structured FROM analysis_runs "
            "ORDER BY run_date DESC LIMIT 1",
        ).fetchone()

    if row is None:
        return {"error": "No reports found" + (f" for id={report_id}" if report_id else "")}

    result = {
        "report_id": row[0],
        "run_date": row[1],
        "template": row[2],
        "trc_filter": row[3],
        "date_range": f"{row[4] or ''} to {row[5] or ''}",
        "ticket_count": row[6],
    }

    # Parse structured output if available
    structured = _parse_structured(row[8])
    if structured and section:
        result["section"] = section
        result["data"] = structured.get(section, {})
    elif structured:
        result["structured"] = structured
    else:
        # Fall back to raw text output
        result["output_text"] = _truncate_output(row[7] or "")

    return result


def handle_run_report(conn, args: dict, session_filters: dict) -> dict:
    """Propose or trigger a new analysis report.

    Session 2: propose-only. Returns proposed parameters for user confirmation.
    Session 5: will wire async pipeline execution when confirm=True.
    """
    template = args.get("template", "general_trend")
    confirm = args.get("confirm", False)

    if template not in _VALID_TEMPLATES:
        return {"error": f"Invalid template: {template}. Use: {sorted(_VALID_TEMPLATES)}"}

    # Merge session filters with any tool-arg filters
    proposed_filters = dict(session_filters)
    if "filters" in args:
        proposed_filters.update(args["filters"])

    if not confirm:
        return {
            "action": "confirm_required",
            "proposed_params": {
                "template": template,
                "filters": proposed_filters,
            },
            "template": template,
            "message": (
                f"Ready to run a '{template}' report"
                + (f" with filters: {json.dumps(proposed_filters)}" if proposed_filters else "")
                + ". Confirm to proceed."
            ),
        }

    # Session 5: Submit async report job
    from src.data.chat_tools.report_executor import submit_report_job
    session_id = args.get("session_id")
    return submit_report_job(
        db=conn,
        filters=proposed_filters,
        template=template,
        session_id=session_id,
    )


def _parse_structured(raw: str | None) -> dict | None:
    """Parse output_structured JSON, returning None on failure."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _truncate_output(text: str, max_len: int = 6000) -> str:
    """Truncate report output text to prevent oversized responses."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n[... report truncated, use section filter for specific parts]"
