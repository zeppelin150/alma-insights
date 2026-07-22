"""The model-independent Drive folder picker (dialog + roots + wiring).

Two defects drove this build, both verified live on 2026-07-21:

1. The in-chat picker only opens if the model calls ``request_drive_picker``,
   and measured across identical runs the model skips that call a meaningful
   fraction of the time — "open the picker" could silently do nothing. The fix
   is a native Settings-card button that opens the same picker with no LLM in
   the loop.

2. Even when it opened, the tree was built from ``list_drives`` +
   ``list_folders('root')`` — for a service account BOTH are empty. Folders
   shared TO an SA appear only under ``sharedWithMe = true``, which nothing
   queried. Verified live: the shared folder existed, the old roots were [].
   The fix is ``list_picker_roots()``: Shared Drives + shared-with-me folders.

The pick persists through the SAME ``_set_drive_folder_impl`` the chat resolve
path uses, so both paths converge on ``enablement.drive.active_folders``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from src.data.drive_reader import DriveReader
from src.services.agent_chat import DriveListWorker
from src.ui.dialogs.drive_folder_picker_dialog import (
    _NO_CHILDREN_TEXT, _PLACEHOLDER_TEXT, DriveFolderPickerDialog,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch):
    """Point settings_manager at a temp settings.yaml so nothing here reads or
    writes the operator's real configuration."""
    import src.data.settings_manager as sm
    path = tmp_path / "settings.yaml"
    monkeypatch.setattr(sm, "get_settings_path", lambda: path)
    return path


SA = "almainsights-test@alma-insights-test.iam.gserviceaccount.com"


# ── DriveReader.list_shared_roots ────────────────────────────────────


def _configured_reader(auth_type="service_account") -> DriveReader:
    r = DriveReader(credentials_path="x", auth_type=auth_type)
    r.is_configured = lambda: True  # type: ignore[method-assign]
    return r


def _files_service(pages) -> MagicMock:
    svc = MagicMock()
    svc.files.return_value.list.return_value.execute.side_effect = list(pages)
    return svc


class TestListSharedRoots:
    def test_queries_shared_with_me_folders_only(self):
        """The load-bearing query: sharedWithMe=true + folder mime + untrashed.
        This is the ONLY way folders shared to a service account surface."""
        svc = _files_service([{"files": [
            {"id": "F1", "name": "almainsightstest", "driveId": None}]}])
        r = _configured_reader()
        with patch.object(r, "_build_service", return_value=svc):
            out = r.list_shared_roots()
        q = svc.files.return_value.list.call_args.kwargs["q"]
        assert "sharedWithMe = true" in q
        assert "mimeType = 'application/vnd.google-apps.folder'" in q
        assert "trashed = false" in q
        assert out == [{"id": "F1", "name": "almainsightstest",
                        "drive_id": None, "shared": True}]

    def test_paginates_across_nextPageToken(self):
        svc = _files_service([
            {"files": [{"id": "A", "name": "a"}], "nextPageToken": "t2"},
            {"files": [{"id": "B", "name": "b"}]},
        ])
        r = _configured_reader()
        with patch.object(r, "_build_service", return_value=svc):
            out = r.list_shared_roots()
        assert [f["id"] for f in out] == ["A", "B"]

    def test_gate_closed_makes_no_drive_call(self):
        r = DriveReader(credentials_path="", auth_type="service_account")
        with patch.object(r, "_build_service") as build:
            assert r.list_shared_roots() == []
        build.assert_not_called()


class TestListPickerRoots:
    def test_service_account_drops_the_synthetic_my_drive(self):
        """An SA's own My Drive is structurally empty — offering it invites an
        expand-to-nothing dead end. Roots = shared drives + shared folders."""
        r = _configured_reader("service_account")
        r.list_drives = lambda: [
            {"id": "root", "name": "My Drive", "is_my_drive": True},
            {"id": "0ATeam", "name": "Team RCM", "is_my_drive": False}]
        r.list_shared_roots = lambda: [
            {"id": "F1", "name": "almainsightstest", "drive_id": None, "shared": True}]
        out = r.list_picker_roots()
        assert [f["id"] for f in out] == ["0ATeam", "F1"]

    def test_oauth_user_keeps_my_drive_and_gains_shared_roots(self):
        r = _configured_reader("oauth_user")
        r.list_drives = lambda: [
            {"id": "root", "name": "My Drive", "is_my_drive": True}]
        r.list_shared_roots = lambda: [
            {"id": "F2", "name": "From a colleague", "drive_id": None, "shared": True}]
        out = r.list_picker_roots()
        assert [f["id"] for f in out] == ["root", "F2"]

    def test_gate_closed_returns_empty(self):
        r = DriveReader(credentials_path="", auth_type="service_account")
        assert r.list_picker_roots() == []


