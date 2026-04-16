"""
Alma Insights — VOC Root Cause Analysis Builder (Build 9.0)

Multi-perspective VOC pipeline with batched analysis, pipelined
accumulator, and specialist analysts:

Phase 1 (Batched TRC Analysis): VOCBatchPacker bin-packs TRCs into
  batched Gemini calls (~25-35 batches vs 127 individual). Priority 0.

Phase 2a (Accumulator): 6-8 sequential rounds building an evidence ledger
  progressively. Pipelined into Phase 1 whitespace via priority 1.

Phase 2b (Specialists): Pattern Detector, Novelty Scanner, Friction
  Scorer — injected after Phase 1 completes at priority 0.

Phase 3 (Convergence): Accumulator ledger + 3 specialist reports →
  complete 7-section executive report.

All phases run through a single unified priority dispatch
(ReportOrchestrator.run_parallel with PriorityQueue).
"""

import json
import logging
import random
import re
import threading
import time
import traceback
import yaml
from collections import defaultdict

from src.data.settings_manager import get_section
from datetime import datetime
from math import ceil
from pathlib import Path
from queue import PriorityQueue

logger = logging.getLogger("alma.voc_builder")

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"


class VOCBuilder:
    """Orchestrates the full VOC root cause analysis pipeline."""

    # ── Defaults (overridden by settings.yaml) ──────────────────────
    ANALYSIS_TRUNCATION = 1500     # chars per thread (shorter than scan's 3000)
    MAX_TICKETS_PER_BATCH = 150    # fits in 300K input budget with overhead
    SAMPLE_ALL_THRESHOLD = 200     # TRCs below this: include all tickets
    SAMPLE_MEDIUM_MAX = 200        # sample size for 200-1000 ticket TRCs
    SAMPLE_LARGE_MAX = 300         # sample size for >1000 ticket TRCs

    # Per-ticket overhead in JSONL section (JSON wrapper + field names)
    JSON_OVERHEAD_PER_TICKET = 120

    # Fixed prompt overhead: template + stats + NLP context + instructions
    PROMPT_OVERHEAD_CHARS = 25_000

    # Cost estimates (Gemini Flash 2.5 pricing, approximate)
    EST_INPUT_COST_PER_MCHAR = 0.15   # $/million input chars
    EST_OUTPUT_COST_PER_MCHAR = 0.60  # $/million output chars
    EST_OUTPUT_CHARS_PER_CALL = 5_000  # average analysis response

    # ── Build 9.0: Pipelined architecture constants ──────────────
    ACCUMULATOR_ROUNDS = 8       # configurable via settings.yaml
    ACCUMULATOR_ROUND_TIMEOUT = 180      # seconds per accumulator round
    ACCUMULATOR_ANALYSIS_WAIT = 300      # seconds waiting for TRC analyses
    ACCUMULATOR_RESULT_WAIT = 300        # seconds waiting for round result
    SPECIALIST_TIMEOUT = 600             # seconds per specialist call
    SPECIALIST_RESULT_WAIT = 660         # 600s call + 60s buffer
    SPECIALIST_OVERHEAD = 30_000         # chars of prompt overhead
    CONVERGENCE_INPUT_CAP = 800_000      # chars max for convergence input
    CONVERGENCE_OVERHEAD = 20_000        # chars of convergence prompt overhead
    PHASE1_DONE_TIMEOUT = 1800           # 30 min safety timeout

    def __init__(self, db, gemini_client, progress_callback=None,
                 orchestrator=None):
        self.db = db
        self.gemini = gemini_client
        self._progress = progress_callback or (lambda msg, pct: None)
        self._cancelled = False
        self._orchestrator = orchestrator  # Build 7.0: ReportOrchestrator for parallel Phase 1

        # 9.0 T4: Stat context cache — eliminates redundant trending/incident/correlation calls
        self._stat_context_cache = {}

        # Load settings overrides
        self._load_config()

    def _load_config(self):
        """Read voc_report config from settings.yaml."""
        try:
            voc_cfg = get_section("gemini", {}).get("voc_report", {})
            if voc_cfg:
                self.SAMPLE_ALL_THRESHOLD = voc_cfg.get(
                    "sample_all_threshold", self.SAMPLE_ALL_THRESHOLD)
                self.SAMPLE_MEDIUM_MAX = voc_cfg.get(
                    "sample_medium_max", self.SAMPLE_MEDIUM_MAX)
                self.SAMPLE_LARGE_MAX = voc_cfg.get(
                    "sample_large_max", self.SAMPLE_LARGE_MAX)
                self.ANALYSIS_TRUNCATION = voc_cfg.get(
                    "analysis_truncation", self.ANALYSIS_TRUNCATION)
                self.MAX_TICKETS_PER_BATCH = voc_cfg.get(
                    "max_tickets_per_batch", self.MAX_TICKETS_PER_BATCH)
        except Exception:
            pass  # Use class defaults

    # ═══════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def plan(self, date_start, date_end, trc_filter=None):
        """Dry-run: compute batch plan + cost/time estimate without calling Gemini.

        Returns dict with:
            trc_plans: [{trc, total_tickets, sampled, batches, has_nlp}, ...]
            total_tickets, total_sampled, total_batches, total_gemini_calls
            est_cost_usd, est_time_min, has_nlp_data, model
        """
        return self._build_plan(date_start, date_end, trc_filter)

    def run(self, date_start, date_end, trc_filter=None):
        """Execute full VOC pipeline. Returns {report_text, trc_analyses, stats}.

        Build 9.0: When an orchestrator is available, uses the pipelined
        multi-perspective architecture (batched Phase 1 + accumulator +
        specialists + convergence). Falls back to Build 7.0 sequential
        pipeline otherwise.

        Raises on fatal error. Respects cancel() for clean abort.
        """
        self._cancelled = False
        self._stat_context_cache = {}  # 9.0 T4: Clear cache per run
        t0 = time.time()

        # Phase 0: Build plan
        self._progress("Planning VOC analysis...", 0)
        plan = self._build_plan(date_start, date_end, trc_filter)

        if not plan["trc_plans"]:
            return {
                "report_text": "No tickets found in the specified date range.",
                "trc_analyses": {},
                "stats": plan,
            }

        # ── Build 9.0: Pipelined multi-perspective pipeline ──
        if self._orchestrator:
            try:
                trc_analyses, accumulator_result, specialist_results = (
                    self._run_analysis_and_synthesis(
                        plan, date_start, date_end))

                if self._cancelled:
                    return {
                        "report_text": "VOC analysis cancelled by user.",
                        "trc_analyses": trc_analyses,
                        "stats": plan,
                    }

                # Phase 3: Convergence
                report_text = self._run_convergence(
                    accumulator_result, specialist_results,
                    plan, date_start, date_end,
                )

            except Exception as e:
                logger.error("Build 9.0 pipeline failed, falling back: %s",
                             traceback.format_exc())
                # Fall back to legacy pipeline
                trc_analyses = self._run_analysis_phase(
                    plan, date_start, date_end)
                if not self._cancelled:
                    report_text = self._run_synthesis_phase(
                        plan, trc_analyses, date_start, date_end)
                else:
                    report_text = "VOC analysis cancelled by user."

        else:
            # ── Legacy pipeline (no orchestrator) ──
            trc_analyses = self._run_analysis_phase(
                plan, date_start, date_end)

            if self._cancelled:
                return {
                    "report_text": "VOC analysis cancelled by user.",
                    "trc_analyses": trc_analyses,
                    "stats": plan,
                }

            report_text = self._run_synthesis_phase(
                plan, trc_analyses, date_start, date_end)

        # Persist
        duration_ms = int((time.time() - t0) * 1000)
        self._persist_report(
            date_start, date_end, trc_filter,
            report_text, trc_analyses, plan, duration_ms,
        )

        return {
            "report_text": report_text,
            "trc_analyses": trc_analyses,
            "stats": plan,
        }

    def cancel(self):
        """Signal cancellation from UI thread."""
        self._cancelled = True

    # ═══════════════════════════════════════════════════════════════
    #  PHASE 0: PLANNING
    # ═══════════════════════════════════════════════════════════════

    def _build_plan(self, date_start, date_end, trc_filter=None):
        """Enumerate TRCs, compute sample sizes, batch counts, cost estimates."""
        trc_counts = self.db.get_trc_ticket_counts(date_start, date_end)

        if trc_filter:
            trc_counts = [t for t in trc_counts if t["trc"] == trc_filter]

        # Check for NLP data
        scan = self.db.get_latest_completed_scan()
        has_nlp = scan is not None
        scan_id = scan["scan_id"] if scan else None

        trc_plans = []
        total_sampled = 0
        total_batches = 0

        for tc in trc_counts:
            trc = tc["trc"]
            n = tc["n"]
            if n == 0:
                continue

            # Compute sample size
            if n <= self.SAMPLE_ALL_THRESHOLD:
                sampled = n
            elif n <= 1000:
                sampled = min(n, self.SAMPLE_MEDIUM_MAX)
            else:
                sampled = min(n, self.SAMPLE_LARGE_MAX)

            # Compute batches
            batches = max(1, (sampled + self.MAX_TICKETS_PER_BATCH - 1)
                          // self.MAX_TICKETS_PER_BATCH)

            # Check NLP data for this TRC
            trc_has_nlp = False
            if has_nlp:
                nlp_agg = self.db.get_nlp_aggregate_for_trc(trc, scan_id)
                trc_has_nlp = nlp_agg is not None

            trc_plans.append({
                "trc": trc,
                "total_tickets": n,
                "sampled": sampled,
                "batches": batches,
                "has_nlp": trc_has_nlp,
            })
            total_sampled += sampled
            total_batches += batches

        total_tickets = sum(tc["n"] for tc in trc_counts)

        # Cost estimate
        # Analysis calls: one per TRC (single batch or multi-batch → still 1 call per TRC)
        # Synthesis: 1 call
        analysis_calls = len(trc_plans)
        synthesis_calls = 1 if trc_plans else 0
        total_calls = analysis_calls + synthesis_calls

        # Input cost: average ~200K chars per analysis call, ~60K for synthesis
        est_input_chars = (analysis_calls * 200_000 + synthesis_calls * 60_000)
        est_output_chars = (analysis_calls * self.EST_OUTPUT_CHARS_PER_CALL
                            + synthesis_calls * 15_000)
        est_cost = (est_input_chars / 1_000_000 * self.EST_INPUT_COST_PER_MCHAR
                    + est_output_chars / 1_000_000 * self.EST_OUTPUT_COST_PER_MCHAR)

        # Time estimate: ~15-25s per Gemini call
        est_time_min = round(total_calls * 20 / 60, 1)

        return {
            "trc_plans": trc_plans,
            "total_tickets": total_tickets,
            "total_sampled": total_sampled,
            "total_batches": total_batches,
            "total_gemini_calls": total_calls,
            "est_cost_usd": round(est_cost, 2),
            "est_time_min": est_time_min,
            "has_nlp_data": has_nlp,
            "model": self.gemini.model,
            "scan_id": scan_id,
        }

    # ═══════════════════════════════════════════════════════════════
    #  PHASE 1: PER-TRC ANALYSIS
    # ═══════════════════════════════════════════════════════════════

    def _run_analysis_phase(self, plan, date_start, date_end):
        """For each TRC, sample tickets, build prompt, call Gemini.

        Build 7.0: If an orchestrator is available, builds all prompts
        upfront (main thread, DB-safe) then dispatches Gemini calls in
        parallel across the bridge pool (~3x speedup).
        """
        if self._orchestrator:
            return self._run_analysis_phase_parallel(
                plan, date_start, date_end
            )

        return self._run_analysis_phase_sequential(
            plan, date_start, date_end
        )

    def _run_analysis_phase_sequential(self, plan, date_start, date_end):
        """Original sequential Phase 1 (fallback when no orchestrator)."""
        trc_analyses = {}
        total_trcs = len(plan["trc_plans"])

        for idx, trc_plan in enumerate(plan["trc_plans"]):
            if self._cancelled:
                break

            trc = trc_plan["trc"]
            pct = int((idx / max(total_trcs, 1)) * 80)  # 0-80% for phase 1
            self._progress(
                f"Analyzing TRC: {trc} ({idx + 1}/{total_trcs})", pct
            )

            try:
                analysis = self._analyze_single_trc(
                    trc, trc_plan, plan, date_start, date_end
                )
                trc_analyses[trc] = analysis
            except Exception as e:
                logger.error(f"VOC analysis failed for TRC {trc}: {e}")
                trc_analyses[trc] = f"[Analysis failed: {e}]"

        return trc_analyses

    def _run_analysis_phase_parallel(self, plan, date_start, date_end):
        """Build 7.0: Parallel Phase 1 via ReportOrchestrator.

        Strategy: build ALL prompts upfront (main thread, DB-safe),
        then dispatch only the Gemini calls across the bridge pool.
        DB access stays single-threaded; parallelism is network-only.
        """
        total_trcs = len(plan["trc_plans"])
        self._progress("Building analysis prompts...", 0)

        # ── Step 1: Build all prompts (main thread, sequential) ──
        tasks = []
        for idx, trc_plan in enumerate(plan["trc_plans"]):
            if self._cancelled:
                break

            trc = trc_plan["trc"]
            try:
                prompt = self._build_trc_analysis_prompt(
                    trc, trc_plan, plan, date_start, date_end
                )
                tasks.append({
                    "id": trc,
                    "prompt": prompt,
                    "timeout": 300,
                })
            except Exception as e:
                logger.error(f"VOC prompt build failed for TRC {trc}: {e}")
                tasks.append({
                    "id": trc,
                    "prompt": None,
                    "timeout": 300,
                    "_error": f"[Prompt build failed: {e}]",
                })

        logger.info(
            "VOC parallel: %d prompts built, dispatching to orchestrator",
            len([t for t in tasks if t["prompt"]]),
        )

        # ── Step 2: Separate good tasks from build failures ──
        dispatch_tasks = [t for t in tasks if t["prompt"]]
        failed_tasks = {t["id"]: t["_error"] for t in tasks if not t["prompt"]}

        # ── Step 3: Dispatch through bridge pool ──
        self._progress(
            f"Analyzing {len(dispatch_tasks)} TRCs across "
            f"{self._orchestrator._num_bridges} bridges...", 5
        )

        results = self._orchestrator.run_parallel(
            dispatch_tasks,
            progress_cb=self._progress,
        )

        # ── Step 4: Merge results ──
        trc_analyses = {}
        for trc, result in results.items():
            trc_analyses[trc] = result
            # Log usage for successful results
            if not result.startswith("["):
                prompt_len = next(
                    (len(t["prompt"]) for t in dispatch_tasks if t["id"] == trc),
                    0
                )
                self._log_usage(prompt_len, len(result), "voc_analysis")

        # Add build failures
        trc_analyses.update(failed_tasks)

        logger.info(
            "VOC parallel complete: %d/%d TRCs analyzed",
            sum(1 for v in trc_analyses.values() if not v.startswith("[")),
            total_trcs,
        )

        return trc_analyses

    def _build_trc_analysis_prompt(self, trc, trc_plan, plan,
                                    date_start, date_end):
        """Build the full analysis prompt for a single TRC (DB access, main thread).

        Extracted from _analyze_single_trc() so prompt building can happen
        in the main thread while Gemini calls run in parallel.
        """
        # 1. Sample tickets
        tickets = self._sample_tickets_for_trc(
            trc, date_start, date_end,
            trc_plan["sampled"], plan.get("scan_id"),
        )

        if not tickets:
            return None  # Caller handles as empty

        # 2. Package as JSONL
        jsonl_lines = []
        for t in tickets:
            line = self._package_ticket_jsonl(t, plan.get("scan_id"))
            jsonl_lines.append(line)
        ticket_jsonl = "\n".join(jsonl_lines)

        # 3. Build NLP context
        nlp_context = ""
        if trc_plan.get("has_nlp"):
            nlp_context = self._build_nlp_context_for_trc(
                trc, plan.get("scan_id")
            )

        # 4. Build statistical context
        stat_context = self._build_stat_context_for_trc(
            trc, date_start, date_end
        )

        # 5. Assemble prompt
        prompt = self._assemble_analysis_prompt(
            trc=trc,
            trc_label=self._get_trc_label(trc),
            date_start=date_start,
            date_end=date_end,
            n_tickets=len(tickets),
            total_trc_tickets=trc_plan["total_tickets"],
            nlp_context=nlp_context,
            statistical_context=stat_context,
            ticket_jsonl=ticket_jsonl,
        )

        logger.info(
            f"VOC prompt for {trc}: {len(tickets)} tickets, "
            f"{len(prompt)} chars"
        )

        return prompt

    def _analyze_single_trc(self, trc, trc_plan, plan, date_start, date_end):
        """Run analysis for a single TRC: sample → JSONL → prompt → Gemini."""
        # 1. Sample tickets
        tickets = self._sample_tickets_for_trc(
            trc, date_start, date_end,
            trc_plan["sampled"], plan.get("scan_id"),
        )

        if not tickets:
            return "[No tickets found for this TRC]"

        # 2. Package as JSONL
        jsonl_lines = []
        for t in tickets:
            line = self._package_ticket_jsonl(t, plan.get("scan_id"))
            jsonl_lines.append(line)
        ticket_jsonl = "\n".join(jsonl_lines)

        # 3. Build NLP context
        nlp_context = ""
        if trc_plan.get("has_nlp"):
            nlp_context = self._build_nlp_context_for_trc(
                trc, plan.get("scan_id")
            )

        # 4. Build statistical context
        stat_context = self._build_stat_context_for_trc(
            trc, date_start, date_end
        )

        # 5. Assemble prompt
        prompt = self._assemble_analysis_prompt(
            trc=trc,
            trc_label=self._get_trc_label(trc),
            date_start=date_start,
            date_end=date_end,
            n_tickets=len(tickets),
            total_trc_tickets=trc_plan["total_tickets"],
            nlp_context=nlp_context,
            statistical_context=stat_context,
            ticket_jsonl=ticket_jsonl,
        )

        # 6. Call Gemini
        logger.info(
            f"VOC analysis for {trc}: {len(tickets)} tickets, "
            f"{len(prompt)} chars prompt"
        )
        response = self.gemini.generate(prompt, timeout=300)

        # 7. Log usage
        self._log_usage(len(prompt), len(response), "voc_analysis")

        return response

    # ═══════════════════════════════════════════════════════════════
    #  PHASE 2: SYNTHESIS
    # ═══════════════════════════════════════════════════════════════

    def _run_synthesis_phase(self, plan, trc_analyses, date_start, date_end):
        """Combine all TRC analyses + global stats → executive report."""
        self._progress("Synthesizing executive report...", 85)

        if not trc_analyses:
            return "No TRC analyses to synthesize."

        # Build full statistical context (cross-TRC)
        full_stat_context = self._build_global_stat_context(
            date_start, date_end
        )

        # 6.3: Build model health context from probe data
        model_health_ctx = self._build_model_health_context(
            plan.get("scan_id")
        )

        # Format TRC analyses
        trc_analyses_text = self._format_trc_analyses_for_synthesis(
            trc_analyses
        )

        # Assemble synthesis prompt
        prompt = self._assemble_synthesis_prompt(
            date_start=date_start,
            date_end=date_end,
            total_tickets=plan["total_tickets"],
            sampled_tickets=plan["total_sampled"],
            trc_count=len(plan["trc_plans"]),
            full_statistical_context=full_stat_context,
            trc_analyses=trc_analyses_text,
            model_health_context=model_health_ctx,
        )

        # Call Gemini
        logger.info(f"VOC synthesis: {len(prompt)} chars prompt")
        self._progress("Generating executive report...", 90)
        response = self.gemini.generate(prompt, timeout=300)

        # Log usage
        self._log_usage(len(prompt), len(response), "voc_synthesis")

        self._progress("Report complete", 100)
        return response

    # ═══════════════════════════════════════════════════════════════
    #  BUILD 9.0: BATCHED TRC ANALYSIS INFRASTRUCTURE (T5)
    # ═══════════════════════════════════════════════════════════════

    def _build_trc_prompt_components(self, trc, trc_plan, plan,
                                      date_start, date_end):
        """Build raw prompt components for a single TRC (DB access, main thread).

        Returns dict with: ticket_jsonl, nlp_context, stat_context,
        n_tickets, total_trc_tickets, trc_label, tickets.
        Returns None if no tickets found.
        """
        tickets = self._sample_tickets_for_trc(
            trc, date_start, date_end,
            trc_plan["sampled"], plan.get("scan_id"),
        )

        if not tickets:
            return None

        jsonl_lines = [self._package_ticket_jsonl(t, plan.get("scan_id"))
                       for t in tickets]
        ticket_jsonl = "\n".join(jsonl_lines)

        nlp_context = ""
        if trc_plan.get("has_nlp"):
            nlp_context = self._build_nlp_context_for_trc(
                trc, plan.get("scan_id"))

        stat_context = self._build_stat_context_for_trc(
            trc, date_start, date_end)

        return {
            "trc": trc,
            "ticket_jsonl": ticket_jsonl,
            "nlp_context": nlp_context,
            "stat_context": stat_context,
            "n_tickets": len(tickets),
            "total_trc_tickets": trc_plan["total_tickets"],
            "trc_label": self._get_trc_label(trc),
            "tickets": tickets,  # Kept for intra-TRC chunking
        }

    def _build_batch_tasks(self, plan, date_start, date_end):
        """Build all Phase 1 batch tasks using VOCBatchPacker.

        Builds prompt components for every TRC (main thread, DB-safe),
        bin-packs into batches, and assembles prompts.

        Returns:
            (tasks, batch_trc_map, trc_components, batch_chunk_info)
            tasks: list of task dicts for run_parallel
            batch_trc_map: {batch_task_id: [trc_codes]}
            trc_components: {trc: component_dict}
            batch_chunk_info: {batch_task_id: {trc: (chunk_n, chunk_total)}}
        """
        from src.agents.voc_batch_packer import VOCBatchPacker, TRC_SECTION_OVERHEAD

        total_trcs = len(plan["trc_plans"])
        self._progress("Building TRC prompt components...", 0)

        # ── Step 1: Build all TRC components (main thread, DB-safe) ──
        trc_components = {}
        for idx, trc_plan in enumerate(plan["trc_plans"]):
            if self._cancelled:
                break
            trc = trc_plan["trc"]
            try:
                comp = self._build_trc_prompt_components(
                    trc, trc_plan, plan, date_start, date_end)
                if comp:
                    trc_components[trc] = comp
                else:
                    logger.warning("VOC batch: no tickets for TRC %s", trc)
            except Exception as e:
                logger.error("VOC batch: component build failed for %s: %s",
                             trc, e)

        if not trc_components:
            return [], {}, {}, {}

        # ── Step 2: Measure input sizes ──
        trc_sizes = []
        trc_ticket_counts = {}
        for trc, comp in trc_components.items():
            size = (len(comp["ticket_jsonl"]) + len(comp["nlp_context"])
                    + len(comp["stat_context"]) + TRC_SECTION_OVERHEAD)
            trc_sizes.append((trc, size))
            trc_ticket_counts[trc] = comp["n_tickets"]

        # ── Step 3: Bin-pack ──
        packer = VOCBatchPacker(model=self.gemini.model)
        batch_specs = packer.pack_trcs(trc_sizes, trc_ticket_counts)

        logger.info("VOC batch: %d TRCs -> %d batch specs",
                     len(trc_components), len(batch_specs))

        # ── Step 4: Build batch tasks ──
        tasks = []
        batch_trc_map = {}
        batch_chunk_info = {}

        for batch_idx, spec in enumerate(batch_specs):
            batch_trcs = spec["trcs"]
            chunk_info = spec.get("chunk_info", {})
            task_id = f"batch_{batch_idx:03d}"

            batch_trc_map[task_id] = batch_trcs
            batch_chunk_info[task_id] = chunk_info

            if len(batch_trcs) == 1 and not chunk_info:
                # ── Single-TRC batch → voc_analysis.txt template ──
                trc = batch_trcs[0]
                comp = trc_components[trc]
                prompt = self._assemble_analysis_prompt(
                    trc=trc, trc_label=comp["trc_label"],
                    date_start=date_start, date_end=date_end,
                    n_tickets=comp["n_tickets"],
                    total_trc_tickets=comp["total_trc_tickets"],
                    nlp_context=comp["nlp_context"],
                    statistical_context=comp["stat_context"],
                    ticket_jsonl=comp["ticket_jsonl"],
                )

            elif len(batch_trcs) == 1 and chunk_info:
                # ── Chunked TRC → sliced tickets ──
                trc = batch_trcs[0]
                chunk_n, chunk_total = chunk_info[trc]
                comp = trc_components[trc]

                tickets = comp["tickets"]
                chunk_size = max(1, len(tickets) // chunk_total)
                start = chunk_n * chunk_size
                end = (len(tickets) if chunk_n == chunk_total - 1
                       else start + chunk_size)
                chunk_tickets = tickets[start:end]

                chunk_jsonl = "\n".join(
                    self._package_ticket_jsonl(t, plan.get("scan_id"))
                    for t in chunk_tickets
                )

                prompt = self._assemble_analysis_prompt(
                    trc=trc, trc_label=comp["trc_label"],
                    date_start=date_start, date_end=date_end,
                    n_tickets=len(chunk_tickets),
                    total_trc_tickets=comp["total_trc_tickets"],
                    nlp_context=comp["nlp_context"],
                    statistical_context=comp["stat_context"],
                    ticket_jsonl=chunk_jsonl,
                )

            else:
                # ── Multi-TRC batch → voc_analysis_batch.txt template ──
                prompt = self._assemble_batch_prompt(
                    batch_trcs, trc_components, plan,
                    date_start, date_end,
                )

            tasks.append({
                "id": task_id,
                "prompt": prompt,
                "timeout": 300,
            })

        self._progress(
            f"Built {len(tasks)} batch tasks for {len(trc_components)} TRCs",
            5,
        )

        return tasks, batch_trc_map, trc_components, batch_chunk_info

    def _assemble_batch_prompt(self, batch_trcs, trc_components,
                                plan, date_start, date_end):
        """Build a multi-TRC batch prompt using voc_analysis_batch.txt."""
        template = (_PROMPTS_DIR / "voc_analysis_batch.txt").read_text(
            encoding="utf-8")

        sections = []
        for trc in batch_trcs:
            comp = trc_components[trc]
            sample_pct = round(
                comp["n_tickets"] / max(comp["total_trc_tickets"], 1) * 100)

            section = f"=== TRC: {trc} ===\n"
            section += (f"TRC Label: {comp['trc_label']}\n"
                        f"Tickets: {comp['n_tickets']} of "
                        f"{comp['total_trc_tickets']} ({sample_pct}% sample)\n\n")

            if comp["nlp_context"]:
                section += f"{comp['nlp_context']}\n\n"

            section += (f"STATISTICAL CONTEXT:\n{comp['stat_context']}\n\n"
                        f"TICKET DATA (JSONL):\n{comp['ticket_jsonl']}\n")
            sections.append(section)

        batch_sections = "\n\n".join(sections)

        prompt = template.replace("{date_start}", date_start)
        prompt = prompt.replace("{date_end}", date_end)
        prompt = prompt.replace("{batch_trc_count}", str(len(batch_trcs)))
        prompt = prompt.replace("{total_trc_count}",
                                str(len(trc_components)))
        prompt = prompt.replace("{batch_sections}", batch_sections)

        return prompt

    def _parse_batch_response(self, response, expected_trcs):
        """Parse a multi-TRC batch response into individual TRC analyses.

        Returns:
            (parsed_dict, missing_trcs)
            parsed_dict: {trc_code: analysis_text}
            missing_trcs: [trc_codes not found in response]
        """
        if not response or not expected_trcs:
            return {}, list(expected_trcs or [])

        # Single-TRC batch: no delimiter needed
        if len(expected_trcs) == 1:
            return {expected_trcs[0]: response}, []

        # Multi-TRC: split on delimiters
        # TRC codes contain spaces, so capture everything between TRC: and ===
        parts = re.split(r'===\s*TRC:\s*(.+?)\s*===', response)

        parsed = {}
        # parts[0] = preamble, then alternating: code, analysis, code, ...
        i = 1
        while i < len(parts) - 1:
            trc_code = parts[i].strip()
            analysis = parts[i + 1].strip()
            if trc_code and analysis:
                parsed[trc_code] = analysis
            i += 2

        missing = [t for t in expected_trcs if t not in parsed]
        extra = [t for t in parsed if t not in expected_trcs]

        if extra:
            logger.warning(
                "VOC batch parse: unexpected TRCs in response: %s", extra)
        if missing:
            logger.warning(
                "VOC batch parse: missing TRCs: %s (expected %d, got %d)",
                missing, len(expected_trcs), len(parsed),
            )

        result = {t: parsed[t] for t in expected_trcs if t in parsed}
        return result, missing

    def _handle_missing_trcs(self, missing_trcs, task_queue, next_seq_fn,
                              trc_components, plan, date_start, date_end):
        """Split-on-failure: requeue missing TRCs as individual tasks.

        Called when a batch response is missing some TRCs. Builds individual
        prompts for the missing TRCs and enqueues at priority 0.
        """
        for trc in missing_trcs:
            comp = trc_components.get(trc)
            if not comp:
                logger.error(
                    "VOC batch: missing TRC %s has no components", trc)
                continue

            try:
                prompt = self._assemble_analysis_prompt(
                    trc=trc, trc_label=comp["trc_label"],
                    date_start=date_start, date_end=date_end,
                    n_tickets=comp["n_tickets"],
                    total_trc_tickets=comp["total_trc_tickets"],
                    nlp_context=comp["nlp_context"],
                    statistical_context=comp["stat_context"],
                    ticket_jsonl=comp["ticket_jsonl"],
                )
                task = {
                    "id": f"retry_{trc}",
                    "prompt": prompt,
                    "timeout": 300,
                }
                task_queue.put((0, next_seq_fn(), task, 0))
                logger.info(
                    "VOC batch: requeued missing TRC %s as individual task",
                    trc,
                )
            except Exception as e:
                logger.error(
                    "VOC batch: failed to requeue TRC %s: %s", trc, e)

    def _merge_chunked_trc_results(self, trc, chunk_results):
        """Merge multiple chunk analysis results for an oversized TRC.

        Args:
            trc: TRC code
            chunk_results: list of (chunk_n, analysis_text) tuples

        Returns: merged analysis text
        """
        if len(chunk_results) == 1:
            return chunk_results[0][1]

        parts = [
            f"[MERGED ANALYSIS: {trc} — {len(chunk_results)} chunks]\n"
        ]
        for chunk_n, analysis in sorted(chunk_results, key=lambda x: x[0]):
            parts.append(
                f"--- Chunk {chunk_n + 1}/{len(chunk_results)} ---\n"
                f"{analysis}\n"
            )
        return "\n".join(parts)

    def _format_analyses_for_specialists(self, trc_analyses):
        """Format all TRC analyses for specialist prompts (full fidelity).

        Unlike _format_trc_analyses_for_synthesis which truncates to 800 chars,
        specialists get untruncated analyses within their input budget.
        """
        sections = []
        for trc in sorted(trc_analyses.keys()):
            sections.append(f"=== TRC: {trc} ===\n{trc_analyses[trc]}\n")
        return "\n".join(sections)

    def _compress_analyses_for_specialists(self, trc_analyses, plan, target):
        """Compress TRC analyses to fit within specialist budget.

        Strategy:
        1. Drop low-volume TRC analyses (< 10 tickets) — they're in the
           accumulator ledger already.
        2. Truncate remaining to ~500 chars each (keeps summary header).
        """
        volume_map = {tp["trc"]: tp["total_tickets"]
                      for tp in plan["trc_plans"]}
        sorted_trcs = sorted(
            trc_analyses.keys(),
            key=lambda t: volume_map.get(t, 0),
            reverse=True,
        )

        # Phase 1: Drop low-volume TRCs
        MIN_TICKETS = 10
        kept = [(t, trc_analyses[t]) for t in sorted_trcs
                if volume_map.get(t, 0) >= MIN_TICKETS]

        current_size = sum(len(a) for _, a in kept)
        if current_size <= target:
            return "\n".join(
                f"=== TRC: {t} ===\n{a}\n" for t, a in kept)

        # Phase 2: Truncate each to fit
        n_trcs = len(kept)
        per_trc_budget = max(200, (target - n_trcs * 50) // max(n_trcs, 1))

        sections = []
        for trc, analysis in kept:
            text = analysis[:per_trc_budget]
            if len(analysis) > per_trc_budget:
                text += "\n[...truncated for specialist input]"
            sections.append(f"=== TRC: {trc} ===\n{text}\n")

        return "\n".join(sections)

    def _get_specialist_budget(self):
        """Get the effective input budget for specialist calls."""
        from src.agents.batch_packer import MODEL_INPUT_LIMITS, DEFAULT_INPUT_BUDGET
        raw = MODEL_INPUT_LIMITS.get(self.gemini.model, DEFAULT_INPUT_BUDGET)
        return int(raw * 0.75)  # Same headroom as VOCBatchPacker

    # ═══════════════════════════════════════════════════════════════
    #  BUILD 9.0: UNIFIED PRIORITY DISPATCH (T6)
    # ═══════════════════════════════════════════════════════════════

    def _run_analysis_and_synthesis(self, plan, date_start, date_end):
        """Phases 1+2a+2b: single priority dispatch.

        Phase 1 batches (priority 0) + accumulator rounds (priority 1) +
        specialists (priority 0, injected after Phase 1) — all through one
        run_parallel() call with dynamic task injection.

        Returns:
            (trc_analyses, accumulator_result, specialist_results)
        """
        # ── Build Phase 1 batch tasks ──
        batch_tasks, batch_trc_map, trc_components, batch_chunk_info = (
            self._build_batch_tasks(plan, date_start, date_end))

        if not batch_tasks:
            return {}, {"ledger": "", "synthesis": "", "rounds_completed": 0}, {}

        # ── Pre-build DB-dependent contexts (main thread, SQLite-safe) ──
        # These are used by specialist_injector and convergence, which run
        # in daemon threads. SQLite objects can only be used in the thread
        # that created them, so we build all DB contexts here.
        pre_global_stats = self._build_global_stat_context(
            date_start, date_end)
        pre_nlp_baseline = self._build_nlp_baseline_context(plan)
        pre_friction_metrics = self._build_friction_metrics_context(plan)

        # ── Shared state ──
        task_queue = PriorityQueue()
        drain_event = threading.Event()
        all_results = {}
        all_results_lock = threading.Lock()
        trc_analyses = {}
        trc_lock = threading.Lock()
        seq_counter = [0]
        seq_lock = threading.Lock()

        def next_seq():
            with seq_lock:
                s = seq_counter[0]
                seq_counter[0] += 1
                return s

        # Enqueue Phase 1 tasks at priority 0
        for task in batch_tasks:
            task_queue.put((0, next_seq(), task, 0))

        # ── on_complete callback: LIGHTWEIGHT ──
        phase1_done = threading.Event()
        phase1_total = len(batch_tasks)
        phase1_count_lock = threading.Lock()
        phase1_completed = [0]
        phase1_errors = [0]
        raw_batch_results = {}
        raw_batch_lock = threading.Lock()
        batch_result_event = threading.Event()
        total_trcs = len(plan["trc_plans"])
        _progress_ref = self._progress  # closure ref for thread safety

        def on_task_complete(task_id, result):
            """Called by run_parallel worker thread. MUST be lightweight.
            Only stores raw result and sets events."""
            with all_results_lock:
                all_results[task_id] = result

            is_error = isinstance(result, str) and result.startswith("[Error:")

            if task_id.startswith("batch_") or task_id.startswith("retry_"):
                with raw_batch_lock:
                    raw_batch_results[task_id] = result
                batch_result_event.set()

                if task_id.startswith("batch_"):
                    with phase1_count_lock:
                        phase1_completed[0] += 1
                        if is_error:
                            phase1_errors[0] += 1
                        done = phase1_completed[0]
                        errs = phase1_errors[0]
                        if done >= phase1_total:
                            phase1_done.set()
                    # Progress: batches span 10-60%
                    pct = 10 + int(50 * done / max(phase1_total, 1))
                    err_tag = f" ({errs} errors)" if errs else ""
                    _progress_ref(
                        f"Phase 1: batch {done}/{phase1_total} complete"
                        f"{err_tag}",
                        min(pct, 60))

            elif task_id.startswith("accum_"):
                status = "error" if is_error else "done"
                _progress_ref(f"Accumulator {task_id}: {status}", None)

            elif task_id.startswith("spec_"):
                name = task_id[5:]
                status = "error" if is_error else "done"
                _progress_ref(f"Specialist '{name}': {status}", None)

        # ── Batch result parser thread ──
        parsed_batch_ids = set()

        def batch_parser_loop():
            """Parses raw batch results into individual TRC analyses.
            Runs in its own thread so worker bridges aren't blocked."""
            while not self._cancelled:
                # Check if we're done
                if phase1_done.is_set() and len(parsed_batch_ids) >= phase1_total:
                    break

                batch_result_event.wait(timeout=5)
                batch_result_event.clear()

                with raw_batch_lock:
                    pending = {k: v for k, v in raw_batch_results.items()
                               if k not in parsed_batch_ids}

                for task_id, result in pending.items():
                    parsed_batch_ids.add(task_id)

                    if isinstance(result, str) and result.startswith("[Error:"):
                        logger.warning("Batch %s returned error: %s",
                                       task_id, result[:100])
                        continue

                    # Determine expected TRCs
                    if task_id.startswith("retry_"):
                        # Individual retry: TRC code is the task_id suffix
                        retry_trc = task_id[len("retry_"):]
                        expected_trcs = [retry_trc]
                    else:
                        expected_trcs = batch_trc_map.get(task_id, [])

                    if not expected_trcs:
                        continue

                    # Check if this is a chunked TRC
                    chunk_info = batch_chunk_info.get(task_id, {})
                    if chunk_info:
                        # Chunked: store chunk result for later merge
                        for trc, (chunk_n, chunk_total) in chunk_info.items():
                            chunk_key = f"_chunk_{trc}"
                            with trc_lock:
                                if chunk_key not in trc_analyses:
                                    trc_analyses[chunk_key] = []
                                trc_analyses[chunk_key].append(
                                    (chunk_n, result))
                                # Check if all chunks arrived
                                if len(trc_analyses[chunk_key]) >= chunk_total:
                                    merged = self._merge_chunked_trc_results(
                                        trc, trc_analyses[chunk_key])
                                    trc_analyses[trc] = merged
                                    del trc_analyses[chunk_key]
                        continue

                    # Parse batch response
                    parsed, missing = self._parse_batch_response(
                        result, expected_trcs)

                    with trc_lock:
                        trc_analyses.update(parsed)
                        n_parsed_so_far = sum(
                            1 for k in trc_analyses
                            if not k.startswith("_chunk_"))

                    # Report TRC-level progress
                    if parsed:
                        _progress_ref(
                            f"TRCs parsed: {n_parsed_so_far}/{total_trcs}"
                            f" (+{len(parsed)} from {task_id},"
                            f" {len(missing)} missing)",
                            None)

                    # Handle missing TRCs
                    if missing:
                        self._handle_missing_trcs(
                            missing, task_queue, next_seq,
                            trc_components, plan, date_start, date_end)

            n_parsed = sum(1 for k in trc_analyses
                          if not k.startswith("_chunk_"))
            logger.info("Batch parser done: %d TRCs parsed", n_parsed)
            _progress_ref(
                f"Phase 1 parsing complete: {n_parsed}/{total_trcs} TRCs",
                65)

        # ── Accumulator thread ──
        accumulator_result = {
            "ledger": "", "synthesis": "", "rounds_completed": 0}

        def accumulator_loop():
            sorted_trcs = sorted(
                [tp["trc"] for tp in plan["trc_plans"]],
                key=lambda t: next(
                    (tp["total_tickets"] for tp in plan["trc_plans"]
                     if tp["trc"] == t), 0),
                reverse=True,
            )

            n_rounds = min(self.ACCUMULATOR_ROUNDS, len(sorted_trcs))
            if n_rounds == 0:
                return

            round_size = max(1, len(sorted_trcs) // n_rounds)
            rounds = [sorted_trcs[i:i + round_size]
                      for i in range(0, len(sorted_trcs), round_size)]
            if len(rounds) > n_rounds:
                rounds[-2].extend(rounds[-1])
                rounds = rounds[:-1]

            evidence_ledger = []
            running_synthesis = ""
            template = (_PROMPTS_DIR / "voc_accumulator.txt").read_text(
                encoding="utf-8")

            for round_idx, round_trcs in enumerate(rounds):
                if self._cancelled:
                    break

                # Wait for ≥80% of this round's TRCs to have analyses
                threshold = max(1, int(len(round_trcs) * 0.8))
                wait_start = time.time()
                while True:
                    with trc_lock:
                        ready = sum(1 for t in round_trcs
                                    if t in trc_analyses)
                    if ready >= threshold:
                        break
                    if self._cancelled:
                        break
                    if time.time() - wait_start > self.ACCUMULATOR_ANALYSIS_WAIT:
                        logger.warning(
                            "Accumulator round %d: timed out waiting "
                            "(%d/%d ready)", round_idx + 1, ready,
                            len(round_trcs))
                        break
                    time.sleep(3)

                if self._cancelled:
                    break

                # Build prompt from available analyses
                with trc_lock:
                    round_analyses = "\n".join(
                        f"=== TRC: {t} ===\n"
                        f"{trc_analyses.get(t, '[pending]')}"
                        for t in round_trcs
                    )

                prompt = template.replace(
                    "{round_number}", str(round_idx + 1))
                prompt = prompt.replace(
                    "{total_rounds}", str(n_rounds))
                prompt = prompt.replace(
                    "{evidence_ledger}",
                    "\n---\n".join(evidence_ledger) or "(empty)")
                prompt = prompt.replace(
                    "{running_synthesis}",
                    running_synthesis or "(first round)")
                prompt = prompt.replace(
                    "{round_analyses}", round_analyses)
                prompt = prompt.replace(
                    "{round_trc_count}", str(len(round_trcs)))
                prompt = prompt.replace(
                    "{total_trc_count}", str(len(sorted_trcs)))
                prompt = prompt.replace("{date_start}", date_start)
                prompt = prompt.replace("{date_end}", date_end)

                # Enqueue at priority 1
                accum_task_id = f"accum_r{round_idx + 1}"
                task = {
                    "id": accum_task_id,
                    "prompt": prompt,
                    "timeout": self.ACCUMULATOR_ROUND_TIMEOUT,
                }
                task_queue.put((1, next_seq(), task, 0))

                # Wait for this round's result
                result_wait_start = time.time()
                while True:
                    with all_results_lock:
                        have_result = accum_task_id in all_results
                    if have_result:
                        break
                    if self._cancelled:
                        break
                    if (time.time() - result_wait_start
                            > self.ACCUMULATOR_RESULT_WAIT):
                        logger.warning(
                            "Accumulator round %d: result wait timed out "
                            "— continuing to next round",
                            round_idx + 1)
                        break
                    time.sleep(2)

                if self._cancelled:
                    break

                with all_results_lock:
                    result = all_results.get(accum_task_id)

                if not result:
                    # Timed out — continue to next round with what we have.
                    # The result may arrive later via on_complete but
                    # we don't block the accumulator loop on it.
                    logger.info(
                        "Accumulator round %d: skipping (no result), "
                        "continuing with %d ledger entries",
                        round_idx + 1, len(evidence_ledger))
                    continue

                if isinstance(result, str) and result.startswith("[Error:"):
                    logger.warning(
                        "Accumulator round %d failed: %s — continuing",
                        round_idx + 1, result[:100])
                    continue

                new_findings, updated_synthesis = (
                    self._parse_accumulator_response(result))
                evidence_ledger.append(
                    f"[Round {round_idx + 1}: "
                    f"{len(round_trcs)} TRCs]\n{new_findings}")
                running_synthesis = updated_synthesis
                accumulator_result["rounds_completed"] = round_idx + 1

                logger.info(
                    "Accumulator round %d/%d complete, "
                    "ledger entries: %d",
                    round_idx + 1, n_rounds, len(evidence_ledger))
                _progress_ref(
                    f"Accumulator round {round_idx + 1}/{n_rounds} "
                    f"complete ({len(evidence_ledger)} ledger entries)",
                    None)

            accumulator_result["ledger"] = "\n---\n".join(evidence_ledger)
            accumulator_result["synthesis"] = running_synthesis

        # ── Specialist injection thread ──
        specialist_results = {}

        def specialist_injector():
            phase1_done.wait(timeout=self.PHASE1_DONE_TIMEOUT)

            if self._cancelled or not phase1_done.is_set():
                drain_event.set()
                return

            logger.info("Phase 1 complete — injecting specialists")
            _progress_ref("Phase 1 done — launching 3 specialists", 70)

            with trc_lock:
                analyses_text = self._format_analyses_for_specialists(
                    trc_analyses)
            # Use pre-built DB contexts (main thread, SQLite-safe)
            global_stats = pre_global_stats

            # Snapshot accumulator's partial ledger
            partial_ledger = accumulator_result.get("ledger", "")
            partial_synthesis = accumulator_result.get("synthesis", "")
            accum_context = ""
            if partial_ledger:
                rounds_done = accumulator_result.get(
                    "rounds_completed", 0)
                accum_context = (
                    f"ACCUMULATOR CONTEXT (from {rounds_done} "
                    f"rounds completed so far):\n"
                    f"{partial_synthesis}\n\n"
                    f"Key findings from accumulator ledger:\n"
                    f"{partial_ledger[:8000]}"
                )

            # Budget check
            total_input = (len(analyses_text) + len(global_stats)
                           + len(accum_context) + self.SPECIALIST_OVERHEAD)
            if total_input > self._get_specialist_budget():
                analyses_text = self._compress_analyses_for_specialists(
                    trc_analyses, plan,
                    target=(self._get_specialist_budget()
                            - len(global_stats) - len(accum_context)))

            # Use pre-built DB contexts (main thread, SQLite-safe)
            nlp_baseline = pre_nlp_baseline
            friction_metrics = pre_friction_metrics

            specs = [
                ("pattern", "voc_pattern_detector.txt", ""),
                ("novelty", "voc_novelty_scanner.txt", nlp_baseline),
                ("friction", "voc_friction_scorer.txt", friction_metrics),
            ]

            for name, template_file, extra in specs:
                template = (_PROMPTS_DIR / template_file).read_text(
                    encoding="utf-8")
                prompt = template.replace("{trc_analyses}", analyses_text)
                prompt = prompt.replace(
                    "{global_statistics}", global_stats)
                prompt = prompt.replace(
                    "{accumulator_context}", accum_context)
                prompt = prompt.replace("{extra_context}", extra)
                prompt = prompt.replace("{date_start}", date_start)
                prompt = prompt.replace("{date_end}", date_end)

                task = {
                    "id": f"spec_{name}",
                    "prompt": prompt,
                    "timeout": self.SPECIALIST_TIMEOUT,
                }
                task_queue.put((0, next_seq(), task, 0))

            # Wait for all specialist results
            for name, _, _ in specs:
                tid = f"spec_{name}"
                spec_wait_start = time.time()
                while True:
                    with all_results_lock:
                        have_result = tid in all_results
                    if have_result:
                        with all_results_lock:
                            specialist_results[name] = all_results[tid]
                        break
                    if self._cancelled:
                        specialist_results[name] = ""
                        break
                    if (time.time() - spec_wait_start
                            > self.SPECIALIST_RESULT_WAIT):
                        logger.error(
                            "Specialist %s: result wait timed out", name)
                        specialist_results[name] = ""
                        break
                    time.sleep(2)

            # All dynamic tasks injected — signal drain
            spec_ok = sum(1 for v in specialist_results.values()
                          if v and not (isinstance(v, str)
                                        and v.startswith("[Error:")))
            _progress_ref(
                f"Specialists complete ({spec_ok}/3 succeeded) "
                f"— preparing convergence", 85)
            drain_event.set()

        # ── Launch threads ──
        parser_thread = threading.Thread(
            target=batch_parser_loop, name="batch_parser", daemon=True)
        accum_thread = threading.Thread(
            target=accumulator_loop, name="accumulator", daemon=True)
        spec_thread = threading.Thread(
            target=specialist_injector, name="spec_injector", daemon=True)

        parser_thread.start()
        accum_thread.start()
        spec_thread.start()

        # ── Run unified dispatch ──
        self._progress(
            f"Running pipelined analysis: {len(batch_tasks)} batches, "
            f"{self.ACCUMULATOR_ROUNDS} accumulator rounds, 3 specialists",
            10,
        )

        self._orchestrator.run_parallel(
            [],
            task_queue=task_queue,
            progress_cb=self._progress,
            on_complete=on_task_complete,
            drain_event=drain_event,
        )

        # Wait for threads to finish
        parser_thread.join(timeout=30)
        accum_thread.join(timeout=30)
        spec_thread.join(timeout=30)

        # Log summary
        n_analyses = sum(1 for k, v in trc_analyses.items()
                         if not k.startswith("_chunk_")
                         and not (isinstance(v, str)
                                  and v.startswith("[Error:")))
        n_spec_ok = len([v for v in specialist_results.values()
                         if v and not (isinstance(v, str)
                                       and v.startswith("[Error:"))])
        accum_rounds = accumulator_result["rounds_completed"]
        logger.info(
            "Pipelined dispatch complete: %d TRC analyses, "
            "%d accumulator rounds, %d specialist reports",
            n_analyses, accum_rounds, n_spec_ok,
        )
        self._progress(
            f"Dispatch complete: {n_analyses}/{total_trcs} TRCs, "
            f"{accum_rounds} accum rounds, {n_spec_ok}/3 specialists",
            88)

        return trc_analyses, accumulator_result, specialist_results

    def _parse_accumulator_response(self, response):
        """Parse accumulator round output into new_findings + running_synthesis.

        Expected format:
            ## NEW_FINDINGS
            ... findings ...
            ## RUNNING_SYNTHESIS
            ... synthesis ...

        Returns: (new_findings, running_synthesis)
        """
        new_findings = ""
        running_synthesis = ""

        if not response:
            return new_findings, running_synthesis

        # Split on section headers
        parts = re.split(
            r'##\s*(?:NEW_FINDINGS|RUNNING_SYNTHESIS)', response)

        # Find section positions
        findings_match = re.search(r'##\s*NEW_FINDINGS', response)
        synthesis_match = re.search(r'##\s*RUNNING_SYNTHESIS', response)

        if findings_match and synthesis_match:
            # Both sections found
            f_start = findings_match.end()
            s_start = synthesis_match.end()

            if f_start < s_start:
                new_findings = response[f_start:synthesis_match.start()].strip()
                running_synthesis = response[s_start:].strip()
            else:
                running_synthesis = response[s_start:findings_match.start()].strip()
                new_findings = response[f_start:].strip()
        elif findings_match:
            new_findings = response[findings_match.end():].strip()
        elif synthesis_match:
            running_synthesis = response[synthesis_match.end():].strip()
        else:
            # Malformed: treat entire response as findings
            logger.warning(
                "Accumulator response missing section headers, "
                "treating as raw findings")
            new_findings = response.strip()

        return new_findings, running_synthesis

    def _build_nlp_baseline_context(self, plan):
        """Build NLP baseline context for the novelty scanner.

        Provides prior-period NLP aggregates so the scanner can distinguish
        genuinely new issues from pre-existing ones.
        """
        scan_id = plan.get("scan_id")
        if not scan_id:
            return "(No NLP baseline data available)"

        try:
            # Get overall NLP scan summary
            scan = self.db.get_scan(scan_id)
            if not scan:
                return "(No NLP baseline data available)"

            lines = ["NLP BASELINE CONTEXT (from latest scan):"]
            lines.append(f"  Scan ID: {scan_id}")
            lines.append(f"  Status: {scan.get('status', 'unknown')}")

            # Aggregate friction distribution across all TRCs
            total_by_friction = defaultdict(int)
            total_classified = 0
            for trc_plan in plan["trc_plans"]:
                trc = trc_plan["trc"]
                agg = self.db.get_nlp_aggregate_for_trc(trc, scan_id)
                if agg and agg.get("friction_distribution"):
                    for ftype, cnt in agg["friction_distribution"].items():
                        total_by_friction[ftype] += cnt
                    total_classified += agg.get("total_classified", 0)

            if total_by_friction:
                lines.append(
                    f"  Total classified: {total_classified} tickets")
                parts = []
                for ftype, cnt in sorted(total_by_friction.items(),
                                          key=lambda x: x[1],
                                          reverse=True):
                    pct = round(
                        cnt / max(total_classified, 1) * 100)
                    parts.append(f"{ftype}={pct}%")
                lines.append(
                    f"  Global friction distribution: {', '.join(parts)}")

            return "\n".join(lines)
        except Exception as e:
            logger.debug("NLP baseline context build failed: %s", e)
            return "(NLP baseline context unavailable)"

    def _build_friction_metrics_context(self, plan):
        """Build friction metrics context for the friction scorer.

        Provides CSAT/escalation/resolution metrics for severity scoring.
        """
        try:
            lines = ["FRICTION METRICS CONTEXT:"]

            # Aggregate CSAT and volume data from plan
            trc_metrics = []
            for trc_plan in plan["trc_plans"]:
                trc = trc_plan["trc"]
                n = trc_plan["total_tickets"]

                # Get CSAT distribution for this TRC
                try:
                    tickets = self.db.get_tickets_for_trc(
                        trc, "", "")  # All dates for baseline
                    if tickets:
                        csat_scores = [t["csat_score"] for t in tickets
                                       if t.get("csat_score") is not None]
                        if csat_scores:
                            avg_csat = sum(csat_scores) / len(csat_scores)
                            low_csat_pct = round(
                                sum(1 for s in csat_scores if s <= 2)
                                / len(csat_scores) * 100)
                            trc_metrics.append(
                                f"  {trc}: {n} tickets, "
                                f"avg CSAT={avg_csat:.1f}, "
                                f"low CSAT={low_csat_pct}%")
                        else:
                            trc_metrics.append(
                                f"  {trc}: {n} tickets, no CSAT data")
                except Exception:
                    trc_metrics.append(
                        f"  {trc}: {n} tickets, metrics unavailable")

            if trc_metrics:
                lines.append("Per-TRC metrics:")
                lines.extend(trc_metrics[:30])  # Cap at 30 for budget
                if len(trc_metrics) > 30:
                    lines.append(
                        f"  ... and {len(trc_metrics) - 30} more TRCs")

            return "\n".join(lines)
        except Exception as e:
            logger.debug("Friction metrics context build failed: %s", e)
            return "(Friction metrics unavailable)"

    # ═══════════════════════════════════════════════════════════════
    #  BUILD 9.0: CONVERGENCE (T7)
    # ═══════════════════════════════════════════════════════════════

    def _run_convergence(self, accumulator_result, specialist_results,
                          plan, date_start, date_end):
        """Phase 3: Stitch accumulator ledger + specialist reports into
        the final 7-section executive report.

        Uses run_resilient() for full retry/stall recovery. Falls back
        to _assemble_partial_report() if convergence call fails.
        """
        self._progress("Generating executive report (convergence)...", 90)

        # ── Preflight: budget check + compress if needed ──
        accum_size = (len(accumulator_result.get("ledger", ""))
                      + len(accumulator_result.get("synthesis", "")))
        spec_size = sum(len(v) for v in specialist_results.values() if v)

        if (accum_size + spec_size + self.CONVERGENCE_OVERHEAD
                > self.CONVERGENCE_INPUT_CAP):
            specialist_results = self._compress_specialist_reports(
                specialist_results,
                target_total=(self.CONVERGENCE_INPUT_CAP
                              - accum_size - self.CONVERGENCE_OVERHEAD))

        # ── Build convergence prompt ──
        template = (_PROMPTS_DIR / "voc_convergence.txt").read_text(
            encoding="utf-8")

        # Global statistical context
        full_stat_context = self._build_global_stat_context(
            date_start, date_end)

        # Model health context
        model_health_ctx = self._build_model_health_context(
            plan.get("scan_id"))

        # Temporal context
        temporal_ctx = ""
        try:
            from src.data.report_builder import (
                build_windowed_data_blocks, format_temporal_context,
            )
            windowed = build_windowed_data_blocks(
                self.db, date_start, date_end)
            temporal_ctx = format_temporal_context(windowed)
        except Exception:
            pass

        # Trim ledger to last 2 rounds + summary to prevent duplication.
        # The full ledger can be 6-8 rounds of verbose findings — the LLM
        # tends to echo/repeat each round instead of synthesizing.
        raw_ledger = accumulator_result.get("ledger", "(No ledger data)")
        rounds_completed = accumulator_result.get("rounds_completed", 0)
        if rounds_completed > 2 and "\n---\n" in raw_ledger:
            ledger_parts = raw_ledger.split("\n---\n")
            trimmed_ledger = (
                f"[Accumulator ran {rounds_completed} rounds. "
                f"Showing final {min(2, len(ledger_parts))} rounds "
                f"(earlier rounds are incorporated into the running "
                f"synthesis above).]\n\n"
                + "\n---\n".join(ledger_parts[-2:])
            )
        else:
            trimmed_ledger = raw_ledger

        prompt = template.replace(
            "{accumulator_ledger}", trimmed_ledger)
        prompt = prompt.replace(
            "{accumulator_synthesis}",
            accumulator_result.get("synthesis", "(No synthesis data)"))
        prompt = prompt.replace(
            "{pattern_report}",
            specialist_results.get("pattern", "(Pattern report unavailable)"))
        prompt = prompt.replace(
            "{novelty_report}",
            specialist_results.get("novelty", "(Novelty report unavailable)"))
        prompt = prompt.replace(
            "{friction_report}",
            specialist_results.get("friction",
                                   "(Friction report unavailable)"))
        prompt = prompt.replace(
            "{rounds_completed}",
            str(accumulator_result.get("rounds_completed", 0)))
        prompt = prompt.replace("{date_start}", date_start)
        prompt = prompt.replace("{date_end}", date_end)
        prompt = prompt.replace(
            "{total_tickets}", str(plan["total_tickets"]))
        prompt = prompt.replace(
            "{sampled_tickets}", str(plan["total_sampled"]))
        prompt = prompt.replace(
            "{trc_count}", str(len(plan["trc_plans"])))
        prompt = prompt.replace(
            "{full_statistical_context}",
            full_stat_context or "(No statistical context available)")
        prompt = prompt.replace(
            "{model_health_context}",
            model_health_ctx or "(No model health data available)")
        prompt = prompt.replace(
            "{temporal_context}", temporal_ctx or "")

        # ── Call via run_resilient ──
        logger.info("Convergence prompt: %d chars", len(prompt))
        try:
            report = self._orchestrator.run_resilient(
                prompt, request_id="convergence", timeout=600)
            self._log_usage(len(prompt), len(report), "voc_convergence")
            self._progress("Report complete", 100)
            return report
        except RuntimeError as e:
            logger.error("Convergence failed: %s — assembling partial", e)
            self._progress("Assembling partial report...", 95)
            return self._assemble_partial_report(
                accumulator_result, specialist_results)

    def _assemble_partial_report(self, accumulator_result,
                                  specialist_results):
        """Graceful degradation: stitch available outputs directly.

        Used when the convergence Gemini call fails after all retries.
        """
        parts = [
            "# VOC Root Cause Analysis Report",
            "*[Assembled from partial results — convergence model call failed]*\n",
        ]

        # Executive summary from accumulator synthesis
        synthesis = accumulator_result.get("synthesis", "")
        if synthesis:
            parts.append("## 1. EXECUTIVE SUMMARY\n")
            parts.append(synthesis)
            parts.append("")

        # Specialist sections
        pattern = specialist_results.get("pattern", "")
        if pattern:
            parts.append(pattern)
            parts.append("")

        novelty = specialist_results.get("novelty", "")
        if novelty:
            parts.append(novelty)
            parts.append("")

        friction = specialist_results.get("friction", "")
        if friction:
            parts.append(friction)
            parts.append("")

        # Accumulator ledger as appendix
        ledger = accumulator_result.get("ledger", "")
        if ledger:
            parts.append("## 7. APPENDIX: ACCUMULATOR EVIDENCE LEDGER\n")
            parts.append(ledger)

        if len(parts) <= 2:
            return ("# VOC Root Cause Analysis Report\n\n"
                    "*Report generation failed — no data available.*")

        return "\n\n".join(parts)

    def _compress_specialist_reports(self, specialist_results, target_total):
        """Compress specialist reports to fit within convergence budget.

        Truncates each report proportionally to fit target_total chars.
        """
        total = sum(len(v) for v in specialist_results.values() if v)
        if total <= target_total:
            return specialist_results

        compressed = {}
        for name, report in specialist_results.items():
            if not report:
                compressed[name] = ""
                continue
            # Proportional allocation
            alloc = max(1000, int(target_total * len(report) / max(total, 1)))
            if len(report) > alloc:
                compressed[name] = (
                    report[:alloc]
                    + "\n[...truncated for convergence input budget]")
            else:
                compressed[name] = report

        return compressed

    # ═══════════════════════════════════════════════════════════════
    #  TICKET SAMPLING (T2)
    # ═══════════════════════════════════════════════════════════════

    def _sample_tickets_for_trc(self, trc, date_start, date_end,
                                 target_n, scan_id=None):
        """Stratified sampling. NLP-enriched: by friction_type + over-sample
        anomalies. Raw mode: by CSAT quartile. Below threshold: all tickets."""
        all_tickets = self.db.get_tickets_for_trc(trc, date_start, date_end)

        if not all_tickets:
            return []

        # Below threshold: return all
        if len(all_tickets) <= target_n:
            return all_tickets

        # Try NLP-enriched stratified sampling
        if scan_id:
            sampled = self._stratified_sample_nlp(
                all_tickets, target_n, trc, scan_id
            )
            if sampled:
                return sampled

        # Fallback: CSAT-stratified sampling
        return self._stratified_sample_csat(all_tickets, target_n)

    def _stratified_sample_nlp(self, tickets, target_n, trc, scan_id):
        """Sample stratified by NLP friction_type, over-sampling anomalies."""
        # Get NLP classifications for these tickets
        ticket_ids = [t["ticket_id"] for t in tickets]
        placeholders = ",".join("?" * len(ticket_ids))
        rows = self.db.conn.execute(f"""
            SELECT ticket_id, friction_type, anomaly_flag
            FROM nlp_ticket_classifications
            WHERE scan_id = ? AND trc = ? AND ticket_id IN ({placeholders})
        """, [scan_id, trc] + ticket_ids).fetchall()

        if not rows:
            return None  # No NLP data, fallback to CSAT

        # Build lookup
        nlp_lookup = {}
        for r in rows:
            nlp_lookup[r["ticket_id"]] = {
                "friction_type": r["friction_type"] or "unknown",
                "anomaly_flag": r["anomaly_flag"] or "none",
            }

        # Partition by friction type
        by_friction = defaultdict(list)
        anomaly_pool = []
        for t in tickets:
            tid = t["ticket_id"]
            nlp = nlp_lookup.get(tid, {"friction_type": "unknown", "anomaly_flag": "none"})
            by_friction[nlp["friction_type"]].append(t)
            if nlp["anomaly_flag"] in ("critical", "unusual"):
                anomaly_pool.append(t)

        # Over-sample anomalies: up to 20% of target
        anomaly_budget = int(target_n * 0.20)
        anomaly_sample = random.sample(
            anomaly_pool, min(anomaly_budget, len(anomaly_pool))
        )
        anomaly_ids = {t["ticket_id"] for t in anomaly_sample}

        # Remaining budget: distribute proportionally across friction types
        remaining_budget = target_n - len(anomaly_sample)
        total_non_anomaly = sum(
            len([t for t in v if t["ticket_id"] not in anomaly_ids])
            for v in by_friction.values()
        )

        sampled = list(anomaly_sample)
        seen = set(anomaly_ids)

        for friction_type, pool in by_friction.items():
            eligible = [t for t in pool if t["ticket_id"] not in seen]
            if not eligible or total_non_anomaly == 0:
                continue
            # Proportional allocation
            alloc = max(1, int(remaining_budget * len(eligible) / total_non_anomaly))
            chosen = random.sample(eligible, min(alloc, len(eligible)))
            sampled.extend(chosen)
            seen.update(t["ticket_id"] for t in chosen)

        # If we're short, fill randomly from remaining
        if len(sampled) < target_n:
            remaining = [t for t in tickets if t["ticket_id"] not in seen]
            fill = random.sample(
                remaining, min(target_n - len(sampled), len(remaining))
            )
            sampled.extend(fill)

        return sampled[:target_n]

    def _stratified_sample_csat(self, tickets, target_n):
        """Sample stratified by CSAT quartile."""
        # Partition by CSAT score
        quartiles = defaultdict(list)
        for t in tickets:
            csat = t.get("csat_score")
            if csat is None:
                quartiles["no_csat"].append(t)
            elif csat <= 2:
                quartiles["low"].append(t)
            elif csat <= 3:
                quartiles["mid"].append(t)
            else:
                quartiles["high"].append(t)

        total = len(tickets)
        sampled = []
        seen = set()

        for q_name, pool in quartiles.items():
            if not pool:
                continue
            alloc = max(1, int(target_n * len(pool) / total))
            chosen = random.sample(pool, min(alloc, len(pool)))
            sampled.extend(chosen)
            seen.update(t["ticket_id"] for t in chosen)

        # Fill if short
        if len(sampled) < target_n:
            remaining = [t for t in tickets if t["ticket_id"] not in seen]
            fill = random.sample(
                remaining, min(target_n - len(sampled), len(remaining))
            )
            sampled.extend(fill)

        return sampled[:target_n]

    # ═══════════════════════════════════════════════════════════════
    #  JSONL PACKAGING (T2)
    # ═══════════════════════════════════════════════════════════════

    def _package_ticket_jsonl(self, ticket, scan_id=None):
        """Single ticket → redacted JSONL line.

        Truncates full_thread to ANALYSIS_TRUNCATION.
        Applies PII redaction. Includes NLP fields if available.
        """
        from src.gemini.gemini_client import GeminiClient

        thread = ticket.get("full_thread", "") or ""
        subject = ticket.get("subject", "") or ""

        # Truncate
        thread = thread[:self.ANALYSIS_TRUNCATION]
        subject = subject[:200]

        # Redact
        thread = GeminiClient._redact_base(None, thread)
        thread = GeminiClient._redact_aggressive(None, thread)
        subject = GeminiClient._redact_base(None, subject)
        subject = GeminiClient._redact_aggressive(None, subject)

        record = {
            "ticket_id": ticket.get("ticket_id", ""),
            "trc": ticket.get("trc_code", ""),
            "created_at": (ticket.get("created_at", "") or "")[:10],
            "subject": subject,
            "csat": ticket.get("csat_score"),
            "thread": thread,
        }

        # Enrich with NLP classification if available
        if scan_id:
            nlp = self._get_ticket_nlp(ticket["ticket_id"], scan_id)
            if nlp:
                record["nlp_friction"] = nlp.get("friction_type", "")
                record["nlp_sentiment"] = nlp.get("sentiment_polarity", "")
                record["nlp_root_cause"] = nlp.get("root_cause_hint", "")

        return json.dumps(record, ensure_ascii=False)

    def _get_ticket_nlp(self, ticket_id, scan_id):
        """Get NLP classification for a single ticket."""
        try:
            row = self.db.conn.execute("""
                SELECT friction_type, sentiment_polarity, root_cause_hint,
                       anomaly_flag, sub_cluster
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND ticket_id = ?
            """, (scan_id, ticket_id)).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    #  NLP CONTEXT ASSEMBLY (T3)
    # ═══════════════════════════════════════════════════════════════

    def _build_nlp_context_for_trc(self, trc, scan_id=None):
        """Format NLP aggregates as text block for prompt injection.
        Returns '' if no NLP data.
        """
        agg = self.db.get_nlp_aggregate_for_trc(trc, scan_id)
        if not agg:
            return ""

        lines = ["NLP SCAN CONTEXT (from latest scan):"]
        lines.append(f"  Classified: {agg['total_classified']} tickets")

        # Friction distribution
        if agg.get("friction_distribution"):
            total = sum(agg["friction_distribution"].values())
            parts = []
            for ftype, cnt in sorted(
                agg["friction_distribution"].items(),
                key=lambda x: x[1], reverse=True
            ):
                pct = round(cnt / total * 100) if total else 0
                parts.append(f"{ftype}={pct}%")
            lines.append(f"  Friction distribution: {', '.join(parts)}")

        # Sentiment distribution
        if agg.get("sentiment_distribution"):
            total = sum(agg["sentiment_distribution"].values())
            parts = []
            for pol, cnt in sorted(
                agg["sentiment_distribution"].items(),
                key=lambda x: x[1], reverse=True
            ):
                pct = round(cnt / total * 100) if total else 0
                parts.append(f"{pol}={pct}%")
            lines.append(f"  Sentiment: {', '.join(parts)}")

        if agg.get("avg_sentiment_intensity") is not None:
            lines.append(
                f"  Avg sentiment intensity: {agg['avg_sentiment_intensity']}"
            )

        # Top sub-patterns
        if agg.get("top_sub_clusters"):
            parts = [
                f'"{label}" ({cnt} tickets)'
                for label, cnt in agg["top_sub_clusters"][:5]
            ]
            lines.append(f"  Top sub-patterns: {', '.join(parts)}")

        # Anomalies
        if agg.get("anomaly_counts"):
            parts = [
                f"{cnt} {flag}" for flag, cnt in agg["anomaly_counts"].items()
            ]
            lines.append(f"  Anomalies: {', '.join(parts)}")

        # Root causes
        if agg.get("top_root_cause_hints"):
            parts = [
                f'"{hint}"' for hint, cnt in agg["top_root_cause_hints"][:5]
            ]
            lines.append(f"  Top root causes: {', '.join(parts)}")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    #  STATISTICAL CONTEXT ASSEMBLY (T4)
    # ═══════════════════════════════════════════════════════════════

    def _build_stat_context_for_trc(self, trc, date_start, date_end):
        """Per-TRC stats via existing report builder.

        9.0 T4: Cached by (trc, date_start, date_end) to eliminate
        redundant trending engine + incident scan + correlation calls.
        """
        cache_key = (trc, date_start, date_end)
        if cache_key in self._stat_context_cache:
            return self._stat_context_cache[cache_key]

        try:
            from src.data.report_builder import (
                build_data_block, format_data_block_for_prompt
            )
            block = build_data_block(
                self.db, date_start, date_end, trc_filter=trc
            )
            result = format_data_block_for_prompt(block)
        except Exception as e:
            logger.warning(f"Failed to build stat context for {trc}: {e}")
            result = f"[Statistical context unavailable: {e}]"

        self._stat_context_cache[cache_key] = result
        return result

    def _build_global_stat_context(self, date_start, date_end):
        """Full-population statistical context (cross-TRC) for synthesis."""
        try:
            from src.data.report_builder import (
                build_data_block, format_data_block_for_prompt
            )
            block = build_data_block(self.db, date_start, date_end)
            return format_data_block_for_prompt(block)
        except Exception as e:
            logger.warning(f"Failed to build global stat context: {e}")
            return f"[Statistical context unavailable: {e}]"

    # ═══════════════════════════════════════════════════════════════
    #  PROMPT ASSEMBLY
    # ═══════════════════════════════════════════════════════════════

    def _assemble_analysis_prompt(self, trc, trc_label, date_start, date_end,
                                   n_tickets, total_trc_tickets,
                                   nlp_context, statistical_context,
                                   ticket_jsonl):
        """Load voc_analysis.txt template and inject variables."""
        template_path = _PROMPTS_DIR / "voc_analysis.txt"
        template = template_path.read_text(encoding="utf-8")

        sample_pct = round(n_tickets / max(total_trc_tickets, 1) * 100)

        prompt = template.replace("{trc_code}", trc)
        prompt = prompt.replace("{trc_label}", trc_label or trc)
        prompt = prompt.replace("{date_start}", date_start)
        prompt = prompt.replace("{date_end}", date_end)
        prompt = prompt.replace("{n_tickets}", str(n_tickets))
        prompt = prompt.replace("{total_trc_tickets}", str(total_trc_tickets))
        prompt = prompt.replace("{sample_pct}", str(sample_pct))
        prompt = prompt.replace("{nlp_context}", nlp_context or "(No NLP scan data available)")
        prompt = prompt.replace("{statistical_context}", statistical_context or "(No statistical context available)")
        prompt = prompt.replace("{ticket_jsonl}", ticket_jsonl)

        return prompt

    def _assemble_synthesis_prompt(self, date_start, date_end,
                                    total_tickets, sampled_tickets,
                                    trc_count, full_statistical_context,
                                    trc_analyses, model_health_context=None):
        """Load voc_synthesis.txt template and inject variables."""
        template_path = _PROMPTS_DIR / "voc_synthesis.txt"
        template = template_path.read_text(encoding="utf-8")

        # Build 7.0: Temporal context for VOC synthesis
        temporal_ctx = "(No temporal data available)"
        try:
            from src.data.report_builder import (
                build_windowed_data_blocks, format_temporal_context,
            )
            windowed = build_windowed_data_blocks(
                self.db, date_start, date_end
            )
            temporal_ctx = format_temporal_context(windowed)
        except Exception:
            pass

        prompt = template.replace("{date_start}", date_start)
        prompt = prompt.replace("{date_end}", date_end)
        prompt = prompt.replace("{total_tickets}", str(total_tickets))
        prompt = prompt.replace("{sampled_tickets}", str(sampled_tickets))
        prompt = prompt.replace("{trc_count}", str(trc_count))
        prompt = prompt.replace("{full_statistical_context}", full_statistical_context or "(No statistical context available)")
        prompt = prompt.replace("{model_health_context}", model_health_context or "(No model health data available)")
        prompt = prompt.replace("{temporal_context}", temporal_ctx)
        prompt = prompt.replace("{trc_analyses}", trc_analyses)

        return prompt

    def _build_model_health_context(self, scan_id):
        """Build model health context string from probe + scan event data (6.3).

        Returns a formatted string for injection into the synthesis prompt,
        or None if no probe data exists.
        """
        try:
            summary = self.db.get_probe_summary(scan_id=scan_id, days=7)

            if not summary or summary["total_probes"] == 0:
                return None

            lines = ["[MODEL HEALTH CONTEXT]"]

            # Current scan probes
            if summary["current_p50"] is not None:
                lines.append(
                    f"Pre-scan probe latency: P50={summary['current_p50']}ms, "
                    f"P95={summary['current_p95']}ms "
                    f"({summary['scan_probes']} bridges probed)"
                )

            # Historical
            if summary["historical_p50"] is not None:
                lines.append(
                    f"Historical 7-day: P50={summary['historical_p50']}ms, "
                    f"P95={summary['historical_p95']}ms "
                    f"({summary['total_probes']} probes, "
                    f"{summary['success_rate']}% success)"
                )

            # Scan stall/death events from scan_events
            if scan_id:
                try:
                    stall_count = self.db.conn.execute("""
                        SELECT COUNT(*) FROM scan_events
                        WHERE scan_id = ? AND message LIKE '%stall%'
                    """, (scan_id,)).fetchone()[0]
                    death_count = self.db.conn.execute("""
                        SELECT COUNT(*) FROM scan_events
                        WHERE scan_id = ? AND message LIKE '%bridge%died%'
                    """, (scan_id,)).fetchone()[0]
                    restart_count = self.db.conn.execute("""
                        SELECT COUNT(*) FROM scan_events
                        WHERE scan_id = ? AND message LIKE '%restart%'
                    """, (scan_id,)).fetchone()[0]
                    lines.append(
                        f"Scan events: {stall_count} stall(s), "
                        f"{death_count} bridge death(s), "
                        f"{restart_count} restart(s)"
                    )
                except Exception:
                    pass

            return "\n".join(lines) if len(lines) > 1 else None

        except Exception as e:
            logger.debug(f"Model health context build failed: {e}")
            return None

    # Max chars per TRC analysis in the synthesis prompt.  With 127 TRCs
    # at full length (~1700 ch each) the prompt exceeds Gemini's comfortable
    # working set and triggers loop detection.  800 chars retains the key
    # findings / themes while keeping total analysis text under ~110K chars.
    SYNTHESIS_ANALYSIS_TRUNCATION = 800

    def _format_trc_analyses_for_synthesis(self, trc_analyses):
        """Format all TRC analyses into a single text block for synthesis.

        Each individual analysis is truncated to SYNTHESIS_ANALYSIS_TRUNCATION
        chars to keep the combined prompt within Gemini's effective window.
        """
        sections = []
        trunc = self.SYNTHESIS_ANALYSIS_TRUNCATION
        for trc, analysis in trc_analyses.items():
            text = analysis or ""
            if len(text) > trunc:
                text = text[:trunc] + "\n[...truncated for synthesis]"
            sections.append(
                f"{'=' * 60}\n"
                f"TRC: {trc}\n"
                f"{'=' * 60}\n"
                f"{text}\n"
            )
        return "\n".join(sections)

    # ═══════════════════════════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _get_trc_label(self, trc_code):
        """Look up TRC label from warehouse tables."""
        try:
            from src.data.source_registry import SourceRegistry
            from src.data.warehouse_query import WarehouseQuery
            registry = SourceRegistry(self.db.conn)
            wq = WarehouseQuery(self.db.conn, registry)
            rows = wq.query_conversations_raw(
                "SELECT DISTINCT trc_label FROM {table} WHERE trc_code = ? AND trc_label IS NOT NULL AND trc_label != '' LIMIT 1",
                (trc_code,),
            )
            return rows[0][0] if rows and rows[0][0] else trc_code
        except Exception:
            return trc_code

    def _log_usage(self, input_chars, output_chars, source):
        """Log API usage if tracker is available."""
        try:
            from src.data.usage_tracker import UsageTracker
            tracker = UsageTracker(self.db)
            # Rough token estimate: 1 token ≈ 4 chars
            tracker.log_call(
                source=source,
                tokens_in=input_chars // 4,
                tokens_out=output_chars // 4,
                model=self.gemini.model,
            )
        except Exception:
            pass

    def _persist_report(self, date_start, date_end, trc_filter,
                        report_text, trc_analyses, plan, duration_ms):
        """Save the VOC report to history."""
        try:
            self.db.save_report(
                page="ai_reports",
                parameters={
                    "report_type": "voc_rca",
                    "date_start": date_start,
                    "date_end": date_end,
                    "trc_filter": trc_filter,
                    "trc_count": len(plan["trc_plans"]),
                    "total_tickets": plan["total_tickets"],
                    "sampled_tickets": plan["total_sampled"],
                    "gemini_calls": plan["total_gemini_calls"],
                    "model": plan["model"],
                    "has_nlp_data": plan["has_nlp_data"],
                },
                summary=report_text[:500],
                full_results=json.dumps({
                    "report_text": report_text,
                    "trc_analyses": trc_analyses,
                }),
                ticket_count=plan["total_tickets"],
                duration_ms=duration_ms,
                report_type="voc",
            )
        except Exception as e:
            logger.error(f"Failed to persist VOC report: {e}")
