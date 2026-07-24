# Pilot-Fix Surgical Port Guide — CoveHealth repo (2026-07-24)

**Source of truth:** `zeppelin150/alma-insights` @ `4f186fa` on branch
`enablement-content-tabs` (commits `a9def5c`, `6720872`, `9a1145e`, `4f186fa`).
**Target:** the CoveHealth `alma-insights` repo, on the branch its CI builds from.
**Executor:** a human in the GitHub web editor, or Claude working in the local
tree via GitHub Desktop. No git CLI required.

## What this port delivers

1. **Keyring fix** — bundles stop shipping with a dead credential store
   (the `NoKeyringError` critical splash failure on every end-user machine).
2. **Keyring self-heal** — even a metadata-stripped bundle recovers at runtime.
3. **OPERATING MODE card** — a real demo/live toggle in enablement Settings;
   no more hand-editing `settings.yaml`.
4. Corrected IT-security documentation + three regression-test files.

## Ground rules (read before any edit)

- Every edit below is **FIND verbatim → REPLACE verbatim**, or
  **CREATE file with exact contents**. Nothing is to be improvised, adapted,
  reformatted, or "improved".
- If a FIND block does not match **exactly** (including indentation), **STOP
  that edit and report the mismatch**. Do not guess.
- Python blocks are indentation-sensitive. Copy leading spaces exactly.
  **Never tabs.**
- Copy special characters exactly: `—` (em-dash), `…` (ellipsis), `⇄`, `→`,
  `•`. Tests assert on some of these (the button label is literally
  `Go live…`). Do not substitute ASCII lookalikes.
- Edits are independent of each other; do them in the order given anyway.
- The two macOS workflow files (`.github/workflows/build.yml`,
  `.github/workflows/release.yml`) are **NOT part of this port** — the
  CoveHealth copies were already fixed on 2026-07-23/24 (macos-15 runners,
  `if-no-files-found: error`, node24 action pins) and are AHEAD of the
  source repo's workflows. Leave them untouched.

---

## Edit 0 — Preflight (read-only, no changes)

0.1 Open `installer/build_release.py`, search for `arch_label`. Inside
`build_macos()` it must already read `arch_label = "x64"` and the arm check
must read `("arm64", "aarch64")`. If it still says `"x86_64"`, the 2026-07-24
browser fix did not land — STOP and report before continuing.

0.2 Open `.github/workflows/build.yml`, confirm it contains `macos-15-intel`
and `if-no-files-found: error`. If yes, the workflows are current — skip them
entirely, as stated above.

---

## Edit 1 — `installer/build_release.py` (keyring root cause)

One replacement inside `clean_python_dir()`.

**FIND (5 lines, directly after the `__pycache__` removal loop):**

```python
    # Remove .dist-info directories (pip metadata)
    for dist_info in python_dir.rglob("*.dist-info"):
        if dist_info.is_dir():
            shutil.rmtree(dist_info, ignore_errors=True)
            removed += 1
```

**REPLACE WITH (5 lines):**

```python
    # .dist-info directories are KEPT: importlib.metadata entry points live
    # there, and keyring 25.x resolves every backend (macOS Keychain, Windows
    # Credential Manager) through the 'keyring.backends' entry-point group.
    # Stripping them ships bundles whose keyring is the fail stub — a critical
    # "Keyring unavailable" splash failure on every end-user machine.
```

**Verify:** the file no longer contains the string `rglob("*.dist-info")`
anywhere, and the phrase `.dist-info directories are KEPT` appears once.

---

## Edit 2 — `src/data/pat_store.py` (keyring self-heal)

Two insertions.

### 2a — add the `sys` import

**FIND (3 lines, near the top of the file):**

```python
import json
import os
from pathlib import Path
```

**REPLACE WITH (4 lines):**

```python
import json
import os
import sys
from pathlib import Path
```

### 2b — insert the self-heal function

**FIND (3 lines, the configuration constants):**

```python
_CONFIG_DIR = Path.home() / ".alma-insights"
_STATE_FILE = _CONFIG_DIR / "ui_state.json"
_LEGACY_FILE = _CONFIG_DIR / "credentials.json"
```

