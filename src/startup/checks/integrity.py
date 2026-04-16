"""
Optional startup check — verify app/checksums.json against disk.

The bundle builder writes `checksums.json` containing SHA-256 hashes
for every app/**/*.py file (see installer/build_release.py::
write_checksums). At startup we spot-check a random sample to detect
tampering without paying the full hash-every-file cost.

The check is non-critical by design:
  * Dev checkouts don't have checksums.json — returns pass with a note
  * A single mismatch surfaces as warn (potential corruption)
  * > 5 mismatches surfaces as fail (likely compromised install)

Sample size is capped so even a bundle with thousands of .py files
completes in under 100 ms on a typical laptop.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from src.startup.checker import CheckResult

_CHECKSUMS_FILE = Path("checksums.json")
_SAMPLE_SIZE = 25         # spot-check this many files per launch
_FAIL_THRESHOLD = 5       # mismatches above this are treated as critical


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def check_integrity() -> CheckResult:
    manifest = _load_manifest()
    if manifest is None:
        return CheckResult(
            id="integrity",
            name="Bundle integrity",
            status="pass",
            message="checksums.json not present (dev checkout)",
            critical=False,
        )

    files = manifest.get("files") or {}
    if not files:
        return CheckResult(
            id="integrity",
            name="Bundle integrity",
            status="warn",
            message="checksums.json has no file entries",
            remediation="Reinstall to restore integrity metadata.",
            critical=False,
        )

    sample = _sample_entries(files)
    mismatches = _verify_sample(sample)

    if not mismatches:
        return CheckResult(
            id="integrity",
            name="Bundle integrity",
            status="pass",
            message=f"Verified {len(sample)} of {len(files)} files",
            critical=False,
        )

    if len(mismatches) > _FAIL_THRESHOLD:
        return CheckResult(
            id="integrity",
            name="Bundle integrity",
            status="fail",
            message=f"{len(mismatches)} / {len(sample)} files mismatched",
            remediation="Reinstall Alma Insights — bundle may be tampered.",
            critical=True,
        )

    sample_names = ", ".join(mismatches[:3])
    return CheckResult(
        id="integrity",
        name="Bundle integrity",
        status="warn",
        message=f"{len(mismatches)} file(s) changed: {sample_names}",
        remediation="Reinstall Alma Insights to restore the bundled files.",
        critical=False,
    )


# ──────────────────────────────────────────────────────────────────
# Helpers (small, unit-testable)
# ──────────────────────────────────────────────────────────────────

def _load_manifest() -> dict | None:
    """Read checksums.json. Returns None if absent or unreadable."""
    try:
        if _CHECKSUMS_FILE.exists():
            return json.loads(_CHECKSUMS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _sample_entries(files: dict[str, str]) -> list[tuple[str, str]]:
    """Pick up to _SAMPLE_SIZE random (path, expected_sha) pairs."""
    items = list(files.items())
    if len(items) <= _SAMPLE_SIZE:
        return items
    return random.sample(items, _SAMPLE_SIZE)


def _verify_sample(sample: list[tuple[str, str]]) -> list[str]:
    """Return the names of entries whose on-disk hash does not match."""
    mismatches: list[str] = []
    for rel_path, expected in sample:
        actual = _sha256_of(Path(rel_path))
        if actual and actual != expected:
            mismatches.append(rel_path)
    return mismatches


def _sha256_of(path: Path) -> str | None:
    """Hex SHA-256 of a file, or None if the file is missing / unreadable."""
    if not path.is_file():
        return None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
