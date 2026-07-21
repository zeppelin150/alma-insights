"""Demo mode must not let Renn's publish tool reach live Guru.

Audit finding 03: the Workbench publish button suppresses the Guru client in
demo mode (``page.py::_guru_for_push`` returns None), but Renn's
``push_guru_draft`` tool built a real ``GuruClient`` from stored credentials
with no demo check — so a draft published through chat while demo mode was on
was handed a live client and reached production Guru.

The contract mirrors the Workbench, deliberately: demo mode SUPPRESSES the
client rather than refusing the tool, so the draft still marks as pushed
locally and the demo flow stays complete.
"""

import pytest

from src.data.chat_tools import enablement_tools


@pytest.fixture
def captured(monkeypatch):
    """Capture the guru_client publish_draft is handed, without publishing.

    enablement_tools imports the store lazily inside each function, so the
    patch has to land on the store module itself.
    """
    from src.data import enablement_store
    seen = {}

    def fake_publish(conn, did, guru_client=None, collection_id=None,
                     folder_id=None):
        seen["client"] = guru_client
        seen["called"] = True
        return {"ok": True, "draft_id": did}

    monkeypatch.setattr(enablement_store, "publish_draft", fake_publish)
    return seen


@pytest.fixture
def a_draft(monkeypatch):
    """A draft that is approved, so the approval gate does not short-circuit."""
    from src.data import enablement_store
    monkeypatch.setattr(
        enablement_store, "get_draft",
        lambda conn, did: {"draft_id": did, "id": did, "title": "Card",
                           "require_approval": False, "approved_at": "2026-07-20"})


def _set_demo(monkeypatch, enabled):
    import src.data.settings_manager as sm
    monkeypatch.setattr(
        sm, "get_section",
        lambda name, default=None: ({"demo_mode": enabled}
                                    if name == "enablement" else (default or {})))


def _credentials_exist(monkeypatch):
    """Stored Guru credentials are present — the precondition that made the
    original defect reachable."""
    from src.data.guru_client import GuruClient
    monkeypatch.setattr(GuruClient, "load_credentials",
                        classmethod(lambda cls: ("ops@example.com", "tok-123")))


def test_demo_mode_suppresses_the_guru_client(monkeypatch, empty_db, captured,
                                              a_draft):
    """The defect: with credentials stored and demo mode ON, the tool must NOT
    hand a live client to publish_draft."""
    _credentials_exist(monkeypatch)
    _set_demo(monkeypatch, True)
    result = enablement_tools.handle_push_guru_draft(
        empty_db.conn, {"draft_id": 1}, {})
    assert captured.get("called"), "publish_draft was not reached"
    assert captured["client"] is None, (
        "demo mode handed a LIVE GuruClient to publish_draft — a demo publish "
        "can reach production Guru")
    assert result.get("ok") is True, "the demo publish should still complete locally"


def test_live_mode_still_passes_a_real_client(monkeypatch, empty_db, captured,
                                              a_draft):
    """The suppression must be demo-only — live publishing keeps working."""
    _credentials_exist(monkeypatch)
    _set_demo(monkeypatch, False)
    enablement_tools.handle_push_guru_draft(
        empty_db.conn, {"draft_id": 1}, {})
    assert captured["client"] is not None, (
        "live mode lost its Guru client — publishing would silently become "
        "local-only")


def test_missing_demo_key_fails_safe(monkeypatch, empty_db, captured, a_draft):
    """`demo_mode` absent from settings must be treated as demo ON, matching
    kb_tools' default — an unconfigured install must not publish live."""
    import src.data.settings_manager as sm
    _credentials_exist(monkeypatch)
    monkeypatch.setattr(sm, "get_section",
                        lambda name, default=None: {} if name == "enablement"
                        else (default or {}))
    enablement_tools.handle_push_guru_draft(
        empty_db.conn, {"draft_id": 1}, {})
    assert captured["client"] is None, (
        "an absent demo_mode key defaulted to LIVE — it must fail safe to demo")


def test_settings_failure_fails_safe(monkeypatch, empty_db, captured, a_draft):
    """A settings read that raises must also suppress, never publish live."""
    import src.data.settings_manager as sm
    _credentials_exist(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(sm, "get_section", boom)
    enablement_tools.handle_push_guru_draft(
        empty_db.conn, {"draft_id": 1}, {})
    assert captured["client"] is None, (
        "a settings failure published live — it must fail safe to demo")


def test_no_credentials_is_still_local(monkeypatch, empty_db, captured, a_draft):
    """Unchanged behaviour: with no credentials the publish stays local in
    either mode."""
    from src.data.guru_client import GuruClient
    monkeypatch.setattr(GuruClient, "load_credentials",
                        classmethod(lambda cls: ("", "")))
    _set_demo(monkeypatch, False)
    enablement_tools.handle_push_guru_draft(
        empty_db.conn, {"draft_id": 1}, {})
    assert captured["client"] is None
