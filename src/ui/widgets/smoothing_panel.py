"""
Alma Insights — AI Smoothing Review Panel (Pass 3.0)
Accept/reject per-suggestion panel for AI cluster smoothing.
Shows both statistical and AI labels side by side.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_SUCCESS, ALMA_WARNING,
)


class SmoothingReviewPanel(QWidget):
    """Panel for reviewing and accepting/rejecting AI smoothing suggestions."""

    suggestions_applied = Signal(dict)  # Emitted with accepted suggestions
    dismissed = Signal()                 # Emitted when panel wants to close

    def __init__(self, parent=None):
        super().__init__(parent)
        self._suggestions = []
        self._checkboxes = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title = QLabel("AI Smoothing Suggestions")
        title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_MID};")
        header.addWidget(title)
        header.addStretch()

        self._accept_all_btn = QPushButton("Accept All")
        self._accept_all_btn.setCursor(Qt.PointingHandCursor)
        self._accept_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_SUCCESS}; color: white;
                border: none; border-radius: 4px; padding: 4px 12px;
                font-size: 11px; font-weight: 600;
            }}
        """)
        self._accept_all_btn.clicked.connect(self._accept_all)
        header.addWidget(self._accept_all_btn)

        self._reject_all_btn = QPushButton("Reject All")
        self._reject_all_btn.setCursor(Qt.PointingHandCursor)
        self._reject_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 4px;
                padding: 4px 12px; font-size: 11px;
            }}
        """)
        self._reject_all_btn.clicked.connect(self._reject_all)
        header.addWidget(self._reject_all_btn)

        self._revert_btn = QPushButton("Revert to Statistical")
        self._revert_btn.setCursor(Qt.PointingHandCursor)
        self._revert_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_WARNING};
                border: 1px solid {ALMA_WARNING}; border-radius: 4px;
                padding: 4px 12px; font-size: 11px;
            }}
        """)
        self._revert_btn.clicked.connect(self._revert_all)
        header.addWidget(self._revert_btn)

        layout.addLayout(header)

        # Scroll area for suggestions
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        # No max-height — let the panel fill its container (e.g. DrilldownPanel)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        layout.addWidget(self._scroll)

        # Apply button
        self._apply_btn = QPushButton("Apply Selected")
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 8px 20px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._apply_btn.clicked.connect(self._apply_selected)
        layout.addWidget(self._apply_btn, alignment=Qt.AlignRight)

        # Visibility controlled by parent (DrilldownPanel or layout)

    def set_suggestions(self, suggestions):
        """Load suggestions from AI smoothing.

        suggestions: [{cluster_id, statistical_label, ai_label,
                       confidence, action, merge_with, split_into}, ...]
        """
        self._suggestions = suggestions
        self._checkboxes = []

        # Clear existing
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for s in suggestions:
            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                    border-radius: 6px; padding: 8px;
                }}
            """)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)

            cb = QCheckBox()
            cb.setChecked(True)
            row_layout.addWidget(cb)
            self._checkboxes.append(cb)

            info = QVBoxLayout()
            stat_label = QLabel(
                f"Statistical: {s.get('statistical_label', 'N/A')}"
            )
            stat_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
            info.addWidget(stat_label)

            conf = s.get("confidence", 0)
            conf_str = f" ({conf:.0%})" if isinstance(conf, (int, float)) else ""
            ai_label = QLabel(
                f"AI: {s.get('ai_label', 'N/A')}{conf_str}"
            )
            ai_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_DARK};")
            info.addWidget(ai_label)

            action = s.get("action", "relabel")
            if action == "merge":
                action_label = QLabel(f"Merge with cluster {s.get('merge_with', '?')}")
            elif action == "split":
                action_label = QLabel(f"Split into: {s.get('split_into', '?')}")
            else:
                action_label = QLabel(f"Action: {action}")
            action_label.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
            info.addWidget(action_label)

            row_layout.addLayout(info, 1)

            idx = self._list_layout.count() - 1
            self._list_layout.insertWidget(idx, row)

        # Visibility is controlled by the DrilldownPanel — do NOT self.show()
        # as that creates a floating top-level window.

    def _accept_all(self):
        for cb in self._checkboxes:
            cb.setChecked(True)

    def _reject_all(self):
        for cb in self._checkboxes:
            cb.setChecked(False)

    def _revert_all(self):
        self._reject_all()
        self._apply_selected()

    def _apply_selected(self):
        accepted = {}
        for i, (cb, s) in enumerate(zip(self._checkboxes, self._suggestions)):
            if cb.isChecked():
                accepted[s.get("cluster_id", i)] = s
        self.suggestions_applied.emit(accepted)
        self.dismissed.emit()
