#!/usr/bin/env python3
"""
scripts/provision_update_token.py — install-time keyring seeder.

Called once by the installer after the Python runtime is unpacked but
before the first splash launch. Stores the GitHub update token in the
OS keyring so the shipped app never needs to see it again.

Usage (quiet / CI):
    python scripts/provision_update_token.py --from-env ALMA_UPDATE_TOKEN
    python scripts/provision_update_token.py --token ghp_xxx
    python scripts/provision_update_token.py --from-file ./token.txt

Usage (interactive):
    python scripts/provision_update_token.py            # prompts with getpass
    python scripts/provision_update_token.py --clear    # remove stored token

Exit codes:
    0   token stored (or already present and --force not given)
    1   validation error (bad args)
    2   keyring unavailable
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Make `from src...` work regardless of how the script is invoked
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data import pat_store  # noqa: E402

_KEY = "github_update_token"


# ──────────────────────────────────────────────────────────────────
# Argument handling
# ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the GitHub update token.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--token", help="Token value (avoid in shared shell history)")
    source.add_argument("--from-env", metavar="VAR", help="Read from environment variable")
    source.add_argument("--from-file", metavar="PATH", help="Read from a file's first line")
    source.add_argument("--clear", action="store_true", help="Delete the stored token")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing stored token")
    parser.add_argument("--quiet", action="store_true", help="Suppress success output")
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────
# Token resolution
# ──────────────────────────────────────────────────────────────────

def _resolve_token(args: argparse.Namespace) -> str | None:
    if args.clear:
        return None
    if args.token:
        return args.token.strip()
    if args.from_env:
        return (os.environ.get(args.from_env) or "").strip()
    if args.from_file:
        try:
            return Path(args.from_file).read_text(encoding="utf-8").strip().splitlines()[0].strip()
        except (OSError, IndexError):
            return ""
    return getpass.getpass("GitHub update token: ").strip()


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    args = _parse_args()

    ok, backend = pat_store.keyring_available()
    if not ok:
        print(f"[provision_update_token] Keyring unavailable: {backend}", file=sys.stderr)
        return 2

    if args.clear:
        pat_store.save_setting(_KEY, "")   # routes secret keys → keyring.delete
        if not args.quiet:
            print("[provision_update_token] Cleared stored token.")
        return 0

    existing = pat_store.load_setting(_KEY)
    if existing and not args.force:
        if not args.quiet:
            print("[provision_update_token] Token already present — pass --force to overwrite.")
        return 0

    token = _resolve_token(args)
    if not token:
        print("[provision_update_token] No token supplied — nothing to do.", file=sys.stderr)
        return 1

    if not pat_store.save_setting(_KEY, token):
        print("[provision_update_token] Keyring write failed.", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"[provision_update_token] Stored under '{_KEY}' in {backend}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
