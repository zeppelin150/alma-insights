"""Slide-deck preview — render an outline as styled slide cards.

Shared by the PowerPoint tab's inline preview and its expand overlay so
both show the same deck-like view. An outline is
{title, slides:[{title, bullets[]}]}.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from src.ui.pages.enablement._common import card_frame
from src.ui.theme import (
    ALMA_ACCENT_TEAL, ALMA_BG_INSET, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
)


def slide_card(index: int, slide: dict) -> QFrame:
    card = card_frame()
    card.setObjectName("SlideCard")
    v = QVBoxLayout(card)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)

    # thin teal accent rail across the top — gives it a "slide" feel
    rail = QFrame()
    rail.setFixedHeight(4)
    rail.setStyleSheet(
        f"background:{ALMA_ACCENT_TEAL}; border-top-left-radius:8px; "
        f"border-top-right-radius:8px; border:none;")
    v.addWidget(rail)

    body = QVBoxLayout()
    body.setContentsMargins(16, 12, 16, 14)
    body.setSpacing(7)

    head = QHBoxLayout()
    head.setSpacing(8)
    num = QLabel(str(index))
    num.setFixedSize(20, 20)
    num.setAlignment(Qt.AlignCenter)
    num.setStyleSheet(
        f"background:{ALMA_BG_INSET}; color:{ALMA_TEXT_MID}; border-radius:10px; "
        f"font-size:10px; font-weight:700;")
    head.addWidget(num)
    title = QLabel(slide.get("title", "Slide"))
    title.setWordWrap(True)
    title.setStyleSheet(
        f"color:{ALMA_TEXT_DARK}; font-size:15px; font-weight:700; border:none;")
    head.addWidget(title, 1)
    body.addLayout(head)

    bullets = [b for b in (slide.get("bullets") or []) if str(b).strip()]
    if bullets:
        for b in bullets:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setContentsMargins(6, 0, 0, 0)
            dot = QLabel("•")
            dot.setStyleSheet(
                f"color:{ALMA_ACCENT_TEAL}; font-size:13px; border:none;")
            dot.setFixedWidth(10)
            row.addWidget(dot, 0, Qt.AlignTop)
            txt = QLabel(str(b))
            txt.setWordWrap(True)
            txt.setStyleSheet(
                f"color:{ALMA_TEXT_DARK}; font-size:12.5px; line-height:140%; border:none;")
            row.addWidget(txt, 1)
            body.addLayout(row)
    else:
        empty = QLabel("(no bullets)")
        empty.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:11.5px; font-style:italic; border:none;")
        body.addWidget(empty)

    v.addLayout(body)
    return card


def render_slides(layout, outline: dict) -> None:
    """Clear `layout` and (re)populate it with a slide card per slide."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
    slides = (outline or {}).get("slides") or []
    if not slides:
        empty = QLabel("No slides yet — model a deck from a topic or a document.")
        empty.setStyleSheet(
            f"color:{ALMA_TEXT_LIGHT}; font-size:12.5px; border:none; padding:10px 2px;")
        layout.addWidget(empty)
        return
    for i, s in enumerate(slides, start=1):
        layout.addWidget(slide_card(i, s))
    layout.addStretch(1)
