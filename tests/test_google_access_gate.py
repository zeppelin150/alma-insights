"""The "is Google reachable" gate must be auth_type-aware, not is_active()-only.

``google_oauth.is_active()`` answers "did the operator reconnect the per-user
OAuth session THIS launch". On the service-account path that flag is
structurally always False, so every caller that used it as a proxy for "Google
is connected" reported a perfectly good service-account credential as
permanently disconnected: the KB tick skipped forever, the Settings status line
claimed a disconnection that wasn't real, and "Bootstrap EC folder" was
unreachable.

These tests pin the fixed gate (``src.data.google_access.google_access_ready``)
at each site while ``is_active()`` stays False — which is the whole point — and
pin the oauth_user path's disable-on-launch behaviour as a regression guard.
"""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def drive_settings(tmp_path, monkeypatch):
    """Isolated enablement.drive config. Belt AND braces: get_settings_path →
    tmp (so ANY unpatched read misses the dev machine's real file), plus
    get_section/set_section over an in-memory dict."""
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_settings_path",
                        lambda: tmp_path / "settings.yaml")
    state: dict = {}
    monkeypatch.setattr(sm, "get_section",
                        lambda n, d=None: state.get(n, d if d is not None else {}))
    monkeypatch.setattr(sm, "set_section",
                        lambda n, v: state.__setitem__(n, dict(v)) or True)
    return state


@pytest.fixture
def sa_creds(tmp_path):
    """A credentials file that exists — is_configured() only does is_file()."""
    p = tmp_path / "sa.json"
    p.write_text("{}")
    return str(p)


@pytest.fixture
def reset_oauth():
    import src.data.google_oauth as go
    go.disconnect()
    yield go
    go.disconnect()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _RecordingDrive:
    """Records every attribute touched instead of raising.

    A raising tripwire is USELESS here: ``tick_once`` wraps the push,
    reconcile and staleness legs in ``except Exception`` and logs, so an
    AssertionError from inside them is swallowed and the test still passes.
    Recording + asserting the log is empty survives those handlers.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self.calls: list[str] = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _record(*a, **k):
            self.calls.append(f"{self.kind}.{name}")
            return None
        return _record


def _NoDriveReader():
    return _RecordingDrive("read")


def _NoDriveExporter():
    return _RecordingDrive("write")


def _sa(sa_creds, **over):
    drive = {"auth_type": "service_account", "credentials_path": sa_creds,
             "read_enabled": True}
    drive.update(over)
    return {"drive": drive}


# ── site B — src/data/kb/worker.py ───────────────────────────────────

def test_service_account_tick_is_not_skipped_as_google_not_connected(
        empty_db, drive_settings, sa_creds, reset_oauth, monkeypatch):
    """THE REPRO: a service-account install is connected, so the tick must run."""
    from src.data import google_oauth
    from src.data.kb import worker
    drive_settings["enablement"] = _sa(sa_creds)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    res = worker.tick_once(empty_db.conn, reader=_NoDriveReader(),
                           exporter=_NoDriveExporter())

    assert res.get("skipped") is False
    assert res.get("reason") != "google_not_connected"


def test_service_account_tick_on_unbootstrapped_kb_makes_no_drive_calls(
        empty_db, drive_settings, sa_creds, reset_oauth, monkeypatch):
    """Blast-radius pin: with an empty KB the tick touches no Drive surface and
    creates no folders/queue/cards."""
    from src.data import google_oauth
    from src.data.kb import worker
    conn = empty_db.conn
    drive_settings["enablement"] = _sa(sa_creds)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)
    reader, exporter = _NoDriveReader(), _NoDriveExporter()

    worker.tick_once(conn, reader=reader, exporter=exporter, do_reconcile=True)

    # tick_once swallows exceptions from the push/reconcile/staleness legs, so
    # this must be a recorded-call assertion, not a raise-on-touch tripwire.
    assert reader.calls == [], f"unexpected Drive reads: {reader.calls}"
    assert exporter.calls == [], f"unexpected Drive writes: {exporter.calls}"
    for table in ("kb_folders", "kb_queue", "kb_cards"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n == 0, f"{table} must stay empty on an unbootstrapped KB"


def test_oauth_user_tick_still_skips_until_reconnect(
        empty_db, drive_settings, reset_oauth, monkeypatch):
    """Disable-on-launch guard: the OAuth path is still gated on reconnect."""
    from src.data import google_oauth
    from src.data.kb import worker
    drive_settings["enablement"] = {"drive": {"auth_type": "oauth_user",
                                              "read_enabled": True}}
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    res = worker.tick_once(empty_db.conn, reader=_NoDriveReader(),
                           exporter=_NoDriveExporter())

    assert res == {"skipped": True, "reason": "google_not_connected"}


def test_tick_skips_when_drive_read_is_disabled(
        empty_db, drive_settings, sa_creds, reset_oauth, monkeypatch):
    """read_enabled stays a hard off-switch for the whole tick."""
    from src.data import google_oauth
    from src.data.kb import worker
    drive_settings["enablement"] = _sa(sa_creds, read_enabled=False)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    res = worker.tick_once(empty_db.conn, reader=_NoDriveReader(),
                           exporter=_NoDriveExporter())

    assert res == {"skipped": True, "reason": "google_not_connected"}


def test_tick_skips_when_service_account_file_is_missing(
        empty_db, drive_settings, tmp_path, reset_oauth, monkeypatch):
    """A credentials path that isn't there is not a usable connection."""
    from src.data import google_oauth
    from src.data.kb import worker
    drive_settings["enablement"] = _sa(str(tmp_path / "nope.json"))
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    res = worker.tick_once(empty_db.conn, reader=_NoDriveReader(),
                           exporter=_NoDriveExporter())

    assert res == {"skipped": True, "reason": "google_not_connected"}


