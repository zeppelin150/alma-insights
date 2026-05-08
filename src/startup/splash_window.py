"""
Alma Insights — Startup Splash Window

A branded splash that streams CheckResults from a Checker into stacked
SplashRow widgets. Critical-check status gates the Continue button.

Design match with the reference mock (see docs/STARTUP_SPLASH.md):
  * Dark Alma-green background
  * Centred cream card with header + checks + footer
  * Version string top-right of the header
  * "Submit support ticket" + "Continue" buttons in the footer

Public API:
    SplashWindow(app_version)
        .run(checker)                       start streaming results
        .continue_clicked                   Signal (emitted on Continue)
        .support_clicked                    Signal (emitted on Support)

Threading model: checks run on the main thread, one per
QTimer.singleShot(0, ...). Total splash time ≈ sum of check durations
plus a small UI settle delay between each. Suitable because our checks
are either I/O-light or already bounded by their own timeouts.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from src.startup.checker import CheckResult, Checker
from src.startup.splash_row import SplashRow
from src.ui.theme import (
    ALMA_BORDER,
    ALMA_CREAM,
    ALMA_ERROR,
    ALMA_GREEN_DARK,
    ALMA_GREEN_LIGHT,
    ALMA_GREEN_MID,
    ALMA_SUCCESS,
    ALMA_TEXT_ON_DARK,
    ALMA_WARNING,
)


class SplashWindow(QDialog):
    """
    Modal splash shown before the main window. Closes automatically only
    if the caller invokes .accept() — clicking "Continue" does so.
    """

    continue_clicked = Signal()
    support_clicked = Signal()

    def __init__(self, app_version: str = "unknown", parent: QWidget | None = None):
        super().__init__(parent)
        self._app_version = app_version
        self._checker: Checker | None = None
        self._rows_layout: QVBoxLayout | None = None
        self._continue_btn: QPushButton | None = None
        self._summary_label: QLabel | None = None

        self.setWindowTitle("Alma Insights — Starting up")
        self.setModal(True)
        self.setMinimumSize(640, 620)
        self._apply_palette()
        self._build_layout()

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def run(self, checker: Checker, step_delay_ms: int = 60) -> None:
        """Begin streaming results from the checker into the UI."""
        self._checker = checker
        self._schedule_next(step_delay_ms)

    def add_result(self, result: CheckResult) -> None:
        """Append a row to the UI (also called directly from tests)."""
        assert self._rows_layout is not None
        self._rows_layout.addWidget(SplashRow(result))

    # ──────────────────────────────────────────────────────────────
    # Streaming loop
    # ──────────────────────────────────────────────────────────────

    def _schedule_next(self, delay_ms: int) -> None:
        QTimer.singleShot(delay_ms, lambda: self._run_one(delay_ms))

    def _run_one(self, delay_ms: int) -> None:
        assert self._checker is not None
        result = self._checker.run_next(on_result=self.add_result)
        if result is None:
            self._finish()
            return
        self._schedule_next(delay_ms)

    def _finish(self) -> None:
        assert self._checker is not None
        summary = self._checker.summary()
        passed = self._checker.passed_critical
        self._summary_label.setText(self._summary_text(summary, passed))
        self._summary_label.setStyleSheet(
            f"color: {ALMA_SUCCESS if passed else ALMA_WARNING};"
            "font-size: 13px; padding: 8px 0;"
        )
        if passed:
            self._continue_btn.setEnabled(True)
            self._continue_btn.setDefault(True)
            self._continue_btn.setFocus()
        # Mount the "Install & Restart" widget if check #4 stashed a
        # pending update payload. Done here (not during _build_layout)
        # so the widget only appears once we have data — and so we
        # don't have to duplicate the cache-read logic in two places.
        self._maybe_mount_update_action()

    def _maybe_mount_update_action(self) -> None:
        """Mount the update action widget if check #4 found a newer release.

        No-op when there is no pending update or one was already
        mounted (idempotent — safe to call multiple times).
        """
        if self._update_action_widget is not None:
            return
        try:
            from src.startup.checks.updates import get_pending_update
            payload = get_pending_update()
        except Exception as exc:  # noqa: BLE001 — splash must never crash here
            return
        if not payload:
            return

        from src.startup.update_action_widget import UpdateActionWidget
        widget = UpdateActionWidget(payload, parent=self)
        widget.busy_changed.connect(self._on_update_action_busy)
        self._update_action_layout.addWidget(widget)
        self._update_action_widget = widget

    def _on_update_action_busy(self, busy: bool) -> None:
        """While an install is staging, don't let the user dismiss the splash."""
        if self._continue_btn is not None:
            self._continue_btn.setEnabled(not busy)

    # ──────────────────────────────────────────────────────────────
    # Layout
    # ──────────────────────────────────────────────────────────────

    def _apply_palette(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.Window, Qt.GlobalColor.transparent)
        self.setPalette(palette)
        # Scope to the dialog itself so descendants (labels, buttons) don't
        # inherit the dark-green background and render as banded boxes.
        self.setObjectName("splashDialog")
        self.setStyleSheet(
            f"#splashDialog {{ background-color: {ALMA_GREEN_DARK}; }}"
        )

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("splashCard")
        card.setStyleSheet(
            f"#splashCard {{ background-color: {ALMA_GREEN_MID}; "
            f"border: 1px solid {ALMA_GREEN_LIGHT}; border-radius: 8px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(40, 36, 40, 32)
        card_layout.setSpacing(20)

        card_layout.addLayout(self._build_header())
        card_layout.addWidget(self._build_divider())
        card_layout.addWidget(self._build_rows_scroll(), 1)
        card_layout.addWidget(self._build_divider())
        card_layout.addLayout(self._build_footer())

        root.addWidget(card, 1)

    def _build_header(self) -> QVBoxLayout:
        header = QVBoxLayout()
        header.setSpacing(4)
        title = QLabel("ALMA INSIGHTS")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title_font.setLetterSpacing(QFont.AbsoluteSpacing, 4.0)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {ALMA_CREAM}; letter-spacing: 4px;")
        header.addWidget(title)

        tagline = QLabel("RCM ISSUE ANALYSIS")
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(
            f"color: {ALMA_TEXT_ON_DARK}; opacity: 0.75; font-size: 11px; letter-spacing: 3px;"
        )
        header.addWidget(tagline)

        version = QLabel(self._app_version)
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(
            f"color: {ALMA_TEXT_ON_DARK}; opacity: 0.50; font-size: 10px; padding-top: 4px;"
        )
        header.addWidget(version)
        return header

    def _build_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {ALMA_GREEN_LIGHT}; background-color: {ALMA_GREEN_LIGHT};")
        line.setFixedHeight(1)
        return line

    def _build_rows_scroll(self) -> QScrollArea:
        container = QWidget()
        self._rows_layout = QVBoxLayout(container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return scroll

    def _build_footer(self) -> QVBoxLayout:
        footer = QVBoxLayout()
        footer.setSpacing(10)

        self._summary_label = QLabel("Running checks…")
        self._summary_label.setAlignment(Qt.AlignCenter)
        self._summary_label.setStyleSheet(
            f"color: {ALMA_TEXT_ON_DARK}; opacity: 0.70; font-size: 12px;"
        )
        footer.addWidget(self._summary_label)

        # Slot for the "Install & Restart" widget. We keep a reference
        # to the layout so _maybe_mount_update_action() can insert a
        # widget here after checks finish (only when a pending update
        # was found by check #4).
        self._update_action_layout = QVBoxLayout()
        self._update_action_layout.setContentsMargins(0, 0, 0, 0)
        self._update_action_widget: QWidget | None = None
        footer.addLayout(self._update_action_layout)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)

        support = QPushButton("Submit support ticket")
        support.setCursor(Qt.PointingHandCursor)
        support.setStyleSheet(
            f"QPushButton {{ color: {ALMA_TEXT_ON_DARK}; background: transparent; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 4px; padding: 8px 16px; }}"
            "QPushButton:hover { background: rgba(255,255,255,0.05); }"
        )
        support.clicked.connect(self.support_clicked.emit)
        buttons.addWidget(support)

        buttons.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self._continue_btn = QPushButton("Continue")
        self._continue_btn.setEnabled(False)
        self._continue_btn.setCursor(Qt.PointingHandCursor)
        self._continue_btn.setStyleSheet(
            f"QPushButton {{ color: {ALMA_CREAM}; background: {ALMA_SUCCESS}; "
            "border: none; border-radius: 4px; padding: 8px 24px; font-weight: 600; }}"
            "QPushButton:disabled { background: #3a5a4a; color: #7a8a80; }"
            "QPushButton:hover:enabled { background: #1a8549; }"
        )
        self._continue_btn.clicked.connect(self._on_continue)
        buttons.addWidget(self._continue_btn)

        footer.addLayout(buttons)

        foot_version = QLabel(f"{self._app_version} — RCM Operations")
        foot_version.setAlignment(Qt.AlignCenter)
        foot_version.setStyleSheet(
            f"color: {ALMA_TEXT_ON_DARK}; opacity: 0.45; font-size: 10px; padding-top: 12px;"
        )
        footer.addWidget(foot_version)
        return footer

    # ──────────────────────────────────────────────────────────────
    # Handlers
    # ──────────────────────────────────────────────────────────────

    def _on_continue(self) -> None:
        self.continue_clicked.emit()
        self.accept()

    def _summary_text(self, summary: dict[str, int], passed_critical: bool) -> str:
        if passed_critical and summary["warn"] == 0 and summary["fail"] == 0:
            return "All systems ready."
        warn = summary["warn"]
        fail = summary["fail"]
        parts = []
        if warn:
            parts.append(f"{warn} warning{'s' if warn != 1 else ''}")
        if fail:
            label = "critical failure" if not passed_critical else "non-critical failure"
            parts.append(f"{fail} {label}{'s' if fail != 1 else ''}")
        suffix = " App will continue." if passed_critical else " Cannot continue."
        return ", ".join(parts) + " — see details above." + suffix
