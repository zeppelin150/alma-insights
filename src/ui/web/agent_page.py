"""The embedded Agent chat page: a ``QWebEngineView`` hosting the web UI,
bridged to Python via ``QWebChannel``.

In-process only — the UI loads from disk via ``file://`` (the built React bundle
``dist/index.html`` when present, else the minimal spike ``static/index.html``);
the JS<->Python transport is QWebChannel over the Qt event loop. **No web server,
no localhost.** ``qwebchannel.js`` rides alongside as a classic script.

The view/diagnostics/channel plumbing lives in the shared
:class:`~src.ui.web.web_host.WebHost` (also used by the enablement web tabs);
this module owns only the Agent-specific ``ChatBridge`` wiring.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .chat_bridge import ChatBridge
from .web_host import _DIST, _SPIKE, WebHost  # noqa: F401 — path constants re-exported for tests


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
        # The chat is the SPA's default route — no fragment, so pre-router
        # bundles keep working unchanged.
        self.host = WebHost(bridge=self.bridge, channel_name="almaBridge",
                            route="", log_name="alma.agent.web", parent=self)
        # Back-compat surface: tests and callers reach the view directly.
        self.view = self.host.view
        self._page = self.host._page

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.host)

    def load(self):
        self.host.load()
