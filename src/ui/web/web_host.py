"""Shared ``QWebEngineView`` host for the embedded web UI.

One widget owns everything every web surface needs (the Agent chat today; the
enablement Calendar/Workbench tabs behind ``enablement.web_tabs``): the view +
diagnostics, QWebChannel registration, bundle resolution, and optional Qt-side
drag-drop forwarding. The SPA is a single ``dist/index.html`` bundle with hash
routes — each host loads the same file with its own ``route`` fragment
(``""`` → chat, ``"/calendar"``, ``"/workbench"``).

In-process only — the UI loads from disk via ``file://``; the JS<->Python
transport is QWebChannel over the Qt event loop. **No web server, no
localhost.** ``qwebchannel.js`` rides alongside as a classic script.

Diagnostics are first-class (the macOS blank-render lesson): the page's
``console.*`` output, uncaught JS errors, and renderer-process deaths all land
in the Python log, so a blank page is observable without a devtools port.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage,
                                     QWebEngineUrlRequestInterceptor)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

_HERE = os.path.dirname(__file__)
_DIST = os.path.join(_HERE, "dist", "index.html")        # built React bundle
_SPIKE = os.path.join(_HERE, "static", "index.html")     # fallback (no build)

_diag_logged = False


def _startup_diag(logger):
    """Under ALMA_WEB_DIAG=1, log the full web_diag fact sheet ONCE per
    process, on the first WebHost construction."""
    global _diag_logged
    if _diag_logged or not os.environ.get("ALMA_WEB_DIAG"):
        return
    _diag_logged = True
    try:
        from .web_diag import log_report
        log_report(logger)
    except Exception:  # noqa: BLE001
        pass

# JS console level → Python log level. Normal console.log stays at DEBUG (quiet);
# only page warnings/errors surface by default.
_JS_LEVELS = {
    QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: logging.DEBUG,
    QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: logging.WARNING,
    QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: logging.ERROR,
}


class LoggingPage(QWebEnginePage):
    """A ``QWebEnginePage`` that routes the web page's ``console.*`` output,
    uncaught JS errors, and CORS / resource-load failures into the Python log.

    This is the primary diagnostic for a blank web page: a JS exception or a
    blocked ``file://`` module load prints here with its source + line, so the
    cause is observable without opening a devtools port (airgap-safe)."""

    def __init__(self, logger, parent=None):
        super().__init__(parent)
        self._logger = logger

    def javaScriptConsoleMessage(self, level, message, line, source_id):  # noqa: N802 (Qt override)
        self._logger.log(_JS_LEVELS.get(level, logging.INFO),
                         "JS console %s:%s — %s", source_id, line, message)


class RequestLogger(QWebEngineUrlRequestInterceptor):
    """Logs every subresource request the page issues (e.g. each
    ``file://…/assets/*.js`` chunk) so a blocked/failed fetch is visible.
    Installed only in debug mode (``ALMA_AGENT_DEVTOOLS``)."""

    def __init__(self, logger, parent=None):
        super().__init__(parent)
        self._logger = logger

    def interceptRequest(self, info):  # noqa: N802 (Qt override)
        try:
            self._logger.debug("REQ %s %s",
                               bytes(info.requestMethod()).decode("ascii", "replace"),
                               info.requestUrl().toString())
        except Exception:  # noqa: BLE001 — logging must never break a request
            pass


class WebHost(QWidget):
    """A self-contained web-UI surface: view + diagnostics + channel + route.

    ``bridge`` (a QObject, or None) is registered on the page's QWebChannel
    under ``channel_name`` — the ONLY JS<->Python boundary for this surface.
    ``route`` is the SPA hash route fragment WITHOUT the '#' (e.g.
    ``"/calendar"``); empty means the default route (chat).

    ``accept_drops=True`` turns on Qt-side drag-drop forwarding: the view stops
    accepting drops (so Chromium doesn't swallow them) and a local file dropped
    anywhere on the host emits ``dropped(path)``. Web-side ``File`` objects
    carry no filesystem paths, so native drops MUST be handled on the Qt side.
    """

    dropped = Signal(str)   # local file path dropped onto the host

    def __init__(self, bridge=None, channel_name="almaBridge", route="",
                 accept_drops=False, log_name="alma.web", extra_bridges=None,
                 parent=None):
        super().__init__(parent)
        self._logger = logging.getLogger(log_name)
        _startup_diag(self._logger)
        self._route = route or ""
        self.view = QWebEngineView(self)
        # Route the page's console / JS errors / resource-load failures into the
        # Python log — the primary diagnostic for a blank page. Set BEFORE the
        # settings + channel calls so those apply to this page.
        self._page = LoggingPage(self._logger, self.view)
        self.view.setPage(self._page)
        self.view.loadFinished.connect(self._on_load_finished)
        self._page.renderProcessTerminated.connect(self._on_render_terminated)
        # Let the file:// page load its sibling qwebchannel.js (classic script).
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            self.view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        except Exception:  # noqa: BLE001 — default already permits sibling files
            pass
        # One channel may publish several objects (e.g. the Workbench tab
        # carries workbenchBridge + the shared almaBridge chat drawer, M5.5).
        self._channel = None
        objects = {}
        if bridge is not None:
            objects[channel_name] = bridge
        for name, obj in (extra_bridges or {}).items():
            if obj is not None:
                objects[name] = obj
        if objects:
            self._channel = QWebChannel(self)
            for name, obj in objects.items():
                # QWebChannel does NOT take ownership: a parentless bridge
                # passed as a temporary gets garbage-collected and the channel
                # then dereferences freed memory (a native access violation —
                # found by the M6 RSS probe). Claim ownership of orphans so
                # the crash class is impossible; bridges that already have a
                # parent (e.g. ChatBridge parented to the page) keep it.
                if obj.parent() is None:
                    obj.setParent(self)
                self._channel.registerObject(name, obj)
            self.view.page().setWebChannel(self._channel)

        # Optional full Chromium DevTools + per-request logging, all in-process
        # (NO network port). Enable with ALMA_AGENT_DEVTOOLS=1.
        if os.environ.get("ALMA_AGENT_DEVTOOLS"):
            self._install_devtools()

        if accept_drops:
            # Chromium normally consumes drag-drop; hand it to Qt instead so the
            # host can forward real filesystem paths.
            self.setAcceptDrops(True)
            self.view.setAcceptDrops(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.load()

    def on_page_shown(self):
        """Refresh hook fired when this host becomes the active page again.

        A view that merely failed to LOAD (transient blank) recovers by
        reloading the bundle. This cannot revive a hard-crashed renderer
        process — that path falls back to the native page elsewhere — but it
        fixes the common "came back to a blank tab" case at no cost."""
        try:
            ok = self.view.page().url().isValid()
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            self.load()

    # ── bundle resolution + route ─────────────────────────────────────
    def load(self):
        path = _DIST if os.path.exists(_DIST) else _SPIKE
        if not os.path.exists(path):
            self._logger.error(
                "Web UI bundle missing — neither %s nor %s exists; the page "
                "will be blank. (Did the build/copy skip the web assets?)",
                _DIST, _SPIKE)
        url = QUrl.fromLocalFile(path)
        if self._route:
            url.setFragment(self._route)
        self.view.setUrl(url)

    # ── diagnostics ──────────────────────────────────────────────────
    def _on_load_finished(self, ok: bool):
        if ok:
            self._logger.debug("Web page loadFinished ok — %s",
                               self.view.url().toString())
        else:
            self._logger.error("Web page FAILED to load (loadFinished ok=False) — %s",
                               self.view.url().toString())

    def _on_render_terminated(self, status, exit_code: int):
        self._logger.error(
            "Web renderer process terminated: status=%s exitCode=%s "
            "(blank page follows). On macOS this is usually signing/"
            "entitlements or a sandbox kill of QtWebEngineProcess.",
            int(status), exit_code)
        # The moment a renderer dies, dump the OS-aware fact sheet — every
        # known blank-page cause (Rosetta, arch mismatch, corrupt frameworks,
        # missing bundle) is a probe in web_diag.
        try:
            from .web_diag import log_report
            log_report(self._logger)
        except Exception:  # noqa: BLE001 — diagnostics never take the app down
            pass

    def _install_devtools(self):
        """Full Chromium DevTools in a separate in-process window (NO port) plus
        per-request logging. Gated by ALMA_AGENT_DEVTOOLS so production stays
        clean. Airgap-safe: the inspector runs over the Qt event loop, not a
        socket."""
        try:
            self._req_logger = RequestLogger(self._logger, self)
            self.view.page().profile().setUrlRequestInterceptor(self._req_logger)
            self._devview = QWebEngineView()  # kept as an attr so it isn't GC'd
            self._devview.setWindowTitle("Web DevTools")
            self._devview.resize(1000, 720)
            self.view.page().setDevToolsPage(self._devview.page())
            self._devview.show()
            self._logger.warning("Web DevTools enabled (ALMA_AGENT_DEVTOOLS) — no "
                                 "network port opened.")
        except Exception as exc:  # noqa: BLE001 — devtools must never break the page
            self._logger.warning("Web DevTools setup failed: %s", exc)

    # ── Qt-side drag-drop forwarding (accept_drops=True only) ────────
    def dragEnterEvent(self, e):
        if self.acceptDrops() and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        # Windows rejects the drop if the move isn't also accepted, even after
        # dragEnter accepted — keep saying yes for file drags.
        if self.acceptDrops() and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not self.acceptDrops():
            return
        urls = e.mimeData().urls()
        if urls:
            e.acceptProposedAction()
            path = urls[0].toLocalFile()
            if path:
                self.dropped.emit(path)
