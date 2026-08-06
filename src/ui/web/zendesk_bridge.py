"""QWebChannel bridge for the web Zendesk tab — pure relay, zero logic.

Registered as ``zendeskBridge`` on the Zendesk WebHost's channel.
Everything is injected from a ``ZendeskWebController`` (signals +
callables), never imported, so this stays the only JS<->Python boundary
and tests supply fakes.

QWebChannel is the trust boundary: any script in the page can call these
slots, so none carries authority. Every id/kind/scope is validated in the
controller against Python-held state, destructive slots run behind the
controller's single-winner claim + NATIVE confirm, and the pull/import
slots only *ask* the host to start its off-thread workers. Nothing here
can reach a Zendesk write path — the controller never emits the push
signals and holds no client.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class ZendeskBridge(QObject):
    """The single JS<->Python boundary for the web Zendesk tab."""

    zendeskData = Signal(str)      # JSON main viewmodel
    articleDetail = Signal(str)    # JSON article + sanitized body_srcdoc
    macroDetail = Signal(str)      # JSON macro + decoded actions
    revisionsData = Signal(str)    # JSON {filter, revisions}
    diffReady = Signal(str)        # JSON DiffBody rows
    importResolved = Signal(str)   # JSON import report verbatim
    pullResolved = Signal(str)     # JSON pull report verbatim
    copyResolved = Signal(str)     # JSON {request_id, target, field, ok, chars}
    actionResolved = Signal(str)   # JSON {request_id, action, ok, approved}
    statusText = Signal(str)       # plain status line text

    def __init__(self, data_signal=None, article_signal=None, macro_signal=None,
                 revisions_signal=None, diff_signal=None, import_signal=None,
                 pull_signal=None, copy_signal=None, action_signal=None,
                 status_signal=None,
                 refresh_fn=None, view_fn=None, open_article_fn=None,
                 open_macro_fn=None, search_fn=None, revisions_fn=None,
                 diff_fn=None, save_fn=None, save_body_edit_fn=None,
                 ready_fn=None, copied_fn=None,
                 copy_fn=None, import_fn=None, import_folder_fn=None,
                 pull_fn=None, delete_fn=None, purge_fn=None, parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self._view_fn = view_fn
        self._open_article_fn = open_article_fn
        self._open_macro_fn = open_macro_fn
        self._search_fn = search_fn
        self._revisions_fn = revisions_fn
        self._diff_fn = diff_fn
        self._save_fn = save_fn
        self._save_body_edit_fn = save_body_edit_fn
        self._ready_fn = ready_fn
        self._copied_fn = copied_fn
        self._copy_fn = copy_fn
        self._import_fn = import_fn
        self._import_folder_fn = import_folder_fn
        self._pull_fn = pull_fn
        self._delete_fn = delete_fn
        self._purge_fn = purge_fn
        for sig, mine in ((data_signal, self.zendeskData),
                          (article_signal, self.articleDetail),
                          (macro_signal, self.macroDetail),
                          (revisions_signal, self.revisionsData),
                          (diff_signal, self.diffReady),
                          (import_signal, self.importResolved),
                          (pull_signal, self.pullResolved),
                          (copy_signal, self.copyResolved),
                          (action_signal, self.actionResolved),
                          (status_signal, self.statusText)):
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
        """Page mounted (or wants a re-push) → replay the viewmodel."""
        self._call(self._refresh_fn)

    @Slot(str)
    def setView(self, view):
        """Articles/Macros/Revisions tab switch (allowlisted upstream)."""
        self._call(self._view_fn, view or "")

    @Slot(str)
    def openArticle(self, article_id):
        """Row clicked → controller validates the id against the mirror."""
        self._call(self._open_article_fn, article_id or "")

    @Slot(str)
    def openMacro(self, macro_id):
        self._call(self._open_macro_fn, macro_id or "")

    @Slot(str, str)
    def search(self, query, kind):
        """Search box → Python-side FTS over the mirror (capped upstream)."""
        self._call(self._search_fn, query or "", kind or "")

    @Slot(str)
    def requestRevisions(self, filter):  # noqa: A002 — channel-facing name
        self._call(self._revisions_fn, filter or "")

    @Slot(str, str)
    def requestDiff(self, kind, draft_id):
        """Review view → word-level diff computed in Python."""
        self._call(self._diff_fn, kind or "", draft_id or "")

    @Slot(str, str, str)
    def saveDraft(self, kind, draft_id, payload_json):
        """Draft edit committed. The controller enforces the payload-key
        allowlist and the pending/ready precondition."""
        self._call(self._save_fn, kind or "", draft_id or "",
                   payload_json or "")

    @Slot(str, str, str)
    def saveBodyEdit(self, target_kind, target_id, payload):
        """Specialist body edit (articles only). Relayed verbatim — the
        controller honors ONLY the payload's markdown ``body`` key and
        recomputes+sanitizes body_html Python-side, so page-supplied HTML
        can never land."""
        self._call(self._save_body_edit_fn, target_kind or "",
                   target_id or "", payload or "")

    @Slot(str, str)
    def markReady(self, kind, draft_id):
        """pending→ready only, validated against the CURRENT DB status."""
        self._call(self._ready_fn, kind or "", draft_id or "")

    @Slot(str, str)
    def markCopied(self, kind, draft_id):
        """pending|ready→copied; the store stamps copied_at."""
        self._call(self._copied_fn, kind or "", draft_id or "")

    @Slot(str, str, str)
    def copyField(self, target, target_id, field):
        """Copy exact — the controller re-reads the DB bytes at click time
        and the Python-side clipboard does the copy (target kind decides
        the table; draft copies require a recorded review + the native
        confirm, at any status)."""
        self._call(self._copy_fn, target or "", target_id or "", field or "")

    @Slot()
    def requestImport(self):
        """File import — the host opens its NATIVE picker; the claim is
        taken before the picker's nested event loop spins."""
        self._call(self._import_fn)

    @Slot()
    def requestImportFolder(self):
        self._call(self._import_folder_fn)

    @Slot()
    def requestPull(self):
        """Read-only GET pull; cooldown + single-winner enforced upstream."""
        self._call(self._pull_fn)

    @Slot(str, str)
    def deleteRevision(self, kind, draft_id):
        """DESTRUCTIVE — controller gate: claim before the NATIVE confirm,
        re-verify after approve, fail closed without a confirm fn."""
        self._call(self._delete_fn, kind or "", draft_id or "")

    @Slot(str)
    def purgeMirror(self, scope):
        """DESTRUCTIVE — same gate with scope-specific confirm text and
        post-approve count recompute."""
        self._call(self._purge_fn, scope or "")

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip."""
        return "pong"
