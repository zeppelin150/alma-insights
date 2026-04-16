"""
Alma Insights — Release Manifest

A release manifest is a small JSON file shipped alongside the release
zip on GitHub Releases. It pins a SHA-256 for each platform artifact
plus optional metadata. The updater reads it BEFORE downloading the
zip so it can refuse any artifact whose checksum doesn't match.

Schema:
    {
      "schema_version": 1,
      "version": "v9.3.0",
      "released_at": "2026-04-15T20:45:00+00:00",
      "artifacts": {
        "AlmaInsights-win64.zip":     { "sha256": "…", "size": 1234567 },
        "AlmaInsights-macOS-arm64.zip":{ "sha256": "…", "size": 1234567 },
        "AlmaInsights-macOS-x64.zip":  { "sha256": "…", "size": 1234567 }
      },
      "notes_url": "https://github.com/…/releases/tag/v9.3.0"
    }

Public API:
    build_manifest(version, artifacts_dir)  -> dict
    write_manifest(manifest, dest)          -> Path
    load_manifest(path)                     -> dict
    sha256_of(path)                         -> str (hex)
    lookup_artifact(manifest, name)         -> (url|None, sha|None, size|None)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_SCHEMA_VERSION = 1
_DEFAULT_ARTIFACT_GLOBS = ("*.zip", "*.dmg", "*.exe", "*.pkg")


# ──────────────────────────────────────────────────────────────────
# Building
# ──────────────────────────────────────────────────────────────────

def build_manifest(
    version: str,
    artifacts_dir: Path,
    *,
    globs: Iterable[str] = _DEFAULT_ARTIFACT_GLOBS,
    notes_url: str = "",
) -> dict:
    """Scan artifacts_dir for release binaries and compute their hashes."""
    artifacts_dir = Path(artifacts_dir)
    if not artifacts_dir.is_dir():
        raise FileNotFoundError(f"Artifacts directory not found: {artifacts_dir}")

    entries: dict[str, dict] = {}
    for path in _iter_artifact_files(artifacts_dir, globs):
        entries[path.name] = {
            "sha256": sha256_of(path),
            "size": path.stat().st_size,
        }

    return {
        "schema_version": _SCHEMA_VERSION,
        "version": version,
        "released_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": entries,
        "notes_url": notes_url,
    }


def _iter_artifact_files(root: Path, globs: Iterable[str]):
    """Yield files matching any of the configured globs, sorted by name."""
    found: set[Path] = set()
    for pattern in globs:
        for path in root.glob(pattern):
            if path.is_file():
                found.add(path)
    yield from sorted(found)


# ──────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────

def write_manifest(manifest: dict, dest: Path) -> Path:
    """Write the manifest as pretty-printed JSON."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest


def load_manifest(path: Path) -> dict:
    """Read a manifest file. Raises FileNotFoundError / JSONDecodeError on failure."""
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────
# Consumers (used by updater.py once it integrates the manifest)
# ──────────────────────────────────────────────────────────────────

def lookup_artifact(manifest: dict, name: str) -> tuple[str | None, int | None]:
    """Return (sha256, size) for artifact `name`, or (None, None) if absent."""
    entry = manifest.get("artifacts", {}).get(name)
    if not entry:
        return None, None
    return entry.get("sha256"), entry.get("size")


# ──────────────────────────────────────────────────────────────────
# Hash helper
# ──────────────────────────────────────────────────────────────────

def sha256_of(path: Path) -> str:
    """Hex-encoded SHA-256 of a file, reading in 64 KB chunks."""
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
