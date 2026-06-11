"""Enablement monitor orchestrator — owns the per-source connectors and gives the
UI one signal + one manual trigger.

The Qt page connects to ``changed``; the operator's "Scan now" calls
``scan_all()``, which runs each connector's module-level ``poll_once(conn)`` once
(out of band of the background timers) and emits ``changed`` so the four pages
refresh. Background polling is just each sub-monitor's own QTimer.

``run_monitor_now`` (the chat tool) calls the SAME module-level ``poll_once``
functions directly — it runs in the separate MCP-server process and cannot reach
this Qt object — so the timer, the tool, and tests all share one code path.
"""

from __future__ import annotations

import logging
from threading import Thread

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("alma.enablement_monitor")


class EnablementMonitor(QObject):
    """Composes the Asana + Drive monitors behind one refresh signal."""

    changed = Signal()          # something was created/updated → refresh the UI
    status = Signal(str)

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        from src.data.asana_monitor import AsanaMonitor
        from src.data.drive_monitor import DriveMonitor
        self.asana = AsanaMonitor(db_manager, self)
        self.drive = DriveMonitor(db_manager, self)
        # fold each sub-monitor's background activity into one refresh signal
        self.asana.tasks_created.connect(lambda _rows: self.changed.emit())
        self.drive.documents_indexed.connect(lambda _rows: self.changed.emit())

    def start(self, interval_seconds: int = 300):
        """Start each sub-monitor's background poll IFF its source is configured."""
        try:
            from src.data import asana_setup
            if asana_setup.is_asana_connected():
                self.asana.start(interval_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.debug("asana monitor start skipped: %s", exc)
        try:
            from src.data.drive_reader import DriveReader
            if DriveReader.from_settings().is_configured():
                self.drive.start(interval_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.debug("drive monitor start skipped: %s", exc)

    def stop(self):
        for mon in (self.asana, self.drive):
            try:
                mon.stop()
            except Exception:  # noqa: BLE001
                pass

    def scan_all(self):
        """Manual one-off poll of every source (off the UI thread); emits changed."""
        Thread(target=self._scan_all_worker, daemon=True).start()

    def _scan_all_worker(self):
        try:
            from src.data import asana_monitor, drive_monitor
            from src.data.connection_factory import get_connection
            conn = get_connection(str(self.db.db_path))
            try:
                asana_monitor.poll_once(conn)
                drive_monitor.poll_once(conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan_all failed: %s", exc)
        # Always refresh — the queued signal hops back to the UI thread.
        self.changed.emit()
