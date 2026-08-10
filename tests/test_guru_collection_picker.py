"""The native Guru collections picker (pilot G1) — searchable scope editor.

The dialog lists the account's collections as a searchable checkbox list,
pre-checks the persisted scope, and on OK persists the checked rows to
``enablement.guru.search_collections`` as ``[{"id", "name"}, ...]`` via
settings_manager — the exact shape the G2 read paths consume. Empty
selection = ALL collections (stated in the dialog), and a not-connected
Guru renders instructions, not an empty list.

Headless: no network, no keyring. GuruClient is monkeypatched wholesale
(``src.data.guru_client.GuruClient``, resolved at call time inside the
worker), and the REAL worker's ``start`` is rebound to ``run`` so its body
executes synchronously on the main thread; its queued signals drain with
``processEvents()``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel  # noqa: E402

import src.data.guru_client as guru_client_mod  # noqa: E402
from src.ui.dialogs.guru_collection_picker_dialog import (  # noqa: E402
    _ROLE_COLLECTION, GuruCollectionListWorker, GuruCollectionPickerDialog,
)

pytestmark = pytest.mark.ui


# ── fixtures / helpers ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def fake_settings(monkeypatch):
    """Dict-backed settings_manager so no test touches settings.yaml."""
    state: dict = {}

    def get_section(name, default=None):
        return state.get(name, default if default is not None else {})

    def set_section(name, value):
        state[name] = dict(value)
        return True

    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", get_section)
    monkeypatch.setattr(sm, "set_section", set_section)
    return state


COLS = [
    {"id": "c1", "name": "Provider Onboarding", "description": "",
     "slug": "po", "read_only": False},
    {"id": "c2", "name": "Billing", "description": "", "slug": "b",
     "read_only": False},
    {"id": "c3", "name": "Enablement Playbooks", "description": "",
     "slug": "ep", "read_only": True},
]


class FakeGuruClient:
    """Stands in for src.data.guru_client.GuruClient — records construction,
    serves a canned list_collections, never touches network or keyring."""

    credentials = ("chris@cambric.ai", "tok-123")
    collections: list = []
    boom: Exception | None = None
    constructed: list = []

    def __init__(self, email, token):
        type(self).constructed.append((email, token))

    @classmethod
    def load_credentials(cls):
        return cls.credentials

    def list_collections(self):
        if type(self).boom is not None:
            raise type(self).boom
        return list(type(self).collections)


@pytest.fixture()
def fake_guru(monkeypatch):
    FakeGuruClient.credentials = ("chris@cambric.ai", "tok-123")
    FakeGuruClient.collections = list(COLS)
    FakeGuruClient.boom = None
    FakeGuruClient.constructed = []
    monkeypatch.setattr(guru_client_mod, "GuruClient", FakeGuruClient)
    # Run the real worker body synchronously: start() → run() on this thread;
    # the queued signals then drain with processEvents().
    monkeypatch.setattr(GuruCollectionListWorker, "start",
                        GuruCollectionListWorker.run)
    return FakeGuruClient


def make(qapp) -> GuruCollectionPickerDialog:
    dlg = GuruCollectionPickerDialog()
    qapp.processEvents()          # drain the queued listing emission
    return dlg


def row_texts(dlg) -> list:
    return [dlg._list.item(i).text() for i in range(dlg._list.count())]


def checked_ids(dlg) -> list:
    out = []
    for i in range(dlg._list.count()):
        it = dlg._list.item(i)
        if it.checkState() == Qt.CheckState.Checked:
            out.append((it.data(_ROLE_COLLECTION) or {}).get("id"))
    return out


def set_checked(dlg, cid: str, checked: bool):
    for i in range(dlg._list.count()):
        it = dlg._list.item(i)
        if (it.data(_ROLE_COLLECTION) or {}).get("id") == cid:
            it.setCheckState(Qt.CheckState.Checked if checked
                             else Qt.CheckState.Unchecked)
            return
    raise AssertionError(f"no row with id {cid!r}")


# ── listing ───────────────────────────────────────────────────────────

class TestListing:
    def test_dialog_lists_collections_off_thread(self, qapp, fake_settings,
                                                 fake_guru):
        dlg = make(qapp)
        assert row_texts(dlg) == ["Provider Onboarding", "Billing",
                                  "Enablement Playbooks"]
        assert dlg._banner.isHidden()
        # built exactly one client, with the loaded credentials
        assert fake_guru.constructed == [("chris@cambric.ai", "tok-123")]

    def test_no_stored_scope_starts_all_unchecked_and_says_all(
            self, qapp, fake_settings, fake_guru):
        dlg = make(qapp)
        assert checked_ids(dlg) == []
        assert "ALL collections" in dlg._status.text()

    def test_listing_error_lands_in_the_banner(self, qapp, fake_settings,
                                               fake_guru):
        fake_guru.boom = RuntimeError("HTTP 500")
        dlg = make(qapp)
        assert not dlg._banner.isHidden()
        assert "HTTP 500" in dlg._banner.text()


# ── search filtering ──────────────────────────────────────────────────

class TestSearchFilter:
    def test_search_box_filters_rows(self, qapp, fake_settings, fake_guru):
        dlg = make(qapp)
        dlg._search.setText("billing")
        assert [dlg._list.item(i).isHidden() for i in range(3)] == \
            [True, False, True]
        dlg._search.setText("")
        assert [dlg._list.item(i).isHidden() for i in range(3)] == \
            [False, False, False]

    def test_hidden_rows_keep_their_check_and_still_persist(
            self, qapp, fake_settings, fake_guru):
        """Filtering is display-only: a checked row hidden by the filter is
        still part of the scope OK writes."""
        dlg = make(qapp)
        set_checked(dlg, "c1", True)
        dlg._search.setText("billing")           # hides c1
        assert "c1" in checked_ids(dlg)
        dlg._on_ok()
        assert fake_settings["enablement"]["guru"]["search_collections"] == \
            [{"id": "c1", "name": "Provider Onboarding"}]


# ── pre-selection ─────────────────────────────────────────────────────

class TestPreselection:
    def test_stored_scope_rows_are_prechecked(self, qapp, fake_settings,
                                              fake_guru):
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": "c2", "name": "Billing"}]}}
        dlg = make(qapp)
        assert checked_ids(dlg) == ["c2"]

    def test_stored_id_missing_from_listing_stays_visible_and_checked(
            self, qapp, fake_settings, fake_guru):
        """A Guru-side deletion (or flaky listing) must never silently narrow
        the scope: the stored row renders, marked, still checked."""
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": "zz", "name": "Archived Plays"}]}}
        dlg = make(qapp)
        assert "zz" in checked_ids(dlg)
        kept = [t for t in row_texts(dlg) if "Archived Plays" in t]
        assert kept and "not found" in kept[0]


# ── persistence ───────────────────────────────────────────────────────

class TestPersist:
    def test_ok_persists_the_documented_shape(self, qapp, fake_settings,
                                              fake_guru):
        dlg = make(qapp)
        set_checked(dlg, "c1", True)
        set_checked(dlg, "c3", True)
        dlg._on_ok()
        assert fake_settings["enablement"]["guru"]["search_collections"] == [
            {"id": "c1", "name": "Provider Onboarding"},
            {"id": "c3", "name": "Enablement Playbooks"}]
        assert dlg.selected == [
            {"id": "c1", "name": "Provider Onboarding"},
            {"id": "c3", "name": "Enablement Playbooks"}]
        assert dlg.result() == QDialog.DialogCode.Accepted

    def test_prechecked_selection_round_trips_unchanged(self, qapp,
                                                        fake_settings,
                                                        fake_guru):
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": "c2", "name": "Billing"}]}}
        dlg = make(qapp)
        dlg._on_ok()
        assert fake_settings["enablement"]["guru"]["search_collections"] == \
            [{"id": "c2", "name": "Billing"}]

    def test_empty_selection_persists_empty_list_meaning_all(
            self, qapp, fake_settings, fake_guru):
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": "c2", "name": "Billing"}]}}
        dlg = make(qapp)
        set_checked(dlg, "c2", False)
        dlg._on_ok()
        assert fake_settings["enablement"]["guru"]["search_collections"] == []
        assert dlg.selected == []
        assert dlg.result() == QDialog.DialogCode.Accepted

    def test_ok_preserves_sibling_guru_keys(self, qapp, fake_settings,
                                            fake_guru):
        """The publish target lives next door in the same subsection — the
        scope write must not clobber it."""
        fake_settings["enablement"] = {"guru": {"publish_collection_id": "PC1"},
                                       "demo_mode": False}
        dlg = make(qapp)
        set_checked(dlg, "c1", True)
        dlg._on_ok()
        assert fake_settings["enablement"]["guru"]["publish_collection_id"] == "PC1"
        assert fake_settings["enablement"]["demo_mode"] is False

    def test_failed_write_keeps_the_dialog_open_and_says_so(
            self, qapp, fake_settings, fake_guru, monkeypatch):
        import src.data.settings_manager as sm
        monkeypatch.setattr(sm, "set_section", lambda *_a: False)
        dlg = make(qapp)
        set_checked(dlg, "c1", True)
        dlg._on_ok()
        assert dlg.result() != QDialog.DialogCode.Accepted
        assert dlg.selected is None
        assert not dlg._banner.isHidden()
        assert "NOT saved" in dlg._banner.text()


# ── not connected ─────────────────────────────────────────────────────

class TestNotConnected:
    def test_not_connected_shows_the_message_not_an_empty_list(
            self, qapp, fake_settings, fake_guru):
        fake_guru.credentials = ("", "")
        dlg = make(qapp)
        assert not dlg._banner.isHidden()
        assert "isn't connected" in dlg._banner.text()
        assert dlg._list.count() == 0
        assert not dlg._workers and not dlg._pending
        assert fake_guru.constructed == []       # no client, no HTTP

    def test_refresh_after_connecting_recovers(self, qapp, fake_settings,
                                               fake_guru):
        fake_guru.credentials = ("", "")
        dlg = make(qapp)
        assert not dlg._banner.isHidden()
        fake_guru.credentials = ("chris@cambric.ai", "tok-123")
        dlg.reload()
        qapp.processEvents()
        assert dlg._banner.isHidden()
        assert dlg._list.count() == 3

    def test_not_connected_keeps_the_stored_scope_intact_on_ok(
            self, qapp, fake_settings, fake_guru):
        """With no listing, the stored rows still render checked, so OK
        round-trips the existing scope instead of silently wiping it."""
        fake_settings["enablement"] = {"guru": {"search_collections": [
            {"id": "c2", "name": "Billing"}]}}
        fake_guru.credentials = ("", "")
        dlg = make(qapp)
        assert checked_ids(dlg) == ["c2"]
        dlg._on_ok()
        assert fake_settings["enablement"]["guru"]["search_collections"] == \
            [{"id": "c2", "name": "Billing"}]


# ── the empty-scope contract ──────────────────────────────────────────

class TestEmptyMeansAll:
    def test_dialog_states_that_empty_means_all_collections(
            self, qapp, fake_settings, fake_guru):
        dlg = make(qapp)
        texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
        assert any(
            "Empty = all collections — the scope is never silently narrowed."
            in t for t in texts)

    def test_ok_is_enabled_with_nothing_checked(self, qapp, fake_settings,
                                                fake_guru):
        """Empty is a valid scope (= all) — OK must never gate on a check."""
        dlg = make(qapp)
        assert checked_ids(dlg) == []
        assert dlg._ok_btn.isEnabled()
