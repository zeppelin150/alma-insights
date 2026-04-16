"""
Alma Insights — A/B Report Pipeline (Phase 5.5B)

Multi-phase A/B comparison pipeline modeled after AIReportPipeline.
Replaces the single-shot Gemini call in ABComparePage with:
  Phase 1: Dual data assembly (stats_a, stats_b, comparison)
  Phase 2: Gemini-powered comparison narrative
  Phase 3: Append tech summary sections

Graceful degradation: If Gemini fails, returns statistical comparison only.

Usage:
    pipeline = ABReportPipeline(db)
    result = pipeline.run(prompt_data, config)
    # result = {
    #   "report_md": str,       # full comparison report
    #   "tech_summary_md": str, # technical process summary
    #   "data_block": str,      # raw comparison data
    #   "stats_a": dict,        # dataset A stats
    #   "stats_b": dict,        # dataset B stats
    #   "comparison": dict,     # statistical comparison results
    #   "phases_completed": list,
    # }
"""
from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger("alma.ab_report_pipeline")


class ABReportPipeline:
    """Multi-phase A/B comparison pipeline."""

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

    def run(self, prompt_data: dict, config: dict) -> dict:
        """Execute the full A/B comparison pipeline.

        Args:
            prompt_data: dict with 'prompt_text' and 'system_prompt'
            config: dict with dataset_a_id, dataset_b_id,
                    date_start_a, date_end_a, date_start_b, date_end_b

        Returns:
            dict with report_md, tech_summary_md, data_block, stats_a, stats_b,
            comparison, phases_completed
        """
        t0 = time.time()
        result = {
            "report_md": "",
            "tech_summary_md": "",
            "data_block": "",
            "stats_a": {},
            "stats_b": {},
            "comparison": {},
            "phases_completed": [],
            "error": None,
        }

        # ── Phase 1: Dual Data Assembly ──
        self._progress("Phase 1: Computing Dataset A statistics...")
        try:
            stats_a, stats_b, comparison, data_block_text = (
                self._phase1_dual_assembly(config)
            )
            result["stats_a"] = stats_a
            result["stats_b"] = stats_b
            result["comparison"] = comparison
            result["data_block"] = data_block_text
            result["phases_completed"].append("dual_data_assembly")
        except Exception as e:
            logger.error(f"Phase 1 failed: {e}")
            result["error"] = f"Data assembly failed: {e}"
            return result

        # ── Phase 1b: Tech Summary ──
        self._progress("Phase 1b: Gathering technical metrics...")
        try:
            result["tech_summary_md"] = self._gather_tech_summary()
            result["phases_completed"].append("tech_summary")
        except Exception as e:
            logger.debug(f"Phase 1b partial failure (non-critical): {e}")

        # ── Phase 2: Gemini Comparison Narrative ──
        if self.client is None:
            result["report_md"] = self._build_stats_only_report(
                stats_a, stats_b, comparison
            )
            result["phases_completed"].append("stats_only_report")
            return result

        self._progress("Phase 2: Generating comparison analysis via Gemini...")
        try:
            report_text = self._phase2_generate(prompt_data, data_block_text)
            result["report_md"] = report_text
            result["phases_completed"].append("gemini_generation")
        except Exception as e:
            logger.error(f"Phase 2 failed: {e}")
            result["report_md"] = self._build_stats_only_report(
                stats_a, stats_b, comparison
            )
            result["error"] = f"Gemini generation failed (stats-only fallback): {e}"
            result["phases_completed"].append("gemini_fallback")

        # ── Phase 3: Assemble Final Report ──
        self._progress("Phase 3: Assembling final report...")
        result["report_md"] = self._phase3_assemble(result)
        result["phases_completed"].append("assembly")

        elapsed = time.time() - t0
        logger.info(
            f"ABReportPipeline completed in {elapsed:.1f}s, "
            f"phases: {result['phases_completed']}"
        )
        return result

    # ══════════════════════════════════════════════════════════════════
    # Phase implementations
    # ══════════════════════════════════════════════════════════════════

    def _phase1_dual_assembly(self, config):
        """Compute stats for both datasets and run statistical comparison."""
        from src.data.ab_analysis import (
            compute_dataset_stats, compare_datasets, build_ab_data_block,
        )
        from src.data.report_builder import format_data_block_for_prompt

        self._progress("Phase 1a: Computing Dataset A statistics...")
        stats_a = compute_dataset_stats(
            self.db, config["dataset_a_id"],
            config.get("date_start_a", ""),
            config.get("date_end_a", ""),
        )

        self._progress("Phase 1b: Computing Dataset B statistics...")
        stats_b = compute_dataset_stats(
            self.db, config["dataset_b_id"],
            config.get("date_start_b", ""),
            config.get("date_end_b", ""),
        )

        self._progress("Phase 1c: Running statistical comparisons...")
        comparison = compare_datasets(stats_a, stats_b)

        self._progress("Phase 1d: Building comparison data block...")
        block = build_ab_data_block(stats_a, stats_b, comparison)
        text = format_data_block_for_prompt(block)

        return stats_a, stats_b, comparison, text

    def _phase2_generate(self, prompt_data, data_block_text):
        """Send comparison prompt to Gemini."""
        from src.data.report_builder import _validate_prompt_before_send

        prompt_text = prompt_data.get("prompt_text", "")
        # Replace {data_block} placeholder with actual data
        prompt = prompt_text.replace("{data_block}", data_block_text)

        _validate_prompt_before_send(prompt)

        system_prompt = prompt_data.get("system_prompt", "")
        return self.client.generate(prompt, system_prompt=system_prompt)

    def _phase3_assemble(self, result):
        """Combine report + tech summary into final markdown."""
        sections = []

        # Main report
        if result["report_md"]:
            sections.append(result["report_md"])

        # Technical summary section
        if result["tech_summary_md"]:
            sections.append("\n---\n")
            sections.append(result["tech_summary_md"])

        return "\n".join(sections)

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _gather_tech_summary(self) -> str:
        """Get formatted technical process summary from latest scan."""
        try:
            from src.data.tech_summary_builder import (
                build_tech_summary, format_tech_summary_as_markdown,
            )
            row = self.db.conn.execute(
                "SELECT scan_id FROM nlp_scan_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return ""
            summary = build_tech_summary(self.db, scan_id=row["scan_id"])
            return format_tech_summary_as_markdown(summary)
        except Exception as e:
            logger.debug(f"Tech summary unavailable: {e}")
            return ""

    def _build_stats_only_report(self, stats_a, stats_b, comparison) -> str:
        """Fallback report when Gemini is unavailable — stats-only."""
        lines = [
            "## A/B Comparison Results (Gemini Unavailable)\n",
            "Statistical comparison completed. Gemini narrative unavailable.\n",
            "### Dataset Summary\n",
            "| Metric | Dataset A | Dataset B |",
            "|--------|-----------|-----------|",
            f"| Tickets | {stats_a.get('ticket_count', '?')} | {stats_b.get('ticket_count', '?')} |",
        ]

        # Add date ranges
        dr_a = stats_a.get("date_range", "?")
        dr_b = stats_b.get("date_range", "?")
        lines.append(f"| Date Range | {dr_a} | {dr_b} |")

        # Statistical tests summary
        test_results = comparison.get("statistical_tests", {})
        if test_results:
            lines.append("\n### Statistical Tests\n")
            lines.append("| Test | Statistic | p-value | Significant |")
            lines.append("|------|-----------|---------|-------------|")
            for name, test in test_results.items():
                if isinstance(test, dict):
                    stat = test.get("statistic", "—")
                    p = test.get("p_value", "—")
                    sig = "Yes" if isinstance(p, (int, float)) and p < 0.05 else "No"
                    stat_str = f"{stat:.4f}" if isinstance(stat, float) else str(stat)
                    p_str = f"{p:.4f}" if isinstance(p, float) else str(p)
                    lines.append(f"| {name} | {stat_str} | {p_str} | {sig} |")

        lines.append(
            "\n*Configure Gemini in Settings to enable AI-powered comparison narrative.*"
        )
        return "\n".join(lines)
