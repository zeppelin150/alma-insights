"""Renn usage panel — the enablement Settings Usage tab.

Renders what ``src/data/renn_usage.py`` meters: the assistant's real
per-turn activity (turns, tokens, CLI-reported cost), replacing the
scan-centric CostDashboard on this side (2026-07-22). The NLP scanner's
cost limits / plan utilization stay on the product side's Scanning Costs
tab where they belong.

Layout: a hero row of stat tiles (today / week / month / cost), a 14-day
activity column chart, and the recent turns list. All reads go through
``renn_usage.usage_summary`` on the hosting warehouse (the demo db in
demo mode); refresh happens on show and via the Refresh button — no
timers (the Settings page is pinned timer-free by the help claims).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.data import renn_usage
from src.ui.pages.enablement._common import card_frame, pill, section_label
from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
)

_ACCENT = "#0D7D72"          # the enablement teal (matches Home)
_ACCENT_SOFT = "#E5F3EC"


def _fmt_tokens(n: int) -> str:
    """Compact token counts: 950 → '950', 12_340 → '12.3k', 1.2M."""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    if n >= 1_000:
        return f"{n / 1_000:.2f}k"
    return str(n)


def _fmt_cost(cost: float) -> str:
    cost = float(cost or 0.0)
    if cost == 0.0:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:,.2f}"


class _ColumnChart(QWidget):
    """Minimal 14-day activity column chart (QPainter, no dependencies).

    One rounded column per day, teal on the cream card; day-of-month labels
    under every other column. All-zero series renders a quiet empty state.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: list[tuple[str, int]] = []
        self.setMinimumHeight(150)

    def set_series(self, series: list[tuple[str, int]]):
        self._series = list(series or [])
        self.update()

    def paintEvent(self, event):  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        left, right, top, bottom = 8, 8, 14, 26
        plot_w = rect.width() - left - right
        plot_h = rect.height() - top - bottom
        baseline_y = top + plot_h

        # Baseline
        painter.setPen(QColor(ALMA_BORDER_LIGHT))
        painter.drawLine(left, baseline_y, rect.width() - right, baseline_y)

        if not self._series or all(v == 0 for _d, v in self._series):
            painter.setPen(QColor(ALMA_TEXT_LIGHT))
            painter.drawText(rect.adjusted(0, 0, 0, -bottom), Qt.AlignCenter,
                             "No metered turns yet — chat with Renn and "
                             "activity appears here.")
            painter.end()
            return

        n = len(self._series)
        max_v = max(v for _d, v in self._series)
        slot = plot_w / n
        bar_w = max(6.0, min(30.0, slot * 0.55))

        painter.setPen(Qt.NoPen)
        label_pen = QColor(ALMA_TEXT_LIGHT)
        value_pen = QColor(ALMA_TEXT_MID)
        for i, (day, value) in enumerate(self._series):
            cx = left + slot * i + slot / 2
            if value > 0:
                h = max(4.0, (value / max_v) * (plot_h - 18))
                bar = QRectF(cx - bar_w / 2, baseline_y - h, bar_w, h)
                path = QPainterPath()
                path.addRoundedRect(bar, 3, 3)
                painter.fillPath(path, QColor(_ACCENT))
                # Value above the column (skip when crowded)
                if slot >= 24:
                    painter.setPen(value_pen)
                    painter.drawText(
                        QRectF(cx - slot / 2, baseline_y - h - 16, slot, 14),
                        Qt.AlignHCenter | Qt.AlignBottom, str(value))
                    painter.setPen(Qt.NoPen)
            # Day-of-month label under every other column
            if i % 2 == (n - 1) % 2:
                painter.setPen(label_pen)
                painter.drawText(
                    QRectF(cx - slot / 2, baseline_y + 4, slot, 16),
                    Qt.AlignHCenter | Qt.AlignTop, day[-2:])
                painter.setPen(Qt.NoPen)
        painter.end()


