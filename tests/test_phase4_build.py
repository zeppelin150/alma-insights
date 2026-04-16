"""
Unit tests for Phase-4 build hardening in installer/build_release.py

Covers:
  - PACKAGES list uses exact (==) pins, not >=
  - GEMINI_CLI_PACKAGE pins to a specific version
  - write_sbom emits the expected schema + pip-list payload
  - write_checksums computes real SHA-256s and skips __pycache__ / data/
  - The module's PACKAGES stays in sync with requirements.txt

Does NOT actually run the bundle build — that's CI-only.

Run: python -m pytest tests/test_phase4_build.py -x -v
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────────────────────────
# Importer — build_release.py isn't in src/, load it by path
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def build_release():
    spec = importlib.util.spec_from_file_location(
        "build_release",
        _REPO_ROOT / "installer" / "build_release.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ──────────────────────────────────────────────────────────────────
# Pinning policy
# ──────────────────────────────────────────────────────────────────

class TestExactPinning:
    def test_all_packages_use_exact_pin(self, build_release):
        for pkg in build_release.PACKAGES:
            assert "==" in pkg, f"Not exactly pinned: {pkg}"
            assert ">=" not in pkg, f"Still floating: {pkg}"

    def test_gemini_cli_pinned(self, build_release):
        assert "@" in build_release.GEMINI_CLI_PACKAGE.split("/")[-1], (
            f"Gemini CLI not version-pinned: {build_release.GEMINI_CLI_PACKAGE}"
        )

    def test_packages_match_requirements_txt(self, build_release):
        """The PACKAGES list must mirror requirements.txt exactly."""
        req_text = (_REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        req_pkgs = {}
        for line in req_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "==" not in line:
                continue
            name, _, version = line.partition("==")
            req_pkgs[name.strip().lower()] = version.strip()

        for spec in build_release.PACKAGES:
            name, _, version = spec.partition("==")
            key = name.strip().lower()
            assert key in req_pkgs, f"{name} in PACKAGES but not requirements.txt"
            assert req_pkgs[key] == version.strip(), (
                f"Version mismatch for {name}: "
                f"requirements.txt={req_pkgs[key]} PACKAGES={version}"
            )


# ──────────────────────────────────────────────────────────────────
# write_sbom
# ──────────────────────────────────────────────────────────────────

class TestSbom:
    def test_writes_valid_json(self, build_release, tmp_path):
        staging = tmp_path / "staging"
        (staging / "app").mkdir(parents=True)
        fake_exe = tmp_path / "python.exe"
        fake_exe.write_text("")  # just needs to exist

        fake_result = MagicMock(returncode=0, stdout=json.dumps([
            {"name": "pandas", "version": "2.2.3"},
            {"name": "numpy", "version": "1.26.4"},
        ]), stderr="")

        with patch.object(build_release, "run_cmd", return_value=fake_result):
            build_release.write_sbom(fake_exe, staging)

        sbom = json.loads((staging / "app" / "sbom.json").read_text())
        assert sbom["schema_version"] == 1
        assert sbom["app"] == build_release.APP_NAME
        assert sbom["python_version"] == build_release.PY_VERSION
        assert len(sbom["packages"]) == 2
        names = [p["name"] for p in sbom["packages"]]
        assert "pandas" in names and "numpy" in names

    def test_pip_failure_aborts_build(self, build_release, tmp_path):
        staging = tmp_path / "staging"
        (staging / "app").mkdir(parents=True)
        fake_exe = tmp_path / "python.exe"
        fake_exe.write_text("")

        fake_result = MagicMock(returncode=1, stdout="", stderr="pip blew up")
        with patch.object(build_release, "run_cmd", return_value=fake_result):
            with pytest.raises(SystemExit) as exc_info:
                build_release.write_sbom(fake_exe, staging)
        assert exc_info.value.code == 1


# ──────────────────────────────────────────────────────────────────
# write_checksums
# ──────────────────────────────────────────────────────────────────

class TestChecksums:
    def _seed_app(self, staging: Path) -> dict[str, str]:
        """Create a fake staging/app tree and return the expected hashes."""
        app = staging / "app"
        app.mkdir(parents=True)

        files = {
            "main.py": b"print('alma')\n",
            "src/__init__.py": b"VERSION = '9.3.0'\n",
            "src/util.py": b"def f(): return 42\n",
        }
        expected = {}
        for rel, data in files.items():
            p = app / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            expected[rel] = hashlib.sha256(data).hexdigest()

        # These should be skipped
        (app / "src" / "__pycache__").mkdir(exist_ok=True)
        (app / "src" / "__pycache__" / "util.cpython-312.pyc").write_bytes(b"bytecode")
        (app / "data").mkdir(exist_ok=True)
        (app / "data" / "foo.py").write_bytes(b"runtime state, should be skipped")

        return expected

    def test_hashes_all_app_py_files(self, build_release, tmp_path):
        expected = self._seed_app(tmp_path / "staging")
        build_release.write_checksums(tmp_path / "staging")

        written = json.loads(
            (tmp_path / "staging" / "app" / "checksums.json").read_text()
        )
        assert written["schema_version"] == 1
        assert written["algorithm"] == "sha256"
        assert written["files"] == expected

    def test_skips_pycache_and_data(self, build_release, tmp_path):
        self._seed_app(tmp_path / "staging")
        build_release.write_checksums(tmp_path / "staging")
        written = json.loads(
            (tmp_path / "staging" / "app" / "checksums.json").read_text()
        )
        for path in written["files"]:
            assert "__pycache__" not in path
            assert not path.startswith("data/")
