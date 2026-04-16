"""
Alma Insights -- Analyst Agent (Pass 5.0)

Post-scan LLM agent for judgment tasks. Runs AFTER all classification
batches complete. Uses a dedicated bridge instance for its own
context window.

Four analysis tasks:
  1. Cross-TRC Synthesis — find shared root causes across TRCs
  2. Quality Audit — grade classification accuracy on a random sample
  3. Novelty Validation — confirm novel patterns are genuinely new
  4. Pattern Merge — suggest duplicate pattern merges

Each task loads data from SQLite, builds an analysis prompt, calls
the bridge (blocking mode), parses the structured response, and
stores results to the analyst_reports table.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.analyst")


class AnalystAgent:
    """
    Post-scan LLM agent for cross-TRC synthesis, quality audit,
    novelty validation, and pattern merge suggestions.

    Usage:
        analyst = AnalystAgent(bridge, db_path)
        analyst.run_cross_trc_synthesis(scan_id)
        analyst.run_quality_audit(scan_id)
        analyst.run_novelty_validation(scan_id)
        analyst.run_pattern_merge(scan_id)
    """

    def __init__(self, bridge, db_path):
        """
        Args:
            bridge: GeminiBridge instance (dedicated to analyst)
            db_path: Path to SQLite database
        """
        self.bridge = bridge
        self.db_path = db_path
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = get_connection(self.db_path)
        return self._conn

    # ──────────────────────────────────────────────────────────────────────
    # 1. Cross-TRC Synthesis
    # ──────────────────────────────────────────────────────────────────────

    def run_cross_trc_synthesis(self, scan_id):
        """
        Find shared root causes, correlated patterns, and systemic
        issues that span multiple TRCs.

        Loads classification summaries grouped by TRC, sends to LLM
        for cross-referencing, stores synthesis report.
        """
        logger.info(f"Analyst: running cross-TRC synthesis for {scan_id}")

        # Load classification data grouped by TRC
        try:
            rows = self.conn.execute("""
                SELECT trc,
                       COUNT(*) as ticket_count,
                       GROUP_CONCAT(DISTINCT sub_cluster) as sub_clusters,
                       GROUP_CONCAT(DISTINCT friction_type) as friction_types,
                       AVG(sentiment_intensity) as avg_sentiment,
                       SUM(CASE WHEN anomaly_flag = 'critical' THEN 1 ELSE 0 END) as critical_count,
                       SUM(CASE WHEN is_novel = 1 THEN 1 ELSE 0 END) as novel_count
                FROM nlp_ticket_classifications
                WHERE scan_id = ?
                GROUP BY trc
                ORDER BY ticket_count DESC
            """, (scan_id,)).fetchall()
        except Exception as e:
            logger.error(f"Analyst: cross-TRC data load failed: {e}")
            return None

        if not rows:
            logger.warning(f"Analyst: no classifications for {scan_id}")
            return None

        # Build analysis prompt
        trc_summaries = []
        for r in rows:
            sub_clusters = (r["sub_clusters"] or "").split(",")[:10]
            trc_summaries.append(
                f"TRC: {r['trc']} ({r['ticket_count']} tickets)\n"
                f"  Sub-patterns: {', '.join(sub_clusters)}\n"
                f"  Friction types: {r['friction_types']}\n"
                f"  Avg sentiment: {r['avg_sentiment']:.1f}/5\n"
                f"  Critical anomalies: {r['critical_count']}\n"
                f"  Novel patterns: {r['novel_count']}"
            )

        prompt = f"""You are analyzing classification results from an NLP scan of {len(rows)} TRCs.

CLASSIFICATION SUMMARY BY TRC:
{chr(10).join(trc_summaries)}

TASK: Identify cross-TRC patterns and systemic issues.

Output a JSON object:
{{
  "shared_root_causes": [
    {{
      "cause": "<root cause description>",
      "affected_trcs": ["TRC1", "TRC2"],
      "evidence": "<what patterns suggest this>"
    }}
  ],
  "systemic_issues": [
    {{
      "issue": "<systemic issue>",
      "scope": "<how widespread>",
      "severity": "<low|medium|high|critical>"
    }}
  ],
  "correlations": [
    {{
      "trc_a": "...",
      "trc_b": "...",
      "correlation": "<what links them>"
    }}
  ],
  "summary": "<2-3 sentence executive summary>"
}}

