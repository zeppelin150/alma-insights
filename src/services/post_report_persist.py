"""
Alma Insights — Post-Report Persistence Writer (Build 11.0)

Persists every AI report generation to the analysis_runs table.
Called after report output renders in the UI.
"""

import uuid
import logging
from datetime import datetime

logger = logging.getLogger("alma.post_report_persist")


def persist_report_run(run_data: dict, conn) -> str:
    """Insert a report run into analysis_runs.

    Args:
        run_data: Dict with keys matching analysis_runs columns.
                  Required: prompt_template, output_text.
                  Optional: trc_filter, date_start, date_end, ticket_count,
                            model_used, token_count, cost_usd, duration_sec,
                            definition_id, source, prompt_text, output_structured.
        conn:     SQLite connection.

    Returns:
        The generated run_id (UUID string).
    """
    run_id = run_data.get("run_id") or str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    conn.execute(
        """
        INSERT INTO analysis_runs (
            run_id, run_date, prompt_template, prompt_text,
            trc_filter, date_start, date_end, ticket_count,
            model_used, output_text, output_structured,
            token_count, cost_usd, duration_sec,
            definition_id, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            now,
            run_data.get("prompt_template"),
            run_data.get("prompt_text"),
            run_data.get("trc_filter"),
            run_data.get("date_start"),
            run_data.get("date_end"),
            run_data.get("ticket_count"),
            run_data.get("model_used"),
            run_data.get("output_text"),
            run_data.get("output_structured"),
            run_data.get("token_count"),
            run_data.get("cost_usd"),
            run_data.get("duration_sec"),
            run_data.get("definition_id"),
            run_data.get("source", "manual"),
        ),
    )
    conn.commit()
    logger.info("Persisted report run: %s (template=%s)", run_id, run_data.get("prompt_template"))
    return run_id
