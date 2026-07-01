"""Google Drive ingest monitor — the MVP hero flow's Extract step.

For each enabled Drive source in monitor_sources, list files changed since the
cursor, export their text into the local store, draft a Guru card from each, and
surface a card_review task (review-then-publish: Drive never auto-pushes). The
body is the module-level poll_once(conn) so the Qt monitor, the run_monitor_now
chat tool (separate process), and tests share one code path.

Build/test against a FakeDriveReader now; goes live when the service account has
drive.readonly and the watched folders are shared (an org step). When not yet
configured the poll is a no-op marked 'skipped'.
"""

from __future__ import annotations

import logging
from threading import Thread

from PySide6.QtCore import QTimer, Signal

from src.data import enablement_sources as sources
from src.data.source_types import SourceMonitor

logger = logging.getLogger("alma.drive_monitor")
_DEFAULT_INTERVAL = 300


def poll_once(conn, *, reader=None, llm_client=None) -> dict:
    """Pull changed docs from every enabled Drive source → index → draft → task."""
    drive_sources = [s for s in sources.list_sources(conn, "drive") if s.get("enabled", True)]
    if not drive_sources:
        return {"documents": [], "drafts": [], "tasks": []}

    if reader is None:
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
        if not reader.is_configured():
            for s in drive_sources:
                sources.mark_source(conn, s["source_id"], status="skipped")
            return {"documents": [], "drafts": [], "tasks": [], "skipped": True}

    if llm_client is None:
        from src.gemini.client_factory import build_client_for_task
        llm_client = build_client_for_task("enablement_card_gen")

    from src.data import enablement_store as store
    from src.data import enablement_tasks as tasks

    docs, drafts, task_ids = [], [], []
    for s in drive_sources:
        cfg = s.get("config") or {}
        folder_id = cfg.get("folder_id")
        if not folder_id:
            continue
        watermark = s.get("cursor")
        try:
            files = reader.list_changed_files(
                folder_id, modified_after=watermark,
                recursive=cfg.get("recursive", True), mime_types=cfg.get("mime_types"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Drive source %s poll failed: %s", s["source_id"], exc)
            sources.mark_source(conn, s["source_id"], status="error", error=str(exc))
            continue
        newest = watermark
        for f in files:
            mt = f.get("modifiedTime") or ""
            if mt and (not newest or mt > newest):
                newest = mt
            text = ""
            try:
                text = reader.export_text(f["id"], f.get("mimeType", ""))
            except Exception as exc:  # noqa: BLE001
                logger.debug("export_text failed for %s: %s", f.get("id"), exc)
            doc_id = store.save_document(
                conn, source="drive", doc_id=f.get("id"), name=f.get("name", ""),
                source_ref=f.get("id"), mime_type=f.get("mimeType", ""),
                web_url=f.get("webViewLink", ""), modified_time=mt, full_text=text)
            docs.append(doc_id)
            draft = store.draft_card_from_document(
                conn, doc_id, llm_client, collection="Provider Enablement")
            drafts.append(draft["id"])
            tid = tasks.create_task(
                conn, source="drive", kind="card_review",
                title=f"Review card: {f.get('name', 'document')}",
                source_ref=f.get("id"), source_url=f.get("webViewLink"),
                draft_id=draft["id"], created_by="agent")
            task_ids.append(tid)
        sources.mark_source(conn, s["source_id"], status="ok", cursor=newest)
    return {"documents": docs, "drafts": drafts, "tasks": task_ids}


class DriveMonitor(SourceMonitor):
    """Polls watched Drive folders and emits the indexed document ids."""

    documents_indexed = Signal(list)

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._status = "paused"
        self._fetching = False
        self._interval = _DEFAULT_INTERVAL

    @property
    def source_name(self) -> str:
        return "drive"

    @property
    def status(self) -> str:
        return self._status

    def start(self, interval_seconds: int = _DEFAULT_INTERVAL):
        self._interval = max(60, interval_seconds)
        self._status = "live"
        self.status_changed.emit("live")
        self._timer.start(self._interval * 1000)
        self._on_tick()

    def stop(self):
        self._timer.stop()
        self._status = "paused"
        self.status_changed.emit("paused")

    def scan_now(self):
        self._on_tick()

    def _on_tick(self):
        if self._fetching:
            return
        self._fetching = True
        Thread(target=self._do_fetch, daemon=True).start()

    def _do_fetch(self):
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(str(self.db.db_path))
            try:
                result = poll_once(conn)
            finally:
                conn.close()
            if result.get("documents"):
                self.documents_indexed.emit(result["documents"])
                self.records_received.emit(result["documents"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Drive fetch failed: %s", exc)
        finally:
            self._fetching = False


# ── task-source registration (M4) ────────────────────────────────────
from src.data import task_sources as _task_sources  # noqa: E402

_task_sources.register(_task_sources.TaskSourceSpec(
    name="drive", display_name="Google Drive", kind="ingest",
    poll=lambda conn, **kw: poll_once(conn, **kw),
))
