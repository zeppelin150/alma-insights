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

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

from src.agents.gemini_bridge_wrapper import GeminiBridge
from src.agents.worker_agent import WorkerAgent
from src.agents.supervisor import Supervisor
from src.agents.analyst_agent import AnalystAgent
from src.agents.rate_governor import RateGovernor
from src.agents.batch_packer import BatchPacker
from src.data.usage_tracker import UsageTracker

logger = logging.getLogger("alma.orchestrator")

_PROJECT_ROOT = Path(__file__).parent.parent.parent

# ── Limits ──
MAX_TICKETS_CLI = 5000         # hard cap for CLI mode
MAX_BATCH_TICKETS = 25         # default per-batch cap (overridden by model limits)
MAX_PARALLEL_WORKERS = 3       # max concurrent workers


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

    def __init__(self, db_path=None, num_workers=3):
        """
        Args:
            db_path: Path to SQLite database (auto-detected if None).
            num_workers: Number of parallel worker agents (capped at 3).
        """
        raw = str(db_path or (_PROJECT_ROOT / "data" / "local_warehouse.db"))
        self.db_path = str(Path(raw).resolve())
        self.num_workers = min(num_workers, MAX_PARALLEL_WORKERS)

        # Components (created on start_scan, destroyed on shutdown)
        self._bridges = []
        self._workers = []
        self._analyst_bridge = None
        self._analyst = None
        self._rate_governor = RateGovernor()
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

        # 6.3: Adaptive thresholds (set by canary probes before each scan)
        self._adaptive_call_timeout = 600  # default; overridden by probes

    # ──────────────────────────────────────────────────────────────────────
    # Public API (matches ScanWorkerManager)
    # ──────────────────────────────────────────────────────────────────────

    def start_scan(self, date_start, date_end,
                   trc_filter=None, batch_size=25,
                   budget_cap=50.0, parallel_workers=3,
                   mode='full'):
        """
        Create scan record, partition batches, boot workers,
        start classification.

        Returns: dict with scan_id, total_batches, etc. or error key.
        """
        actual_workers = min(parallel_workers, MAX_PARALLEL_WORKERS)
        conn = self._get_conn()
        scan_id = str(uuid.uuid4())

        try:
            # ── Query TRC distribution ──
            trc_counts = conn.execute("""
                SELECT trc_code AS trc, COUNT(DISTINCT ticket_id) AS n
                FROM conversations
                WHERE created_at >= ? AND created_at <= ?
                  AND trc_code IS NOT NULL AND trc_code != ''
                GROUP BY trc_code ORDER BY n DESC
            """, (date_start, date_end + ' 23:59:59')).fetchall()

            untagged_row = conn.execute("""
                SELECT COUNT(DISTINCT ticket_id) AS n FROM conversations
                WHERE created_at >= ? AND created_at <= ?
                  AND (trc_code IS NULL OR trc_code = '')
            """, (date_start, date_end + ' 23:59:59')).fetchone()

            # ── Query thread length stats per TRC (5.4 input-aware batching) ──
            thread_stats_rows = conn.execute("""
                SELECT trc_code AS trc,
                       AVG(CASE WHEN LENGTH(COALESCE(full_thread, '')) > 3000
                                THEN 3000
                                ELSE LENGTH(COALESCE(full_thread, ''))
                           END) AS avg_thread,
                       SUM(CASE WHEN LENGTH(COALESCE(full_thread, '')) > 3000
                                THEN 3000
                                ELSE LENGTH(COALESCE(full_thread, ''))
                           END) AS total_thread_chars
                FROM conversations
                WHERE created_at >= ? AND created_at <= ?
                  AND trc_code IS NOT NULL AND trc_code != ''
                GROUP BY trc_code
            """, (date_start, date_end + ' 23:59:59')).fetchall()

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
        """Read model from settings.yaml — same key the Settings UI writes to."""
        config_path = Path(self.db_path).parent.parent / "config" / "settings.yaml"
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            model = cfg.get("gemini", {}).get("model", "gemini-2.5-flash")
            logger.info(f"ScanOrchestrator: using model '{model}' from settings")
            return model
        except Exception:
            return "gemini-2.5-flash"

    def _load_scan_config(self):
        """Read nlp_scan settings from settings.yaml (5.2)."""
        config_path = Path(self.db_path).parent.parent / "config" / "settings.yaml"
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return cfg.get("nlp_scan", {})
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
            interval = self._rate_governor.min_interval
            self._emit_event(scan_id, 'preflight', 'complete',
                             f'Rate governor — initial interval {interval}s')

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

            # ── 5. Run supervisor (blocks until complete) ──
            self._supervisor.run()

            # ── 6. Wait for worker threads ──
            for t in self._worker_threads:
                t.join(timeout=30)

            # ── Check if cancelled ──
            if self._stop_event.is_set():
                self._emit_event(scan_id, 'info', 'complete',
                                 'Scan stopped by user')
                logger.info(f"Scan {scan_id[:8]}: stopped by user")
                return

            # ── 6b. Retry sweep for any remaining failed batches ──
            self._retry_sweep(scan_id, date_start, date_end)

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
            # Shutdown bridges (workers stay defined for status queries)
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

    def _worker_loop(self, worker, scan_id, date_start, date_end,
                     budget_cap):
        """
        Worker thread: pulls batches from queue and classifies them.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

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

            # Get next batch
            try:
                batch = self._batch_queue.get(timeout=2)
            except Empty:
                # No more batches
                break

            batch_id = batch['batch_id']
            trc = batch['trc']
            batch_num = batch.get('batch_number', 0)

            try:
                # Mark batch as running
                try:
                    conn.execute(
                        "UPDATE nlp_batches SET status = 'running' "
                        "WHERE batch_id = ?", (batch_id,)
                    )
                    conn.commit()
                except Exception:
                    pass

                # ── 6.2: Early dead-bridge check before rate-governor wait ──
                if not worker.bridge.is_alive():
                    logger.warning(
                        "[HEALTH] %s bridge dead before rate-governor acquire "
                        "— restarting bridge",
                        worker.agent_id,
                    )
                    restarted = False
                    for _ra in range(3):
                        try:
                            worker.bridge.restart()
                            time.sleep(3)
                            if worker.bridge.is_alive():
                                restarted = True
                                logger.info(
                                    "[HEALTH] %s bridge revived (attempt %d)",
                                    worker.agent_id, _ra + 1,
                                )
                                break
                        except Exception as _re:
                            logger.error(
                                "[HEALTH] bridge restart attempt %d failed: %s",
                                _ra + 1, _re,
                            )
                            time.sleep(2 ** _ra)
                    if not restarted:
                        logger.error(
                            "[HEALTH] %s bridge unrecoverable — requeueing batch",
                            worker.agent_id,
                        )
                        self._batch_queue.put(batch)
                        break  # exit worker loop; this worker is dead

                # Acquire rate governor slot
                if not self._rate_governor.acquire(timeout=120):
                    logger.warning(
                        f"Worker {worker.agent_id}: rate governor timeout"
                    )
                    self._batch_queue.put(batch)  # re-queue
                    continue

                # Requeue guard: skip if stalled batch already fully classified (5.3)
                retry_count = batch.get('retry_count', 0)
                expected_tickets = batch.get('ticket_count', 0)
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
                        continue

                # Get tickets for this batch (skip already-classified on resume, 5.2)
                tickets = self._get_tickets_for_batch(
                    conn, trc, batch.get('trc_chunk', 1),
                    batch.get('trc_chunk_total', 1),
                    date_start, date_end, scan_id=scan_id
                )

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
                    continue

                # Build batch payload
                # Get stats context and taxonomy via tool calls
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

                payload = {
                    "scan_id": scan_id,
                    "batch_id": batch_id,
                    "trc": trc,
                    "tickets": tickets,
                    "stats_context": stats_ctx,
                    "sub_taxonomy": sub_tax,
                    "chunk_n": batch.get('trc_chunk', 1),
                    "chunk_total": batch.get('trc_chunk_total', 1),
                    "date_start": date_start,
                    "date_end": date_end,
                    "call_timeout": self._adaptive_call_timeout,
                }

                # Check context overflow
                if worker.needs_reset():
                    worker.reset()

                # Ensure bridge is alive before classifying
                if not worker.bridge.is_alive():
                    logger.warning(
                        "[HEALTH] %s bridge dead pre-classify | "
                        "batch=%s deaths=%d",
                        worker.agent_id, batch_id[:8],
                        getattr(worker.bridge, '_death_count', 0),
                    )
                    restarted = False
                    for restart_attempt in range(3):
                        try:
                            worker.bridge.restart()
                            time.sleep(3)
                            if worker.bridge.is_alive():
                                restarted = True
                                logger.info(
                                    f"Worker {worker.agent_id}: bridge "
                                    f"restarted (attempt {restart_attempt + 1})"
                                )
                                break
                        except Exception as e:
                            logger.error(
                                f"Bridge restart attempt "
                                f"{restart_attempt + 1} failed: {e}"
                            )
                            time.sleep(2 ** restart_attempt)
                    if not restarted:
                        logger.error(
                            f"Worker {worker.agent_id}: bridge restart "
                            f"failed after 3 attempts, skipping batch"
                        )
                        self._batch_queue.put(batch)  # requeue for other worker
                        continue

                # Classify
                start = time.time()
                result = worker.classify_batch(payload)
                elapsed = time.time() - start

                # Report to rate governor
                if result.get("error"):
                    error_code = str(result["error"])
                    if "rate_limit" in error_code:
                        self._rate_governor.report_rate_limit()
                    else:
                        self._rate_governor.report_error()

                    # ── 6.2: Track stall events on bridge + escalate ──
                    if "stall_timeout" in error_code:
                        needs_restart = worker.bridge.record_stall()
                        if needs_restart:
                            self._emit_event(
                                scan_id, 'warning', 'running',
                                f'Bridge stall escalation on {worker.agent_id} '
                                f'— auto-restarting bridge',
                                metadata={
                                    'worker': worker.agent_id,
                                    'consecutive_stalls':
                                        worker.bridge._consecutive_stalls,
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
                    self._rate_governor.report_success(elapsed)
                    worker.bridge.record_success()

                # Record batch size for learning
                n_classified = result.get("classified", 0)
                if n_classified > 0:
                    # Use actual response length if available, else estimate
                    output_chars = result.get(
                        "response_chars", n_classified * 800
                    )
                    self._batch_packer.record_result(
                        trc, len(tickets), output_chars
                    )

                # Update batch status
                has_error = bool(result.get('error'))
                status = 'completed' if not has_error else 'failed'
                classified = result.get('classified', 0)
                elapsed_s = round(elapsed, 1)

                # ── Retry logic for failed batches ──
                if has_error:
                    retry_count = batch.get('retry_count', 0) + 1
                    error_str = str(result.get('error', '')).lower()
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
                        self._emit_event(
                            scan_id, 'info', 'running',
                            f'Batch {batch_num} retry {retry_count}/{max_retries} '
                            f'| {result.get("error", "unknown")}',
                            metadata={'batch_id': batch_id}
                        )
                        logger.info(
                            f"Batch {batch_id[:8]} requeued "
                            f"(retry {retry_count}/{max_retries})"
                        )
                        continue  # skip marking as failed

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
                    conn.execute("""
                        UPDATE nlp_scan_runs SET
                            completed_batches = (
                                SELECT COUNT(*) FROM nlp_batches
                                WHERE scan_id = ? AND status = 'completed'
                            ),
                            actual_cost_usd = COALESCE(
                                (SELECT SUM(cost_usd) FROM gemini_usage
                                 WHERE scan_id = ?), 0.0
                            )
                        WHERE scan_id = ?
                    """, (scan_id, scan_id, scan_id))
                    conn.commit()
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

                # Truncation recovery: re-queue at half size
                if result.get("error") == "truncation":
                    half = self._batch_packer.halve_for_retry(
                        len(tickets)
                    )
                    logger.info(
                        f"Truncation recovery: re-queuing at size {half}"
                    )

            except Exception as e:
                # Catch-all: prevent worker thread from dying silently
                logger.error(
                    f"Worker {worker.agent_id}: unhandled error on "
                    f"batch {batch_id[:8]}: {e}"
                )
                try:
                    conn.execute("""
                        UPDATE nlp_batches SET status = 'failed'
                        WHERE batch_id = ?
                    """, (batch_id,))
                    conn.execute("""
                        UPDATE nlp_scan_runs SET
                            completed_batches = (
                                SELECT COUNT(*) FROM nlp_batches
                                WHERE scan_id = ?
                                  AND status = 'completed'
                            ),
                            actual_cost_usd = COALESCE(
                                (SELECT SUM(cost_usd) FROM gemini_usage
                                 WHERE scan_id = ?), 0.0
                            )
                        WHERE scan_id = ?
                    """, (scan_id, scan_id, scan_id))
                    conn.commit()
                except Exception:
                    pass
                if self._supervisor:
                    self._supervisor.notify_batch_complete(
                        worker.agent_id,
                        {"classified": 0, "error": str(e)}
                    )

        conn.close()

    # ──────────────────────────────────────────────────────────────────────
    # Internal: Retry Sweep
    # ──────────────────────────────────────────────────────────────────────

    def _retry_sweep(self, scan_id, date_start, date_end):
        """One final retry pass for failed batches after main scan completes.

        Boots a single fresh worker with a clean bridge context and
        re-attempts each failed batch once.  Called automatically at
        the end of ``_run_scan`` and also by ``retry_failed_batches``.
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
            logger.info(
                f"Retry sweep: {n_failed} failed batch(es) to retry"
            )
            self._emit_event(
                scan_id, 'info', 'running',
                f'Retry sweep: re-attempting {n_failed} failed batch(es)'
            )

            # Boot one fresh worker with clean bridge context
            self._boot_workers(1, scan_id=scan_id)
            worker = self._workers[0]

            for batch_row in failed:
                if self._cancelled or self._stop_event.is_set():
                    break

                batch_id = batch_row['batch_id']
                trc = batch_row['trc']
                batch_num = batch_row['batch_number']
                chunk_n = batch_row['trc_chunk'] or 1
                chunk_total = batch_row['trc_chunk_total'] or 1

                # Mark as running
                conn.execute(
                    "UPDATE nlp_batches SET status = 'running' "
                    "WHERE batch_id = ?",
                    (batch_id,)
                )
                conn.commit()

                self._emit_event(
                    scan_id, 'batch_start', 'running',
                    f'Retry sweep batch {batch_num} | {trc[:40]}',
                    metadata={'batch_id': batch_id, 'trc': trc}
                )

                # Get remaining unclassified tickets for this batch
                tickets = self._get_tickets_for_batch(
                    conn, trc, chunk_n, chunk_total,
                    date_start, date_end, scan_id=scan_id
                )

                if not tickets:
                    conn.execute(
                        "UPDATE nlp_batches SET status = 'completed' "
                        "WHERE batch_id = ?",
                        (batch_id,)
                    )
                    conn.commit()
                    logger.info(
                        f"Retry sweep batch {batch_id[:8]}: "
                        f"all tickets already classified"
                    )
                    continue

                # Build payload (same structure as _worker_loop)
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
                    logger.debug(f"Retry sweep context build failed: {e}")

                payload = {
                    "scan_id": scan_id,
                    "batch_id": batch_id,
                    "trc": trc,
                    "tickets": tickets,
                    "stats_context": stats_ctx,
                    "sub_taxonomy": sub_tax,
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

                sweep_lbl = 'complete' if not has_error else 'error'
                classified = result.get('classified', 0)
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

            # Shutdown the sweep worker's bridge
            try:
                worker.bridge.shutdown()
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
        """Fire a canary probe through each bridge and store results.

        Probes are sequential to avoid rate-limit interference.
        Returns list of probe result dicts.
        """
        from src.data.db_manager import DatabaseManager
        results = []
        db = None
        try:
            db = DatabaseManager(Path(self.db_path))
        except Exception as e:
            logger.warning("[HEALTH] canary probe: DB init failed: %s", e)

        for i, bridge in enumerate(self._bridges):
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

        # Derive rate governor floor (probes are fast; don't tighten below 3x P50)
        p50_seconds = current_p50 / 1000
        adaptive_floor = max(15.0, p50_seconds * 3.0)
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
        """Create and boot bridge + worker instances."""
        self._bridges = []
        self._workers = []

        for i in range(num_workers):
            bridge = GeminiBridge(model=self._model)
            # Attach per-bridge usage tracker (thread-safe, 5.2 fix)
            try:
                from src.data.db_manager import DatabaseManager
                worker_db = DatabaseManager(Path(self.db_path))
                bridge._usage_tracker = UsageTracker(worker_db)
                bridge._scan_id = scan_id
            except Exception:
                bridge._usage_tracker = None
                bridge._scan_id = scan_id

            # 6.2: Wire death callback for scan event emission
            def _make_death_cb(worker_idx, sid):
                def _on_bridge_death(bridge_ref):
                    logger.error(
                        "[HEALTH] bridge death callback | worker_%d scan=%s",
                        worker_idx, (sid or "?")[:8],
                    )
                    if sid:
                        try:
                            self._emit_event(
                                sid, 'warning', 'running',
                                f'Bridge died for worker_{worker_idx} — '
                                f'watchdog detected exit',
                                metadata={
                                    'worker': f'worker_{worker_idx}',
                                    'death_count': bridge_ref._death_count,
                                }
                            )
                        except Exception:
                            pass
                return _on_bridge_death
            bridge.set_on_death(_make_death_cb(i, scan_id))

            bridge.ensure_running()
            self._bridges.append(bridge)

            worker = WorkerAgent(
                f"worker_{i}", bridge, self.db_path
            )
            self._workers.append(worker)

        logger.info(
            f"ScanOrchestrator: booted {num_workers} workers"
        )

    def _boot_analyst(self):
        """Create analyst bridge + agent."""
        self._analyst_bridge = GeminiBridge(model=self._model)
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
        exclude_clause = ""
        if scan_id:
            exclude_clause = (
                " AND ticket_id NOT IN ("
                "   SELECT ticket_id FROM nlp_ticket_classifications"
                "   WHERE scan_id = ?"
                " )"
            )
            params.append(scan_id)

        # Get distinct ticket IDs
        ticket_rows = conn.execute(f"""
            SELECT DISTINCT ticket_id, trc_code
            FROM conversations
            WHERE created_at >= ? AND created_at <= ?
              AND {where_clause}
              {exclude_clause}
            ORDER BY ticket_id
        """, params).fetchall()

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

            # Production schema: full_thread is a single text column
            thread_row = conn.execute("""
                SELECT full_thread FROM conversations
                WHERE ticket_id = ?
            """, (tid,)).fetchone()

            full_thread = (thread_row['full_thread'] or '') if thread_row else ''

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

    def _finalize_scan(self, scan_id):
        """Mark scan as completed (or completed_with_errors) in the database."""
        conn = self._get_conn()
        try:
            completed_batches = conn.execute("""
                SELECT COUNT(*) as n FROM nlp_batches
                WHERE scan_id = ? AND status = 'completed'
            """, (scan_id,)).fetchone()

            failed_batches = conn.execute("""
                SELECT COUNT(*) as n FROM nlp_batches
                WHERE scan_id = ? AND status = 'failed'
            """, (scan_id,)).fetchone()

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
            final_status = (
                'completed_with_errors' if failed_n > 0 else 'completed'
            )

            # Build informative error_log from batch-level errors
            error_log_text = None
            if failed_n > 0:
                error_reasons = conn.execute("""
                    SELECT error_message, COUNT(*) as cnt
                    FROM nlp_batches
                    WHERE scan_id = ? AND status = 'failed'
                      AND error_message IS NOT NULL
                    GROUP BY error_message
                """, (scan_id,)).fetchall()
                parts = [f"{failed_n} batch(es) failed"]
                for row in error_reasons:
                    parts.append(f"  {row['error_message']}: {row['cnt']}")
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def __repr__(self):
        return (
            f"ScanOrchestrator(workers={len(self._workers)}, "
            f"scan={self._current_scan_id})"
        )
