"""
Tests for pat_store's keyring backend self-heal.

Companion to tests/test_build_release_cleanup.py: even if a bundle ships
without *.dist-info metadata (killing keyring's entry-point discovery),
pat_store must direct-import the platform backend rather than leave the
fail stub in place.
"""

import sys
import types

import keyring
from keyring.backends import fail

from src.data import pat_store


def test_self_heal_replaces_fail_backend_windows(monkeypatch):
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    captured = {}
    monkeypatch.setattr(
        keyring, "set_keyring", lambda b: captured.__setitem__("backend", b)
    )
    monkeypatch.setattr(sys, "platform", "win32")

    pat_store._ensure_keyring_backend()

    from keyring.backends import Windows

    assert isinstance(captured["backend"], Windows.WinVaultKeyring)


def test_self_heal_replaces_fail_backend_macos(monkeypatch):
    # The real macOS backend can't import off-Mac (ctypes loads
    # Security.framework), so stand in a stub module for the import path.
    fake = types.ModuleType("keyring.backends.macOS")

    class _StubKeyring:
        pass

    fake.Keyring = _StubKeyring
    monkeypatch.setitem(sys.modules, "keyring.backends.macOS", fake)
    import keyring.backends as _backends

    monkeypatch.setattr(_backends, "macOS", fake, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    captured = {}
    monkeypatch.setattr(
        keyring, "set_keyring", lambda b: captured.__setitem__("backend", b)
    )

    pat_store._ensure_keyring_backend()

    assert isinstance(captured["backend"], _StubKeyring)


def test_self_heal_noop_when_backend_healthy(monkeypatch):
    class _Healthy:
        pass

    monkeypatch.setattr(keyring, "get_keyring", lambda: _Healthy())
    called = []
    monkeypatch.setattr(keyring, "set_keyring", called.append)

    pat_store._ensure_keyring_backend()

    assert called == []


def test_self_heal_never_raises(monkeypatch):
    monkeypatch.setattr(
        keyring, "get_keyring", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    pat_store._ensure_keyring_backend()  # must swallow, not raise


def test_self_heal_skips_linux(monkeypatch):
    # On Linux the fail stub usually means SecretService viability failed
    # (no D-Bus); healing would replace graceful degradation with crashes.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    called = []
    monkeypatch.setattr(keyring, "set_keyring", called.append)

    pat_store._ensure_keyring_backend()

    assert called == []


def test_self_heal_respects_explicit_env_backend(monkeypatch):
    # PYTHON_KEYRING_BACKEND=...fail.Keyring is a deliberate hard-disable;
    # the heal must never override explicit configuration.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(
        "PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring"
    )
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    called = []
    monkeypatch.setattr(keyring, "set_keyring", called.append)

    pat_store._ensure_keyring_backend()

    assert called == []


def test_self_heal_respects_keyringrc(monkeypatch, tmp_path):
    import keyring.util.platform_ as kp

    (tmp_path / "keyringrc.cfg").write_text(
        "[backend]\ndefault-keyring=keyring.backends.fail.Keyring\n"
    )
    monkeypatch.setattr(kp, "config_root", lambda: str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    called = []
    monkeypatch.setattr(keyring, "set_keyring", called.append)

    pat_store._ensure_keyring_backend()

    assert called == []
