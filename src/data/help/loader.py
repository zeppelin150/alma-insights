"""Load the bundled help corpus from ``assets/help/`` into the database.

Authoring format — one markdown file per article, YAML frontmatter then body:

    ---
    id: renn-what-it-can-do
    title: What Renn can do, and what Renn will refuse
    section: renn
    section_title: Renn, your assistant
    section_order: 2
    order: 1
    status: available          # available | partial | flag-gated | not-available
    features: [en_agent, kb_search]
    summary: One sentence shown in search results.
    last_verified: 2026-07-20
    ---

    ## How it works
    ...

``assets/`` already ships with the app (the installer copies it), so adding or
correcting an article is a file replacement — no code change, no migration.
The loader upserts on content hash, so calling it on every launch is cheap.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from src.data.help import store

logger = logging.getLogger("alma.help.loader")

# src/data/help/loader.py → parents[3] is the project root. Matches the
# existing bundled-asset idiom (enablement/page.py:2283).
_HELP_DIR = Path(__file__).resolve().parents[3] / "assets" / "help"

_REQUIRED = ("id", "title", "section")


def help_dir() -> Path:
    return _HELP_DIR


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Missing frontmatter yields ('', text)."""
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]).lstrip("\n")
    return "", text


def _parse_yaml(raw: str) -> dict:
    try:
        import yaml
        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a bad file must not break the app
        return {}


def parse_article(path: Path) -> dict | None:
    """Parse one help file into an article dict, or None if unusable."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("help: unreadable %s (%s)", path.name, exc)
        return None
    raw_fm, body = _split_frontmatter(text)
    meta = _parse_yaml(raw_fm)
    missing = [k for k in _REQUIRED if not meta.get(k)]
    if missing:
        logger.warning("help: %s missing frontmatter %s — skipped",
                       path.name, missing)
        return None
    return {
        "article_id": str(meta["id"]).strip(),
        "title": str(meta["title"]).strip(),
        "section": str(meta["section"]).strip(),
        "section_title": str(meta.get("section_title") or meta["section"]),
        "section_order": meta.get("section_order") or 0,
        "article_order": meta.get("order") or 0,
        "status": str(meta.get("status") or "available").strip(),
        "applies_to": str(meta.get("applies_to") or "enablement").strip(),
        "summary": str(meta.get("summary") or "").strip(),
        "body": body,
        "features": meta.get("features") or [],
        "last_verified": str(meta.get("last_verified") or "").strip(),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }


def bundled_file_count(directory: Path | None = None) -> int:
    """How many ``*.md`` files the bundled corpus ships. Cheap (no parse) —
    used as the cross-check against the DB row count to decide whether a
    re-sync is needed. An over-count (a malformed file that won't parse) only
    causes one extra idempotent load, never a miss."""
    root = directory or _HELP_DIR
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob("*.md"))


def sync_bundled_help(conn, *, directory: Path | None = None) -> dict:
    """Load the corpus IFF the DB is behind the bundled files, else no-op.

    The corpus is the source of truth and grows over releases; a DB populated
    against an older, smaller corpus (the 2026-07-22 "only 8 of 62 articles"
    field bug) must re-sync when new files ship — not only when the table is
    empty. Runs the (upsert-on-hash, idempotent) loader when the on-disk file
    count exceeds the DB row count; otherwise returns ``{"skipped_current": N}``
    so an already-current DB pays only one COUNT, no re-hash of every file.

    Returns the loader summary (or the skip marker). Never raises.
    """
    try:
        db_n = store.count_articles(conn)
        disk_n = bundled_file_count(directory)
    except Exception as exc:  # noqa: BLE001 — a count failure must not break help
        logger.debug("help sync count failed: %s", exc)
        return {"loaded": 0, "skipped": 0, "unchanged": 0, "errors": []}
    if db_n >= disk_n and db_n > 0:
        return {"skipped_current": db_n}
    return load_bundled_help(conn, directory=directory)


def load_bundled_help(conn, *, directory: Path | None = None) -> dict:
    """Load every ``assets/help/**/*.md`` into ``help_articles``.

    Returns a summary dict ``{loaded, skipped, unchanged, errors}``. Never
    raises: a malformed article is logged and skipped, because a broken help
    file must not stop the app from launching.
    """
    root = directory or _HELP_DIR
    summary = {"loaded": 0, "skipped": 0, "unchanged": 0, "errors": []}
    if not root.exists():
        logger.info("help: no bundled corpus at %s", root)
        return summary
    seen_ids: set[str] = set()
    for path in sorted(root.rglob("*.md")):
        article = parse_article(path)
        if article is None:
            summary["skipped"] += 1
            summary["errors"].append(f"{path.name}: unparseable frontmatter")
            continue
        if article["article_id"] in seen_ids:
            summary["skipped"] += 1
            summary["errors"].append(
                f"{path.name}: duplicate id {article['article_id']!r}")
            continue
        seen_ids.add(article["article_id"])
        try:
            wrote = store.upsert_article(conn, article)
        except ValueError as exc:
            summary["skipped"] += 1
            summary["errors"].append(f"{path.name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            summary["skipped"] += 1
            summary["errors"].append(f"{path.name}: {exc}")
            continue
        if wrote:
            summary["loaded"] += 1
        else:
            summary["unchanged"] += 1
    if summary["errors"]:
        logger.warning("help: %d article(s) skipped: %s",
                       summary["skipped"], "; ".join(summary["errors"][:5]))
    return summary
