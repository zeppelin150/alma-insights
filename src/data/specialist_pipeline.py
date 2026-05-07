"""AI Reports — Multi-bridge specialist pipeline (R2.1).

Three-specialist fan-out + convergence dispatcher built on top of
`ReportOrchestrator` (src/agents/report_orchestrator.py). Replaces the
single Gemini call in AIReportPipeline when settings
`ai.report_pipeline.kind == "multi_bridge"` (the universal default per
2026-05-06 user spec).

Flow:

    1.  Boot ReportOrchestrator with `bridges` persistent ACP bridges.
    2.  Build 3 specialist prompts (friction / sentiment / anomaly) by
        loading config/prompts/specialist_*.txt and stuffing the data
        block + prompt-author scope text via `replace_prompt_variables`.
    3.  Dispatch all 3 in parallel via `orchestrator.run_parallel()`.
        Returns `{specialist_id: raw_text}`.
    4.  Parse each specialist's output into a partial `Report` via
        `report_parser.parse_report`. Aggregate the findings list.
    5.  Build a convergence prompt that hands the 3 partials to a single
        bridge, asking for the unified executive Report.
    6.  Parse the convergence response into the final `Report`.
    7.  Populate `pipeline_kind="multi_bridge"`, `specialist_count`,
        `bridges_used`, and `cost_usd` (best-effort from rate governor).

Reference: 3.24.26 Updated_AI_Reports_Report_History_Page.png — the
"3-bridge pipeline · 3 specialists" metadata label.

Public API
----------
- `SpecialistPipeline(db, bridges=4, model=None,
   convergence_model=None, progress_cb=None)`
- `SpecialistPipeline.run(prompt_data, data_block, data_block_text,
   title_hint, trc_filter=None) -> Report`

Failure modes (never raises):
- Orchestrator boot fails → returns parse_report fallback Report
- All 3 specialists fail → convergence runs over empty input; harness
  flags `specialist_failed_all`.
- Convergence fails → returns the union of specialist findings (best
  partial) so the UI still renders something useful.

Dependencies
------------
- src.agents.report_orchestrator.ReportOrchestrator
- src.data.report_parser.parse_report
- src.data.report_schema (Report, Finding)
- src.data.report_builder (replace_prompt_variables)
- src.data.db_manager.DB_PATH (orchestrator constructor needs it)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.data.report_parser import parse_report
from src.data.report_schema import Finding, Report

logger = logging.getLogger("alma.specialist_pipeline")


# ──────────────────────────────────────────────────────────────────────
# Specialist definitions
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpecialistDef:
    """Static config for one specialist task."""
    specialist_id: str          # e.g. "friction"
    prompt_file: str            # filename under config/prompts/
    system_prompt: str          # short system message for the bridge
    priority: int = 0           # PriorityQueue ordering; lower = first


SPECIALISTS: tuple[SpecialistDef, ...] = (
    SpecialistDef(
        specialist_id="friction",
        prompt_file="specialist_friction.txt",
        system_prompt=(
            "You are the friction-pattern specialist. Surface the dominant friction "
            "types and the cohorts driving them; do not editorialize on sentiment."
        ),
    ),
    SpecialistDef(
        specialist_id="sentiment",
        prompt_file="specialist_sentiment.txt",
        system_prompt=(
            "You are the sentiment specialist. Quantify net sentiment per cohort and "
            "name the n-grams driving each move; do not duplicate friction commentary."
        ),
    ),
    SpecialistDef(
        specialist_id="anomaly",
        prompt_file="specialist_anomaly.txt",
        system_prompt=(
            "You are the anomaly specialist. Cite Poisson/CUSUM and z-score signals "
            "only; do not invent root causes beyond the data block."
        ),
    ),
)

CONVERGENCE_PROMPT_FILE = "convergence.txt"
CONVERGENCE_SYSTEM_PROMPT = (
    "You are the convergence editor. You receive three specialist drafts and produce "
    "a single unified Report. Deduplicate, rank by impact, drop weakly-supported claims."
)


# ──────────────────────────────────────────────────────────────────────
# SpecialistPipeline
# ──────────────────────────────────────────────────────────────────────

class SpecialistPipeline:
    """Multi-bridge specialist + convergence runner."""

    def __init__(
        self,
        db: object,
        bridges: int = 4,
        model: str | None = None,
        convergence_model: str | None = None,
        progress_cb: Callable[[str], None] | None = None,
        usage_tracker=None,
        report_run_id: str | None = None,
    ) -> None:
        self.db = db
        self.bridges = max(1, int(bridges))
        self.model = model or "gemini-2.5-flash"
        self.convergence_model = convergence_model or self.model
        self._progress = progress_cb or (lambda msg: None)
        self._orch = None  # ReportOrchestrator, lazily constructed
        # Cost tracking — wrapper auto-tags each log_call with our scan_id so
        # AIReportPipeline._rollup_cost can sum exactly the rows that belong
        # to this multi_bridge run.
        self._usage_tracker = usage_tracker
        self._report_run_id = report_run_id or ""

    # ── public ─────────────────────────────────────────────────────

    def run(
        self,
        *,
        prompt_data: dict,
        data_block,
        data_block_text: str,
        title_hint: str = "Report",
        trc_filter: str | None = None,
    ) -> Report:
        """Run all 3 specialists in parallel + converge into one Report."""
        t0 = time.time()
        try:
            specialist_outputs = self._dispatch_specialists(
                data_block, data_block_text, prompt_data, trc_filter,
            )
        except Exception as exc:
            logger.error("specialist dispatch failed: %s", exc)
            return self._failure_report(title_hint, reason=f"dispatch_failed: {exc}")

        partials = self._parse_partials(specialist_outputs, title_hint)
        if not any(p.findings for p in partials.values()):
            logger.warning("all specialists produced no findings")
            return self._merge_partials(partials, title_hint, time.time() - t0,
                                         flag="specialist_failed_all")

        try:
            converged_text = self._run_convergence(partials, prompt_data, data_block_text)
            final = parse_report(
                converged_text, fallback_title=title_hint, pipeline_kind="multi_bridge",
            )
            if not final.findings:
                logger.info("convergence produced no findings; merging partials")
                final = self._merge_partials(partials, title_hint, time.time() - t0)
        except Exception as exc:
            logger.error("convergence failed; merging partials: %s", exc)
            final = self._merge_partials(partials, title_hint, time.time() - t0,
                                          flag=f"convergence_failed: {exc}")

        # Populate metadata
        final.pipeline_kind = "multi_bridge"
        final.specialist_count = len(SPECIALISTS)
        final.bridges_used = self.bridges
        final.duration_sec = round(time.time() - t0, 1)
        final.cost_usd = self._estimate_cost(final, partials)
        return final

    def shutdown(self) -> None:
        """Tear down the orchestrator (best-effort)."""
        if self._orch is not None:
            try:
                self._orch.shutdown()
            except Exception as exc:
                logger.debug("orchestrator shutdown error: %s", exc)
            self._orch = None

    # ── stages ─────────────────────────────────────────────────────

    def _dispatch_specialists(
        self, data_block, data_block_text: str, prompt_data: dict, trc_filter,
    ) -> dict[str, str]:
        """Build + dispatch the 3 specialist tasks. Returns id → raw text."""
        from src.data.report_builder import replace_prompt_variables
        tasks = []
        for spec in SPECIALISTS:
            template = _load_prompt(spec.prompt_file)
            if not template:
                logger.warning("missing prompt template %s", spec.prompt_file)
                continue
            bound_prompt = replace_prompt_variables(
                _stitch_specialist_prompt(template, prompt_data, trc_filter),
                data_block,
            )
            tasks.append({
                "id": spec.specialist_id,
                "prompt": _wrap_with_system(bound_prompt, spec.system_prompt),
                "priority": spec.priority,
                "timeout": _specialist_timeout(),
            })
        if not tasks:
            raise RuntimeError("no specialist prompt templates loaded")

        self._progress(f"Specialists: dispatching {len(tasks)} tasks across "
                        f"{self.bridges} bridges...")
        orch = self._ensure_orchestrator()
        results = orch.run_parallel(tasks, progress_cb=self._on_orch_progress)
        return dict(results)

    def _parse_partials(
        self, specialist_outputs: dict[str, str], title_hint: str,
    ) -> dict[str, Report]:
        """Parse each specialist's text into a partial Report."""
        partials: dict[str, Report] = {}
        for spec in SPECIALISTS:
            raw = specialist_outputs.get(spec.specialist_id, "")
            if not raw or raw.startswith("[Error") or raw.startswith("[Cancelled"):
                logger.warning(
                    "specialist=%s produced no usable output: %s",
                    spec.specialist_id, (raw or "")[:120],
                )
                partials[spec.specialist_id] = Report.empty(
                    title=f"{title_hint} — {spec.specialist_id}",
                )
                continue
            partials[spec.specialist_id] = parse_report(
                raw, fallback_title=f"{title_hint} — {spec.specialist_id}",
                pipeline_kind="specialist",
                report_id=f"r-spec-{spec.specialist_id}",
            )
        return partials

    def _run_convergence(
        self, partials: dict[str, Report], prompt_data: dict, data_block_text: str,
    ) -> str:
        """Single bridge call that converges the 3 partial Reports."""
        self._progress("Converging specialist reports...")
        template = _load_prompt(CONVERGENCE_PROMPT_FILE) or ""
        if not template:
            raise RuntimeError("missing convergence.txt")

        partials_payload = _format_partials_for_convergence(partials)
        prompt = template.replace("{partials}", partials_payload)
        prompt = prompt.replace("{name}", str(prompt_data.get("name", "Report")))
        prompt = prompt.replace("{description}", str(prompt_data.get("description", "")))
        prompt = prompt.replace("{data_block}", (data_block_text or "")[:4000])

        full_prompt = _wrap_with_system(prompt, CONVERGENCE_SYSTEM_PROMPT)
        orch = self._ensure_orchestrator()
        # run_single uses the persistent pool — single call, no PriorityQueue
        return orch.run_single(full_prompt, request_id="convergence",
                                timeout=_convergence_timeout())

    # ── helpers ────────────────────────────────────────────────────

    def _ensure_orchestrator(self):
        """Lazy-boot the orchestrator on first dispatch.

        After boot, attach our scoped usage tracker to each persistent
        ACPBridge so every bridge.call_blocking()/call_streaming() inside
        run_parallel + run_single auto-logs to gemini_usage with our
        report_run_id as scan_id.
        """
        if self._orch is not None:
            return self._orch
        from src.agents.report_orchestrator import ReportOrchestrator
        from src.data.db_manager import DB_PATH
        self._orch = ReportOrchestrator(
            db_path=DB_PATH, model=self.model, num_bridges=self.bridges,
        )
        self._orch.boot()
        if self._usage_tracker is not None:
            for bridge in getattr(self._orch, "_bridges", []) or []:
                try:
                    bridge._usage_tracker = self._usage_tracker
                except Exception:
                    pass
        return self._orch

    def _on_orch_progress(self, msg: str, percent: float | None = None) -> None:
        # Forward orchestrator progress with a "specialists:" prefix
        if percent is not None:
            self._progress(f"Specialists: {msg} ({percent:.0f}%)")
        else:
            self._progress(f"Specialists: {msg}")

    def _failure_report(self, title_hint: str, *, reason: str) -> Report:
        from src.data.report_schema import Severity
        report = Report.empty(title=title_hint)
        report.pipeline_kind = "multi_bridge"
        report.specialist_count = len(SPECIALISTS)
        report.bridges_used = self.bridges
        report.accuracy_flags = [reason]
        report.findings = [Finding(
            finding_id="f-multi-failed",
            title="Multi-bridge pipeline failed",
            summary=f"Could not dispatch specialists: {reason}",
            severity=Severity.INFO,
        )]
        return report

    def _merge_partials(
        self, partials: dict[str, Report], title_hint: str, elapsed: float,
        *, flag: str | None = None,
    ) -> Report:
        """Concatenate partial findings into one Report (convergence-failed path)."""
        merged = Report.empty(title=title_hint)
        merged.pipeline_kind = "multi_bridge"
        merged.specialist_count = len(SPECIALISTS)
        merged.bridges_used = self.bridges
        merged.duration_sec = round(elapsed, 1)
        for spec_id, partial in partials.items():
            for f in partial.findings:
                f.finding_id = f.finding_id or f"f-{spec_id}-fallback"
                merged.findings.append(f)
        if flag:
            merged.accuracy_flags.append(flag)
        merged.executive_summary = (
            "Specialist outputs merged without convergence. Review raw findings; "
            "convergence layer was unavailable."
        )
        return merged

    def _estimate_cost(
        self, final: Report, partials: dict[str, Report],
    ) -> float:
        """Best-effort per-pipeline cost.

        AIReportPipeline._rollup_cost re-queries gemini_usage post-run for
        the authoritative number. Inside SpecialistPipeline we hand back
        whatever the bound tracker reports right now (used for live UI
        progress callbacks, not persistence).
        """
        if self._usage_tracker is None or not self._report_run_id:
            return 0.0
        try:
            row = self._usage_tracker.get_scan_cost(self._report_run_id)
            return float(row.get("cost_usd", 0.0) or 0.0)
        except Exception:
            return 0.0


