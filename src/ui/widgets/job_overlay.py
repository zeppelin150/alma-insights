"""
Alma Insights — Unified Job Overlay

Semi-transparent dark-green overlay displayed on top of the content
area while the job queue is executing.  Shows the animated sprout
symbol, current job description with trailing ellipsis, and a
compact list of queued/running/completed jobs.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QEvent,
)
from PySide6.QtGui import QPainter, QColor

from src.ui.widgets.sprout_animation import SproutAnimation


# Background colour — dark green (#063023) at ~92 % opacity
_BG_COLOR = QColor(6, 48, 35, 235)

# Text colours
_TEXT_PRIMARY = "#F3F1EC"      # ALMA_CREAM
_TEXT_SECONDARY = "rgba(243, 241, 236, 0.7)"
_TEXT_COMPLETED = "rgba(243, 241, 236, 0.45)"


class JobOverlay(QWidget):
    """Full-content-area overlay with sprout animation and job stack."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)

        # ── Fade animations ──────────────────────────────────────────
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_fx)

        self._fade_in = QPropertyAnimation(self._opacity_fx, b"opacity")
        self._fade_in.setDuration(300)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)

        self._fade_out = QPropertyAnimation(self._opacity_fx, b"opacity")
        self._fade_out.setDuration(250)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self._on_fade_out_done)

        self._build_ui()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(40, 60, 40, 40)
        layout.setSpacing(12)

        layout.addStretch(3)

        # Sprout animation (centred)
        self._sprout = SproutAnimation(size=72)
        layout.addWidget(self._sprout, alignment=Qt.AlignCenter)

        layout.addSpacing(8)

        # Status text (current job description)
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {_TEXT_PRIMARY};"
            " background: transparent; border: none;"
        )
        layout.addWidget(self._status_label)

        # Animated trailing ellipsis
        self._ellipsis_timer = QTimer(self)
        self._ellipsis_timer.timeout.connect(self._animate_ellipsis)
        self._ellipsis_count = 0
        self._base_status = ""

        layout.addStretch(1)

        # ── Job stack / trace ────────────────────────────────────────
        self._job_list_frame = QFrame()
        self._job_list_frame.setStyleSheet(
            "background: rgba(255, 255, 255, 0.06);"
            " border-radius: 10px;"
            " border: none;"
        )
        self._job_list_frame.setMaximumWidth(420)
        self._job_list_layout = QVBoxLayout(self._job_list_frame)
        self._job_list_layout.setContentsMargins(16, 12, 16, 12)
        self._job_list_layout.setSpacing(4)

        # Wrapper layout to centre the frame horizontally
        frame_row = QHBoxLayout()
        frame_row.addStretch()
        frame_row.addWidget(self._job_list_frame)
        frame_row.addStretch()
        layout.addLayout(frame_row)

        layout.addStretch(2)

    # ── Background ───────────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), _BG_COLOR)
        p.end()
        super().paintEvent(event)

    # ── Resize tracking ──────────────────────────────────────────────

    def eventFilter(self, watched, event):
        """Keep overlay sized to its parent (content_stack)."""
        if event.type() == QEvent.Resize and watched is self.parent():
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    # ── Public API ───────────────────────────────────────────────────

    def show_overlay(self):
        """Fade in the overlay and start the sprout animation."""
        if self.isVisible() and self._opacity_fx.opacity() > 0.9:
            return  # already showing
        self._fade_out.stop()
        self.setVisible(True)
        self.raise_()
        if self.parent():
            self.setGeometry(self.parent().rect())
        self._sprout.start()
        self._ellipsis_count = 0
        self._ellipsis_timer.start(400)
        self._fade_in.start()

    def hide_overlay(self):
        """Fade out and stop animations when queue empties."""
        self._fade_in.stop()
        self._fade_out.start()

    def update_status(self, description: str):
        """Set the current job description (the main text below the sprout)."""
        self._base_status = description.rstrip(".")
        self._ellipsis_count = 0
        self._status_label.setText(self._base_status)

    def update_job_list(self, jobs: list):
        """Rebuild the job stack trace. *jobs*: [{job_id, name, state}, ...]"""
        # Clear existing rows
        while self._job_list_layout.count():
            child = self._job_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not jobs:
            self._job_list_frame.setVisible(False)
            return

        self._job_list_frame.setVisible(True)

        for job in jobs:
            state = job.get("state", "queued")
            name = job.get("name", "")

            if state == "completed":
                prefix = "\u2713"  # ✓
                color = _TEXT_COMPLETED
            elif state == "running":
                prefix = "\u25b8"  # ▸
                color = _TEXT_PRIMARY
            elif state == "failed":
                prefix = "\u2717"  # ✗
                color = "#F87171"  # soft red
            else:
                prefix = "\u2013"  # –
                color = _TEXT_SECONDARY

            row = QLabel(f"  {prefix}   {name}")
            row.setStyleSheet(
                f"font-size: 13px; color: {color};"
                " background: transparent; border: none;"
                " font-weight: 500; padding: 2px 0;"
            )
            self._job_list_layout.addWidget(row)

    # ── Animations ───────────────────────────────────────────────────

    def _animate_ellipsis(self):
        self._ellipsis_count = (self._ellipsis_count + 1) % 4
        dots = "." * self._ellipsis_count
        self._status_label.setText(self._base_status + dots)

    def _on_fade_out_done(self):
        self._sprout.stop()
        self._ellipsis_timer.stop()
        self.setVisible(False)
