"""Guru Analytics — top cards, verification KPIs, open comments, due cards.

Pure view: the host (EnablementPage) feeds data via set_data()/set_filters()
and reacts to the signals. Rendering is row-based (rank + proportional usage
bar) rather than a chart widget — matches the Guru-style list UX and stays
resize-robust.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_ELEVATED, ALMA_BG_INSET, ALMA_BORDER,
    ALMA_CREAM, ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK, ALMA_WARNING,
)
from src.ui.pages.enablement._common import badge, card_frame, pill, section_label
from src.ui.pages.enablement.mini_charts import DonutChart

_REASON_BADGE = {
    "unverified": ("Unverified", "high"),
    "verification_due": ("Verification due", "in_progress"),
    "stale_high_traffic": ("High traffic, stale", "draft"),
}

# Verification-state → (legend label, donut colour).
_STATE_STYLE = {
    "TRUSTED": ("Trusted", "#3FA66A"),
    "VERIFIED": ("Verified", "#3FA66A"),
    "NEEDS_VERIFICATION": ("Needs verification", "#E0A82E"),
    "UNVERIFIED": ("Unverified", "#E0A82E"),
    "STALE": ("Stale", "#D6603A"),
}


class AnalyticsPage(QWidget):
    refresh_requested = Signal()
    filters_changed = Signal()
    comment_task_requested = Signal(str)      # comment_id
    targeted_update_requested = Signal(str)   # card_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._overlay_host = None
        self._overlay = None
        self._last = ({}, [], [], [])
        self._build()

    # ── layout ──────────────────────────────────────────────────────

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 18)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(section_label("GURU ANALYTICS"))
        self._sync_pill = pill("Never synced", "#ECEAE5", ALMA_TEXT_LIGHT)
        head.addWidget(self._sync_pill)
        head.addStretch(1)

        self._days = QComboBox()
        for label, days in (("Last 7 days", 7), ("Last 30 days", 30),
                            ("Last 90 days", 90)):
            self._days.addItem(label, days)
        self._days.setCurrentIndex(1)
        self._collection = QComboBox()
        self._collection.addItem("All collections", None)
        self._domain = QComboBox()
        self._domain.addItem("All card types", None)
        for combo in (self._days, self._collection, self._domain):
            combo.setFixedHeight(30)
            combo.setStyleSheet(
                f"QComboBox{{background:{ALMA_BG_ELEVATED}; border:1px solid "
                f"{ALMA_BORDER}; border-radius:7px; padding:2px 10px; "
                f"font-size:12px; color:{ALMA_TEXT_DARK}; min-width:118px;}}"
            )
            combo.currentIndexChanged.connect(
                lambda _i: self.filters_changed.emit()
            )
            head.addWidget(combo)

        expand = QPushButton("⤢  Expand")
        expand.setCursor(Qt.PointingHandCursor)
        expand.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_TEXT_MID}; "
            f"border:1px solid {ALMA_BORDER}; border-radius:8px; padding:7px 13px; "
            "font-size:12px; font-weight:600; }"
        )
        expand.clicked.connect(self._open_expand)
        head.addWidget(expand)

        refresh = QPushButton("Refresh from Guru")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; "
            "border:none; border-radius:8px; padding:7px 16px; font-size:12px; "
            "font-weight:600; }"
        )
        refresh.clicked.connect(self.refresh_requested.emit)
        head.addWidget(refresh)
        outer.addLayout(head)

        self._kpi_row = QHBoxLayout()
        self._kpi_row.setSpacing(10)
        outer.addLayout(self._kpi_row)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(section_label("TOP USED CARDS"))
        self._top_card = card_frame()
        self._top_layout = QVBoxLayout(self._top_card)
        self._top_layout.setContentsMargins(16, 12, 16, 12)
        self._top_layout.setSpacing(4)
        left.addWidget(self._top_card, 1)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(section_label("VERIFICATION"))
        self._donut_card = card_frame()
        dl = QHBoxLayout(self._donut_card)
        dl.setContentsMargins(14, 10, 14, 10)
        dl.setSpacing(12)
        self._donut = DonutChart()
        self._donut.setFixedWidth(140)
        dl.addWidget(self._donut)
        legend_w = QWidget()
        legend_w.setStyleSheet("background:transparent;")
        self._legend = QVBoxLayout(legend_w)
        self._legend.setContentsMargins(0, 6, 0, 6)
        self._legend.setSpacing(5)
        dl.addWidget(legend_w, 1)
        right.addWidget(self._donut_card)

        right.addWidget(section_label("NEEDS ATTENTION"))
        self._due_card = card_frame()
        self._due_layout = QVBoxLayout(self._due_card)
        self._due_layout.setContentsMargins(16, 12, 16, 12)
        self._due_layout.setSpacing(6)
        right.addWidget(self._due_card)
        right.addWidget(section_label("OPEN CARD COMMENTS"))
        self._comments_card = card_frame()
        self._comments_layout = QVBoxLayout(self._comments_card)
        self._comments_layout.setContentsMargins(16, 12, 16, 12)
        self._comments_layout.setSpacing(6)
        right.addWidget(self._comments_card, 1)
        body.addLayout(right, 2)

        outer.addLayout(body, 1)

    # ── filter accessors (host reads on refresh) ────────────────────

    def days(self) -> int:
        return int(self._days.currentData() or 30)

    def collection_id(self):
        return self._collection.currentData()

    def domain(self):
        return self._domain.currentData()

    def set_filters(self, collections: list[tuple], domains: list[str]):
        """(id, name) collections + domain strings; preserves selection."""
        for combo, items, label in (
            (self._collection,
             [(cid, name) for cid, name in collections], "All collections"),
            (self._domain, [(d, d) for d in domains], "All card types"),
        ):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, None)
            for value, name in items:
                combo.addItem(name or value, value)
            idx = combo.findData(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    # ── data ────────────────────────────────────────────────────────

    def set_data(self, kpis: dict, top: list[dict], comments: list[dict],
                 due: list[dict]):
        self._last = (kpis, top, comments, due)
        self._render_sync_pill(kpis.get("last_sync_at", ""))
        self._render_kpis(kpis)
        self._render_donut(kpis, self._donut, self._legend)
        self._render_top(top, self._top_layout)
        self._render_due(due)
        self._render_comments(comments)

    def set_overlay_host(self, widget):
        self._overlay_host = widget

    # ── verification donut ──────────────────────────────────────────
    def _donut_segments(self, kpis: dict) -> list[tuple]:
        segs = []
        for key, count in (kpis.get("states", {}) or {}).items():
            if not count:
                continue
            label, color = _STATE_STYLE.get(str(key).upper(),
                                            (str(key).title(), "#9AA4B2"))
            segs.append((label, int(count), color))
        return segs

    def _render_donut(self, kpis: dict, donut, legend):
        segs = self._donut_segments(kpis)
        donut.set_segments(segs, "cards")
        self._clear(legend)
        if not segs:
            legend.addWidget(self._empty("No verification data yet."))
            return
        for label, count, color in segs:
            row = QFrame()
            row.setStyleSheet("background:transparent; border:none;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            h.addWidget(dot, 0, Qt.AlignVCenter)
            name = QLabel(label)
            name.setStyleSheet(
                f"color:{ALMA_TEXT_MID}; font-size:11.5px; border:none;")
            h.addWidget(name, 1)
            val = QLabel(f"{count:,}")
            val.setStyleSheet(
                f"color:{ALMA_TEXT_DARK}; font-size:12px; font-weight:700; border:none;")
            h.addWidget(val)
            legend.addWidget(row)
        legend.addStretch(1)

    # ── expand / focus ──────────────────────────────────────────────
    def _open_expand(self):
        from src.ui.pages.enablement.focus_overlay import FocusOverlay
        if self._overlay is None:
            self._overlay = FocusOverlay(self._overlay_host or self.window(),
                                         title="Guru Analytics")
        self._build_expand_content(self._overlay.content)
        self._overlay.present()

    def _build_expand_content(self, layout):
        # clear any prior expand content
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())
        kpis, top, _comments, _due = self._last
        row = QHBoxLayout()
        row.setSpacing(14)
        # big donut + legend
        dcard = card_frame()
        dl = QHBoxLayout(dcard)
        dl.setContentsMargins(18, 14, 18, 14)
        dl.setSpacing(16)
        big = DonutChart()
        big.setMinimumSize(220, 220)
        dl.addWidget(big)
        lw = QWidget()
        lw.setStyleSheet("background:transparent;")
        leg = QVBoxLayout(lw)
        leg.setSpacing(7)
        dl.addWidget(lw, 1)
        self._render_donut(kpis, big, leg)
        row.addWidget(dcard, 2)
        # top cards
        tcard = card_frame()
        tl = QVBoxLayout(tcard)
        tl.setContentsMargins(18, 14, 18, 14)
        tl.setSpacing(6)
        tl.addWidget(section_label("TOP USED CARDS"))
        self._render_top(top, tl, limit=20)
        row.addWidget(tcard, 3)
        layout.addLayout(row, 1)

    def _clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _render_sync_pill(self, last_sync: str):
        if last_sync:
            self._sync_pill.setText(f"Synced {last_sync[:16].replace('T', ' ')}")
        else:
            self._sync_pill.setText("Never synced")

    def _render_kpis(self, kpis: dict):
        self._clear(self._kpi_row)
        states = kpis.get("states", {})
        cells = (
            ("In verification queue", kpis.get("queue_total", 0)),
            ("Due in 14 days", kpis.get("due_soon", 0)),
            ("Open comments", kpis.get("open_comments", 0)),
            ("Needs verification",
             states.get("NEEDS_VERIFICATION", 0) + states.get("STALE", 0)),
        )
        for label, value in cells:
            cell = card_frame()
            v = QVBoxLayout(cell)
            v.setContentsMargins(16, 10, 16, 10)
            v.setSpacing(2)
            num = QLabel(f"{value:,}")
            num.setStyleSheet(
                f"color:{ALMA_TEXT_DARK}; font-size:24px; font-weight:700; border:none;"
            )
            cap = QLabel(label.upper())
            cap.setStyleSheet(
                f"color:{ALMA_TEXT_LIGHT}; font-size:10px; font-weight:700; "
                "letter-spacing:0.6px; border:none;"
            )
            v.addWidget(num)
            v.addWidget(cap)
            self._kpi_row.addWidget(cell)

    def _render_top(self, top: list[dict], layout, limit: int = 12):
        self._clear(layout)
        if not top:
            layout.addWidget(self._empty(
                "No usage events yet — refresh from Guru to pull analytics."))
            return
        max_views = max((c.get("views", 0) for c in top), default=1) or 1
        for rank, card in enumerate(top[:limit], start=1):
            row = QFrame()
            row.setStyleSheet("background:transparent; border:none;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(10)
            num = QLabel(f"{rank}")
            num.setFixedWidth(18)
            num.setStyleSheet(
                f"color:{ALMA_TEXT_LIGHT}; font-size:11px; font-weight:700; border:none;"
            )
            h.addWidget(num)

            text_col = QVBoxLayout()
            text_col.setSpacing(1)
            title = QLabel(card.get("title", "")[:70])
            title.setStyleSheet(
                f"color:{ALMA_TEXT_DARK}; font-size:12.5px; font-weight:600; border:none;"
            )
            text_col.addWidget(title)
            bar_row = QHBoxLayout()
            bar_row.setSpacing(0)
            frac = (card.get("views", 0) / max_views)
            bar = QFrame()
            bar.setFixedHeight(5)
            bar.setFixedWidth(max(int(220 * frac), 3))
            bar.setStyleSheet(
                f"background:{ALMA_GREEN_LIGHT}; border-radius:2px; border:none;"
            )
            bar_row.addWidget(bar)
            bar_row.addStretch(1)
            text_col.addLayout(bar_row)
            h.addLayout(text_col, 1)

            if card.get("collection"):
                h.addWidget(badge(card["collection"][:24], "guru"))
            views = QLabel(f"{card.get('views', 0):,} views")
            views.setStyleSheet(
                f"color:{ALMA_TEXT_MID}; font-size:11.5px; border:none;"
            )
            h.addWidget(views)
            layout.addWidget(row)
        layout.addStretch(1)

    def _render_due(self, due: list[dict]):
        self._clear(self._due_layout)
        if not due:
            self._due_layout.addWidget(self._empty("Nothing due — nice."))
            return
        for card in due[:6]:
            row = QFrame()
            row.setStyleSheet("background:transparent; border:none;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(8)
            label, kind = _REASON_BADGE.get(
                card.get("reason", ""), (card.get("reason", ""), "normal"))
            h.addWidget(badge(label, kind))
            col = QVBoxLayout()
            col.setSpacing(0)
            t = QLabel(card.get("title", "")[:48])
            t.setStyleSheet(
                f"color:{ALMA_TEXT_DARK}; font-size:12px; font-weight:600; border:none;"
            )
            col.addWidget(t)
            d = QLabel(f"due {card.get('due_date', '')}")
            d.setStyleSheet(
                f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; border:none;"
            )
            col.addWidget(d)
            h.addLayout(col, 1)
            btn = QPushButton("Targeted update")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{ALMA_ACCENT_TEAL}; "
                f"border:1px solid {ALMA_ACCENT_TEAL}; border-radius:7px; "
                "padding:4px 10px; font-size:11px; font-weight:600; }"
            )
            btn.clicked.connect(
                lambda _=False, cid=card.get("card_id", ""):
                self.targeted_update_requested.emit(cid)
            )
            h.addWidget(btn)
            self._due_layout.addWidget(row)

    def _render_comments(self, comments: list[dict]):
        self._clear(self._comments_layout)
        if not comments:
            self._comments_layout.addWidget(self._empty("No open comments."))
            return
        for c in comments[:8]:
            row = QFrame()
            row.setStyleSheet(
                f"QFrame{{background:{ALMA_BG_INSET}; border:none; border-radius:8px;}}"
            )
            v = QVBoxLayout(row)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(3)
            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            t = QLabel(c.get("card_title", "") or c.get("card_id", ""))
            t.setStyleSheet(
                f"color:{ALMA_TEXT_DARK}; font-size:12px; font-weight:600; "
                "border:none; background:transparent;"
            )
            top_row.addWidget(t, 1)
            if c.get("task_id"):
                top_row.addWidget(badge("Task created", "done"))
            else:
                b = QPushButton("Create task")
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet(
                    f"QPushButton{{background:transparent; color:{ALMA_GREEN_DARK}; "
                    f"border:1px solid {ALMA_BORDER}; border-radius:6px; "
                    "padding:3px 9px; font-size:10.5px; font-weight:600; }"
                )
                b.clicked.connect(
                    lambda _=False, cid=c.get("comment_id", ""):
                    self.comment_task_requested.emit(cid)
                )
                top_row.addWidget(b)
            v.addLayout(top_row)
            snippet = QLabel(f"{c.get('author', '')}: {(c.get('text', '') or '')[:120]}")
            snippet.setWordWrap(True)
            snippet.setStyleSheet(
                f"color:{ALMA_TEXT_MID}; font-size:11.5px; border:none; "
                "background:transparent;"
            )
            v.addWidget(snippet)
            self._comments_layout.addWidget(row)
        self._comments_layout.addStretch(1)

    def _empty(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none; padding:6px 0;"
        )
        return lbl
