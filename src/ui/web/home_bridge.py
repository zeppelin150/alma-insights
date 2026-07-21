"""QWebChannel bridge for the web Home page — pure relay, zero logic.

Registered as ``homeBridge`` on the Home WebHost's channel. Like
``CalendarBridge``, everything is injected (signals + callables from a
``HomeWebController``), never imported, so this module stays the only
JS<->Python boundary and tests supply fakes.

QWebChannel is the trust boundary: any script in the page can call these
slots, so none of them carry authority. They forward to controller methods
that validate against Python-held state — the mode allowlist, the quick-action
set for the mode Python holds, the known activity kinds — and, for the one
consequential slot (``requestModeSwitch``), the single-winner claim plus a
NATIVE confirm dialog.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class HomeBridge(QObject):
    """The single JS<->Python boundary for the web Home page."""

    homeData = Signal(str)   # JSON viewmodel: see HomeWebController._build_payload

    def __init__(self, data_signal=None, refresh_fn=None, quick_action_fn=None,
                 activity_fn=None, mode_switch_fn=None, parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self._quick_action_fn = quick_action_fn
        self._activity_fn = activity_fn
        self._mode_switch_fn = mode_switch_fn
        # Re-emit the controller's viewmodel signal as the bridge's.
        if data_signal is not None:
            try:
                data_signal.connect(self.homeData)
            except Exception:  # noqa: BLE001 — best-effort wiring
                pass

    # ── inbound (JS -> Python; untrusted) ────────────────────────────
    @Slot()
    def refresh(self):
        """The page mounted (or wants a re-push) → replay the current state."""
        if self._refresh_fn is None:
            return
        try:
            self._refresh_fn()
        except Exception:  # noqa: BLE001 — never crash the boot page
            pass

    @Slot(str)
    def quickAction(self, key):
        """A quick-action chip was clicked. The controller validates the key
        against the actions for the mode PYTHON holds; unknown keys no-op."""
        if self._quick_action_fn is None:
            return
        try:
            self._quick_action_fn(key or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(str)
    def activityActivated(self, kind):
        """A recent-activity row was clicked → the controller resolves the kind
        and MainWindow applies its existing mode-aware destination map."""
        if self._activity_fn is None:
            return
        try:
            self._activity_fn(kind or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(str)
    def requestModeSwitch(self, mode):
        """A mode tile's switch button was clicked.

        This slot NEVER switches anything itself. The controller validates
        against Python-held state, claims a single-winner inflight flag,
        raises the NATIVE confirm (unreachable from any page script), and only
        then dispatches the switch deferred off this stack frame. A forged
        invoke — or a second one during an open dialog — is a silent no-op.
        """
        if self._mode_switch_fn is None:
            return
        try:
            self._mode_switch_fn(mode or "")
        except Exception:  # noqa: BLE001
            pass

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip."""
        return "pong"
