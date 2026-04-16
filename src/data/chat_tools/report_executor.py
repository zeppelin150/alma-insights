"""Async report job submission via JobQueue.

Encapsulates the CallableWorker + JobQueue submission for
report generation triggered from chat tools.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def submit_report_job(
    db,
    filters: dict,
    template: str,
    session_id: str | None = None,
    job_queue=None,
) -> dict:
    """Submit a report job to the queue.

    Args:
        db: DatabaseManager instance.
        filters: Merged session + tool filters.
        template: Report template name.
        session_id: Chat session ID for status tracking.
        job_queue: JobQueue instance (if None, returns deferred status).

    Returns:
        Dict with report_id, status, message.
    """
    report_id = f"rpt_{uuid.uuid4().hex[:8]}"

    if job_queue is None:
        return _no_queue_response(report_id)

    try:
        job = _build_job(db, report_id, filters, template)
        job_queue.submit(job)

        # Track in session if provided
        if session_id:
            _track_in_session(db, session_id, report_id)

        return {
            "report_id": report_id,
            "status": "queued",
            "message": (
                f"Report '{template}' queued (id: {report_id}). "
                "Results available via query_report when complete."
            ),
        }
    except Exception as e:
        logger.error("Failed to submit report job: %s", e)
        return {"error": f"Failed to queue report: {e}"}


def _build_job(db, report_id: str, filters: dict, template: str):
    """Build a JobDescriptor for the report pipeline."""
    from src.data.job_queue import JobDescriptor, CallableWorker

    def create_worker():
        return CallableWorker(
            _run_report_pipeline,
            db, report_id, filters, template,
        )

    return JobDescriptor(
        job_id=report_id,
        name=f"Report: {template}",
        description=f"Generating {template} report...",
        create_worker=create_worker,
    )


def _run_report_pipeline(
    db, report_id: str, filters: dict, template: str,
) -> dict:
    """Execute the report pipeline (runs on worker thread)."""
    from src.data.ai_report_pipeline import AIReportPipeline

    date_start = filters.get("date_start", "")
    date_end = filters.get("date_end", "")
    trc_filter = None
    trc_codes = filters.get("trc_codes", [])
    if trc_codes and len(trc_codes) == 1:
        trc_filter = trc_codes[0]

    pipeline = AIReportPipeline(db, gemini_client=None)
    prompt_data = {
        "prompt_text": f"Generate a {template} analysis report.",
        "system_prompt": "You are an analytical report generator.",
    }

    result = pipeline.run(
        prompt_data=prompt_data,
        date_start=date_start,
        date_end=date_end,
        trc_filter=trc_filter,
    )

    # Persist to analysis_runs
    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute(
        "INSERT INTO analysis_runs "
        "(run_id, run_date, prompt_template, trc_filter, "
        "date_start, date_end, output_text, output_structured) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (report_id, now, template, trc_filter,
         date_start, date_end,
         result.get("report_md", ""),
         json.dumps(result.get("data_block", {}))),
    )
    db.commit()

    return {"report_id": report_id, "phases": result.get("phases_completed", [])}


def _track_in_session(db, session_id: str, report_id: str) -> None:
    """Add report_id to the session's active_report_ids."""
    try:
        from src.services.chat_session import add_active_report_id
        conn = getattr(db, "conn", db)
        add_active_report_id(session_id, report_id, conn=conn)
    except Exception as e:
        logger.debug("Failed to track report in session: %s", e)


def _no_queue_response(report_id: str) -> dict:
    """Response when no JobQueue is available."""
    return {
        "report_id": report_id,
        "status": "no_queue",
        "message": "Report queued but no job queue available. Run manually.",
    }
