"""Native Guru collections picker — the search-scope editor (pilot G1).

Production Guru holds ~30 collections and every search used to sweep all of
them — the operator had no way to say "only these". This dialog is the
plain-Qt scope editor: a searchable checkbox list of the account's
collections. OK persists the checked rows to
``enablement.guru.search_collections`` as ``[{"id", "name"}, ...]`` via
settings_manager — the exact shape the G2 read paths consume. An EMPTY
selection means "search ALL collections": the dialog and the Settings row
both say so, because a scope that narrows silently is the twenty-minute
mystery the enablement roadmap warned about.

Collections load OFF the UI thread (``GuruCollectionListWorker``, the
DriveListWorker pattern): GuruClient is stdlib urllib and its HTTP would
freeze the Qt event loop if called from the dialog. Never list synchronously
here.

Worker lifetime: workers are parented to the dialog AND tracked in
``self._workers`` until their main-thread slot fires — a mid-run QThread
whose only Python reference is dropped gets GC'd and crashes natively (the
documented QWebChannel-era lesson). The dialog itself is cached and REUSED
by its caller (see SettingsPage) rather than destroyed per open, so a close
with a listing in flight never destroys a running QThread's parent.

The not-connected state is instructions, not an empty list: without saved
Guru credentials the dialog names the fix (Settings → Providers → Guru)
instead of rendering zero rows as if the account had no collections. And a
stored selection the listing no longer returns stays visible and CHECKED
("not found — kept in scope"), so a Guru-side deletion or a flaky listing
can never silently narrow the scope either.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from src.ui.theme import ALMA_BG_ELEVATED, ALMA_BORDER, ALMA_TEXT_DARK

_ROLE_COLLECTION = Qt.ItemDataRole.UserRole   # {"id", "name"} dict per row

_NOT_CONNECTED_TEXT = (
    "Guru isn't connected yet. In Settings → Providers, save your Guru "
    "email and API token, then click Refresh here.")


class GuruCollectionListWorker(QThread):
    """Off-thread Guru collection lister (the DriveListWorker pattern).

    Loads credentials via ``GuruClient.load_credentials()`` and emits
    ``[{"id", "name"}, ...]`` rows on ``finished`` (queued to the main
    thread). Only ids and names ever cross the thread boundary.
    """

    finished = Signal(str, list)   # (request_id, collections)
    failed = Signal(str, str)      # (request_id, short non-PHI message)

    def __init__(self, request_id: str, parent=None):
        super().__init__(parent)
        self._request_id = request_id or ""

    def run(self):
        try:
            from src.data.guru_client import GuruClient
            email, token = GuruClient.load_credentials()
            if not (email and token):
                self.failed.emit(self._request_id, "guru_not_connected")
                return
            rows = GuruClient(email, token).list_collections() or []
            cols = [{"id": str(r.get("id")),
                     "name": str(r.get("name") or r.get("id"))}
                    for r in rows if r.get("id")]
            self.finished.emit(self._request_id, cols)
        except Exception as exc:  # noqa: BLE001 — surface a short message
            self.failed.emit(self._request_id, str(exc)[:160])


class GuruCollectionPickerDialog(QDialog):
    """Check the Guru collections searches should cover; OK persists.

    After ``exec()`` returns ``Accepted``, ``self.selected`` holds the
    persisted ``[{"id", "name"}, ...]`` list (``[]`` = all collections).
    Call :meth:`reload` before each ``exec()`` when reusing a cached
    instance.
    """

    # Class attribute so tests can swap in a synchronous fake worker.
    worker_cls = GuruCollectionListWorker

    # Test seam: emitted whenever a listing lands (row count incl. kept rows).
    listing_loaded = Signal(int)

    _HEADER = "Renn's Guru searches cover the checked collections."

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Guru collections to search")
        self.setModal(True)
        self.resize(520, 480)
        # Opened from SettingsPage, whose selectorless cream background
        # cascades to these buttons and (with the app QSS's white text)
        # blanks their labels — style the dialog itself (2026-07-22 fix).
        from src.ui.pages.enablement._common import style_native_dialog
        style_native_dialog(self)
        self.selected: list[dict] | None = None
        self._workers: set = set()      # live QThreads — see module docstring
        self._pending: set = set()      # outstanding request tokens
        self._seq = 0
        self._stored: list[dict] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        self._header = QLabel(self._HEADER)
        self._header.setStyleSheet("color:#6B7268; font-size:11.5px;")
        self._header.setWordWrap(True)
        v.addWidget(self._header)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search collections…")
        self._search.setStyleSheet(
            f"QLineEdit{{background:{ALMA_BG_ELEVATED}; border:1px solid "
            f"{ALMA_BORDER}; border-radius:7px; padding:4px 12px; "
            f"font-size:12.5px; color:{ALMA_TEXT_DARK};}}")
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        # Banner carries the non-list states: not connected, listing errors,
        # and a failed settings write. Plain text — the message interpolates
        # strings the app does not author (exception text).
        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setTextFormat(Qt.PlainText)
        self._banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._banner.setStyleSheet(
            "QLabel{background:#FBF1E5; color:#8A5A19; border:none; "
            "border-radius:7px; padding:10px 12px; font-size:12px;}")
        self._banner.hide()
        v.addWidget(self._banner)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{ALMA_BG_ELEVATED}; border:1px solid "
            f"{ALMA_BORDER}; border-radius:8px; font-size:13px; "
            f"color:{ALMA_TEXT_DARK};}}")
        self._list.itemChanged.connect(self._update_status)
        v.addWidget(self._list, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#6B7268; font-size:11.5px;")
        v.addWidget(self._status)

        # The empty-scope contract, stated where the choice is made.
        hint = QLabel("Empty = all collections — the scope is never "
                      "silently narrowed.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7268; font-size:11.5px; font-weight:600;")
        v.addWidget(hint)

        row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.reload)
        row.addWidget(self._refresh_btn)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        # Never disabled: an empty selection is a valid scope (= ALL
        # collections) — the hint above says exactly that.
        self._ok_btn = QPushButton("Use this scope")
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self._on_ok)
        row.addWidget(self._ok_btn)
        v.addLayout(row)

        self.reload()

    # ── loading ──────────────────────────────────────────────────────

    def reload(self):
        """(Re)load the collection list. Also the Refresh handler — after
        connecting Guru in Settings, this is the click that fills the list."""
        self.selected = None
        self._pending.clear()         # responses for old tokens drop harmlessly
        self._banner.hide()
        self._search.clear()
        self._stored = self._stored_selection()
        if not self._connected():
            # Stored rows stay rendered + checked so OK round-trips the
            # existing scope unchanged instead of silently wiping it.
            self._fill([], fetched=False)
            self._show_banner(_NOT_CONNECTED_TEXT)
            return
        self._header.setText(self._HEADER + "  ·  loading collections…")
        self._list.clear()
        self._update_status()
        self._spawn()

    def _spawn(self):
        """Start one off-thread listing; route its result back by token."""
        self._seq += 1
        token = f"guru-col-{self._seq}"
        self._pending.add(token)
        worker = self.worker_cls(token, parent=self)
        worker.finished.connect(self._on_listed, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_list_failed, Qt.ConnectionType.QueuedConnection)
        self._workers.add(worker)
        worker.start()

    def _on_listed(self, token, collections):
        self._reap(token)
        if token not in self._pending:
            return                    # stale: a Refresh cleared this token
        self._pending.discard(token)
        self._header.setText(self._HEADER)
        self._fill(list(collections or []), fetched=True)
        self.listing_loaded.emit(self._list.count())

    def _on_list_failed(self, token, msg):
        self._reap(token)
        if token not in self._pending:
            return
        self._pending.discard(token)
        self._header.setText(self._HEADER)
        self._fill([], fetched=False)   # stored rows stay visible + checked
        if msg == "guru_not_connected":
            self._show_banner(_NOT_CONNECTED_TEXT)
        else:
            self._show_banner(
                f"Couldn't list Guru collections: {msg or 'unknown error'}. "
                "Check the connection in Settings, then click Refresh.")
        self.listing_loaded.emit(self._list.count())

    def _reap(self, token):
        """Drop finished workers. Refs are held until HERE (main thread) so a
        mid-run QThread is never GC'd; the Qt parent keeps teardown ordered."""
        for w in [w for w in self._workers
                  if getattr(w, "_request_id", None) == token]:
            self._workers.discard(w)

    # ── list building ────────────────────────────────────────────────

    def _fill(self, fetched_rows: list[dict], *, fetched: bool):
        """Rebuild the checkbox list: fetched rows in API order, then any
        stored-selection rows the listing did not return — still checked,
        because a missing listing must never silently narrow the scope. The
        ``fetched`` flag gates the "(not found)" marker: without a successful
        fetch nothing can honestly be called missing."""
        stored_ids = {s["id"] for s in self._stored}
        self._list.blockSignals(True)
        self._list.clear()
        seen: set = set()
        for r in fetched_rows:
            cid = str(r.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            self._add_row(cid, str(r.get("name") or cid),
                          checked=cid in stored_ids)
        for s in self._stored:
            if s["id"] in seen:
                continue
            label = s["name"] + ("   (not found — kept in scope)"
                                 if fetched else "")
            self._add_row(s["id"], s["name"], checked=True, label=label)
        self._list.blockSignals(False)
        self._apply_filter(self._search.text())
        self._update_status()

    def _add_row(self, cid: str, name: str, checked: bool, label: str = ""):
        it = QListWidgetItem(label or name)
        it.setData(_ROLE_COLLECTION, {"id": cid, "name": name})
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked if checked
                         else Qt.CheckState.Unchecked)
        self._list.addItem(it)

    def _apply_filter(self, text: str):
        """Case-insensitive substring filter. Hiding is display-only — hidden
        rows keep their check state and still count toward the scope."""
        needle = (text or "").strip().lower()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(needle) and needle not in it.text().lower())

    def _update_status(self, *_):
        total = self._list.count()
        if not total:
            self._status.setText("")
            return
        checked = sum(
            1 for i in range(total)
            if self._list.item(i).checkState() == Qt.CheckState.Checked)
        if not checked:
            self._status.setText(
                f"{total} collections — none checked, so searches cover "
                "ALL collections.")
        else:
            self._status.setText(f"{total} collections — {checked} in scope.")

    def _show_banner(self, text: str):
        self._banner.setText(text)
        self._banner.show()

    # ── accept / persist ─────────────────────────────────────────────

    def _on_ok(self):
        """Persist the checked rows (in list order) and accept. Empty is a
        valid scope — it means ALL collections — so OK never blocks on it."""
        rows = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            row = it.data(_ROLE_COLLECTION) or {}
            if row.get("id"):
                rows.append({"id": str(row["id"]),
                             "name": str(row.get("name") or row["id"])})
        if not self._persist(rows):
            self._show_banner(
                "Couldn't write settings — the scope was NOT saved. Check "
                "that the data folder is writable, then try again.")
            return
        self.selected = rows
        self.accept()

    # ── seams (patched in tests; isolated so no network/keyring in CI) ──

    @staticmethod
    def _connected() -> bool:
        from src.data.guru_client import GuruClient
        try:
            email, token = GuruClient.load_credentials()
        except Exception:  # noqa: BLE001
            return False
        return bool(email and token)

    @staticmethod
    def _stored_selection() -> list[dict]:
        """Current scope from ``enablement.guru.search_collections``,
        normalized to ``[{"id", "name"}, ...]``; junk entries drop."""
        from src.data.settings_manager import get_section
        try:
            en = get_section("enablement", {}) or {}
        except Exception:  # noqa: BLE001
            en = {}
        raw = (en.get("guru") or {}).get("search_collections") or []
        out = []
        for r in raw:
            if isinstance(r, dict) and r.get("id"):
                out.append({"id": str(r["id"]),
                            "name": str(r.get("name") or r["id"])})
        return out

    @staticmethod
    def _persist(selection: list[dict]) -> bool:
        """Write the scope. Whole-section set_section (temp-file rename) with
        sibling ``guru`` keys preserved — the publish target lives next door."""
        from src.data.settings_manager import get_section, set_section
        en = dict(get_section("enablement", {}) or {})
        guru = dict(en.get("guru") or {})
        guru["search_collections"] = [dict(s) for s in selection]
        en["guru"] = guru
        return bool(set_section("enablement", en))
