"""
Alerts tab — fired Watchlist alerts with Confirm/Dismiss feedback.

Each alert card shows: severity badge, title, summary, ticket count,
TRC code, source label, and (for open alerts) Confirm/Dismiss buttons
that update the rule's EWMA confidence via
``WatchlistEngine.record_feedback()``.

Decomposed from ``source_monitor_page._build_alerts_tab`` plus the
``_make_alert_card`` and ``_refresh_alerts`` helpers.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.ui.pages.source_monitor._styles import GHOST_BTN
from src.ui.theme import (
    ALMA_BORDER,
    ALMA_BORDER_LIGHT,
    ALMA_CREAM,
    ALMA_ERROR,
    ALMA_GREEN_DARK,
    ALMA_GREEN_SUBTLE,
    ALMA_INFO,
    ALMA_SUCCESS,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
    ALMA_WHITE,
    apply_card_shadow_soft,
)

logger = logging.getLogger("alma.source_monitor.alerts")


class AlertsTab(QWidget):
    """Scrollable list of fired Watchlist alerts.

    Public API
    ──────────
    - ``set_watchlist(watchlist)`` — wire the engine and trigger initial refresh
    - ``refresh()`` — reload alerts from the engine
    - ``open_alert_count`` (property) — count of currently-open alerts,
      used by the page shell to render a tab badge

    Signals
    ───────
    - ``alert_count_changed(int)`` — fired after every refresh with the
      open-alert count. The page shell connects this to update the
      "Alerts (N)" badge on its tab widget.
    """

    alert_count_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._watchlist = None
        self._alert_cards: list[QFrame] = []
        self._open_alert_count = 0
        self._build_ui()

    # ── Public API ────────────────────────────────────────────────

    def set_watchlist(self, watchlist) -> None:
        """Wire the WatchlistEngine and trigger an initial refresh."""
        self._watchlist = watchlist
        self.refresh()

    def refresh(self) -> None:
        """Reload alert cards from the engine.

        Clears all existing cards, applies the severity filter, and
        rebuilds the list. Emits ``alert_count_changed`` with the
        number of currently-open alerts.
        """
        self._clear_cards()

        if not self._watchlist:
            self._placeholder.show()
            self._set_open_count(0)
            return

        try:
            alerts = self._watchlist.get_all_alerts(limit=50)
        except Exception as exc:
            logger.warning("Failed to load alerts: %s", exc)
            alerts = []

        sev_filter = self._severity_filter.currentText()
        if sev_filter != "All":
            alerts = [a for a in alerts if a.get("severity") == sev_filter]

        if not alerts:
            self._placeholder.show()
            self._set_open_count(0)
            return

        self._placeholder.hide()

        open_count = 0
        for alert in alerts:
            card = _build_alert_card(alert, self._on_feedback)
            # Insert before the trailing stretch.
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._alert_cards.append(card)
            if alert.get("status") == "open":
                open_count += 1

        self._set_open_count(open_count)

    @property
    def open_alert_count(self) -> int:
        """Currently-open alert count (drives the page tab badge)."""
        return self._open_alert_count

    # ── Internal ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out the filter row, scroll area, and placeholder."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(12)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(12)

        lbl = QLabel("Severity:")
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};"
        )
        filter_row.addWidget(lbl)

        self._severity_filter = QComboBox()
        self._severity_filter.addItems(["All", "incident", "watch"])
        self._severity_filter.setStyleSheet(
            f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px 10px; font-size: 12px;
                color: {ALMA_TEXT_DARK}; min-width: 100px;
            }}
            """
        )
        self._severity_filter.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self._severity_filter)

        filter_row.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(GHOST_BTN)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh)
        filter_row.addWidget(refresh_btn)

        layout.addLayout(filter_row)

        # Scrollable cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        cards_container = QWidget()
        self._cards_layout = QVBoxLayout(cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)

        self._placeholder = QLabel(
            "No alerts yet. Watchlist rules evaluate incoming tickets "
            "automatically — alerts will show here when they fire."
        )
        self._placeholder.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; padding: 40px;"
        )
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._cards_layout.addWidget(self._placeholder)
        self._cards_layout.addStretch()

        scroll.setWidget(cards_container)
        layout.addWidget(scroll, 1)

    def _clear_cards(self) -> None:
        """Drop all current alert card widgets."""
        for card in self._alert_cards:
            card.setParent(None)
            card.deleteLater()
        self._alert_cards.clear()

    def _set_open_count(self, n: int) -> None:
        """Update the cached count and emit the change signal."""
        self._open_alert_count = n
        self.alert_count_changed.emit(n)

    def _on_feedback(self, alert_id: int, outcome: str) -> None:
        """User clicked Confirm or Dismiss on an alert card."""
        if not self._watchlist or alert_id is None:
            return
        try:
            self._watchlist.record_feedback(alert_id, outcome)
        except Exception as exc:
            logger.warning("record_feedback failed: %s", exc)
        self.refresh()


# ─── Free-function card builder ──────────────────────────────────