class RennUsagePanel(QWidget):
    """Renn's metered activity — turns, tokens, and CLI-reported cost."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._tiles: dict[str, tuple[QLabel, QLabel]] = {}
        self._build()
        self.refresh()

    # ── construction ────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 8, 2, 8)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(section_label("RENN ACTIVITY"))
        head.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ALMA_TEXT_MID}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:7px; "
            "padding:4px 12px; font-size:11.5px; font-weight:600;}"
        )
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        outer.addLayout(head)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        for key, caption in (("today", "TURNS TODAY"),
                             ("week", "THIS WEEK"),
                             ("month", "THIS MONTH"),
                             ("cost", "REPORTED COST · 30 DAYS")):
            tiles.addWidget(self._tile(key, caption), 1)
        outer.addLayout(tiles)

        chart_card = card_frame()
        cv = QVBoxLayout(chart_card)
        cv.setContentsMargins(20, 14, 20, 12)
        cv.setSpacing(6)
        cv.addWidget(section_label("ACTIVITY — LAST 14 DAYS"))
        self._chart = _ColumnChart()
        cv.addWidget(self._chart)
        outer.addWidget(chart_card)

        recent_card = card_frame()
        rv = QVBoxLayout(recent_card)
        rv.setContentsMargins(20, 14, 20, 14)
        rv.setSpacing(6)
        rv.addWidget(section_label("RECENT TURNS"))
        self._recent_layout = QVBoxLayout()
        self._recent_layout.setSpacing(4)
        rv.addLayout(self._recent_layout)
        outer.addWidget(recent_card)

        note = QLabel(
            "Metered from the Claude CLI's own per-turn usage reports "
            "(source renn_chat). Cost shows $0.00 on a subscription CLI "
            "login — the CLI reports no dollar figure there. Background "
            "generation jobs aren't metered yet; scan and report costs "
            "live on the product side's Scanning Costs tab."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11.5px; border:none; "
            "background:transparent;")
        outer.addWidget(note)
        outer.addStretch(1)

    def _tile(self, key: str, caption: str) -> QFrame:
        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 12, 18, 12)
        v.setSpacing(2)
        num = QLabel("0")
        num.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:26px; font-weight:700; "
            "background:transparent; border:none;")
        v.addWidget(num)
        cap = QLabel(caption)
        cap.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:10px; font-weight:700; "
            "letter-spacing:0.6px; background:transparent; border:none;")
        v.addWidget(cap)
        sub = QLabel("")
        sub.setStyleSheet(
            f"color:{ALMA_TEXT_MID}; font-size:11px; "
            "background:transparent; border:none;")
        v.addWidget(sub)
        self._tiles[key] = (num, sub)
        return card

    # ── data ────────────────────────────────────────────────────────

    def _conn(self):
        return getattr(self._db, "conn", None)

    def refresh(self):
        """Re-query the ledger and repaint every section. Never raises."""
        conn = self._conn()
        if conn is None:
            return
        summary = renn_usage.usage_summary(conn)

        for key in ("today", "week", "month"):
            totals = summary[key]
            num, sub = self._tiles[key]
            num.setText(f"{totals['turns']:,}")
            num_tokens = totals["tokens_in"] + totals["tokens_out"]
            sub.setText(
                f"{_fmt_tokens(totals['tokens_in'])} in · "
                f"{_fmt_tokens(totals['tokens_out'])} out"
                if num_tokens else "no tokens metered")

        num, sub = self._tiles["cost"]
        month = summary["month"]
        num.setText(_fmt_cost(month["cost_usd"]))
        sub.setText("CLI-reported" if month["cost_usd"] > 0
                    else "$0 on subscription login")

        self._chart.set_series(summary["series"])
        self._render_recent(summary["recent"])

    def _render_recent(self, rows: list[dict]):
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        if not rows:
            empty = QLabel("Nothing metered yet.")
            empty.setStyleSheet(
                f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none; "
                "background:transparent; padding:4px 0;")
            self._recent_layout.addWidget(empty)
            return
        for row in rows:
            self._recent_layout.addWidget(self._recent_row(row))

    def _recent_row(self, row: dict) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame{{background:{ALMA_BG_ELEVATED}; border:1px solid "
            f"{ALMA_BORDER_LIGHT}; border-radius:7px;}}")
        h = QHBoxLayout(frame)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)
        h.addWidget(pill(row.get("source", ""), _ACCENT_SOFT, _ACCENT))
        model = QLabel(row.get("model") or "—")
        model.setStyleSheet(
            f"color:{ALMA_TEXT_DARK}; font-size:12px; font-weight:600; "
            "border:none; background:transparent;")
        h.addWidget(model)
        tokens = QLabel(
            f"{_fmt_tokens(row.get('tokens_in', 0))} in · "
            f"{_fmt_tokens(row.get('tokens_out', 0))} out")
        tokens.setStyleSheet(
            f"color:{ALMA_TEXT_MID}; font-size:11.5px; border:none; "
            "background:transparent;")
        h.addWidget(tokens)
        h.addStretch(1)
        cost = row.get("cost_usd", 0.0)
        if cost:
            cost_lbl = QLabel(_fmt_cost(cost))
            cost_lbl.setStyleSheet(
                f"color:{ALMA_TEXT_MID}; font-size:11.5px; border:none; "
                "background:transparent;")
            h.addWidget(cost_lbl)
        hour = row.get("hour")
        when = QLabel(f"{row.get('date', '')}"
                      f"{f'  {int(hour):02d}:00' if hour is not None else ''}")
        when.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none; "
            "background:transparent;")
        h.addWidget(when)
        return frame

    def showEvent(self, event):  # noqa: N802 — Qt override
        super().showEvent(event)
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 — a usage read must not break Settings
            pass
