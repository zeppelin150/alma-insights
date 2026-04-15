"""Run the Gemini NLP scan against data/phase3_gate_test.db.

The gate DB already has 1788 ingested tickets with Qwen3 embeddings.
This script populates nlp_ticket_classifications by running the full
Gemini classification pipeline (ACP bridge + worker agents + analyst).

Downstream: composite embedding validation can use the populated labels
to test whether Gemini-derived sub_cluster / friction_type close the
recall gap.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

DB_PATH = str(_ROOT / "data" / "phase3_gate_test.db")


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _setup_logging()
    log = logging.getLogger("nlp_runner")
    log.info("Starting NLP scan against %s", DB_PATH)

    import sqlite3
    # Sanity check DB state
    c = sqlite3.connect(DB_PATH)
    n_tickets = c.execute("SELECT COUNT(*) FROM ticket_index").fetchone()[0]
    n_classif = c.execute("SELECT COUNT(*) FROM nlp_ticket_classifications").fetchone()[0]
    date_range = c.execute(
        "SELECT MIN(ticket_created_date), MAX(ticket_created_date) FROM ticket_index"
    ).fetchone()
    c.close()
    log.info("DB state: tickets=%d  classifications=%d  date_range=%s..%s",
             n_tickets, n_classif, date_range[0], date_range[1])

    if n_classif > 0:
        log.warning("nlp_ticket_classifications already has %d rows — scan "
                     "will append / re-classify as the orchestrator decides.",
                     n_classif)

    # Use dates slightly wider than data range, to be safe
    start_date = "2025-01-01"
    end_date = "2025-04-16"
    log.info("Filter window: %s .. %s", start_date, end_date)

    # Verify Gemini API key is configured
    try:
        from src.data.pat_store import PatStore
        store = PatStore()
        if not store.has_token("gemini_api_key"):
            log.error("No gemini_api_key in PatStore — scan cannot run.")
            log.error("Set it in the app Settings > API Keys tab first.")
            return 2
    except Exception as exc:   # noqa: BLE001
        log.warning("Could not verify API key (%s); continuing anyway", exc)

    # Kick off the scan
    from src.agents.scan_orchestrator import ScanOrchestrator
    orch = ScanOrchestrator(DB_PATH)
    t_start = time.time()
    log.info("=" * 60)
    log.info("STARTING NLP SCAN")
    log.info("=" * 60)
    result = orch.start_scan(
        date_start=start_date,
        date_end=end_date,
        budget_cap=50.0,            # max $50 hard cap
        parallel_workers=3,
    )
    elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("SCAN DONE in %.1fs (%.1f min)", elapsed, elapsed / 60)
    log.info("Result: %s", result)
    log.info("=" * 60)

    # Quick post-scan summary
    c = sqlite3.connect(DB_PATH)
    n_classif_after = c.execute(
        "SELECT COUNT(*) FROM nlp_ticket_classifications"
    ).fetchone()[0]
    n_trcs = c.execute(
        "SELECT COUNT(DISTINCT trc) FROM nlp_ticket_classifications"
    ).fetchone()[0]
    n_subclusters = c.execute(
        "SELECT COUNT(DISTINCT sub_cluster) FROM nlp_ticket_classifications "
        "WHERE sub_cluster IS NOT NULL"
    ).fetchone()[0]
    n_frictions = c.execute(
        "SELECT COUNT(DISTINCT friction_type) FROM nlp_ticket_classifications "
        "WHERE friction_type IS NOT NULL"
    ).fetchone()[0]
    c.close()

    log.info("Post-scan: %d classifications, %d distinct TRCs, "
             "%d sub_clusters, %d friction_types",
             n_classif_after, n_trcs, n_subclusters, n_frictions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