# ──────────────────────────────────────────────────────────────────────
# Module-level helpers
# ──────────────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template; empty string if missing (caller decides)."""
    path = _PROMPTS_DIR / filename
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _stitch_specialist_prompt(
    template: str, prompt_data: dict, trc_filter: str | None,
) -> str:
    """Apply lightweight substitutions before report_builder takes over.

    Specialist templates can reference {name}, {description}, {trc_filter} —
    fill those in with the user's prompt picker context so the LLM knows
    what kind of report is being assembled.
    """
    return (
        template
        .replace("{name}", str(prompt_data.get("name", "")))
        .replace("{description}", str(prompt_data.get("description", "")))
        .replace("{trc_filter}", trc_filter or "all TRCs")
    )


def _wrap_with_system(prompt: str, system: str) -> str:
    """Same convention as ReportBridgeClient — explicit SYSTEM block."""
    if not system:
        return prompt
    return (
        f"[SYSTEM INSTRUCTIONS]\n{system}\n[END SYSTEM INSTRUCTIONS]\n\n{prompt}"
    )


def _format_partials_for_convergence(partials: dict[str, Report]) -> str:
    """Render the 3 partials as compact markdown for the convergence prompt."""
    blocks: list[str] = []
    for spec_id, partial in partials.items():
        blocks.append(f"## SPECIALIST: {spec_id}\n")
        if partial.executive_summary:
            blocks.append(partial.executive_summary)
            blocks.append("")
        for f in partial.findings:
            blocks.append(f"### {f.title} [{f.severity.value}]")
            blocks.append(f.summary)
            for chip in f.evidence_chips:
                blocks.append(f"- {chip.label}: {chip.value}")
            blocks.append("")
        blocks.append("---\n")
    return "\n".join(blocks)


def _specialist_timeout() -> int:
    """Per-specialist call timeout in seconds (defensive default)."""
    return 480


def _convergence_timeout() -> int:
    return 300
