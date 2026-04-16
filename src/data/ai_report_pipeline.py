"""
Alma Insights — AI Report Pipeline (Phase 5.5B)

Multi-phase report generation modeled after VOCBuilder.
Replaces the single-shot Gemini call in AIReportsPage with:
  Phase 1: Data assembly (data block + tech summary + analyst reports)
  Phase 2: Primary report generation via Gemini
  Phase 3: Append analyst reports + technical summary sections

Graceful degradation: If Gemini fails, returns data-only summary.

Usage:
    pipeline = AIReportPipeline(db)
    result = pipeline.run(prompt_data, date_start, date_end, trc_filter)
    # result = {
    #   "report_md": str,       # full markdown report
    #   "tech_summary_md": str, # technical process summary
    #   "analyst_md": str,      # formatted analyst reports
    #   "data_block": str,      # raw data block text
    #   "phases_completed": list,
    # }
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

logger = logging.getLogger("alma.ai_report_pipeline")


class AIReportPipeline:
    """Multi-phase AI report generation pipeline."""

    def __init__(self, db: object, gemini_client: object | None = None, progress_cb: Callable[[str], None] | None = None):
        """
        Args:
            db: DatabaseManager instance
            gemini_client: GeminiClient or ReportBridgeClient
            progress_cb: callable(str) for progress updates
        """
        self.db = db
        self.client = gemini_client
        self._progress = progress_cb or (lambda msg: None)

    def run(self, prompt_data: dict, date_start: str, date_end: str,
            trc_filter: str | None = None, scan_id: str | None = None,
            source_id: str | None = None) -> dict:
        """Execute the full pipeline.

        Args:
            prompt_data: dict with 'prompt_text' and 'system_prompt'
            date_start: YYYY-MM-DD
            date_end: YYYY-MM-DD
            trc_filter: optional TRC code filter
            scan_id: optional scan_id for tech summary / analyst reports

        Returns:
            dict with report_md, tech_summary_md, analyst_md, data_block, phases_completed
        """
        t0 = time.time()
        result = {
            "report_md": "",
            "tech_summary_md": "",
            "analyst_md": "",
            "data_block": "",
            "phases_completed": [],
            "error": None,
        }

        # ── Phase 1: Data Assembly ──
        self._progress("Phase 1: Building analytics data block...")
        try:
            data_block, data_block_text = self._phase1_data_assembly(
                date_start, date_end, trc_filter, source_id=source_id
            )
            result["data_block"] = data_block_text
            result["phases_completed"].append("data_assembly")
        except Exception as e:
            logger.error(f"Phase 1 failed: {e}")
            result["error"] = f"Data assembly failed: {e}"
            return result

        # ── Phase 1b: Analyst Reports + Tech Summary (parallel-safe) ──
        self._progress("Phase 1b: Gathering analyst reports and tech metrics...")
        try:
            result["analyst_md"] = self._gather_analyst_reports(scan_id)
            result["tech_summary_md"] = self._gather_tech_summary(scan_id)
            result["phases_completed"].append("metadata_assembly")
        except Exception as e:
            logger.debug(f"Phase 1b partial failure (non-critical): {e}")

        # ── Phase 2: Primary Report Generation ──
        if self.client is None:
            result["report_md"] = self._build_data_only_report(data_block_text)
            result["phases_completed"].append("data_only_report")
            return result

        self._progress("Phase 2: Generating report via Gemini...")
        try:
            report_text = self._phase2_generate(prompt_data, data_block)
            result["report_md"] = report_text
            result["phases_completed"].append("gemini_generation")
        except Exception as e:
            logger.error(f"Phase 2 failed: {e}")
            result["report_md"] = self._build_data_only_report(data_block_text)
            result["error"] = f"Gemini generation failed (data-only fallback): {e}"
            result["phases_completed"].append("gemini_fallback")

        # ── Phase 3: Assemble Final Report ──
        self._progress("Phase 3: Assembling final report...")
        result["report_md"] = self._phase3_assemble(result)
        result["phases_completed"].append("assembly")

        elapsed = time.time() - t0
        result["duration_sec"] = round(elapsed, 1)
        logger.info(
            f"AIReportPipeline completed in {elapsed:.1f}s, "
            f"phases: {result['phases_completed']}"
        )
        return result

    # ══════════════════════════════════════════════════════════════════
    # Phase implementations
    # ══════════════════════════════════════════════════════════════════

    def _phase1_data_assembly(self, date_start, date_end, trc_filter, source_id=None):
        """Build the data block from pre-computed analytics."""
        from src.data.report_builder import (
            build_data_block, format_data_block_for_prompt,
        )
        block = build_data_block(
            self.db, date_start, date_end,
            trc_filter=trc_filter or None,
            source_id=source_id,
        )
        text = format_data_block_for_prompt(block)
        return block, text

    def _phase2_generate(self, prompt_data, data_block):
        """Send prompt to Gemini and return the response."""
        from src.data.report_builder import (
            replace_prompt_variables, _validate_prompt_before_send,
        )
        prompt_text = prompt_data.get("prompt_text", "")
        prompt = replace_prompt_variables(prompt_text, data_block)

        _validate_prompt_before_send(prompt)

        system_prompt = prompt_data.get("system_prompt", "")
        return self.client.generate(prompt, system_prompt=system_prompt)

    def _phase3_assemble(self, result):
        """Combine report + analyst reports + tech summary into final markdown."""
        sections = []

        # Main report
        if result["report_md"]:
            sections.append(result["report_md"])

        # Analyst reports section
        if result["analyst_md"]:
            sections.append("\n---\n")
            sections.append(result["analyst_md"])

        # Technical summary section
        if result["tech_summary_md"]:
            sections.append("\n---\n")
            sections.append(result["tech_summary_md"])

        return "\n".join(sections)

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _gather_analyst_reports(self, scan_id=None) -> str:
        """Get formatted analyst reports from latest scan."""
        try:
            from src.data.analyst_report_formatter import get_latest_analyst_summary
            md, _ = get_latest_analyst_summary(self.db, scan_id)
            return md
        except Exception as e:
            logger.debug(f"Analyst reports unavailable: {e}")
            return ""

    def _gather_tech_summary(self, scan_id=None) -> str:
        """Get formatted technical process summary."""
        try:
            from src.data.tech_summary_builder import (
                build_tech_summary, format_tech_summary_as_markdown,
            )
            if not scan_id:
                # Try to find the latest scan
                row = self.db.conn.execute(
                    "SELECT scan_id FROM nlp_scan_runs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    scan_id = row["scan_id"]
            if not scan_id:
                return ""
            summary = build_tech_summary(self.db, scan_id=scan_id)
            return format_tech_summary_as_markdown(summary)
        except Exception as e:
            logger.debug(f"Tech summary unavailable: {e}")
            return ""

    def _build_data_only_report(self, data_block_text: str) -> str:
        """Fallback report when Gemini is unavailable."""
        return (
            "## Data Summary (Gemini Unavailable)\n\n"
            "Report generation requires Gemini, but the analytics data "
            "was assembled successfully.\n\n"
            "### Raw Data Block\n\n"
            f"```\n{data_block_text[:3000]}\n```\n"
        )
