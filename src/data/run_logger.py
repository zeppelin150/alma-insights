"""
Alma Insights — Run Logger
Timestamped, structured logging for preflight + ingestion runs.
Outputs: JSONL file (machine), TXT summary (human), Qt signal (in-app live view).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from PySide6.QtCore import QObject, Signal


LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"


class RunLogger(QObject):
    """
    Structured run logger.

    Emits `log_event(str)` signal for live in-app display.
    Writes JSONL + TXT to data/logs/<run_id>/.
    """

    # Signal emitted for every log event (carries formatted text line)
    log_event = Signal(str)

    def __init__(self, dataset_name: str = "", run_type: str = "ingestion"):
        super().__init__()
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.dataset_name = dataset_name
        self.run_type = run_type
        self.start_time = datetime.now(timezone.utc)

        self._events: List[dict] = []
        self._warnings: List[str] = []
        self._errors: List[str] = []

        # Counters
        self.rows_received = 0
        self.rows_written = 0
        self.pages_fetched = 0
        self.chunks_completed = 0
        self.conversations_rebuilt = 0
        self.retries = 0

        # File handles
        self._run_dir: Optional[Path] = None
        self._jsonl_fh = None
        self._setup_files()

    def _setup_files(self):
        """Create log directory and open JSONL file."""
        try:
            self._run_dir = LOG_DIR / self.run_id
            self._run_dir.mkdir(parents=True, exist_ok=True)
            self._jsonl_fh = open(self._run_dir / "events.jsonl", "a", encoding="utf-8")
        except OSError:
            self._run_dir = None
            self._jsonl_fh = None

    # ═══════════════════════════════════
    #  PUBLIC LOGGING API
    # ═══════════════════════════════════

    def log(self, phase: str, action: str, status: str = "OK", detail: str = "",
            http_method: str = "", url_path: str = "", response_code: int = 0,
            attempt: int = 0, backoff_ms: int = 0, retry_after_s: float = 0,
            chunk_id: str = "", date_start: str = "", date_end: str = "",
            total_results: int = 0, rows_received: int = 0, rows_written: int = 0,
            extra: dict = None):
        """Log a structured event."""
        ts = datetime.now(timezone.utc).isoformat()

        event = {
            "ts": ts,
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "phase": phase,
            "action": action,
            "status": status,
            "detail": detail,
        }

        # Optional fields (only include if non-default)
        if http_method:
            event["http_method"] = http_method
        if url_path:
            event["url_path"] = url_path
        if response_code:
            event["response_code"] = response_code
        if attempt:
            event["attempt"] = attempt
        if backoff_ms:
            event["backoff_ms"] = backoff_ms
        if retry_after_s:
            event["retry_after_s"] = retry_after_s
        if chunk_id:
            event["chunk_id"] = chunk_id
        if date_start:
            event["date_start"] = date_start
        if date_end:
            event["date_end"] = date_end
        if total_results:
            event["total_results"] = total_results
        if rows_received:
            event["rows_received"] = rows_received
        if rows_written:
            event["rows_written"] = rows_written
        if extra:
            event.update(extra)

        # Track warnings/errors
        if status == "WARN":
            self._warnings.append(detail)
        elif status == "FAIL":
            self._errors.append(detail)

        self._events.append(event)

        # Write to JSONL file
        if self._jsonl_fh:
            try:
                self._jsonl_fh.write(json.dumps(event) + "\n")
                self._jsonl_fh.flush()
            except OSError:
                pass

        # Emit for in-app display
        display = self._format_display(event)
        self.log_event.emit(display)

    def start(self, detail: str = ""):
        self.log("START", "run_begin", detail=detail or f"Starting {self.run_type} run")

    def end(self, status: str = "OK", detail: str = ""):
        self.log("END", "run_end", status=status, detail=detail or "Run complete")
        self._write_summary()
        self._close()

    def preflight(self, action: str, status: str = "OK", detail: str = "", **kw):
        self.log("PREFLIGHT", action, status=status, detail=detail, **kw)

    def request(self, action: str, **kw):
        self.log("REQUEST", action, **kw)

    def backoff(self, action: str, attempt: int, backoff_ms: int,
                retry_after_s: float = 0, **kw):
        self.retries += 1
        self.log("BACKOFF", action, status="WARN", attempt=attempt,
                 backoff_ms=backoff_ms, retry_after_s=retry_after_s, **kw)

    def page(self, page_num: int, rows: int, **kw):
        self.pages_fetched += 1
        self.rows_received += rows
        self.log("PAGE", f"page_{page_num}", rows_received=rows,
                 detail=f"Page {page_num}: {rows} rows", **kw)

    def db(self, action: str, rows_written: int = 0, **kw):
        self.rows_written += rows_written
        self.log("DB", action, rows_written=rows_written, **kw)

    def rebuild(self, action: str, **kw):
        self.log("REBUILD", action, **kw)

    def error(self, action: str, detail: str, **kw):
        self.log("ERROR", action, status="FAIL", detail=detail, **kw)

    def warn(self, action: str, detail: str, **kw):
        self.log("WARN", action, status="WARN", detail=detail, **kw)

    # ═══════════════════════════════════
    #  SUMMARY + FILE OUTPUT
    # ═══════════════════════════════════

    def _write_summary(self):
        """Write human-readable TXT summary."""
        if not self._run_dir:
            return

        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        lines = [
            "═" * 60,
            f"  ALMA INSIGHTS — {self.run_type.upper()} RUN SUMMARY",
            "═" * 60,
            f"  Run ID:            {self.run_id}",
            f"  Dataset:           {self.dataset_name}",
            f"  Started:           {self.start_time.isoformat()}",
            f"  Duration:          {elapsed:.1f}s",
            f"  Status:            {'FAILED' if self._errors else 'SUCCESS'}",
            "",
            f"  Rows received:     {self.rows_received:,}",
            f"  Rows written:      {self.rows_written:,}",
            f"  Pages fetched:     {self.pages_fetched}",
            f"  Chunks completed:  {self.chunks_completed}",
            f"  Conversations:     {self.conversations_rebuilt:,}",
            f"  Retries:           {self.retries}",
            "",
        ]

        if self._warnings:
            lines.append(f"  Warnings ({len(self._warnings)}):")
            for w in self._warnings[:20]:
                lines.append(f"    ⚠ {w}")
            if len(self._warnings) > 20:
                lines.append(f"    ... and {len(self._warnings) - 20} more")
            lines.append("")

        if self._errors:
            lines.append(f"  Errors ({len(self._errors)}):")
            for e in self._errors[:20]:
                lines.append(f"    ✗ {e}")
            lines.append("")

        lines.append("═" * 60)

        try:
            (self._run_dir / "summary.txt").write_text(
                "\n".join(lines), encoding="utf-8"
            )
        except OSError:
            pass

    def _format_display(self, event: dict) -> str:
        """Format event for in-app live log display."""
        ts = event["ts"][11:19]  # HH:MM:SS
        phase = event["phase"]
        status = event["status"]
        detail = event.get("detail", "")

        # Status indicator
        icon = "✓" if status == "OK" else "⚠" if status == "WARN" else "✗"
        return f"[{ts}] {icon} {phase:<12s} {detail}"

    def _close(self):
        if self._jsonl_fh:
            try:
                self._jsonl_fh.close()
            except OSError:
                pass
            self._jsonl_fh = None

    @property
    def log_dir(self) -> Optional[Path]:
        return self._run_dir

    def get_display_lines(self) -> List[str]:
        """Return all formatted display lines so far."""
        return [self._format_display(e) for e in self._events]