# ── DriveListWorker: roots go through list_picker_roots ─────────────


class TestWorkerRoots:
    def _run(self, monkeypatch, parent_id):
        reader = SimpleNamespace(
            list_picker_roots=lambda: [
                {"id": "F1", "name": "almainsightstest", "drive_id": None,
                 "shared": True}],
            list_folders=lambda pid: [
                {"id": "C1", "name": f"child-of-{pid}", "drive_id": "D9"}])
        monkeypatch.setattr(
            "src.data.drive_reader.DriveReader.from_settings",
            classmethod(lambda cls: reader))
        seen = []
        w = DriveListWorker("tok", parent_id)
        w.finished.connect(lambda *a: seen.append(a))
        w.run()   # direct call — exercises the body without starting a thread
        return seen

    def test_empty_parent_uses_picker_roots_and_keeps_shared_flag(self, monkeypatch):
        """The regression: roots used list_drives(), which for an SA never
        contains the shared folder. Roots must now carry it, marked shared."""
        seen = self._run(monkeypatch, "")
        assert seen and seen[0][2] == [
            {"id": "F1", "name": "almainsightstest", "driveId": None, "shared": True}]

    def test_real_parent_still_lists_children(self, monkeypatch):
        seen = self._run(monkeypatch, "F1")
        assert seen and seen[0][2] == [
            {"id": "C1", "name": "child-of-F1", "driveId": "D9", "shared": False}]


# ── the native dialog ────────────────────────────────────────────────


class FakeWorker(QObject):
    """Synchronous stand-in for DriveListWorker: start() emits immediately.

    ``responses`` maps parent_id -> rows (or an Exception to emit ``failed``).
    The dialog connects with QueuedConnection, so tests drain the event loop
    with processEvents() after each trigger.
    """

    finished = Signal(str, str, list)
    failed = Signal(str, str)
    responses: dict = {}

    def __init__(self, request_id, parent_id, parent=None):
        super().__init__(parent)
        self._request_id = request_id
        self._parent_id = parent_id

    def start(self):
        r = self.responses.get(self._parent_id, [])
        if isinstance(r, Exception):
            self.failed.emit(self._request_id, str(r))
        else:
            self.finished.emit(self._request_id, self._parent_id, list(r))


@pytest.fixture()
def picker(qapp, monkeypatch):
    """A dialog wired to the fake worker, access-ready, SA email known."""
    monkeypatch.setattr(DriveFolderPickerDialog, "worker_cls", FakeWorker)
    monkeypatch.setattr(DriveFolderPickerDialog, "_access_ready",
                        staticmethod(lambda: True))
    monkeypatch.setattr(DriveFolderPickerDialog, "_sa_email",
                        staticmethod(lambda: SA))
    FakeWorker.responses = {}

    def make():
        dlg = DriveFolderPickerDialog()
        qapp.processEvents()          # drain the queued roots emission
        return dlg

    yield make
    FakeWorker.responses = {}


ROOTS = [{"id": "F1", "name": "almainsightstest", "driveId": None, "shared": True},
         {"id": "0ATeam", "name": "Team RCM", "driveId": "0ATeam", "shared": False}]


