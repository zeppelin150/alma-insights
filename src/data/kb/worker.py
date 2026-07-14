"""KBWorker — the main-process KB tick (WS2-M3, renn-calendar-kb-studio).

STARTS UNCONDITIONALLY when the KB is enabled and checks
``google_oauth.is_active()`` PER TICK (the pre-mortem blocker fix: OAuth is
disable-on-launch, so a start-time gate can NEVER be true at monitor wiring —
the DriveMonitor precedent). A tick where Google is disconnected marks
``skipped`` and no-ops; the tick after the operator Reconnects picks the
whole backlog up (bounded — queue coalescing + push limits).

Tick body: reset stale claim leases → push queued card writes → pull every EC
folder → every Nth tick, full reconcile (+ _index.md head files).

SourceMonitor discipline copied from AsanaMonitor: QTimer on the Qt thread,
single-flight latch, daemon worker thread, FRESH connection per run, never
atomic().
"""

from __future__ import annotations

import logging
from threading import Thread

logger = logging.getLogger("alma.kb.worker")

_RECONCILE_EVERY = 5   # full reconcile every Nth tick


def kb_enabled() -> bool:
    try:
        from src.data.settings_manager import get_section
        en = get_section("enablement", {}) or {}
        if en.get("demo_mode", True):
            return False
        return bool((en.get("kb") or {}).get("enabled", False))
    except Exception:  # noqa: BLE001
        return False


def tick_once(conn, *, reader=None, exporter=None, do_reconcile: bool = False) -> dict:
    """One KB maintenance pass (shared by the worker, tests, and manual scans)."""
    from src.data import google_oauth
    from src.data.kb import drive_kb, sync
    try:
        active = google_oauth.is_active()
    except Exception:  # noqa: BLE001 — subprocess guard etc.
        active = False
    if not active:
        root = drive_kb.ec_root_id(conn)
        if root:
            sync._mark_sync(conn, root, status="skipped: Google not connected")
        return {"skipped": True, "reason": "google_not_connected"}
    if reader is None:
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
    results = {"skipped": False}
    results["leases_reset"] = sync.reset_stale_claims(conn)
    results["expired"] = sync.expire_stale_pending(conn)
    results["index_jobs"] = sync.drain_index_jobs(conn, reader=reader,
                                                  exporter=exporter)
    try:
        results["pushed"] = sync.push_pending(conn, exporter=exporter, reader=reader)
    except Exception as exc:  # noqa: BLE001 — incl. KBWriteDenied: log loudly, keep pulling
        logger.warning("KB push failed: %s", exc)
        results["pushed"] = 0
    results["pulled"] = sync.pull_all(conn, reader=reader)
    results["distilled"] = sync.drain_distill_jobs(conn, exporter=exporter)
    if do_reconcile:
        try:
            results["reconcile"] = sync.full_reconcile(conn, reader=reader,
                                                       exporter=exporter)
        except Exception as exc:  # noqa: BLE001
            logger.debug("KB reconcile failed: %s", exc)
        try:
            results["staleness"] = sync.scan_stale_sources(conn, reader=reader,
                                                           exporter=exporter)
        except Exception as exc:  # noqa: BLE001
            logger.debug("KB staleness scan failed: %s", exc)
    return results


class KBWorker:
    """Timer-driven KB tick, composed into EnablementMonitor (no new
    main_window wiring). Not a QObject — no signals needed; the pages read
    the mirror on their normal refresh."""

    def __init__(self, db_manager):
        from PySide6.QtCore import QTimer
        self.db = db_manager
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_tick)
        self._running = False
        self._ticks = 0

    def start(self, interval_seconds: int = 300):
        self._timer.start(max(60, int(interval_seconds)) * 1000)
        self._on_tick()

    def stop(self):
        self._timer.stop()

    def scan_now(self):
        self._on_tick()

    def _on_tick(self):
        if self._running:
            return
        self._running = True
        Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(str(self.db.db_path))
            try:
                self._ticks += 1
                tick_once(conn,
                          do_reconcile=(self._ticks % _RECONCILE_EVERY == 1))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("KB tick failed: %s", exc)
        finally:
            self._running = False
