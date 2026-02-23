"""
Alma Insights — Modern Date Picker Widget
A styled date picker that replaces the native QDateEdit calendar popup.
Displays as: [ Feb 18, 2026  ▾ ]
When clicked, opens a styled frameless popup calendar with drop-shadow.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QVBoxLayout,
    QGridLayout, QLabel, QSizePolicy, QGraphicsDropShadowEffect,
    QApplication,
)
from PySide6.QtCore import Qt, QDate, Signal, QPoint, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QBrush, QPen, QRegion
from src.ui.theme import (
    ALMA_WHITE, ALMA_GREEN_LIGHT, ALMA_GREEN_DARK,
    ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_TEXT_ON_DARK,
    ALMA_BG_ELEVATED,
)


# ─── Common style snippet to neutralize the global QPushButton theme ───
# The global stylesheet in theme.py sets padding: 10px 22px on ALL QPushButtons
# which makes small fixed-size calendar buttons clip their text.  Every calendar
# button MUST include this reset.
_BTN_RESET = "padding: 0px; margin: 0px;"


class ModernDatePicker(QWidget):
    """
    A modern date picker that looks like Lightdash/Notion date selectors.

    Displays as a button: [ Feb 18, 2026  ▾ ]
    When clicked, opens a styled frameless popup calendar with drop-shadow.
    """

    date_changed = Signal(QDate)
    dateChanged = date_changed  # Qt-compatible alias

    def __init__(self, parent=None):
        super().__init__(parent)
        self._date = QDate.currentDate()
        self._popup = None
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.button = QPushButton()
        self.button.setObjectName("DatePickerButton")
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self._show_calendar)
        self._update_display()
        layout.addWidget(self.button)

    def _update_display(self):
        text = self._date.toString("MMM d, yyyy")
        self.button.setText(f"  {text}  \u25be")

    def _show_calendar(self):
        # Close any existing popup
        if self._popup and self._popup.isVisible():
            self._popup.close()
            self._popup = None
            return

        self._popup = CalendarPopup(self._date, parent=self)
        self._popup.date_selected.connect(self._on_date_selected)

        # Position below the button
        btn_rect = self.button.rect()
        global_pos = self.button.mapToGlobal(btn_rect.bottomLeft())

        # Offset to account for shadow margins (12px on each side, 4px top, 16px bottom)
        popup_x = global_pos.x() - 12
        popup_y = global_pos.y() - 4 + 2  # 2px gap

        # Ensure popup stays on screen
        screen = QApplication.screenAt(global_pos)
        if screen:
            screen_rect = screen.availableGeometry()
            popup_w = self._popup.width()
            popup_h = self._popup.height()
            if popup_x + popup_w > screen_rect.right():
                popup_x = screen_rect.right() - popup_w
            if popup_y + popup_h > screen_rect.bottom():
                # Show above instead
                popup_y = self.button.mapToGlobal(btn_rect.topLeft()).y() - popup_h + 4

        self._popup.move(popup_x, popup_y)
        self._popup.show()

    def _on_date_selected(self, date):
        self._date = date
        self._update_display()
        self.date_changed.emit(date)
        self._popup = None

    # Public API — compatible with QDateEdit usage patterns

    def date(self) -> QDate:
        return self._date

    def setDate(self, date: QDate):
        self._date = date
        self._update_display()

    def set_date(self, date: QDate):
        """Snake-case alias for setDate."""
        self.setDate(date)

    def get_date_string(self) -> str:
        """Return the current date as a YYYY-MM-DD string."""
        return self._date.toString("yyyy-MM-dd")

    def setDisplayFormat(self, fmt: str):
        pass  # Ignored — we always use "MMM d, yyyy"

    def setCalendarPopup(self, enabled: bool):
        pass  # Always a calendar popup

    def setMinimumDate(self, date: QDate):
        pass  # Not enforced in this simple version

    def setMaximumDate(self, date: QDate):
        pass


class CalendarPopup(QWidget):
    """
    Frameless popup calendar with drop shadow and rounded corners.
    Uses WA_TranslucentBackground for the shadow to render outside the card.
    """

    date_selected = Signal(QDate)

    # Geometry constants
    SHADOW_H = 12      # horizontal shadow margin
    SHADOW_TOP = 4      # top shadow margin
    SHADOW_BOT = 16     # bottom shadow margin (shadow falls downward)
    CARD_W = 280        # inner card width
    CARD_H = 330        # inner card height
    CORNER_R = 12       # border-radius of the card

    def __init__(self, current_date: QDate, parent=None):
        super().__init__(parent)

        # Frameless + popup + translucent so shadow can extend beyond card
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        total_w = self.CARD_W + 2 * self.SHADOW_H
        total_h = self.CARD_H + self.SHADOW_TOP + self.SHADOW_BOT
        self.setFixedSize(total_w, total_h)

        self._selected = current_date
        self._view_year = current_date.year()
        self._view_month = current_date.month()
        self._today = QDate.currentDate()

        # ── Shadow container ──
        # We don't set any stylesheet on `self` (it's translucent).
        # Instead we place a card widget inside with proper offset.
        self._card = QWidget(self)
        self._card.setObjectName("CalendarCard")
        self._card.setGeometry(
            self.SHADOW_H, self.SHADOW_TOP,
            self.CARD_W, self.CARD_H,
        )
        self._card.setStyleSheet(f"""
            #CalendarCard {{
                background: {ALMA_BG_ELEVATED};
                border: 1px solid {ALMA_BORDER};
                border-radius: {self.CORNER_R}px;
            }}
        """)

        # Apply a real QGraphicsDropShadowEffect
        shadow_fx = QGraphicsDropShadowEffect(self._card)
        shadow_fx.setBlurRadius(24)
        shadow_fx.setOffset(0, 6)
        shadow_fx.setColor(QColor(0, 0, 0, 40))
        self._card.setGraphicsEffect(shadow_fx)

        # ── Layout inside the card ──
        self._main_layout = QVBoxLayout(self._card)
        self._main_layout.setContentsMargins(12, 12, 12, 10)
        self._main_layout.setSpacing(4)

        self._render_calendar()

    # ──────────────────────────────────────────────────────────
    #  Painting — draw the white rounded rect as background
    # ──────────────────────────────────────────────────────────

    def paintEvent(self, event):
        """Draw the rounded-rect card background (needed because WA_TranslucentBackground)."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        card_rect = self._card.geometry()

        # White fill with rounded corners
        path = QPainterPath()
        path.addRoundedRect(
            float(card_rect.x()), float(card_rect.y()),
            float(card_rect.width()), float(card_rect.height()),
            self.CORNER_R, self.CORNER_R,
        )
        painter.fillPath(path, QBrush(QColor(ALMA_BG_ELEVATED)))

        # Subtle border
        painter.setPen(QPen(QColor(ALMA_BORDER), 1.0))
        painter.drawPath(path)

        painter.end()

    # ──────────────────────────────────────────────────────────
    #  Calendar rendering
    # ──────────────────────────────────────────────────────────

    def _render_calendar(self):
        """Build/rebuild the month view inside self._main_layout."""
        # Clear existing content
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        # ── Month / Year header ──
        header_w = QWidget()
        header_w.setObjectName("CalHeader")
        header_w.setStyleSheet("#CalHeader { background: transparent; }")
        header = QHBoxLayout(header_w)
        header.setContentsMargins(2, 0, 2, 0)
        header.setSpacing(4)

        prev_btn = QPushButton("\u25c0")
        prev_btn.setFixedSize(30, 30)
        prev_btn.setCursor(Qt.PointingHandCursor)
        prev_btn.setStyleSheet(self._nav_btn_style())
        prev_btn.clicked.connect(self._prev_month)
        header.addWidget(prev_btn)

        header.addStretch()

        month_label = QLabel(
            QDate(self._view_year, self._view_month, 1).toString("MMMM yyyy")
        )
        month_label.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK}; background: transparent;"
        )
        month_label.setAlignment(Qt.AlignCenter)
        header.addWidget(month_label)

        header.addStretch()

        next_btn = QPushButton("\u25b6")
        next_btn.setFixedSize(30, 30)
        next_btn.setCursor(Qt.PointingHandCursor)
        next_btn.setStyleSheet(self._nav_btn_style())
        next_btn.clicked.connect(self._next_month)
        header.addWidget(next_btn)

        self._main_layout.addWidget(header_w)

        # ── Day-of-week headers ──
        dow_w = QWidget()
        dow_w.setObjectName("CalDowRow")
        dow_w.setStyleSheet("#CalDowRow { background: transparent; }")
        dow_grid = QGridLayout(dow_w)
        dow_grid.setContentsMargins(0, 4, 0, 2)
        dow_grid.setSpacing(0)
        for i, day in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
            lbl = QLabel(day)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedHeight(20)
            lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 600; color: {ALMA_TEXT_LIGHT}; background: transparent;"
            )
            dow_grid.addWidget(lbl, 0, i)
        self._main_layout.addWidget(dow_w)

        # ── Days grid ──
        days_w = QWidget()
        days_w.setObjectName("CalDaysGrid")
        days_w.setStyleSheet("#CalDaysGrid { background: transparent; }")
        grid = QGridLayout(days_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        first_of_month = QDate(self._view_year, self._view_month, 1)
        start_dow = first_of_month.dayOfWeek() - 1  # Monday = 0
        days_in_month = first_of_month.daysInMonth()

        row = 0
        col = start_dow
        for day_num in range(1, days_in_month + 1):
            d = QDate(self._view_year, self._view_month, day_num)
            btn = QPushButton(str(day_num))
            btn.setFixedSize(34, 30)
            btn.setCursor(Qt.PointingHandCursor)

            is_selected = (d == self._selected)
            is_today = (d == self._today)

            btn.setStyleSheet(self._day_btn_style(is_selected, is_today))
            btn.clicked.connect(lambda checked, date=d: self._pick_date(date))
            grid.addWidget(btn, row, col)

            col += 1
            if col > 6:
                col = 0
                row += 1

        self._main_layout.addWidget(days_w)
        self._main_layout.addStretch()

        # ── Today shortcut ──
        today_btn = QPushButton("Today")
        today_btn.setCursor(Qt.PointingHandCursor)
        today_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: none; font-size: 11px; font-weight: 600;
                {_BTN_RESET}
                padding: 4px 8px;
            }}
            QPushButton:hover {{ color: {ALMA_GREEN_LIGHT}; }}
        """)
        today_btn.clicked.connect(lambda: self._pick_date(self._today))
        self._main_layout.addWidget(today_btn, alignment=Qt.AlignCenter)

    # ──────────────────────────────────────────────────────────
    #  Styles
    # ──────────────────────────────────────────────────────────

    def _day_btn_style(self, is_selected: bool, is_today: bool) -> str:
        """Return the full inline stylesheet for a day button.

        CRITICAL: every rule includes `padding: 0px; margin: 0px;` to override
        the global QPushButton { padding: 10px 22px } from theme.py.
        """
        if is_selected:
            return f"""
                QPushButton {{
                    background: {ALMA_GREEN_LIGHT}; color: #FFFFFF;
                    border: none; border-radius: 6px;
                    font-size: 12px; font-weight: 700;
                    {_BTN_RESET}
                }}
                QPushButton:hover {{
                    background: {ALMA_GREEN_DARK};
                    {_BTN_RESET}
                }}
            """
        elif is_today:
            return f"""
                QPushButton {{
                    background: {ALMA_CREAM}; color: {ALMA_GREEN_DARK};
                    border: 1.5px solid {ALMA_GREEN_LIGHT}; border-radius: 6px;
                    font-size: 12px; font-weight: 700;
                    {_BTN_RESET}
                }}
                QPushButton:hover {{
                    background: {ALMA_GREEN_LIGHT}; color: #FFFFFF;
                    {_BTN_RESET}
                }}
            """
        else:
            return f"""
                QPushButton {{
                    background: {ALMA_BG_ELEVATED}; color: {ALMA_TEXT_DARK};
                    border: none; border-radius: 6px;
                    font-size: 12px; font-weight: 400;
                    {_BTN_RESET}
                }}
                QPushButton:hover {{
                    background: {ALMA_CREAM}; color: {ALMA_GREEN_DARK};
                    {_BTN_RESET}
                }}
            """

    def _nav_btn_style(self) -> str:
        """Style for ◀ / ▶ navigation buttons."""
        return f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: none; border-radius: 6px;
                font-size: 12px; font-weight: 600;
                {_BTN_RESET}
            }}
            QPushButton:hover {{
                background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};
                {_BTN_RESET}
            }}
        """

    # ──────────────────────────────────────────────────────────
    #  Actions
    # ──────────────────────────────────────────────────────────

    def _pick_date(self, date: QDate):
        self.date_selected.emit(date)
        self.close()

    def _prev_month(self):
        if self._view_month == 1:
            self._view_month = 12
            self._view_year -= 1
        else:
            self._view_month -= 1
        self._render_calendar()

    def _next_month(self):
        if self._view_month == 12:
            self._view_month = 1
            self._view_year += 1
        else:
            self._view_month += 1
        self._render_calendar()

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                CalendarPopup._clear_layout(child.layout())
