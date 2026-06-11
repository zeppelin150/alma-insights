"""
Alma Insights — Brand Theme & Stylesheet (compatibility facade)

Colors extracted from official Alma logo.
Lightdash-level polish: clean, modern, spacious, professional.

The token values now live in `src.ui.design.tokens` (single source,
dark-ready) and the stylesheet in `src.ui.design.qss`; this module
re-exports the flat `ALMA_*` constants and keeps the shadow/table
helpers so every existing `from src.ui.theme import *` call site keeps
working unchanged.
"""

from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem, QStyle,
)
from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtGui import QColor

from src.ui.design.tokens import *  # noqa: F401,F403 — legacy ALMA_* surface
from src.ui.design.tokens import ELEVATION


# ═══ SHADOW FUNCTIONS ═══

def _apply_shadow(widget, level):
    spec = ELEVATION[level]
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(spec["blur"])
    shadow.setOffset(*spec["offset"])
    shadow.setColor(QColor(0, 0, 0, spec["alpha"]))
    widget.setGraphicsEffect(shadow)


def apply_card_shadow(widget):
    """Resting-state card shadow — light and subtle (Build 10.0: T4)."""
    _apply_shadow(widget, "resting")


def apply_card_shadow_hover(widget):
    """Hover/focus elevated shadow — cards 'lift' on interaction."""
    _apply_shadow(widget, "hover")


def apply_card_shadow_soft(widget):
    """Lighter shadow for smaller inline cards (KPI cards, term rows)."""
    _apply_shadow(widget, "soft")


class _NoFocusDelegate(QStyledItemDelegate):
    """Delegate that strips the focus rectangle from item painting.

    QSS `outline: 0` doesn't reliably suppress the native focus indicator
    on Windows PySide6. Removing State_HasFocus in initStyleOption does.
    """

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus


def configure_table(table):
    """Standard table configuration — full-row selection, no focus rect."""
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setItemDelegate(_NoFocusDelegate(table))


def configure_tree(tree):
    """Standard tree widget configuration — no focus rect."""
    tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    tree.setItemDelegate(_NoFocusDelegate(tree))


def get_stylesheet():
    from src.ui.design.qss import build_stylesheet
    return build_stylesheet()
