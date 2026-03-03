"""
Alma Insights — Expanded Section Dialog
Near-fullscreen modal for viewing any CollapsibleSection's content at full window size.
Re-parents the content widget into the dialog (preserving full interactivity)
and returns it on close.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)
from PySide6.QtCore import Qt

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
    ALMA_BORDER_LIGHT, ALMA_HOVER_LIGHT,
    apply_card_shadow,
)


class ExpandedSectionDialog(QDialog):
    """Near-fullscreen dialog for expanding a CollapsibleSection's content."""

    def __init__(self, title: str, content_widget, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} — Expanded View")
        self._content_widget = content_widget
        self._original_min_height = content_widget.minimumHeight()
        self._build_ui(title)
        self._size_to_parent()

    def _build_ui(self, title: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # ── Header row: title + close button ──
        header = QHBoxLayout()
        header.setSpacing(12)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        header.addWidget(title_label)

        hint = QLabel("Expanded View")
        hint.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-style: italic;"
        )
        header.addWidget(hint)
        header.addStretch()

        close_btn = QPushButton("✕  Close")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 8px 20px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        close_btn.clicked.connect(self.accept)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Divider ──
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        layout.addWidget(divider)

        # ── Re-parent the content widget into this dialog ──
        self._content_widget.setMinimumHeight(0)
        layout.addWidget(self._content_widget, 1)  # stretch=1

    def _size_to_parent(self):
        """Size dialog to ~90% of the parent window, centered."""
        if self.parent():
            parent_geom = self.parent().window().geometry()
            w = int(parent_geom.width() * 0.92)
            h = int(parent_geom.height() * 0.92)
            self.resize(w, h)
            # Center on parent
            cx = parent_geom.x() + (parent_geom.width() - w) // 2
            cy = parent_geom.y() + (parent_geom.height() - h) // 2
            self.move(cx, cy)
        else:
            self.resize(1200, 750)

    def closeEvent(self, event):
        """Restore original minimum height before re-parenting back."""
        self._content_widget.setMinimumHeight(self._original_min_height)
        super().closeEvent(event)