class TestDialogTree:
    def test_roots_render_with_the_shared_marker(self, picker):
        FakeWorker.responses = {"": ROOTS}
        dlg = picker()
        texts = [dlg._tree.topLevelItem(i).text(0)
                 for i in range(dlg._tree.topLevelItemCount())]
        assert texts[0].startswith("almainsightstest")
        assert "(shared with you)" in texts[0]
        assert texts[1] == "Team RCM"
        assert dlg._banner.isHidden()

    def test_expand_lazily_loads_children(self, qapp, picker):
        FakeWorker.responses = {
            "": ROOTS,
            "F1": [{"id": "C1", "name": "corpus", "driveId": None, "shared": False}]}
        dlg = picker()
        top = dlg._tree.topLevelItem(0)
        assert top.child(0).text(0) == _PLACEHOLDER_TEXT
        dlg._tree.expandItem(top)
        qapp.processEvents()
        assert top.childCount() == 1
        assert top.child(0).text(0) == "corpus"

    def test_expand_of_a_leaf_notes_no_subfolders(self, qapp, picker):
        FakeWorker.responses = {"": ROOTS, "F1": []}
        dlg = picker()
        top = dlg._tree.topLevelItem(0)
        dlg._tree.expandItem(top)
        qapp.processEvents()
        assert top.child(0).text(0) == _NO_CHILDREN_TEXT

    def test_roots_error_lands_in_the_banner_not_a_blank_tree(self, picker):
        FakeWorker.responses = {"": RuntimeError("HttpError 403")}
        dlg = picker()
        assert not dlg._banner.isHidden()
        assert "HttpError 403" in dlg._banner.text()


class TestDialogEmptyState:
    """THE state this whole feature exists for: auth fine, nothing shared."""

    def test_zero_roots_names_the_address_to_share_to(self, picker):
        FakeWorker.responses = {"": []}
        dlg = picker()
        assert not dlg._banner.isHidden()
        assert SA in dlg._banner.text()
        assert "Share" in dlg._banner.text()
        assert not dlg._copy_btn.isHidden()

    def test_not_connected_asks_for_setup_and_spawns_no_worker(self, qapp, monkeypatch):
        monkeypatch.setattr(DriveFolderPickerDialog, "worker_cls", FakeWorker)
        monkeypatch.setattr(DriveFolderPickerDialog, "_access_ready",
                            staticmethod(lambda: False))
        monkeypatch.setattr(DriveFolderPickerDialog, "_sa_email",
                            staticmethod(lambda: ""))
        dlg = DriveFolderPickerDialog()
        qapp.processEvents()
        assert not dlg._banner.isHidden()
        assert "isn't connected" in dlg._banner.text()
        assert not dlg._workers and not dlg._pending

    def test_refresh_after_sharing_recovers(self, qapp, picker):
        """The operator's real sequence: open (empty) -> share in Drive ->
        Refresh -> the folder appears."""
        FakeWorker.responses = {"": []}
        dlg = picker()
        assert not dlg._banner.isHidden()
        FakeWorker.responses = {"": ROOTS}
        dlg.reload()
        qapp.processEvents()
        assert dlg._banner.isHidden()
        assert dlg._tree.topLevelItemCount() == 2


class TestDialogPick:
    def test_selecting_a_folder_enables_use_and_accept_fills_picked(self, qapp, picker):
        FakeWorker.responses = {"": ROOTS}
        dlg = picker()
        assert not dlg._ok_btn.isEnabled()
        dlg._tree.setCurrentItem(dlg._tree.topLevelItem(0))
        assert dlg._ok_btn.isEnabled()
        dlg._on_use()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.picked == {"id": "F1", "name": "almainsightstest", "drive_id": ""}

    def test_the_synthetic_my_drive_root_is_not_pickable(self, qapp, picker):
        """'root' is an API alias, not a concrete folder id — persisting it
        would make every downstream query mean something different."""
        FakeWorker.responses = {"": [
            {"id": "root", "name": "My Drive", "driveId": None, "shared": False}]}
        dlg = picker()
        dlg._tree.setCurrentItem(dlg._tree.topLevelItem(0))
        assert not dlg._ok_btn.isEnabled()

    def test_stale_response_after_refresh_is_dropped(self, qapp, picker):
        """reload() clears pending tokens, so a listing that lands late must
        not repopulate the fresh tree with pre-refresh rows."""
        FakeWorker.responses = {"": ROOTS}
        dlg = picker()
        stale = FakeWorker("native-1", "")
        stale.finished.connect(dlg._on_listed)
        stale.start()                 # token no longer pending -> ignored
        qapp.processEvents()
        assert dlg._tree.topLevelItemCount() == 2   # not doubled