Output ONLY valid JSON. No markdown. No preamble."""

        # Call bridge
        result = self._call_bridge(prompt, f"analyst_synthesis_{scan_id}")
        if not result:
            return None

        # Parse and store
        parsed = self._parse_json_response(result)
        self._store_report(scan_id, "synthesis", result, {
            "trc_count": len(rows),
            "shared_causes": len(parsed.get("shared_root_causes", [])) if parsed else 0,
        })

        return parsed

    # ──────────────────────────────────────────────────────────────────────
    # 2. Quality Audit
    # ──────────────────────────────────────────────────────────────────────

    def run_quality_audit(self, scan_id, sample_size=25):
        """
        Grade classification accuracy on a random sample.

        Loads classifications + their source conversations,
        asks the LLM to grade each one, reports quality score.
        """
        logger.info(
            f"Analyst: running quality audit for {scan_id} "
            f"(sample={sample_size})"
        )

        # Load classifications with conversation text
        try:
            all_rows = self.conn.execute("""
                SELECT c.classification_id, c.ticket_id, c.trc,
                       c.sub_cluster, c.friction_type,
                       c.sentiment_intensity, c.sentiment_polarity,
                       c.anomaly_flag, c.summary,
                       c.sub_cluster_confidence, c.is_novel,
                       conv.full_thread as thread_sample
                FROM nlp_ticket_classifications c
                LEFT JOIN conversations conv
                    ON conv.ticket_id = c.ticket_id
                WHERE c.scan_id = ?
            """, (scan_id,)).fetchall()
        except Exception as e:
            logger.error(f"Analyst: quality audit data load failed: {e}")
            return None

        if not all_rows:
            return None

        # Random sample
        sample = random.sample(
            all_rows,
            min(sample_size, len(all_rows))
        )

        # Build audit prompt
        items = []
        for r in sample:
            thread = (r["thread_sample"] or "")[:500]
            items.append(
                f"---\n"
                f"ticket_id: {r['ticket_id']}\n"
                f"trc: {r['trc']}\n"
                f"thread_excerpt: {thread}\n"
                f"classified_as:\n"
                f"  sub_cluster: {r['sub_cluster']}\n"
                f"  friction_type: {r['friction_type']}\n"
                f"  sentiment: {r['sentiment_intensity']}/5 {r['sentiment_polarity']}\n"
                f"  anomaly: {r['anomaly_flag']}\n"
                f"  is_novel: {r['is_novel']}\n"
                f"  confidence: {r['sub_cluster_confidence']}\n"
                f"  summary: {r['summary']}"
            )

        prompt = f"""You are auditing the quality of NLP classifications.

For each ticket below, grade the classification accuracy:
- CORRECT: Classification accurately reflects the ticket content
- PARTIAL: Classification is close but misses key aspects
- INCORRECT: Classification does not match the ticket content
- UNCERTAIN: Not enough information to judge

{chr(10).join(items)}

Output a JSON object:
{{
  "grades": [
    {{
      "ticket_id": "...",
      "grade": "CORRECT|PARTIAL|INCORRECT|UNCERTAIN",
      "notes": "<brief explanation>"
    }}
  ],
  "quality_score": <0.0-1.0 overall accuracy>,
  "common_errors": ["<error pattern 1>", "<error pattern 2>"],
  "recommendations": ["<improvement suggestion>"]
}}

