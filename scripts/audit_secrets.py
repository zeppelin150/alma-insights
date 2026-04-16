#!/usr/bin/env python3
"""
scripts/audit_secrets.py — credential leak scanner.

Scans the working tree, the git history, and a few notable config
files for credential-shaped strings. Intended to run in CI as a
blocking step, and locally via `python scripts/audit_secrets.py`.

Patterns matched (case-insensitive):
  ldpat_                   Lightdash PAT prefix
  sk-ant-[A-Za-z0-9]{20}+  Anthropic API key
  gh[pousr]_[A-Za-z0-9]{36}+  GitHub fine-grained PAT
  AIza[0-9A-Za-z_\\-]{35}  Google API key

Exit codes:
    0  nothing found
    1  at least one suspect string found
    2  invocation / environment error
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent

_PATTERNS = {
    "lightdash_pat":       re.compile(r"ldpat_[A-Za-z0-9]{16,}"),
    "anthropic_api_key":   re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "github_fine_grained": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "google_api_key":      re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "generic_bearer":      re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{40,}", re.IGNORECASE),
}

# Files / directories to skip — they legitimately contain tokens as
# test fixtures or documentation examples.
_SKIP_PATHS = (
    ".git",
    ".claude",                 # Claude Code local state, not part of the app
    ".venv",
    ".lockenv",
    "node_modules",
    "__pycache__",
    "data/models",
    "data/alma_insights.db",
    "dist",
    "tests/",                  # test suites use fixture tokens
    "docs/",                   # docs reference token *shapes*
    "scripts/audit_secrets.py",
    "data/crash_reports",
    "scan_server/node_modules",
)

# Files that are always safe to scan even if under a skipped prefix
_FORCE_SCAN: tuple[str, ...] = ()


# ──────────────────────────────────────────────────────────────────
# Working-tree scan
# ──────────────────────────────────────────────────────────────────

def _should_skip(rel: str) -> bool:
    if rel in _FORCE_SCAN:
        return False
    return any(rel.startswith(prefix) for prefix in _SKIP_PATHS)


def scan_working_tree(root: Path) -> list[tuple[str, str, int, str]]:
    """
    Walk `root` for text files and return matches as
    (path, pattern_name, line_no, line).
    """
    findings: list[tuple[str, str, int, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _should_skip(rel):
            continue
        if path.suffix in {".pyc", ".png", ".jpg", ".ico", ".zip", ".db",
                           ".so", ".dylib", ".dll", ".pyd", ".bin",
                           ".safetensors"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(_scan_text(rel, text))
    return findings


def _scan_text(source: str, text: str) -> Iterable[tuple[str, str, int, str]]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        for name, pattern in _PATTERNS.items():
            if pattern.search(line):
                yield (source, name, line_no, line.strip())


# ──────────────────────────────────────────────────────────────────
# Git-history scan
# ──────────────────────────────────────────────────────────────────

def scan_git_history(root: Path) -> list[tuple[str, str, str]]:
    """
    Pipe `git log --all -p` through our patterns. Each hit is returned
    as (commit_hash_or_line_marker, pattern_name, matched_line).

    Returns [] if the root isn't a git repo or git is unavailable.
    """
    if not (root / ".git").exists():
        return []
    try:
        proc = subprocess.run(
            ["git", "log", "--all", "-p", "--no-color"],
            cwd=root, capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    findings: list[tuple[str, str, str]] = []
    current_commit = "?"
    for line in proc.stdout.splitlines():
        if line.startswith("commit "):
            current_commit = line.split()[1][:12]
            continue
        for name, pattern in _PATTERNS.items():
            if pattern.search(line):
                findings.append((current_commit, name, line.strip()))
    return findings


# ──────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────

def _print_findings(
    tree_findings, history_findings, *, show_history: bool,
) -> None:
    if tree_findings:
        print(f"Working tree — {len(tree_findings)} finding(s):")
        for src, pattern, line_no, line in tree_findings[:50]:
            print(f"  {src}:{line_no} [{pattern}] {line[:160]}")
        if len(tree_findings) > 50:
            print(f"  ... and {len(tree_findings) - 50} more")

    if show_history and history_findings:
        print(f"\nGit history — {len(history_findings)} finding(s):")
        for commit, pattern, line in history_findings[:20]:
            print(f"  {commit} [{pattern}] {line[:160]}")
        if len(history_findings) > 20:
            print(f"  ... and {len(history_findings) - 20} more")


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for leaked credentials.")
    parser.add_argument("--root", type=Path, default=_REPO_ROOT,
                        help="Repo root (default: auto-detected)")
    parser.add_argument("--no-history", action="store_true",
                        help="Skip the git-history scan")
    parser.add_argument("--allow-history", action="store_true",
                        help="Report history findings without failing the build")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"[audit_secrets] Root not found: {args.root}", file=sys.stderr)
        return 2

    tree = scan_working_tree(args.root)
    history = [] if args.no_history else scan_git_history(args.root)

    _print_findings(tree, history, show_history=not args.no_history)

    if tree:
        return 1
    if history and not args.allow_history:
        return 1
    print("[audit_secrets] No credential-shaped strings found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