def _build_alert_card(alert: dict, on_feedback) -> QFrame:
    """Construct a single alert card.

    Args:
        alert: Row dict from ``WatchlistEngine.get_all_alerts()``.
        on_feedback: Callable ``(alert_id, outcome)`` where outcome is
            ``"confirmed"`` or ``"dismissed"``. Wired to the buttons.

    Returns:
        Configured QFrame ready to be inserted into a layout.
    """
    severity = alert.get("severity", "watch")
    status = alert.get("status", "open")
    is_incident = severity == "incident"

    # Border color reflects severity for open alerts; status overrides
    # for resolved alerts (green for confirmed, gray for dismissed).
    if status == "confirmed":
        border_color = ALMA_SUCCESS
    elif status in ("dismissed", "expired"):
        border_color = ALMA_BORDER_LIGHT
    else:
        border_color = ALMA_ERROR if is_incident else ALMA_WARNING

    card = QFrame()
    card.setStyleSheet(
        f"""
        QFrame {{
            background: {ALMA_WHITE};
            border: 1px solid {ALMA_BORDER_LIGHT};
            border-left: 4px solid {border_color};
            border-radius: 8px;
        }}
        """
    )
    apply_card_shadow_soft(card)

    lay = QVBoxLayout(card)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(6)

    lay.addLayout(_alert_top_row(alert, is_incident))

    summary = alert.get("summary", "")
    if summary:
        sum_lbl = QLabel(summary)
        sum_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_MID}; border: none;"
        )
        sum_lbl.setWordWrap(True)
        sum_lbl.setMaximumHeight(50)
        lay.addWidget(sum_lbl)

    lay.addLayout(_alert_meta_row(alert, status))

    if status == "open":
        lay.addLayout(_alert_action_row(alert, on_feedback))

    return card


def _alert_top_row(alert: dict, is_incident: bool) -> QHBoxLayout:
    """Top row: severity badge + title + timestamp."""
    top = QHBoxLayout()

    sev_color = ALMA_ERROR if is_incident else ALMA_WARNING
    sev_bg = "rgba(196,30,30,0.08)" if is_incident else "rgba(180,83,9,0.10)"
    sev_badge = QLabel(alert.get("severity", "watch").upper())
    sev_badge.setStyleSheet(
        f"font-size: 10px; font-weight: 700; color: {sev_color}; "
        f"background: {sev_bg}; border-radius: 4px; padding: 2px 8px; "
        f"border: none;"
    )
    top.addWidget(sev_badge)

    title = QLabel(alert.get("title", "Alert"))
    title.setStyleSheet(
        f"font-size: 12px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
        f"border: none;"
    )
    top.addWidget(title)
    top.addStretch()

    ts = alert.get("created_at", "")
    ts_lbl = QLabel(ts[:16] if ts else "")
    ts_lbl.setStyleSheet(
        f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
    )
    top.addWidget(ts_lbl)

    return top


def _alert_meta_row(alert: dict, status: str) -> QHBoxLayout:
    """Meta row: ticket count, source, TRC code, status badge."""
    meta = QHBoxLayout()

    ticket_count = alert.get("ticket_count", 0)
    tc_lbl = QLabel(f"{ticket_count} ticket(s)")
    tc_lbl.setStyleSheet(
        f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
    )
    meta.addWidget(tc_lbl)

    source = alert.get("source", "")
    if source:
        src_lbl = QLabel(source)
        src_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; color: {ALMA_GREEN_DARK}; "
            f"background: {ALMA_GREEN_SUBTLE}; border-radius: 3px; "
            f"padding: 1px 5px; border: none;"
        )
        meta.addWidget(src_lbl)

    trc = alert.get("trc_code", "")
    if trc:
        trc_lbl = QLabel(trc)
        trc_lbl.setStyleSheet(
            f"font-size: 10px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        meta.addWidget(trc_lbl)

    meta.addStretch()

    status_colors = {
        "open": ALMA_INFO,
        "confirmed": ALMA_SUCCESS,
        "dismissed": ALMA_TEXT_LIGHT,
        "expired": ALMA_TEXT_LIGHT,
    }
    s_color = status_colors.get(status, ALMA_TEXT_LIGHT)
    s_lbl = QLabel(status.upper())
    s_lbl.setStyleSheet(
        f"font-size: 9px; font-weight: 700; color: {s_color}; border: none;"
    )
    meta.addWidget(s_lbl)
    return meta


def _alert_action_row(alert: dict, on_feedback) -> QHBoxLayout:
    """Open-alert-only action row: Confirm / Dismiss buttons."""
    btn_row = QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch()

    alert_id = alert.get("id")

    confirm_btn = QPushButton("Confirm")
    confirm_btn.setStyleSheet(
        f"""
        QPushButton {{
            background: {ALMA_SUCCESS}; color: white;
            border: none; border-radius: 4px; padding: 4px 12px;
            font-size: 11px; font-weight: 600;
        }}
        QPushButton:hover {{ background: #059669; }}
        """
    )
    confirm_btn.setCursor(Qt.PointingHandCursor)
    confirm_btn.clicked.connect(
        lambda _, aid=alert_id: on_feedback(aid, "confirmed")
    )
    btn_row.addWidget(confirm_btn)

    dismiss_btn = QPushButton("Dismiss")
    dismiss_btn.setStyleSheet(
        f"""
        QPushButton {{
            background: transparent; color: {ALMA_TEXT_MID};
            border: 1px solid {ALMA_BORDER}; border-radius: 4px;
            padding: 4px 12px; font-size: 11px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """
    )
    dismiss_btn.setCursor(Qt.PointingHandCursor)
    dismiss_btn.clicked.connect(
        lambda _, aid=alert_id: on_feedback(aid, "dismissed")
    )
    btn_row.addWidget(dismiss_btn)

    return btn_row
