"""AI Reports — Finding card widget (R1.6).

Collapsible card rendering one `Finding` from a `Report`. Visually maps
to each row in the 3.24.26 Updated_AI_Reports_Analysis_Canvas.png — title
+ severity badge + summary line, expandable drilldown with evidence
chips and the markdown body.

Emits two signals:
- `clicked(dict)`  — the Finding's `to_dict()` payload, consumed by
                     `evidence_panel.show_finding()`.
- `expanded(bool)` — when the user opens or collapses the body.

Public API
----------
- `FindingCard(finding: Finding, parent=None)`
- `FindingCard.set_finding(finding)` — re-render with new data
- `FindingCard.expand(open: bool)` — programmatic toggle

Dependencies
------------
- PySide6.QtWidgets, PySide6.QtCore
- src.data.report_schema (Finding, EvidenceChip, Severity)
- src.ui.theme
- src.ui.widgets.severity_badge
- src.ui.widgets.markdown_viewer (lazy import — body drilldown)

Dependents
----------
- src.ui.widgets.report_canvas — lays out a list of FindingCards
- src.ui.pages.ai_reports — wires clicked to evidence_panel.show_finding
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from src.data.report_schema import Finding, EvidenceChip, ChipKind, Severity
from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_MID, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_INFO, ALMA_ERROR,
    apply_card_shadow_soft,
)
from src.ui.widgets.severity_badge import SeverityBadge


# ──────────────────────────────────────────────────────────────────────
# Chip color map by ChipKind — matches reference screenshot palette
# ──────────────────────────────────────────────────────────────────────

_CHIP_BG: dict[ChipKind, str] = {
    ChipKind.METRIC: ALMA_GREEN_SUBTLE,
    ChipKind.TREND:  ALMA_INFO,
    ChipKind.SOURCE: ALMA_BG_INSET,
    ChipKind.COHORT: ALMA_GREEN_LIGHT,
    ChipKind.DRIVER: ALMA_WARNING,
}

_CHIP_FG: dict[ChipKind, str] = {
    ChipKind.METRIC: ALMA_GREEN_DARK,
    ChipKind.TREND:  ALMA_WHITE,
    ChipKind.SOURCE: ALMA_TEXT_DARK,
    ChipKind.COHORT: ALMA_GREEN_DARK,
    ChipKind.DRIVER: ALMA_WHITE,
}


# ──────────────────────────────────────────────────────────────────────
# FindingCard
# ──────────────────────────────────────────────────────────────────────

class FindingCard(QFrame):
    """Collapsible finding card. Click-to-expand; double-click pings
    the evidence panel."""

    clicked = Signal(dict)        # emits Finding.to_dict()
    expanded = Signal(bool)       # True on open, False on collapse

    def __init__(self, finding: Finding, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._finding: Finding = finding
        self._is_expanded = False
        self._body_widget: QWidget | None = None  # built lazily on first expand
        self.setObjectName("FindingCard")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._build_ui()
        apply_card_shadow_soft(self)

    # ── public ─────────────────────────────────────────────────────

    def set_finding(self, finding: Finding) -> None:
        """Re-render the card from a new Finding (used on rehydration)."""
        self._finding = finding
        self._title_lbl.setText(finding.title)
        self._summary_lbl.setText(finding.summary)
        self._severity_badge.set_severity(finding.severity)
        self._confidence_lbl.setText(_fmt_confidence(finding.confidence))
        _clear_layout(self._chips_layout)
        _populate_chips(self._chips_layout, finding.evidence_chips)
        if self._body_widget is not None:
            self._body_widget.deleteLater()
            self._body_widget = None
            self._is_expanded = False
            self._expand_btn.setText("▾ Show details")

    def expand(self, open_: bool) -> None:
        """Programmatic toggle — used by 'expand all' actions."""
        if open_ != self._is_expanded:
            self._toggle_body()

    @property
    def finding(self) -> Finding:
        return self._finding

    # ── UI build ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(_card_qss())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        layout.addLayout(self._build_header_row())
        layout.addWidget(self._build_summary())
        layout.addLayout(self._build_chips_row())
        layout.addWidget(self._build_expand_row())

    def _build_header_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self._title_lbl = QLabel(self._finding.title)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        row.addWidget(self._title_lbl, 1)

        self._severity_badge = SeverityBadge(self._finding.severity)
        row.addWidget(self._severity_badge, 0, Qt.AlignTop)

        self._confidence_lbl = QLabel(_fmt_confidence(self._finding.confidence))
        self._confidence_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-weight: 600;"
        )
        row.addWidget(self._confidence_lbl, 0, Qt.AlignTop)
        return row

    def _build_summary(self) -> QLabel:
        self._summary_lbl = QLabel(self._finding.summary)
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; line-height: 1.5;"
        )
        return self._summary_lbl

    def _build_chips_row(self) -> QHBoxLayout:
        self._chips_layout = QHBoxLayout()
        self._chips_layout.setSpacing(6)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        _populate_chips(self._chips_layout, self._finding.evidence_chips)
        self._chips_layout.addStretch()
        return self._chips_layout

    def _build_expand_row(self) -> QWidget:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 4, 0, 0)
        self._expand_btn = QPushButton("▾ Show details")
        self._expand_btn.setCursor(Qt.PointingHandCursor)
        self._expand_btn.setStyleSheet(_link_button_qss())
        self._expand_btn.clicked.connect(self._toggle_body)
        row.addWidget(self._expand_btn)
        row.addStretch()
        view_btn = QPushButton("View in evidence panel →")
        view_btn.setCursor(Qt.PointingHandCursor)
        view_btn.setStyleSheet(_link_button_qss(strong=True))
        view_btn.clicked.connect(self._emit_clicked)
        row.addWidget(view_btn)
        return wrapper

    # ── interactions ───────────────────────────────────────────────

    def _toggle_body(self) -> None:
        if self._body_widget is None:
            self._body_widget = self._build_body()
            self.layout().addWidget(self._body_widget)
        self._is_expanded = not self._is_expanded
        self._body_widget.setVisible(self._is_expanded)
        self._expand_btn.setText(
            "▴ Hide details" if self._is_expanded else "▾ Show details"
        )
        self.expanded.emit(self._is_expanded)

    def _build_body(self) -> QWidget:
        # Lazy import — markdown_viewer pulls QtWebEngine alternatives
        from src.ui.widgets.markdown_viewer import MarkdownViewer
        body = MarkdownViewer()
        body.set_markdown(self._finding.body_md or "_No additional detail._")
        body.setStyleSheet(
            f"QTextBrowser {{"
            f" background: {ALMA_BG_INSET};"
            f" color: {ALMA_TEXT_DARK};"
            f" border: none; border-radius: 6px; padding: 10px;"
            f" font-size: 12px; }}"
        )
        body.setMinimumHeight(120)
        body.setVisible(False)
        return body

    def _emit_clicked(self) -> None:
        self.clicked.emit(self._finding.to_dict())

    def mousePressEvent(self, event) -> None:
        # Single click on card area (not on buttons) selects the finding.
        # Buttons handle their own clicks via setCursor + clicked signals.
        if event.button() == Qt.LeftButton:
            self._emit_clicked()
        super().mousePressEvent(event)


# ──────────────────────────────────────────────────────────────────────
# Module-level helpers
# ──────────────────────────────────────────────────────────────────────

def _populate_chips(layout: QHBoxLayout, chips: list[EvidenceChip]) -> None:
    """Add chip widgets to the layout. Caller must add a stretch after."""
    for chip in chips:
        layout.addWidget(_chip_widget(chip))


def _chip_widget(chip: EvidenceChip) -> QLabel:
    bg = _CHIP_BG.get(chip.kind, ALMA_BG_INSET)
    fg = _CHIP_FG.get(chip.kind, ALMA_TEXT_DARK)
    text = f"{chip.label}: {chip.value}" if chip.label else chip.value
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"QLabel {{"
        f" background: {bg};"
        f" color: {fg};"
        f" border: none; border-radius: 8px;"
        f" padding: 3px 9px;"
        f" font-size: 11px; font-weight: 600;"
        f" }}"
    )
    return lbl


def _clear_layout(layout: QHBoxLayout) -> None:
    """Remove all widgets from a layout (used on set_finding re-render)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _fmt_confidence(c: float) -> str:
    """e.g. 0.85 → 'Confidence 85%'."""
    return f"Confidence {int(round(max(0.0, min(1.0, c)) * 100))}%"


def _card_qss() -> str:
    return (
        f"#FindingCard {{"
        f" background: {ALMA_WHITE};"
        f" border: 1px solid {ALMA_BORDER_LIGHT};"
        f" border-radius: 12px;"
        f" }}"
        f"#FindingCard:hover {{"
        f" border-color: {ALMA_GREEN_LIGHT};"
        f" }}"
    )


def _link_button_qss(*, strong: bool = False) -> str:
    color = ALMA_GREEN_DARK if strong else ALMA_TEXT_MID
    weight = 700 if strong else 500
    return (
        f"QPushButton {{"
        f" background: transparent;"
        f" color: {color};"
        f" border: none;"
        f" font-size: 11px;"
        f" font-weight: {weight};"
        f" padding: 2px 4px;"
        f" }}"
        f"QPushButton:hover {{ color: {ALMA_GREEN_DARK}; }}"
    )