Output ONLY valid JSON."""

        result = self._call_bridge(prompt, f"analyst_audit_{scan_id}")
        if not result:
            return None

        parsed = self._parse_json_response(result)
        self._store_report(scan_id, "audit", result, {
            "sample_size": len(sample),
            "quality_score": parsed.get("quality_score") if parsed else None,
        })

        return parsed

    # ──────────────────────────────────────────────────────────────────────
    # 3. Novelty Validation
    # ──────────────────────────────────────────────────────────────────────

    def run_novelty_validation(self, scan_id, input_budget=None):
        """
        Validate that patterns marked as novel are genuinely new
        and not duplicates of existing patterns.

        Batches novel tickets to avoid exceeding model input limits.
        Returns aggregated validation results across all batches.

        Args:
            scan_id: Scan identifier
            input_budget: Max input chars per bridge call (default: 100_000)
        """
        logger.info(f"Analyst: running novelty validation for {scan_id}")
        budget = input_budget or 100_000

        # Load ALL novel classifications (no truncation)
        try:
            novels = self.conn.execute("""
                SELECT ticket_id, trc, sub_cluster, key_phrases,
                       root_cause_hint, summary
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND is_novel = 1
                ORDER BY trc
            """, (scan_id,)).fetchall()
        except Exception as e:
            logger.error(f"Analyst: novelty data load failed: {e}")
            return None

        if not novels:
            logger.info("Analyst: no novel patterns to validate")
            empty = {
                "validations": [],
                "summary": {"validated": 0, "rejected": 0, "merged": 0},
            }
            self._store_report(scan_id, "novelty", json.dumps(empty), {
                "total_novels": 0, "validated": 0,
                "rejected": 0, "merged": 0,
            })
            return empty

        # Load existing patterns for comparison context
        try:
            existing = self.conn.execute("""
                SELECT trc, label, description, friction_type
                FROM sub_patterns
                WHERE tier IN ('active', 'probationary')
                  AND merged_into IS NULL
                ORDER BY lifetime_tickets DESC
                LIMIT 50
            """).fetchall()
        except Exception:
            existing = []

        existing_list = [
            f"{e['trc']}: {e['label']} ({e['friction_type']})"
            for e in existing
        ]
        existing_block = chr(10).join(existing_list)
        existing_block_chars = len(existing_block)

        # Build per-novel text items
        novel_items = []
        for n in novels:
            kp = n["key_phrases"] or "[]"
            item_text = (
                f"ticket_id: {n['ticket_id']}\n"
                f"  trc: {n['trc']}\n"
                f"  sub_cluster: {n['sub_cluster']}\n"
                f"  key_phrases: {kp}\n"
                f"  root_cause: {n['root_cause_hint']}"
            )
            novel_items.append(item_text)

        # Prompt overhead: instructions + existing patterns + JSON template
        prompt_overhead = 500 + existing_block_chars + 400

        # Batch novels so each prompt stays within input budget
        batches = []
        current_batch = []
        current_chars = prompt_overhead
        for item in novel_items:
            item_chars = len(item) + 1  # +1 for newline separator
            if current_batch and (current_chars + item_chars) > budget:
                batches.append(current_batch)
                current_batch = [item]
                current_chars = prompt_overhead + item_chars
            else:
                current_batch.append(item)
                current_chars += item_chars
        if current_batch:
            batches.append(current_batch)

        logger.info(
            f"Analyst: novelty validation: {len(novels)} novels "
            f"in {len(batches)} batch(es)"
        )

        # Process each batch
        all_validations = []
        total_validated = 0
        total_rejected = 0
        total_merged = 0

        for batch_idx, batch in enumerate(batches):
            prompt = f"""You are validating novel sub-pattern classifications.

EXISTING PATTERNS ({len(existing_list)}):
{existing_block}

NOVEL CLASSIFICATIONS ({len(batch)} tickets, batch {batch_idx + 1}/{len(batches)}):
{chr(10).join(batch)}

For each novel classification, determine:
- VALID: Genuinely new pattern not covered by existing ones
- DUPLICATE: Matches an existing pattern (specify which)
- MERGE: Should be merged with another novel classification

Output a JSON object:
{{
  "validations": [
    {{
      "ticket_id": "...",
      "verdict": "VALID|DUPLICATE|MERGE",
      "existing_match": "<matching pattern label or null>",
      "notes": "<explanation>"
    }}
  ],
  "summary": {{
    "validated": <count>,
    "rejected": <count>,
    "merged": <count>
  }}
}}