**REPLACE WITH (the same 3 lines followed by the new block — copy all of it):**

```python
_CONFIG_DIR = Path.home() / ".alma-insights"
_STATE_FILE = _CONFIG_DIR / "ui_state.json"
_LEGACY_FILE = _CONFIG_DIR / "credentials.json"


# ──────────────────────────────────────────────────────────────────
# Backend self-heal
# ──────────────────────────────────────────────────────────────────

def _ensure_keyring_backend() -> None:
    """
    Recover when keyring resolves to the fail stub because backend
    DISCOVERY failed: an environment without *.dist-info metadata (a
    slimmed bundle, a frozen app) has no entry points to scan, so keyring
    finds nothing even though the platform backend imports and works.
    Direct-import the OS backend in that case.

    Deliberately narrow, in two ways:
    - Windows/macOS only — the platforms bundles ship to, where the OS
      vault always exists. On Linux the fail stub usually means the
      SecretService viability probe failed (no D-Bus session/daemon);
      installing that backend anyway would turn today's graceful
      degradation (NoKeyringError → load_setting returns default) into
      uncaught SecretServiceNotAvailableException crashes.
    - Never when PYTHON_KEYRING_BACKEND or a keyringrc is present: an
      admin may configure the fail backend ON PURPOSE to keep this app
      out of the OS vault. Explicit configuration outranks healing.
    """
    try:
        if sys.platform not in ("win32", "darwin"):
            return
        if os.environ.get("PYTHON_KEYRING_BACKEND"):
            return
        try:
            import keyring.util.platform_ as _kp
            if (Path(_kp.config_root()) / "keyringrc.cfg").exists():
                return
        except Exception:  # noqa: BLE001 — config probe is best-effort
            pass
        from keyring.backends import fail as _fail
        if not isinstance(keyring.get_keyring(), _fail.Keyring):
            return
        if sys.platform == "darwin":
            from keyring.backends import macOS as _mod
            backend = _mod.Keyring()
        else:
            from keyring.backends import Windows as _mod
            backend = _mod.WinVaultKeyring()
        keyring.set_keyring(backend)
    except Exception:  # noqa: BLE001 — never block import; the splash check reports
        pass


_ensure_keyring_backend()
```

**Verify:** `_ensure_keyring_backend` appears exactly twice in the file — once
as `def _ensure_keyring_backend()` and once as the bare call
`_ensure_keyring_backend()` directly below it.

---

## Edit 3 — `src/ui/pages/enablement/settings.py` (OPERATING MODE card)

Three replacements in one file. This is the largest edit — go slowly.

### 3a — wire the card into the Connections tab

**FIND (1 line, inside `__init__`):**

```python
        _add(self._tab([self._connections()]), "antenna", "Connections")
```

**REPLACE WITH (2 lines):**

```python
        _add(self._tab([self._mode_card(), self._connections()]),
             "antenna", "Connections")
```

### 3b — insert the three new methods

**FIND (2 lines — the knowledge-base section header):**

```python
    # ── knowledge base (WS2-M7: status + bootstrap + degraded-state) ──
    def _knowledge_base(self) -> QFrame:
```

**REPLACE WITH (the full operating-mode block below, ending with those same
2 lines — copy everything):**

