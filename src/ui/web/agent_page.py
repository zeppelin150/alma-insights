"""The embedded Agent chat page: a ``QWebEngineView`` hosting the web UI,
bridged to Python via ``QWebChannel``.

In-process only — the UI loads from disk via ``file://`` (the built React bundle
``dist/index.html`` when present, else the minimal spike ``static/index.html``);
the JS<->Python transport is QWebChannel over the Qt event loop. **No web server,
no localhost.** ``qwebchannel.js`` rides alongside as a classic script.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .chat_bridge import ChatBridge

_HERE = os.path.dirname(__file__)
_DIST = os.path.join(_HERE, "dist", "index.html")        # built React bundle
_SPIKE = os.path.join(_HERE, "static", "index.html")     # fallback (no build)


class AgentPage(QWidget):
    """A self-contained Agent chat surface. ``engine`` is the chat runtime
    (a real ``ChatEngine`` in production, a fake in tests)."""

    def __init__(self, engine, send_fn=None, tool_poll=None, session_api=None,
                 job_poll=None, draft_api=None, voice=None, action_poll=None,
                 connect_fn=None, google_state_signal=None, list_fn=None,
                 resolve_fn=None, drive_folders_signal=None,
                 action_resolved_signal=None, asana_list_fn=None,
                 asana_resolve_fn=None, asana_projects_signal=None,
                 guru_list_fn=None, guru_resolve_fn=None,
                 guru_targets_signal=None, confirm_fn=None, cancel_fn=None,
                 parent=None):
        super().__init__(parent)
        self.bridge = ChatBridge(engine, send_fn=send_fn, tool_poll=tool_poll,
                                 session_api=session_api, job_poll=job_poll,
                                 draft_api=draft_api, voice=voice,
                                 action_poll=action_poll, connect_fn=connect_fn,
                                 google_state_signal=google_state_signal,
                                 list_fn=list_fn, resolve_fn=resolve_fn,
                                 drive_folders_signal=drive_folders_signal,
                                 action_resolved_signal=action_resolved_signal,
                                 asana_list_fn=asana_list_fn,
                                 asana_resolve_fn=asana_resolve_fn,
                                 asana_projects_signal=asana_projects_signal,
                                 guru_list_fn=guru_list_fn,
                                 guru_resolve_fn=guru_resolve_fn,
                                 guru_targets_signal=guru_targets_signal,
                                 confirm_fn=confirm_fn, cancel_fn=cancel_fn,
                                 parent=self)
        self.view = QWebEngineView(self)
        # Let the file:// page load its sibling qwebchannel.js (classic script).
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            self.view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        except Exception:  # noqa: BLE001 — default already permits sibling files
            pass
        self._channel = QWebChannel(self)
        self._channel.registerObject("almaBridge", self.bridge)
        self.view.page().setWebChannel(self._channel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.load()

    def load(self):
        path = _DIST if os.path.exists(_DIST) else _SPIKE
        self.view.setUrl(QUrl.fromLocalFile(path))
