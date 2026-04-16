"""
Alma Insights -- Scan Orchestrator (Pass 5.0)

Top-level coordinator for the agentic NLP pipeline.
Drop-in replacement for ScanWorkerManager with identical public API.

Architecture:
  - N persistent worker agents (each with their own bridge)
  - 1 supervisor (deterministic health monitor)
  - 1 analyst agent (post-scan LLM judgment, own bridge)
  - 1 rate governor (shared across all workers)
  - 1 batch packer (dynamic batch sizing with TRC learning)

The orchestrator:
  1. Creates scan records (same tables as ScanWorkerManager)
  2. Partitions tickets into batches (same strategy)
  3. Boots bridges and workers (persistent, not subprocess-per-batch)
  4. Distributes batches to workers (round-robin or queue-based)
  5. Runs supervisor for health monitoring
  6. After classification: runs analyst for cross-TRC synthesis
  7. After analyst: runs existing nlp_meta_analyzer (Layer 2)
  8. Provides get_status(), pause, resume, cancel (same API)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

from src.agents.acp_bridge import ACPBridge
from src.agents.worker_agent import WorkerAgent
from src.agents.supervisor import Supervisor
from src.agents.analyst_agent import AnalystAgent
from src.data.connection_factory import get_connection
from src.agents.rate_governor import RateGovernor
from src.agents.batch_packer import BatchPacker
from src.data.usage_tracker import UsageTracker

logger = logging.getLogger("alma.orchestrator")

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# ── Limits ──
MAX_TICKETS_CLI = 5000         # hard cap for CLI mode
MAX_BATCH_TICKETS = 25         # default per-batch cap (overridden by model limits)
MAX_PARALLEL_WORKERS = 32      # hard ceiling for concurrent workers
DEFAULT_PARALLEL_WORKERS = 8   # sane default balancing throughput vs resources


class ScanOrchestrator:
    """
    Top-level scan coordinator.

    Public API matches ScanWorkerManager for drop-in swap:
      - start_scan(date_start, date_end, ...)
      - get_status(scan_id)
      - pause_scan(scan_id)
      - resume_scan(scan_id)
      - cancel_scan(scan_id)
      - get_history(limit=20)
      - shutdown()
    """

    def __init__(self, db_path=None, num_workers=None):
        """
        Args:
            db_path: Path to SQLite database (auto-detected if None).
            num_workers: Number of parallel worker agents (capped at 32,
                         defaults to 8).
        """
        raw = str(db_path or (_PROJECT_ROOT / "data" / "local_warehouse.db"))
        self.db_path = str(Path(raw).resolve())
        if num_workers is None:
            num_workers = DEFAULT_PARALLEL_WORKERS
        self.num_workers = min(num_workers, MAX_PARALLEL_WORKERS)

        # Components (created on start_scan, destroyed on shutdown)
        self._bridges = []
        self._workers = []
        self._analyst_bridge = None
        self._analyst = None
        # Semaphore rate governor: allows N concurrent in-flight calls
        # with burst protection between dispatches.
        # NOTE: previously capped at 8 via `min(num_workers, 8)`, which
        # permanently starved workers 9+ when the user configured 16
        # parallel_workers (semaphore holders waited on Gemini while
        # the other half timed out every 2 min on acquire).  The cap
        # had no documented reason; Gemini API limits are enforced by
        # Google's side and surfaced as 429s — we have backoff for that
        # already.  Scale directly with worker count.
        self._rate_governor = RateGovernor(
            max_concurrent=max(self.num_workers, 1),
            burst_delay=0.5,
        )
        self._batch_packer = None
        self._supervisor = None

        # Model selection from settings.yaml (Pass 5.1)
        self._model = self._load_model_from_config()

        # Usage tracking (Pass 5.1)
        self._usage_tracker = None  # initialized on first scan

        # Scan state
        self._current_scan_id = None
        self._batch_queue = Queue()
        self._worker_threads = []
        self._supervisor_thread = None
        self._scan_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._cancelled = False
        self._user_cancelled = False  # F4: distinguish user cancel from stall timeout

        # 6.3: Adaptive thresholds (set by canary probes before each scan)
        self._adaptive_call_timeout = 600  # default; overridden by probes

    # ──────────────────────────────────────────────────────────────────────
    # Public API (matches ScanWorkerManager)
    # ──────────────────────────────────────────────────────────────────────

    def start_scan(self, date_start, date_end,
                   trc_filter=None, batch_size=25,
                   budget_cap=50.0, parallel_workers=None,
                   mode='full'):
        """
        Create scan record, partition batches, boot workers,
        start classification.

        Returns: dict with scan_id, total_batches, etc. or error key.
        """
        if parallel_workers is None:
            parallel_workers = DEFAULT_PARALLEL_WORKERS
        actual_workers = min(parallel_workers, MAX_PARALLEL_WORKERS)

        # Fix #4: Reap interrupted scans from a previous process.  If
        # any scans are still marked 'running' we know the prior app
        # instance crashed / was force-closed / lost power — daemon
        # worker threads died with it.  Flip to 'interrupted' so the UI
        # history tab shows them truthfully.
        self._reap_interrupted_scans()

        conn = self._get_conn()
        scan_id = str(uuid.uuid4())

        try:
            # ── Query TRC distribution (via warehouse) ──
            from src.data.source_registry import SourceRegistry
            from src.data.warehouse_query import WarehouseQuery
            _scan_registry = SourceRegistry(conn)
            _scan_wq = WarehouseQuery(conn, _scan_registry)

            _trc_raw = _scan_wq.query_conversations_raw("""
                SELECT trc_code AS trc, COUNT(DISTINCT ticket_id) AS n
                FROM {table}
                WHERE created_at >= ? AND created_at <= ?
                  AND trc_code IS NOT NULL AND trc_code != ''
                GROUP BY trc_code ORDER BY n DESC
            """, (date_start, date_end + ' 23:59:59'))
            _trc_agg = {}
            for r in _trc_raw:
                _trc_agg[r[0]] = _trc_agg.get(r[0], 0) + r[1]
            trc_counts = [{"trc": t, "n": n} for t, n in sorted(_trc_agg.items(), key=lambda x: -x[1])]

            _untag_raw = _scan_wq.query_conversations_raw("""
                SELECT COUNT(DISTINCT ticket_id) AS n FROM {table}
                WHERE created_at >= ? AND created_at <= ?
                  AND (trc_code IS NULL OR trc_code = '')
            """, (date_start, date_end + ' 23:59:59'))
            untagged_row = {"n": sum(r[0] for r in _untag_raw if r and r[0])}

            # ── Query thread length stats per TRC (5.4 input-aware batching) ──
            _ts_raw = _scan_wq.query_conversations_raw("""
                SELECT trc_code AS trc,
                       AVG(CASE WHEN LENGTH(COALESCE(full_thread, '')) > 3000
                                THEN 3000
                                ELSE LENGTH(COALESCE(full_thread, ''))
                           END) AS avg_thread,
                       SUM(CASE WHEN LENGTH(COALESCE(full_thread, '')) > 3000
                                THEN 3000
                                ELSE LENGTH(COALESCE(full_thread, ''))
                           END) AS total_thread_chars
                FROM {table}
                WHERE created_at >= ? AND created_at <= ?
                  AND trc_code IS NOT NULL AND trc_code != ''
                GROUP BY trc_code
            """, (date_start, date_end + ' 23:59:59'))
            # Aggregate thread stats across sources
            _ts_agg = {}
            for r in _ts_raw:
                if r[0] not in _ts_agg:
                    _ts_agg[r[0]] = {"total_chars": 0, "count": 0}
                _ts_agg[r[0]]["total_chars"] += r[2] or 0
                _ts_agg[r[0]]["count"] += 1
            thread_stats_rows = [
                {"trc": t, "avg_thread": v["total_chars"] / max(v["count"], 1), "total_thread_chars": v["total_chars"]}
                for t, v in _ts_agg.items()
            ]

            trc_thread_stats = {
                r['trc']: {'avg_thread': r['avg_thread'] or 0,
                           'total_thread_chars': r['total_thread_chars'] or 0}
                for r in thread_stats_rows
            }

            # ── Init batch packer (model-adaptive, 5.2) ──
            self._batch_packer = BatchPacker(conn, model=self._model)

            # ── Partition into batches (dual-constraint, 5.4) ──
            from src.agents.batch_packer import (
                MODEL_MAX_BATCH, JSON_OVERHEAD_PER_TICKET,
                THREAD_TRUNCATION_LIMIT,
            )
            max_batch_tickets = MODEL_MAX_BATCH.get(
                self._model, MAX_BATCH_TICKETS
            )
            batches = []
            batch_num = 0
            total_tickets = 0
            # Derive thresholds from model-adaptive max (not static batch_size)
            small_threshold = max(10, max_batch_tickets // 3)
            small_trcs = []
            small_total = 0

            for row in trc_counts:
                trc, n = row['trc'], row['n']
                if trc_filter and trc not in trc_filter:
                    continue

                total_tickets += n

                # Use BatchPacker for dual-constraint sizing (5.4)
                avg_thread = trc_thread_stats.get(
                    trc, {}
                ).get('avg_thread', 0)
                dynamic_size = self._batch_packer.compute_batch_size(
                    trc, n, avg_thread_chars=avg_thread
                )
                effective_max = min(dynamic_size, max_batch_tickets)

                if n > effective_max:
                    # Large TRC -> chunk into multiple batches
                    n_chunks = -(-n // effective_max)  # ceiling div
                    for c in range(1, n_chunks + 1):
                        batches.append(self._make_batch(
                            scan_id, batch_num, trc, c, n_chunks,
                            actual_workers
                        ))
                        batch_num += 1
                elif n > small_threshold:
                    # Medium TRC -> own batch
                    batches.append(self._make_batch(
                        scan_id, batch_num, trc, 1, 1,
                        actual_workers
                    ))
                    batch_num += 1
                else:
                    # Small TRC -> queue for packing
                    small_trcs.append((trc, n))
                    small_total += n

            # Include untagged tickets
            untagged_n = untagged_row['n'] if untagged_row else 0
            if untagged_n > 0 and not trc_filter:
                total_tickets += untagged_n
                if untagged_n > max_batch_tickets:
                    n_chunks = -(-untagged_n // max_batch_tickets)
                    for c in range(1, n_chunks + 1):
                        batches.append(self._make_batch(
                            scan_id, batch_num, '(untagged)', c, n_chunks,
                            actual_workers
                        ))
                        batch_num += 1
                elif untagged_n > small_threshold:
                    batches.append(self._make_batch(
                        scan_id, batch_num, '(untagged)', 1, 1,
                        actual_workers
                    ))
                    batch_num += 1
                else:
                    small_trcs.append(('(untagged)', untagged_n))
                    small_total += untagged_n

            # Pack small TRCs into mixed batches (dual constraint: count + input chars, 5.4)
            if small_trcs:
                MIXED_INPUT_CAP = self._batch_packer.get_input_budget()
                current_pack = []
                current_count = 0
                current_input_chars = 0

                for trc, n in small_trcs:
                    # Input char estimate for this TRC (capped threads + JSON overhead)
                    stats = trc_thread_stats.get(trc, {})
                    trc_thread_chars = stats.get(
                        'total_thread_chars',
                        n * THREAD_TRUNCATION_LIMIT
                    )
                    trc_total_chars = trc_thread_chars + (
                        n * JSON_OVERHEAD_PER_TICKET
                    )

                    would_exceed_count = (
                        current_count + n > max_batch_tickets
                    )
                    would_exceed_input = (
                        current_input_chars + trc_total_chars > MIXED_INPUT_CAP
                    )

                    if (would_exceed_count or would_exceed_input) and current_pack:
                        trc_list = [t for t, _ in current_pack]
                        batches.append(self._make_batch(
                            scan_id, batch_num,
                            json.dumps(trc_list), 1, 1, actual_workers
                        ))
                        batch_num += 1
                        current_pack = []
                        current_count = 0
                        current_input_chars = 0

                    current_pack.append((trc, n))
                    current_count += n
                    current_input_chars += trc_total_chars

                if current_pack:
                    if len(current_pack) == 1:
                        trc_str = current_pack[0][0]
                    else:
                        trc_str = json.dumps([t for t, _ in current_pack])
                    batches.append(self._make_batch(
                        scan_id, batch_num, trc_str, 1, 1, actual_workers
                    ))
                    batch_num += 1

            if not batches:
                conn.close()
                return {
                    'error': 'No tickets found in date range',
                    'total_tickets': total_tickets,
                }

            # Hard cap
            if total_tickets > MAX_TICKETS_CLI:
                conn.close()
                return {
                    'error': (
                        f'Ticket count ({total_tickets:,}) exceeds '
                        f'limit ({MAX_TICKETS_CLI:,}). Use a narrower '
                        f'date range or TRC filter.'
                    ),
                    'total_tickets': total_tickets,
                    'max_tickets': MAX_TICKETS_CLI,
                }

            # ── Estimate cost + time ──
            est_input = total_tickets * 600
            est_output = total_tickets * 200
            est_cost = (
                (est_input / 1e6) * 0.15
                + (est_output / 1e6) * 0.60
            )
            # Agentic pipeline is faster: bridge eliminates cold starts
            est_seconds_per_batch = 45  # vs 90s in old pipeline
            est_time_min = (
                len(batches) * est_seconds_per_batch
                / max(actual_workers, 1)
            ) / 60

            # ── Create scan record ──
            now = datetime.utcnow().isoformat()
            conn.execute("""
                INSERT INTO nlp_scan_runs (
                    scan_id, created_at, status, date_range_start,
                    date_range_end, trc_filter, mode, batch_strategy,
                    total_batches, total_tickets, estimated_cost_usd,
                    actual_cost_usd, budget_cap_usd, config_snapshot
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, 'trc_dynamic',
                          ?, ?, ?, 0.0, ?, ?)
            """, (
                scan_id, now, date_start, date_end,
                json.dumps(trc_filter) if trc_filter else None,
                mode, len(batches), total_tickets, est_cost, budget_cap,
                json.dumps({
                    'model': self._model,
                    'mode': 'agentic',
                    'batch_size': batch_size,
                    'parallel_workers': actual_workers,
                    'pipeline_version': '5.0',
                }),
            ))

            # ── Insert batch records ──
            for b in batches:
                conn.execute("""
                    INSERT INTO nlp_batches (
                        batch_id, scan_id, batch_number, trc,
                        trc_chunk, trc_chunk_total, status,
                        worker_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """, (
                    b['batch_id'], scan_id, b['batch_number'],
                    b['trc'], b['trc_chunk'], b['trc_chunk_total'],
                    b['worker_id'], now,
                ))

            conn.commit()
        finally:
            conn.close()

        # ── Store scan ID and launch agentic pipeline ──
        self._current_scan_id = scan_id
        self._stop_event.clear()
        self._user_cancelled = False

        # Launch workers in background thread
        scan_thread = threading.Thread(
            target=self._run_scan,
            args=(scan_id, batches, date_start, date_end,
                  actual_workers, budget_cap, total_tickets),
            name=f"scan-{scan_id[:8]}",
            daemon=True,
        )
        scan_thread.start()

        return {
            'scan_id': scan_id,
            'total_batches': len(batches),
            'total_tickets': total_tickets,
            'estimated_cost': round(est_cost, 4),
            'estimated_time_minutes': round(est_time_min, 1),
            'workers_launched': actual_workers,
        }

    def get_status(self, scan_id=None):
        """Read scan progress from SQLite + live supervisor state."""
        sid = scan_id or self._current_scan_id
        if not sid:
            return {'error': 'no active scan'}

        conn = self._get_conn()
        try:
            scan = conn.execute(
                "SELECT * FROM nlp_scan_runs WHERE scan_id = ?",
                (sid,)
            ).fetchone()

            if not scan:
                return {'error': 'scan not found'}

            result = dict(scan)

            # Get current batch info
            running = conn.execute("""
                SELECT trc, trc_chunk, trc_chunk_total FROM nlp_batches
                WHERE scan_id = ? AND status IN ('running', 'claimed')
                ORDER BY batch_number ASC LIMIT 1
            """, (sid,)).fetchall()

            if running:
                result['current_batch_trc'] = running[0]['trc']
                result['current_chunk'] = (
                    f"{running[0]['trc_chunk']}/"
                    f"{running[0]['trc_chunk_total']}"
                )

            # Add live supervisor data if available
            if self._supervisor and self._supervisor.scan_id == sid:
                sup_status = self._supervisor.get_status()
                result['est_remaining_seconds'] = sup_status.get(
                    'est_remaining_seconds', 0
                )
                result['est_confidence'] = sup_status.get(
                    'est_confidence', 'low'
                )
                result['worker_status'] = sup_status.get('workers', [])
                result['restarts'] = sup_status.get('restarts', 0)

            # Add scan_progress data
            progress = conn.execute(
                "SELECT * FROM scan_progress WHERE scan_id = ?",
                (sid,)
            ).fetchone()
            if progress:
                result['classified_count'] = progress['classified']
                result['total_count'] = progress['total']

            # Count actually classified tickets from nlp_ticket_classifications
            try:
                classified_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM nlp_ticket_classifications "
                    "WHERE scan_id = ?",
                    (sid,)
                ).fetchone()
                result['classified_tickets'] = classified_row['n'] if classified_row else 0
            except Exception:
                result['classified_tickets'] = 0

            return result
        finally:
            conn.close()

    def pause_scan(self, scan_id):
        """Pause scan. Workers check this on next batch."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE nlp_scan_runs SET status = 'paused' "
                "WHERE scan_id = ?", (scan_id,)
            )
            conn.commit()
        finally:
            conn.close()

        self._stop_event.set()
        return {'status': 'paused'}

    def resume_scan(self, scan_id, budget_cap=None):
        """Resume a paused scan."""
        conn = self._get_conn()
        try:
            scan = conn.execute(
                "SELECT * FROM nlp_scan_runs WHERE scan_id = ?",
                (scan_id,)
            ).fetchone()

            if not scan:
                return {'error': 'scan not found'}

            config = json.loads(scan['config_snapshot'] or '{}')
            n_workers = config.get('parallel_workers', 1)

            updates = "status = 'running'"
            params = []
            if budget_cap is not None:
                updates += ", budget_cap_usd = ?"
                params.append(budget_cap)
            params.append(scan_id)

            conn.execute(
                f"UPDATE nlp_scan_runs SET {updates} WHERE scan_id = ?",
                params
            )

            # Re-queue failed and stuck batches
            requeued = conn.execute("""
                UPDATE nlp_batches SET status = 'queued'
                WHERE scan_id = ? AND status = 'failed'
            """, (scan_id,)).rowcount

            reset = conn.execute("""
                UPDATE nlp_batches SET status = 'queued'
                WHERE scan_id = ? AND status IN ('running', 'claimed')
            """, (scan_id,)).rowcount

            conn.commit()

            # Rebuild batch list from queued batches
            queued = conn.execute("""
                SELECT * FROM nlp_batches
                WHERE scan_id = ? AND status = 'queued'
                ORDER BY batch_number ASC
            """, (scan_id,)).fetchall()

            batches = [dict(b) for b in queued]
        finally:
            conn.close()

        if requeued or reset:
            logger.info(
                f"Resume: re-queued {requeued} failed + "
                f"{reset} stuck batches for scan {scan_id[:8]}"
            )

        # Relaunch workers
        self._current_scan_id = scan_id
        self._stop_event.clear()

        actual_workers = min(n_workers, MAX_PARALLEL_WORKERS)
        scan_thread = threading.Thread(
            target=self._run_scan,
            args=(scan_id, batches,
                  scan['date_range_start'], scan['date_range_end'],
                  actual_workers, budget_cap or scan['budget_cap_usd']),
            name=f"scan-resume-{scan_id[:8]}",
            daemon=True,
        )
        scan_thread.start()

        return {'status': 'running', 'requeued_batches': requeued + reset}

    def cancel_scan(self, scan_id):
        """Cancel scan. Workers exit gracefully."""
        self._user_cancelled = True  # F4: explicit user cancel
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE nlp_scan_runs SET status = 'cancelled' "
                "WHERE scan_id = ?", (scan_id,)
            )
            conn.commit()
        finally:
            conn.close()

        self._stop_event.set()
        if self._supervisor:
            self._supervisor.stop()

        return {'status': 'cancelled'}

    def get_history(self, limit=20):
        """Return recent scan history."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT scan_id, created_at, status, date_range_start,
                       date_range_end, total_batches, completed_batches,
                       total_tickets, actual_cost_usd, estimated_cost_usd
                FROM nlp_scan_runs ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def shutdown(self):
        """Shutdown all bridges and workers."""
        self._stop_event.set()

        if self._supervisor:
            self._supervisor.stop()

        for worker in self._workers:
            try:
                worker.shutdown()
            except Exception:
                pass

        for bridge in self._bridges:
            try:
                bridge.shutdown()
            except Exception:
                pass

        if self._analyst_bridge:
            try:
                self._analyst_bridge.shutdown()
            except Exception:
                pass

        if self._analyst:
            try:
                self._analyst.shutdown()
            except Exception:
                pass

        self._bridges = []
        self._workers = []
        self._analyst_bridge = None
        self._analyst = None
        self._supervisor = None

        logger.info("ScanOrchestrator: shutdown complete")

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Scan execution
    # ──────────────────────────────────────────────────────────────────────

    def _emit_event(self, scan_id, event_type, status, message,
                    duration_ms=None, metadata=None):
        """Emit a scan event to the scan_events table for UI polling."""
        try:
            conn = self._get_conn()
            try:
                import json as _json
                conn.execute("""
                    INSERT INTO scan_events
                    (scan_id, timestamp, event_type, status, message,
                     duration_ms, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (scan_id, datetime.utcnow().isoformat(),
                      event_type, status, message, duration_ms,
                      _json.dumps(metadata) if metadata else None))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Failed to emit scan event: {e}")

    def _load_model_from_config(self):
        """Resolve the active Gemini model.

        Priority: ModelRegistry.active() (what the user selected in
        Settings) → legacy `gemini.model` key → loud last-resort.
        This fixes a long-standing bug where `ai.active_model` was
        ignored and scans always ran on the stale `gemini.model` value.
        """
        HARD_FALLBACK = "gemini-2.5-flash"

        # 1) ModelRegistry (authoritative — what Settings UI writes)
        try:
            from src.llm.model_registry import ModelRegistry
            cfg = ModelRegistry.instance().active()
            if cfg and cfg.provider == "gemini" and cfg.enabled:
                logger.info(
                    f"ScanOrchestrator: using model '{cfg.model_string}' "
                    f"from ModelRegistry (active)"
                )
                return cfg.model_string
        except Exception as e:
            logger.debug(f"ModelRegistry lookup failed: {e}")

        # 2) Legacy gemini.model key
        try:
            from src.data.settings_manager import get_section
            gemini_cfg = get_section("gemini", {})
            model = gemini_cfg.get("model")
            if model:
                logger.warning(
                    f"ScanOrchestrator: ModelRegistry unavailable; using "
                    f"legacy gemini.model='{model}'. Check that "
                    f"ai.active_model is set in settings.yaml."
                )
                return model
        except Exception as e:
            logger.debug(f"settings_manager lookup failed: {e}")

        # 3) Hard fallback — should never happen in normal use.  Loud so
        # it shows up in the diagnostic log if somehow both paths fail.
        logger.error(
            f"ScanOrchestrator: NEITHER ModelRegistry NOR gemini.model "
            f"resolved a model. Falling back to hardcoded "
            f"'{HARD_FALLBACK}'. This is a configuration error — "
            f"check settings.yaml and src/llm/model_registry.py."
        )
        return HARD_FALLBACK

    def _load_scan_config(self):
        """Read nlp_scan settings from settings.yaml via centralized settings_manager."""
        try:
            from src.data.settings_manager import get_section
            return get_section("nlp_scan", {})
        except Exception:
            return {}

    def _init_usage_tracker(self):
        """Initialize usage tracker for token/cost logging."""
        try:
            from src.data.db_manager import DatabaseManager
            db = DatabaseManager(Path(self.db_path))
            self._usage_tracker = UsageTracker(db)
        except Exception as e:
            logger.warning(f"Failed to init usage tracker: {e}")
            self._usage_tracker = None

    def _run_scan(self, scan_id, batches, date_start, date_end,
                  num_workers, budget_cap, total_tickets=0):
        """
        Main scan execution (runs in background thread).

        1. Preflight: boot bridges + workers (with event emission)
        2. Queue batches
        3. Start worker threads
        4. Start supervisor
        5. Wait for completion
        6. Run analyst
        7. Run meta-analyzer
        8. Finalize
        """
        try:
            # ── Init usage tracker ──
            self._init_usage_tracker()

            # ── 1. Preflight: boot infrastructure with event emission ──
            self._emit_event(scan_id, 'preflight', 'info',
                             f'Using model: {self._model}')
            self._emit_event(scan_id, 'preflight', 'running',
                             'Bridge boot — starting...')
            t0 = time.time()
            try:
                self._boot_workers(num_workers, scan_id)
                boot_ms = int((time.time() - t0) * 1000)
                self._emit_event(scan_id, 'preflight', 'complete',
                                 f'Bridge boot — connected ({boot_ms}ms)',
                                 duration_ms=boot_ms)
            except Exception as e:
                boot_ms = int((time.time() - t0) * 1000)
                self._emit_event(scan_id, 'preflight', 'error',
                                 f'Bridge boot — failed: {e}',
                                 duration_ms=boot_ms)
                raise

            # ── ACP bridge status ──
            try:
                if self._bridges:
                    stats = self._bridges[0].get_stats()
                    agent_ver = stats.get('agent_version', '?')
                    proto_ver = stats.get('protocol_version', '?')
                    session_id = stats.get('session_id', '?')
                    mcp_label = 'alma-tools' if self._bridges[0]._mcp_servers else 'none'
                    self._emit_event(
                        scan_id, 'preflight', 'complete',
                        f'ACP bridge — gemini --acp v{agent_ver}, '
                        f'protocol={proto_ver}, '
                        f'pool_size={num_workers}, '
                        f'model={self._model or "default"}, '
                        f'mcp={mcp_label}, '
                        f'session={str(session_id)[:8] if session_id else "?"}')
            except Exception:
                pass  # non-fatal — bridge status is informational

            # ── 6.3: Canary probe burst ──
            self._emit_event(scan_id, 'preflight', 'running',
                             'Canary probes — measuring API latency...')
            probe_results = self._run_canary_probes(scan_id)
            self._derive_adaptive_thresholds(scan_id, probe_results)

            # Worker spawn events
            for i, worker in enumerate(self._workers):
                self._emit_event(scan_id, 'preflight', 'complete',
                                 f'Worker {i} spawned — ready')

            # ── 2. Queue batches ──
            for b in batches:
                self._batch_queue.put(b)

            # ── 3. Configure supervisor ──
            self._supervisor = Supervisor(
                self._workers, self._rate_governor,
                self.db_path, scan_id
            )
            self._supervisor.set_scan(
                scan_id, len(batches), total_tickets
            )

            self._emit_event(scan_id, 'preflight', 'complete',
                             'Supervisor online — polling every 5s')

            # Rate governor
            burst = self._rate_governor.min_interval
            concurrency = getattr(self._rate_governor, '_current_concurrent', '?')
            self._emit_event(scan_id, 'preflight', 'complete',
                             f'Rate governor — burst_delay {burst}s, '
                             f'concurrency {concurrency}')

            # Scan started marker
            self._emit_event(scan_id, 'info', 'complete',
                             f'Scan started — {len(batches)} batches, '
                             f'{num_workers} workers')

            # ── 4. Start worker threads ──
            self._worker_threads = []
            for worker in self._workers:
                t = threading.Thread(
                    target=self._worker_loop,
                    args=(worker, scan_id, date_start, date_end, budget_cap),
                    name=f"worker-{worker.agent_id}",
                    daemon=True,
                )
                t.start()
                self._worker_threads.append(t)

            # ── 5. Run supervisor (blocks until complete or cutoff) ──
            self._supervisor.run()

            # F6: Mark any still-running batches as stalled (tail cutoff)
            try:
                mark_conn = self._get_conn()
                stalled = mark_conn.execute(
                    "UPDATE nlp_batches SET status = 'failed', "
                    "error_message = 'tail_stall_cutoff' "
                    "WHERE scan_id = ? AND status = 'running'",
                    (scan_id,)
                ).rowcount
                mark_conn.commit()
                mark_conn.close()
                if stalled:
                    logger.info(
                        f"Scan {scan_id[:8]}: marked {stalled} stalled "
                        f"batch(es) as failed"
                    )
            except Exception:
                pass

            # ── 6. Wait for worker threads ──
            for t in self._worker_threads:
                t.join(timeout=30)

            # ── Check if cancelled ── (F4: distinguish user cancel from stall)
            if self._user_cancelled:
                self._emit_event(scan_id, 'info', 'complete',
                                 'Scan cancelled by user — skipping post-scan')
                logger.info(f"Scan {scan_id[:8]}: cancelled by user")
                self._finalize_scan(scan_id)
                return

            if self._stop_event.is_set():
                # Stall timeout or supervisor cutoff — proceed with post-scan
                # analysis on whatever was classified
                self._emit_event(scan_id, 'info', 'warning',
                                 'Classification ended with stall timeout — '
                                 'proceeding to post-scan analysis')
                logger.info(
                    f"Scan {scan_id[:8]}: stall timeout, "
                    f"continuing to post-scan"
                )

            # ── 6b. Retry sweep for any remaining failed batches ──
            self._retry_sweep(scan_id, date_start, date_end)

            # ── 6c. Classification sweep: recover dropped tickets ──
            self._classification_sweep(scan_id, date_start, date_end)

            self._emit_event(scan_id, 'info', 'complete',
                             'Classification complete')

            # ── 7. Run analyst ──
            try:
                self._emit_event(scan_id, 'analyst', 'running',
                                 'Analyst: cross-TRC synthesis starting')
                self._boot_analyst()
                logger.info(f"Scan {scan_id[:8]}: running analyst...")

                t0 = time.time()
                self._analyst.run_cross_trc_synthesis(scan_id)
                ms = int((time.time() - t0) * 1000)
                self._emit_event(scan_id, 'analyst', 'complete',
                                 'Analyst: cross-TRC synthesis complete',
                                 duration_ms=ms)

                t0 = time.time()
                self._emit_event(scan_id, 'analyst', 'running',
                                 'Analyst: quality audit starting')
                self._analyst.run_quality_audit(scan_id)
                ms = int((time.time() - t0) * 1000)
                self._emit_event(scan_id, 'analyst', 'complete',
                                 'Analyst: quality audit complete',
                                 duration_ms=ms)

                t0 = time.time()
                self._analyst.run_novelty_validation(scan_id)
                ms = int((time.time() - t0) * 1000)
                self._emit_event(scan_id, 'analyst', 'complete',
                                 'Analyst: novelty validation complete',
                                 duration_ms=ms)

                t0 = time.time()
                self._analyst.run_pattern_merge(scan_id)
                ms = int((time.time() - t0) * 1000)
                self._emit_event(scan_id, 'analyst', 'complete',
                                 'Analyst: pattern merge complete',
                                 duration_ms=ms)

                logger.info(f"Scan {scan_id[:8]}: analyst complete")
            except Exception as e:
                self._emit_event(scan_id, 'analyst', 'error',
                                 f'Analyst failed: {e}')
                logger.error(f"Analyst failed: {e}")

            # ── 8. Run meta-analyzer (existing Layer 2) ──
            try:
                self._emit_event(scan_id, 'info', 'running',
                                 'Meta-analysis starting')
                t0 = time.time()
                from src.data.nlp_meta_analyzer import NLPMetaAnalyzer
                from src.data.db_manager import DatabaseManager
                meta_db = DatabaseManager(Path(self.db_path))
                analyzer = NLPMetaAnalyzer(meta_db)
                analyzer.run_analysis(scan_id)
                ms = int((time.time() - t0) * 1000)

                # Get findings count
                try:
                    meta_conn = self._get_conn()
                    findings_count = meta_conn.execute(
                        "SELECT COUNT(*) FROM nlp_findings WHERE scan_id = ?",
                        (scan_id,)
                    ).fetchone()[0]
                    pattern_count = meta_conn.execute(
                        "SELECT COUNT(*) FROM sub_patterns"
                    ).fetchone()[0]
                    meta_conn.close()
                    self._emit_event(
                        scan_id, 'info', 'complete',
                        f'Meta-analysis complete | {findings_count} findings '
                        f'| {pattern_count} sub-patterns',
                        duration_ms=ms
                    )
                except Exception:
                    self._emit_event(scan_id, 'info', 'complete',
                                     'Meta-analysis complete', duration_ms=ms)

                logger.info(f"Scan {scan_id[:8]}: meta-analyzer complete")
            except Exception as e:
                self._emit_event(scan_id, 'info', 'error',
                                 f'Meta-analysis failed: {e}')
                logger.error(f"Meta-analyzer failed: {e}")

            # ── 8.5 Build 11.0: Post-scan persistence ──
            try:
                from src.services.post_scan_persist import run_post_scan_persistence
                persist_conn = get_connection(self.db_path)
                scan_date = datetime.utcnow().isoformat()
                result = run_post_scan_persistence(scan_id, scan_date, persist_conn)
                persist_conn.close()
                self._emit_event(scan_id, 'info', 'running',
                                 f'Persistence: {result["snapshots"]} snapshots, '
                                 f'{result["trends"]} trends, {result["insights"]} insights')
                logger.info(f"Scan {scan_id[:8]}: post-scan persistence complete")
            except Exception as e:
                logger.warning(f"Post-scan persistence failed (non-fatal): {e}")

            # ── 8.6 Post-scan embedding rebuild ──
            try:
                from src.data.embedding.builder import build_embeddings
                embed_conn = get_connection(self.db_path)
                self._emit_event(scan_id, 'info', 'running',
                                 'Rebuilding search index...')
                embed_count = build_embeddings(embed_conn)
                embed_conn.close()
                self._emit_event(scan_id, 'info', 'running',
                                 f'Search index: {embed_count} tickets embedded')
                logger.info(f"Scan {scan_id[:8]}: embedding rebuild complete "
                            f"({embed_count})")
            except Exception as e:
                logger.warning(f"Embedding rebuild failed (non-fatal): {e}")

            # ── 8.7 Post-scan integrity check ──
            try:
                from src.data.integrity_checker import run_post_scan_integrity
                integrity_conn = get_connection(self.db_path)
                integrity = run_post_scan_integrity(integrity_conn, scan_id)
                integrity_conn.close()
                if integrity["passed"]:
                    self._emit_event(scan_id, 'info', 'running',
                                     'Data integrity verified')
                else:
                    issue_count = len(integrity["issues"])
                    self._emit_event(scan_id, 'warning', 'running',
                                     f'Data integrity: {issue_count} issue(s) found')
            except Exception as e:
                logger.warning(f"Integrity check failed (non-fatal): {e}")

            # ── 8.8 Entity normalization ──
            try:
                from src.data.entity_normalizer import normalize_entities_for_scan
                entity_conn = get_connection(self.db_path)
                entity_count = normalize_entities_for_scan(entity_conn, scan_id)
                entity_conn.close()
                self._emit_event(scan_id, 'info', 'running',
                                 f'Entities: {entity_count} normalized')
            except Exception as e:
                logger.warning(f"Entity normalization failed (non-fatal): {e}")

            # ── 9. Finalize scan ──
            self._finalize_scan(scan_id)
            self._emit_event(scan_id, 'info', 'complete', 'Scan complete')

            logger.info(f"Scan {scan_id[:8]}: fully complete")

        except Exception as e:
            self._emit_event(scan_id, 'error', 'error',
                             f'Fatal error: {e}')
            logger.error(f"Scan {scan_id[:8]}: fatal error: {e}")
            self._mark_scan_error(scan_id, str(e))
        finally:
            # Shutdown all bridges — force-kill any that don't exit cleanly
            for bridge in self._bridges:
                try:
                    bridge.shutdown()
                except Exception:
                    pass
                # Force-kill safety net
                proc = getattr(bridge, '_process', None)
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            if self._analyst_bridge:
                try:
                    self._analyst_bridge.shutdown()
                except Exception:
                    pass
                proc = getattr(self._analyst_bridge, '_process', None)
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def _worker_loop(self, worker, scan_id, date_start, date_end,
                     budget_cap):
        """
        Worker thread: pulls batches from queue and classifies them.
        """
        conn = get_connection(self.db_path)

        while not self._stop_event.is_set():
            # Check scan status
            try:
                scan = conn.execute(
                    "SELECT status FROM nlp_scan_runs WHERE scan_id = ?",
                    (scan_id,)
                ).fetchone()
                if scan and scan['status'] in ('paused', 'cancelled'):
                    break
            except Exception:
                pass

            # Get next batch (F7: extended timeout for requeued batches)
            try:
                batch = self._batch_queue.get(timeout=10)
            except Empty:
                # Check if supervisor says we're done
                if (self._supervisor and
                        (self._supervisor._all_complete()
                         or self._supervisor.should_cutoff())):
                    break
                # Otherwise keep waiting — requeued batches may arrive
                continue

            batch_id = batch['batch_id']

            try:
                # Mark batch as running (+ update worker_id for retry tracking)
                try:
                    conn.execute(
                        "UPDATE nlp_batches SET status = 'running', "
                        "worker_id = ? WHERE batch_id = ?",
                        (worker.agent_id, batch_id)
                    )
                    conn.commit()
                except Exception:
                    pass

                action = self._process_single_batch(
                    worker, conn, scan_id, date_start, date_end, batch
                )
                # action is 'continue', 'break', or None (fall through)
                if action == 'break':
                    break
                elif action == 'continue':
                    continue

            except Exception as e:
                # Catch-all: prevent worker thread from dying silently
                self._handle_unhandled_batch_error(
                    worker, conn, scan_id, batch_id, e
                )

        conn.close()

    # ──────────────────────────────────────────────────────────────────────
    # Worker-loop sub-methods (refactored from _worker_loop)
    # ──────────────────────────────────────────────────────────────────────

    def _try_restart_bridge(self, worker, log_context):
        """Attempt up to 3 bridge restarts. Returns True if revived."""
        for attempt in range(3):
            try:
                worker.bridge.restart()
                time.sleep(3)
                if worker.bridge.is_alive():
                    logger.info(
                        "[HEALTH] %s bridge revived (attempt %d) [%s]",
                        worker.agent_id, attempt + 1, log_context,
                    )
                    return True
            except Exception as exc:
                logger.error(
                    "[HEALTH] bridge restart attempt %d failed: %s",
                    attempt + 1, exc,
                )
                time.sleep(2 ** attempt)
        return False

    def _process_single_batch(self, worker, conn, scan_id,
                              date_start, date_end, batch):
        """Core batch processing: bridge check, classify, record result.

        Returns:
            'break'    - caller should exit the worker loop
            'continue' - caller should skip to next iteration
            None       - caller should fall through normally
        """
        batch_id = batch['batch_id']
        trc = batch['trc']
        batch_num = batch.get('batch_number', 0)

        # ── 6.2: Early dead-bridge check before rate-governor wait ──
        if not worker.bridge.is_alive():
            logger.warning(
                "[HEALTH] %s bridge dead before rate-governor acquire "
                "— restarting bridge",
                worker.agent_id,
            )
            if not self._try_restart_bridge(worker, "pre-governor"):
                logger.error(
                    "[HEALTH] %s bridge unrecoverable — requeueing batch",
                    worker.agent_id,
                )
                self._batch_queue.put(batch)
                return 'break'  # exit worker loop; this worker is dead

        # Acquire rate governor slot (semaphore + burst gate)
        if not self._rate_governor.acquire(timeout=120):
            logger.warning(
                f"Worker {worker.agent_id}: rate governor timeout"
            )
            self._batch_queue.put(batch)  # re-queue
            return 'continue'

        # Track for release() in finally block
        _elapsed = 0.0
        _success = False
        _rate_limited = False

        try:
            # Requeue guard: skip if stalled batch already fully classified (5.3)
            if self._batch_already_classified(conn, scan_id, batch, worker):
                return 'continue'

            # Get tickets for this batch (skip already-classified on resume, 5.2)
            tickets = self._get_tickets_for_batch(
                conn, trc, batch.get('trc_chunk', 1),
                batch.get('trc_chunk_total', 1),
                date_start, date_end, scan_id=scan_id
            )

            # F6a: Record batch-to-ticket assignments for audit trail + sweep
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO nlp_batch_tickets "
                    "(batch_id, ticket_id, scan_id) VALUES (?, ?, ?)",
                    [(batch_id, t["ticket_id"], scan_id) for t in tickets]
                )
                conn.commit()
            except Exception:
                pass  # non-fatal; table may not exist on older DBs

            # Emit batch start event (after ticket fetch so we
            # can report the actual ticket count)
            trc_display = trc
            if trc.startswith('['):
                try:
                    trc_display = ", ".join(json.loads(trc))
                except (json.JSONDecodeError, TypeError):
                    pass
            self._emit_event(
                scan_id, 'batch_start', 'running',
                f'Batch {batch_num} started | {trc_display[:40]}',
                metadata={'batch_id': batch_id, 'trc': trc_display,
                          'worker': worker.agent_id,
                          'ticket_count': len(tickets)}
            )

            if not tickets:
                logger.warning(
                    f"Worker {worker.agent_id}: no tickets for "
                    f"batch {batch_id[:8]} trc={trc}"
                )
                conn.execute(
                    "UPDATE nlp_batches SET status = 'completed' "
                    "WHERE batch_id = ?", (batch_id,)
                )
                conn.commit()
                if self._supervisor:
                    self._supervisor.notify_batch_complete(
                        worker.agent_id, {"classified": 0}
                    )
                _success = True
                return 'continue'

            # Build batch payload
            payload = self._build_batch_payload(
                worker, scan_id, batch_id, trc, tickets, batch,
                date_start, date_end
            )

            # Check context overflow
            self._maybe_reset_context(worker)

            # Ensure bridge is alive before classifying
            if not worker.bridge.is_alive():
                logger.warning(
                    "[HEALTH] %s bridge dead pre-classify | "
                    "batch=%s deaths=%d",
                    worker.agent_id, batch_id[:8],
                    getattr(worker.bridge, '_death_count', 0),
                )
                if not self._try_restart_bridge(worker, "pre-classify"):
                    logger.error(
                        f"Worker {worker.agent_id}: bridge restart "
                        f"failed after 3 attempts, skipping batch"
                    )
                    self._batch_queue.put(batch)  # requeue for other worker
                    return 'continue'

            # Classify
            start = time.time()
            result = worker.classify_batch(payload)
            _elapsed = time.time() - start

            # Determine outcome for rate governor
            error = result.get("error")
            _success = not error
            _rate_limited = "rate_limit" in str(error) if error else False

            # Bridge health reporting (stall tracking, restart logic)
            self._report_bridge_health(worker, scan_id, result, _elapsed)

            # Record batch size for learning
            n_classified = result.get("classified", 0)
            if n_classified > 0:
                output_chars = result.get(
                    "response_chars", n_classified * 800
                )
                self._batch_packer.record_result(
                    trc, len(tickets), output_chars
                )

            # Handle retry logic for failed batches; returns 'continue'
            # if the batch was requeued for retry
            has_error = bool(error)
            if has_error:
                should_retry = self._handle_batch_failure(
                    worker, conn, scan_id, batch, batch_num, result, _elapsed
                )
                if should_retry:
                    return 'continue'  # skip marking as failed

            # Record result to DB + emit events + notify supervisor
            self._record_batch_result(
                worker, conn, scan_id, batch, batch_num, trc,
                tickets, result, _elapsed, has_error
            )

            # Truncation recovery: re-queue at half size
            if result.get("error") == "truncation":
                half = self._batch_packer.halve_for_retry(
                    len(tickets)
                )
                logger.info(
                    f"Truncation recovery: re-queuing at size {half}"
                )

            return None

        finally:
            self._rate_governor.release(
                duration=_elapsed if _elapsed > 0 else None,
                success=_success,
                rate_limited=_rate_limited,
            )

    def _batch_already_classified(self, conn, scan_id, batch, worker):
        """Check if a retried batch is already fully classified (5.3 guard).

        Returns True if the batch was already done and should be skipped.
        """
        retry_count = batch.get('retry_count', 0)
        expected_tickets = batch.get('ticket_count', 0)
        batch_id = batch['batch_id']

        if retry_count > 0 and expected_tickets > 0:
            already_done = conn.execute("""
                SELECT COUNT(DISTINCT ticket_id)
                FROM nlp_ticket_classifications
                WHERE scan_id = ? AND batch_id = ?
            """, (scan_id, batch_id)).fetchone()[0]
            if already_done >= expected_tickets:
                logger.info(
                    f"Batch {batch_id[:8]} already fully classified "
                    f"({already_done}/{expected_tickets}), "
                    f"skipping requeue"
                )
                conn.execute(
                    "UPDATE nlp_batches SET status = 'completed' "
                    "WHERE batch_id = ?", (batch_id,)
                )
                conn.commit()
                if self._supervisor:
                    self._supervisor.notify_batch_complete(
                        worker.agent_id,
                        {"classified": already_done}
                    )
                return True
        return False

    def _build_batch_payload(self, worker, scan_id, batch_id, trc,
                             tickets, batch, date_start, date_end):
        """Build the payload dict for classify_batch."""
        stats_ctx = ""
        sub_tax = ""
        try:
            stats_result = worker.tool_registry.execute(
                "get_stats_context", {"trc": trc}
            )
            stats_ctx = self._format_stats_context(stats_result)

            tax_result = worker.tool_registry.execute(
                "query_taxonomy", {"trc": trc}
            )
            sub_tax = self._format_taxonomy(tax_result)
        except Exception as e:
            logger.debug(f"Context build failed: {e}")

        # Phase 5.5 — feed-forward canonical labels for this TRC so the
        # classifier reuses them instead of re-inventing variants.
        canonical_menu = self._load_canonical_menu(trc)

        return {
            "scan_id": scan_id,
            "batch_id": batch_id,
            "trc": trc,
            "tickets": tickets,
            "stats_context": stats_ctx,
            "sub_taxonomy": sub_tax,
            "canonical_menu": canonical_menu,
            "chunk_n": batch.get('trc_chunk', 1),
            "chunk_total": batch.get('trc_chunk_total', 1),
            "date_start": date_start,
            "date_end": date_end,
            "call_timeout": self._adaptive_call_timeout,
        }

    def _load_canonical_menu(self, trc: str) -> list[str]:
        """Return active canonical_label values for a TRC (Phase 5.5).

        Fails soft — returns [] if the table or rows don't exist yet.
        """
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(self.db_path, readonly=True)
            try:
                rows = conn.execute(
                    """
                    SELECT canonical_label FROM canonical_clusters
                    WHERE trc = ?
                      AND canonical_label IS NOT NULL
                      AND (tier IS NULL OR tier NOT IN ('retired', 'split'))
                    ORDER BY member_count DESC
                    LIMIT 50
                    """,
                    (trc,),
                ).fetchall()
                return [r[0] for r in rows if r[0]]
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Canonical menu load failed for TRC=%s: %s", trc, exc)
            return []

    def _maybe_reset_context(self, worker):
        """Reset worker context if it has overflowed."""
        if worker.needs_reset():
            worker.reset()

    def _report_bridge_health(self, worker, scan_id, result, elapsed):
        """Report bridge health after classification (stall/death tracking).

        Rate governor reporting is handled by release() in the finally block.
        This method only handles bridge-level health monitoring.
        """
        if result.get("error"):
            error_code = str(result["error"])

            # ── 6.2: Track stall events on bridge + escalate ──
            if "stall_timeout" in error_code:
                needs_restart = worker.bridge.record_stall()
                if needs_restart:
                    self._emit_event(
                        scan_id, 'warning', 'running',
                        f'Stall escalation: auto-restarting '
                        f'{worker.agent_id} bridge '
                        f'(consecutive={worker.bridge._consecutive_stalls})',
                        metadata={
                            'worker': worker.agent_id,
                            'consecutive_stalls':
                                worker.bridge._consecutive_stalls,
                            'total_stalls': worker.bridge._stall_count,
                            'death_count': worker.bridge._death_count,
                        }
                    )
                    logger.warning(
                        "[HEALTH] stall escalation restart | %s",
                        worker.agent_id,
                    )
                    try:
                        worker.bridge.restart()
                        time.sleep(3)
                    except Exception as _se:
                        logger.error(
                            "[HEALTH] stall escalation restart failed: %s",
                            _se,
                        )
            elif "bridge_dead" in error_code:
                self._emit_event(
                    scan_id, 'warning', 'running',
                    f'Bridge dead detected on {worker.agent_id} '
                    f'during call',
                    metadata={'worker': worker.agent_id}
                )
            else:
                # Non-stall error — reset consecutive stall counter
                worker.bridge.record_success()
        else:
            # Rate governor reporting handled by release() in finally block.
            worker.bridge.record_success()

    def _handle_batch_failure(self, worker, conn, scan_id, batch, batch_num,
                              result, elapsed):
        """Handle retry logic for a failed batch.

        Returns True if the batch was requeued for retry (caller should
        continue to next iteration). Returns False if retries are
        exhausted and the batch should be recorded as failed.
        """
        batch_id = batch['batch_id']
        retry_count = batch.get('retry_count', 0) + 1
        error_str = str(result.get('error', '')).lower()
        error_code = str(result["error"])
        is_quota = 'rate_limit' in error_str or 'quota' in error_str
        max_retries = 6 if is_quota else 3

        if retry_count <= max_retries:
            # Requeue the batch for another attempt
            batch['retry_count'] = retry_count
            self._batch_queue.put(batch)
            try:
                conn.execute(
                    "UPDATE nlp_batches SET status = 'queued', "
                    "retry_count = ? WHERE batch_id = ?",
                    (retry_count, batch_id)
                )
                conn.commit()
            except Exception:
                pass
            _raw_err = result.get("message", "") or result.get("raw", "")
            _err_display = result.get("error", "unknown")
            if _raw_err and _err_display == "unknown":
                _err_display = f'unknown: {_raw_err[:100]}'
            self._emit_event(
                scan_id, 'batch_retry', 'running',
                f'Batch {batch_num} retry {retry_count}/{max_retries} '
                f'| {_err_display}',
                metadata={
                    'batch_id': batch_id,
                    'retry_count': retry_count,
                    'max_retries': max_retries,
                    'error': result.get('error', 'unknown'),
                    'error_code': error_code,
                    'message': result.get('message', ''),
                    'raw': result.get('raw', ''),
                    'worker_id': worker.agent_id,
                    'attempt_latency_ms': int(elapsed * 1000),
                }
            )
            logger.info(
                f"Batch {batch_id[:8]} requeued "
                f"(retry {retry_count}/{max_retries})"
            )
            return True  # requeued

        return False  # retries exhausted

    def _record_batch_result(self, worker, conn, scan_id, batch, batch_num,
                             trc, tickets, result, elapsed, has_error):
        """Write batch outcome to DB, emit events, notify supervisor.

        Delivery accounting (verify-on-commit): a batch is 'partial' if
        the worker completed cleanly (no error) but the DB does not have
        a classification row for every requested ticket. This is the
        "delivery lie" failure mode where Gemini signals `done` but
        didn't emit every store_classification. Partial batches are:
          * not retried (retry wouldn't help — the stream was clean)
          * counted toward scan progress (same as completed)
          * flagged with a `delivery_drop` event carrying the drop list
          * swept up automatically by the existing sweep query, which
            does LEFT JOIN nlp_batch_tickets ⟂ nlp_ticket_classifications
        """
        batch_id = batch['batch_id']
        classified = result.get('classified', 0)
        elapsed_s = round(elapsed, 1)

        # Determine status: failed > partial > completed
        dropped_ids = result.get('dropped_ids') or []
        if has_error:
            status = 'failed'
        elif dropped_ids:
            status = 'partial'
        else:
            status = 'completed'

        try:
            _in_tok = result.get("input_tokens", 0)
            _out_tok = result.get("output_tokens", 0)
            _batch_cost = UsageTracker.estimate_cost(
                _in_tok, _out_tok, self._model
            ) if not has_error else 0.0

            conn.execute("""
                UPDATE nlp_batches
                SET status = ?,
                    completed_at = ?,
                    ticket_count = ?,
                    latency_ms = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    cost_usd = ?,
                    error_message = ?,
                    retry_count = ?
                WHERE batch_id = ?
            """, (
                status, datetime.utcnow().isoformat(),
                len(tickets),
                int(elapsed * 1000),
                _in_tok,
                _out_tok,
                _batch_cost,
                str(result.get("error", "")) if has_error else None,
                batch.get("retry_count", 0),
                batch_id,
            ))

            # Update scan progress + running cost
            self._update_scan_progress(conn, scan_id)
        except Exception as e:
            logger.error(f"Batch status update failed: {e}")

        # Emit batch completion event
        if status == 'completed':
            self._emit_event(
                scan_id, 'batch_complete', 'complete',
                f'Batch {batch_num} complete | {classified}/{len(tickets)} '
                f'| {elapsed_s}s',
                duration_ms=int(elapsed * 1000),
                metadata={'batch_id': batch_id, 'classified': classified,
                          'total': len(tickets), 'trc': trc}
            )
        elif status == 'partial':
            # Delivery lie: stream returned clean but DB is missing N
            # tickets. Emit both batch_complete (progress) and a
            # distinct delivery_drop event carrying the drop list so
            # the UI and logs surface it loudly.
            delivered = len(result.get('delivered_ids') or [])
            requested = len(result.get('requested_ids') or tickets)
            self._emit_event(
                scan_id, 'batch_complete', 'warn',
                f'Batch {batch_num} partial | {delivered}/{requested} '
                f'delivered ({len(dropped_ids)} dropped) | {elapsed_s}s',
                duration_ms=int(elapsed * 1000),
                metadata={'batch_id': batch_id, 'classified': delivered,
                          'total': requested, 'dropped': len(dropped_ids),
                          'trc': trc}
            )
            self._emit_event(
                scan_id, 'delivery_drop', 'warn',
                f'Batch {batch_num}: Gemini signaled done but '
                f'{len(dropped_ids)} tickets were not persisted — '
                f'queued for sweep',
                metadata={
                    'batch_id': batch_id,
                    'trc': trc,
                    'dropped_count': len(dropped_ids),
                    # Include a capped sample; full list is derivable
                    # from nlp_batch_tickets LEFT JOIN classifications.
                    'dropped_sample': dropped_ids[:20],
                }
            )
            logger.warning(
                "Batch %s partial delivery: %d/%d — %d orphans queued "
                "for sweep (first 5: %s)",
                batch_id[:8], delivered, requested, len(dropped_ids),
                dropped_ids[:5],
            )
        else:
            self._emit_event(
                scan_id, 'batch_complete', 'error',
                f'Batch {batch_num} failed (retries exhausted) '
                f'| {result.get("error", "unknown")}',
                metadata={'batch_id': batch_id, 'error': str(result.get('error'))}
            )

        # Notify supervisor
        if self._supervisor:
            self._supervisor.notify_batch_complete(
                worker.agent_id, result
            )

    def _update_scan_progress(self, conn, scan_id):
        """Update completed_batches count and running cost on scan record.

        Partial batches (delivery-lie drops, swept later) count as
        progress — the main pipeline is done with them and the sweep
        will reclaim their orphans. Only 'failed' and in-flight states
        are excluded.
        """
        conn.execute("""
            UPDATE nlp_scan_runs SET
                completed_batches = (
                    SELECT COUNT(*) FROM nlp_batches
                    WHERE scan_id = ?
                      AND status IN ('completed', 'partial')
                ),
                actual_cost_usd = COALESCE(
                    (SELECT SUM(cost_usd) FROM gemini_usage
                     WHERE scan_id = ?), 0.0
                )
            WHERE scan_id = ?
        """, (scan_id, scan_id, scan_id))
        conn.commit()

    def _handle_unhandled_batch_error(self, worker, conn, scan_id,
                                      batch_id, error):
        """Catch-all handler for unexpected errors in the worker loop."""
        logger.error(
            f"Worker {worker.agent_id}: unhandled error on "
            f"batch {batch_id[:8]}: {error}"
        )
        try:
            conn.execute("""
                UPDATE nlp_batches SET status = 'failed'
                WHERE batch_id = ?
            """, (batch_id,))
            self._update_scan_progress(conn, scan_id)
        except Exception:
            pass
        if self._supervisor:
            self._supervisor.notify_batch_complete(
                worker.agent_id,
                {"classified": 0, "error": str(error)}
            )

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Retry Sweep
    # ──────────────────────────────────────────────────────────────────────

    def _retry_sweep(self, scan_id, date_start, date_end):
        """One final retry pass for failed batches after main scan completes.

        P3: Uses up to 4 parallel workers (min 1) for faster retry.
        Called automatically at the end of ``_run_scan`` and also by
        ``retry_failed_batches``.
        """
        conn = self._get_conn()
        try:
            failed = conn.execute("""
                SELECT batch_id, batch_number, trc, trc_chunk,
                       trc_chunk_total, ticket_count, retry_count
                FROM nlp_batches
                WHERE scan_id = ? AND status = 'failed'
                ORDER BY batch_number
            """, (scan_id,)).fetchall()

            if not failed:
                return  # nothing to retry

            n_failed = len(failed)
            n_sweep_workers = min(n_failed, 4)
            logger.info(
                f"Retry sweep: {n_failed} failed batch(es) with "
                f"{n_sweep_workers} worker(s)"
            )
            self._emit_event(
                scan_id, 'info', 'running',
                f'Retry sweep: re-attempting {n_failed} failed batch(es) '
                f'({n_sweep_workers} workers)'
            )

            # Boot sweep workers
            self._boot_workers(n_sweep_workers, scan_id=scan_id)
            sweep_workers = list(self._workers[:n_sweep_workers])

            # Queue failed batches
            sweep_queue = Queue()
            for batch_row in failed:
                sweep_queue.put(batch_row)

            # P3: Parallel sweep threads
            threads = []
            for worker in sweep_workers:
                t = threading.Thread(
                    target=self._sweep_worker_loop,
                    args=(worker, sweep_queue, scan_id,
                          date_start, date_end),
                    daemon=True,
                )
                t.start()
                threads.append(t)

            for t in threads:
                t.join(timeout=120)

            # Update scan_progress so UI reflects retry completions
            try:
                classified_count = conn.execute(
                    "SELECT COUNT(*) FROM nlp_ticket_classifications "
                    "WHERE scan_id = ?", (scan_id,)
                ).fetchone()[0]
                conn.execute(
                    "UPDATE scan_progress SET classified = ?, "
                    "updated_at = ? WHERE scan_id = ?",
                    (classified_count, datetime.utcnow().isoformat(),
                     scan_id)
                )
                conn.commit()
            except Exception:
                pass

            # Shutdown sweep workers
            for worker in sweep_workers:
                try:
                    worker.bridge.shutdown()
                except Exception:
                    pass
        finally:
            conn.close()

    def _sweep_worker_loop(self, worker, sweep_queue, scan_id,
                           date_start, date_end):
        """Process batches from the sweep queue until empty.

        Each thread gets its own DB connection for thread safety.
        """
        conn = get_connection(self.db_path)
        try:
            while True:
                try:
                    batch_row = sweep_queue.get(timeout=2)
                except Empty:
                    break

                if self._user_cancelled:
                    break

                batch_id = batch_row['batch_id']
                trc = batch_row['trc']
                batch_num = batch_row['batch_number']
                chunk_n = batch_row['trc_chunk'] or 1
                chunk_total = batch_row['trc_chunk_total'] or 1

                # Mark as running
                conn.execute(
                    "UPDATE nlp_batches SET status = 'running' "
                    "WHERE batch_id = ?", (batch_id,)
                )
                conn.commit()

                self._emit_event(
                    scan_id, 'batch_start', 'running',
                    f'Retry sweep batch {batch_num} | {trc[:40]}',
                    metadata={'batch_id': batch_id, 'trc': trc}
                )

                # Get remaining unclassified tickets
                tickets = self._get_tickets_for_batch(
                    conn, trc, chunk_n, chunk_total,
                    date_start, date_end, scan_id=scan_id
                )

                if not tickets:
                    conn.execute(
                        "UPDATE nlp_batches SET status = 'completed' "
                        "WHERE batch_id = ?", (batch_id,)
                    )
                    conn.commit()
                    logger.info(
                        f"Retry sweep batch {batch_id[:8]}: "
                        f"all tickets already classified"
                    )
                    continue

                # Build payload
                stats_ctx = ""
                sub_tax = ""
                try:
                    stats_result = worker.tool_registry.execute(
                        "get_stats_context", {"trc": trc}
                    )
                    stats_ctx = self._format_stats_context(stats_result)
                    tax_result = worker.tool_registry.execute(
                        "query_taxonomy", {"trc": trc}
                    )
                    sub_tax = self._format_taxonomy(tax_result)
                except Exception:
                    pass

                payload = {
                    "scan_id": scan_id,
                    "batch_id": batch_id,
                    "trc": trc,
                    "tickets": tickets,
                    "stats_context": stats_ctx,
                    "sub_taxonomy": sub_tax,
                    "canonical_menu": self._load_canonical_menu(trc),
                    "chunk_n": chunk_n,
                    "chunk_total": chunk_total,
                    "date_start": date_start,
                    "date_end": date_end,
                }

                # Ensure bridge is alive
                if not worker.bridge.is_alive():
                    try:
                        worker.bridge.restart()
                        time.sleep(3)
                    except Exception:
                        logger.error(
                            f"Retry sweep: bridge restart failed for "
                            f"batch {batch_id[:8]}"
                        )
                        continue

                # Classify
                start = time.time()
                result = worker.classify_batch(payload)
                elapsed = time.time() - start
                has_error = bool(result.get('error'))
                status = 'completed' if not has_error else 'failed'

                conn.execute("""
                    UPDATE nlp_batches
                    SET status = ?,
                        completed_at = ?,
                        latency_ms = ?,
                        error_message = ?,
                        retry_count = retry_count + 1,
                        input_tokens = ?,
                        output_tokens = ?
                    WHERE batch_id = ?
                """, (
                    status,
                    datetime.utcnow().isoformat(),
                    int(elapsed * 1000),
                    str(result.get('error', '')) if has_error else None,
                    result.get('input_tokens', 0),
                    result.get('output_tokens', 0),
                    batch_id,
                ))
                conn.commit()

                classified = result.get('classified', 0)
                sweep_lbl = 'complete' if not has_error else 'error'
                self._emit_event(
                    scan_id, 'batch_complete', sweep_lbl,
                    f'Retry sweep batch {batch_num}: {status} '
                    f'| {classified}/{len(tickets)}',
                    metadata={'batch_id': batch_id}
                )
                logger.info(
                    f"Retry sweep batch {batch_id[:8]}: {status} "
                    f"({classified}/{len(tickets)} classified, "
                    f"{elapsed:.1f}s)"
                )
        except Exception as e:
            logger.warning(f"Sweep worker {worker.agent_id} error: {e}")
        finally:
            conn.close()

    def _classification_sweep(self, scan_id, date_start, date_end):
        """Find and reclassify tickets that were assigned but never classified.

        Catches: tail-batch drops, worker crashes, MCP tool failures.
        Uses a single fresh worker with micro-batches of 5.

        Preferred path: query nlp_batch_tickets (F6a) for assigned-but-
        unclassified tickets.  Fallback: re-derive from TRC + date range.
        """
        conn = self._get_conn()
        try:
            # ── Preferred: use nlp_batch_tickets for exact unclassified list ──
            unclassified_by_trc = {}
            try:
                rows = conn.execute("""
                    SELECT bt.ticket_id, b.trc
                    FROM nlp_batch_tickets bt
                    JOIN nlp_batches b ON bt.batch_id = b.batch_id
                    LEFT JOIN nlp_ticket_classifications tc
                        ON bt.ticket_id = tc.ticket_id
                        AND bt.scan_id = tc.scan_id
                    WHERE bt.scan_id = ? AND tc.ticket_id IS NULL
                """, (scan_id,)).fetchall()
                if rows:
                    from collections import defaultdict
                    id_by_trc = defaultdict(list)
                    for r in rows:
                        id_by_trc[r['trc']].append(r['ticket_id'])
                    # Fetch full ticket data for each TRC group
                    for trc, ticket_ids in id_by_trc.items():
                        tickets = self._get_tickets_for_batch(
                            conn, trc, 1, 1, date_start, date_end,
                            scan_id=scan_id,
                        )
                        # Filter to only the specific unclassified IDs
                        matched = [
                            t for t in tickets
                            if t["ticket_id"] in set(ticket_ids)
                        ]
                        if matched:
                            unclassified_by_trc[trc] = matched
                elif rows is not None:
                    # Table exists but no unclassified tickets
                    pass
            except Exception:
                # nlp_batch_tickets may not exist (older DB) — fall back
                pass

            # ── Fallback: re-derive from TRC + date range ──
            if not unclassified_by_trc:
                trc_rows = conn.execute("""
                    SELECT DISTINCT trc FROM nlp_batches WHERE scan_id = ?
                """, (scan_id,)).fetchall()

                for row in (trc_rows or []):
                    trc = row['trc']
                    remaining = self._get_tickets_for_batch(
                        conn, trc, 1, 1, date_start, date_end,
                        scan_id=scan_id,
                    )
                    if remaining:
                        unclassified_by_trc[trc] = remaining

            total_unclassified = sum(
                len(t) for t in unclassified_by_trc.values()
            )
            if total_unclassified == 0:
                logger.info("Classification sweep: all tickets classified")
                return

            logger.info(
                f"Classification sweep: {total_unclassified} unclassified "
                f"ticket(s) across {len(unclassified_by_trc)} TRC(s)"
            )
            self._emit_event(
                scan_id, 'sweep_start', 'running',
                f'Sweep: reclassifying {total_unclassified} dropped ticket(s) '
                f'across {len(unclassified_by_trc)} TRC(s)'
            )

            # ── Sweep budgets (hardening) ──────────────────────────
            # The sweep used to run unbounded: one serial worker, 600s
            # per-call timeout, swallowed exceptions, no heartbeat. It
            # could hang the scan for 30+ min silently. Now:
            #   * Total wall-clock bounded at SWEEP_TOTAL_TIMEOUT_S
            #   * Per-chunk progress events (no silent gap > 1 chunk)
            #   * Bridge restart on exception (one shot per chunk)
            #   * Stop after SWEEP_MAX_CONSEC_FAILS in a row
            #   * Each chunk gets a shorter call_timeout so a single
            #     stuck chunk can't consume the whole budget.
            SWEEP_TOTAL_TIMEOUT_S = 180  # 3 min hard cap
            SWEEP_CHUNK_TIMEOUT_S = 60   # per-chunk call timeout
            SWEEP_MAX_CONSEC_FAILS = 3
            SWEEP_CHUNK_SIZE = 5

            # Boot one fresh worker
            self._boot_workers(1, scan_id=scan_id)
            worker = self._workers[0]

            sweep_start = time.time()
            sweep_classified = 0
            sweep_chunks_done = 0
            sweep_chunks_failed = 0
            consec_fails = 0
            total_chunks = sum(
                (len(t) + SWEEP_CHUNK_SIZE - 1) // SWEEP_CHUNK_SIZE
                for t in unclassified_by_trc.values()
            )

            def _budget_exceeded() -> bool:
                return (time.time() - sweep_start) > SWEEP_TOTAL_TIMEOUT_S

            aborted_reason: str | None = None
            for trc, tickets in unclassified_by_trc.items():
                if aborted_reason:
                    break
                # Process in micro-batches of SWEEP_CHUNK_SIZE
                for i in range(0, len(tickets), SWEEP_CHUNK_SIZE):
                    if self._user_cancelled or self._stop_event.is_set():
                        aborted_reason = 'user_cancel'
                        break
                    if _budget_exceeded():
                        aborted_reason = (
                            f'total_timeout ({SWEEP_TOTAL_TIMEOUT_S}s)'
                        )
                        break
                    if consec_fails >= SWEEP_MAX_CONSEC_FAILS:
                        aborted_reason = (
                            f'{SWEEP_MAX_CONSEC_FAILS} consecutive chunks '
                            f'failed'
                        )
                        break

                    chunk = tickets[i:i + SWEEP_CHUNK_SIZE]
                    chunk_num = sweep_chunks_done + sweep_chunks_failed + 1
                    batch_id = f"sweep_{scan_id[:8]}_{trc[:20]}_{i}"

                    self._emit_event(
                        scan_id, 'sweep_chunk', 'running',
                        f'Sweep chunk {chunk_num}/{total_chunks} '
                        f'({len(chunk)} tickets) | trc={trc[:40]}',
                        metadata={'batch_id': batch_id,
                                  'chunk_size': len(chunk)}
                    )

                    payload = self._build_batch_payload(
                        worker, scan_id, batch_id, trc, chunk,
                        {'batch_id': batch_id, 'trc': trc,
                         'trc_chunk': 1, 'trc_chunk_total': 1},
                        date_start, date_end,
                    )
                    # Tight per-chunk timeout so one stuck call can't
                    # eat the entire sweep budget.
                    payload['call_timeout'] = SWEEP_CHUNK_TIMEOUT_S

                    chunk_start = time.time()
                    try:
                        result = worker.classify_batch(payload)
                        chunk_elapsed = time.time() - chunk_start
                        delivered = len(result.get('delivered_ids') or [])
                        err = result.get('error')
                        if err:
                            sweep_chunks_failed += 1
                            consec_fails += 1
                            self._emit_event(
                                scan_id, 'sweep_chunk', 'warn',
                                f'Sweep chunk {chunk_num} failed: {err} '
                                f'({chunk_elapsed:.1f}s)',
                                metadata={'batch_id': batch_id,
                                          'error': str(err)}
                            )
                            logger.warning(
                                "Sweep chunk %s failed: %s",
                                batch_id[:16], err,
                            )
                            # Try bridge restart — dead-bridge recovery
                            try:
                                worker.bridge.restart()
                                time.sleep(2)
                                logger.info(
                                    "Sweep worker bridge restarted "
                                    "after chunk failure"
                                )
                            except Exception as re:
                                logger.warning(
                                    "Sweep bridge restart failed: %s", re,
                                )
                        else:
                            sweep_classified += delivered
                            sweep_chunks_done += 1
                            consec_fails = 0
                            self._emit_event(
                                scan_id, 'sweep_chunk', 'complete',
                                f'Sweep chunk {chunk_num}/{total_chunks} '
                                f'| {delivered}/{len(chunk)} recovered '
                                f'({chunk_elapsed:.1f}s)',
                                duration_ms=int(chunk_elapsed * 1000),
                                metadata={'batch_id': batch_id,
                                          'recovered': delivered,
                                          'chunk_size': len(chunk)}
                            )
                    except Exception as e:
                        chunk_elapsed = time.time() - chunk_start
                        sweep_chunks_failed += 1
                        consec_fails += 1
                        self._emit_event(
                            scan_id, 'sweep_chunk', 'error',
                            f'Sweep chunk {chunk_num} exception: '
                            f'{type(e).__name__}: {str(e)[:100]}',
                            metadata={'batch_id': batch_id,
                                      'error_type': type(e).__name__}
                        )
                        logger.warning(
                            "Sweep chunk %s exception: %s",
                            batch_id[:16], e,
                        )
                        try:
                            worker.bridge.restart()
                            time.sleep(2)
                        except Exception:
                            pass

            # Final sweep status event
            elapsed = time.time() - sweep_start
            recovery_rate = (
                sweep_classified / total_unclassified * 100
                if total_unclassified else 100.0
            )
            if aborted_reason:
                self._emit_event(
                    scan_id, 'sweep_end', 'warn',
                    f'Sweep aborted ({aborted_reason}): '
                    f'{sweep_classified}/{total_unclassified} recovered '
                    f'({recovery_rate:.1f}%) in {elapsed:.1f}s | '
                    f'chunks={sweep_chunks_done}ok/{sweep_chunks_failed}fail',
                    duration_ms=int(elapsed * 1000),
                    metadata={'aborted': aborted_reason,
                              'recovered': sweep_classified,
                              'unclassified': total_unclassified,
                              'chunks_done': sweep_chunks_done,
                              'chunks_failed': sweep_chunks_failed}
                )
                logger.warning(
                    "Sweep aborted (%s): %d/%d recovered, %d chunks ok, "
                    "%d failed, %.1fs elapsed",
                    aborted_reason, sweep_classified, total_unclassified,
                    sweep_chunks_done, sweep_chunks_failed, elapsed,
                )
            else:
                self._emit_event(
                    scan_id, 'sweep_end', 'complete',
                    f'Sweep complete: {sweep_classified}/'
                    f'{total_unclassified} recovered ({recovery_rate:.1f}%) '
                    f'in {elapsed:.1f}s | {sweep_chunks_done} chunks ok, '
                    f'{sweep_chunks_failed} failed',
                    duration_ms=int(elapsed * 1000),
                    metadata={'recovered': sweep_classified,
                              'unclassified': total_unclassified,
                              'chunks_done': sweep_chunks_done,
                              'chunks_failed': sweep_chunks_failed}
                )
                logger.info(
                    "Sweep complete: %d/%d recovered, %d chunks ok, "
                    "%d failed, %.1fs",
                    sweep_classified, total_unclassified,
                    sweep_chunks_done, sweep_chunks_failed, elapsed,
                )

            # Shutdown sweep worker (always, even on abort)
            try:
                worker.bridge.shutdown()
            except Exception:
                pass

        except Exception as e:
            logger.error(
                f"Classification sweep crashed (non-fatal to scan): {e}",
                exc_info=True,
            )
            try:
                self._emit_event(
                    scan_id, 'sweep_end', 'error',
                    f'Sweep crashed: {type(e).__name__}: {str(e)[:120]}',
                    metadata={'error_type': type(e).__name__}
                )
            except Exception:
                pass
        finally:
            conn.close()

    def retry_failed_batches(self, scan_id):
        """Re-run only the failed batches from a completed scan.

        Called from the UI 'Retry Failed Batches' button.  Validates the
        scan is in a retryable state, marks it as running, executes a
        retry sweep, then re-finalizes.
        """
        conn = self._get_conn()
        try:
            scan = conn.execute(
                "SELECT * FROM nlp_scan_runs WHERE scan_id = ?",
                (scan_id,)
            ).fetchone()
            if not scan:
                raise ValueError(f"Scan {scan_id} not found")

            retryable = (
                'completed', 'completed_with_errors',
                'scan_complete', 'analysis_complete',
            )
            if scan['status'] not in retryable:
                raise ValueError(
                    f"Scan status '{scan['status']}' is not retryable"
                )

            failed_count = conn.execute("""
                SELECT COUNT(*) FROM nlp_batches
                WHERE scan_id = ? AND status = 'failed'
            """, (scan_id,)).fetchone()[0]
            if failed_count == 0:
                raise ValueError("No failed batches to retry")

            date_start = scan['date_range_start']
            date_end = scan['date_range_end']

            # Mark scan as running
            conn.execute(
                "UPDATE nlp_scan_runs SET status = 'running' "
                "WHERE scan_id = ?",
                (scan_id,)
            )
            conn.commit()
        finally:
            conn.close()

        self._cancelled = False
        self._user_cancelled = False
        self._stop_event.clear()

        logger.info(
            f"retry_failed_batches: retrying {failed_count} batch(es) "
            f"for scan {scan_id[:8]}"
        )

        try:
            self._retry_sweep(scan_id, date_start, date_end)
        finally:
            self._finalize_scan(scan_id)

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Canary Probes (Build 6.3)
    # ──────────────────────────────────────────────────────────────────────

    def _run_canary_probes(self, scan_id):
        """Fire canary probes through a sample of bridges.

        With many workers (8-32), probing every bridge is too slow.
        We probe min(3, N) bridges to measure API latency, then
        assume the rest are similar.

        Returns list of probe result dicts.
        """
        from src.data.db_manager import DatabaseManager
        results = []
        db = None
        try:
            db = DatabaseManager(Path(self.db_path))
        except Exception as e:
            logger.warning("[HEALTH] canary probe: DB init failed: %s", e)

        # Sample up to 3 bridges (first, middle, last)
        n = len(self._bridges)
        if n <= 3:
            sample_indices = list(range(n))
        else:
            sample_indices = [0, n // 2, n - 1]

        logger.info(
            "[HEALTH] canary probes: sampling %d of %d bridges",
            len(sample_indices), n,
        )

        for i in sample_indices:
            bridge = self._bridges[i]
            probe = bridge.probe(timeout=30)
            if probe is None:
                probe = {"latency_ms": 0, "status": "error",
                         "error": "bridge_not_alive"}
            results.append(probe)
            logger.debug(
                "[HEALTH] canary probe | bridge=%d latency=%dms status=%s",
                i, probe.get("latency_ms", 0), probe.get("status"),
            )
            # Persist
            if db:
                try:
                    db.store_probe(
                        scan_id, i, "pre_scan",
                        probe["status"], probe.get("latency_ms", 0),
                        probe.get("error"),
                    )
                except Exception as e:
                    logger.debug("Probe store failed: %s", e)

        if db:
            try:
                db.close()
            except Exception:
                pass

        # Emit summary event
        n_ok = sum(1 for r in results if r["status"] == "success")
        latencies = sorted(
            r["latency_ms"] for r in results if r["status"] == "success"
        )
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95_idx = min(int(len(latencies) * 0.95), max(len(latencies) - 1, 0))
        p95 = latencies[p95_idx] if latencies else 0

        if n_ok > 0:
            self._emit_event(
                scan_id, 'preflight', 'complete',
                f'Canary probes — {n_ok}/{len(results)} OK, '
                f'P50={p50}ms P95={p95}ms',
                duration_ms=p95,
                metadata={'probes': len(results), 'ok': n_ok,
                          'p50_ms': p50, 'p95_ms': p95},
            )
        else:
            self._emit_event(
                scan_id, 'preflight', 'warning',
                f'Canary probes — 0/{len(results)} OK (all failed)',
                metadata={'probes': len(results), 'ok': 0},
            )

        return results

    def _derive_adaptive_thresholds(self, scan_id, probe_results):
        """Compute adaptive stall timeout + rate governor floor from probes.

        Uses current scan probes + historical P95 (7-day rolling).
        """
        # Current scan P50/P95
        latencies = sorted(
            r["latency_ms"] for r in probe_results
            if r.get("status") == "success"
        )
        if not latencies:
            logger.info(
                "[HEALTH] no successful probes — using default thresholds"
            )
            self._adaptive_call_timeout = 600  # default
            return

        current_p50 = latencies[len(latencies) // 2]
        p95_idx = min(int(len(latencies) * 0.95), len(latencies) - 1)
        current_p95 = latencies[p95_idx]

        # Historical P95 from DB
        historical_p95 = None
        try:
            from src.data.db_manager import DatabaseManager
            db = DatabaseManager(Path(self.db_path))
            historical_p95 = db.get_probe_percentile(95, days=7)
            db.close()
        except Exception:
            pass

        # Use whichever P95 is higher (conservative)
        reference_p95 = max(
            current_p95,
            historical_p95 or current_p95,
        )

        # Derive adaptive call timeout
        # reference_p95 is for a single trivial probe (~2 tokens out).
        # A real batch with 25 tickets will take much longer.
        # Multiplier: reference_p95_seconds * tickets_per_batch * overhead
        avg_batch_tickets = 25  # default; could be read from batch_packer
        batch_multiplier = avg_batch_tickets * 2.5  # ~2.5s per ticket estimate
        adaptive_timeout = max(
            45,   # floor: bridge-side STALL_MS
            min(
                600,  # ceiling: current hardcoded max
                (reference_p95 / 1000) * batch_multiplier * 1.5,
            ),
        )
        self._adaptive_call_timeout = round(adaptive_timeout)

        # Derive rate governor floor — only use successful, sub-5s probes
        # to avoid inflating the floor from rate-limited or stalled probes
        ok_latencies = [
            r["latency_ms"] for r in probe_results
            if r.get("status") == "success" and r["latency_ms"] < 5000
        ]
        if ok_latencies:
            ok_p50 = sorted(ok_latencies)[len(ok_latencies) // 2] / 1000
            adaptive_floor = max(15.0, ok_p50 * 3.0)
        else:
            adaptive_floor = 15.0  # safe default when no clean probes
        if self._rate_governor:
            self._rate_governor.set_probe_floor(adaptive_floor)

        logger.info(
            "[HEALTH] adaptive thresholds | current_p50=%dms current_p95=%dms "
            "historical_p95=%s adaptive_timeout=%ds rate_floor=%.1fs",
            current_p50, current_p95,
            f"{historical_p95}ms" if historical_p95 else "none",
            self._adaptive_call_timeout, adaptive_floor,
        )
        self._emit_event(
            scan_id, 'preflight', 'complete',
            f'Adaptive thresholds — timeout={self._adaptive_call_timeout}s, '
            f'rate_floor={adaptive_floor:.1f}s '
            f'(probe P50={current_p50}ms P95={current_p95}ms)',
            metadata={
                'adaptive_timeout': self._adaptive_call_timeout,
                'rate_floor': round(adaptive_floor, 1),
                'current_p50': current_p50,
                'current_p95': current_p95,
                'historical_p95': historical_p95,
            },
        )

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Infrastructure
    # ──────────────────────────────────────────────────────────────────────

    def _boot_workers(self, num_workers, scan_id=None):
        """Create and boot ACP bridge + worker instances.

        Bridges are configured sequentially (fast, no I/O), then booted
        in parallel using threads to amortize cold-start latency.
        With 32 workers, parallel boot takes ~5s vs ~112s sequential.
        """
        import sys
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self._bridges = []
        self._workers = []

        # Phase 1: Create and configure all bridges (fast, no subprocess)
        for i in range(num_workers):
            bridge = ACPBridge(model=self._model)
            bridge.set_mcp_config([])  # No MCP — model outputs NDJSON, worker stores locally
            try:
                from src.data.db_manager import DatabaseManager
                worker_db = DatabaseManager(Path(self.db_path))
                bridge._usage_tracker = UsageTracker(worker_db)
                bridge._scan_id = scan_id
            except Exception:
                bridge._usage_tracker = None
                bridge._scan_id = scan_id

            def _make_death_cb(worker_idx, sid):
                def _on_bridge_death(bridge_ref):
                    logger.error(
                        "[HEALTH] bridge death callback | worker_%d scan=%s",
                        worker_idx, (sid or "?")[:8],
                    )
                    if sid:
                        try:
                            _exit_code = bridge_ref._process.returncode if bridge_ref._process else '?'
                            _last_err = (bridge_ref._last_stderr_lines[-1]
                                         if getattr(bridge_ref, '_last_stderr_lines', None)
                                         else '')
                            _err_hint = (_last_err[:80] + '…') if len(_last_err) > 80 else _last_err
                            self._emit_event(
                                    sid, 'warning', 'running',
                                    f'Bridge died for worker_{worker_idx} — '
                                    f'exit={_exit_code} '
                                    f'deaths={bridge_ref._death_count}'
                                    f'{f" | {_err_hint}" if _err_hint else ""}',
                                    metadata={
                                        'worker': f'worker_{worker_idx}',
                                        'death_count': bridge_ref._death_count,
                                        'exit_code': bridge_ref._process.returncode if bridge_ref._process else None,
                                        'stall_count': bridge_ref._stall_count,
                                        'consecutive_stalls': bridge_ref._consecutive_stalls,
                                        'boot_count': bridge_ref._boot_count,
                                        'boot_time': bridge_ref._boot_time,
                                    }
                                )
                        except Exception:
                            pass
                return _on_bridge_death
            bridge.set_on_death(_make_death_cb(i, scan_id))
            self._bridges.append(bridge)

        # Phase 2: Boot all bridges in parallel (subprocess spawn)
        t0 = time.time()

        def _boot_bridge(idx):
            self._bridges[idx].ensure_running()
            return idx

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = {pool.submit(_boot_bridge, i): i for i in range(num_workers)}
            booted = 0
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    future.result()
                    booted += 1
                except Exception as e:
                    logger.error(
                        "[HEALTH] bridge %d boot failed: %s", idx, e
                    )

        boot_elapsed = time.time() - t0
        logger.info(
            "ScanOrchestrator: booted %d/%d bridges in %.1fs (parallel)",
            booted, num_workers, boot_elapsed,
        )

        # Phase 3: Create worker agents (fast, no I/O)
        for i in range(num_workers):
            worker = WorkerAgent(
                f"worker_{i}", self._bridges[i], self.db_path
            )
            self._workers.append(worker)

        logger.info(
            f"ScanOrchestrator: booted {num_workers} workers"
        )

    def _boot_analyst(self):
        """Create analyst ACP bridge + agent (no MCP — analyst uses call_blocking only)."""
        self._analyst_bridge = ACPBridge(model=self._model)
        self._analyst_bridge.ensure_running()
        self._analyst = AnalystAgent(
            self._analyst_bridge, self.db_path
        )

    def _get_tickets_for_batch(self, conn, trc, chunk_n, chunk_total,
                                date_start, date_end, scan_id=None):
        """
        Load tickets for a batch. Builds ticket_id -> full_thread map.
        Handles mixed TRCs (JSON array in trc field).
        If scan_id is provided, skips already-classified tickets (5.2 resume).
        """
        # Determine if mixed batch
        trc_list = []
        if trc.startswith('['):
            try:
                trc_list = json.loads(trc)
            except json.JSONDecodeError:
                trc_list = [trc]
        else:
            trc_list = [trc]

        # Build query
        if '(untagged)' in trc_list:
            where_clause = "(trc_code IS NULL OR trc_code = '')"
            params = [date_start, date_end + ' 23:59:59']
        else:
            placeholders = ','.join('?' for _ in trc_list)
            where_clause = f"trc_code IN ({placeholders})"
            params = [date_start, date_end + ' 23:59:59'] + trc_list

        # Skip already-classified tickets on resume (5.2)
        # IMPORTANT: Only apply exclude on single-chunk batches.
        # Multi-chunk batches use positional slicing (chunk_n/chunk_total)
        # which requires the FULL ticket set for correct boundary math.
        # Excluding already-classified tickets from multi-chunk batches
        # shrinks the pool and causes chunk slicing to silently drop tickets.
        exclude_clause = ""
        if scan_id and chunk_total <= 1:
            exclude_clause = (
                " AND ticket_id NOT IN ("
                "   SELECT ticket_id FROM nlp_ticket_classifications"
                "   WHERE scan_id = ?"
                " )"
            )
            params.append(scan_id)

        # Get distinct ticket IDs (via warehouse)
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        _batch_reg = SourceRegistry(conn)
        _batch_wq = WarehouseQuery(conn, _batch_reg)
        _ticket_rows_raw = _batch_wq.query_conversations_raw(f"""
            SELECT DISTINCT ticket_id, trc_code
            FROM {{table}}
            WHERE created_at >= ? AND created_at <= ?
              AND {where_clause}
              {exclude_clause}
            ORDER BY ticket_id
        """, params)
        ticket_rows = [{"ticket_id": r[0], "trc_code": r[1]} for r in _ticket_rows_raw]

        if not ticket_rows:
            return []

        # Apply chunking if multi-chunk
        if chunk_total > 1:
            chunk_size = -(-len(ticket_rows) // chunk_total)
            start_idx = (chunk_n - 1) * chunk_size
            end_idx = start_idx + chunk_size
            ticket_rows = ticket_rows[start_idx:end_idx]

        # Build ticket list with full threads
        tickets = []
        for row in ticket_rows:
            tid = row['ticket_id']

            # Production schema: full_thread via warehouse
            _ft_rows = _batch_wq.query_conversations_raw(
                "SELECT full_thread FROM {table} WHERE ticket_id = ?",
                (tid,),
            )
            full_thread = (_ft_rows[0][0] or '') if _ft_rows else ''

            tickets.append({
                "ticket_id": tid,
                "trc": row['trc_code'] or '(untagged)',
                "full_thread": full_thread,
            })

        return tickets

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _make_batch(self, scan_id, batch_num, trc, chunk_n, chunk_total,
                    n_workers):
        """Create a batch record dict (same structure as ScanWorkerManager)."""
        return {
            'batch_id': str(uuid.uuid4()),
            'scan_id': scan_id,
            'batch_number': batch_num,
            'trc': trc,
            'trc_chunk': chunk_n,
            'trc_chunk_total': chunk_total,
            'worker_id': batch_num % n_workers,
        }

    def _format_stats_context(self, stats_result):
        """Format stats context tool result into prompt text."""
        if not stats_result or stats_result.get("error"):
            return "No anomalies detected for this TRC."

        lines = []
        for flag in stats_result.get("incident_flags", []):
            lines.append(
                f"- Incident: {flag['type']} ({flag['date']})"
            )
        for anomaly in stats_result.get("theta_anomalies", []):
            lines.append(
                f"- Theta: {anomaly['metric']} "
                f"{anomaly['direction']} ({anomaly['date']})"
            )
        terms = stats_result.get("rising_terms", [])
        if terms:
            top = ', '.join(t['term'] for t in terms[:5])
            lines.append(f"- Rising terms: {top}")

        return "\n".join(lines) if lines else "No anomalies detected."

    def _format_taxonomy(self, tax_result):
        """Format taxonomy tool result into prompt text."""
        if not tax_result or tax_result.get("error"):
            return ""

        patterns = tax_result.get("patterns", [])
        if not patterns:
            patterns_by_trc = tax_result.get("patterns_by_trc", {})
            if not patterns_by_trc:
                return ""

            # Mixed TRC format
            count = tax_result.get("pattern_count", 0)
            section = (
                f"EXISTING SUB-PATTERNS ACROSS "
                f"{len(patterns_by_trc)} TRCs ({count} patterns):\n\n"
            )
            idx = 0
            for trc, pats in patterns_by_trc.items():
                section += f"-- {trc} --\n"
                for p in pats:
                    idx += 1
                    ngrams = ', '.join(p.get('ngrams', []))
                    section += (
                        f"{idx}. \"{p['label']}\"\n"
                        f"   N-grams: [{ngrams}]\n"
                        f"   Friction: {p['friction_type']} | "
                        f"Lifetime: {p['lifetime_tickets']} tickets\n\n"
                    )
            section += (
                "If a ticket matches an existing sub-pattern, "
                "use that exact label. If it does NOT match, "
                "set is_novel=true with justification."
            )
            return section

        # Single TRC format
        count = len(patterns)
        section = (
            f"EXISTING SUB-PATTERNS FOR THIS TRC "
            f"({count} active):\n\n"
        )
        for i, p in enumerate(patterns, 1):
            ngrams = ', '.join(p.get('ngrams', []))
            section += (
                f"{i}. \"{p['label']}\"\n"
                f"   N-grams: [{ngrams}]\n"
                f"   Friction: {p['friction_type']} | "
                f"Lifetime: {p['lifetime_tickets']} tickets\n\n"
            )
        section += (
            "If a ticket matches an existing sub-pattern, "
            "use that exact label. If it does NOT match, "
            "set is_novel=true with justification."
        )
        return section

    def _reap_interrupted_scans(self):
        """Detect and close out scans left in 'running' from a prior
        process.

        When the desktop app is closed mid-scan (or crashes, or hits a
        driver timeout) the orchestrator's worker threads — all daemon
        threads — die silently with the process. The scan row keeps
        status='running' forever, which poisons the UI history tab
        and blocks the 'retry failed batches' code path (which only
        accepts completed / completed_with_errors states).

        This runs once at the top of start_scan(), before any new work
        begins. It's safe because a new ScanOrchestrator instance in a
        fresh process cannot own a 'running' scan from a past process.
        """
        try:
            conn = self._get_conn()
            stale = conn.execute("""
                SELECT scan_id FROM nlp_scan_runs
                WHERE status IN ('running', 'paused')
            """).fetchall()
            if not stale:
                conn.close()
                return

            for row in stale:
                sid = row['scan_id']
                # Flip any in-flight batches to failed with a clear
                # marker so the retry path can pick them up later.
                conn.execute(
                    "UPDATE nlp_batches SET status = 'failed', "
                    "error_message = 'scan_interrupted' "
                    "WHERE scan_id = ? AND status IN "
                    "('running', 'claimed', 'queued')",
                    (sid,)
                )
                conn.execute(
                    "UPDATE nlp_scan_runs SET status = 'interrupted', "
                    "completed_at = ? WHERE scan_id = ?",
                    (datetime.utcnow().isoformat(), sid)
                )
                try:
                    self._emit_event(
                        sid, 'scan_interrupted', 'warn',
                        'Scan marked interrupted: prior app process '
                        'ended before scan completed',
                    )
                except Exception:
                    pass
                logger.warning(
                    "Reaped interrupted scan %s (process died mid-scan)",
                    sid[:8],
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_reap_interrupted_scans failed: {e}")

    def _finalize_scan(self, scan_id):
        """Mark scan as completed (or completed_with_errors) in the database."""
        # D7: Snapshot rate governor state before closing
        if hasattr(self, '_rate_governor') and self._rate_governor:
            try:
                rg = self._rate_governor
                # Diagnostic summary (F1 instrumentation)
                acq = getattr(rg, '_acquire_count', 0)
                if acq > 0:
                    logger.info(
                        "[PERF] Rate governor: %d acquires, "
                        "avg burst wait %.2fs, avg semaphore wait %.2fs",
                        acq,
                        rg._burst_wait_total / acq,
                        rg._semaphore_wait_total / acq,
                    )
                self._emit_event(
                    scan_id, 'info', 'complete',
                    f'Rate governor final: interval={rg.min_interval:.1f}s, '
                    f'throughput={rg.get_throughput():.1f}/min',
                    metadata={
                        'min_interval': rg.min_interval,
                        'throughput': rg.get_throughput(),
                        'probe_floor': getattr(rg, '_probe_floor', None),
                        'total_calls': len(rg.call_log),
                        'rate_limit_events': 1 if getattr(rg, '_last_rate_limit_time', None) else 0,
                        'avg_burst_wait': round(rg._burst_wait_total / acq, 3) if acq else 0,
                        'avg_sem_wait': round(rg._semaphore_wait_total / acq, 3) if acq else 0,
                    }
                )
            except Exception:
                pass

        conn = self._get_conn()
        try:
            # Completed includes partial-delivery batches (drops are
            # swept separately). Failed is exhausted-retry batches.
            completed_batches = conn.execute("""
                SELECT COUNT(*) as n FROM nlp_batches
                WHERE scan_id = ? AND status IN ('completed', 'partial')
            """, (scan_id,)).fetchone()

            failed_batches = conn.execute("""
                SELECT COUNT(*) as n FROM nlp_batches
                WHERE scan_id = ? AND status = 'failed'
            """, (scan_id,)).fetchone()

            # ── Fix #3: Scan-wide delivery audit ───────────────────
            # After the sweep has had its chance, count orphans that
            # STILL don't have a classification row. If any remain,
            # the scan closes as completed_with_errors instead of
            # silently reporting success.
            orphan_row = conn.execute("""
                SELECT COUNT(DISTINCT bt.ticket_id) AS n
                FROM nlp_batch_tickets bt
                LEFT JOIN nlp_ticket_classifications tc
                  ON tc.ticket_id = bt.ticket_id
                 AND tc.scan_id   = bt.scan_id
                WHERE bt.scan_id = ?
                  AND tc.ticket_id IS NULL
            """, (scan_id,)).fetchone()
            orphan_count = orphan_row['n'] if orphan_row else 0
            requested_row = conn.execute(
                "SELECT COUNT(DISTINCT ticket_id) AS n "
                "FROM nlp_batch_tickets WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            requested_count = requested_row['n'] if requested_row else 0
            delivered_count = max(requested_count - orphan_count, 0)

            if orphan_count > 0:
                # Loud event — this is the condition the app never
                # previously surfaced: batches marked complete but
                # tickets never landed in nlp_ticket_classifications.
                logger.warning(
                    "Scan audit: %d orphan ticket(s) — requested=%d, "
                    "delivered=%d (sweep did not recover them)",
                    orphan_count, requested_count, delivered_count,
                )
                try:
                    self._emit_event(
                        scan_id, 'scan_audit', 'warn',
                        f'Scan audit: {orphan_count} tickets missing '
                        f'classifications after sweep '
                        f'({delivered_count}/{requested_count} delivered)',
                        metadata={
                            'orphan_count': orphan_count,
                            'delivered': delivered_count,
                            'requested': requested_count,
                        }
                    )
                except Exception:
                    pass
            else:
                try:
                    self._emit_event(
                        scan_id, 'scan_audit', 'complete',
                        f'Scan audit: all {requested_count} tickets '
                        f'classified (no orphans)',
                        metadata={
                            'orphan_count': 0,
                            'delivered': delivered_count,
                            'requested': requested_count,
                        }
                    )
                except Exception:
                    pass

            # Aggregate tokens from nlp_batches (authoritative source)
            token_row = conn.execute("""
                SELECT COALESCE(SUM(input_tokens), 0) as total_in,
                       COALESCE(SUM(output_tokens), 0) as total_out,
                       COALESCE(SUM(cost_usd), 0.0) as total_cost
                FROM nlp_batches WHERE scan_id = ?
            """, (scan_id,)).fetchone()
            total_tokens_in = token_row['total_in'] if token_row else 0
            total_tokens_out = token_row['total_out'] if token_row else 0
            actual_cost = token_row['total_cost'] if token_row else 0.0

            # Fall back to gemini_usage if batch-level cost is zero
            if actual_cost == 0.0:
                usage_row = conn.execute("""
                    SELECT COALESCE(SUM(cost_usd), 0.0) as total_cost
                    FROM gemini_usage WHERE scan_id = ?
                """, (scan_id,)).fetchone()
                actual_cost = usage_row['total_cost'] if usage_row else 0.0

            failed_n = failed_batches['n'] if failed_batches else 0
            # completed_with_errors if either: a batch failed outright,
            # OR the delivery audit found orphans the sweep couldn't
            # recover. Both represent real data loss the user needs to
            # see, not just a green "completed".
            final_status = (
                'completed_with_errors'
                if (failed_n > 0 or orphan_count > 0)
                else 'completed'
            )

            # Build informative error_log from batch-level errors AND
            # any orphan drops that remain after the sweep.
            error_log_text = None
            parts = []
            if failed_n > 0:
                error_reasons = conn.execute("""
                    SELECT error_message, COUNT(*) as cnt
                    FROM nlp_batches
                    WHERE scan_id = ? AND status = 'failed'
                      AND error_message IS NOT NULL
                    GROUP BY error_message
                """, (scan_id,)).fetchall()
                parts.append(f"{failed_n} batch(es) failed")
                for row in error_reasons:
                    parts.append(f"  {row['error_message']}: {row['cnt']}")
            if orphan_count > 0:
                parts.append(
                    f"{orphan_count} ticket(s) orphaned (delivery-lie, "
                    f"sweep did not recover)"
                )
            if parts:
                error_log_text = "; ".join(parts)

            conn.execute("""
                UPDATE nlp_scan_runs SET
                    status = ?,
                    completed_batches = ?,
                    completed_at = ?,
                    actual_cost_usd = ?,
                    total_input_tokens = ?,
                    total_output_tokens = ?,
                    error_log = CASE WHEN ? IS NOT NULL
                        THEN ? ELSE error_log END
                WHERE scan_id = ?
            """, (
                final_status,
                completed_batches['n'] if completed_batches else 0,
                datetime.utcnow().isoformat(),
                actual_cost,
                total_tokens_in,
                total_tokens_out,
                error_log_text, error_log_text,
                scan_id,
            ))
            conn.commit()

            if failed_n > 0:
                logger.warning(
                    f"Scan {scan_id[:8]} finalized as {final_status}: "
                    f"{failed_n} batch(es) failed"
                )
        finally:
            conn.close()

    def _mark_scan_error(self, scan_id, error_msg):
        """Mark scan as failed with timestamp and error details."""
        conn = self._get_conn()
        try:
            # Also roll up whatever tokens/cost were captured before failure
            token_row = conn.execute("""
                SELECT COALESCE(SUM(input_tokens), 0) as total_in,
                       COALESCE(SUM(output_tokens), 0) as total_out,
                       COALESCE(SUM(cost_usd), 0.0) as total_cost
                FROM nlp_batches WHERE scan_id = ?
            """, (scan_id,)).fetchone()

            conn.execute("""
                UPDATE nlp_scan_runs SET
                    status = 'failed',
                    completed_at = ?,
                    error_log = ?,
                    total_input_tokens = ?,
                    total_output_tokens = ?,
                    actual_cost_usd = ?
                WHERE scan_id = ?
            """, (
                datetime.utcnow().isoformat(),
                error_msg,
                token_row['total_in'] if token_row else 0,
                token_row['total_out'] if token_row else 0,
                token_row['total_cost'] if token_row else 0.0,
                scan_id,
            ))
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self):
        """Get a new SQLite connection."""
        return get_connection(self.db_path)

    def __repr__(self):
        return (
            f"ScanOrchestrator(workers={len(self._workers)}, "
            f"scan={self._current_scan_id})"
        )
