"""
Alma Insights — Chart Grid & Chart Wrapper
Supports view mode (clean, static) and edit mode (drag handles, dashed borders).
Layout state saved to DB and restored on next load.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from src.ui.theme import (
    ALMA_WHITE, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_BORDER,
    ALMA_BORDER_LIGHT, apply_card_shadow,
)


class ChartWrapper(QFrame):
    """
    Wraps a chart widget with:
    - Title bar (always visible)
    - Collapse toggle (always visible)
    - Drag handle (edit mode only)
    """

    def __init__(self, chart_widget, title, chart_id, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._chart = chart_widget
        self._title = title
        self._chart_id = chart_id
        self._collapsed = False
        self._edit_mode = False
        self._build_ui()
        apply_card_shadow(self)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(0)

        # Title bar
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)

        # Drag handle (hidden in view mode)
        self.drag_handle = QLabel("\u2807")
        self.drag_handle.setStyleSheet(
            f"color: {ALMA_TEXT_LIGHT}; font-size: 16px;"
        )
        self.drag_handle.setFixedWidth(16)
        self.drag_handle.setVisible(False)
        title_bar.addWidget(self.drag_handle)

        # Title
        title_label = QLabel(self._title)
        title_label.setObjectName("CardTitle")
        title_bar.addWidget(title_label)

        title_bar.addStretch()

        # Collapse button
        self.collapse_btn = QPushButton("\u25be")
        self.collapse_btn.setObjectName("GhostButton")
        self.collapse_btn.setFixedSize(28, 28)
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        title_bar.addWidget(self.collapse_btn)

        layout.addLayout(title_bar)

        # Chart content
        self.content_container = QWidget()
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(0, 8, 0, 0)
        content_layout.addWidget(self._chart)
        layout.addWidget(self.content_container)

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        self.content_container.setVisible(not self._collapsed)
        self.collapse_btn.setText("\u25b8" if self._collapsed else "\u25be")

    def set_edit_mode(self, editing: bool):
        self._edit_mode = editing
        self.drag_handle.setVisible(editing)
        if editing:
            self.setStyleSheet(
                f"#Card {{ border: 2px dashed {ALMA_BORDER}; border-radius: 12px; }}"
            )
        else:
            self.setStyleSheet("")

    @property
    def chart_id(self):
        return self._chart_id

    @property
    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed):
        if collapsed != self._collapsed:
            self._toggle_collapse()


class ChartGrid(QWidget):
    """
    A grid container for chart widgets with edit mode for rearrangement.

    Uses a QGridLayout. Each chart occupies a cell (or span of cells).
    In edit mode, charts show drag handles and dashed borders.
    Layout state is saved to DB and restored on next load.
    """

    def __init__(self, page_key: str, db_manager=None, parent=None):
        super().__init__(parent)
        self._page_key = page_key
        self._db = db_manager
        self._edit_mode = False
        self._charts = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # Edit mode toggle (top right)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.edit_btn = QPushButton("\u270f\ufe0f Edit Layout")
        self.edit_btn.setObjectName("SecondaryButton")
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.setFixedHeight(32)
        self.edit_btn.clicked.connect(self.toggle_edit_mode)
        btn_row.addWidget(self.edit_btn)
        outer.addLayout(btn_row)

        # Grid layout
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(16)
        self.grid.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.grid_widget)

    def add_chart(self, widget, title: str, chart_id: str,
                  default_row=0, default_col=0,
                  row_span=1, col_span=1):
        """Add a chart widget to the grid."""
        wrapper = ChartWrapper(widget, title, chart_id, self)
        self._charts.append(wrapper)

        # Load saved position from DB, or use defaults
        pos = self._load_position(chart_id)
        if pos:
            self.grid.addWidget(
                wrapper, pos["row_pos"], pos["col_pos"],
                pos["row_span"], pos["col_span"]
            )
            if pos.get("is_collapsed"):
                wrapper.set_collapsed(True)
        else:
            self.grid.addWidget(
                wrapper, default_row, default_col,
                row_span, col_span
            )

    def toggle_edit_mode(self):
        """Switch between view and edit modes."""
        self._edit_mode = not self._edit_mode
        for chart in self._charts:
            chart.set_edit_mode(self._edit_mode)

        if self._edit_mode:
            self.edit_btn.setText("Done")
        else:
            self.edit_btn.setText("\u270f\ufe0f Edit Layout")
            self._save_layout()

    def _load_position(self, chart_id):
        """Load saved position for a chart from DB."""
        if not self._db:
            return None
        try:
            return self._db.get_chart_layout(self._page_key, chart_id)
        except Exception:
            return None

    def _save_layout(self):
        """Save current grid positions to DB."""
        if not self._db:
            return
        for wrapper in self._charts:
            idx = self.grid.indexOf(wrapper)
            if idx >= 0:
                row, col, row_span, col_span = self.grid.getItemPosition(idx)
                try:
                    self._db.save_chart_layout(
                        self._page_key, wrapper.chart_id,
                        row, col, row_span, col_span,
                        is_collapsed=1 if wrapper.is_collapsed else 0,
                    )
                except Exception:
                    pass
