"""Run Gemini NLP scan against data/phase3_gate_test.db with 12 workers.

Modeled on tests/run_e2e_full_scan.py but scoped to the cached gate DB.
Kills scan_server / alma_mcp_server subprocesses on exit so we don't
leave zombies (per CLAUDE.md danger zone #6).

On completion, logs a classification summary for downstream composite-
embedding validation.
"""

from __future__ import annotations

import atexit
import logging
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

DB_PATH = str(_ROOT / "data" / "phase3_gate_test.db")
POLL_INTERVAL = 10      # seconds
MAX_WAIT = 3600         # 60 min hard stop (1788 tickets ~= 30 min expected)

log = logging.getLogger("nlp_gate_runner")


def _kill_zombies():
    """Terminate lingering scan_server / Gemini CLI subprocesses."""
    patterns = [
        "alma_mcp_server",
        "scan_server",
        "gemini",   # Gemini CLI wrapper
    ]
    log.info("Cleanup: terminating lingering subprocesses")
    for p in patterns:
        try:
            subprocess.run(
                ["wmic", "process", "where",
                 f"commandline like '%%{p}%%'", "call", "terminate"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as exc:   # noqa: BLE001
            log.warning("cleanup of %s: %s", p, exc)
    # Bridge node.exe processes
    try:
        subprocess.run(
            ["wmic", "process", "where",
             "commandline like '%%scan_server/server.js%%'",
             "call", "terminate"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _pre_scan_summary() -> dict:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    out = {
        "tickets": c.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0],
        "conversations": c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
        "classifications": c.execute(
            "SELECT COUNT(*) FROM nlp_ticket_classifications"
        ).fetchone()[0],
        "date_range": c.execute(
            "SELECT MIN(ticket_created_date), MAX(ticket_created_date) FROM ticket_index"
        ).fetchone(),
    }
    c.close()
    return out


def _post_scan_summary(scan_id: str) -> dict:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    out = {}
    run = c.execute(
        "SELECT * FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,),
    ).fetchone()
    out["status"] = run["status"] if run else "UNKNOWN"
    out["total_batches"] = run["total_batches"] if run else 0
    out["completed_batches"] = run["completed_batches"] if run else 0
    out["classifications"] = c.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()[0]
    out["distinct_trc"] = c.execute(
        "SELECT COUNT(DISTINCT trc) FROM nlp_ticket_classifications "
        "WHERE scan_id = ? AND trc IS NOT NULL",
        (scan_id,),
    ).fetchone()[0]
    out["distinct_sub_cluster"] = c.execute(
        "SELECT COUNT(DISTINCT sub_cluster) FROM nlp_ticket_classifications "
        "WHERE scan_id = ? AND sub_cluster IS NOT NULL",
        (scan_id,),
    ).fetchone()[0]
    out["distinct_friction_type"] = c.execute(
        "SELECT COUNT(DISTINCT friction_type) FROM nlp_ticket_classifications "
        "WHERE scan_id = ? AND friction_type IS NOT NULL",
        (scan_id,),
    ).fetchone()[0]
    # Top 10 sub_clusters by volume
    top = c.execute(
        """SELECT sub_cluster, COUNT(*) as n
             FROM nlp_ticket_classifications
            WHERE scan_id = ? AND sub_cluster IS NOT NULL
            GROUP BY sub_cluster
            ORDER BY n DESC LIMIT 10""",
        (scan_id,),
    ).fetchall()
    out["top_sub_clusters"] = [(r["sub_cluster"], r["n"]) for r in top]
    c.close()
    return out


def main() -> int:
    _setup_logging()
    # Ensure cleanup fires on any exit path
    atexit.register(_kill_zombies)
    signal.signal(signal.SIGINT, lambda *_: (_kill_zombies(), sys.exit(130)))

    log.info("=" * 60)
    log.info("NLP SCAN — gate DB, 12 workers")
    log.info("  DB: %s", DB_PATH)
    log.info("  Time: %s", datetime.now().isoformat())
    log.info("=" * 60)

    pre = _pre_scan_summary()
    for k, v in pre.items():
        log.info("  pre.%s = %s", k, v)

    # Ensure migrations are applied to gate DB
    from src.data.db_manager import DatabaseManager
    dm = DatabaseManager(Path(DB_PATH))
    dm.initialize()
    dm.close()

    log.info("Starting ScanOrchestrator (12 workers, $50 budget cap)")
    from src.agents.scan_orchestrator import ScanOrchestrator
    orch = ScanOrchestrator(DB_PATH)
    t0 = time.time()
    result = orch.start_scan(
        date_start="2025-01-01",
        date_end="2025-04-16",
        budget_cap=50.0,
        parallel_workers=12,
    )
    log.info("Scan initiated in %.1fs  result=%s",
             time.time() - t0,
             {k: v for k, v in result.items() if k != "batches"})

    scan_id = result.get("scan_id")
    if not scan_id:
        log.error("No scan_id returned — abort")
        return 3

    # Poll loop
    log.info("Polling every %ds (max %d min)", POLL_INTERVAL, MAX_WAIT // 60)
    last_status = ""
    last_n = 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    while (time.time() - t0) < MAX_WAIT:
        run = conn.execute(
            "SELECT status, completed_batches, total_batches "
            "FROM nlp_scan_runs WHERE scan_id = ?", (scan_id,),
        ).fetchone()
        if not run:
            log.error("scan_id not in DB")
            conn.close()
            return 4
        n = conn.execute(
            "SELECT COUNT(*) as n FROM nlp_ticket_classifications WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()["n"]
        status = run["status"]
        elapsed = time.time() - t0
        rate = n / elapsed if elapsed else 0.0
        if status != last_status or n != last_n:
            total = run["total_batches"] or 0
            done = run["completed_batches"] or 0
            pct = (100.0 * done / total) if total else 0.0
            log.info(
                "[%5.0fs] %-20s %d/%d batches (%.1f%%)  %d classified  %.1f cls/s",
                elapsed, status, done, total, pct, n, rate,
            )
            last_status, last_n = status, n
        if status in ("analysis_complete", "completed", "failed", "cancelled"):
            break
        time.sleep(POLL_INTERVAL)
    else:
        log.warning("Scan did not complete within %ds", MAX_WAIT)
    conn.close()

    total_elapsed = time.time() - t0
    log.info("=" * 60)
    log.info("SCAN DONE in %.1fs (%.1f min)", total_elapsed, total_elapsed / 60)
    log.info("=" * 60)

    post = _post_scan_summary(scan_id)
    for k, v in post.items():
        log.info("  post.%s = %s", k, v)

    return 0 if post["status"] in ("analysis_complete", "completed") else 1


if __name__ == "__main__":
    rc = main()
    _kill_zombies()
    sys.exit(rc)