```python
    # ── operating mode (demo ⇄ live) ─────────────────────────────────
    # The flag (`enablement.demo_mode`, default ON) previously had NO UI
    # writer — going live meant hand-editing settings.yaml, which the first
    # end-user pilot proved untenable. Going live is the authority-bearing
    # direction, so it gets a native confirm; returning to demo never does.
    def _mode_card(self) -> QFrame:
        # Boot-time flag snapshot: pages, the chat DB, and the background
        # monitor were all built against this value, and a settings flip
        # only fully lands after restart — the card must show the halfway
        # state honestly instead of claiming the new mode is in force.
        from src.data.settings_manager import get_section
        try:
            en0 = get_section("enablement", {}) or {}
        except Exception:  # noqa: BLE001
            en0 = {}
        self._boot_demo = bool(en0.get("demo_mode", True))

        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        v.addWidget(section_label("OPERATING MODE"))
        self._mode_status = QLabel("")
        self._mode_status.setWordWrap(True)
        self._mode_status.setStyleSheet(
            f"color:{ALMA_TEXT_MID}; font-size:12px; border:none;")
        v.addWidget(self._mode_status)
        row = QHBoxLayout()
        self._mode_toggle_btn = QPushButton("")
        self._mode_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._mode_toggle_btn.setStyleSheet(
            f"QPushButton{{background:{ALMA_BG_ELEVATED}; color:{_TEAL}; "
            f"border:1px solid {_TEAL}; border-radius:7px; padding:6px 12px; "
            f"font-size:12px; font-weight:600;}}")
        self._mode_toggle_btn.clicked.connect(self._on_mode_toggle)
        row.addWidget(self._mode_toggle_btn)
        row.addStretch(1)
        v.addLayout(row)
        self._refresh_mode_card()
        return card

    def _refresh_mode_card(self):
        from src.data.settings_manager import get_section
        try:
            en = get_section("enablement", {}) or {}
        except Exception:  # noqa: BLE001
            en = {}
        demo = bool(en.get("demo_mode", True))
        if demo != self._boot_demo:
            still = ("Background sync started in live mode is STILL RUNNING "
                     "until you restart."
                     if not self._boot_demo else
                     "Pages and background workers stay in demo until you "
                     "restart.")
            self._mode_status.setText(
                f"Mode change saved — restart the app to apply it. {still}")
        elif demo:
            self._mode_status.setText(
                "Demo mode — a practice sandbox. Asana sync, Guru "
                "publishing, KB indexing and briefs are simulated, and "
                "Workbench/calendar work lives in a temporary practice "
                "database that is cleared at every launch. Renn chat still "
                "uses the live AI service.")
        else:
            self._mode_status.setText(
                "Live mode — connected integrations act for real.")
        self._mode_toggle_btn.setText(
            "Go live…" if demo else "Return to demo mode")

    def _on_mode_toggle(self):
        from PySide6.QtWidgets import QMessageBox
        from src.data.settings_manager import get_section, set_section
        demo = bool((get_section("enablement", {}) or {}).get(
            "demo_mode", True))
        if demo:
            resp = QMessageBox.question(
                self, "Leave demo mode?",
                "Live mode lets connected integrations act for real:\n"
                "Guru publishes reach your Guru workspace, Asana updates "
                "real tasks, and the KB indexes your Drive.\n\n"
                "The switch only fully applies after you RESTART the app — "
                "until then, pages and Renn keep parts of demo mode.\n"
                "You can return to demo mode here at any time.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if resp != QMessageBox.Yes:
                return
        # Re-read AFTER the modal: its nested event loop keeps main-thread
        # slots firing (e.g. the chat action poll writes enablement subkeys
        # via whole-section set_section) — writing a pre-dialog snapshot
        # back would silently revert those.
        cfg = dict(get_section("enablement", {}) or {})
        cfg["demo_mode"] = not demo
        if not set_section("enablement", cfg):
            QMessageBox.warning(
                self, "Couldn't save",
                "The mode change could not be written to settings — the "
                "app is still in its previous mode. Check that the data "
                "folder is writable, then try again.")
            return
        self._refresh_mode_card()
        self.refresh_kb_status()
        if not demo:
            # live→demo is the panic direction and has no confirm, so the
            # restart truth has to land right here, not in a status line.
            QMessageBox.information(
                self, "Restart needed",
                "Demo mode is saved but only fully applies after a "
                "restart. Background sync started in live mode is still "
                "running until then.")

    # ── knowledge base (WS2-M7: status + bootstrap + degraded-state) ──
    def _knowledge_base(self) -> QFrame:
```

### 3c — restart-pending bullet in the KB status readout

**FIND (4 lines, inside `refresh_kb_status()`):**

```python
        lines = []
        if en.get("demo_mode", True):
            lines.append("• Demo mode is ON — no live monitor (Asana sync, "
                         "briefs, KB) runs until it's disabled.")
```

**REPLACE WITH (9 lines):**