# ── Settings card entry point ────────────────────────────────────────


class TestSettingsCardWiring:
    def _page(self, qapp, temp_settings):
        from src.ui.pages.enablement.settings import SettingsPage
        return SettingsPage()

    def test_browse_emits_the_pick_and_reuses_one_dialog(self, qapp, temp_settings,
                                                         monkeypatch):
        page = self._page(qapp, temp_settings)
        made = []

        class FakeDialog:
            def __init__(self, parent=None):
                made.append(self)
                self.picked = None
                self.reloads = 0

            def reload(self):
                self.reloads += 1

            def exec(self):
                self.picked = {"id": "F1", "name": "almainsightstest",
                               "drive_id": ""}
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "src.ui.dialogs.drive_folder_picker_dialog.DriveFolderPickerDialog",
            FakeDialog)
        got = []
        page.drive_folder_picked.connect(lambda *a: got.append(a))
        page._on_browse_drive()
        assert got == [("F1", "almainsightstest", "")]
        page._on_browse_drive()
        assert len(made) == 1, "the dialog must be cached and reused"
        assert made[0].reloads == 1

    def test_cancel_emits_nothing(self, qapp, temp_settings, monkeypatch):
        page = self._page(qapp, temp_settings)

        class Cancelled:
            def __init__(self, parent=None):
                self.picked = None

            def reload(self):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr(
            "src.ui.dialogs.drive_folder_picker_dialog.DriveFolderPickerDialog",
            Cancelled)
        got = []
        page.drive_folder_picked.connect(lambda *a: got.append(a))
        page._on_browse_drive()
        assert got == []

    def test_active_label_renders_the_current_pick(self, qapp, temp_settings):
        page = self._page(qapp, temp_settings)
        assert "none yet" in page._drive_active_lbl.text()
        page.set_active_drive_folders([{"id": "F1", "name": "almainsightstest"}])
        assert "almainsightstest" in page._drive_active_lbl.text()
        page.set_active_drive_folders([])
        assert "none yet" in page._drive_active_lbl.text()


# ── host persistence: the button path converges with the chat path ──


class TestPickPersistence:
    def test_impl_writes_active_folders_with_a_none_conn(self, temp_settings):
        """_set_drive_folder_impl is a settings-only write (its conn arg is
        unused) — the native path passes None and must still persist exactly
        what the chat resolve path persists."""
        from src.data.chat_tools.enablement_tools import _set_drive_folder_impl
        from src.data.settings_manager import get_section
        res = _set_drive_folder_impl(None, "F1", "almainsightstest", None)
        assert res["ok"] is True
        folders = (get_section("enablement", {})["drive"]["active_folders"])
        assert folders == [{"id": "F1", "name": "almainsightstest",
                            "drive_id": None}]

    def test_page_handler_persists_then_refreshes_the_label(self, temp_settings):
        """EnablementPage._on_drive_folder_picked, exercised on a stub host so
        the test doesn't have to construct the full page: persist first, then
        status, then re-feed the card from settings."""
        from src.data.settings_manager import get_section
        from src.ui.pages.enablement.page import EnablementPage

        class Host:
            def __init__(self):
                self.statuses = []
                self.fed = []
                self.settings = SimpleNamespace(
                    set_active_drive_folders=self.fed.append)

            def _set_status(self, s):
                self.statuses.append(s)

            _on_drive_folder_picked = EnablementPage._on_drive_folder_picked
            _refresh_active_drive_folder = EnablementPage._refresh_active_drive_folder

        host = Host()
        host._on_drive_folder_picked("F1", "almainsightstest", "")
        assert (get_section("enablement", {})["drive"]["active_folders"][0]["id"]
                == "F1")
        assert host.statuses and "almainsightstest" in host.statuses[0]
        assert host.fed and host.fed[0][0]["id"] == "F1"