Output ONLY valid JSON."""

            result = self._call_bridge(
                prompt,
                f"analyst_novelty_{scan_id}_b{batch_idx}"
            )
            if not result:
                logger.warning(
                    f"Analyst: novelty batch {batch_idx + 1}/{len(batches)} "
                    f"failed — skipping"
                )
                continue

            parsed = self._parse_json_response(result)
            if parsed:
                batch_validations = parsed.get("validations", [])
                all_validations.extend(batch_validations)
                summary = parsed.get("summary", {})
                total_validated += summary.get("validated", 0)
                total_rejected += summary.get("rejected", 0)
                total_merged += summary.get("merged", 0)

        # Aggregate result
        aggregated = {
            "validations": all_validations,
            "summary": {
                "validated": total_validated,
                "rejected": total_rejected,
                "merged": total_merged,
            },
        }

        self._store_report(scan_id, "novelty", json.dumps(aggregated), {
            "total_novels": len(novels),
            "batches": len(batches),
            "validated": total_validated,
            "rejected": total_rejected,
            "merged": total_merged,
        })

        return aggregated

    def apply_novelty_verdicts(self, scan_id, validations):
        """
        Write DUPLICATE and MERGE verdicts to nlp_ticket_classifications
        so the meta-analyzer can use them as advisory signals.

        DUPLICATE verdicts set is_novel = 0 (not truly novel).
        MERGE verdicts tag the ticket but keep is_novel = 1.
        VALID verdicts require no update (default state).

        Args:
            scan_id: Scan identifier
            validations: List of verdict dicts from run_novelty_validation()
        """
        if not validations:
            return

        dup_count = 0
        merge_count = 0

        for v in validations:
            ticket_id = v.get("ticket_id")
            verdict = (v.get("verdict") or "").upper()
            existing_match = v.get("existing_match")

            if not ticket_id:
                continue

            if verdict == "DUPLICATE":
                try:
                    self.conn.execute("""
                        UPDATE nlp_ticket_classifications
                        SET is_novel = 0,
                            novelty_verdict = ?,
                            novelty_match = ?
                        WHERE scan_id = ? AND ticket_id = ?
                    """, (verdict, existing_match, scan_id, ticket_id))
                    dup_count += 1
                except Exception as e:
                    logger.warning(
                        f"Analyst: verdict write failed for {ticket_id}: {e}"
                    )

            elif verdict == "MERGE":
                try:
                    self.conn.execute("""
                        UPDATE nlp_ticket_classifications
                        SET novelty_verdict = ?,
                            novelty_match = ?
                        WHERE scan_id = ? AND ticket_id = ?
                    """, (verdict, existing_match, scan_id, ticket_id))
                    merge_count += 1
                except Exception as e:
                    logger.warning(
                        f"Analyst: verdict write failed for {ticket_id}: {e}"
                    )

        self.conn.commit()
        logger.info(
            f"Analyst: applied novelty verdicts for {scan_id} — "
            f"{dup_count} duplicates downgraded, {merge_count} merges tagged"
        )

    # ──────────────────────────────────────────────────────────────────────
    # 4. Pattern Merge
    # ──────────────────────────────────────────────────────────────────────

    def run_pattern_merge(self, scan_id):
        """
        Suggest merges between similar sub-patterns across TRCs.
        """
        logger.info(f"Analyst: running pattern merge analysis for {scan_id}")

        # Load all active patterns
        try:
            patterns = self.conn.execute("""
                SELECT pattern_id, trc, label, description,
                       friction_type, lifetime_tickets
                FROM sub_patterns
                WHERE tier IN ('active', 'probationary')
                  AND merged_into IS NULL
                ORDER BY trc, lifetime_tickets DESC
            """).fetchall()
        except Exception as e:
            logger.error(f"Analyst: pattern merge data load failed: {e}")
            return None

        if len(patterns) < 2:
            return {"merge_suggestions": [], "count": 0}

        pattern_list = [
            f"{p['trc']}: \"{p['label']}\" "
            f"(friction={p['friction_type']}, "
            f"tickets={p['lifetime_tickets']})"
            for p in patterns
        ]

        prompt = f"""You are analyzing sub-patterns for potential merges.

ACTIVE PATTERNS ({len(patterns)}):
{chr(10).join(pattern_list[:50])}

Find patterns that describe the same underlying issue but with different
labels. Consider patterns across different TRCs that share root causes.

Output a JSON object:
{{
  "merge_suggestions": [
    {{
      "primary_label": "<label to keep>",
      "merge_candidates": ["<label to merge>", "..."],
      "rationale": "<why these should be merged>",
      "confidence": <0.0-1.0>
    }}
  ],
  "count": <number of suggestions>
}}

Output ONLY valid JSON."""

        result = self._call_bridge(prompt, f"analyst_merge_{scan_id}")
        if not result:
            return None

        parsed = self._parse_json_response(result)
        self._store_report(scan_id, "merge", result, {
            "total_patterns": len(patterns),
            "merge_suggestions": parsed.get("count", 0) if parsed else 0,
        })

        return parsed

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _call_bridge(self, prompt, request_id):
        """
        Make a blocking bridge call with error handling.

        Returns response text or None on failure.
        """
        try:
            self.bridge.ensure_running()
            result = self.bridge.call_blocking(
                prompt, request_id, timeout=180
            )
            return result
        except Exception as e:
            logger.error(f"Analyst: bridge call failed: {e}")
            return None

    def _parse_json_response(self, text):
        """Parse a JSON response from the LLM, with fallback."""
        import re

        if not text:
            return None

        cleaned = text.strip()

        # Strip markdown fences
        if cleaned.startswith("```"):
            cleaned = cleaned.lstrip("`").lstrip("json").lstrip("\n")
            cleaned = cleaned.rstrip("`").rstrip("\n")

        # Fix trailing commas
        cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try regex extraction
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(
                    re.sub(r",\s*([\]}])", r"\1", match.group())
                )
            except json.JSONDecodeError:
                pass

        logger.warning(
            f"Analyst: failed to parse JSON response "
            f"({len(text)} chars)"
        )
        return None

    def _store_report(self, scan_id, report_type, content, metrics):
        """Store an analyst report to the database."""
        try:
            self.conn.execute("""
                INSERT INTO analyst_reports
                    (scan_id, report_type, content, metrics, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                scan_id,
                report_type,
                content,
                json.dumps(metrics),
                datetime.utcnow().isoformat(),
            ))
            self.conn.commit()
            logger.info(
                f"Analyst: stored {report_type} report for {scan_id}"
            )
        except Exception as e:
            logger.error(f"Analyst: failed to store report: {e}")

    def shutdown(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __repr__(self):
        return f"AnalystAgent(bridge={self.bridge})"
