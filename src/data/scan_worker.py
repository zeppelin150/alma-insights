"""
Alma Insights — NLP Scan Worker (CLI Mode)
Detached subprocess for batch NLP classification via Gemini CLI.

Communicates exclusively via SQLite. Uses existing GeminiClient (CLI route)
for all Gemini calls. No new network connections.

Usage:
    python -m src.data.scan_worker --db <path> --scan <scan_id>
                                   [--worker <worker_id>]
"""

from __future__ import annotations

import sys
import os
import json
import time
import re
import uuid as uuid_mod
import argparse
import logging
import sqlite3
from pathlib import Path
from datetime import datetime
from collections import Counter

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.connection_factory import get_connection
from src.gemini.gemini_client import GeminiClient

logger = logging.getLogger("alma.scan_worker")

# ── Hard cap: CLI mode cannot exceed this ──
MAX_TICKETS_CLI_MODE = 50_000


class ScanWorker:
    """Legacy subprocess-per-batch scan worker.

    Superseded by the persistent-worker model in `src.agents.WorkerAgent`,
    which boots the Gemini bridge once and stays alive across batches.
    Retained here for backward-compatibility and fallback scenarios.
    """

    def __init__(self, db_path: str, scan_id: str, worker_id: int = 0) -> None:
        self.db_path = str(Path(db_path).resolve())
        self.scan_id = scan_id
        self.worker_id = worker_id
        self.gemini = GeminiClient()
        self.running = True

        # Verify Gemini CLI is available
        if not self.gemini.is_available():
            logger.error("Gemini CLI not found. Cannot proceed.")
            self._open_conn()
            self._update_status('failed')
            self.conn.close()
            sys.exit(1)

        # Open dedicated SQLite connection (WAL mode)
        self._open_conn()

        # Per-ticket TRC lookup (populated in _get_tickets_for_batch)
        self._ticket_trc_map = {}

        # Load the prompt template once
        template_path = (Path(__file__).parent.parent.parent
                         / 'config' / 'prompts' / 'nlp_classify.txt')
        try:
            self._prompt_template = template_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            logger.error(f"Prompt template not found: {template_path}")
            self._prompt_template = ""

    def _open_conn(self):
        """Open a SQLite connection with WAL mode and busy timeout."""
        logger.info(f"Connecting to DB: {self.db_path}")
        self.conn = get_connection(self.db_path)

    def run(self) -> None:
        """
        Main loop. Process batches until:
        - All batches complete
        - Status set to paused/cancelled (by PySide6 app)
        - Budget cap reached
        - Error threshold exceeded
        """
        logger.info(f"Worker {self.worker_id} starting scan {self.scan_id}")
        consecutive_errors = 0
        max_consecutive_errors = 5

        while self.running:
            # ── Check scan status (pause/cancel from UI) ──
            scan = self._read_scan()
            if not scan:
                logger.error(f"Scan {self.scan_id} not found")
                break

            if scan['status'] in ('paused', 'cancelled', 'failed',
                                   'scan_complete', 'analysis_complete'):
                logger.info(f"Scan {scan['status']} — stopping worker")
                break

            # ── Budget cap check ──
            cost = scan['actual_cost_usd'] or 0
            cap = scan['budget_cap_usd'] or 50.0
            if cost >= cap:
                logger.info(f"Budget cap reached: ${cost:.2f} / ${cap:.2f}")
                self._update_status('paused')
                break

            # ── Get next batch for this worker ──
            batch = self._get_next_batch()
            if not batch:
                # Check if ALL workers are done (not just this one)
                remaining = self._count_remaining_batches()
                if remaining == 0:
                    # Check if any batches failed
                    failed_count = self._count_failed_batches()
                    total = self._read_scan().get('total_batches', 0)
                    if failed_count > 0 and failed_count == total:
                        self._update_status('failed')
                        logger.info(f"All {failed_count} batches failed")
                    elif failed_count > 0:
                        self._update_status('scan_complete')
                        logger.info(f"Scan complete "
                                    f"({total - failed_count} ok, "
                                    f"{failed_count} failed)")
                    else:
                        self._update_status('scan_complete')
                        logger.info("All batches complete")
                else:
                    logger.info(f"No batches for worker {self.worker_id}, "
                                f"{remaining} remaining for other workers")
                break

            # ── Process batch ──
            try:
                self._process_batch(dict(batch), dict(scan))
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                error_str = str(e)
                logger.error(f"Batch {batch['batch_id']} failed: {error_str}")
                is_quota = self._is_quota_error(error_str)
                self._handle_batch_error(dict(batch), error_str)

                if is_quota:
                    # Quota/rate-limit: wait before retrying
                    wait_secs = min(120, 30 * consecutive_errors)
                    logger.info(f"Quota error detected — waiting "
                                f"{wait_secs}s before retry")
                    time.sleep(wait_secs)

                if consecutive_errors >= max_consecutive_errors:
                    # Check if all errors were quota — mark differently
                    if is_quota:
                        logger.error(
                            f"{max_consecutive_errors} consecutive quota "
                            f"errors — marking scan as quota_exhausted")
                        self._update_status('quota_exhausted')
                    else:
                        logger.error(
                            f"{max_consecutive_errors} consecutive "
                            f"errors — pausing scan")
                        self._update_status('paused')
                    break

        # ── Final check: finalize scan if we're the last worker ──
        try:
            self._try_finalize_scan()
        except Exception:
            pass

        self.conn.close()
        logger.info(f"Worker {self.worker_id} finished")

    def _process_batch(self, batch, scan):
        """Build prompt, call Gemini via CLI, parse, store."""
        start_time = time.time()
        batch_id = batch['batch_id']
        trc = batch['trc']
        if trc.startswith('['):
            try:
                trc_list = json.loads(trc)
                trc_display = f"Mixed ({len(trc_list)} TRCs)"
            except Exception:
                trc_display = trc
        else:
            trc_display = trc
        logger.info(f"Batch {batch['batch_number']}: TRC='{trc_display}' "
                     f"chunk {batch['trc_chunk']}/{batch['trc_chunk_total']}")

        # Mark batch running
        self._update_batch_status(batch_id, 'running')

        # 1. Pull tickets for this TRC
        tickets = self._get_tickets_for_batch(batch)
        if not tickets:
            self._update_batch_status(batch_id, 'completed',
                                      ticket_count=0)
            return

        # 2. Build prompt (uses existing GeminiClient redaction)
        prompt, system_prompt = self._build_prompt(batch, tickets)
        input_tokens = self._estimate_tokens(prompt)

        # 3. Call Gemini via CLI (EXISTING APPROVED ROUTE)
        # Timeout: 60s base + ~1.5s per ticket (output generation dominates)
        # 700 tickets ≈ ~17 min, 100 tickets ≈ ~3 min
        timeout = max(120, 60 + int(len(tickets) * 1.5))
        raw_response = self.gemini.generate(
            prompt,
            system_prompt=system_prompt,
            timeout=timeout,
        )

        # 4. Parse JSON response
        records, errors, output_tokens = self._parse_response(
            raw_response, batch_id, trc)

        # 5. Store classifications
        if records:
            try:
                self._store_classifications(records)
                logger.info(f"  -> Stored {len(records)} classifications")
            except Exception as store_err:
                logger.error(f"  -> STORE FAILED: {store_err}")
                # Don't let store failure prevent batch completion tracking
        else:
            logger.warning(f"  -> No records parsed from response "
                          f"(errors: {errors})")

        # 6. Compute cost + update progress
        batch_cost = self._estimate_cost(input_tokens, output_tokens)
        elapsed_ms = int((time.time() - start_time) * 1000)

        self._update_batch_status(batch_id, 'completed',
            ticket_count=len(records),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=batch_cost,
            latency_ms=elapsed_ms,
            raw_response=raw_response or '',
            error_message=json.dumps(errors) if errors else None,
            completed_at=datetime.utcnow().isoformat())

        # Update scan totals
        self._increment_scan_progress(batch_cost)

        # 7. Build prior_chunks_context for next chunk if needed
        if batch['trc_chunk'] < batch['trc_chunk_total']:
            self._update_prior_chunks(batch, records)

        logger.info(f"  -> {len(records)} classified, "
                     f"{len(errors)} errors, "
                     f"${batch_cost:.3f}, {elapsed_ms}ms")

    def _build_prompt(self, batch, tickets):
        """
        Assemble classification prompt with:
        - Stats context (from incident/theta/trending engines)
        - Established sub-taxonomy (active patterns + n-grams)
        - Prior chunks context (for multi-chunk TRCs)
        - Redacted ticket threads
        """
        trc = batch['trc']

        # Detect mixed batch
        is_mixed = trc.startswith('[')
        if is_mixed:
            try:
                trc_list = json.loads(trc)
                trc_display = f"Mixed ({len(trc_list)} TRCs)"
            except (json.JSONDecodeError, TypeError):
                trc_list = [trc]
                trc_display = trc
        else:
            trc_list = None
            trc_display = trc

        # ── Statistical context ──
        if trc_list:
            # Aggregate stats across all TRCs in the mix
            stats_parts = []
            for t in trc_list:
                ctx = self._build_stats_context(t)
                if ctx and 'No anomalies' not in ctx:
                    stats_parts.append(f"[{t}]\n{ctx}")
            stats_context = '\n'.join(stats_parts) if stats_parts else None
        else:
            stats_context = self._build_stats_context(trc)

        # ── Sub-taxonomy ──
        if trc_list:
            sub_taxonomy = self._build_mixed_sub_taxonomy(trc_list)
        else:
            sub_taxonomy = self._build_sub_taxonomy(trc)

        # ── Prior chunks ──
        prior_chunks = batch.get('prior_chunks_context') or ''
        prior_section = ''
        if batch['trc_chunk'] > 1 and prior_chunks:
            prior_section = (
                f"\nSUB-PATTERNS FROM PREVIOUS CHUNKS OF THIS TRC:\n"
                f"{prior_chunks}\n\n"
                f"Use these labels for matching tickets. Create new labels "
                f"only for genuinely different sub-patterns not covered above.\n"
            )

        # ── Format tickets as JSONL ──
        # Redaction happens inside gemini_client.generate() (mandatory base)
        ticket_lines = []
        total_comments = 0
        for t in tickets:
            thread = t.get('full_thread') or ''
            comment_count = (len(re.findall(
                r'\[(Client|Agent|System|Bot)\]', thread, re.IGNORECASE
            )) or 1)
            total_comments += comment_count

            ticket_lines.append(json.dumps({
                'ticket_id': t['ticket_id'],
                'trc': t.get('trc_code') or trc,
                'created_at': t.get('created_at') or '',
                'subject': t.get('subject') or '',
                'csat': t.get('csat_score'),
                'thread': thread[:4000],
            }))
        ticket_jsonl = '\n'.join(ticket_lines)

        # ── Assemble from template ──
        prompt = self._prompt_template
        prompt = prompt.replace('{trc_path}', trc_display)
        prompt = prompt.replace('{statistical_context}',
                                stats_context or 'No statistical data available.')
        prompt = prompt.replace('{sub_taxonomy_section}',
                                sub_taxonomy + prior_section)
        prompt = prompt.replace('{n_tickets}', str(len(tickets)))
        prompt = prompt.replace('{n_comments}', str(total_comments))

        # Get date range from scan
        scan = self._read_scan()
        prompt = prompt.replace('{date_start}',
                                scan['date_range_start'] if scan else '')
        prompt = prompt.replace('{date_end}',
                                scan['date_range_end'] if scan else '')
        prompt = prompt.replace('{chunk_n}', str(batch['trc_chunk']))
        prompt = prompt.replace('{chunk_total}', str(batch['trc_chunk_total']))
        prompt = prompt.replace('{ticket_jsonl}', ticket_jsonl)

        system_prompt = (
            "You are classifying support tickets for an RCM "
            "healthcare platform. Output ONLY structured tool_call blocks. "
            "No markdown. No explanation."
        )

        return prompt, system_prompt

    def _build_stats_context(self, trc):
        """Query existing statistical engines for TRC context."""
        lines = []
        try:
            flags = self.conn.execute("""
                SELECT flag_type, flagged_date, description
                FROM incident_flags
                WHERE trc_code = ? AND status = 'open'
                ORDER BY flagged_date DESC LIMIT 5
            """, (trc,)).fetchall()
            for f in flags:
                lines.append(f"- Incident: {f['flag_type']} "
                             f"({f['flagged_date']})")
        except Exception:
            pass

        try:
            flags = self.conn.execute("""
                SELECT metric_type, date, direction
                FROM anomaly_flags
                WHERE trc_code = ?
                  AND metric_type IN ('sentiment', 'term_freq')
                ORDER BY date DESC LIMIT 5
            """, (trc,)).fetchall()
            for f in flags:
                lines.append(f"- Theta: {f['metric_type']} "
                             f"{f['direction']} ({f['date']})")
        except Exception:
            pass

        try:
            terms = self.conn.execute("""
                SELECT term, score FROM tfidf_scores
                WHERE trc = ?
                ORDER BY score DESC LIMIT 10
            """, (trc,)).fetchall()
            if terms:
                top = ', '.join(t['term'] for t in terms[:5])
                lines.append(f"- Rising terms: {top}")
        except Exception:
            pass

        if not lines:
            return "No anomalies detected for this TRC."
        return '\n'.join(lines)

    def _build_sub_taxonomy(self, trc):
        """Load active sub-patterns + top n-grams for this TRC."""
        patterns = self.conn.execute("""
            SELECT pattern_id, label, description, friction_type,
                   lifetime_tickets
            FROM sub_patterns
            WHERE trc = ? AND tier IN ('active', 'probationary')
              AND merged_into IS NULL
            ORDER BY lifetime_tickets DESC
        """, (trc,)).fetchall()

        if not patterns:
            return ''

        section = (f"EXISTING SUB-PATTERNS FOR THIS TRC "
                    f"({len(patterns)} active):\n\n")

        for i, p in enumerate(patterns):
            ngrams = self.conn.execute("""
                SELECT ngram FROM sub_pattern_ngrams
                WHERE pattern_id = ? AND specificity > 0.1
                ORDER BY (frequency * specificity) DESC
                LIMIT 8
            """, (p['pattern_id'],)).fetchall()

            ngram_list = ', '.join(n['ngram'] for n in ngrams)
            section += (f"{i+1}. \"{p['label']}\"\n"
                        f"   N-grams: [{ngram_list}]\n"
                        f"   Friction: {p['friction_type'] or 'unclassified'}"
                        f" | Lifetime: {p['lifetime_tickets']} tickets\n\n")

        section += ("If a ticket matches an existing sub-pattern, "
                     "use that exact label. If it does NOT match, "
                     "set is_novel=true with justification.")
        return section

    def _build_mixed_sub_taxonomy(self, trc_list):
        """Load sub-patterns across ALL TRCs in a mixed batch.

        Unlike _build_sub_taxonomy() which queries one TRC at a time,
        this does a single query across all TRCs, sorts globally by
        lifetime_tickets, caps at 30 patterns, and groups by TRC.
        """
        if not trc_list:
            return ''

        placeholders = ','.join('?' for _ in trc_list)
        patterns = self.conn.execute(f"""
            SELECT pattern_id, trc, label, description, friction_type,
                   lifetime_tickets
            FROM sub_patterns
            WHERE trc IN ({placeholders})
              AND tier IN ('active', 'probationary')
              AND merged_into IS NULL
            ORDER BY lifetime_tickets DESC
            LIMIT 30
        """, trc_list).fetchall()

        if not patterns:
            return ''

        # Group by TRC for clarity in prompt
        from collections import OrderedDict
        by_trc = OrderedDict()
        for p in patterns:
            trc = p['trc']
            if trc not in by_trc:
                by_trc[trc] = []
            by_trc[trc].append(p)

        section = (f"EXISTING SUB-PATTERNS ACROSS {len(by_trc)} TRCs "
                   f"({len(patterns)} patterns):\n\n")

        idx = 0
        for trc, pats in by_trc.items():
            section += f"── {trc} ──\n"
            for p in pats:
                idx += 1
                ngrams = self.conn.execute("""
                    SELECT ngram FROM sub_pattern_ngrams
                    WHERE pattern_id = ? AND specificity > 0.1
                    ORDER BY (frequency * specificity) DESC
                    LIMIT 8
                """, (p['pattern_id'],)).fetchall()

                ngram_list = ', '.join(n['ngram'] for n in ngrams)
                section += (f"{idx}. \"{p['label']}\"\n"
                            f"   N-grams: [{ngram_list}]\n"
                            f"   Friction: {p['friction_type'] or 'unclassified'}"
                            f" | Lifetime: {p['lifetime_tickets']} tickets\n\n")

        section += ("If a ticket matches an existing sub-pattern, "
                    "use that exact label. If it does NOT match, "
                    "set is_novel=true with justification.")
        return section

    def _parse_response(self, raw, batch_id, trc):
        """Parse Gemini JSON response into classification records.

        Resilient to truncated responses: if the full JSON array can't
        be parsed, falls back to extracting individual complete JSON
        objects via regex. This handles the common case where Gemini's
        output token limit cuts off mid-array.
        """
        output_tokens = int(len(raw or '') * 1.3 / 4)

        if not raw or not raw.strip():
            return [], ['Empty response'], 0

        # Extract JSON array (handle markdown fences, trailing commas)
        cleaned = raw.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.lstrip('`').lstrip('json').lstrip('\n')
            cleaned = cleaned.rstrip('`').rstrip('\n')

        # Fix trailing commas
        cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned)

        parsed = None

        # Attempt 1: parse the full response as a JSON array
        try:
            parsed = json.loads(cleaned)
            if not isinstance(parsed, list):
                parsed = [parsed]
        except json.JSONDecodeError:
            pass

        # Attempt 2: find a complete JSON array via regex
        if parsed is None:
            match = re.search(r'\[[\s\S]*\]', cleaned)
            if match:
                try:
                    arr = re.sub(r',\s*([\]}])', r'\1', match.group())
                    parsed = json.loads(arr)
                except json.JSONDecodeError:
                    pass

        # Attempt 3: truncated response — extract individual JSON objects
        # This handles Gemini output that was cut off mid-array
        if parsed is None:
            parsed = self._extract_partial_json_objects(cleaned)
            if parsed:
                logger.info(f"  -> Recovered {len(parsed)} records "
                            f"from truncated response "
                            f"({len(cleaned)} chars)")
            else:
                return [], ['JSON parse failed'], int(output_tokens)

        VALID_FRICTION = {
            'access_blocked', 'self_serve_failure', 'automation_loop',
            'incorrect_charge', 'missing_information', 'policy_confusion',
            'feature_broken', 'feature_missing', 'process_delay',
            'communication_gap', 'escalation_demand', 'repeat_contact',
            'positive_feedback', 'other'
        }
        VALID_ANOMALY = {'normal', 'unusual', 'critical'}
        VALID_POLARITY = {'positive', 'negative', 'mixed', 'neutral'}

        records = []
        errors = []
        now = datetime.utcnow().isoformat()

        for item in parsed:
            try:
                record = {
                    'classification_id': str(uuid_mod.uuid4()),
                    'batch_id': batch_id,
                    'scan_id': self.scan_id,
                    'ticket_id': item.get('ticket_id', ''),
                    'trc': self._ticket_trc_map.get(
                        item.get('ticket_id', ''), trc),
                    'sub_cluster': self.gemini._redact_base(
                        item.get('sub_cluster', '')),
                    'sub_cluster_confidence': max(0, min(1,
                        float(item.get('sub_cluster_confidence', 0.5)))),
                    'is_novel': 1 if item.get('is_novel') else 0,
                    'sentiment_intensity': max(1, min(5,
                        int(item.get('sentiment_intensity', 3)))),
                    'sentiment_polarity': item.get('sentiment_polarity', 'neutral')
                        if item.get('sentiment_polarity') in VALID_POLARITY
                        else 'neutral',
                    'friction_type': item.get('friction_type', 'other')
                        if item.get('friction_type') in VALID_FRICTION
                        else 'other',
                    'anomaly_flag': item.get('anomaly_flag')
                        if item.get('anomaly_flag') in VALID_ANOMALY
                        else None,
                    'anomaly_reason': self.gemini._redact_base(
                        item.get('anomaly_reason') or ''),
                    'entities_json': json.dumps(item.get('entities', {})),
                    'key_phrases': json.dumps([
                        self.gemini._redact_base(p)
                        for p in (item.get('key_phrases') or [])
                    ]),
                    'root_cause_hint': self.gemini._redact_base(
                        item.get('root_cause_hint') or ''),
                    'summary': self.gemini._redact_base(
                        item.get('summary') or ''),
                    'raw_classification': json.dumps(item),
                    'created_at': now,
                }

                if not record['ticket_id']:
                    errors.append('Missing ticket_id')
                    continue

                records.append(record)
            except Exception as e:
                errors.append(f"Record error: {e}")

        return records, errors, int(output_tokens)

    @staticmethod
    def _extract_partial_json_objects(text):
        """Extract individual complete JSON objects from a truncated array.

        When Gemini's output is cut off (output token limit), we get
        something like:  [{...}, {...}, {...}, {"ticket_id": "123...
        This method finds all complete {...} blocks via brace-depth
        tracking and parses them individually.
        """
        objects = []
        depth = 0
        start = None

        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    fragment = text[start:i + 1]
                    try:
                        # Fix trailing commas in this fragment
                        fragment = re.sub(r',\s*([\]}])', r'\1', fragment)
                        obj = json.loads(fragment)
                        # Must have ticket_id to be a valid classification
                        if isinstance(obj, dict) and obj.get('ticket_id'):
                            objects.append(obj)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    start = None

        return objects

    def _store_classifications(self, records):
        """Batch insert classifications to SQLite."""
        self.conn.executemany("""
            INSERT OR REPLACE INTO nlp_ticket_classifications (
                classification_id, batch_id, scan_id, ticket_id,
                trc, sub_cluster, sub_cluster_confidence, is_novel,
                sentiment_intensity, sentiment_polarity, friction_type,
                anomaly_flag, anomaly_reason, entities_json,
                key_phrases, root_cause_hint, summary,
                raw_classification, created_at
            ) VALUES (
                :classification_id, :batch_id, :scan_id, :ticket_id,
                :trc, :sub_cluster, :sub_cluster_confidence, :is_novel,
                :sentiment_intensity, :sentiment_polarity, :friction_type,
                :anomaly_flag, :anomaly_reason, :entities_json,
                :key_phrases, :root_cause_hint, :summary,
                :raw_classification, :created_at
            )
        """, records)
        self.conn.commit()

    # ── SQLite communication methods ──

    def _read_scan(self):
        row = self.conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?",
            (self.scan_id,)).fetchone()
        return dict(row) if row else None

    def _update_status(self, status):
        self.conn.execute(
            "UPDATE nlp_scan_runs SET status = ? WHERE scan_id = ?",
            (status, self.scan_id))
        self.conn.commit()

    def _get_next_batch(self):
        """Claim the next queued batch for this worker (atomic).

        Uses UPDATE-then-SELECT pattern to prevent race conditions
        when multiple workers launch simultaneously.
        """
        # First try batches pre-assigned to this worker
        self.conn.execute("""
            UPDATE nlp_batches SET status = 'claimed'
            WHERE batch_id = (
                SELECT batch_id FROM nlp_batches
                WHERE scan_id = ? AND status = 'queued'
                  AND worker_id = ?
                ORDER BY batch_number ASC LIMIT 1
            )
        """, (self.scan_id, self.worker_id))
        self.conn.commit()

        row = self.conn.execute("""
            SELECT * FROM nlp_batches
            WHERE scan_id = ? AND status = 'claimed'
              AND worker_id = ?
            ORDER BY batch_number ASC LIMIT 1
        """, (self.scan_id, self.worker_id)).fetchone()
        if row:
            return row

        # Fallback: claim any unclaimed queued batch
        self.conn.execute("""
            UPDATE nlp_batches SET status = 'claimed', worker_id = ?
            WHERE batch_id = (
                SELECT batch_id FROM nlp_batches
                WHERE scan_id = ? AND status = 'queued'
                ORDER BY batch_number ASC LIMIT 1
            )
        """, (self.worker_id, self.scan_id))
        self.conn.commit()

        row = self.conn.execute("""
            SELECT * FROM nlp_batches
            WHERE scan_id = ? AND status = 'claimed'
              AND worker_id = ?
            ORDER BY batch_number ASC LIMIT 1
        """, (self.scan_id, self.worker_id)).fetchone()

        return row

    def _count_remaining_batches(self):
        row = self.conn.execute("""
            SELECT COUNT(*) as n FROM nlp_batches
            WHERE scan_id = ? AND status IN ('queued', 'claimed', 'running')
        """, (self.scan_id,)).fetchone()
        return row['n'] if row else 0

    def _count_failed_batches(self):
        row = self.conn.execute("""
            SELECT COUNT(*) as n FROM nlp_batches
            WHERE scan_id = ? AND status = 'failed'
        """, (self.scan_id,)).fetchone()
        return row['n'] if row else 0

    @staticmethod
    def _is_quota_error(error_str):
        """Detect Gemini quota/rate-limit errors."""
        quota_phrases = (
            'exhausted your capacity',
            'no capacity available',
            'quota',
            'rate limit',
            '429',
            'RetryableQuotaError',
            'resource exhausted',
        )
        lower = error_str.lower()
        return any(phrase.lower() in lower for phrase in quota_phrases)

    def _try_finalize_scan(self):
        """If no batches are queued/claimed/running, finalize scan status.

        This prevents the scan from being stuck in 'running' forever
        when all batches have either completed or failed.
        """
        remaining = self._count_remaining_batches()
        if remaining > 0:
            return  # Other batches still in progress

        scan = self._read_scan()
        if not scan or scan['status'] not in ('running',):
            return  # Already finalized

        failed = self._count_failed_batches()
        total = scan.get('total_batches', 0)

        if failed == total:
            # Check if all failures were quota errors
            quota_batches = self.conn.execute("""
                SELECT COUNT(*) as n FROM nlp_batches
                WHERE scan_id = ? AND status = 'failed'
                  AND (error_message LIKE '%quota%'
                       OR error_message LIKE '%capacity%'
                       OR error_message LIKE '%429%'
                       OR error_message LIKE '%rate limit%')
            """, (self.scan_id,)).fetchone()
            if quota_batches and quota_batches['n'] == total:
                self._update_status('quota_exhausted')
                logger.info("Scan finalized: quota_exhausted (all batches)")
            else:
                self._update_status('failed')
                logger.info("Scan finalized: failed (all batches)")
        elif failed > 0:
            self._update_status('scan_complete')
            logger.info(f"Scan finalized: scan_complete "
                        f"({total - failed} ok, {failed} failed)")
        else:
            self._update_status('scan_complete')
            logger.info("Scan finalized: scan_complete")

    def _update_batch_status(self, batch_id, status, **extras):
        sets = ['status = ?']
        vals = [status]
        for k, v in extras.items():
            sets.append(f'{k} = ?')
            vals.append(v)
        vals.append(batch_id)
        self.conn.execute(
            f"UPDATE nlp_batches SET {', '.join(sets)} "
            f"WHERE batch_id = ?", vals)
        self.conn.commit()

    def _increment_scan_progress(self, batch_cost):
        self.conn.execute("""
            UPDATE nlp_scan_runs
            SET completed_batches = completed_batches + 1,
                actual_cost_usd = actual_cost_usd + ?
            WHERE scan_id = ?
        """, (batch_cost, self.scan_id))
        self.conn.commit()

    def _get_tickets_for_batch(self, batch):
        """Get tickets for this batch's TRC(s), sliced for chunk.

        The `trc` field can be:
        - A plain TRC code string → single-TRC batch
        - "(untagged)" → untagged tickets
        - A JSON array '["TRC_A","TRC_B",...]' → mixed batch (small TRCs packed)
        """
        trc = batch['trc']
        scan = self._read_scan()
        if not scan:
            return []

        date_start = scan['date_range_start']
        date_end = scan['date_range_end']

        # Detect mixed batch (JSON array of TRC codes)
        trc_list = None
        if trc.startswith('['):
            try:
                trc_list = json.loads(trc)
            except (json.JSONDecodeError, TypeError):
                pass

        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        _sw_reg = SourceRegistry(self.conn)
        _sw_wq = WarehouseQuery(self.conn, _sw_reg)
        _sw_cols = ["ticket_id", "subject", "trc_code", "trc_label", "csat_score",
                    "created_at", "full_thread", "message_count", "client_messages", "agent_messages"]

        if trc_list:
            all_rows = []
            for t in trc_list:
                if t == '(untagged)':
                    _raw = _sw_wq.query_conversations_raw("""
                        SELECT ticket_id, subject, trc_code, trc_label,
                               csat_score, created_at, full_thread,
                               message_count, client_messages, agent_messages
                        FROM {table}
                        WHERE (trc_code IS NULL OR trc_code = '')
                          AND created_at >= ? AND created_at <= ?
                        ORDER BY created_at ASC
                    """, (date_start, date_end + ' 23:59:59'))
                else:
                    _raw = _sw_wq.query_conversations_raw("""
                        SELECT ticket_id, subject, trc_code, trc_label,
                               csat_score, created_at, full_thread,
                               message_count, client_messages, agent_messages
                        FROM {table}
                        WHERE trc_code = ? AND created_at >= ? AND created_at <= ?
                        ORDER BY created_at ASC
                    """, (t, date_start, date_end + ' 23:59:59'))
                all_rows.extend(_raw)
            tickets = [dict(zip(_sw_cols, r)) for r in all_rows]
        elif trc == '(untagged)':
            _raw = _sw_wq.query_conversations_raw("""
                SELECT ticket_id, subject, trc_code, trc_label,
                       csat_score, created_at, full_thread,
                       message_count, client_messages, agent_messages
                FROM {table}
                WHERE (trc_code IS NULL OR trc_code = '')
                  AND created_at >= ? AND created_at <= ?
                ORDER BY created_at ASC
            """, (date_start, date_end + ' 23:59:59'))
            tickets = [dict(zip(_sw_cols, r)) for r in _raw]
        else:
            _raw = _sw_wq.query_conversations_raw("""
                SELECT ticket_id, subject, trc_code, trc_label,
                       csat_score, created_at, full_thread,
                       message_count, client_messages, agent_messages
                FROM {table}
                WHERE trc_code = ? AND created_at >= ? AND created_at <= ?
                ORDER BY created_at ASC
            """, (trc, date_start, date_end + ' 23:59:59'))
            tickets = [dict(zip(_sw_cols, r)) for r in _raw]

        # For multi-chunk TRCs, slice to this chunk's portion.
        # chunk_size must match MAX_BATCH_TICKETS in scan_worker_manager
        # (200 tickets per chunk to stay within Gemini output limits).
        chunk_size = 200
        try:
            scan_config = json.loads(scan.get('config_snapshot') or '{}')
            # If config specifies a smaller batch_size, use that
            cfg_bs = scan_config.get('batch_size', 700)
            chunk_size = min(chunk_size, cfg_bs)
        except (json.JSONDecodeError, TypeError):
            pass

        if batch['trc_chunk_total'] > 1 and len(tickets) > chunk_size:
            chunk_start = (batch['trc_chunk'] - 1) * chunk_size
            chunk_end = chunk_start + chunk_size
            tickets = tickets[chunk_start:chunk_end]

        # Build ticket_id → trc_code lookup for per-ticket TRC propagation
        self._ticket_trc_map = {
            t['ticket_id']: (t.get('trc_code') or '')
            for t in tickets
        }

        return tickets

    def _handle_batch_error(self, batch, error_msg):
        retry = (batch.get('retry_count') or 0) + 1
        is_quota = self._is_quota_error(error_msg)
        # Allow more retries for quota errors (they're transient)
        max_retries = 6 if is_quota else 3

        if retry <= max_retries:
            self.conn.execute("""
                UPDATE nlp_batches
                SET retry_count = ?, status = 'queued',
                    error_message = ?
                WHERE batch_id = ?
            """, (retry, error_msg[:2000], batch['batch_id']))
        else:
            self.conn.execute("""
                UPDATE nlp_batches
                SET status = 'failed', error_message = ?,
                    completed_at = ?
                WHERE batch_id = ?
            """, (error_msg[:2000], datetime.utcnow().isoformat(),
                  batch['batch_id']))
        self.conn.commit()

    def _update_prior_chunks(self, batch, records):
        """Summarize sub-patterns for next chunk context."""
        clusters = Counter(r['sub_cluster'] for r in records
                          if r['sub_cluster'])
        context = '\n'.join(
            f'- "{label}" ({n} tickets)'
            for label, n in clusters.most_common()
        )
        self.conn.execute("""
            UPDATE nlp_batches SET prior_chunks_context = ?
            WHERE scan_id = ? AND trc = ? AND trc_chunk = ?
        """, (context, batch['scan_id'], batch['trc'],
              batch['trc_chunk'] + 1))
        self.conn.commit()

    def _estimate_tokens(self, text):
        return int(len(text) * 1.3 / 4)

    def _estimate_cost(self, input_tokens, output_tokens):
        # Gemini 2.5 Flash pricing
        return (input_tokens / 1e6) * 0.15 + (output_tokens / 1e6) * 0.60


# ── Entry point ──

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Alma NLP Scan Worker (CLI Mode)")
    parser.add_argument('--db', required=True, help="Path to SQLite database")
    parser.add_argument('--scan', required=True, help="Scan ID to process")
    parser.add_argument('--worker', type=int, default=0,
                        help="Worker ID (0-2)")
    args = parser.parse_args()

    log_file = Path(args.db).parent / 'scan_worker.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [Worker-%(name)s] %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )

    worker = ScanWorker(args.db, args.scan, args.worker)
    worker.run()
