"""App-owned documents tree — the single source for where doc files live.

The app organizes user-facing files under one root (default
``data/documents/`` next to the database — inside data/ so the tree
survives auto-updates, which replace src/ and config/ wholesale, and stays
outside the installer's APP_CONTENTS, same rationale as artifact_store):

    Downloads/        easy landing zone for saved reports and files
    Exports/          generated deliverables (decks, report exports)
    Zendesk Imports/  drop Zendesk export files here before importing
    Zendesk Edits/    markdown copies of Zendesk revision drafts
    Worksheets/       per-job markdown worksheets (solver / research)

Qt-free and stdlib-only so it imports headlessly anywhere (the web_flags
precedent). The root can be overridden with ``documents.root`` in settings;
any settings error degrades to the default — path resolution must never
block boot. All accessors lazily mkdir on access (the artifact_store
pattern). Anchored to the project root — NEVER the cwd.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_ROOT = _PROJECT_ROOT / "data" / "documents"

# name -> on-disk folder (human-titled: end users browse this tree)
SPACES: dict[str, str] = {
    "downloads": "Downloads",
    "exports": "Exports",
    "zendesk_imports": "Zendesk Imports",
    "zendesk_edits": "Zendesk Edits",
    "worksheets": "Worksheets",
}

_README = """This folder is managed by Alma Insights (Content Command Center).

Downloads       - default landing zone for files you save from the app
Exports         - generated deliverables (decks, report exports)
Zendesk Imports - put Zendesk export files here, then import them in-app
Zendesk Edits   - markdown copies of Zendesk revision drafts
Worksheets      - per-job markdown worksheets

You can move or delete files freely; "Run backfill" in Settings >
Maintenance recreates the folders and re-exports app content.
"""


def _settings_root() -> Path | None:
    """documents.root override; any error or blank degrades to None."""
    try:
        from src.data.settings_manager import get_section
        raw = (get_section("documents", {}) or {}).get("root", "")
        raw = str(raw or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser()
    except Exception:
        return None


def docs_root() -> Path:
    root = _settings_root() or _DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def space_dir(name: str) -> Path:
    if name not in SPACES:
        raise ValueError(f"unknown documents space {name!r}")
    path = docs_root() / SPACES[name]
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir() -> Path:
    return space_dir("downloads")


def exports_dir() -> Path:
    return space_dir("exports")


def zendesk_import_dir() -> Path:
    return space_dir("zendesk_imports")


def zendesk_edits_dir() -> Path:
    return space_dir("zendesk_edits")


def worksheets_dir() -> Path:
    return space_dir("worksheets")


def start_dir(space: str, filename: str | None = None) -> str:
    """QFileDialog-safe accessor: the space path (optionally joined with a
    suggested filename), degrading to the bare filename / "" on ANY error so
    a filesystem problem can never break a save dialog."""
    try:
        base = space_dir(space)
        return str(base / filename) if filename else str(base)
    except Exception:
        return filename or ""


def ensure_docs_tree() -> list[dict]:
    """Create the full tree idempotently; report per-space creation.

    Returns [{"name", "path", "created"}] — the Maintenance backfill card's
    status source. Also drops a README.txt at the root when missing.
    """
    report: list[dict] = []
    root = docs_root()
    for name, folder in SPACES.items():
        path = root / folder
        created = not path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        report.append({"name": name, "path": str(path), "created": created})
    readme = root / "README.txt"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
    return report
