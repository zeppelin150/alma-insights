"""
Alma Insights — NLP Scan Worker Manager (CLI Mode)
Manages NLP scan worker subprocess(es). Communicates via SQLite only.

In CLI mode: launches scan_worker.py as detached subprocess.
In server mode: delegates to Node.js scan server (future).
"""

import sys
import os
import json
import uuid
import subprocess
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("alma.scan_worker_mgr")

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

MAX_TICKETS_CLI = 50_000

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ScanWorkerManager:

    def __init__(self, db_path=None):
        # Always resolve to absolute path — detached workers may have
        # a different CWD and relative paths would resolve wrong.
        raw = str(db_path or (_PROJECT_ROOT / "data" / "local_warehouse.db"))
        self.db_path = str(Path(raw).resolve())
        self.mode = self._get_mode()

    def _get_mode(self):
        env = os.environ.get('ALMA_SCAN_MODE', '').lower()
        if env in ('cli', 'server'):
            return env
        try:
            import yaml
            config_path = _PROJECT_ROOT / 'config' / 'settings.yaml'
            with open(config_path, encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            return cfg.get('nlp_scan', {}).get('mode', 'cli')
        except Exception:
            return 'cli'

    def start_scan(self, date_start, date_end,
                   trc_filter=None, batch_size=700,
                   budget_cap=50.0, parallel_workers=1,
                   mode='full'):
        """
        Create scan record, partition batches by TRC,
        launch worker subprocess(es).

        Returns: dict with scan_id, total_batches, etc. or error key.
        """
        if self.mode == 'server':
            raise NotImplementedError(
                "Server mode not enabled. Set ALMA_SCAN_MODE=server "
                "and configure scan_server/ directory."
            )

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

            # Also check untagged tickets
            untagged_row = conn.execute("""
                SELECT COUNT(DISTINCT ticket_id) AS n FROM conversations
                WHERE created_at >= ? AND created_at <= ?
                  AND (trc_code IS NULL OR trc_code = '')
            """, (date_start, date_end + ' 23:59:59')).fetchone()

            # ── Partition into batches ──
            # Strategy:
            #   - Large TRCs (> batch_size tickets) → chunked into multiple batches
            #   - Medium TRCs (standalone, ≤ batch_size) → one batch each
            #   - Small TRCs → packed together into "(mixed)" batches up to batch_size
            #
            # "Small" threshold = batch_size / 3 (e.g. 233 for batch_size=700).
            # This avoids 84 batches for 888 tickets spread across 84 TRCs.

            batches = []
            batch_num = 0
            total_tickets = 0
            small_threshold = max(10, batch_size // 3)
            # Max tickets per batch to stay within Gemini output limits
            # 200 tickets × ~950 chars/record ≈ 190K chars output
            MAX_BATCH_TICKETS = 200

            # Collect small TRCs for packing
            small_trcs = []       # [(trc, n), ...]
            small_total = 0

            for row in trc_counts:
                trc, n = row['trc'], row['n']
                if trc_filter and trc not in trc_filter:
                    continue

                total_tickets += n

                if n > MAX_BATCH_TICKETS:
                    # Large TRC → chunk into multiple batches
                    n_chunks = -(-n // MAX_BATCH_TICKETS)  # ceiling div
                    for c in range(1, n_chunks + 1):
                        batches.append(self._make_batch(
                            scan_id, batch_num, trc, c, n_chunks,
                            parallel_workers))
                        batch_num += 1
                elif n > small_threshold:
                    # Medium TRC → own batch (already ≤ MAX_BATCH_TICKETS)
                    batches.append(self._make_batch(
                        scan_id, batch_num, trc, 1, 1, parallel_workers))
                    batch_num += 1
                else:
                    # Small TRC → queue for packing
                    small_trcs.append((trc, n))
                    small_total += n

            # Include untagged tickets if any exist and no filter
            untagged_n = untagged_row['n'] if untagged_row else 0
            if untagged_n > 0 and not trc_filter:
                total_tickets += untagged_n
                if untagged_n > MAX_BATCH_TICKETS:
                    n_chunks = -(-untagged_n // MAX_BATCH_TICKETS)
                    for c in range(1, n_chunks + 1):
                        batches.append(self._make_batch(
                            scan_id, batch_num, '(untagged)', c, n_chunks,
                            parallel_workers))
                        batch_num += 1
                elif untagged_n > small_threshold:
                    batches.append(self._make_batch(
                        scan_id, batch_num, '(untagged)', 1, 1,
                        parallel_workers))
                    batch_num += 1
                else:
                    small_trcs.append(('(untagged)', untagged_n))
                    small_total += untagged_n

            # Pack small TRCs into mixed batches.
            # Cap at 500 tickets per mixed batch — Gemini needs to
            # generate one JSON object per ticket (~950 chars each), and
            # Gemini 2.5 Flash has ~65K output token limit (~260K chars).
            # 200 tickets × 950 chars ≈ 190K chars of output — safely
            # within limits. 500 was too high — caused output truncation.
            MIXED_BATCH_CAP = min(batch_size, 200)

            if small_trcs:
                current_pack = []
                current_count = 0
                for trc, n in small_trcs:
                    if current_count + n > MIXED_BATCH_CAP and current_pack:
                        # Flush current pack as a mixed batch
                        trc_list = [t for t, _ in current_pack]
                        batches.append(self._make_batch(
                            scan_id, batch_num,
                            json.dumps(trc_list),  # JSON array of TRC codes
                            1, 1, parallel_workers))
                        batch_num += 1
                        current_pack = []
                        current_count = 0
                    current_pack.append((trc, n))
                    current_count += n

                # Flush remaining
                if current_pack:
                    if len(current_pack) == 1:
                        # Single TRC → store as plain string
                        trc_list_str = current_pack[0][0]
                    else:
                        trc_list_str = json.dumps(
                            [t for t, _ in current_pack])
                    batches.append(self._make_batch(
                        scan_id, batch_num, trc_list_str,
                        1, 1, parallel_workers))
                    batch_num += 1

            if not batches:
                conn.close()
                return {
                    'error': 'No tickets found in date range',
                    'total_tickets': total_tickets,
                }

            # ── HARD CAP: CLI mode ──
            if total_tickets > MAX_TICKETS_CLI:
                conn.close()
                return {
                    'error': (f'Ticket count ({total_tickets:,}) exceeds '
                              f'CLI mode limit ({MAX_TICKETS_CLI:,}). '
                              f'Use a narrower date range or TRC filter.'),
                    'total_tickets': total_tickets,
                    'max_tickets': MAX_TICKETS_CLI,
                }

            # ── Estimate cost + time ──
            est_input = total_tickets * 600
            est_output = total_tickets * 200
            est_cost = ((est_input / 1e6) * 0.15
                        + (est_output / 1e6) * 0.60)
            est_seconds_per_batch = 90
            est_time_min = (len(batches) * est_seconds_per_batch
                            / max(parallel_workers, 1)) / 60

            # ── Create scan record ──
            now = datetime.utcnow().isoformat()
            conn.execute("""
                INSERT INTO nlp_scan_runs (
                    scan_id, created_at, status, date_range_start,
                    date_range_end, trc_filter, mode, batch_strategy,
                    total_batches, total_tickets, estimated_cost_usd,
                    actual_cost_usd, budget_cap_usd, config_snapshot
                ) VALUES (?, ?, 'running', ?, ?, ?, ?, 'trc',
                          ?, ?, ?, 0.0, ?, ?)
            """, (scan_id, now, date_start, date_end,
                  json.dumps(trc_filter) if trc_filter else None,
                  mode, len(batches), total_tickets, est_cost, budget_cap,
                  json.dumps({
                      'mode': 'cli',
                      'batch_size': batch_size,
                      'parallel_workers': parallel_workers,
                  })))

            # ── Insert batch records ──
            for b in batches:
                conn.execute("""
                    INSERT INTO nlp_batches (
                        batch_id, scan_id, batch_number, trc,
                        trc_chunk, trc_chunk_total, status,
                        worker_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """, (b['batch_id'], scan_id, b['batch_number'],
                      b['trc'], b['trc_chunk'], b['trc_chunk_total'],
                      b['worker_id'], now))

            conn.commit()
        finally:
            conn.close()

        # ── Launch worker(s) ──
        actual_workers = min(parallel_workers, 3)  # cap at 3
        for w in range(actual_workers):
            self._launch_worker(scan_id, w)

        return {
            'scan_id': scan_id,
            'total_batches': len(batches),
            'total_tickets': total_tickets,
            'estimated_cost': round(est_cost, 4),
            'estimated_time_minutes': round(est_time_min, 1),
            'workers_launched': actual_workers,
        }

    def _make_batch(self, scan_id, batch_num, trc, chunk,
                    chunk_total, n_workers):
        return {
            'batch_id': str(uuid.uuid4()),
            'batch_number': batch_num,
            'trc': trc,
            'trc_chunk': chunk,
            'trc_chunk_total': chunk_total,
            'worker_id': batch_num % max(n_workers, 1),
        }

    def _launch_worker(self, scan_id, worker_id):
        """Launch scan_worker.py as detached subprocess."""
        cmd = [
            sys.executable, '-m', 'src.data.scan_worker',
            '--db', self.db_path,
            '--scan', scan_id,
            '--worker', str(worker_id),
        ]

        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == 'win32':
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **kwargs)
            logger.info(f"Launched worker {worker_id} for scan "
                        f"{scan_id[:8]}... (PID {proc.pid})")
        except Exception as e:
            logger.error(f"Failed to launch worker {worker_id}: {e}")

    def get_status(self, scan_id):
        """Read scan progress directly from SQLite."""
        conn = self._get_conn()
        scan = conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?",
            (scan_id,)).fetchone()
        if not scan:
            conn.close()
            return {'error': 'scan not found'}

        result = dict(scan)

        # Get current batch info
        running = conn.execute("""
            SELECT trc, trc_chunk, trc_chunk_total FROM nlp_batches
            WHERE scan_id = ? AND status IN ('running', 'claimed')
            ORDER BY batch_number ASC LIMIT 1
        """, (scan_id,)).fetchone()
        if running:
            result['current_batch_trc'] = running['trc']
            result['current_chunk'] = (f"{running['trc_chunk']}/"
                                       f"{running['trc_chunk_total']}")

        conn.close()
        return result

    def pause_scan(self, scan_id):
        """Set status to paused. Workers read this on next loop."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE nlp_scan_runs SET status = 'paused' "
            "WHERE scan_id = ?", (scan_id,))
        conn.commit()
        conn.close()
        return {'status': 'paused'}

    def resume_scan(self, scan_id, budget_cap=None):
        """Set status to running, re-queue failed batches, relaunch workers."""
        conn = self._get_conn()
        scan = conn.execute(
            "SELECT * FROM nlp_scan_runs WHERE scan_id = ?",
            (scan_id,)).fetchone()
        if not scan:
            conn.close()
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
            f"UPDATE nlp_scan_runs SET {updates} "
            f"WHERE scan_id = ?", params)

        # Re-queue any failed batches so workers can retry them
        requeued = conn.execute("""
            UPDATE nlp_batches SET status = 'queued'
            WHERE scan_id = ? AND status = 'failed'
        """, (scan_id,)).rowcount

        # Also reset any stuck 'running'/'claimed' batches
        # (workers that died without finishing)
        reset = conn.execute("""
            UPDATE nlp_batches SET status = 'queued'
            WHERE scan_id = ? AND status IN ('running', 'claimed')
        """, (scan_id,)).rowcount

        conn.commit()
        conn.close()

        if requeued or reset:
            logger.info(f"Resume: re-queued {requeued} failed + "
                        f"{reset} stuck batches for scan {scan_id[:8]}")

        for w in range(min(n_workers, 3)):
            self._launch_worker(scan_id, w)

        return {'status': 'running', 'requeued_batches': requeued + reset}

    def cancel_scan(self, scan_id):
        """Cancel scan. Workers exit on next loop."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE nlp_scan_runs SET status = 'cancelled' "
            "WHERE scan_id = ?", (scan_id,))
        conn.commit()
        conn.close()
        return {'status': 'cancelled'}

    def get_history(self):
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT scan_id, created_at, status, date_range_start,
                   date_range_end, total_batches, completed_batches,
                   total_tickets, actual_cost_usd, estimated_cost_usd
            FROM nlp_scan_runs ORDER BY created_at DESC LIMIT 20
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn
