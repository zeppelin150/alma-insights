"""Service-account status line on the Google Drive credentials card.

Regression cover for the 2026-07-21 incident: the card's ONLY status text was
the OAuth line, which on a build with no bundled GCP client permanently reads
"No OAuth client — add your own GCP client below first." That sat directly
beneath a service-account key that was authenticating fine and could see 100
files, so the operator reasonably concluded Drive was broken and spent hours
on it. Meanwhile Renn invented three SSO documents because the share
genuinely contained none — a state the card could not express either.

These tests pin the two things the card must now be able to say:
  * a healthy key with files  -> "Connected … N files visible"
  * a healthy key with NO share -> "Connected …" AND the address to share to
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.ui.widgets.credentials_panel import CredentialsPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch):
    """Point settings_manager at a temp settings.yaml so a panel construction
    never reads — or writes — the operator's real credentials."""
    import src.data.settings_manager as sm
    path = tmp_path / "settings.yaml"
    monkeypatch.setattr(sm, "get_settings_path", lambda: path)
    return path


fmt = CredentialsPanel._format_sa_status
SA = "almainsights-test@alma-insights-test.iam.gserviceaccount.com"


class TestConnectedWithFiles:
    def test_reports_the_visible_file_count(self):
        out = fmt({"ok": True, "count": 100, "capped": False, "account": SA})
        assert out.startswith("Connected — service account")
        assert "100 files visible" in out

    def test_caps_render_with_a_plus(self):
        out = fmt({"ok": True, "count": 100, "capped": True, "account": SA})
        assert "100+ files visible" in out

    def test_singular_file(self):
        out = fmt({"ok": True, "count": 1, "capped": False, "account": ""})
        assert "1 file visible" in out and "files" not in out

    def test_account_is_shown_so_the_operator_knows_who_holds_access(self):
        assert SA in fmt({"ok": True, "count": 5, "capped": False, "account": SA})


class TestConnectedButEmpty:
    """The case that caused the incident: auth OK, share empty. This must NOT
    read as a failure, and it MUST name the address to share folders to."""

    def test_zero_files_still_says_connected(self):
        out = fmt({"ok": True, "count": 0, "capped": False, "account": SA})
        assert out.startswith("Connected — service account")
        assert "Not connected" not in out

    def test_zero_files_names_the_address_to_share_with(self):
        out = fmt({"ok": True, "count": 0, "capped": False, "account": SA})
        assert SA in out
        assert "Share a folder" in out

    def test_zero_files_without_an_account_email_does_not_dangle(self):
        out = fmt({"ok": True, "count": 0, "capped": False, "account": ""})
        assert out.endswith("yet.")


class TestNotConnected:
    def test_failure_shows_the_reason(self):
        out = fmt({"ok": False, "count": 0, "capped": False, "account": "",
                   "detail": "Service-account credentials path not set"})
        assert out.startswith("Not connected — ")
        assert "credentials path not set" in out

    def test_oauth_branch_renders_nothing(self):
        """auth_type='oauth_user' -> the worker returns a blank detail, and this
        card's SA line must stay silent rather than contradict the OAuth label
        immediately below it."""
        assert fmt({"ok": False, "count": 0, "capped": False,
                    "account": "", "detail": ""}) == ""

    @pytest.mark.parametrize("junk", [None, "", 0, [], "nope"])
    def test_malformed_results_never_raise(self, junk):
        assert fmt(junk) == ""


class TestWorkerContract:
    def test_worker_never_raises_into_qt(self, monkeypatch):
        """run() must swallow everything — an exception escaping a QThread.run
        is a native-level crash risk, not a traceback."""
        from src.ui.widgets.drive_probe_worker import DriveProbeWorker
        w = DriveProbeWorker()
        monkeypatch.setattr(
            w, "_probe", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        seen = []
        w.finished.connect(seen.append)
        w.run()   # direct call: exercises the body without starting a thread
        assert seen and seen[0]["ok"] is False
        assert "boom" in seen[0]["detail"]

    def test_account_reader_never_touches_the_private_key(self):
        """The key-file read must touch client_email only. A regression that
        returned the whole key dict would leak the private key into a UI label.
        The read now lives in google_access.service_account_email (shared with
        the folder-picker dialog); the worker must stay a pure delegate."""
        import inspect
        from src.data import google_access
        from src.ui.widgets.drive_probe_worker import DriveProbeWorker
        src = inspect.getsource(google_access.service_account_email)
        assert "client_email" in src
        assert "private_key" not in src
        assert "service_account_email" in inspect.getsource(DriveProbeWorker._account)

    def test_service_account_email_reads_the_configured_key(self, tmp_path, monkeypatch):
        """End-to-end through settings: auth_type=service_account + a key file
        on disk -> the client_email; oauth_user -> '' (that card has its own
        status line and this helper must stay silent)."""
        import json
        from src.data import google_access
        key = tmp_path / "sa.json"
        key.write_text(json.dumps({"client_email": SA, "private_key": "SECRET"}),
                       encoding="utf-8")
        cfg = {"drive": {"auth_type": "service_account",
                         "credentials_path": str(key)}}
        monkeypatch.setattr("src.data.settings_manager.get_section",
                            lambda name, default=None: cfg if name == "enablement" else default)
        assert google_access.service_account_email() == SA
        cfg["drive"]["auth_type"] = "oauth_user"
        assert google_access.service_account_email() == ""


class TestNoThreadWhenHeadless:
    """Regression: the first cut of this feature probed from refresh(), which
    __init__ calls — so merely CONSTRUCTING the panel started a network QThread.
    With no event loop to join it, the interpreter crashed at teardown with
    0xC0000409 (verified: clean at HEAD, crashing with the change). The probe is
    now show-gated and save-gated on isVisible()."""

    def test_construction_starts_no_probe_thread(self, qapp, temp_settings):
        panel = CredentialsPanel(sections=("external",))
        assert panel._sa_probe_worker is None
        assert panel._sa_probed_once is False

    def test_headless_save_starts_no_probe_thread(self, qapp, temp_settings, tmp_path):
        sa = tmp_path / "sa.json"
        sa.write_text("{}")
        panel = CredentialsPanel(sections=("external",))
        panel._sa_path.setText(str(sa))
        panel._on_save_sa()          # never shown -> must stay thread-free
        assert panel._sa_probe_worker is None

    def test_probe_is_wired_to_showEvent_not_refresh(self):
        import inspect
        assert "_probe_sa" in inspect.getsource(CredentialsPanel.showEvent)
        assert "_probe_sa" not in inspect.getsource(CredentialsPanel.refresh)


class TestStaleWorkerReference:
    """Regression (reported 2026-07-21 22:17): the probe connected finished ->
    deleteLater, so Qt destroyed the C++ object while self._sa_probe_worker kept
    the dead Python wrapper. The NEXT probe called worker.isRunning() on it:

        RuntimeError: Internal C++ object (DriveProbeWorker) already deleted.
        credentials_panel.py:621 in _probe_sa

    Clicking Connect twice — or reopening the card — was enough to hit it."""

    class _Dead:
        """Stands in for a wrapper whose C++ side is gone."""
        def isRunning(self):
            raise RuntimeError(
                "Internal C++ object (DriveProbeWorker) already deleted.")

    def test_probe_survives_a_dead_worker_reference(self, qapp, temp_settings, monkeypatch):
        panel = CredentialsPanel(sections=("external",))
        panel._sa_probe_worker = self._Dead()
        started = []
        monkeypatch.setattr(
            "src.ui.widgets.drive_probe_worker.DriveProbeWorker.start",
            lambda self: started.append(1))
        panel._probe_sa()            # must not raise
        assert started, "a fresh probe should still start after a dead reference"

    def test_completion_clears_the_reference(self, qapp, temp_settings):
        """The fix: drop the Python reference when the probe finishes, so there
        is never a wrapper around a deleted object to dereference."""
        panel = CredentialsPanel(sections=("external",))
        panel._sa_probe_worker = object()
        panel._on_sa_probe({"ok": True, "count": 3, "capped": False, "account": ""})
        assert panel._sa_probe_worker is None
        assert "3 files visible" in panel._sa_status.text()


class TestSaveWritesAuthType:
    def test_save_overwrites_a_stale_oauth_auth_type(self, monkeypatch):
        """setdefault would leave auth_type='oauth_user' in place, and
        DriveReader.is_configured() would then take the OAuth branch and call a
        perfectly good service-account key 'not configured'."""
        import inspect
        src = inspect.getsource(CredentialsPanel._on_save_sa)
        assert 'drive["auth_type"] = "service_account"' in src
        assert 'setdefault("auth_type"' not in src
