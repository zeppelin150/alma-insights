#!/usr/bin/env python3
"""
Alma Insights — Uninstaller

Removes a bundle install. User data (the `data/` directory holding the
SQLite databases, chat sessions, crash reports, hardware profile, and
exported reports) is preserved by default — the user must pass
`--purge-data` to wipe it.

Keyring-stored secrets are deleted when the user opts in: the installer
seeded the `github_update_token` and the app stored the Lightdash PAT
+ API keys during use, all under the `alma-insights` service name. The
uninstaller offers to remove these with a clear confirmation prompt.

Usage:
    python installer/uninstall.py                 # interactive
    python installer/uninstall.py --yes           # confirm every prompt
    python installer/uninstall.py --purge-data    # also delete SQLite dbs
    python installer/uninstall.py --keep-secrets  # leave OS keyring intact
    python installer/uninstall.py --dry-run       # report only, change nothing

Exit codes:
    0  success (including "nothing to do")
    1  user declined
    2  install dir not found
    3  filesystem error
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

# Make `from src...` importable when run from the installed tree.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

APP_NAME = "Alma Insights"
APP_ID = "AlmaInsights"

_SECRET_KEYS = (
    "lightdash_pat",
    "gemini_api_key",
    "anthropic_api_key",
    "guru_api_token",
    "zendesk_api_key",
    "github_update_token",
)


# ──────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Uninstall {APP_NAME}.")
    parser.add_argument("--install-dir", type=Path,
                        help="Override the auto-detected install directory.")
    parser.add_argument("--yes", action="store_true",
                        help="Assume 'yes' for all confirmation prompts.")
    parser.add_argument("--purge-data", action="store_true",
                        help="Delete user data (DBs, chat sessions, reports).")
    parser.add_argument("--keep-secrets", action="store_true",
                        help="Do not remove stored credentials from the OS keyring.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without doing anything.")
    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────
# Install directory resolution
# ──────────────────────────────────────────────────────────────────

def default_install_dir() -> Path:
    """Mirror installer/install.py::get_default_install_dir."""
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        return Path(local) / APP_ID if local else Path.home() / "AppData" / "Local" / APP_ID
    if system == "Darwin":
        return Path.home() / "Applications" / APP_ID
    return Path.home() / f".{APP_ID.lower()}"


# ──────────────────────────────────────────────────────────────────
# User-data handling (preserve by default, opt-in purge)
# ──────────────────────────────────────────────────────────────────

def preserve_user_data(install_dir: Path, dry_run: bool) -> Path | None:
    """
    Move install_dir/app/data to a sibling backup directory so we can
    remove install_dir without losing user databases. Returns the
    backup path, or None if there was nothing to preserve.
    """
    data_dir = install_dir / "app" / "data"
    if not data_dir.is_dir():
        return None

    backup = install_dir.parent / f".{APP_ID}_data_backup"
    if dry_run:
        print(f"  [dry-run] Would move {data_dir} -> {backup}")
        return backup

    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    shutil.move(str(data_dir), str(backup))
    print(f"  Preserved user data at {backup}")
    return backup


# ──────────────────────────────────────────────────────────────────
# Filesystem removal
# ──────────────────────────────────────────────────────────────────

def remove_install_dir(install_dir: Path, dry_run: bool) -> bool:
    """Delete install_dir. Returns True on success."""
    if dry_run:
        print(f"  [dry-run] Would rmtree {install_dir}")
        return True
    try:
        shutil.rmtree(install_dir)
    except OSError as exc:
        print(f"  [ERROR] Could not remove {install_dir}: {exc}", file=sys.stderr)
        return False
    print(f"  Removed {install_dir}")
    return True


def remove_desktop_shortcut(dry_run: bool) -> None:
    """Remove the desktop launcher if present (best-effort)."""
    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        candidates.append(Path.home() / "Desktop" / f"{APP_NAME}.lnk")
    elif system == "Darwin":
        candidates.append(Path.home() / "Desktop" / f"{APP_NAME}.command")

    for path in candidates:
        if not path.exists():
            continue
        if dry_run:
            print(f"  [dry-run] Would delete {path}")
            continue
        try:
            path.unlink()
            print(f"  Removed shortcut {path}")
        except OSError as exc:
            print(f"  [WARN] Could not remove {path}: {exc}")


# ──────────────────────────────────────────────────────────────────
# Keyring cleanup
# ──────────────────────────────────────────────────────────────────

def clear_keyring_entries(dry_run: bool) -> int:
    """Remove all Alma-scoped secrets from the OS keyring. Returns count."""
    try:
        from src.data import pat_store
    except ImportError:
        print("  [WARN] Could not import pat_store — keyring cleanup skipped.")
        return 0

    removed = 0
    for key in _SECRET_KEYS:
        if not pat_store.load_setting(key):
            continue
        if dry_run:
            print(f"  [dry-run] Would delete keyring entry {key!r}")
            removed += 1
            continue
        if pat_store._delete_secret(key):
            print(f"  Deleted keyring entry {key!r}")
            removed += 1
    return removed


# ──────────────────────────────────────────────────────────────────
# Interaction
# ──────────────────────────────────────────────────────────────────

def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        resp = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return resp in ("y", "yes")


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    args = _parse_args()
    install_dir = (args.install_dir or default_install_dir()).resolve()

    print(f"Uninstalling {APP_NAME} from: {install_dir}")
    if not install_dir.exists():
        print(f"  Install directory does not exist — nothing to do.")
        return 2

    if not confirm("Proceed with uninstall?", args.yes):
        print("Aborted.")
        return 1

    # 1. Preserve user data unless the user opted into a full purge.
    if args.purge_data:
        if not confirm("This will DELETE all user data (databases, reports). Confirm?",
                        args.yes):
            print("Aborted.")
            return 1
        print("  User data will be deleted with the install.")
    else:
        preserve_user_data(install_dir, args.dry_run)

    # 2. Remove the install tree.
    if not remove_install_dir(install_dir, args.dry_run):
        return 3

    # 3. Desktop shortcut.
    remove_desktop_shortcut(args.dry_run)

    # 4. Keyring secrets.
    if args.keep_secrets:
        print("  Skipping keyring cleanup (--keep-secrets).")
    elif confirm(
        "Remove stored credentials (Lightdash PAT, API keys, update token)?",
        args.yes,
    ):
        removed = clear_keyring_entries(args.dry_run)
        if removed == 0:
            print("  No stored credentials found.")

    print(f"\n{APP_NAME} uninstalled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
