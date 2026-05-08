"""
Guru KB — Work-in-Progress placeholder page.

The full Guru integration (6 tabs: Cards, Workbench, Gap Analysis,
Drafts, Effectiveness, Connection) is implemented in
``src/ui/pages/guru_page.py`` and remains preserved for the eventual
re-launch. This wrapper page shows a **static QPainter wireframe**
of the planned Workbench UI with a "Coming Soon — Work In Progress"
banner overlay so users see what's being built without tripping over
half-finished interactions.

Routing
───────
``MainWindow._setup_guru()`` checks ``guru.experimental_ui_enabled``
in settings. When ``False`` (the default), it instantiates this page;
when ``True``, it falls through to the legacy ``GuruPage``.

Compatibility
─────────────
The wrapper exposes the same setter methods as ``GuruPage`` so
``MainWindow`` doesn't need to branch:

  - ``set_friction_pipeline``    (no-op)
  - ``set_content_pipeline``     (no-op)
  - ``set_effectiveness_tracker`` (no-op)
  - ``set_drilldown_panel``      (no-op)
  - ``set_guru_client``          (no-op)
  - ``connection_changed`` Signal (defined but never emitted)

The "Enable experimental UI" button writes the settings flag and
prompts for a restart so the legacy page mounts on next boot.

Mockup contents
───────────────
The wireframe paints the planned 5-section Workbench layout:
  1. Card selection (left panel)
  2. TRC scope checkboxes
  3. Analysis context textarea
  4. Phase progress bars (Search → Per-card → Synthesis)
  5. Redline output viewer + export buttons

Source for the planned UI:
  ``docs/plans/2026-03-10_feature-suite-p3.5-p4-source-abstraction.md:806-820``

Author: 2026-05-07 source-monitor + Guru-WIP redesign
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED,
    ALMA_BG_INSET,
    ALMA_BORDER,
    ALMA_BORDER_LIGHT,
    ALMA_CHART_BG,
    ALMA_CREAM,
    ALMA_GREEN_DARK,
    ALMA_GREEN_LIGHT,
    ALMA_GREEN_MID,
    ALMA_INFO,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
    apply_card_shadow,
)

logger = logging.getLogger("alma.guru_wip")

#: Settings key that enables the legacy interactive Guru UI.
EXPERIMENTAL_FLAG_KEY = "guru.experimental_ui_enabled"

#: Planned Workbench feature list — also rendered as a bullet list on
#: the left of the page so the static mockup tells the same story
#: as the planning doc.
PLANNED_FEATURES: list[str] = [
    "Browse Guru cards with friction scores",
    "Gap analysis: friction types vs Guru coverage",
    "LLM-drafted card rewrites with diff approval",
    "Effectiveness tracking (pre/post ticket volume)",
    "Card cluster + redline workbench",
    "Closed-loop friction analysis with feedback",
]


class GuruWipPage(QWidget):
    """Stand-in for the in-progress Guru KB page.

    Layout
    ──────
    Top: title + subtitle banner.
    Middle: WIP card containing
      - "Coming Soon" headline + planned-features bullet list (left)
      - WorkbenchMockupWidget wireframe (right)
    Bottom: "Enable experimental UI" escape-hatch button.
    """

    #: Defined for API compatibility with ``GuruPage.connection_changed``.
    #: Never emitted by this placeholder.
    connection_changed = Signal()

    def __init__(self, db_manager=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db_manager
        self.setStyleSheet(f"background: {ALMA_CREAM};")
        self._build_ui()

    # ── Compatibility shims (mirror GuruPage's public surface) ────

    def set_friction_pipeline(self, _pipeline) -> None:  # noqa: D401
        """No-op; legacy GuruPage wires this. WIP page ignores it."""
        return None

    def set_content_pipeline(self, _pipeline) -> None:
        """No-op."""
        return None

    def set_effectiveness_tracker(self, _tracker) -> None:
        """No-op."""
        return None

    def set_drilldown_panel(self, _panel) -> None:
        """No-op."""
        return None

    def set_guru_client(self, _client) -> None:
        """No-op."""
        return None

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self) -> None:
        """Lay out header banner + content card + escape-hatch button."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(12)

        # Page header
        header = QLabel("Guru Knowledge Base")
        header.setObjectName("PageHeader")
        outer.addWidget(header)

        sub = QLabel(
            "Friction analysis · Content drafts · Effectiveness tracking"
        )
        sub.setObjectName("PageSubheader")
        outer.addWidget(sub)

        # Main WIP content card
        outer.addWidget(self._build_content_card(), 1)

        # Escape-hatch row at the bottom
        outer.addLayout(self._build_escape_hatch_row())

    def _build_content_card(self) -> QFrame:
        """Single rounded card with copy on the left, wireframe on right."""
        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 14px;
            }}
            """
        )
        apply_card_shadow(card)
        outer = QHBoxLayout(card)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(28)

        outer.addLayout(self._build_copy_column(), 2)
        outer.addWidget(self._build_mockup(), 3)

        return card

    def _build_copy_column(self) -> QVBoxLayout:
        """Title + body copy + planned-features bullet list."""
        col = QVBoxLayout()
        col.setSpacing(10)

        emoji = QLabel("📚")
        emoji.setStyleSheet("font-size: 36px; border: none;")
        col.addWidget(emoji)

        title = QLabel("Coming soon")
        title.setStyleSheet(
            f"font-size: 26px; font-weight: 700; color: {ALMA_TEXT_DARK}; "
            f"border: none;"
        )
        col.addWidget(title)

        body = QLabel(
            "We're rebuilding the Guru integration around closed-loop "
            "friction analysis. The data model and pipelines are wired, "
            "but the UI is still being polished. Here's what you'll see "
            "in this page when it ships:"
        )
        body.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_MID}; border: none;"
        )
        body.setWordWrap(True)
        col.addWidget(body)

        # Bullet list of planned features
        list_frame = QFrame()
        list_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        list_lay = QVBoxLayout(list_frame)
        list_lay.setContentsMargins(0, 6, 0, 0)
        list_lay.setSpacing(6)
        for feat in PLANNED_FEATURES:
            row = QLabel(f"•  {feat}")
            row.setStyleSheet(
                f"font-size: 12px; color: {ALMA_TEXT_DARK}; border: none;"
            )
            list_lay.addWidget(row)
        col.addWidget(list_frame)

        col.addStretch()
        return col

    def _build_mockup(self) -> QWidget:
        """Right column: the static QPainter wireframe."""
        wrap = QFrame()
        wrap.setStyleSheet(
            f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border: 1px dashed {ALMA_BORDER};
                border-radius: 10px;
            }}
            """
        )
        wrap.setMinimumSize(420, 360)
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._mockup = WorkbenchMockupWidget()
        layout.addWidget(self._mockup)

        return wrap

    def _build_escape_hatch_row(self) -> QHBoxLayout:
        """Bottom row: small note + "Enable experimental UI" button."""
        row = QHBoxLayout()
        row.setSpacing(10)

        note = QLabel(
            "Want to preview the in-progress UI? Enable it below "
            "(restart required). Some tabs are incomplete."
        )
        note.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;"
        )
        note.setWordWrap(True)
        row.addWidget(note, 1)

        self._enable_btn = QPushButton("Enable experimental UI")
        self._enable_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 8px 18px;
                font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            """
        )
        self._enable_btn.setCursor(Qt.PointingHandCursor)
        self._enable_btn.clicked.connect(self._on_enable_experimental)
        row.addWidget(self._enable_btn)

        return row

    # ── Settings flag toggle ─────────────────────────────────────

    def _on_enable_experimental(self) -> None:
        """Confirm and set the experimental flag; prompt for restart."""
        confirm = QMessageBox.question(
            self,
            "Enable experimental UI?",
            "Switch to the in-progress Guru UI? Some tabs are incomplete; "
            "you can flip the flag back via Settings → System.\n\n"
            "App restart required for the change to take effect.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            from src.data.settings_manager import (
                load_settings,
                save_settings,
            )
            settings = load_settings()
            guru_block = dict(settings.get("guru", {}))
            guru_block["experimental_ui_enabled"] = True
            settings["guru"] = guru_block
            save_settings(settings)
        except Exception as exc:
            logger.warning(
                "Failed to enable experimental UI flag: %s", exc
            )
            QMessageBox.warning(
                self,
                "Could not save",
                f"Settings save failed:\n{exc}",
            )
            return

        QMessageBox.information(
            self,
            "Restart required",
            "The experimental Guru UI is enabled. Restart Alma Insights "
            "to load it.",
        )


# ═══════════════════════════════════════════════════════════════════
#  WorkbenchMockupWidget — the static QPainter wireframe
# ═══════════════════════════════════════════════════════════════════

class WorkbenchMockupWidget(QWidget):
    """Static wireframe of the planned Guru Workbench layout.

    Renders five labelled regions matching the original Phase 4 spec:

    .. code-block:: text

       ┌──────────────┬──────────────────────────────────┐
       │ Card list    │ Status bar                       │
       │              ├──────────────────────────────────┤
       │ TRC scope    │ Phase progress (3 bars)          │
       │              ├──────────────────────────────────┤
       │ Context      │ Redline preview                  │
       │              │                                  │
       │              │ [Copy] [Save .md] [Save .html]   │
       └──────────────┴──────────────────────────────────┘

    Non-interactive — mouse events are absorbed silently. The "Coming
    Soon" overlay is painted last so it sits on top of all regions.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(380, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # The whole widget paints. No internal state.

    def paintEvent(self, _event) -> None:  # noqa: N802
        """Render the static wireframe + WIP overlay."""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            self._paint_background(painter)
            self._paint_left_column(painter)
            self._paint_right_column(painter)
            self._paint_overlay(painter)
        finally:
            painter.end()

    # ── Region painters ─────────────────────────────────────────

    def _paint_background(self, painter: QPainter) -> None:
        """Off-white background for the wireframe area."""
        painter.fillRect(self.rect(), QColor(ALMA_CHART_BG))

    def _paint_left_column(self, painter: QPainter) -> None:
        """Left panel: card list, TRC scope checkboxes, context box."""
        w = self.width()
        h = self.height()
        col_left = 12
        col_right = int(w * 0.42)
        col_top = 12

        # Three stacked boxes — divide the column vertically into thirds.
        box_height = (h - 32) // 3

        for i, label in enumerate(("Cards", "TRC scope", "Context")):
            top = col_top + i * (box_height + 4)
            self._paint_panel(
                painter,
                QRectF(col_left, top, col_right - col_left, box_height),
                label,
            )

            # Sketch contents inside each box.
            inner_top = top + 28
            inner_left = col_left + 14
            inner_w = (col_right - col_left) - 28
            if label == "Cards":
                self._paint_card_rows(painter, inner_left, inner_top,
                                      inner_w, count=3)
            elif label == "TRC scope":
                self._paint_checkbox_rows(painter, inner_left, inner_top,
                                           inner_w, count=4)
            else:  # Context
                self._paint_text_lines(painter, inner_left, inner_top,
                                        inner_w, count=3)

    def _paint_right_column(self, painter: QPainter) -> None:
        """Right panel: status bar, phase bars, redline preview, buttons."""
        w = self.width()
        h = self.height()
        col_left = int(w * 0.44)
        col_right = w - 12
        col_top = 12

        # Status strip
        status_h = 26
        self._paint_panel(
            painter,
            QRectF(col_left, col_top, col_right - col_left, status_h),
            "Status: Ready",
        )

        # Phase progress
        phases_top = col_top + status_h + 6
        phases_h = 70
        self._paint_panel(
            painter,
            QRectF(col_left, phases_top, col_right - col_left, phases_h),
            "Phase progress",
        )
        self._paint_phase_bars(painter, col_left + 12, phases_top + 28,
                                col_right - col_left - 24)

        # Redline preview
        preview_top = phases_top + phases_h + 6
        preview_h = h - preview_top - 36 - 12
        self._paint_panel(
            painter,
            QRectF(col_left, preview_top, col_right - col_left, preview_h),
            "Redline preview",
        )
        self._paint_redline_lines(painter, col_left + 12, preview_top + 28,
                                    col_right - col_left - 24,
                                    preview_h - 36)

        # Export buttons row
        button_top = preview_top + preview_h + 6
        self._paint_buttons(painter, col_left, button_top,
                             col_right - col_left)

    def _paint_overlay(self, painter: QPainter) -> None:
        """Semi-transparent diagonal "WORK IN PROGRESS" stamp."""
        stamp_color = QColor(ALMA_WARNING)
        stamp_color.setAlpha(60)

        painter.save()
        # Centered, no rotation — keeps the stamp readable at any size.
        painter.setPen(QPen(stamp_color, 3))
        font = QFont("Segoe UI", 22, QFont.Bold)
        font.setLetterSpacing(QFont.PercentageSpacing, 110)
        painter.setFont(font)
        painter.drawText(
            self.rect(),
            Qt.AlignCenter,
            "PREVIEW · WORK IN PROGRESS",
        )
        painter.restore()

    # ── Wireframe primitives ─────────────────────────────────────

    @staticmethod
    def _paint_panel(painter: QPainter, rect: QRectF, label: str) -> None:
        """Outlined rectangle with a small label in the upper-left."""
        painter.setBrush(QBrush(QColor(ALMA_BG_ELEVATED)))
        painter.setPen(QPen(QColor(ALMA_BORDER), 1))
        painter.drawRoundedRect(rect, 6, 6)

        painter.setPen(QColor(ALMA_TEXT_MID))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(
            QRectF(rect.left() + 8, rect.top() + 6, rect.width() - 16, 18),
            Qt.AlignLeft | Qt.AlignTop,
            label,
        )

    @staticmethod
    def _paint_card_rows(
        painter: QPainter, x: int, y: int, w: int, count: int
    ) -> None:
        """Sketch ``count`` card-list rows: dot + title bar + score bar."""
        painter.setPen(Qt.NoPen)
        for i in range(count):
            row_y = y + i * 24
            # Bullet dot
            painter.setBrush(QBrush(QColor(ALMA_GREEN_LIGHT)))
            painter.drawEllipse(QPointF(x + 6, row_y + 9), 3.5, 3.5)
            # Title bar
            painter.setBrush(QBrush(QColor(ALMA_BORDER_LIGHT)))
            painter.drawRoundedRect(
                QRectF(x + 18, row_y + 4, w * 0.55, 11), 3, 3
            )
            # Score bar
            painter.setBrush(QBrush(QColor(ALMA_INFO)))
            painter.drawRoundedRect(
                QRectF(x + 18 + w * 0.55 + 6, row_y + 6, w * 0.18, 6),
                3, 3,
            )

    @staticmethod
    def _paint_checkbox_rows(
        painter: QPainter, x: int, y: int, w: int, count: int
    ) -> None:
        """Sketch ``count`` checkbox rows: square + label bar."""
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1))
        for i in range(count):
            row_y = y + i * 20
            painter.drawRect(QRectF(x + 4, row_y + 4, 11, 11))
            # First two checkboxes filled.
            if i < 2:
                painter.fillRect(
                    QRectF(x + 6, row_y + 6, 7, 7),
                    QColor(ALMA_GREEN_LIGHT),
                )
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(ALMA_BORDER_LIGHT)))
            painter.drawRoundedRect(
                QRectF(x + 22, row_y + 5, w * 0.6, 9), 3, 3
            )
            painter.setPen(QPen(QColor(ALMA_TEXT_LIGHT), 1))
            painter.setBrush(Qt.NoBrush)

    @staticmethod
    def _paint_text_lines(
        painter: QPainter, x: int, y: int, w: int, count: int
    ) -> None:
        """Sketch ``count`` text lines (varying widths) for a textarea."""
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(ALMA_BORDER_LIGHT)))
        widths = [w * 0.85, w * 0.72, w * 0.55]
        for i in range(min(count, len(widths))):
            painter.drawRoundedRect(
                QRectF(x, y + i * 14, widths[i], 7), 3, 3
            )

    @staticmethod
    def _paint_phase_bars(
        painter: QPainter, x: int, y: int, w: int
    ) -> None:
        """Three horizontal progress bars at decreasing fill levels."""
        bar_h = 8
        gap = 8
        fills = [0.92, 0.55, 0.18]  # Search done, Per-card mid, Synth begun
        labels = ["Search", "Per-card", "Synthesis"]
        painter.setFont(QFont("Segoe UI", 8))
        for i, (fill, label) in enumerate(zip(fills, labels)):
            top = y + i * (bar_h + gap)
            painter.setPen(Qt.NoPen)
            # Track
            painter.setBrush(QBrush(QColor(ALMA_BG_INSET)))
            painter.drawRoundedRect(QRectF(x, top, w, bar_h), 4, 4)
            # Fill
            painter.setBrush(QBrush(QColor(ALMA_GREEN_LIGHT)))
            painter.drawRoundedRect(
                QRectF(x, top, w * fill, bar_h), 4, 4
            )
            # Label
            painter.setPen(QColor(ALMA_TEXT_MID))
            painter.drawText(
                QRectF(x + w + 6, top - 1, 60, bar_h + 4),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )

    @staticmethod
    def _paint_redline_lines(
        painter: QPainter, x: int, y: int, w: int, h: int
    ) -> None:
        """Sketch a redline diff: 5 lines, alternating green/red highlights."""
        line_h = 12
        max_lines = max(2, min(6, int(h / line_h)))
        line_widths = [w * 0.84, w * 0.62, w * 0.71, w * 0.55, w * 0.66, w * 0.48]
        green = QColor(ALMA_GREEN_LIGHT)
        green.setAlpha(80)
        red_color = QColor("#C41E1E")
        red_color.setAlpha(70)
        gray = QColor(ALMA_BORDER_LIGHT)

        painter.setPen(Qt.NoPen)
        for i in range(max_lines):
            if i >= len(line_widths):
                break
            top = y + i * line_h
            # Alternate every other row gets a tinted background.
            if i % 3 == 0:
                painter.setBrush(QBrush(green))
                painter.drawRoundedRect(
                    QRectF(x - 2, top - 1, w * 0.9, line_h - 2), 3, 3
                )
            elif i % 3 == 1:
                painter.setBrush(QBrush(red_color))
                painter.drawRoundedRect(
                    QRectF(x - 2, top - 1, w * 0.78, line_h - 2), 3, 3
                )
            painter.setBrush(QBrush(gray))
            painter.drawRoundedRect(
                QRectF(x, top + 2, line_widths[i], 6), 3, 3
            )

    @staticmethod
    def _paint_buttons(
        painter: QPainter, x: int, y: int, w: int
    ) -> None:
        """Three pill-shaped placeholder buttons left-aligned."""
        labels = ["Copy", "Save .md", "Save .html"]
        painter.setPen(Qt.NoPen)
        bx = x
        for label in labels:
            font = QFont("Segoe UI", 9, QFont.Bold)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(label)
            button_w = text_w + 22
            painter.setBrush(QBrush(QColor(ALMA_BG_INSET)))
            painter.drawRoundedRect(QRectF(bx, y, button_w, 24), 6, 6)
            painter.setPen(QColor(ALMA_TEXT_MID))
            painter.drawText(
                QRectF(bx, y, button_w, 24),
                Qt.AlignCenter,
                label,
            )
            painter.setPen(Qt.NoPen)
            bx += button_w + 6
