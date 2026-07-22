"""Shared widget helpers for the Enablement pages (Alma theme)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy

from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_BORDER_LIGHT,
    ALMA_ERROR, ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_INFO, ALMA_SUCCESS,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_MID, ALMA_TEXT_ON_DARK,
    ALMA_WHITE, ALMA_WARNING,
)

_TEAL = ALMA_ACCENT_TEAL


def native_dialog_button_qss() -> str:
    """Button chrome for a NATIVE QMessageBox/QDialog, set on the DIALOG itself.

    The Enablement pages set a selectorless ``background: <cream>`` stylesheet,
    which Qt cascades to every descendant — including the buttons of a native
    dialog parented to the page. That overrides the app QSS's green button fill
    while the app QSS's white text survives: white-on-cream = invisible button
    labels (the 2026-07-22 web-calendar "Move task" confirm rendered blank).

    A rule on an ANCESTOR loses to the page's own selectorless background by
    proximity (measured), so the corrective has to be set ON THE DIALOG. Apply
    via ``style_native_dialog(box)`` or feed this string to a QDialog's
    setStyleSheet. Styles BOTH the default (question/OK) and flat (Cancel)
    buttons so every label is legible.
    """
    return (
        f"QPushButton {{"
        f" background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK};"
        f" border:none; border-radius:8px; padding:8px 18px;"
        f" font-size:12.5px; font-weight:600; min-width:72px; }}"
        f"QPushButton:hover {{ background:{ALMA_GREEN_LIGHT}; }}"
        f"QPushButton:default {{ background:{ALMA_GREEN_DARK}; }}"
    )


def style_native_dialog(dialog):
    """Apply :func:`native_dialog_button_qss` to a native dialog and return it.

    Sets a cream dialog background + legible buttons in one shot, so a native
    QMessageBox/QDialog under an Enablement page never renders blank buttons.
    """
    dialog.setStyleSheet(f"QDialog, QMessageBox {{ background:{ALMA_WHITE}; }}"
                         + native_dialog_button_qss())
    return dialog

# kind → (tint background, text colour)
TINT = {
    "drive": ("#E4ECF5", ALMA_INFO), "guru": ("#DCEFEC", _TEAL),
    "asana": ("#F8E6E1", "#C2543F"), "high": ("#F7E2E2", ALMA_ERROR),
    "normal": ("#ECEAE5", ALMA_TEXT_LIGHT), "low": ("#E6EDEF", "#5A7A86"),
    "open": ("#E2ECF4", ALMA_INFO), "in_progress": ("#F6EBDD", ALMA_WARNING),
    "done": ("#E4EFE9", ALMA_SUCCESS), "draft": ("#F6EBDD", ALMA_WARNING),
}
DOT = {"open": ALMA_INFO, "in_progress": ALMA_WARNING, "done": ALMA_SUCCESS}


def badge(text: str, kind: str) -> QLabel:
    bg, fg = TINT.get(kind, ("#ECEAE5", ALMA_TEXT_LIGHT))
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:10px; padding:2px 10px; "
        f"font-size:11px; font-weight:600; border:none;"
    )
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lbl


def pill(text: str, bg: str, fg: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background:{bg}; color:{fg}; border-radius:12px; padding:4px 11px; "
        f"font-size:12px; font-weight:600; border:none;"
    )
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return lbl


def card_frame() -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER_LIGHT}; "
        f"border-radius:12px;}}"
    )
    return f


def section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{ALMA_TEXT_LIGHT}; font-size:11px; font-weight:700; "
        f"letter-spacing:0.7px; border:none;"
    )
    return lbl


def field(text: str, w: int | None = None, strong: bool = False) -> QLabel:
    """A read-only widget that looks like a form field / dropdown value."""
    lbl = QLabel(text)
    lbl.setFixedHeight(30)
    if w:
        lbl.setFixedWidth(w)
        lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    else:
        lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    color = ALMA_TEXT_DARK if strong else ALMA_TEXT_MID
    lbl.setStyleSheet(
        f"background:{ALMA_BG_ELEVATED}; color:{color}; border:1px solid {ALMA_BORDER}; "
        f"border-radius:7px; padding:4px 12px; font-size:12.5px;"
    )
    return lbl


class Toggle(QFrame):
    """A clickable on/off switch (track + knob, no glyphs). Flips on click."""

    def __init__(self, on: bool = True, parent=None):
        super().__init__(parent)
        self.on = on
        self.setFixedSize(34, 18)
        self.setCursor(Qt.PointingHandCursor)
        self._knob = QLabel(self)
        self._knob.setFixedSize(14, 14)
        self._render()

    def _render(self):
        self.setStyleSheet(
            f"background:{ALMA_GREEN_LIGHT if self.on else ALMA_BORDER}; border-radius:9px; border:none;"
        )
        self._knob.setStyleSheet("background:white; border-radius:7px;")
        self._knob.move(18 if self.on else 2, 2)

    def mousePressEvent(self, e):
        self.on = not self.on
        self._render()
        super().mousePressEvent(e)


def toggle(on: bool = True) -> "Toggle":
    return Toggle(on)
