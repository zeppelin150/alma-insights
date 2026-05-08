"""
End-to-end auto-update simulation (Piece 1 + 2A + 2B + 3, 2026-05-07)
=======================================================================

Drives the full phone-home → install → restart → apply chain against
an in-memory fake GitHub server. **No real network calls. No real
process spawn.** Exercises:

    UpdateChecker._do_check (background thread, real urllib stack)
        ↓ assets stashed on checker.last_assets
    manifest_fetcher.resolve_release_artifact (real urllib)
        ↓ resolves URL + SHA-256
    Updater.stage (real download + SHA verify + zip extract)
        ↓ writes _update_staging/ + update_meta.json
    restart.restart_app (mocked Popen)
        ↓ would have spawned a child + quit
    updater.apply_staged_update (real file moves)
        ↓ swaps src/, rewrites VERSION, records rollback
    rollback.record_apply
        ↓ writes data/rollback_state.json

Each leg has its own narrow unit tests (test_manifest_fetcher.py,
test_update_action_widget.py, test_restart_helper.py, test_updater.py).
This test exists to catch interface drift between them — the kind of
bug a unit test can't see because each layer's mock matches that
layer's contract but the contracts disagree across layers.

Run: ``python -m pytest tests/test_update_e2e_simulation.py -x -v``
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── In-memory fake GitHub Releases API ────────────────────────────


class _FakeGitHub:
    """Stub urlopen routing for a synthetic GitHub release.

    Three URLs are served:
        * The /releases/latest JSON.
        * The manifest.json asset.
        * The platform-zip asset (a real zip of fake src/ contents).

    Anything else raises HTTPError(404).
    """

    def __init__(self, *, version: str, src_dir_contents: dict[str, str]):
        self.version = version
        # Build the fake release zip in memory.
        self.zip_bytes = self._build_zip(version, src_dir_contents)
        self.zip_sha256 = hashlib.sha256(self.zip_bytes).hexdigest()
        self.calls: list[str] = []

    # ── canonical URLs ───────────────────────────────────────

    @property
    def releases_url(self) -> str:
        return ("https://api.github.com/repos/alma-health/"
                "alma-insights/releases/latest")

    @property
    def manifest_url(self) -> str:
        return ("https://example.com/releases/download/"
                f"v{self.version}/manifest.json")

    @property
    def zip_url(self) -> str:
        return ("https://example.com/releases/download/"
                f"v{self.version}/AlmaInsights-win64.zip")

    # ── payload builders ─────────────────────────────────────

    def releases_json(self) -> bytes:
        return json.dumps({
            "tag_name": f"v{self.version}",
            "html_url": f"https://github.com/.../releases/tag/v{self.version}",
            "assets": [
                {"name": "AlmaInsights-win64.zip",
                 "browser_download_url": self.zip_url},
                {"name": "manifest.json",
                 "browser_download_url": self.manifest_url},
            ],
        }).encode("utf-8")

    def manifest_json(self) -> bytes:
        return json.dumps({
            "schema_version": 1,
            "version": f"v{self.version}",
            "released_at": "2026-05-07T12:00:00+00:00",
            "artifacts": {
                "AlmaInsights-win64.zip": {
                    "sha256": self.zip_sha256,
                    "size": len(self.zip_bytes),
                },
            },
        }).encode("utf-8")

    # ── fake transport ───────────────────────────────────────

    def urlopen(self, req, *_a, **_kw):
        url = req.full_url if hasattr(req, "full_url") else req
        self.calls.append(url)
        if url == self.releases_url:
            return _resp(self.releases_json())
        if url == self.manifest_url:
            return _resp(self.manifest_json())
        if url == self.zip_url:
            return _resp(self.zip_bytes, content_length=len(self.zip_bytes))
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _build_zip(version: str, contents: dict[str, str]) -> bytes:
        """Build a zip whose top-level dir is `repo-name-vX.Y.Z/` to
        match GitHub's release zip layout."""
        buf = io.BytesIO()
        prefix = f"alma-insights-v{version}/"
        with zipfile.ZipFile(buf, "w") as zf:
            for path, body in contents.items():
                zf.writestr(prefix + path, body)
        return buf.getvalue()


class _Resp:
    """Minimal context-manager wrapper that mimics urlopen's return."""

    def __init__(self, body: bytes, content_length: int | None = None):
        self._body = body
        self._cl = content_length
        self.headers = {"Content-Length": str(content_length)} if content_length else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n: int = -1) -> bytes:
        if n in (None, -1):
            data, self._body = self._body, b""
            return data
        data = self._body[:n]
        self._body = self._body[n:]
        return data


