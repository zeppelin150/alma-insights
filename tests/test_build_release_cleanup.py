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
