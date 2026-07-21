"""QWebChannel bridge for the web Calendar tab — pure relay, zero logic.

Registered as ``calendarBridge`` on the Calendar WebHost's channel. Like
``ChatBridge``, everything is injected (signals + callables from a
``CalendarWebController``), never imported, so this stays the only JS<->Python
boundary and tests supply fakes.

QWebChannel is the trust boundary: any script in the page can call these
slots, so none of them carry authority — they delegate to controller methods
that validate against Python-side state (known task ids, known scopes) and,
for anything consequential in later milestones, the request-row single-winner
pattern. M1 is read-only: no slot here writes anything anywhere.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class CalendarBridge(QObject):
    """The single JS<->Python boundary for the web Calendar."""

    calendarData = Signal(str)        # JSON viewmodel: {events, scope, today}
    briefReady = Signal(str)          # JSON: {task_id, brief|null}
    rescheduleResolved = Signal(str)  # JSON: {request_id, task_id, date, approved, dispatched, cancelled}

    def __init__(self, data_signal=None, brief_signal=None, refresh_fn=None,
                 scope_fn=None, open_fn=None, brief_fn=None,
                 reschedule_fn=None, resolved_signal=None, parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self._scope_fn = scope_fn
        self._open_fn = open_fn
        self._brief_fn = brief_fn
        self._reschedule_fn = reschedule_fn
        # Re-emit the controller's signals as the bridge's (signal-to-signal).
        if data_signal is not None:
            try:
                data_signal.connect(self.calendarData)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass
        if brief_signal is not None:
            try:
                brief_signal.connect(self.briefReady)
            except Exception:  # noqa: BLE001
                pass
        if resolved_signal is not None:
            try:
                resolved_signal.connect(self.rescheduleResolved)
            except Exception:  # noqa: BLE001
                pass

    # ── inbound (JS -> Python; untrusted) ────────────────────────────
    @Slot()
    def refresh(self):
        """The page mounted (or wants a re-push) → replay the current state."""
        if self._refresh_fn is None:
            return
        try:
            self._refresh_fn()
        except Exception:  # noqa: BLE001 — never crash the tab
            pass

    @Slot(str)
    def setScope(self, scope):
        """Mine/All toggled in the web UI. The controller validates the value
        and the host refilters + persists exactly like the Qt toggle."""
        if self._scope_fn is None:
            return
        try:
            self._scope_fn(scope or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(str)
    def openTask(self, task_id):
        """Chip clicked → the controller resolves the id against the last feed
        (forged/stale ids are no-ops) and the host opens its native panel."""
        if self._open_fn is None:
            return
        try:
            self._open_fn(task_id or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(str)
    def requestBrief(self, task_id):
        """Hover card asked for the stored Haiku brief → ``briefReady``."""
        if self._brief_fn is None:
            return
        try:
            self._brief_fn(task_id or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(str, str)
    def requestReschedule(self, task_id, new_date):
        """Chip dropped on a new day (M2). The controller validates against
        Python state, claims a single-winner inflight flag, raises the NATIVE
        confirm dialog (unreachable from any page script), and only then
        dispatches the CAS-guarded write-back. A forged invoke without a real
        matching task — or during an open dialog — is a silent no-op."""
        if self._reschedule_fn is None:
            return
        try:
            self._reschedule_fn(task_id or "", new_date or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip."""
        return "pong"
