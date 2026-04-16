"""
Alma Insights — Updater (Phase 2)

Downloads a release zip from GitHub, verifies its SHA-256 checksum,
and stages it for application on next launch.

Windows caveat:  Running .py / .pyd files cannot be replaced while the
interpreter holds them open.  We therefore use a *stage-and-apply*
pattern:
  1.  Download + verify → ``_update_staging/``
  2.  On next launch ``apply_staged_update()`` swaps ``src/`` and ``config/``

Signals:
    progress(pct: int, msg: str)   — 0-100 percent + human-readable step
    complete()                      — staged successfully, restart needed
    failed(msg: str)                — something went wrong
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from threading import Thread

from PySide6.QtCore import QObject, Signal

from src import VERSION

logger = logging.getLogger("alma.updater")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STAGING_DIR = _PROJECT_ROOT / "_update_staging"
_STAGING_META = _STAGING_DIR / "update_meta.json"

# Directories replaced by an update (code + schema; data/ is preserved)
_REPLACEABLE_DIRS = ("src", "config", "migrations")

# Files/dirs that must NEVER be deleted during an update
_PROTECTED = {"data", "_update_staging", ".git", ".venv", "venv",
              "node_modules", "scan_server", "assets", "debug", "ocr_debug",
              "docs", "installer", "tests"}


def has_staged_update() -> bool:
    """Return True if a staged update is waiting to be applied."""
    return _STAGING_META.is_file()


def apply_staged_update() -> bool:
    """Apply a previously staged update.  Call this early in main.py,
    BEFORE importing src modules (so file locks are not held).

    Returns True if an update was applied, False otherwise.
    """
    if not has_staged_update():
        return False

    try:
        meta = json.loads(_STAGING_META.read_text(encoding="utf-8"))
        new_version = meta.get("version", "unknown")
        previous_version = meta.get("previous_version", VERSION)
        logger.info("Applying staged update to v%s", new_version)

        for dirname in _REPLACEABLE_DIRS:
            staged = _STAGING_DIR / dirname
            live = _PROJECT_ROOT / dirname
            backup = _PROJECT_ROOT / f"_{dirname}_backup"

            if not staged.is_dir():
                continue

            # 1. Backup current
            if live.is_dir():
                if backup.is_dir():
                    shutil.rmtree(backup)
                live.rename(backup)

            # 2. Move staged into place
            staged.rename(live)

            # Note: backups are NOT deleted here. src/updater/rollback.py
            # moves _*_backup -> _*_previous below so we can revert if
            # the new version crash-loops within the grace window.

        # Update version in src/__init__.py if present
        init_py = _PROJECT_ROOT / "src" / "__init__.py"
        if init_py.is_file():
            text = init_py.read_text(encoding="utf-8")
            if "VERSION" in text:
                import re
                text = re.sub(
                    r'VERSION\s*=\s*"[^"]*"',
                    f'VERSION = "{new_version}"',
                    text,
                )
                init_py.write_text(text, encoding="utf-8")

        # Record rollback state + preserve previous-version backups.
        from src.updater.rollback import record_apply
        record_apply(previous_version=previous_version, new_version=new_version)

        # Clean up staging
        shutil.rmtree(_STAGING_DIR, ignore_errors=True)
        logger.info("Update to v%s applied successfully", new_version)
        return True

    except Exception as exc:
        logger.exception("Failed to apply staged update: %s", exc)
        # Attempt rollback
        _rollback()
        return False


def _rollback():
    """Restore backups if something went wrong during apply."""
    for dirname in _REPLACEABLE_DIRS:
        backup = _PROJECT_ROOT / f"_{dirname}_backup"
        live = _PROJECT_ROOT / dirname
        if backup.is_dir() and not live.is_dir():
            backup.rename(live)
            logger.info("Rolled back %s from backup", dirname)


class Updater(QObject):
    """Downloads and stages an update for application on next launch."""

    progress = Signal(int, str)   # (percent, step_description)
    complete = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel_requested = False

    # ── public ──────────────────────────────────────────────

    def stage(self, download_url: str, expected_sha256: str = "",
              new_version: str = "", *, require_checksum: bool = True) -> None:
        """Download and stage an update (runs in background thread).

        Parameters
        ----------
        download_url
            HTTPS URL of the release zip.
        expected_sha256
            Hex-encoded SHA-256 of the zip. Required in normal use —
            Phase 3 treats updates with no checksum as untrusted.
        new_version
            Target version tag (written to update_meta.json).
        require_checksum
            Default True. Set to False only for development tooling
            that knowingly bypasses verification.
        """
        self._cancel_requested = False

        if require_checksum and not (expected_sha256 or "").strip():
            # Fire an immediate failure on the main thread — don't open
            # the socket, don't touch the filesystem.
            self.failed.emit(
                "Update refused — no SHA-256 checksum supplied. "
                "This protects against corrupted or tampered downloads."
            )
            return

        t = Thread(
            target=self._do_stage,
            args=(download_url, expected_sha256, new_version, require_checksum),
            daemon=True,
        )
        t.start()

    def cancel(self) -> None:
        """Request cancellation of an in-progress download."""
        self._cancel_requested = True

    # ── internal ────────────────────────────────────────────

    def _do_stage(self, download_url: str, expected_sha256: str,
                  new_version: str, require_checksum: bool = True):
        tmp_path = None
        try:
            # Step 1: Download to temp file
            self.progress.emit(5, "Downloading update…")
            tmp_path = self._download(download_url)
            if self._cancel_requested:
                return

            # Step 2: Verify checksum — mandatory in normal operation.
            if expected_sha256:
                self.progress.emit(50, "Verifying checksum…")
                actual = self._sha256(tmp_path)
                if actual != expected_sha256.lower():
                    self.failed.emit(
                        f"Checksum mismatch: expected {expected_sha256[:12]}… "
                        f"got {actual[:12]}… — refusing to stage untrusted update."
                    )
                    return
            elif require_checksum:
                # Defence in depth — caller already checked, but be explicit
                self.failed.emit("No checksum supplied — refusing to stage.")
                return
            else:
                self.progress.emit(50, "Dev mode — skipping checksum verification")

            if self._cancel_requested:
                return

            # Step 3: Extract to staging
            self.progress.emit(60, "Extracting update…")
            self._extract_to_staging(tmp_path, new_version)
            if self._cancel_requested:
                return

            # Step 4: Write metadata
            self.progress.emit(90, "Finalizing…")
            meta = {
                "version": new_version,
                "previous_version": VERSION,
                "download_url": download_url,
                "sha256": expected_sha256 or "not_verified",
            }
            _STAGING_META.write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )

            self.progress.emit(100, "Update staged — restart to apply")
            logger.info("Update v%s staged successfully", new_version)
            self.complete.emit()

        except Exception as exc:
            msg = f"Update failed: {exc}"
            logger.exception(msg)
            self.failed.emit(msg)

        finally:
            # Clean up temp file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _download(self, url: str) -> str:
        """Download url to a temp file, returning the path."""
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"AlmaInsights/{VERSION}"},
        )
        suffix = ".zip" if url.endswith(".zip") else ""
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="alma_update_")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with os.fdopen(fd, "wb") as f:
                    while True:
                        if self._cancel_requested:
                            return tmp_path
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(45, int(downloaded / total * 45) + 5)
                            self.progress.emit(pct, f"Downloading… {downloaded // 1024}KB")
        except Exception:
            os.close(fd) if not os.get_inheritable(fd) else None
            raise
        return tmp_path

    @staticmethod
    def _sha256(path: str) -> str:
        """Compute SHA-256 hex digest of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _extract_to_staging(self, zip_path: str, version: str):
        """Extract the zip into _update_staging/, keeping only replaceable dirs."""
        if _STAGING_DIR.is_dir():
            shutil.rmtree(_STAGING_DIR)
        _STAGING_DIR.mkdir(parents=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find the root directory inside the zip (GitHub zips have one)
            names = zf.namelist()
            # Common pattern: "repo-name-v1.0.0/src/..."
            zip_root = ""
            if names and "/" in names[0]:
                zip_root = names[0].split("/", 1)[0] + "/"

            for member in zf.infolist():
                if self._cancel_requested:
                    return

                # Strip zip root prefix
                rel = member.filename
                if zip_root and rel.startswith(zip_root):
                    rel = rel[len(zip_root):]

                if not rel:
                    continue

                # Only extract replaceable directories
                top_dir = rel.split("/", 1)[0]
                if top_dir not in _REPLACEABLE_DIRS:
                    continue

                target = _STAGING_DIR / rel
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)

        logger.info("Extracted update to staging: %s", _STAGING_DIR)
