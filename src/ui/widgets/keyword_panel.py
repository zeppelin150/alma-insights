"""
Alma Insights — AI Keyword Suggestion Panel (Pass 3.0)
Review panel for AI-suggested keyword suppressions and concept map additions.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QScrollArea,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_WARNING, ALMA_ERROR,
)


class KeywordReviewPanel(QWidget):
    """Panel for reviewing AI keyword suggestions: suppress noise or add to concept map."""

    keywords_applied = Signal(dict)  # {"suppress": [...], "add": [...]}
    dismissed = Signal()             # Emitted when panel wants to close

    def __init__(self, parent=None):
        super().__init__(parent)
        self._suppress_items = []
        self._add_items = []
        self._suppress_cbs = []
        self._add_cbs = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title = QLabel("AI Keyword Suggestions")
        title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_MID};")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        # No max-height — let the panel fill its container (e.g. DrilldownPanel)

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(8)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        dismiss_btn = QPushButton("Dismiss All")
        dismiss_btn.setCursor(Qt.PointingHandCursor)
        dismiss_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 4px;
                padding: 6px 16px; font-size: 11px;
            }}
        """)
        dismiss_btn.clicked.connect(self._dismiss_all)
        btn_row.addWidget(dismiss_btn)

        apply_btn = QPushButton("Apply Selected")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 6px 16px;
                font-weight: 600; font-size: 11px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        apply_btn.clicked.connect(self._apply_selected)
        btn_row.addWidget(apply_btn)

        layout.addLayout(btn_row)
        # Visibility controlled by parent (DrilldownPanel or layout)

    def set_suggestions(self, suppress=None, add_to_map=None):
        """Load suggestions from AI keyword review.

        suppress: [{"term": "...", "reason": "..."}, ...]
        add_to_map: [{"term": "...", "concept_group": "...", "reason": "..."}, ...]
        """
        self._suppress_items = suppress or []
        self._add_items = add_to_map or []
        self._suppress_cbs = []
        self._add_cbs = []

        # Clear
        while self._container_layout.count() > 0:
            item = self._container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Suppress section
        if self._suppress_items:
            section = QLabel("SUPPRESS (noise)")
            section.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {ALMA_ERROR}; "
                f"padding: 4px 0;"
            )
            self._container_layout.addWidget(section)

            for item in self._suppress_items:
                row = self._build_row(
                    item["term"],
                    f"AI: {item.get('reason', 'statistical noise')}",
                    is_suppress=True
                )
                cb = row.findChild(QCheckBox)
                self._suppress_cbs.append(cb)
                self._container_layout.addWidget(row)

        # Add to concept map section
        if self._add_items:
            section = QLabel("ADD TO CONCEPT MAP")
            section.setStyleSheet(
                f"font-size: 12px; font-weight: 700; color: {ALMA_GREEN_DARK}; "
                f"padding: 4px 0; margin-top: 8px;"
            )
            self._container_layout.addWidget(section)

            for item in self._add_items:
                group = item.get("concept_group", "new group")
                row = self._build_row(
                    f"\"{item['term']}\" -> {group}",
                    f"AI: {item.get('reason', '')}",
                    is_suppress=False
                )
                cb = row.findChild(QCheckBox)
                self._add_cbs.append(cb)
                self._container_layout.addWidget(row)

        # Visibility is controlled by the DrilldownPanel — do NOT self.show()
        # as that creates a floating top-level window.

    def _build_row(self, label_text, detail_text, is_suppress=True):
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 6px; padding: 6px;
            }}
        """)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 8, 4)

        cb = QCheckBox()
        cb.setChecked(True)
        row_layout.addWidget(cb)

        info = QVBoxLayout()
        term_lbl = QLabel(label_text)
        term_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK};")
        info.addWidget(term_lbl)

        detail_lbl = QLabel(detail_text)
        detail_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
        detail_lbl.setWordWrap(True)
        info.addWidget(detail_lbl)

        row_layout.addLayout(info, 1)
        return row

    def _dismiss_all(self):
        self.keywords_applied.emit({"suppress": [], "add": []})
        self.dismissed.emit()

    def _apply_selected(self):
        suppress = [
            self._suppress_items[i]
            for i, cb in enumerate(self._suppress_cbs) if cb.isChecked()
        ]
        add = [
            self._add_items[i]
            for i, cb in enumerate(self._add_cbs) if cb.isChecked()
        ]
        self.keywords_applied.emit({"suppress": suppress, "add": add})
        self.dismissed.emit()
