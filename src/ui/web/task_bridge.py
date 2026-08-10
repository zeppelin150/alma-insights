"""QWebChannel bridge for the web task drilldown — pure relay, zero logic.

Registered as ``taskBridge`` on the drilldown WebHost's channel. Like
``CalendarBridge``, everything is injected (signals + callables from a
``TaskWebController``), never imported, so this stays the only JS<->Python
boundary and tests supply fakes.

QWebChannel is the trust boundary: any script in the page can call these
slots, so none of them carry authority — they delegate to controller methods
that validate against the last-pushed viewmodel (current task id, served
attachment gids, served link urls) behind a single-winner inflight claim.
Writes only relay onward to the host's existing CAS-guarded
``_run_task_writeback`` lanes.

RULE (cee4c86): every connection on this object is created HERE, in
``__init__``, BEFORE WebHost registers it on the channel. Nothing may create
a connection or dynamic slot on this bridge after registration — PySide6's
functional ``QTimer.singleShot(ms, bridge_method)`` grows the metaobject and
silently kills ALL subsequent Python→JS signal delivery.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class TaskBridge(QObject):
    """The single JS<->Python boundary for the web task panel."""

    taskData = Signal(str)        # JSON viewmodel (task_vm.build_task_vm)
    statusText = Signal(str)      # plain status line text
    actionResolved = Signal(str)  # JSON {action, task_id, ok, error}

    def __init__(self, data_signal=None, status_signal=None,
                 resolved_signal=None, refresh_fn=None, refresh_task_fn=None,
                 complete_fn=None, due_fn=None, comment_fn=None,
                 subtask_fn=None, attachment_fn=None, url_fn=None,
                 parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self._refresh_task_fn = refresh_task_fn
        self._complete_fn = complete_fn
        self._due_fn = due_fn
        self._comment_fn = comment_fn
        self._subtask_fn = subtask_fn
        self._attachment_fn = attachment_fn
        self._url_fn = url_fn
        # Re-emit the controller's signals as the bridge's (signal-to-signal).
        for sig, mine in ((data_signal, self.taskData),
                          (status_signal, self.statusText),
                          (resolved_signal, self.actionResolved)):
            if sig is not None:
                try:
                    sig.connect(mine)
                except Exception:  # noqa: BLE001 — best-effort wiring
                    pass

    def _call(self, fn, *args):
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — never crash the panel
            pass

    # ── inbound (JS -> Python; untrusted) ────────────────────────────
    @Slot()
    def refresh(self):
        """The page mounted (or wants a re-push) → replay the current state."""
        self._call(self._refresh_fn)

    @Slot(str)
    def refreshTask(self, task_id):
        """Refresh row clicked → one on-demand extras refetch for the CURRENT
        task only (the controller checks the id; forged/stale ids no-op)."""
        self._call(self._refresh_task_fn, task_id or "")

    @Slot(str, bool)
    def toggleComplete(self, task_id, done):
        """Mark complete / Reopen → the host's CAS-guarded writeback lane."""
        self._call(self._complete_fn, task_id or "", bool(done))

    @Slot(str, str)
    def setDue(self, task_id, iso_date):
        """Due date picked → the host's CAS-guarded due-update lane."""
        self._call(self._due_fn, task_id or "", iso_date or "")

    @Slot(str, str)
    def postComment(self, task_id, text):
        """Composer submitted → the host's comment lane (append-only)."""
        self._call(self._comment_fn, task_id or "", text or "")

    @Slot(str, str)
    def addSubtask(self, task_id, text):
        """Subtask composer submitted → the host's subtask lane."""
        self._call(self._subtask_fn, task_id or "", text or "")

    @Slot(str)
    def openAttachment(self, gid):
        """Attachment clicked → the controller resolves a fresh view_url
        off-thread (gids validated against the last-pushed viewmodel) and
        opens http(s) only."""
        self._call(self._attachment_fn, gid or "")

    @Slot(str)
    def openUrl(self, url):
        """A link in the feed/header clicked → opens ONLY if the exact url
        exists in the last-pushed viewmodel's link registry."""
        self._call(self._url_fn, url or "")

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip."""
        return "pong"