class TestLegacyStringEntries:
    """Field incident 2026-07-22: the operator's real settings held
    ``active_folders: ['11eRk97…']`` — a plain STRING hand-wired during the
    2026-07-20 eval — and the first-ever live picker resolve crashed on it:
    ``(f or {}).get("id")`` raises AttributeError on a str, resolve_drive_folder
    returned {ok:false} that React swallowed silently, the row stayed
    resolved=0, and routing kept reporting 0 active folders (its reader SKIPS
    non-dict entries instead of counting them). Every path that touches
    active_folders must tolerate both shapes."""

    def _seed(self, folders):
        from src.data.settings_manager import set_section
        set_section("enablement", {"drive": {"active_folders": folders}})

    def test_impl_survives_a_legacy_string_entry(self, temp_settings):
        from src.data.chat_tools.enablement_tools import _set_drive_folder_impl
        from src.data.settings_manager import get_section
        self._seed(["LEGACY_ID", {"id": "D2", "name": "n2", "drive_id": None}])
        res = _set_drive_folder_impl(None, "F1", "corpus", None)
        assert res["ok"] is True
        folders = get_section("enablement", {})["drive"]["active_folders"]
        assert folders == [
            {"id": "LEGACY_ID", "name": None, "drive_id": None},  # normalized
            {"id": "D2", "name": "n2", "drive_id": None},
            {"id": "F1", "name": "corpus", "drive_id": None},
        ]

    def test_impl_replaces_the_legacy_string_on_repick(self, temp_settings):
        """Picking the folder the legacy string already names must REPLACE the
        string (dedupe by id), not append a duplicate."""
        from src.data.chat_tools.enablement_tools import _set_drive_folder_impl
        from src.data.settings_manager import get_section
        self._seed(["LEGACY_ID"])
        res = _set_drive_folder_impl(None, "LEGACY_ID", "now named", None)
        assert res["ok"] is True and res["count"] == 1
        folders = get_section("enablement", {})["drive"]["active_folders"]
        assert folders == [{"id": "LEGACY_ID", "name": "now named",
                            "drive_id": None}]

    def test_routing_reports_legacy_string_entries(self, temp_settings):
        """get_enablement_routing told the operator '0 active folders' while a
        legacy string entry was configured — it must COUNT both shapes."""
        from src.data.chat_tools.enablement_tools import (
            _get_enablement_routing_impl)
        self._seed(["LEGACY_ID", {"id": "D2"}])
        out = _get_enablement_routing_impl(None)
        assert out["drive"]["active_folder_ids"] == ["LEGACY_ID", "D2"]
        assert out["drive"]["count"] == 2

    def test_settings_card_label_tolerates_legacy_strings(self, qapp,
                                                          temp_settings):
        from src.ui.pages.enablement.settings import SettingsPage
        page = SettingsPage()
        page.set_active_drive_folders(["LEGACY_ID", {"id": "D2", "name": "n2"}])
        text = page._drive_active_lbl.text()
        assert "LEGACY_ID" in text and "n2" in text


class TestPickerRequestSupersession:
    """Field incident 2026-07-22 (secondary): the two crashed resolves left
    drive_folder_picker rows consumed=1,resolved=0 FOREVER — and
    has_pending_action keys off resolved=0 with no expiry, so the by-id
    set_drive_folder fallback would refuse with needs_picker for the rest of
    the session's life (the research_plan gate-brick class). Minting a NEW
    picker/connect request must supersede (resolve) older unresolved requests
    of the same type+session — only the newest instance is live, matching the
    'reopen the picker' reality. confirm_write is deliberately untouched."""

    def test_new_picker_request_supersedes_stale_unresolved_same_type(
            self, empty_db):
        from src.data import chat_action_requests as car
        conn = empty_db.conn
        stale = car.create_action_request(conn, "s1", "drive_folder_picker")
        car.claim_pending_actions(conn, "s1")          # picker opened, never picked
        fresh = car.create_action_request(conn, "s1", "drive_folder_picker")
        assert car.get_action_request(conn, stale)["resolved"] == 1
        assert car.get_action_request(conn, fresh)["resolved"] == 0
        assert car.has_pending_action(conn, "s1") is True   # the fresh one
        assert car.mark_resolved(conn, fresh) is True
        assert car.has_pending_action(conn, "s1") is False  # gate reopens

    def test_supersession_is_scoped_to_type_and_session(self, empty_db):
        from src.data import chat_action_requests as car
        conn = empty_db.conn
        other_type = car.create_action_request(conn, "s1", "asana_board_picker")
        other_session = car.create_action_request(conn, "s2",
                                                  "drive_folder_picker")
        car.create_action_request(conn, "s1", "drive_folder_picker")
        assert car.get_action_request(conn, other_type)["resolved"] == 0
        assert car.get_action_request(conn, other_session)["resolved"] == 0

    def test_confirm_write_rows_are_never_superseded(self, empty_db):
        """A pending Confirm card is a HUMAN GATE on a live write — a later
        picker (or even a second confirm) must never silently resolve it."""
        from src.data import chat_action_requests as car
        conn = empty_db.conn
        confirm = car.create_confirm_write(
            conn, "s1", "create_guru_folder", "make a folder",
            {"collection_id": "c1", "title": "t"})
        car.create_action_request(conn, "s1", "drive_folder_picker")
        car.create_confirm_write(conn, "s1", "create_asana_task", "task",
                                 {"project_gid": "p", "name": "n"})
        assert car.get_action_request(conn, confirm)["resolved"] == 0