```python
        lines = []
        demo_now = bool(en.get("demo_mode", True))
        if demo_now != getattr(self, "_boot_demo", demo_now):
            lines.append("• Mode change pending — restart to apply. "
                         "Background monitors still reflect the previous "
                         "mode.")
        if demo_now:
            lines.append("• Demo mode is ON — no live monitor (Asana sync, "
                         "briefs, KB) runs until it's disabled.")
```

**Verify (whole file):** `_mode_card` appears twice (definition + the
Connections-tab call), `_refresh_mode_card` three times (definition + two
calls), `_on_mode_toggle` twice (definition + the `clicked.connect`),
`_boot_demo` four times.

---

## Edit 4 — `installer/README_IT_SECURITY.md` (audit-doc accuracy)

Two replacements.

### 4a — runner table

**FIND (1 line, in the §3.1 table):**

```markdown
| `build-macos` | `macos-14` (GitHub-hosted, Apple Silicon) | `AlmaInsights-macOS-arm64.zip` |
```

**REPLACE WITH (2 lines):**

```markdown
| `build-macos` (arm64) | `macos-15` (GitHub-hosted, Apple Silicon) | `AlmaInsights-macOS-arm64.zip` |
| `build-macos` (x64) | `macos-15-intel` (GitHub-hosted, Intel) | `AlmaInsights-macOS-x64.zip` |
```

### 4b — cleanup description

**FIND (1 line, in the §3.2 build-steps list):**

```markdown
   - Removes build artifacts (`__pycache__`, `.dist-info`, test dirs, pip itself)
```

**REPLACE WITH (4 lines):**

```markdown
   - Removes build artifacts (`__pycache__`, test dirs, pip itself).
     Package `.dist-info` metadata is retained: `importlib.metadata`
     entry points live there, and the `keyring` credential backends are
     discovered through them at runtime.
```

---

## Edit 5 — create three new test files

Create each file with exactly the contents shown. In the GitHub web UI:
repo root → Add file → Create new file → type the path → paste → commit.

### 5a — `tests/test_build_release_cleanup.py`

```python
"""
Tests for installer/build_release.py bundle cleanup.

Regression guard for the keyring/NoKeyringError defect: keyring 25.x
discovers ALL of its backends — including the built-in macOS Keychain and
Windows Credential Manager — via importlib.metadata entry points, which
live in each package's *.dist-info directory. A cleanup pass that strips
dist-info leaves every bundle with only keyring.backends.fail and a
critical "Keyring unavailable" splash failure on end-user machines.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_release():
    spec = importlib.util.spec_from_file_location(
        "build_release", ROOT / "installer" / "build_release.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_python_dir_preserves_dist_info(tmp_path):
    br = _load_build_release()

    site_packages = tmp_path / "Lib" / "site-packages"
    dist_info = site_packages / "keyring-25.5.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "entry_points.txt").write_text(
        "[keyring.backends]\nmacOS = keyring.backends.macOS\n",
        encoding="utf-8",
    )
    (site_packages / "keyring" / "backends").mkdir(parents=True)

    br.clean_python_dir(tmp_path)

    assert dist_info.is_dir(), (
        "dist-info must survive cleanup — keyring resolves its backends "
        "from entry points stored there"
    )
    assert (dist_info / "entry_points.txt").exists()


def test_clean_python_dir_still_slims_caches(tmp_path):
    br = _load_build_release()

    site_packages = tmp_path / "Lib" / "site-packages"
    pkg = site_packages / "keyring"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "junk.pyc").write_bytes(b"\x00")
    pip_dir = site_packages / "pip"
    pip_dir.mkdir()

    br.clean_python_dir(tmp_path)

    assert not (pkg / "__pycache__").exists()
    assert not (pkg / "junk.pyc").exists()
    assert not pip_dir.exists()
```

### 5b — `tests/test_pat_store_backend.py`

```python
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
```

### 5c — `tests/test_demo_mode_toggle.py`

```python
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
```

---

## Post-port verification

1. **If a local tree + Python env is available** (the work Mac), run the new
   tests plus neighbors in one group, offscreen:

   ```
   QT_QPA_PLATFORM=offscreen python -m pytest tests/test_build_release_cleanup.py tests/test_pat_store_backend.py tests/test_demo_mode_toggle.py -q
   ```

   Expected: **16 passed**. (Do not run the full `tests/` directory.)

