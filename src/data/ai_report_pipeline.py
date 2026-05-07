"""Alma Insights — AI Report Pipeline (Phase 5.5B + R1.7/R2.4/R3.2 rebuild).

Multi-phase report generation. Replaces the single-shot Gemini call in
AIReportsPage with a structured-output pipeline:

  Phase 1   — Data assembly (data block + tech summary + analyst reports)
  Phase 2   — Primary report generation
                · single_pass:  one Gemini/ACP call (legacy)
                · multi_bridge: N specialists fan out via SpecialistPipeline,
                                converge into one structured report (R2.4)
  Phase 2b  — Parse JSON-fenced output into a `Report` (R1.7)
  Phase 2c  — Run grounding harness on the Report; never blocks (R3.2)
  Phase 3   — Assemble analyst reports + technical summary sections

Reference: 3.24.26 build plan, R1-R5 spec in conversation history (2026-05-06),
docs/AI_REPORTS.md.

Graceful degradation:
- Bridge failure → data-only fallback Report
- Parse failure → legacy markdown carried in `Report.raw_markdown`
- Grounding failure → flag added to `Report.accuracy_flags`, never raises

Usage::

    pipeline = AIReportPipeline(db, gemini_client=client)
    result = pipeline.run(prompt_data, date_start, date_end, trc_filter)
    # result["report"]          → Report (always present)
    # result["report_md"]       → markdown blob (legacy callers)
    # result["analyst_md"]      → analyst-agent summary
    # result["tech_summary_md"] → tech process summary
    # result["data_block"]      → raw data block text
    # result["duration_sec"]    → wall-clock seconds

Public API
----------
- `AIReportPipeline(db, gemini_client=None, progress_cb=None)`
- `AIReportPipeline.run(prompt_data, date_start, date_end, trc_filter=None,
                        scan_id=None, source_id=None) -> dict`

Settings honored
----------------
- `ai.report_pipeline.kind`              — "single_pass" | "multi_bridge" (default)
- `ai.report_pipeline.bridges`           — bridge pool size for multi_bridge
- `ai.report_pipeline.model`             — model override for specialists
- `ai.report_pipeline.convergence_model` — model override for the convergence step

Dependencies
------------
- src.data.report_builder (data block assembly + variable substitution)
- src.data.report_parser  (JSON-fence extraction → Report)
- src.data.report_schema  (Report dataclass)
- src.data.report_grounding (post-parse accuracy harness, lazy import)
- src.data.specialist_pipeline (multi-bridge dispatch, lazy import)
- src.data.settings_manager (pipeline kind + model resolution)
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from src.data.report_parser import parse_report
from src.data.report_schema import Report

logger = logging.getLogger("alma.ai_report_pipeline")


# ──────────────────────────────────────────────────────────────────────
# Settings helpers — module-level so tests can monkeypatch
# ──────────────────────────────────────────────────────────────────────

def _get_pipeline_params() -> dict:
    """Resolve the active `ai.report_pipeline` settings with defaults.

    Universal default per user spec (2026-05-06): multi_bridge for ALL prompts;
    `bridges=4` so the orchestrator pool stays modest on 16 GB M1 prod.
    """
    try:
        from src.data.settings_manager import get_section
        cfg = (get_section("ai", {}) or {}).get("report_pipeline") or {}
    except Exception:
        cfg = {}
    return {
        "kind":              str(cfg.get("kind", "multi_bridge") or "multi_bridge"),
        "bridges":           int(cfg.get("bridges", 4) or 4),
        "model":             (str(cfg.get("model", "")).strip() or None),
        "convergence_model": (str(cfg.get("convergence_model", "")).strip() or None),
    }


# ──────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────

class AIReportPipeline:
    """Multi-phase AI report generation pipeline."""

    def __init__(
        self,
        db: object,
        gemini_client: object | None = None,
        progress_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.db = db
        self.client = gemini_client
        self._progress = progress_cb or (lambda msg: None)

    # ── public ─────────────────────────────────────────────────────

    def run(
        self,
        prompt_data: dict,
        date_start: str,
        date_end: str,
        trc_filter: str | None = None,
        scan_id: str | None = None,
        source_id: str | None = None,
    ) -> dict:
        """Execute the full pipeline. Always returns a result dict — never raises."""
        t0 = time.time()
        result = self._fresh_result()

        # Generate the per-run scope id used by the cost tracker. We mint
        # one even if cost tracking is disabled so downstream callers can
        # always cite report.report_id ↔ gemini_usage.scan_id.
        report_run_id = f"r-{uuid.uuid4().hex[:10]}"
        result["report_run_id"] = report_run_id

        scoped_tracker = self._attach_usage_tracker(report_run_id)

        try:
            data_block, data_block_text = self._phase1(
                date_start, date_end, trc_filter, source_id,
            )
            result["data_block"] = data_block_text
            result["phases_completed"].append("data_assembly")
        except Exception as exc:
            logger.error("Phase 1 failed: %s", exc)
            result["error"] = f"Data assembly failed: {exc}"
            result["report"] = self._data_only_report(data_block_text="", title=prompt_data.get("name", "Report"))
            result["report"].report_id = report_run_id
            return result

        self._phase1b_metadata(result, scan_id)

        report = self._phase2_generate(
            prompt_data, data_block, data_block_text, trc_filter,
            scoped_tracker=scoped_tracker, report_run_id=report_run_id,
        )
        report.report_id = report_run_id
        result["report"] = report
        result["report_md"] = self._compose_report_md(report, result)
        result["phases_completed"].append("structured_parse")

        self._phase2c_ground(report, date_start, date_end, trc_filter)
        result["phases_completed"].append("grounding")

        # Cost rollup — query gemini_usage for everything tagged with our run id
        report.cost_usd = self._rollup_cost(scoped_tracker, report_run_id)

        result["duration_sec"] = round(time.time() - t0, 1)
        report.duration_sec = result["duration_sec"]
        report.scope = self._build_scope(data_block, date_start, date_end, trc_filter)
        logger.info(
            "AIReportPipeline done in %.1fs | run_id=%s | findings=%d | "
            "accuracy=%s | cost=$%.4f | flags=%s",
            result["duration_sec"], report_run_id, len(report.findings),
            report.accuracy_score, report.cost_usd, report.accuracy_flags,
        )
        return result

    # ── phases ─────────────────────────────────────────────────────

    def _phase1(self, date_start, date_end, trc_filter, source_id):
        """Build the data block from pre-computed analytics."""
        self._progress("Phase 1: Building analytics data block...")
        from src.data.report_builder import (
            build_data_block, format_data_block_for_prompt,
        )
        block = build_data_block(
            self.db, date_start, date_end,
            trc_filter=trc_filter or None, source_id=source_id,
        )
        return block, format_data_block_for_prompt(block)

    def _phase1b_metadata(self, result: dict, scan_id: str | None) -> None:
        """Gather analyst reports + tech summary. Non-critical, swallowed errors."""
        self._progress("Phase 1b: Gathering analyst reports + tech metrics...")
        try:
            result["analyst_md"] = self._gather_analyst_reports(scan_id)
            result["tech_summary_md"] = self._gather_tech_summary(scan_id)
            result["phases_completed"].append("metadata_assembly")
        except Exception as exc:
            logger.debug("Phase 1b partial failure (non-critical): %s", exc)

    def _phase2_generate(
        self, prompt_data: dict, data_block, data_block_text: str, trc_filter,
        *, scoped_tracker=None, report_run_id: str | None = None,
    ) -> Report:
        """Dispatch to single_pass OR multi_bridge based on settings.

        Always returns a Report. On any failure returns a data-only Report
        whose `raw_markdown` carries whatever fallback text we can build.
        """
        params = _get_pipeline_params()
        kind = params["kind"]
        title_hint = str(prompt_data.get("name") or "Report")
        if kind == "multi_bridge":
            return self._phase2_multi(prompt_data, data_block, data_block_text,
                                       title_hint, params, trc_filter,
                                       scoped_tracker=scoped_tracker,
                                       report_run_id=report_run_id)
        return self._phase2_single(prompt_data, data_block, data_block_text,
                                    title_hint, scoped_tracker=scoped_tracker)

    def _phase2_single(
        self, prompt_data: dict, data_block, data_block_text: str, title_hint: str,
        *, scoped_tracker=None,
    ) -> Report:
        """Single Gemini call → parse → Report. Legacy path."""
        self._progress("Phase 2: Generating report (single_pass)...")
        if self.client is None:
            return self._data_only_report(data_block_text, title_hint)
        # Attach scoped tracker so the bridge log_call rows carry our scan_id
        if scoped_tracker is not None and hasattr(self.client, "_usage_tracker"):
            self.client._usage_tracker = scoped_tracker
        try:
            raw = self._call_client(prompt_data, data_block)
        except Exception as exc:
            logger.error("Phase 2 single_pass failed: %s", exc)
            return self._data_only_report(data_block_text, title_hint, error=str(exc))
        return parse_report(raw, fallback_title=title_hint, pipeline_kind="single_pass")

    def _phase2_multi(
        self, prompt_data: dict, data_block, data_block_text: str,
        title_hint: str, params: dict, trc_filter,
        *, scoped_tracker=None, report_run_id: str | None = None,
    ) -> Report:
        """Specialist fan-out + convergence → Report. R2.4 path."""
        self._progress("Phase 2: Running specialist pipeline (multi_bridge)...")
        try:
            from src.data.specialist_pipeline import SpecialistPipeline
        except ImportError as exc:
            logger.warning("multi_bridge unavailable, falling back to single_pass: %s", exc)
            return self._phase2_single(prompt_data, data_block, data_block_text,
                                        title_hint, scoped_tracker=scoped_tracker)
        try:
            specialist = SpecialistPipeline(
                db=self.db,
                bridges=params["bridges"],
                model=params["model"],
                convergence_model=params["convergence_model"],
                progress_cb=self._progress,
                usage_tracker=scoped_tracker,
                report_run_id=report_run_id,
            )
            return specialist.run(
                prompt_data=prompt_data,
                data_block=data_block,
                data_block_text=data_block_text,
                title_hint=title_hint,
                trc_filter=trc_filter,
            )
        except Exception as exc:
            logger.error("multi_bridge dispatch failed, falling back: %s", exc)
            return self._phase2_single(prompt_data, data_block, data_block_text,
                                        title_hint, scoped_tracker=scoped_tracker)

    def _phase2c_ground(
        self, report: Report, date_start: str, date_end: str, trc_filter,
    ) -> None:
        """Run the grounding harness. Never raises (warn-only per R3.2)."""
        self._progress("Phase 2c: Auditing accuracy...")
        try:
            from src.data.report_grounding import GroundingHarness
        except ImportError:
            logger.debug("report_grounding unavailable; skipping accuracy audit")
            report.accuracy_flags.append("grounding_unavailable")
            return
        try:
            harness = GroundingHarness(
                db=self.db, date_start=date_start, date_end=date_end,
                trc_filter=trc_filter,
            )
            harness.score(report)
        except Exception as exc:
            logger.warning("Grounding harness raised; flagging only: %s", exc)
            report.accuracy_flags.append("grounding_error")

    # ── helpers ────────────────────────────────────────────────────

    def _call_client(self, prompt_data: dict, data_block) -> str:
        """Build prompt + call client.generate(). Validates first.

        Timeout: large data blocks (>1500 tickets) routinely need 3-10 min on
        gemini-2.5-flash. The default of 120s on ReportBridgeClient was hitting
        timeouts in the 1788-ticket warehouse run. We honor `prompt_data["timeout"]`
        when present, otherwise use a safer 600s default for single_pass reports.
        """
        from src.data.report_builder import (
            replace_prompt_variables, _validate_prompt_before_send,
        )
        prompt_text = prompt_data.get("prompt_text", "") or ""
        prompt = replace_prompt_variables(prompt_text, data_block)
        _validate_prompt_before_send(prompt)
        timeout = int(prompt_data.get("timeout") or 600)
        return self.client.generate(
            prompt,
            system_prompt=prompt_data.get("system_prompt", ""),
            timeout=timeout,
        )

    def _gather_analyst_reports(self, scan_id=None) -> str:
        try:
            from src.data.analyst_report_formatter import get_latest_analyst_summary
            md, _ = get_latest_analyst_summary(self.db, scan_id)
            return md
        except Exception as exc:
            logger.debug("Analyst reports unavailable: %s", exc)
            return ""

    def _gather_tech_summary(self, scan_id=None) -> str:
        try:
            from src.data.tech_summary_builder import (
                build_tech_summary, format_tech_summary_as_markdown,
            )
            sid = scan_id or self._latest_scan_id()
            if not sid:
                return ""
            return format_tech_summary_as_markdown(build_tech_summary(self.db, scan_id=sid))
        except Exception as exc:
            logger.debug("Tech summary unavailable: %s", exc)
            return ""

    def _latest_scan_id(self) -> str | None:
        try:
            row = self.db.conn.execute(
                "SELECT scan_id FROM nlp_scan_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return row["scan_id"] if row else None
        except Exception:
            return None

    def _data_only_report(self, data_block_text: str, title: str, error: str | None = None) -> Report:
        """Fallback Report when the LLM is unavailable or crashed.

        Carries a single 'data_only' Finding so the UI still renders something
        useful and the legacy markdown lives in `raw_markdown`.
        """
        from src.data.report_schema import Finding, Severity, EvidenceChip, ChipKind
        body = (
            "## Data Summary (LLM unavailable)\n\n"
            "The analytics block was assembled, but no narrative was produced.\n\n"
            f"```\n{(data_block_text or '')[:3000]}\n```"
        )
        finding = Finding(
            finding_id="f-data-only",
            title="Data summary (LLM unavailable)",
            summary="LLM call failed or was disabled; raw analytics retained.",
            severity=Severity.INFO,
            confidence=0.0,
            evidence_chips=[EvidenceChip(label="Status", value="data-only", kind=ChipKind.SOURCE)],
            body_md=body,
        )
        report = Report.empty(title=title)
        report.findings = [finding]
        report.raw_markdown = body
        report.accuracy_flags = ["data_only" if not error else f"llm_error: {error[:120]}"]
        return report

    def _compose_report_md(self, report: Report, result: dict) -> str:
        """Stitch report.raw_markdown + analyst + tech sections into one blob.

        Preserves the legacy contract: callers that consumed `result['report_md']`
        keep getting a usable markdown string for save .md / save .html / chat.
        If structured findings exist, prefix with a JSON-fence that parsers
        downstream (e.g. follow-up chat) can re-extract.
        """
        sections: list[str] = []
        if report.findings:
            sections.append(_dump_findings_markdown(report))
        elif report.raw_markdown:
            sections.append(report.raw_markdown)
        analyst = result.get("analyst_md") or ""
        tech = result.get("tech_summary_md") or ""
        if analyst:
            sections.append("\n---\n")
            sections.append(analyst)
        if tech:
            sections.append("\n---\n")
            sections.append(tech)
        return "\n".join(sections)

    def _build_scope(self, data_block, date_start: str, date_end: str, trc_filter) -> dict:
        """Best-effort scope dict for the structured Report header chips."""
        ticket_count = self._extract_ticket_count(data_block)
        return {
            "ticket_count": ticket_count,
            "date_range": f"{date_start}/{date_end}",
            "trc_filter": trc_filter or "all",
        }

    def _extract_ticket_count(self, data_block) -> int:
        """Pull a ticket-count int out of report_builder's data block."""
        if isinstance(data_block, dict):
            for key in ("ticket_count", "total_tickets", "tickets"):
                value = data_block.get(key)
                if isinstance(value, int):
                    return value
                if isinstance(value, str) and value.isdigit():
                    return int(value)
        return 0

    def _fresh_result(self) -> dict:
        return {
            "report": None,
            "report_md": "",
            "tech_summary_md": "",
            "analyst_md": "",
            "data_block": "",
            "phases_completed": [],
            "error": None,
            "duration_sec": 0.0,
            "report_run_id": "",
        }

    # ── Cost tracking ──────────────────────────────────────────────

    def _attach_usage_tracker(self, report_run_id: str):
        """Build a ScopedUsageTracker bound to this run.

        Returns None when the db can't host a UsageTracker (e.g. test stubs
        that don't expose `log_gemini_usage`). Pipeline still runs; cost
        rollup just yields 0.0 in that case.
        """
        try:
            from src.data.usage_tracker import UsageTracker
            from src.data.scoped_usage_tracker import ScopedUsageTracker
        except ImportError:
            return None
        try:
            base = UsageTracker(self.db)
            # Smoke-test the underlying log path so we fail-fast on stub DBs
            if not hasattr(self.db, "log_gemini_usage"):
                return None
            return ScopedUsageTracker(base, report_run_id)
        except Exception as exc:
            logger.debug("usage tracker attach failed: %s", exc)
            return None

    def _rollup_cost(self, scoped_tracker, report_run_id: str) -> float:
        """Sum gemini_usage.cost_usd for every row tagged with this run id."""
        if scoped_tracker is None:
            return 0.0
        try:
            scan_cost = scoped_tracker.get_scan_cost(report_run_id)
            return float(scan_cost.get("cost_usd", 0.0) or 0.0)
        except Exception as exc:
            logger.debug("cost rollup failed: %s", exc)
            return 0.0


# ──────────────────────────────────────────────────────────────────────
# Render helpers
# ──────────────────────────────────────────────────────────────────────

def _dump_findings_markdown(report: Report) -> str:
    """Render a Report as readable markdown for save .md / chat / fallback.

    The structured finding cards are the primary UX; this is for callers
    that still want a flat document (file export, follow-up chat context).
    """
    lines: list[str] = [f"# {report.title}", ""]
    if report.executive_summary:
        lines.extend([report.executive_summary, ""])
    for idx, f in enumerate(report.findings, start=1):
        lines.append(f"## {idx}. {f.title}  _[{f.severity.value.upper()}]_ ")
        lines.append(f.summary)
        if f.evidence_chips:
            chips = " · ".join(f"**{c.label}**: {c.value}" for c in f.evidence_chips)
            lines.append("")
            lines.append(chips)
        if f.body_md:
            lines.append("")
            lines.append(f.body_md)
        lines.append("")
    return "\n".join(lines).strip()
