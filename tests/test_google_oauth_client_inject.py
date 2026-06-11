"""P0 — build-time Google OAuth client injection (mirror of the release-PAT
obfuscation). Exercises the env-var matrix + obfuscation round-trip without
running a real bundle build.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

_FAKE_CLIENT = json.dumps({
    "installed": {
        "client_id": "1234567890-abcdefg.apps.googleusercontent.com",
        "project_id": "alma-insights",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "GOCSPX-fake_desktop_secret_value",
        "redirect_uris": ["http://localhost"],
    }
})


@pytest.fixture(scope="session")
def build_release():
    spec = importlib.util.spec_from_file_location(
        "build_release", _REPO_ROOT / "installer" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def staged_app(tmp_path):
    """A minimal staged app tree with the obfuscation helper in place."""
    app = tmp_path / "app"
    (app / "src" / "updater").mkdir(parents=True)
    (app / "src" / "data").mkdir(parents=True)
    shutil.copy2(
        _REPO_ROOT / "src" / "updater" / "_token_obfuscation.py",
        app / "src" / "updater" / "_token_obfuscation.py",
    )
    return app


def _load_emitted(app):
    path = app / "src" / "data" / "_google_oauth_client.py"
    spec = importlib.util.spec_from_file_location("_goc_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestInject:
    def test_round_trip(self, build_release, staged_app, monkeypatch):
        monkeypatch.setenv("ALMA_GOOGLE_OAUTH_CLIENT", _FAKE_CLIENT)
        build_release._inject_google_oauth_client(staged_app)

        mod = _load_emitted(staged_app)
        assert mod.SCHEMA == 1
        from src.updater._token_obfuscation import deobfuscate
        recovered = deobfuscate(mod.CLIENT_PAYLOAD, mod.CLIENT_KEY)
        assert json.loads(recovered) == json.loads(_FAKE_CLIENT)

    def test_secret_not_plaintext_on_disk(self, build_release, staged_app, monkeypatch):
        monkeypatch.setenv("ALMA_GOOGLE_OAUTH_CLIENT", _FAKE_CLIENT)
        build_release._inject_google_oauth_client(staged_app)
        text = (staged_app / "src" / "data" / "_google_oauth_client.py").read_text()
        assert "GOCSPX-fake_desktop_secret_value" not in text
        assert "client_secret" not in text  # the whole JSON is obfuscated

    def test_fingerprint_is_client_id(self, build_release, staged_app, monkeypatch):
        monkeypatch.setenv("ALMA_GOOGLE_OAUTH_CLIENT", _FAKE_CLIENT)
        build_release._inject_google_oauth_client(staged_app)
        mod = _load_emitted(staged_app)
        # fingerprint() is first4••••last4 of the client_id
        assert mod.CLIENT_FINGERPRINT.startswith("1234")

    def test_empty_env_raises(self, build_release, staged_app, monkeypatch):
        monkeypatch.setenv("ALMA_GOOGLE_OAUTH_CLIENT", "")
        with pytest.raises(RuntimeError, match="half-configured"):
            build_release._inject_google_oauth_client(staged_app)

    def test_absent_env_skips(self, build_release, staged_app, monkeypatch):
        monkeypatch.delenv("ALMA_GOOGLE_OAUTH_CLIENT", raising=False)
        build_release._inject_google_oauth_client(staged_app)
        assert not (staged_app / "src" / "data" / "_google_oauth_client.py").exists()


class TestParity:
    def test_google_packages_in_packages_list(self, build_release):
        names = {p.partition("==")[0] for p in build_release.PACKAGES}
        assert {"google-api-python-client", "google-auth",
                "google-auth-oauthlib"} <= names
