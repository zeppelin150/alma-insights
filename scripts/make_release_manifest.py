#!/usr/bin/env python3
"""
scripts/make_release_manifest.py — CI post-build step.

Walks the given artifacts directory (typically `dist/`), computes a
SHA-256 for every zip/dmg/pkg/exe it finds, and writes the combined
release manifest to `dist/release_manifest.json`. The manifest is
uploaded as a release asset alongside the binaries.

Usage:
    python scripts/make_release_manifest.py \\
        --version v9.3.0 \\
        --artifacts dist \\
        --output dist/release_manifest.json \\
        --notes-url https://github.com/alma-health/alma-insights/releases/tag/v9.3.0

Exit code is zero on success and non-zero on any error — safe for CI
`set -e` pipelines.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `from src...` work when run from a CI checkout
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.updater.release_manifest import build_manifest, write_manifest  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a release manifest.")
    parser.add_argument(
        "--version", required=True,
        help="Release tag, e.g. v9.3.0",
    )
    parser.add_argument(
        "--artifacts", default="dist", type=Path,
        help="Directory containing the release zips (default: dist)",
    )
    parser.add_argument(
        "--output", default=None, type=Path,
        help="Output path (default: <artifacts>/release_manifest.json)",
    )
    parser.add_argument(
        "--notes-url", default="",
        help="Optional URL to the release notes page",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = args.output or (args.artifacts / "release_manifest.json")

    try:
        manifest = build_manifest(args.version, args.artifacts, notes_url=args.notes_url)
    except FileNotFoundError as exc:
        print(f"[make_release_manifest] {exc}", file=sys.stderr)
        return 2

    if not manifest["artifacts"]:
        print(
            f"[make_release_manifest] No artifacts found in {args.artifacts}",
            file=sys.stderr,
        )
        return 3

    write_manifest(manifest, output)

    print(f"[make_release_manifest] Wrote {output}")
    for name, entry in manifest["artifacts"].items():
        print(f"  {name:40s} {entry['sha256']}  ({entry['size']:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
