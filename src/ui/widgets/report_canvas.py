"""AI Reports — Report canvas widget (R1.6/R1.8 wiring).

Renders a structured `Report` as the Analysis Canvas in the AI Reports
page. Replaces the previous "raw markdown blob" by laying out:

- Header (report title, scope chips, accuracy badge)
- Executive summary block
- Stack of `FindingCard` widgets (one per finding)
- Fallback markdown viewer when `report.findings` is empty (legacy /
  parse-failure path; preserves existing UX)

Emits two signals consumed by the page:

- `finding_clicked(dict)`   — finding payload for `evidence_panel.show_finding`
- `metadata_changed(dict)`  — report metadata for `evidence_panel.show_metadata`
- `ticket_clicked(str)`     — re-emitted from MarkdownViewer for evidence panel

Public API
----------
- `ReportCanvas(parent=None)`
- `ReportCanvas.set_report(report: Report)`
- `ReportCanvas.set_markdown(md: str)` — legacy fallback
- `ReportCanvas.clear()`
- `ReportCanvas.expand_all(open_: bool)`

Reference: 3.24.26 Updated_AI_Reports_Analysis_Canvas.png. Plan section
R1.8 in the conversation history.

Dependencies
------------
- PySide6.QtWidgets / QtCore
- src.data.report_schema (Report)
- src.ui.theme
- src.ui.widgets.finding_card.FindingCard
- src.ui.widgets.markdown_viewer.MarkdownViewer (lazy)
- src.ui.widgets.severity_badge (indirectly via FindingCard)

Dependents
----------
- src.ui.pages.ai_reports — embeds this in the analysis-canvas tab.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QStackedWidget, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from src.data.report_schema import Report, Severity
from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)
from src.ui.widgets.finding_card import FindingCard


# ──────────────────────────────────────────────────────────────────────
# Stack indices
# ──────────────────────────────────────────────────────────────────────

VIEW_EMPTY = 0
VIEW_STRUCTURED = 1
VIEW_LEGACY = 2


# ──────────────────────────────────────────────────────────────────────
# Widget
# ──────────────────────────────────────────────────────────────────────

class ReportCanvas(QWidget):
    """Structured report canvas with legacy markdown fallback."""

    finding_clicked = Signal(dict)
    metadata_changed = Signal(dict)
    ticket_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._report: Report | None = None
        self._cards: list[FindingCard] = []
        self._build_ui()
        self._stack.setCurrentIndex(VIEW_EMPTY)

    # ── public ─────────────────────────────────────────────────────

    def set_report(self, report: Report) -> None:
        """Render a structured Report. Falls back to markdown if findings empty."""
        self._report = report
        if not report.findings:
            self.set_markdown(report.raw_markdown or "_No findings produced._")
            self._emit_metadata(report)
            return

        self._render_structured(report)
        self._emit_metadata(report)
        self._stack.setCurrentIndex(VIEW_STRUCTURED)

    def set_markdown(self, md: str) -> None:
        """Legacy / fallback path — render raw markdown."""
        self._legacy_viewer.set_markdown(md or "")
        self._stack.setCurrentIndex(VIEW_LEGACY)

    def clear(self) -> None:
        """Reset to empty state."""
        self._report = None
        self._clear_cards()
        self._legacy_viewer.clear_content()
        self._stack.setCurrentIndex(VIEW_EMPTY)

    def expand_all(self, open_: bool) -> None:
        """Expand or collapse every finding card at once."""
        for card in self._cards:
            card.expand(open_)

    def toPlainText(self) -> str:
        """Backward-compat shim — older save/copy paths called
        `_output_area.toPlainText()` against the bare MarkdownViewer.

        Returns the current report rendered as flat text. Prefers
        structured findings → markdown stitch; falls back to the legacy
        viewer; empty string if neither populated.
        """
        if self._report and self._report.findings:
            from src.data.ai_report_pipeline import _dump_findings_markdown
            return _dump_findings_markdown(self._report)
        if self._report and self._report.raw_markdown:
            return self._report.raw_markdown
        try:
            return self._legacy_viewer.toPlainText()
        except Exception:
            return ""

    def get_markdown(self) -> str:
        """Same as toPlainText — explicit name for new callers."""
        return self.toPlainText()

    @property
    def current_report(self) -> Report | None:
        return self._report

    # ── UI build ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_empty_view())
        self._stack.addWidget(self._build_structured_view())
        self._stack.addWidget(self._build_legacy_view())
        layout.addWidget(self._stack)

    def _build_empty_view(self) -> QWidget:
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(24, 48, 24, 24)
        msg = QLabel(
            "Pick a prompt and click Generate Report to populate the canvas."
        )
        msg.setAlignment(Qt.AlignCenter)
        msg.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_LIGHT};")
        wl.addWidget(msg)
        wl.addStretch()
        return w

    def _build_structured_view(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {ALMA_CREAM}; }}")

        container = QWidget()
        container.setStyleSheet(f"background: {ALMA_CREAM};")
        self._structured_layout = QVBoxLayout(container)
        self._structured_layout.setContentsMargins(16, 12, 16, 16)
        self._structured_layout.setSpacing(12)

        self._header_box = self._build_header_box()
        self._structured_layout.addWidget(self._header_box)

        self._summary_box = self._build_summary_box()
        self._structured_layout.addWidget(self._summary_box)

        # Card area placeholder — populated in _render_structured
        self._cards_holder = QWidget()
        cards_layout = QVBoxLayout(self._cards_holder)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)
        self._cards_layout = cards_layout
        self._structured_layout.addWidget(self._cards_holder)

        self._structured_layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def _build_header_box(self) -> QFrame:
        box = QFrame()
        box.setStyleSheet(_header_qss())
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        self._title_lbl = QLabel("Report")
        self._title_lbl.setStyleSheet(
            f"font-size: 18px; font-weight: 800; color: {ALMA_GREEN_DARK};"
        )
        layout.addWidget(self._title_lbl)

        self._chips_row = QHBoxLayout()
        self._chips_row.setSpacing(6)
        self._chips_row.addStretch()
        layout.addLayout(self._chips_row)
        return box

    def _build_summary_box(self) -> QFrame:
        box = QFrame()
        box.setStyleSheet(
            f"QFrame {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};"
            f" border-radius: 10px; }}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("EXECUTIVE SUMMARY")
        title.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_LIGHT};"
            f" letter-spacing: 1px;"
        )
        layout.addWidget(title)
        self._summary_lbl = QLabel("")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_DARK}; line-height: 1.5;"
        )
        layout.addWidget(self._summary_lbl)
        box.setVisible(False)
        return box

    def _build_legacy_view(self) -> QWidget:
        # Lazy import so QtWebEngineWidgets isn't required just to import this module
        from src.ui.widgets.markdown_viewer import MarkdownViewer
        self._legacy_viewer = MarkdownViewer()
        self._legacy_viewer.setStyleSheet(
            f"QTextBrowser {{ background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};"
            f" border: none; border-radius: 8px; padding: 14px; font-size: 13px; }}"
        )
        self._legacy_viewer.ticket_clicked.connect(self.ticket_clicked.emit)
        return self._legacy_viewer

    # ── render path ────────────────────────────────────────────────

    def _render_structured(self, report: Report) -> None:
        self._title_lbl.setText(report.title or "Report")
        self._summary_box.setVisible(bool(report.executive_summary))
        self._summary_lbl.setText(report.executive_summary or "")
        self._render_header_chips(report)
        self._render_cards(report)

    def _render_header_chips(self, report: Report) -> None:
        _clear_layout(self._chips_row, keep_stretch=True)
        scope = report.scope or {}
        ticket_count = scope.get("ticket_count") or scope.get("tickets")
        if ticket_count is not None:
            self._chips_row.insertWidget(
                self._chips_row.count() - 1,
                _scope_chip(f"{ticket_count} tickets"),
            )
        if report.pipeline_kind == "multi_bridge":
            chip = _scope_chip(
                f"{report.bridges_used}-bridge pipeline · "
                f"{report.specialist_count} specialists"
            )
            self._chips_row.insertWidget(self._chips_row.count() - 1, chip)
        if report.accuracy_score is not None:
            self._chips_row.insertWidget(
                self._chips_row.count() - 1,
                _accuracy_chip(report.accuracy_score, len(report.accuracy_flags)),
            )

    def _render_cards(self, report: Report) -> None:
        self._clear_cards()
        for finding in report.findings:
            card = FindingCard(finding)
            card.clicked.connect(self.finding_clicked.emit)
            self._cards_layout.addWidget(card)
            self._cards.append(card)

    def _clear_cards(self) -> None:
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        _clear_layout(self._cards_layout)

    # ── metadata + signals ─────────────────────────────────────────

    def _emit_metadata(self, report: Report) -> None:
        scope = report.scope or {}
        bridges = report.bridges_used if report.pipeline_kind == "multi_bridge" else 1
        spec = report.specialist_count if report.pipeline_kind == "multi_bridge" else 0
        meta = {
            "pipeline": report.pipeline_kind,
            "specialists": spec,
            "tickets": scope.get("ticket_count") or scope.get("tickets") or "-",
            "trc categories": _format_trc_count(scope, report),
            "generated": report.generated_at or "-",
            "cost": _format_cost(report.cost_usd),
            "bridges": bridges,
            "accuracy_score": report.accuracy_score,
            "accuracy_flags": list(report.accuracy_flags),
            "duration_sec": report.duration_sec,
        }
        self.metadata_changed.emit(meta)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _clear_layout(layout, *, keep_stretch: bool = False) -> None:
    """Drop every widget in the layout. Preserves a trailing stretch when asked."""
    keep_last = keep_stretch and layout.count() > 0
    end = layout.count() - (1 if keep_last else 0)
    for _ in range(end):
        item = layout.takeAt(0)
        widget = item.widget() if item else None
        if widget is not None:
            widget.deleteLater()


def _scope_chip(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"QLabel {{ background: {ALMA_GREEN_SUBTLE}; color: {ALMA_GREEN_DARK};"
        f" border-radius: 10px; padding: 3px 10px; font-size: 11px;"
        f" font-weight: 600; }}"
    )
    return lbl


def _accuracy_chip(score: float, flag_count: int) -> QLabel:
    pct = int(round(max(0.0, min(1.0, score)) * 100))
    text = f"{pct}% verified"
    if flag_count:
        text += f" · {flag_count} evidence flag{'s' if flag_count != 1 else ''}"
    bg = _accuracy_color(score)
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"QLabel {{ background: {bg}; color: {ALMA_WHITE};"
        f" border-radius: 10px; padding: 3px 10px; font-size: 11px;"
        f" font-weight: 700; letter-spacing: 0.3px; }}"
    )
    return lbl


def _accuracy_color(score: float) -> str:
    if score >= 0.90:
        return ALMA_SUCCESS
    if score >= 0.75:
        return ALMA_WARNING
    return ALMA_ERROR


def _format_cost(cost: float) -> str:
    if cost <= 0:
        return "-"
    if cost < 1.0:
        return f"${cost:.2f}"
    return f"${cost:.2f}"


def _format_trc_count(scope: dict, report: Report) -> str:
    trcs = scope.get("trcs")
    if isinstance(trcs, list):
        return str(len(trcs))
    if isinstance(trcs, int):
        return str(trcs)
    # Derive from findings as a fallback.
    counted = {trc for f in report.findings for trc in (f.trcs_touched or [])}
    return str(len(counted)) if counted else "-"


def _header_qss() -> str:
    return (
        f"QFrame {{ background: {ALMA_BG_ELEVATED}; border: 1px solid {ALMA_BORDER_LIGHT};"
        f" border-radius: 12px; }}"
    )