# ── site A — src/ui/pages/enablement/settings.py (status line) ───────

def test_status_line_does_not_claim_disconnected_on_service_account(
        qapp, drive_settings, sa_creds, reset_oauth, monkeypatch):
    """Cosmetic bug site: the degraded-state line must not name a
    disconnection that only exists on the OAuth path."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    from src.ui.pages.enablement.settings import SettingsPage
    state = {"demo_mode": False, "kb": {},
             "drive": {"auth_type": "service_account",
                       "credentials_path": sa_creds, "read_enabled": True}}
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: state)
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    page = SettingsPage()
    try:
        page.refresh_kb_status()
        assert "Google: not connected this session" not in page._kb_status.text()
    finally:
        page.deleteLater()


def test_status_line_still_warns_when_oauth_user_never_reconnected(
        qapp, drive_settings, reset_oauth, monkeypatch):
    """Regression guard for the OAuth path's honest warning."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    from src.ui.pages.enablement.settings import SettingsPage
    state = {"demo_mode": False, "kb": {},
             "drive": {"auth_type": "oauth_user", "read_enabled": True}}
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: state)
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    page = SettingsPage()
    try:
        page.refresh_kb_status()
        assert "Google: not connected this session" in page._kb_status.text()
    finally:
        page.deleteLater()


# ── site A′ — settings.py bootstrap gate ─────────────────────────────

def test_bootstrap_is_not_blocked_on_service_account(
        qapp, drive_settings, sa_creds, reset_oauth, monkeypatch):
    """Functional bug site: "Bootstrap EC folder" was unreachable on the
    service-account path. ensure_ec_root is stubbed so no Drive call happens."""
    import src.data.settings_manager as sm
    from PySide6.QtCore import QCoreApplication
    from src.data import asana_setup, google_oauth
    from src.data.kb import drive_kb
    from src.ui.pages.enablement.settings import SettingsPage
    state = {"demo_mode": False, "kb": {"enabled": True},
             "drive": {"auth_type": "service_account",
                       "credentials_path": sa_creds, "read_enabled": True}}
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: state)
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)
    monkeypatch.setattr(drive_kb, "ensure_ec_root", lambda conn: {"ok": True})

    page = SettingsPage()
    try:
        seen: list[str] = []
        page._kb_bootstrap_finished.connect(seen.append)

        class _Conn:
            def close(self):
                pass

        page.kb_conn_factory = _Conn
        page._on_kb_bootstrap()
        deadline = time.monotonic() + 10          # wall-clock bound, not a
        while not seen and time.monotonic() < deadline:   # spin count
            QCoreApplication.processEvents()
            time.sleep(0.005)
        assert seen, "the bootstrap worker never reported back"
        # Positive assertion: an `is not in` check alone passes when the
        # bootstrap explodes for some unrelated reason.
        assert seen[0] == "EC folder ready."
        assert "Reconnect first" not in seen[0]
    finally:
        page.deleteLater()


# ── site C — Renn's in-chat Drive folder picker ──────────────────────

def test_drive_folder_picker_is_not_needs_connect_on_service_account(
        qapp, drive_settings, sa_creds, reset_oauth, monkeypatch):
    """The picker's gate was the same is_active()-only check: on a
    service-account install it answered ``needs_connect`` forever even though
    the worker behind it (DriveReader.from_settings) would have worked."""
    from src.data import google_oauth
    from src.services.agent_chat import AgentChatController
    drive_settings["enablement"] = _sa(sa_creds)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)
    ctl = AgentChatController(demo=True)
    try:
        assert ctl._google_is_active() is True
    finally:
        ctl.deleteLater()


def test_drive_folder_picker_still_needs_connect_on_oauth_user(
        qapp, drive_settings, reset_oauth, monkeypatch):
    """Disable-on-launch regression guard for the picker."""
    from src.data import google_oauth
    from src.services.agent_chat import AgentChatController
    drive_settings["enablement"] = {"drive": {"auth_type": "oauth_user",
                                              "read_enabled": True}}
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)
    ctl = AgentChatController(demo=True)
    try:
        assert ctl._google_is_active() is False
    finally:
        ctl.deleteLater()


# ── helper contract ──────────────────────────────────────────────────

def test_helper_is_safe_in_the_mcp_subprocess(
        drive_settings, sa_creds, reset_oauth, monkeypatch):
    """Under ALMA_MCP_MODE the OAuth branch raises inside google_oauth; the
    helper must absorb that and answer "not ready" instead of exploding in a
    worker tick. The service-account branch never consults google_oauth."""
    from src.data.google_access import google_access_ready
    monkeypatch.setenv("ALMA_MCP_MODE", "1")

    drive_settings["enablement"] = {"drive": {"auth_type": "oauth_user",
                                              "read_enabled": True}}
    assert google_access_ready() is False

    drive_settings["enablement"] = _sa(sa_creds)
    assert google_access_ready() is True