def _resp(body: bytes, content_length: int | None = None) -> _Resp:
    return _Resp(body, content_length)


# ── Project-staging fixture ───────────────────────────────────────


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    """Stand up a fake project root with a current src/ + config/ +
    migrations/ tree, plus an empty data/ for rollback state.

    Re-points the updater + rollback modules at it so the real
    apply/rollback logic operates on temp files instead of the live
    repo.
    """
    root = tmp_path / "alma-insights"
    (root / "src").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "migrations").mkdir()
    (root / "data" / "crash_reports").mkdir(parents=True)

    # Current source files we expect apply_staged_update to back up.
    (root / "src" / "__init__.py").write_text(
        '# Alma Insights\nVERSION = "9.0.0"\n', encoding="utf-8",
    )
    (root / "src" / "marker.py").write_text(
        "# original\nMARKER = 'old'\n", encoding="utf-8",
    )
    (root / "config" / "settings.yaml").write_text("noop: true\n")
    (root / "migrations" / "001_initial.sql").write_text("-- noop\n")

    from src.updater import updater as updater_mod
    from src.updater import rollback as rollback_mod
    monkeypatch.setattr(updater_mod, "_PROJECT_ROOT", root)
    monkeypatch.setattr(updater_mod, "_STAGING_DIR", root / "_update_staging")
    monkeypatch.setattr(updater_mod, "_STAGING_META",
                         root / "_update_staging" / "update_meta.json")
    monkeypatch.setattr(rollback_mod, "_PROJECT_ROOT", root)
    monkeypatch.setattr(rollback_mod, "_ROLLBACK_STATE",
                         root / "data" / "rollback_state.json")
    monkeypatch.setattr(rollback_mod, "_CRASH_DIR",
                         root / "data" / "crash_reports")

    return root


# ── The simulation ────────────────────────────────────────────────


