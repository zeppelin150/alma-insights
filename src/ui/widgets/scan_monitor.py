"""
Alma Insights -- Live Scan Monitor Widget (Pass 5.1)

Provides real-time scan visibility during NLP classification:
  - Sprout animation with live percentage counter
  - Preflight checklist + execution log
  - Per-batch progress rows
  - Scan runtime timer + time estimates
  - Bridge health / agent activity / token streaming columns

The monitor is hidden until a scan starts, then shown with animation.
It polls scan_events + scan_progress tables every 3 seconds.
"""

import json
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QProgressBar, QPlainTextEdit, QScrollArea, QStackedWidget,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID,
    ALMA_TEXT_LIGHT, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
    ALMA_BG_ELEVATED, apply_card_shadow_soft,
)
from src.ui.widgets.sprout_animation import SproutAnimation

logger = logging.getLogger("alma.scan_monitor")

# ── Event status emoji mapping ──
STATUS_EMOJI = {
    "pending": "\u23f3",     # hourglass
    "running": "\U0001f535",  # blue circle
    "complete": "\u2705",     # green check
    "error": "\u274c",        # red X
    "warning": "\u26a0\ufe0f",  # warning
}


class ScanStatusPanel(QWidget):
    """Two-state panel: idle (estimates) vs running (3-column live status)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        # ── Idle state: estimates ──
        self._idle_widget = QWidget()
        idle_layout = QVBoxLayout(self._idle_widget)
        idle_layout.setContentsMargins(0, 0, 0, 0)
        idle_layout.setSpacing(6)

        self._est_title = QLabel("Scan Estimates")
        self._est_title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        idle_layout.addWidget(self._est_title)

        self._est_tickets = QLabel("Total tickets: --")
        self._est_time = QLabel("Est. time: --")
        self._est_tokens = QLabel("Est. tokens: --")
        self._est_cost = QLabel("Est. cost: --")
        for lbl in [self._est_tickets, self._est_time, self._est_tokens, self._est_cost]:
            lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID}; border: none;")
            idle_layout.addWidget(lbl)

        idle_layout.addStretch()
        self._stack.addWidget(self._idle_widget)

        # ── Running state: 3 columns ──
        self._running_widget = QWidget()
        running_layout = QHBoxLayout(self._running_widget)
        running_layout.setContentsMargins(0, 0, 0, 0)
        running_layout.setSpacing(16)

        # Column 1: Bridge Health
        self._bridge_lbl = QLabel("Bridge: --")
        self._bridge_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        self._bridge_lbl.setWordWrap(True)
        running_layout.addWidget(self._bridge_lbl, 1)

        # Column 2: Agent Activity
        self._agent_lbl = QLabel("Agents: --")
        self._agent_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        self._agent_lbl.setWordWrap(True)
        running_layout.addWidget(self._agent_lbl, 1)

        # Column 3: Token Streaming
        self._token_lbl = QLabel("Tokens: --")
        self._token_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        self._token_lbl.setWordWrap(True)
        running_layout.addWidget(self._token_lbl, 1)

        self._stack.addWidget(self._running_widget)

        # Start in idle mode
        self._stack.setCurrentIndex(0)

    def show_idle(self):
        self._stack.setCurrentIndex(0)

    def show_running(self):
        self._stack.setCurrentIndex(1)

    def set_estimates(self, tickets=0, time_min=0.0, tokens=0, cost=0.0):
        self._est_tickets.setText(f"Total tickets: {tickets:,}")
        self._est_time.setText(f"Est. time: ~{time_min:.0f} min")
        self._est_tokens.setText(f"Est. tokens: ~{tokens:,.0f}")
        self._est_cost.setText(f"Est. cost: ${cost:.2f}")

    def update_bridge(self, text):
        self._bridge_lbl.setText(text)

    def update_agents(self, text):
        self._agent_lbl.setText(text)

    def update_tokens(self, text):
        self._token_lbl.setText(text)


class BatchProgressRow(QWidget):
    """Single batch progress row: batch num, TRC list, ticket count, progress, time."""

    def __init__(self, batch_num, trc_label, total_tickets, parent=None):
        super().__init__(parent)
        self.batch_num = batch_num
        self.total_tickets = total_tickets
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        self._batch_lbl = QLabel(f"B{batch_num}")
        self._batch_lbl.setFixedWidth(30)
        self._batch_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;"
        )
        layout.addWidget(self._batch_lbl)

        self._trc_lbl = QLabel(trc_label[:35] + "..." if len(trc_label) > 35 else trc_label)
        self._trc_lbl.setFixedWidth(180)
        self._trc_lbl.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_MID}; border: none;"
        )
        layout.addWidget(self._trc_lbl)

        self._count_lbl = QLabel(f"{total_tickets} tkts")
        self._count_lbl.setFixedWidth(55)
        self._count_lbl.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        layout.addWidget(self._count_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(14)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background: {ALMA_CREAM};
                text-align: center;
                font-size: 9px;
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_DARK};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(self._progress, 1)

        self._time_lbl = QLabel("--")
        self._time_lbl.setFixedWidth(40)
        self._time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._time_lbl.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        layout.addWidget(self._time_lbl)

    def set_progress(self, pct, elapsed_text=""):
        self._progress.setValue(min(100, pct))
        if elapsed_text:
            self._time_lbl.setText(elapsed_text)

    def set_complete(self, elapsed_text=""):
        self._progress.setValue(100)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background: {ALMA_CREAM};
                text-align: center;
                font-size: 9px;
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_LIGHT};
                border-radius: 3px;
            }}
        """)
        if elapsed_text:
            self._time_lbl.setText(elapsed_text)

    def set_error(self):
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background: {ALMA_CREAM};
                text-align: center;
                font-size: 9px;
            }}
            QProgressBar::chunk {{
                background: {ALMA_ERROR};
                border-radius: 3px;
            }}
        """)


class ScanMonitorWidget(QWidget):
    """
    Live scan monitor: sprout animation, percentage counter,
    preflight/execution log, batch progress, runtime timer.

    Hidden until start_monitoring() is called.
    """

    scan_completed = Signal()  # emitted when scan finishes

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._scan_id = None
        self._scan_start_time = None
        self._last_event_count = 0
        self._batch_rows = {}  # batch_num -> BatchProgressRow

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_update)
        self._poll_timer.setInterval(3000)

        self._build_ui()
        self.setVisible(False)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Main card frame ──
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow_soft(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(12)

        title = QLabel("Live Scan Monitor")
        title.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        card_layout.addWidget(title)

        # ── Sprout + percentage section ──
        sprout_row = QHBoxLayout()
        sprout_row.setSpacing(16)

        # Sprout animation with dark background
        self._sprout_container = QFrame()
        self._sprout_container.setFixedSize(100, 130)
        self._sprout_container.setStyleSheet(f"""
            background: {ALMA_GREEN_DARK};
            border-radius: 12px; border: none;
        """)
        sprout_inner = QVBoxLayout(self._sprout_container)
        sprout_inner.setContentsMargins(10, 10, 10, 10)
        sprout_inner.setAlignment(Qt.AlignCenter)
        self._sprout = SproutAnimation(size=60)
        sprout_inner.addWidget(self._sprout, alignment=Qt.AlignCenter)
        sprout_row.addWidget(self._sprout_container)

        # Percentage + progress bar
        pct_col = QVBoxLayout()
        pct_col.setSpacing(4)

        self._pct_label = QLabel("0% scanning...")
        self._pct_label.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {ALMA_TEXT_DARK}; border: none;"
        )
        pct_col.addWidget(self._pct_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 6px;
                background: {ALMA_CREAM};
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_DARK};
                border-radius: 5px;
            }}
        """)
        pct_col.addWidget(self._progress_bar)

        self._detail_label = QLabel("0 of 0 tickets classified")
        self._detail_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        pct_col.addWidget(self._detail_label)

        pct_col.addStretch()
        sprout_row.addLayout(pct_col, 1)
        card_layout.addLayout(sprout_row)

        # ── Preflight / Execution Log ──
        log_title = QLabel("Preflight / Execution Log")
        log_title.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;"
        )
        card_layout.addWidget(log_title)

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumBlockCount(500)
        self._log_text.setFixedHeight(200)
        self._log_text.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {ALMA_WHITE};
                border: none;
                border-radius: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                color: {ALMA_TEXT_DARK};
                padding: 8px;
            }}
        """)
        card_layout.addWidget(self._log_text)

        # ── Batch Progress ──
        batch_title = QLabel("Batch Progress")
        batch_title.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK}; border: none;"
        )
        card_layout.addWidget(batch_title)

        self._batch_scroll = QScrollArea()
        self._batch_scroll.setWidgetResizable(True)
        self._batch_scroll.setFixedHeight(180)
        self._batch_scroll.setFrameShape(QFrame.NoFrame)
        self._batch_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        self._batch_container = QWidget()
        self._batch_layout = QVBoxLayout(self._batch_container)
        self._batch_layout.setContentsMargins(0, 0, 0, 0)
        self._batch_layout.setSpacing(2)
        self._batch_layout.addStretch()
        self._batch_scroll.setWidget(self._batch_container)
        card_layout.addWidget(self._batch_scroll)

        # ── Runtime footer ──
        footer = QHBoxLayout()
        self._runtime_label = QLabel("Scan runtime: 0:00")
        self._runtime_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        footer.addWidget(self._runtime_label)

        self._remaining_label = QLabel("Est. remaining: --")
        self._remaining_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        footer.addWidget(self._remaining_label, alignment=Qt.AlignRight)
        card_layout.addLayout(footer)

        layout.addWidget(card)

    # ── Public API ──

    def start_monitoring(self, scan_id):
        """Show the monitor and start polling."""
        self._scan_id = scan_id
        self._scan_start_time = datetime.now()
        self._last_event_count = 0
        self._batch_rows.clear()

        # Clear previous content
        self._log_text.clear()
        self._pct_label.setText("0% scanning...")
        self._progress_bar.setValue(0)
        self._detail_label.setText("0 of 0 tickets classified")
        self._runtime_label.setText("Scan runtime: 0:00")
        self._remaining_label.setText("Est. remaining: --")

        # Clear batch rows
        while self._batch_layout.count() > 1:  # keep stretch
            item = self._batch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Start animation
        self._sprout.start()
        self.setVisible(True)
        self._poll_timer.start()

    def stop_monitoring(self, completed: bool = False):
        """Stop polling and freeze display.

        Args:
            completed: If True, force the UI to 100% completion state
                       regardless of classified/total gap.
        """
        self._poll_timer.stop()
        self._sprout.stop()

        if completed:
            self._set_complete_state()

    def _set_complete_state(self):
        """Force the UI to show 100% completion."""
        self._pct_label.setText("100% complete")
        self._pct_label.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {ALMA_SUCCESS}; border: none;"
        )
        self._progress_bar.setValue(100)
        self._remaining_label.setText("Complete")

        # Update detail label with final classified count
        try:
            progress = self.db.get_scan_progress(self._scan_id)
            if progress:
                classified = progress.get("classified", 0)
                total = progress.get("total", 0)
                self._detail_label.setText(
                    f"{classified:,} of {total:,} tickets classified"
                )
        except Exception:
            pass

        self.scan_completed.emit()

    def set_error_state(self, message="Error"):
        """Show error state on the monitor."""
        self._pct_label.setText("Error")
        self._pct_label.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {ALMA_ERROR}; border: none;"
        )
        self._detail_label.setText(message)
        self._sprout.stop()

    # ── Polling ──

    def _poll_update(self):
        """Poll scan_events and scan_progress tables to update all components."""
        if not self._scan_id:
            return

        try:
            self._update_events()
            self._update_progress()
            self._update_runtime()
        except Exception as e:
            logger.debug(f"Poll update error: {e}")

    def _update_events(self):
        """Fetch new scan events and append to log."""
        try:
            events = self.db.get_scan_events(self._scan_id)
        except Exception:
            return

        # Only process new events
        new_events = events[self._last_event_count:]
        self._last_event_count = len(events)

        for ev in new_events:
            emoji = STATUS_EMOJI.get(ev.get("status", ""), "")
            timestamp = ev.get("timestamp", "")
            # Extract HH:MM:SS from ISO timestamp
            try:
                dt = datetime.fromisoformat(timestamp)
                if self._scan_start_time:
                    elapsed = dt - self._scan_start_time
                    mins = int(elapsed.total_seconds()) // 60
                    secs = int(elapsed.total_seconds()) % 60
                    time_str = f"{mins:02d}:{secs:02d}"
                else:
                    time_str = dt.strftime("%H:%M:%S")
            except Exception:
                time_str = "??:??"

            message = ev.get("message", "")
            duration_ms = ev.get("duration_ms")
            line = f"{emoji} {time_str}  {message}"
            if duration_ms and "(" not in message:
                line += f" ({duration_ms}ms)"

            self._log_text.appendPlainText(line)

            # Update batch progress rows from batch events
            event_type = ev.get("event_type", "")
            metadata = ev.get("metadata_json")
            if metadata and isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            elif not metadata:
                metadata = {}

            if event_type == "batch_start":
                self._add_batch_row(metadata)
            elif event_type == "batch_complete":
                self._complete_batch_row(ev, metadata)

    def _add_batch_row(self, metadata):
        """Add a batch progress row when batch starts."""
        trc = metadata.get("trc", "?")
        # Parse JSON array TRC labels for mixed batches
        if trc.startswith('['):
            try:
                trc = ", ".join(json.loads(trc))
            except (json.JSONDecodeError, TypeError):
                pass
        batch_id = metadata.get("batch_id", "")
        batch_num = len(self._batch_rows)
        total_tickets = metadata.get("ticket_count", 0)

        row = BatchProgressRow(batch_num, trc, total_tickets)
        self._batch_rows[batch_id] = row
        # Insert before the stretch
        self._batch_layout.insertWidget(
            self._batch_layout.count() - 1, row
        )

    def _complete_batch_row(self, event, metadata):
        """Update batch row when batch completes."""
        batch_id = metadata.get("batch_id", "")
        row = self._batch_rows.get(batch_id)
        if not row:
            return

        status = event.get("status", "")
        duration_ms = event.get("duration_ms", 0)
        elapsed_s = duration_ms / 1000 if duration_ms else 0
        mins = int(elapsed_s) // 60
        secs = int(elapsed_s) % 60
        time_str = f"{mins}:{secs:02d}"

        if status == "complete":
            row.set_complete(time_str)
        else:
            row.set_error()

    def _update_progress(self):
        """Update percentage and progress bar from scan_progress table."""
        try:
            progress = self.db.get_scan_progress(self._scan_id)
        except Exception:
            return

        if not progress:
            return

        classified = progress.get("classified", 0)
        total = progress.get("total", 0)
        pct = round(classified / total * 100) if total > 0 else 0
        # Cap at 99% during active scan — 100% is set by _set_complete_state()
        pct = min(pct, 99) if total > 0 and classified < total else pct

        self._pct_label.setText(f"{pct}% scanning...")
        self._progress_bar.setValue(pct)
        self._detail_label.setText(f"{classified:,} of {total:,} tickets classified")

        # Check completion
        est_remaining = progress.get("est_remaining_seconds")
        if est_remaining and est_remaining > 0:
            mins = int(est_remaining) // 60
            self._remaining_label.setText(f"Est. remaining: ~{mins} min")
        elif classified >= total and total > 0:
            self._remaining_label.setText("Complete")

        # Token info
        tokens_in = progress.get("tokens_in", 0)
        tokens_out = progress.get("tokens_out", 0)
        if tokens_in or tokens_out:
            total_tok = tokens_in + tokens_out
            # No direct way to set this unless we have the status panel ref
            # This data is handled by the parent page's status panel

    def _update_runtime(self):
        """Update runtime timer."""
        if not self._scan_start_time:
            return
        elapsed = datetime.now() - self._scan_start_time
        mins = int(elapsed.total_seconds()) // 60
        secs = int(elapsed.total_seconds()) % 60
        self._runtime_label.setText(f"Scan runtime: {mins}:{secs:02d}")

    def update_agent_status(self, health_data):
        """Update agent activity column from agent_health table data.

        Called externally by the parent page.
        """
        if not health_data:
            return

        lines = []
        for h in health_data:
            agent_id = h.get("agent_id", "?")
            status = h.get("status", "idle")
            tickets = h.get("tickets_done", 0)
            lines.append(f"{agent_id}: {status} ({tickets} done)")

        return "\n".join(lines)