class TestFieldReplayResolve:
    """End-to-end replay of the 2026-07-22 field failure through the REAL
    resolve_drive_folder: the operator's real settings (legacy string entry
    '11eRk97…' from the eval wiring) + a claimed picker request + the exact
    click ('Use this' on almainsightstest). Pre-fix this crashed at the persist
    step and left the row unresolved; it must now persist, resolve, and heal
    the legacy entry to dict shape."""

    FIELD_LEGACY = "11eRk97prQEihKGfh9fvQv2cFZPob3OIw"
    PICKED_ID = "1hktGvTFlmdzch5zAnIIR9TKm9ECAuRKa"

    def test_the_exact_field_click_now_resolves(self, qapp, temp_settings,
                                                empty_db, monkeypatch):
        from PySide6.QtCore import QObject
        from src.data import chat_action_requests as car
        from src.data.connection_factory import get_connection
        from src.data.settings_manager import get_section, set_section
        from src.services.agent_chat import AgentChatController

        set_section("enablement",
                    {"drive": {"active_folders": [self.FIELD_LEGACY]}})
        conn = empty_db.conn
        rid = car.create_action_request(conn, "s1", "drive_folder_picker")
        car.claim_pending_actions(conn, "s1")   # the picker opened

        # A stripped controller: QObject-initialized (signals live), no engine
        # (notify/enqueue guard on None), session + db wired to the fixture.
        ctrl = AgentChatController.__new__(AgentChatController)
        QObject.__init__(ctrl)
        ctrl._engine = None
        ctrl._session_id = "s1"
        ctrl._pending_triggers = []
        db_path = str(empty_db.db_path)
        monkeypatch.setattr(
            ctrl, "_open_conn",
            lambda readonly=False: get_connection(db_path, readonly=readonly))

        res = ctrl.resolve_drive_folder(rid, self.PICKED_ID,
                                        "almainsightstest", "")
        assert res["ok"] is True

        assert car.get_action_request(conn, rid)["resolved"] == 1
        folders = get_section("enablement", {})["drive"]["active_folders"]
        assert {"id": self.FIELD_LEGACY, "name": None, "drive_id": None} in folders
        assert any(f["id"] == self.PICKED_ID and f["name"] == "almainsightstest"
                   for f in folders)
        assert car.has_pending_action(conn, "s1") is False   # gate reopened


class TestPickPersistenceFailure:
    def test_a_failed_persist_reports_and_does_not_pretend(self, temp_settings,
                                                           monkeypatch):
        from src.ui.pages.enablement.page import EnablementPage

        class Host:
            def __init__(self):
                self.statuses = []

            def _set_status(self, s):
                self.statuses.append(s)

            def _refresh_active_drive_folder(self):
                raise AssertionError("must not refresh after a failed persist")

            _on_drive_folder_picked = EnablementPage._on_drive_folder_picked

        monkeypatch.setattr(
            "src.data.chat_tools.enablement_tools._set_drive_folder_impl",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
        host = Host()
        host._on_drive_folder_picked("F1", "x", "")
        assert host.statuses and "disk full" in host.statuses[0]
