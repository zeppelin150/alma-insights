"""
Alma Insights — Reusable Pagination Bar Widget
Compact [ < ]  Page 1 of 5  [ > ]   Showing 1-10 of 47
Auto-hides when total items <= page_size.
"""

import math
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Qt, Signal
from src.ui.theme import (
    ALMA_TEXT_MID, ALMA_TEXT_LIGHT, ALMA_GREEN_DARK, ALMA_GREEN_LIGHT,
    ALMA_CREAM, ALMA_BORDER_LIGHT, ALMA_HOVER_LIGHT,
)

# Neutralize the global QPushButton { padding: 10px 22px } from theme.py
_BTN_RESET = "padding: 0px; margin: 0px;"


class PaginationBar(QWidget):
    """
    Compact pagination bar with prev/next buttons and item range display.

    Signals:
        page_changed(int)  — emitted with 0-indexed page number

    Usage:
        pager = PaginationBar()
        pager.set_total(47)          # 47 items total
        pager.set_page_size(10)      # 10 per page (default)
        pager.page_changed.connect(my_chart.set_page)
    """

    page_changed = Signal(int)

    def __init__(self, page_size: int = 10, parent=None):
        super().__init__(parent)
        self._page = 0
        self._page_size = page_size
        self._total = 0
        self._build_ui()
        self._update_state()

    def _build_ui(self):
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        # Prev button
        self._prev_btn = QPushButton("\u25c0")
        self._prev_btn.setFixedSize(28, 24)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.setStyleSheet(self._nav_style())
        self._prev_btn.clicked.connect(self._go_prev)
        layout.addWidget(self._prev_btn)

        # Page label
        self._page_label = QLabel("Page 1 of 1")
        self._page_label.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID};"
            " background: transparent;"
        )
        self._page_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._page_label)

        # Next button
        self._next_btn = QPushButton("\u25b6")
        self._next_btn.setFixedSize(28, 24)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setStyleSheet(self._nav_style())
        self._next_btn.clicked.connect(self._go_next)
        layout.addWidget(self._next_btn)

        layout.addSpacing(12)

        # Range label
        self._range_label = QLabel("Showing 0-0 of 0")
        self._range_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
            " background: transparent;"
        )
        layout.addWidget(self._range_label)

        layout.addStretch()
        self.setFixedHeight(32)

    def _nav_style(self) -> str:
        return f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 4px;
                font-size: 10px; font-weight: 600;
                {_BTN_RESET}
            }}
            QPushButton:hover {{
                background: {ALMA_HOVER_LIGHT}; color: {ALMA_GREEN_DARK};
                border-color: {ALMA_GREEN_LIGHT};
                {_BTN_RESET}
            }}
            QPushButton:disabled {{
                background: transparent; color: {ALMA_BORDER_LIGHT};
                border-color: {ALMA_BORDER_LIGHT};
                {_BTN_RESET}
            }}
        """

    # ── Public API ──

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def total_pages(self) -> int:
        if self._total <= 0:
            return 1
        return math.ceil(self._total / self._page_size)

    def set_total(self, total: int):
        """Set total item count. Resets to page 0 and auto-hides if single page."""
        self._total = max(0, total)
        self._page = 0
        self._update_state()

    def set_page_size(self, size: int):
        """Change page size. Resets to page 0."""
        self._page_size = max(1, size)
        self._page = 0
        self._update_state()

    def set_page(self, page: int):
        """Jump to a specific page (0-indexed). Clamps to valid range."""
        page = max(0, min(page, self.total_pages - 1))
        if page != self._page:
            self._page = page
            self._update_state()
            self.page_changed.emit(self._page)

    def reset(self):
        """Reset to page 0."""
        self._page = 0
        self._total = 0
        self._update_state()

    def page_slice(self) -> tuple:
        """Return (start_index, end_index) for current page — handy for slicing."""
        start = self._page * self._page_size
        end = min(start + self._page_size, self._total)
        return start, end

    # ── Navigation ──

    def _go_prev(self):
        if self._page > 0:
            self._page -= 1
            self._update_state()
            self.page_changed.emit(self._page)

    def _go_next(self):
        if self._page < self.total_pages - 1:
            self._page += 1
            self._update_state()
            self.page_changed.emit(self._page)

    # ── State ──

    def _update_state(self):
        total_p = self.total_pages
        current_p = self._page + 1  # 1-indexed for display

        # Auto-hide when single page or no data
        if self._total <= self._page_size:
            self.setVisible(False)
            return
        self.setVisible(True)

        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page < total_p - 1)

        self._page_label.setText(f"Page {current_p} of {total_p}")

        start = self._page * self._page_size + 1
        end = min((self._page + 1) * self._page_size, self._total)
        self._range_label.setText(f"Showing {start}\u2013{end} of {self._total}")
