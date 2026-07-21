"""Asana setup must not persist a board config built from mock GIDs.

Audit finding 14: with no key stored, asana_setup.discover() silently returns
MOCK_DISCOVERY, and the real setup flow wrote that fabricated project/field
config (project 120420000111 "Enablement Requests") into monitor_sources. The
mock is legitimate for demo mode (it shows the UI working); it must never reach
a real source config.

Contract: discover() tags its result with `mock`, and the setup writer refuses
to save mock data outside demo mode.
"""

import pytest

from src.data import asana_setup


def test_discover_flags_mock_when_no_key(monkeypatch):
    monkeypatch.setattr(asana_setup, "load_setting" if hasattr(
        asana_setup, "load_setting") else "__nope__", None, raising=False)
    # No key path: patch pat_store.load_setting to return empty.
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "load_setting", lambda k, d="": "")
    disc = asana_setup.discover()
    assert disc.get("mock") is True, "mock fallback must be flagged"


def test_discover_flags_live_when_key_present(monkeypatch):
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "load_setting", lambda k, d="": "real-key")

    class FakeClient:
        def __init__(self, key):
            pass

        def discover(self):
            return {"workspace": {"gid": "W9", "name": "Live"},
                    "projects": [{"gid": "999", "name": "Real Project"}],
                    "custom_fields": {}}

    import src.data.asana_client as ac
    monkeypatch.setattr(ac, "AsanaClient", FakeClient)
    disc = asana_setup.discover()
    assert disc.get("mock") is False, "a live discovery must not be flagged mock"
    assert disc["projects"][0]["gid"] == "999"


def test_discover_flags_mock_on_client_error(monkeypatch):
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "load_setting", lambda k, d="": "real-key")

    class BoomClient:
        def __init__(self, key):
            pass

        def discover(self):
            raise RuntimeError("network down")

    import src.data.asana_client as ac
    monkeypatch.setattr(ac, "AsanaClient", BoomClient)
    disc = asana_setup.discover()
    assert disc.get("mock") is True, "a failed live discovery falls back to mock and must say so"


def test_mock_discovery_constant_is_not_mutated(monkeypatch):
    """discover() must not stamp its flag onto the shared MOCK_DISCOVERY dict."""
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "load_setting", lambda k, d="": "")
    asana_setup.discover()
    assert "mock" not in asana_setup.MOCK_DISCOVERY, \
        "the shared mock constant was mutated"
