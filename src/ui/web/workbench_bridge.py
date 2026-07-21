"""QWebChannel bridge for the web Workbench tab — pure relay, zero logic.

Registered as ``workbenchBridge`` on the Workbench WebHost's channel.
Everything is injected from a ``WorkbenchWebController`` (signals +
callables), never imported, so this stays the only JS<->Python boundary and
tests supply fakes.

QWebChannel is the trust boundary: any script in the page can call these
slots, so none carries authority. M3 is read-only — workspace switch/close
resolve against the controller's OPEN chips (forged ids no-op), the diff and
preview are computed Python-side, and upload only *asks* the host to open its
native file dialog. Publishing stays out of the bridge entirely until M4's
native-confirm flow.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class WorkbenchBridge(QObject):
    """The single JS<->Python boundary for the web Workbench."""

    workbenchData = Signal(str)    # JSON {chips, active_id}
    draftLoaded = Signal(str)      # JSON card viewmodel (preview pre-sanitized)
    diffReady = Signal(str)        # JSON {rows, change_count, baseline_present}
    previewUpdated = Signal(str)   # JSON {id, preview_html, checks} — edit echo
    existingCards = Signal(str)    # JSON {items: [{key, title}]}
    publishResolved = Signal(str)  # JSON {request_id, dest, approved, dispatched, cancelled}
    aiEditResolved = Signal(str)   # JSON {ok} — un-busy the AI-edit bar

    def __init__(self, data_signal=None, draft_signal=None, diff_signal=None,
                 preview_signal=None, cards_signal=None, publish_signal=None,
                 ai_resolved_signal=None,
                 refresh_fn=None, switch_fn=None, close_fn=None, diff_fn=None,
                 upload_fn=None, find_task_fn=None, open_chat_fn=None,
                 edit_fn=None, ai_edit_fn=None, publish_fn=None,
                 import_fn=None, cards_fn=None, parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self._switch_fn = switch_fn
        self._close_fn = close_fn
        self._diff_fn = diff_fn
        self._upload_fn = upload_fn
        self._find_task_fn = find_task_fn
        self._open_chat_fn = open_chat_fn
        self._edit_fn = edit_fn
        self._ai_edit_fn = ai_edit_fn
        self._publish_fn = publish_fn
        self._import_fn = import_fn
        self._cards_fn = cards_fn
        for sig, mine in ((data_signal, self.workbenchData),
                          (draft_signal, self.draftLoaded),
                          (diff_signal, self.diffReady),
                          (preview_signal, self.previewUpdated),
                          (cards_signal, self.existingCards),
                          (publish_signal, self.publishResolved),
                          (ai_resolved_signal, self.aiEditResolved)):
            if sig is not None:
                try:
                    sig.connect(mine)
                except Exception:  # noqa: BLE001 — best-effort wiring
                    pass

    # ── inbound (JS -> Python; untrusted) ────────────────────────────
    def _call(self, fn, *args):
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — never crash the tab
            pass

    @Slot()
    def refresh(self):
        """Page mounted (or wants a re-push) → replay chips + active draft."""
        self._call(self._refresh_fn)

    @Slot(str)
    def switchWorkspace(self, draft_id):
        """Chip clicked → controller validates against OPEN chips, then the
        host loads the draft (same loop as the Qt chip click)."""
        self._call(self._switch_fn, draft_id or "")

    @Slot(str)
    def closeWorkspace(self, draft_id):
        """Chip × clicked → controller validates, host closes + reflows."""
        self._call(self._close_fn, draft_id or "")

    @Slot()
    def requestDiff(self):
        """Review-changes view opened → word-level diff computed in Python."""
        self._call(self._diff_fn)

    @Slot()
    def uploadRequested(self):
        """Upload button → the host opens its NATIVE file dialog (the page
        never sees the filesystem)."""
        self._call(self._upload_fn)

    @Slot()
    def findTask(self):
        """'+ Find a task' → the host opens its native search dialog."""
        self._call(self._find_task_fn)

    @Slot()
    def openChat(self):
        """'Open Assistant' → the host raises the shared chat drilldown."""
        self._call(self._open_chat_fn)

    @Slot(str, str)
    def contentEdited(self, draft_id, md):
        """Editor commit-on-leave (M4). The controller enforces active-
        workspace-only editing and the markdown-clears-rich-HTML parity rule;
        page.py's existing _on_content_edited persists (pushed drafts frozen)."""
        self._call(self._edit_fn, draft_id or "", md if md is not None else "")

    @Slot(str, str)
    def aiEdit(self, instruction, selection):
        """Slash-menu / highlight-to-edit ask (M4) → the host's existing
        off-thread revise; the result returns via draftLoaded."""
        self._call(self._ai_edit_fn, instruction or "", selection or "")

    @Slot(str)
    def requestPublish(self, dest_key):
        """Publish clicked (M4). The controller validates the destination
        against its allowlist, claims a single-winner flag, and raises the
        NATIVE confirm (unreachable from any page script) before page.py's
        publish path runs. A forged destination is a silent no-op."""
        self._call(self._publish_fn, dest_key or "")

    @Slot(str)
    def requestImport(self, kind):
        """Import asked (M4) — the host opens its NATIVE picker dialogs."""
        self._call(self._import_fn, kind or "")

    @Slot()
    def listExistingCards(self):
        """'Existing Guru card' submenu opened → host fetch → existingCards."""
        self._call(self._cards_fn)

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip."""
        return "pong"
