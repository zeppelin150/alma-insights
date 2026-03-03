"""
Alma Insights — KPI Card Building Block

Reusable KPI snapshot card extracted from TRC Analytics.
Used across all analysis pages for consistent KPI presentation.

Usage:
    row = KPICardRow()
    row.add_card(KPICard("Total Tickets", "—", "in selected range"))
    row.add_card(KPICard("Avg CSAT", "—", "satisfaction score"))
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
)
from PySide6.QtCore import Qt, QEvent

from src.ui.theme import (
    ALMA_TEXT_LIGHT, ALMA_BG_ELEVATED, ALMA_SUCCESS, ALMA_ERROR,
    apply_card_shadow_soft, apply_card_shadow_hover,
)


class KPICard(QFrame):
    """Single KPI snapshot card with title, value, delta, and subtitle.

    ObjectName-styled labels: #KPILabel, #KPIValue, #KPIDeltaPositive/Negative/Neutral.
    Hover shadow swap via eventFilter on the frame.
    """

    # Accent color cycle for KPI rows — use with set_accent() or accent= kwarg
    ACCENT_CYCLE = ("green", "blue", "teal", "amber")

    def __init__(self, title: str, value: str = "\u2014", subtitle: str = "",
                 accent: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("KPICard")
        self.setStyleSheet(
            f"#KPICard {{ background: {ALMA_BG_ELEVATED};"
            " border: 1px solid rgba(214, 210, 202, 0.45);"
            " border-radius: 12px; }}"
        )
        apply_card_shadow_soft(self)
        self.installEventFilter(self)

        # Apply accent border if provided
        if accent:
            self.setProperty("accent", accent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(4)

        # Title
        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("KPILabel")
        self._title_lbl.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(self._title_lbl)

        # Value
        self._value_lbl = QLabel(value)
        self._value_lbl.setObjectName("KPIValue")
        self._value_lbl.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(self._value_lbl)

        # Delta (hidden until set)
        self._delta_lbl = QLabel("")
        self._delta_lbl.setObjectName("KPIDeltaNeutral")
        self._delta_lbl.setStyleSheet("border: none; background: transparent;")
        self._delta_lbl.setVisible(False)
        layout.addWidget(self._delta_lbl)

        # Subtitle
        self._subtitle_lbl = QLabel(subtitle)
        self._subtitle_lbl.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
            " border: none; background: transparent;"
        )
        layout.addWidget(self._subtitle_lbl)

    # ── Public API ──

    def set_value(self, value: str):
        """Update the main KPI value."""
        self._value_lbl.setText(value)

    def set_delta(self, delta_text: str, direction: str = "neutral"):
        """Show a delta indicator (e.g. '+12.3%').

        Args:
            delta_text: Text to display (e.g. '+5%', '-2.1 hrs')
            direction: 'up' (green), 'down' (red), or 'neutral' (gray)
        """
        obj_name_map = {
            "up": "KPIDeltaPositive",
            "down": "KPIDeltaNegative",
            "neutral": "KPIDeltaNeutral",
        }
        self._delta_lbl.setObjectName(obj_name_map.get(direction, "KPIDeltaNeutral"))
        self._delta_lbl.setStyleSheet("border: none; background: transparent;")
        self._delta_lbl.setText(delta_text)
        self._delta_lbl.setVisible(bool(delta_text))

    def set_subtitle(self, text: str):
        """Update the subtitle text."""
        self._subtitle_lbl.setText(text)

    def set_title(self, text: str):
        """Update the title text."""
        self._title_lbl.setText(text)

    def set_accent(self, color_name: str):
        """Set the accent border color via Qt property selector.

        Supported values: 'green', 'blue', 'teal', 'amber'.
        Triggers QSS refresh so #KPICard[accent="..."] rules take effect.
        """
        self.setProperty("accent", color_name)
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def value_label(self) -> QLabel:
        """Direct access to value label for legacy compatibility."""
        return self._value_lbl

    @property
    def delta_label(self) -> QLabel:
        """Direct access to delta label for legacy compatibility."""
        return self._delta_lbl

    @property
    def subtitle_label(self) -> QLabel:
        """Direct access to subtitle label."""
        return self._subtitle_lbl

    # ── Hover shadow swap ──

    def eventFilter(self, obj, event):
        if obj is self:
            if event.type() == QEvent.Type.Enter:
                apply_card_shadow_hover(self)
            elif event.type() == QEvent.Type.Leave:
                apply_card_shadow_soft(self)
        return super().eventFilter(obj, event)


class KPICardRow(QWidget):
    """Horizontal row of KPI cards with equal spacing.

    Usage:
        row = KPICardRow()
        row.add_card(KPICard("Total", "42"))
        row.add_card(KPICard("Avg", "3.7"))
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)
        self._cards: list[KPICard] = []

    def add_card(self, card: KPICard) -> KPICard:
        """Add a KPI card to the row (stretch=1 for equal sizing)."""
        self._cards.append(card)
        self._layout.addWidget(card, 1)
        return card

    @property
    def cards(self) -> list[KPICard]:
        """Access all cards in the row."""
        return list(self._cards)

    def card(self, index: int) -> KPICard:
        """Get a card by index."""
        return self._cards[index]

    def apply_accent_cycle(self):
        """Apply cycling accent colors (green, blue, teal, amber) to all cards."""
        for i, card in enumerate(self._cards):
            card.set_accent(KPICard.ACCENT_CYCLE[i % len(KPICard.ACCENT_CYCLE)])
