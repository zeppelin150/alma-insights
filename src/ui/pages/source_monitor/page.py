"""
Source Monitor page shell.

Top-level QWidget that hosts the four tabs:
  1. Live Feed   — RateTab (rate-per-hour control chart, 2026-05-07 redesign)
  2. Alerts      — AlertsTab (fired Watchlist alerts + Confirm/Dismiss)
  3. Watchlist   — WatchlistTab (rule manager with severity badges)
  4. Connection  — ConnectionTab (Zendesk credentials + TRC mapping)

The shell is responsible only for:
- Owning the QTabWidget and the persistent status bar
- Wiring monitor signals through to the tabs
- Forwarding ``connection_changed`` to MainWindow

Tab-specific UI (cards, dialogs, charts) lives in the per-tab files —
keeps this file under ~280 LOC and well below the cyclomatic-complexity
budget per CLAUDE.md.

Original public API preserved for backwards compatibility:
- ``SourceMonitorPage(db_manager, parent)``
- ``set_monitor(monitor)``
- ``set_watchlist(watchlist)``
- ``connection_changed`` Signal
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.pages.source_monitor._styles import GHOST_BTN
from src.ui.pages.source_monitor.alerts_tab import AlertsTab
from src.ui.pages.source_monitor.connection_tab import ConnectionTab
from src.ui.pages.source_monitor.rate_tab import RateTab
from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
from src.ui.theme import (
    ALMA_BG_ELEVATED,
    ALMA_BORDER_LIGHT,
    ALMA_CREAM,
    ALMA_ERROR,
    ALMA_SUCCESS,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
)

logger = logging.getLogger("alma.source_monitor")

#: Tab indices — kept stable for any test or external code addressing
#: tabs by index. The Alerts tab badge is updated by index in
#: ``_on_alert_count_changed``.
TAB_LIVE_FEED = 0
TAB_ALERTS = 1
TAB_WATCHLIST = 2
TAB_CONNECTION = 3


class SourceMonitorPage(QWidget):
    """Top-level Source Monitor page.

    Owns the tab widget and the status bar; delegates per-tab logic to
    the four tab classes.
    """

    #: Re-emitted from ConnectionTab.connection_changed so MainWindow
    #: can rewire the live monitor when credentials are saved.
    connection_changed = Signal()

    def __init__(self, db_manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db_manager
        self._monitor = None
        self._watchlist = None
        # Tracks which records-arrival signal was connected so unwire
        # doesn't print a Qt warning when disconnecting the other one.
        self._records_signal_name: str | None = None

        self.setStyleSheet(f"background: {ALMA_CREAM};")
        self._build_ui()

    # ── Public API ────────────────────────────────────────────────

    def set_watchlist(self, watchlist) -> None:
        """Wire the WatchlistEngine to the Alerts and Watchlist tabs."""
        self._watchlist = watchlist
        self._alerts_tab.set_watchlist(watchlist)
        self._watchlist_tab.set_watchlist(watchlist)

    def set_monitor(self, monitor) -> None:
        """Wire the ZendeskMonitor (or any SourceMonitor) signals.

        Disconnects any previously wired monitor first so that
        re-wiring after a credential change doesn't double-fire signals.
        """
        if self._monitor is not None:
            self._unwire_monitor(self._monitor)

        self._monitor = monitor
        if monitor is None:
            return

        # Records arriving → schedule a debounced rate-chart refresh
        # (RateTab handles the debounce internally). Both the legacy
        # `tickets_received` and the source-agnostic `records_received`
        # signals exist; we subscribe to whichever the monitor provides
        # and remember it so unwire matches.
        self._records_signal_name = None
        if hasattr(monitor, "records_received"):
            monitor.records_received.connect(self._on_records_received)
            self._records_signal_name = "records_received"
        elif hasattr(monitor, "tickets_received"):
            monitor.tickets_received.connect(self._on_records_received)
            self._records_signal_name = "tickets_received"

        if hasattr(monitor, "status_changed"):
            monitor.status_changed.connect(self._on_status_changed)
        if hasattr(monitor, "alert_fired"):
            monitor.alert_fired.connect(self._on_alert_fired)

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        """Build header, status bar, and tab widget."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 0)
        outer.setSpacing(12)

        # Page header
        header = QLabel("Source Monitor")
        header.setObjectName("PageHeader")
        outer.addWidget(header)

        sub = QLabel(
            "Real-time Zendesk ticket feed with rate-baseline spike detection"
        )
        sub.setObjectName("PageSubheader")
        outer.addWidget(sub)

        # Status bar
        self._status_bar = self._build_status_bar()
        outer.addWidget(self._status_bar)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        outer.addWidget(self._tabs, 1)

        # Live Feed (Rate)
        self._rate_tab = RateTab()
        self._rate_tab.set_db(self.db)
        self._tabs.addTab(self._rate_tab, "Live Feed")

        # Alerts
        self._alerts_tab = AlertsTab()
        self._alerts_tab.alert_count_changed.connect(
            self._on_alert_count_changed
        )
        self._tabs.addTab(self._alerts_tab, "Alerts")

        # Watchlist
        self._watchlist_tab = WatchlistTab()
        self._tabs.addTab(self._watchlist_tab, "Watchlist")

        # Connection
        self._connection_tab = ConnectionTab()
        self._connection_tab.connection_changed.connect(
            self.connection_changed
        )
        self._tabs.addTab(self._connection_tab, "Connection")

    def _build_status_bar(self) -> QFrame:
        """Build the persistent connection-status bar at the top."""
        bar = QFrame()
        bar.setStyleSheet(
            f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px; padding: 6px 12px;
            }}
            """
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(20)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(
            f"font-size: 14px; color: {ALMA_TEXT_LIGHT};"
        )
        row.addWidget(self._status_dot)

        self._status_label = QLabel("Not connected")
        self._status_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};"
        )
        row.addWidget(self._status_label)

        row.addStretch()

        self._last_pull_label = QLabel("Last pull: —")
        self._last_pull_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
        )
        row.addWidget(self._last_pull_label)

        self._tickets_today_label = QLabel("Today: 0")
        self._tickets_today_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
        )
        row.addWidget(self._tickets_today_label)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setStyleSheet(GHOST_BTN)
        self._pause_btn.setCursor(Qt.PointingHandCursor)
        self._pause_btn.setFixedWidth(80)
        self._pause_btn.clicked.connect(self._on_pause_resume)
        self._pause_btn.setEnabled(False)
        row.addWidget(self._pause_btn)

        return bar

    # ── Monitor signal handlers ─────────────────────────────────

    def _on_records_received(self, _records: list) -> None:
        """Schedule a debounced rate-chart refresh; update status bar."""
        self._rate_tab.schedule_refresh()
        if self._monitor:
            today = getattr(self._monitor, "tickets_today", None)
            last_pull = getattr(self._monitor, "last_pull", "")
            if today is not None:
                self._tickets_today_label.setText(f"Today: {today}")
            if last_pull:
                self._last_pull_label.setText(f"Last pull: {last_pull}")

    def _on_status_changed(self, status: str) -> None:
        """Update status indicator color/label and Pause button state."""
        colors = {
            "live": ALMA_SUCCESS,
            "paused": ALMA_WARNING,
            "error": ALMA_ERROR,
        }
        labels = {"live": "Live", "paused": "Paused", "error": "Error"}
        c = colors.get(status, ALMA_TEXT_LIGHT)
        self._status_dot.setStyleSheet(f"font-size: 14px; color: {c};")
        self._status_label.setText(labels.get(status, status))
        self._status_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {c};"
        )
        self._pause_btn.setEnabled(status in ("live", "paused"))
        self._pause_btn.setText("Resume" if status == "paused" else "Pause")

    def _on_alert_fired(self, _alert: dict) -> None:
        """Live alert arrived — refresh the alerts tab to show it."""
        self._alerts_tab.refresh()

    def _on_alert_count_changed(self, count: int) -> None:
        """Update the Alerts tab badge text whenever the count changes."""
        badge = f" ({count})" if count > 0 else ""
        self._tabs.setTabText(TAB_ALERTS, f"Alerts{badge}")

    # ── Pause/resume ─────────────────────────────────────────────

    def _on_pause_resume(self) -> None:
        """Toggle the live monitor's run/pause state."""
        if not self._monitor:
            return
        status = getattr(self._monitor, "status", None)
        if status == "live":
            self._monitor.pause()
        else:
            self._monitor.resume()

    # ── Private helpers ─────────────────────────────────────────

    def _unwire_monitor(self, monitor) -> None:
        """Disconnect every signal we connected in ``set_monitor``.

        Called when set_monitor is invoked a second time (after
        credentials change) so we don't get duplicate slot calls.
        """
        # Disconnect ONLY the signal we actually wired (avoids the
        # noisy Qt warning when calling disconnect on an unwired signal).
        try:
            if self._records_signal_name == "records_received":
                monitor.records_received.disconnect(self._on_records_received)
            elif self._records_signal_name == "tickets_received":
                monitor.tickets_received.disconnect(self._on_records_received)
        except (TypeError, RuntimeError):
            # Either no slot was connected, or the C++ object was already
            # destroyed. Both are benign — proceed with cleanup.
            pass
        self._records_signal_name = None
        try:
            if hasattr(monitor, "status_changed"):
                monitor.status_changed.disconnect(self._on_status_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            if hasattr(monitor, "alert_fired"):
                monitor.alert_fired.disconnect(self._on_alert_fired)
        except (TypeError, RuntimeError):
            pass
