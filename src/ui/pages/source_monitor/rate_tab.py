"""
Rate tab — Live Feed (rate-per-hour control chart).

Replaces the old ticket-card scroll. Shows ``RateChartWidget`` with the
output of ``compute_rate_baseline()``. Header controls:

- TRC dropdown (aggregate or specific)
- 24 / 48 hour window toggle (matches the user's "24<>48 toggle" decision)
- Spike sidebar listing currently-spiking buckets

Refreshes are debounced — `monitor.records_received` arrives in batches,
and a full DB query on every batch would thrash the UI. We coalesce
into a single re-compute every 2 seconds.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.data.source_baseline import (
    DEFAULT_WINDOW_HOURS,
    compute_rate_baseline,
    list_active_trcs,
)
from src.ui.pages.source_monitor._styles import GHOST_BTN
from src.ui.theme import (
    ALMA_BORDER_LIGHT,
    ALMA_CHART_BG,
    ALMA_CREAM,
    ALMA_GREEN_DARK,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
    ALMA_WHITE,
    apply_card_shadow_soft,
)
from src.ui.widgets.rate_chart import RateChartWidget

logger = logging.getLogger("alma.source_monitor.rate_tab")

#: Debounce window for refreshes triggered by record-arrival signals.
_REFRESH_DEBOUNCE_MS = 2000

#: Default source. The page is currently Zendesk-only; this constant
#: makes the eventual multi-source upgrade obvious.
_DEFAULT_SOURCE = "zendesk"


class RateTab(QWidget):
    """Live Feed tab — control chart + spike sidebar.

    Public API
    ──────────
    - ``set_db(db_manager)`` — wire the database connection used for
      baseline queries
    - ``schedule_refresh()`` — request a debounced re-compute (called
      by the page shell when ``monitor.records_received`` fires)
    - ``refresh_now()`` — bypass the debounce; use for explicit
      "Refresh" button clicks
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = None
        self._window_hours = DEFAULT_WINDOW_HOURS
        self._trc_filter: str | None = None  # None = aggregate

        # Debounce timer — single-shot, restarted on each request.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_now)

        self._build_ui()

    # ── Public API ────────────────────────────────────────────────

    def set_db(self, db_manager) -> None:
        """Wire the DB and trigger an initial refresh."""
        self._db = db_manager
        # Populate TRC dropdown from current data; fire baseline query.
        self._refresh_trc_dropdown()
        self.refresh_now()

    def schedule_refresh(self) -> None:
        """Request a debounced refresh.

        Restarts the QTimer; if multiple records arrive in quick
        succession we only re-compute once.
        """
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
        self._refresh_timer.start(_REFRESH_DEBOUNCE_MS)

    def refresh_now(self) -> None:
        """Bypass the debounce and re-compute the baseline immediately."""
        if self._db is None:
            return
        try:
            conn = getattr(self._db, "conn", None) or self._db
            baseline = compute_rate_baseline(
                conn,
                _DEFAULT_SOURCE,
                trc_code=self._trc_filter,
                window_hours=self._window_hours,
            )
        except Exception as exc:
            logger.warning("compute_rate_baseline failed: %s", exc)
            self._chart.clear()
            return

        self._chart.set_baseline(baseline)
        self._update_spike_sidebar(baseline)

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out header controls + chart + spike sidebar."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # ── Left column: controls + chart ──
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        left.addLayout(self._build_controls_row())

        self._chart = RateChartWidget()
        self._chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._chart.setStyleSheet(
            f"background: {ALMA_CHART_BG}; border-radius: 8px;"
        )
        left.addWidget(self._chart, 1)

        layout.addLayout(left, 3)

        # ── Right column: spike sidebar ──
        layout.addWidget(self._build_spike_sidebar(), 1)

    def _build_controls_row(self) -> QHBoxLayout:
        """TRC dropdown + 24/48 toggle + Refresh button."""
        row = QHBoxLayout()
        row.setSpacing(10)

        trc_lbl = QLabel("TRC:")
        trc_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};"
        )
        row.addWidget(trc_lbl)

        self._trc_combo = QComboBox()
        self._trc_combo.addItem("All TRCs (aggregate)", None)
        self._trc_combo.setStyleSheet(
            f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 6px; padding: 4px 10px; font-size: 12px;
                color: {ALMA_TEXT_DARK}; min-width: 220px;
            }}
            """
        )
        self._trc_combo.currentIndexChanged.connect(self._on_trc_changed)
        row.addWidget(self._trc_combo)

        row.addSpacing(16)

        win_lbl = QLabel("Window:")
        win_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};"
        )
        row.addWidget(win_lbl)

        # The 24/48 toggle is a small QButtonGroup of 2 mutually-exclusive
        # toggle buttons. Cleaner than a combo for binary state.
        self._window_group = QButtonGroup(self)
        self._window_group.setExclusive(True)
        for label, hours in (("24h", 24), ("48h", 48)):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(_window_btn_style())
            btn.setProperty("window_hours", hours)
            if hours == self._window_hours:
                btn.setChecked(True)
            btn.clicked.connect(self._on_window_changed)
            self._window_group.addButton(btn)
            row.addWidget(btn)

        row.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(GHOST_BTN)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_now)
        row.addWidget(refresh_btn)

        return row

    def _build_spike_sidebar(self) -> QFrame:
        """Right-hand panel listing currently-spiking buckets."""
        panel = QFrame()
        panel.setStyleSheet(
            f"""
            QFrame {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 12px;
            }}
            """
        )
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(280)
        apply_card_shadow_soft(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        title = QLabel("Spikes (last 24h)")
        title.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            f"border: none;"
        )
        lay.addWidget(title)

        self._spike_list_layout = QVBoxLayout()
        self._spike_list_layout.setSpacing(4)
        lay.addLayout(self._spike_list_layout)

        self._spike_empty_label = QLabel("No spikes detected")
        self._spike_empty_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        self._spike_list_layout.addWidget(self._spike_empty_label)

        lay.addStretch()
        return panel

    # ── Slot handlers ────────────────────────────────────────────

    def _on_trc_changed(self, _index: int) -> None:
        """User picked a different TRC from the dropdown."""
        self._trc_filter = self._trc_combo.currentData()
        self.refresh_now()

    def _on_window_changed(self) -> None:
        """User toggled 24h ↔ 48h."""
        for btn in self._window_group.buttons():
            if btn.isChecked():
                self._window_hours = int(btn.property("window_hours"))
                break
        self.refresh_now()

    # ── TRC dropdown population ──────────────────────────────────

    def _refresh_trc_dropdown(self) -> None:
        """Populate the TRC combo from the warehouse's most-active list."""
        if self._db is None:
            return

        try:
            conn = getattr(self._db, "conn", None) or self._db
            trcs = list_active_trcs(conn, _DEFAULT_SOURCE)
        except Exception as exc:
            logger.debug("list_active_trcs failed: %s", exc)
            trcs = []

        # Preserve current selection if still valid.
        current = self._trc_combo.currentData()

        self._trc_combo.blockSignals(True)
        self._trc_combo.clear()
        self._trc_combo.addItem("All TRCs (aggregate)", None)
        for trc in trcs:
            self._trc_combo.addItem(trc, trc)

        if current and current in trcs:
            idx = self._trc_combo.findData(current)
            if idx >= 0:
                self._trc_combo.setCurrentIndex(idx)
        self._trc_combo.blockSignals(False)

    # ── Spike sidebar update ─────────────────────────────────────

    def _update_spike_sidebar(self, baseline) -> None:
        """Rebuild the sidebar list from baseline.is_spike."""
        # Clear all entries except the empty-label sentinel.
        while self._spike_list_layout.count() > 0:
            item = self._spike_list_layout.takeAt(0)
            w = item.widget()
            if w and w is not self._spike_empty_label:
                w.setParent(None)
                w.deleteLater()

        # Surface up to 6 most-recent spikes (newest first).
        spike_indices = [i for i, s in enumerate(baseline.is_spike) if s]
        spike_indices.reverse()
        spike_indices = spike_indices[:6]

        if not spike_indices:
            self._spike_empty_label.show()
            self._spike_list_layout.addWidget(self._spike_empty_label)
            return

        self._spike_empty_label.hide()

        for i in spike_indices:
            ts = baseline.hours[i]
            count = baseline.rates[i]
            upper = baseline.upper_band[i]
            label = QLabel(self._format_spike_entry(ts, count, upper))
            label.setStyleSheet(
                f"font-size: 11px; font-weight: 600; "
                f"color: {ALMA_WARNING}; "
                f"background: rgba(180,83,9,0.08); border-radius: 4px; "
                f"padding: 6px 8px; border: none;"
            )
            label.setWordWrap(True)
            self._spike_list_layout.addWidget(label)

    @staticmethod
    def _format_spike_entry(iso: str, count: int, upper: float) -> str:
        """Compose the sidebar entry text: ``HH:00 — N tickets (>X)``."""
        hh = iso[11:13] if "T" in iso else "??"
        date = iso[:10] if "T" in iso else iso
        return f"⚠ {date} {hh}:00 — {count} tickets (band {upper:.0f})"


# ─── Free-function styling helpers ───────────────────────────────

def _window_btn_style() -> str:
    """QSS for the 24h / 48h toggle button. Highlights when checked."""
    return f"""
        QPushButton {{
            background: {ALMA_WHITE}; color: {ALMA_TEXT_MID};
            border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 6px;
            padding: 4px 14px; font-size: 12px; font-weight: 600;
            min-width: 44px;
        }}
        QPushButton:hover {{ background: {ALMA_CREAM}; }}
        QPushButton:checked {{
            background: {ALMA_GREEN_DARK}; color: {ALMA_WHITE};
            border-color: {ALMA_GREEN_DARK};
        }}
    """
