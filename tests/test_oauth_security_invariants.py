"""P6 — security invariants for the OAuth/keyring surface. These encode the
threat-model mitigations so a regression trips a test.
"""

import json
import logging
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture()
def fake_stores(monkeypatch):
    secrets, sections = {}, {}
    import src.data.pat_store as ps
    monkeypatch.setattr(ps, "save_setting",
                        lambda k, v: secrets.__setitem__(k, "" if v is None else str(v)) or True)
    monkeypatch.setattr(ps, "load_setting", lambda k, d=None: secrets.get(k, d))
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section",
                        lambda n, d=None: sections.get(n, d if d is not None else {}))
    monkeypatch.setattr(sm, "set_section",
                        lambda n, v: sections.__setitem__(n, dict(v)) or True)
    return secrets, sections


def test_scopes_are_least_privilege():
    import src.data.google_oauth as go
    for s in go.scopes():
        assert s.endswith("drive.readonly") or s.endswith("drive.file")
    assert "https://www.googleapis.com/auth/drive" not in go.scopes()


def test_flow_pins_ipv4_loopback_with_timeout():
    """Source-level guard: IPv4-pinned host (closes the localhost→::1 race),
    ephemeral port, bounded wait, never a hardcoded redirect port."""
    src = (_SRC / "data" / "google_oauth.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in src      # not the default 'localhost' (::1-first)
    assert "port=0" in src
    assert "timeout_seconds=" in src       # no infinite hang
    assert "port=8080" not in src and "port=8000" not in src


def test_record_is_minimal_and_bounded(fake_stores):
    import src.data.google_oauth as go
    go.disconnect()
    go.store_credentials({
        "client_id": "c", "client_secret": "s",
        "refresh_token": "1//0gR", "token_uri": "https://t",
        "access_token": "ya29.LIVE", "id_token": "eyJ" + "z" * 1500,
    })
    secrets, _ = fake_stores
    stored = json.loads(secrets["google_oauth_user"])
    assert set(stored) == set(go._RECORD_KEYS)   # exactly the 4 keys
    assert len(secrets["google_oauth_user"]) <= go._MAX_RECORD_CHARS


def test_tokens_never_logged(fake_stores, caplog):
    import src.data.google_oauth as go
    go.disconnect()
    with caplog.at_level(logging.DEBUG, logger="alma.google_oauth"):
        go.store_credentials({
            "client_id": "123.apps.googleusercontent.com",
            "client_secret": "GOCSPX-supersecret",
            "refresh_token": "1//0gSECRETrefreshTOKEN",
            "token_uri": "https://oauth2.googleapis.com/token",
        })
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "1//0gSECRETrefreshTOKEN" not in blob
    assert "GOCSPX-supersecret" not in blob


def test_client_config_none_when_neither(fake_stores, monkeypatch):
    import src.data.google_oauth as go
    monkeypatch.setattr(go, "_load_bundled_client", lambda: None)
    assert go.client_config() is None


def test_secret_never_falls_back_to_plaintext_json(tmp_path, monkeypatch):
    """The keyring no-plaintext property must hold for the OAuth key too."""
    import keyring
    import keyring.backend
    import keyring.errors

    class _Broken(keyring.backend.KeyringBackend):
        priority = 1
        def set_password(self, *a):
            raise keyring.errors.KeyringError("down")
        def get_password(self, *a):
            raise keyring.errors.KeyringError("down")
        def delete_password(self, *a):
            raise keyring.errors.KeyringError("down")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import importlib
    from src.data import pat_store
    importlib.reload(pat_store)
    original = keyring.get_keyring()
    keyring.set_keyring(_Broken())
    try:
        ok = pat_store.save_setting("google_oauth_user", '{"refresh_token":"1//SECRET"}')
        assert ok is False
        if pat_store._STATE_FILE.exists():
            assert "1//SECRET" not in pat_store._STATE_FILE.read_text()
    finally:
        keyring.set_keyring(original)


def test_bundled_client_module_is_gitignored():
    gi = (_SRC.parent / ".gitignore").read_text(encoding="utf-8")
    assert "src/data/_google_oauth_client.py" in gi
