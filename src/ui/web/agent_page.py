"""The embedded Agent chat page: a ``QWebEngineView`` hosting the web UI,
bridged to Python via ``QWebChannel``.

In-process only — the static UI loads from disk via ``file://`` (production will
swap in the compiled React bundle via ``qrc:``); the JS<->Python transport is
QWebChannel over the Qt event loop. **No web server, no localhost.**
"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .chat_bridge import ChatBridge

_STATIC = os.path.join(os.path.dirname(__file__), "static")


class AgentPage(QWidget):
    """A self-contained Agent chat surface. ``engine`` is the chat runtime
    (a real ``ChatEngine`` in production, a fake in tests)."""

    def __init__(self, engine, send_fn=None, tool_poll=None, parent=None):
        super().__init__(parent)
        self.bridge = ChatBridge(engine, send_fn=send_fn, tool_poll=tool_poll, parent=self)
        self.view = QWebEngineView(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("almaBridge", self.bridge)
        self.view.page().setWebChannel(self._channel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.load()

    def load(self):
        self.view.setUrl(QUrl.fromLocalFile(os.path.join(_STATIC, "index.html")))