2. **Trigger CI:** Actions → Build Bundles → Run workflow → the CI branch.
   Expected: all three legs green, three artifacts attached
   (`win64`, `macOS-arm64`, `macOS-x64`).

3. **End-user acceptance (Emily):** reinstall from the fresh arm64 artifact
   (download → CLI `unzip` → `bash Install*`). The startup splash's
   "Checking credential store" must be **green on a plain double-click
   launch** — no `export PYTHON_KEYRING_BACKEND` ritual. The OPERATING MODE
   card must be visible at the top of enablement Settings → Connections.
   If the splash still blocks, capture the FULL text of every red item —
   the second critical failure from 2026-07-23 was never read.

## Appendix A — Corrected delivery path (file-copy method, 2026-07-24)

Status: an earlier execution applied all files correctly but to
`~/Downloads/AlmaInsights/` — a scratch folder, **not** a repo checkout
(only 8 of the tree's files existed there). Nothing has reached the
CoveHealth repository yet. The file-copy method below replaces Edits 1–5;
the source zip already contains every edit pre-applied, so no in-place
editing is needed — but the copies must land in the REAL clone.

A1. Locate the actual CoveHealth clone: open **GitHub Desktop**, select
    the CoveHealth alma-insights repository, menu **Repository → Show in
    Finder**. Call that folder CLONE. Verify it is real: it must contain
    a full tree (~1,000 files) including `installer/build_release.py`,
    `src/data/pat_store.py`, `src/ui/pages/enablement/settings.py`, and
    a `.git` directory (hidden). If any of those are absent, STOP — the
    wrong folder is selected.

A2. In GitHub Desktop, confirm the current branch is the one CI builds
    from (the branch the 2026-07-23/24 fixes were made on), and press
    **Fetch origin / Pull** so the clone is current.

A3. From the extracted source zip
    (`/tmp/enablement_v4/alma-insights-enablement-content-tabs/`), copy
    these 7 files into CLONE at the SAME relative paths, overwriting
    where the file exists:

    - `installer/build_release.py`
    - `installer/README_IT_SECURITY.md`
    - `src/data/pat_store.py`
    - `src/ui/pages/enablement/settings.py`
    - `tests/test_build_release_cleanup.py`   (new)
    - `tests/test_pat_store_backend.py`       (new)
    - `tests/test_demo_mode_toggle.py`        (new)

    Do NOT copy anything else from the zip — in particular not the
    `.github/workflows/` files (the CoveHealth copies are ahead) and not
    `main.py` or any other file.

A4. GitHub Desktop → **Changes** tab. Expect EXACTLY 7 files listed:
    4 modified + 3 new. Review each modified file's diff against these
    expectations:

    - `build_release.py` — 2 hunks: the dist-info removal block replaced
      by the KEPT comment; a short comment above `arch_label = "x64"`.
    - `pat_store.py` — 2 hunks: `import sys`; the
      `_ensure_keyring_backend` block + call.
    - `settings.py` — 3 hunks: the Connections-tab call; the
      operating-mode methods block; the `refresh_kb_status` bullet.
    - `README_IT_SECURITY.md` — 2 hunks: runner table; cleanup bullet.

    If ANY other file appears in Changes, or any diff shows hunks beyond
    the above, STOP and report — do not commit.

A5. Commit with summary
    `port: pilot fixes — keyring dist-info+self-heal, OPERATING MODE card, tests`
    and **Push origin**.

A6. Continue at "Post-port verification" above.

## Known-and-accepted limitations shipping with this port

- A mode flip is only fully real after an app **restart**: live→demo does
  not stop an already-running background monitor, and demo→live does not
  rebuild pages or the chat database mid-session. The UI states this at
  every step (pending card state, KB-readout bullet, dialogs). Mechanical
  teardown/rebuild on flip is a tracked follow-up.
- Demo mode's practice database is wiped at every launch, and Renn chat
  reaches the live AI service even in demo — both now disclosed in the
  card copy; both by design.
