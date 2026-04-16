#!/usr/bin/env python3
"""
Alma Insights — Release Packager (Phase 5)

Packages ``src/`` and ``config/`` into a versioned zip with a
SHA256SUMS manifest.  The zip can be uploaded to a GitHub release
and consumed by the Updater (``src/updater/updater.py``).

Usage:
    python scripts/make_release.py                  # auto-detect version
    python scripts/make_release.py --version 2.1.0  # explicit version
    python scripts/make_release.py --output dist/    # custom output dir

Output:
    dist/alma-insights-v{VERSION}.zip
    dist/SHA256SUMS
"""

import argparse
import hashlib
import os
import sys
import zipfile
from pathlib import Path

# Resolve project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Directories included in the release zip
INCLUDE_DIRS = ("src", "config", "migrations")

# Individual files included at the root level
INCLUDE_FILES = ("main.py", "requirements.txt")

# Patterns to exclude
EXCLUDE_PATTERNS = (
    "__pycache__",
    ".pyc",
    ".pyo",
    ".egg-info",
    ".pytest_cache",
    "__pycache__",
)


def get_version() -> str:
    """Read VERSION from src/__init__.py."""
    init_py = PROJECT_ROOT / "src" / "__init__.py"
    if not init_py.exists():
        return "0.0.0"
    for line in init_py.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("VERSION"):
            # VERSION = "1.2.3"
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def should_exclude(path: Path) -> bool:
    """Check if a path should be excluded from the release."""
    parts = path.parts
    for pattern in EXCLUDE_PATTERNS:
        if any(pattern in p for p in parts):
            return True
    return False


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_release(version: str, output_dir: Path) -> Path:
    """Build the release zip and SHA256SUMS.

    Returns the path to the created zip file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_name = f"alma-insights-v{version}.zip"
    zip_path = output_dir / zip_name
    zip_root = f"alma-insights-v{version}"

    print(f"Building release v{version}")
    print(f"Output: {zip_path}")

    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add directories
        for dirname in INCLUDE_DIRS:
            dir_path = PROJECT_ROOT / dirname
            if not dir_path.is_dir():
                print(f"  WARNING: {dirname}/ not found, skipping")
                continue

            for root, dirs, files in os.walk(dir_path):
                root_path = Path(root)
                if should_exclude(root_path):
                    continue

                for fname in sorted(files):
                    fpath = root_path / fname
                    if should_exclude(fpath):
                        continue

                    arcname = f"{zip_root}/{fpath.relative_to(PROJECT_ROOT)}"
                    zf.write(fpath, arcname)
                    file_count += 1

        # Add individual root files
        for fname in INCLUDE_FILES:
            fpath = PROJECT_ROOT / fname
            if fpath.is_file():
                arcname = f"{zip_root}/{fname}"
                zf.write(fpath, arcname)
                file_count += 1

    print(f"  Packed {file_count} files")

    # Generate SHA256SUMS
    checksum = sha256_file(zip_path)
    sums_path = output_dir / "SHA256SUMS"
    with open(sums_path, "w", encoding="utf-8") as f:
        f.write(f"{checksum}  {zip_name}\n")

    print(f"  SHA-256: {checksum}")
    print(f"  SHA256SUMS written to {sums_path}")

    # Also write a .sha256 sidecar for convenience
    sidecar = output_dir / f"{zip_name}.sha256"
    sidecar.write_text(checksum, encoding="utf-8")

    zip_size = zip_path.stat().st_size
    print(f"  Size: {zip_size / 1024:.1f} KB")
    print(f"Done. Upload {zip_name} and SHA256SUMS to GitHub release.")

    return zip_path


def main():
    parser = argparse.ArgumentParser(
        description="Package Alma Insights for release"
    )
    parser.add_argument(
        "--version", "-v",
        help="Version string (default: auto-detect from src/__init__.py)",
    )
    parser.add_argument(
        "--output", "-o",
        default="dist",
        help="Output directory (default: dist/)",
    )
    args = parser.parse_args()

    version = args.version or get_version()
    output_dir = Path(args.output)

    make_release(version, output_dir)


if __name__ == "__main__":
    main()
