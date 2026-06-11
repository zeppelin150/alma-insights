"""Standalone Assistant chat panel — hosted in the shared DrilldownPanel.

Extracted from the Workbench so the chat can "occupy the drilldown" (the app's
right-side slide-out) like ab_compare's chat. The host wires the signals to the
ChatEngine + enablement_store; this widget owns no provider/DB logic.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.ui.theme import (
    ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_GREEN_DARK, ALMA_GREEN_LIGHT,
    ALMA_TEXT_DARK, ALMA_TEXT_LIGHT, ALMA_TEXT_ON_DARK,
)


class ChatPanel(QWidget):
    """The Assistant chat surface (header + transcript + quick actions + input)."""

    chat_submitted = Signal(str)
    push_requested = Signal()
    revise_requested = Signal()
    draft_subtasks_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{ALMA_BG_ELEVATED};")
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QFrame()
        head.setStyleSheet("QFrame{background:#F7F5F0; border:none;}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        dot = QLabel("")
        dot.setFixedSize(18, 18)
        dot.setStyleSheet(f"background:{ALMA_GREEN_LIGHT}; border-radius:9px;")
        hl.addWidget(dot)
        title = QLabel("Renn")
        title.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:13.5px; font-weight:700; border:none;")
        hl.addWidget(title)
        sub = QLabel("· Enablement assistant")
        sub.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:11px; border:none;")
        hl.addWidget(sub)
        hl.addStretch(1)
        self._prov = QLabel("")
        self._prov.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:10.5px; border:none;")
        hl.addWidget(self._prov)
        v.addWidget(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea{border:none; background:transparent;}")
        self._holder = QWidget()
        self._holder.setStyleSheet("background:transparent;")
        self._cv = QVBoxLayout(self._holder)
        self._cv.setContentsMargins(14, 12, 14, 12)
        self._cv.setSpacing(10)
        self._cv.addStretch(1)
        self._scroll.setWidget(self._holder)
        v.addWidget(self._scroll, 1)

        qa = QHBoxLayout()
        qa.setContentsMargins(14, 0, 14, 8)
        qa.setSpacing(8)
        for label, sig in (("Revise", self.revise_requested),
                           ("Draft subtasks", self.draft_subtasks_requested),
                           ("Push to Guru", self.push_requested)):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:#EBEFEA; color:{ALMA_GREEN_DARK}; border:none; "
                f"border-radius:12px; padding:5px 12px; font-size:11.5px; font-weight:600;}}"
            )
            b.clicked.connect(lambda _=False, s=sig: s.emit())
            qa.addWidget(b)
        qa.addStretch(1)
        v.addLayout(qa)

        inrow = QHBoxLayout()
        inrow.setContentsMargins(14, 0, 14, 14)
        inrow.setSpacing(8)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask to edit or publish…")
        self._input.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid {ALMA_BORDER}; "
            f"border-radius:10px; padding:8px 12px; font-size:12.5px; color:{ALMA_TEXT_DARK};}}"
        )
        self._input.returnPressed.connect(self._on_send)
        inrow.addWidget(self._input, 1)
        send = QPushButton(">")
        send.setFixedSize(34, 34)
        send.setCursor(Qt.PointingHandCursor)
        send.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; font-size:16px; font-weight:700;}}"
        )
        send.clicked.connect(self._on_send)
        inrow.addWidget(send)
        v.addLayout(inrow)

    def set_chat(self, messages: list[tuple[str, str]]):
        while self._cv.count() > 1:
            item = self._cv.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for who, text in messages:
            self._append(who, text)

    def add_message(self, who: str, text: str):
        self._append(who, text)

    def set_provider_label(self, text: str):
        self._prov.setText(text or "")

    def _append(self, who: str, text: str):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(330)
        if who == "a":
            bubble.setStyleSheet(f"background:#EFF3F0; color:{ALMA_TEXT_DARK}; border-radius:10px; padding:9px 12px; font-size:12.5px;")
        else:
            bubble.setStyleSheet(f"background:{ALMA_GREEN_LIGHT}; color:{ALMA_TEXT_ON_DARK}; border-radius:10px; padding:9px 12px; font-size:12.5px;")
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        if who != "a":
            wrap.addStretch(1)
        wrap.addWidget(bubble)
        if who == "a":
            wrap.addStretch(1)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        holder.setLayout(wrap)
        self._cv.insertWidget(self._cv.count() - 1, holder)

    def _on_send(self):
        t = self._input.text().strip()
        if not t:
            return
        self._append("u", t)
        self._input.clear()
        self.chat_submitted.emit(t)
