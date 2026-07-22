"""Native Google Drive folder picker — the model-independent entry point.

The in-chat picker only opens when the model calls ``request_drive_picker``,
and measured over identical runs the model skips that call a significant
fraction of the time — so "open the folder picker" could silently do nothing.
This dialog is the same picker as a plain Qt affordance: the operator opens it
from Settings, no LLM in the loop, and a pick persists through the SAME
``_set_drive_folder_impl`` the chat resolve path uses, so both paths converge
on ``enablement.drive.active_folders``.

Tree data comes from the same off-thread ``DriveListWorker`` the chat picker
uses (roots = ``list_picker_roots()``: Shared Drives + shared-with-me folders —
the only roots a service account actually has; children = ``list_folders``).
Never call DriveReader synchronously here: the HTTP would freeze the Qt event
loop (invariant 6).

Worker lifetime: workers are parented to the dialog AND tracked in
``self._workers`` until their main-thread slot fires — a mid-run QThread whose
only Python reference is dropped gets GC'd and crashes natively (the
documented QWebChannel-era lesson). The dialog itself is cached and REUSED by
its caller (see SettingsPage) rather than destroyed per open, so a close with
a listing in flight never destroys a running QThread's parent.

The empty state is a feature, not an error: a fresh service account sees ZERO
folders until one is shared to it, and that must render as instructions naming
the exact address to share to (``google_access.service_account_email``) — not
as a blank tree.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from src.services.agent_chat import DriveListWorker

_ROLE_FOLDER = Qt.ItemDataRole.UserRole          # dict row for real folders, None for placeholders
_PLACEHOLDER_TEXT = "Loading…"
_NO_CHILDREN_TEXT = "(no subfolders)"


class DriveFolderPickerDialog(QDialog):
    """Browse the folders the configured Google account can see; pick one.

    After ``exec()`` returns ``Accepted``, ``self.picked`` holds
    ``{"id", "name", "drive_id"}`` for the chosen folder. Call :meth:`reload`
    before each ``exec()`` when reusing a cached instance.
    """

    # Class attribute so tests can swap in a synchronous fake worker.
    worker_cls = DriveListWorker

    # Test seam: emitted whenever a listing lands (roots or children).
    listing_loaded = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a Google Drive folder")
        self.setModal(True)
        self.resize(560, 440)
        # This dialog is opened from SettingsPage, whose selectorless cream
        # background cascades to these buttons and (with the app QSS's white
        # text) blanks their labels. Style the dialog itself — an ancestor rule
        # loses to the page's own background by proximity (2026-07-22 fix).
        from src.ui.pages.enablement._common import style_native_dialog
        style_native_dialog(self)
        self.picked: dict | None = None
        self._workers: set = set()        # live QThreads — see module docstring
        self._pending: dict = {}          # token -> QTreeWidgetItem | None (roots)
        self._seq = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        self._header = QLabel("")
        self._header.setStyleSheet("color:#6B7268; font-size:11.5px;")
        self._header.setWordWrap(True)
        v.addWidget(self._header)

        # Banner carries the three non-tree states: connecting guidance,
        # "nothing shared yet" instructions, and listing errors.
        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._banner.setStyleSheet(
            "QLabel{background:#FBF1E5; color:#8A5A19; border:none; "
            "border-radius:7px; padding:10px 12px; font-size:12px;}")
        self._banner.hide()
        v.addWidget(self._banner)

        self._copy_btn = QPushButton("Copy service-account email")
        self._copy_btn.clicked.connect(self._copy_email)
        self._copy_btn.hide()
        v.addWidget(self._copy_btn, 0, Qt.AlignLeft)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        v.addWidget(self._tree, 1)

        row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.reload)
        row.addWidget(self._refresh_btn)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self._ok_btn = QPushButton("Use this folder")
        self._ok_btn.setDefault(True)
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_use)
        row.addWidget(self._ok_btn)
        v.addLayout(row)

        self.reload()

    # ── loading ──────────────────────────────────────────────────────

    def reload(self):
        """(Re)load the roots. Also the Refresh handler — after the operator
        shares a folder, this is the click that makes it appear."""
        self.picked = None
        self._pending.clear()             # responses for old tokens drop harmlessly
        self._tree.clear()
        self._ok_btn.setEnabled(False)
        self._banner.hide()
        self._copy_btn.hide()
        self._header.setText(self._header_text())
        if not self._access_ready():
            self._show_banner(
                "Google Drive isn't connected yet. In Settings, set the "
                "service-account key (or reconnect Google), then click Refresh.")
            return
        self._header.setText(self._header_text() + "  ·  loading folders…")
        self._spawn("", None)

    def _spawn(self, parent_id: str, item):
        """Start one off-thread listing; route its result back by token."""
        self._seq += 1
        token = f"native-{self._seq}"
        self._pending[token] = item
        worker = self.worker_cls(token, parent_id, parent=self)
        worker.finished.connect(self._on_listed, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_list_failed, Qt.ConnectionType.QueuedConnection)
        self._workers.add(worker)
        worker.start()

    def _on_listed(self, token, parent_id, folders):
        self._reap(token)
        if token not in self._pending:
            return                        # stale: a Refresh cleared this token
        item = self._pending.pop(token)
        rows = list(folders or [])
        if item is None:
            self._fill_roots(rows)
        else:
            self._fill_children(item, rows)
        self.listing_loaded.emit(parent_id or "")

    def _on_list_failed(self, token, msg):
        self._reap(token)
        if token not in self._pending:
            return
        item = self._pending.pop(token)
        if item is None:
            self._header.setText(self._header_text())
            self._show_banner(f"Couldn't list Drive folders: {msg or 'unknown error'}. "
                              "Check the connection in Settings, then click Refresh.")
        else:
            self._clear_children(item)
            self._add_note(item, f"(couldn't load: {msg or 'error'})")
        self.listing_loaded.emit("!error")

    def _reap(self, token):
        """Drop finished workers. Refs are held until HERE (main thread) so a
        mid-run QThread is never GC'd; the Qt parent keeps teardown ordered."""
        for w in [w for w in self._workers if getattr(w, "_request_id", None) == token]:
            self._workers.discard(w)

    # ── tree building ────────────────────────────────────────────────

    def _fill_roots(self, rows: list[dict]):
        self._header.setText(self._header_text())
        if not rows:
            self._show_empty_state()
            return
        for r in rows:
            self._add_folder_item(self._tree, r)

    def _fill_children(self, item, rows: list[dict]):
        self._clear_children(item)
        if not rows:
            self._add_note(item, _NO_CHILDREN_TEXT)
            return
        for r in rows:
            self._add_folder_item(item, r)

    def _add_folder_item(self, parent, row: dict):
        name = str(row.get("name") or row.get("id") or "folder")
        if row.get("shared"):
            name += "   (shared with you)"
        it = QTreeWidgetItem(parent, [name])
        it.setData(0, _ROLE_FOLDER, dict(row))
        # Expandable without knowing children yet: a placeholder child makes the
        # arrow render; the real listing replaces it on first expand.
        QTreeWidgetItem(it, [_PLACEHOLDER_TEXT])

    @staticmethod
    def _add_note(item, text: str):
        note = QTreeWidgetItem(item, [text])
        note.setFlags(Qt.ItemFlag.NoItemFlags)

    @staticmethod
    def _clear_children(item):
        item.takeChildren()

    def _on_expanded(self, item):
        kids = [item.child(i) for i in range(item.childCount())]
        if len(kids) == 1 and kids[0].text(0) == _PLACEHOLDER_TEXT:
            row = item.data(0, _ROLE_FOLDER) or {}
            if row.get("id"):
                self._spawn(str(row["id"]), item)

    # ── states ───────────────────────────────────────────────────────

    def _show_empty_state(self):
        email = self._sa_email()
        if email:
            self._show_banner(
                "This service account can't see any folders yet — that's the "
                "normal state of a fresh key, not an error.\n\n"
                "In Google Drive: right-click a folder → Share → add\n"
                f"{email}\n"
                "as Viewer, then click Refresh here.")
            self._copy_btn.show()
        else:
            self._show_banner(
                "No folders are visible to this Google account yet. Share a "
                "folder with it in Google Drive, then click Refresh.")

    def _show_banner(self, text: str):
        self._banner.setText(text)
        self._banner.show()

    def _copy_email(self):
        email = self._sa_email()
        if email:
            QApplication.clipboard().setText(email)

    def _header_text(self) -> str:
        email = self._sa_email()
        return f"Browsing as {email}" if email else "Browsing your connected Google Drive"

    # ── selection / accept ───────────────────────────────────────────

    def _selected_row(self) -> dict | None:
        items = self._tree.selectedItems()
        if not items:
            return None
        row = items[0].data(0, _ROLE_FOLDER)
        # The synthetic My Drive root ('root') is an alias, not a concrete
        # folder — a pick must be a real id so every downstream query agrees.
        if not row or not row.get("id") or row.get("id") == "root":
            return None
        return row

    def _on_selection(self):
        self._ok_btn.setEnabled(self._selected_row() is not None)

    def _on_double_clicked(self, item, _col):
        if self._selected_row() is not None:
            self._on_use()

    def _on_use(self):
        row = self._selected_row()
        if row is None:
            return
        self.picked = {"id": str(row.get("id")),
                       "name": str(row.get("name") or ""),
                       "drive_id": str(row.get("driveId") or "")}
        self.accept()

    # ── seams (patched in tests; isolated so no network in CI) ───────

    @staticmethod
    def _access_ready() -> bool:
        from src.data.google_access import google_access_ready
        return google_access_ready()

    @staticmethod
    def _sa_email() -> str:
        from src.data.google_access import service_account_email
        return service_account_email()
