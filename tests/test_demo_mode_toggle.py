"""
Demo/Live mode toggle on the enablement Settings page (Connections tab).

`enablement.demo_mode` defaults ON and previously had no UI writer — going
live required hand-editing settings.yaml, which the first end-user pilot
proved untenable. The card must read the flag honestly, confirm before the
authority-bearing demo→live flip (and only that direction), write through
settings_manager, surface write failures, and — because pages/monitors
capture the flag at construction — show an explicit restart-pending state
whenever the settings flag differs from the boot-time value.
"""

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _page(monkeypatch, state, *, write_ok=True):
    import src.data.settings_manager as sm

    written = {}

    def _get(name, default=None):
        # Reads reflect prior writes, like the real settings store.
        if name in written:
            return written[name]
        return state if name == "enablement" else (default or {})

    def _set(name, cfg):
        if write_ok:
            written[name] = cfg
        return write_ok

    monkeypatch.setattr(sm, "get_section", _get)
    monkeypatch.setattr(sm, "set_section", _set)
    from src.ui.pages.enablement.settings import SettingsPage

    return SettingsPage(), written


def _silence_info(monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )


def test_defaults_to_demo_mode(qapp, monkeypatch):
    page, _ = _page(monkeypatch, {})
    try:
        assert page._mode_toggle_btn.text() == "Go live…"
        assert "Demo mode" in page._mode_status.text()
        # The sandbox truth an end user must know: work is disposable.
        assert "cleared at every launch" in page._mode_status.text()
    finally:
        page.deleteLater()


def test_live_mode_reads_honestly(qapp, monkeypatch):
    page, _ = _page(monkeypatch, {"demo_mode": False, "kb": {}})
    try:
        assert page._mode_toggle_btn.text() == "Return to demo mode"
        assert "Live mode" in page._mode_status.text()
    finally:
        page.deleteLater()


def test_return_to_demo_writes_without_confirm(qapp, monkeypatch):
    page, written = _page(monkeypatch, {"demo_mode": False, "kb": {}})
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: pytest.fail("live→demo must not prompt for confirmation")
        ),
    )
    infos = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a: infos.append(a))
    )
    try:
        page._on_mode_toggle()
        assert written["enablement"]["demo_mode"] is True
        # The panic direction has no confirm, so the restart truth must
        # arrive as an info dialog instead.
        assert infos and "restart" in (infos[0][2]).lower()
    finally:
        page.deleteLater()


def test_go_live_requires_confirm_yes(qapp, monkeypatch):
    page, written = _page(monkeypatch, {"kb": {}})
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    _silence_info(monkeypatch)
    try:
        page._on_mode_toggle()
        assert written["enablement"]["demo_mode"] is False
        assert page._mode_toggle_btn.text() == "Return to demo mode"
        # Boot flag was demo → settings now live → restart-pending state.
        assert "restart" in page._mode_status.text().lower()
        assert "Mode change pending" in page._kb_status.text()
    finally:
        page.deleteLater()


def test_go_live_confirm_declined_writes_nothing(qapp, monkeypatch):
    page, written = _page(monkeypatch, {"kb": {}})
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    try:
        page._on_mode_toggle()
        assert written == {}
        assert page._mode_toggle_btn.text() == "Go live…"
    finally:
        page.deleteLater()


def test_write_failure_warns_and_keeps_state(qapp, monkeypatch):
    page, written = _page(monkeypatch, {"kb": {}}, write_ok=False)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a: warnings.append(a))
    )
    try:
        page._on_mode_toggle()
        assert written == {}
        assert warnings, "a failed settings write must be surfaced, not silent"
        assert page._mode_toggle_btn.text() == "Go live…"
    finally:
        page.deleteLater()


def test_full_round_trip_against_real_settings_file(qapp, monkeypatch, tmp_path):
    """Integration: no settings mocks — the real settings_manager against a
    temp settings.yaml, the real button clicked through Qt. Covers the
    whole chain the unit tests stub out: YAML load, atomic write-and-
    rename, re-read, pending-state detection, section preservation."""
    import yaml as _yaml

    import src.data.settings_manager as sm

    sp = tmp_path / "settings.yaml"
    sp.write_text(
        "enablement:\n  demo_mode: true\n  kb: {}\nother_section:\n  keep: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sm, "get_settings_path", lambda: sp)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    from src.ui.pages.enablement.settings import SettingsPage

    page = SettingsPage()
    try:
        assert page._mode_toggle_btn.text() == "Go live…"

        page._mode_toggle_btn.click()  # demo → live, real write

        on_disk = _yaml.safe_load(sp.read_text(encoding="utf-8"))
        assert on_disk["enablement"]["demo_mode"] is False
        assert on_disk["enablement"]["kb"] == {}, "sibling keys preserved"
        assert on_disk["other_section"] == {"keep": 1}, "other sections preserved"
        assert "restart" in page._mode_status.text().lower()

        page._mode_toggle_btn.click()  # live → demo, panic direction

        on_disk = _yaml.safe_load(sp.read_text(encoding="utf-8"))
        assert on_disk["enablement"]["demo_mode"] is True
        # Back in sync with the boot-time flag → normal demo copy again.
        assert "Demo mode" in page._mode_status.text()
    finally:
        page.deleteLater()


def test_confirm_dialog_does_not_stomp_concurrent_writes(qapp, monkeypatch):
    # The modal confirm spins a nested event loop; other slots (the chat
    # action poll) may write enablement subkeys meanwhile. The toggle must
    # re-read after the dialog, not write back a pre-dialog snapshot.
    state = {"kb": {}}
    page, written = _page(monkeypatch, state)

    def _question(*a, **k):
        state["drive"] = {"active_folders": ["folder-picked-mid-dialog"]}
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    _silence_info(monkeypatch)
    try:
        page._on_mode_toggle()
        assert written["enablement"]["demo_mode"] is False
        assert written["enablement"]["drive"] == {
            "active_folders": ["folder-picked-mid-dialog"]
        }
    finally:
        page.deleteLater()