class TestEndToEndUpdateChain:
    def test_full_phone_home_to_apply_chain(self, project_root, monkeypatch):
        # ── Set up the fake server ──
        new_version = "9.0.1"
        github = _FakeGitHub(
            version=new_version,
            src_dir_contents={
                "src/__init__.py": '# upgraded\nVERSION = "9.0.1"\n',
                "src/marker.py": "# UPGRADED\nMARKER = 'new'\n",
                "config/settings.yaml": "upgraded: true\n",
                "migrations/001_initial.sql": "-- noop (upgraded)\n",
                # Anything outside the replaceable dirs must NOT land
                # on disk after extract — testing that boundary.
                "tests/should_be_filtered.py": "# this should NOT extract\n",
                "data/should_be_filtered.db": "# this should NOT extract\n",
            },
        )

        # Route every urlopen call to our fake.
        monkeypatch.setattr("urllib.request.urlopen", github.urlopen)
        # Pin the local VERSION the checker compares against.
        from src.updater import update_checker as uc
        from src.updater import updater as updater_mod
        monkeypatch.setattr(uc, "VERSION", "9.0.0")
        monkeypatch.setattr(updater_mod, "VERSION", "9.0.0")

        # ── 1. Phone home: build a checker, run it, wait for assets ──
        from src.updater.update_checker import UpdateChecker
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance() or QApplication([])

        checker = UpdateChecker(
            releases_url=github.releases_url,
            github_pat="bearer-token",
            auth_mode="pat",
        )
        signals: list[tuple] = []
        checker.update_available.connect(
            lambda c, n, u: signals.append(("avail", c, n, u)),
        )
        checker.up_to_date.connect(lambda: signals.append(("up_to_date",)))
        checker.check_failed.connect(lambda m: signals.append(("failed", m)))

        checker.check()
        # The check runs in a daemon thread; the slot fires on the main
        # thread via a queued connection, so we have to process Qt
        # events for the signal to land. 5s budget covers a slow CI box.
        deadline = time.time() + 5
        while not signals and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert signals, "checker never emitted a signal"
        assert signals[0][0] == "avail", \
            f"expected 'avail', got {signals[0]}"
        assert signals[0][2] == new_version
        assert checker.last_assets is not None
        assert len(checker.last_assets) == 2  # zip + manifest
        assert checker.last_token == "bearer-token"

        # ── 2. Manifest fetch resolves URL + SHA ──
        from src.updater.manifest_fetcher import resolve_release_artifact
        url, sha, size = resolve_release_artifact(
            checker.last_assets, token=checker.last_token,
            artifact_name="AlmaInsights-win64.zip",
        )
        assert url == github.zip_url
        assert sha == github.zip_sha256
        assert size == len(github.zip_bytes)

        # ── 3. Updater.stage downloads + verifies + extracts ──
        from src.updater.updater import Updater
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])

        updater = Updater()
        outcomes: dict[str, object] = {}
        updater.complete.connect(lambda: outcomes.setdefault("complete", True))
        updater.failed.connect(lambda m: outcomes.setdefault("failed", m))

        updater.stage(url, expected_sha256=sha, new_version=new_version)
        # Same queued-connection pattern as step 1: pump events while
        # the staging thread runs.
        deadline = time.time() + 5
        while not outcomes and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert outcomes, "stage never finished"
        assert outcomes.get("complete") is True, \
            f"stage failed: {outcomes.get('failed')}"

        # Staging metadata + zipped contents are on disk
        meta_path = project_root / "_update_staging" / "update_meta.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["version"] == new_version
        assert meta["sha256"] == sha
        assert meta["previous_version"] == "9.0.0"

        # Replaceable dirs were extracted
        staged_src = project_root / "_update_staging" / "src"
        assert staged_src.is_dir()
        assert (staged_src / "marker.py").read_text(encoding="utf-8") == \
            "# UPGRADED\nMARKER = 'new'\n"
        # Excluded dirs were NOT extracted
        assert not (project_root / "_update_staging" / "tests").exists()
        assert not (project_root / "_update_staging" / "data").exists()

        # ── 4. Restart helper would spawn a child + quit ──
        from src.updater import restart
        with patch("subprocess.Popen") as fake_popen, \
             patch("PySide6.QtWidgets.QApplication.instance",
                   return_value=None):
            restart.restart_app()
        fake_popen.assert_called_once()

        # ── 5. apply_staged_update swaps in the new code ──
        from src.updater.updater import apply_staged_update
        applied = apply_staged_update()
        assert applied is True
        # Marker swapped to the new version
        assert (project_root / "src" / "marker.py").read_text(
            encoding="utf-8",
        ) == "# UPGRADED\nMARKER = 'new'\n"
        # VERSION rewritten in src/__init__.py
        init_text = (project_root / "src" / "__init__.py").read_text(
            encoding="utf-8",
        )
        assert f'VERSION = "{new_version}"' in init_text
        # _src_previous (60s rollback lifeline) exists
        assert (project_root / "_src_previous").is_dir()
        assert (project_root / "_src_previous" / "marker.py").read_text(
            encoding="utf-8",
        ) == "# original\nMARKER = 'old'\n"
        # _update_staging is cleaned up
        assert not (project_root / "_update_staging").exists()

        # ── 6. Rollback state is recorded for the next launch ──
        rb_state = json.loads(
            (project_root / "data" / "rollback_state.json").read_text(
                encoding="utf-8",
            ),
        )
        assert rb_state["previous"] == "9.0.0"
        assert rb_state["new"] == new_version
        assert "applied_at" in rb_state

    def test_checksum_mismatch_aborts_install(self, project_root, monkeypatch):
        """If the manifest's SHA-256 doesn't match the downloaded zip,
        the updater must refuse to extract and emit `failed`. The
        whole staging tree must not exist afterward."""
        github = _FakeGitHub(
            version="9.0.1",
            src_dir_contents={
                "src/__init__.py": 'VERSION = "9.0.1"\n',
                "src/marker.py": "MARKER = 'new'\n",
            },
        )
        monkeypatch.setattr("urllib.request.urlopen", github.urlopen)

        from src.updater.updater import Updater
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance() or QApplication([])

        updater = Updater()
        outcomes: dict[str, object] = {}
        updater.complete.connect(lambda: outcomes.setdefault("complete", True))
        updater.failed.connect(lambda m: outcomes.setdefault("failed", m))

        wrong_sha = "0" * 64
        updater.stage(
            github.zip_url, expected_sha256=wrong_sha, new_version="9.0.1",
        )
        deadline = time.time() + 5
        while not outcomes and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert outcomes
        assert "complete" not in outcomes
        assert "Checksum mismatch" in str(outcomes["failed"])
        # No staging dir left on disk
        assert not (project_root / "_update_staging").exists()
