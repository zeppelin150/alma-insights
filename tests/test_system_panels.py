"""UpdatesPanel + MaintenancePanel — the shared system widgets both Settings
pages embed (extracted from the product page 2026-07-22).

Hermetic: settings/keyring/updater state are monkeypatched, no network, no
real vault, offscreen Qt.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QWidget  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ── UpdatesPanel ──────────────────────────────────────────────────────

@pytest.fixture
def updates(qapp, monkeypatch):
    """An UpdatesPanel over faked settings + keyring + rollback state."""
    import src.ui.widgets.updates_panel as up
    import src.updater.rollback as rollback

    store: dict[str, dict] = {}

    def fake_get(section, default=None):
        return dict(store.get(section) or ({} if default is None else default))

    def fake_update(section, updates_):
        cur = dict(store.get(section) or {})
        cur.update(updates_ or {})
        store[section] = cur
        return True

    monkeypatch.setattr(up, "get_section", fake_get)
    monkeypatch.setattr(up, "update_section", fake_update)
    monkeypatch.setattr(rollback, "current_state", lambda: None)

    import src.data.pat_store as pat_store
    vault: dict[str, str] = {}
    monkeypatch.setattr(pat_store, "load_setting",
                        lambda k, d=None: vault.get(k, d if d is not None else ""))
    monkeypatch.setattr(pat_store, "save_setting",
                        lambda k, v: (vault.__setitem__(k, v), True)[1])

    panel = up.UpdatesPanel()
    return panel, store, vault


def test_constructs_with_wired_buttons_and_hidden_install_flow(updates):
    panel, _store, _vault = updates
    assert panel._check_updates_btn.receivers("2clicked(bool)") >= 1
    assert not panel._install_btn.isVisibleTo(panel)
    assert not panel._restart_btn.isVisibleTo(panel)
    assert not panel._release_notes_btn.isEnabled()
    # No previous version on disk → rollback offered but disabled.
    assert not panel._rollback_btn.isEnabled()


def test_github_settings_save_merges_and_normalizes(updates):
    panel, store, vault = updates
    store["updates"] = {"last_checked": "2026-07-01 09:00"}
    panel._github_repo_input.setText("https://github.com/alma-health/alma-insights")
    panel._github_pat_input.setText("ghp_secret")
    panel._on_save_github_settings()
    # Merged, not replaced — last_checked survives (the historical clobber bug).
    assert store["updates"]["github_repo"] == "alma-health/alma-insights"
    assert store["updates"]["last_checked"] == "2026-07-01 09:00"
    assert vault["github_pat"] == "ghp_secret"
    assert panel._github_repo_input.text() == "alma-health/alma-insights"
    assert panel._github_status.text() == "✓ Saved"


def test_github_fields_populate_on_first_show_only(updates):
    panel, store, vault = updates
    store["updates"] = {"github_repo": "owner/repo"}
    vault["github_pat"] = "ghp_loaded"
    assert panel._github_repo_input.text() == ""   # constructor reads nothing
    panel.show()
    assert panel._github_repo_input.text() == "owner/repo"
    assert panel._github_pat_input.text() == "ghp_loaded"
    # A later show must not re-read (the flag is one-shot).
    store["updates"] = {"github_repo": "changed/elsewhere"}
    panel.hide()
    panel.show()
    assert panel._github_repo_input.text() == "owner/repo"
    panel.hide()


def test_update_available_reveals_install_and_release_notes(updates):
    panel, _store, _vault = updates
    panel._on_update_available(
        "1.0.7", "1.0.8", "https://github.com/o/r/releases/tag/v1.0.8")
    assert panel._update_status_pill.text() == "v1.0.8 available"
    assert panel._release_notes_btn.isEnabled()
    assert panel._install_btn.isVisibleTo(panel)
    assert panel._latest_download_url.endswith(
        "/releases/download/v1.0.8/alma-insights-v1.0.8.zip")


def test_update_available_without_github_url_hides_install(updates):
    panel, _store, _vault = updates
    panel._on_update_available("1.0.7", "1.0.8", "")
    assert not panel._install_btn.isVisibleTo(panel)
    assert not panel._release_notes_btn.isEnabled()


def test_up_to_date_records_last_checked(updates):
    panel, store, _vault = updates
    panel._on_up_to_date()
    assert panel._update_status_pill.text() == "Up to date"
    assert store["updates"]["last_checked"]
    assert panel._last_checked_label.text().startswith("Last checked:")


def test_staged_update_offers_restart(updates):
    panel, _store, _vault = updates
    panel._on_update_complete()
    assert panel._restart_btn.isVisibleTo(panel)
    assert "Restart to apply" in panel._progress_label.text()


def test_failed_update_restores_the_install_button(updates):
    panel, _store, _vault = updates
    panel._on_update_failed("checksum mismatch")
    assert "checksum mismatch" in panel._update_msg.text()
    assert panel._install_btn.isVisibleTo(panel)


# ── MaintenancePanel ──────────────────────────────────────────────────

def _maintenance_host(qapp):
    """A top-level host standing in for MainWindow (self.window() target)."""
    from src.ui.widgets.maintenance_panel import MaintenancePanel
    host = QWidget()
    cleared = []
    host._clear_all_data = lambda: cleared.append(True)
    panel = MaintenancePanel(host)
    return host, panel, cleared


def test_reset_is_a_no_op_without_the_typed_delete(qapp, monkeypatch):
    _host, panel, cleared = _maintenance_host(qapp)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("nope", True)))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: pytest.fail("reset ran")))
    panel._full_database_reset()
    assert cleared == []


def test_reset_is_a_no_op_on_cancel(qapp, monkeypatch):
    _host, panel, cleared = _maintenance_host(qapp)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("DELETE", False)))
    panel._full_database_reset()
    assert cleared == []


def test_reset_with_typed_delete_clears_via_the_window(qapp, monkeypatch):
    _host, panel, cleared = _maintenance_host(qapp)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("DELETE", True)))
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a)))
    panel._full_database_reset()
    assert cleared == [True]
    assert shown, "no completion notice shown"


def test_force_gc_reports_before_and_after(qapp):
    _host, panel, _cleared = _maintenance_host(qapp)
    panel._on_force_gc()
    text = panel._mem_quick_stats.text()
    assert "GC collected" in text and "Objects:" in text
