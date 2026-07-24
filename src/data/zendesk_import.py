"""Zendesk mirror population — manual file import + GET-only API pull.

Two lanes feed the mig-051 local mirror (see zendesk_store):

* ``import_file`` / ``import_paths`` / ``import_folder`` — parse
  Zendesk-shaped JSON (single object, arrays, ``{"articles": [...]}``-style
  envelopes, macros), raw ``.html`` files, and any doc_reader-supported
  document (.docx / .md / .txt / ...) into mirror upserts with
  ``origin='import'``. The parser NEVER raises: every failure — malformed
  JSON, unreadable binary, unrecognized shape, missing file — becomes a
  per-file error string in the ImportReport and the remaining files
  continue.
* ``pull_mirror`` — read-only API refresh through the paged GET client
  methods (categories → sections → articles → macros), ``origin='pull'``.
  No client write method is ever called from this module: the mirror is
  read-only by design and the specialist copies content into real Zendesk
  by hand.

File-authored content (an .html/.docx/.md file, or JSON entries without an
``id``) gets a deterministic NEGATIVE synthetic id —
``-(int(sha256(source_ref).hexdigest()[:15], 16))`` — stable across
re-imports so the same file updates the same mirror row instead of
duplicating it (``source_file`` records the origin path). Real API ids are
kept verbatim.

Collision policy lives in the store upserts: an import may never replace a
differing pull-origin baseline (the mirror is authoritative) — refused pks
come back in ``conflict_ids`` and surface here under ``conflicts``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import zipfile
from typing import TypedDict

from src.data.zendesk_store import (
    html_to_text,
    upsert_articles,
    upsert_categories,
    upsert_macros,
    upsert_sections,
)

logger = logging.getLogger("alma.zendesk.import")

_HTML_EXTS = {".html", ".htm"}
# Folder scan set: JSON + HTML + .docx + the doc_reader text formats.
_FOLDER_EXTS = {".json", ".html", ".htm", ".docx", ".md", ".markdown",
                ".txt", ".text", ".csv", ".rst", ".log"}

_MAX_FILE_BYTES = 50 * 1024 * 1024  # hostile-input guard: refuse >50MB files
_MAX_TITLE_CHARS = 255

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


class ImportReport(TypedDict):
    """Per-file import outcome (shape frozen — WS3 relays it verbatim as
    ``import_resolved``)."""
    file: str
    kind: str                 # sniff_payload result; 'article' for file docs
    imported: int
    updated: int
    skipped_unchanged: int
    conflicts: list           # [pk, ...] — refused pull-origin collisions
    errors: list              # [str, ...]


# ── payload classification ───────────────────────────────────────────

def sniff_payload(obj) -> str:
    """Classify a decoded JSON payload.

    Returns ``'articles' | 'macros' | 'sections' | 'categories'`` for
    collections (envelope keys or an array sniffed by its first dict),
    ``'article' | 'macro'`` for single objects, else ``'unknown'``.
    """
    if isinstance(obj, dict):
        for key in ("articles", "macros", "sections", "categories"):
            if isinstance(obj.get(key), list):
                return key
        if "title" in obj and ("body" in obj or "body_html" in obj):
            return "article"
        if "actions" in obj and ("name" in obj or "title" in obj):
            return "macro"
        return "unknown"
    if isinstance(obj, list):
        first = next((x for x in obj if isinstance(x, dict)), None)
        if first is None:
            return "unknown"
        # actions first: an API macro also carries title+id, so the macro
        # tell must win before the title+id article rule fires.
        if "actions" in first:
            return "macros"
        if ("body" in first or "body_html" in first
                or ("title" in first and "id" in first)):
            return "articles"
        if "category_id" in first and "name" in first:
            return "sections"
        if "name" in first and "position" in first and "body" not in first:
            return "categories"
        return "unknown"
    return "unknown"


# ── synthetic ids + entry prep ───────────────────────────────────────

def _synthetic_id(source_ref: str) -> int:
    """Deterministic NEGATIVE id for file-authored content: stable across
    re-imports (same source ref → same row) and disjoint from real API ids
    (which are positive)."""
    digest = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()
    return -int(digest[:15], 16)


def _prep_entries(items: list, source_ref: str,
                  name_keys: tuple) -> tuple[list, int]:
    """Copy mirror entries, injecting a synthetic id where none is present
    (keyed on source path + title/name so multiple id-less entries in one
    file stay distinct). Non-dict entries and entries whose explicit id is
    not int-coercible are dropped and counted."""
    valid: list = []
    dropped = 0
    for it in items:
        if not isinstance(it, dict):
            dropped += 1
            continue
        entry = dict(it)  # never mutate the caller's payload
        if entry.get("id") in (None, ""):
            label = next((str(entry.get(k)) for k in name_keys
                          if entry.get(k)), "")
            entry["id"] = _synthetic_id(f"{source_ref}#{label}")
        else:
            try:
                int(entry["id"])
            except (TypeError, ValueError):
                dropped += 1
                continue
        valid.append(entry)
    return valid, dropped


def _prep_taxonomy(items: list) -> tuple[list, int]:
    """Sections/categories keep real ids only — taxonomy without an id is
    dropped and counted (no synthetic ids for structure rows)."""
    valid: list = []
    dropped = 0
    for it in items:
        if not isinstance(it, dict):
            dropped += 1
            continue
        try:
            int(it.get("id"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        valid.append(it)
    return valid, dropped


def _merge_mirror_report(report: dict, res: dict) -> None:
    """Fold a store upsert report into the per-file ImportReport."""
    report["imported"] += res.get("inserted", 0)
    report["updated"] += res.get("updated", 0)
    report["skipped_unchanged"] += res.get("unchanged", 0)
    report["conflicts"].extend(res.get("conflict_ids", []))


# ── titles ───────────────────────────────────────────────────────────

def _stem_title(abs_path: str) -> str:
    stem = os.path.splitext(os.path.basename(abs_path))[0]
    return stem[:_MAX_TITLE_CHARS] or "Imported document"


def _html_title(raw: str) -> str:
    """First non-empty of <title> then <h1>, tag-stripped + entity-decoded."""
    for pat in (_TITLE_RE, _H1_RE):
        m = pat.search(raw)
        if m:
            title = " ".join(html_to_text(m.group(1)).split())
            if title:
                return title[:_MAX_TITLE_CHARS]
    return ""


def _md_title(markdown: str) -> str:
    m = _MD_HEADING_RE.search(markdown or "")
    if m:
        return " ".join(m.group(1).split())[:_MAX_TITLE_CHARS]
    return ""


# ── per-format importers ─────────────────────────────────────────────

def _import_json(conn, abs_path: str, report: dict) -> None:
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            payload = json.load(fh)
    except (ValueError, OSError) as exc:
        report["errors"].append(f"not valid JSON: {exc}")
        return
    kind = sniff_payload(payload)
    report["kind"] = kind
    if isinstance(payload, dict) and kind in ("articles", "macros",
                                              "sections", "categories"):
        # Envelope: import every family key present, not just the sniffed one
        families = {k: payload[k]
                    for k in ("categories", "sections", "articles", "macros")
                    if isinstance(payload.get(k), list)}
    elif kind in ("articles", "macros", "sections", "categories"):
        families = {kind: payload}
    elif kind == "article":
        families = {"articles": [payload]}
    elif kind == "macro":
        families = {"macros": [payload]}
    else:
        report["errors"].append("unrecognized JSON shape")
        return

    dropped = 0
    for family in ("categories", "sections", "articles", "macros"):
        items = families.get(family)
        if items is None:
            continue
        if family == "articles":
            valid, d = _prep_entries(items, abs_path, ("title", "name"))
            _merge_mirror_report(report, upsert_articles(
                conn, valid, origin="import", source_file=abs_path))
        elif family == "macros":
            valid, d = _prep_entries(items, abs_path, ("name", "title"))
            _merge_mirror_report(report, upsert_macros(
                conn, valid, origin="import", source_file=abs_path))
        elif family == "sections":
            valid, d = _prep_taxonomy(items)
            res = upsert_sections(conn, valid, origin="import")
            report["imported"] += res.get("inserted", 0)
            report["updated"] += res.get("updated", 0)
        else:
            valid, d = _prep_taxonomy(items)
            res = upsert_categories(conn, valid, origin="import")
            report["imported"] += res.get("inserted", 0)
            report["updated"] += res.get("updated", 0)
        dropped += d
    if dropped:
        report["errors"].append(f"{dropped} invalid entries skipped")


def _import_html(conn, abs_path: str, report: dict) -> None:
    """A raw HTML file becomes one article: body_html is the file text
    VERBATIM; title from <title> then <h1> then the filename."""
    report["kind"] = "article"
    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    article = {"id": _synthetic_id(abs_path),
               "title": _html_title(raw) or _stem_title(abs_path),
               "body_html": raw}
    _merge_mirror_report(report, upsert_articles(
        conn, [article], origin="import", source_file=abs_path))


def _import_document(conn, abs_path: str, report: dict) -> None:
    """doc_reader path (.docx/.md/.txt/...): document → markdown →
    markdown_to_html → body_html; title from the first heading or the
    filename. strict=True so binary/unreadable files become a clear
    per-file error instead of a mojibake article."""
    from src.data.doc_reader import UnsupportedDocumentError, read_document
    from src.data.html_markdown import markdown_to_html
    report["kind"] = "article"
    # Zip-bomb guard: a tiny-on-disk .docx can expand to GBs in memory. Sum
    # the archive's decompressed sizes BEFORE doc_reader parses it; a non-zip
    # .docx falls through so read_document reports its own clear error.
    if os.path.splitext(abs_path)[1].lower() == ".docx":
        expanded = None
        try:
            with zipfile.ZipFile(abs_path) as zf:
                expanded = sum(i.file_size for i in zf.infolist())
        except Exception:  # noqa: BLE001 — bad/absent zip → read_document's error
            expanded = None
        if expanded is not None and expanded > _MAX_FILE_BYTES:
            report["errors"].append("docx too large when decompressed (>50MB)")
            return
    try:
        markdown = read_document(abs_path, strict=True)
    except UnsupportedDocumentError as exc:
        report["errors"].append(str(exc))
        return
    article = {"id": _synthetic_id(abs_path),
               "title": _md_title(markdown) or _stem_title(abs_path),
               "body_html": markdown_to_html(markdown)}
    _merge_mirror_report(report, upsert_articles(
        conn, [article], origin="import", source_file=abs_path))


# ── public API ───────────────────────────────────────────────────────

def _new_report(path: str) -> dict:
    return {"file": str(path), "kind": "unknown", "imported": 0, "updated": 0,
            "skipped_unchanged": 0, "conflicts": [], "errors": []}


def _zero_totals() -> dict:
    return {"files": 0, "imported": 0, "updated": 0, "skipped_unchanged": 0,
            "conflicts": 0, "errors": 0}


def import_file(conn, path: str) -> dict:
    """Import ONE file into the mirror (origin='import'). Never raises —
    every failure lands in the report's ``errors`` list."""
    report = _new_report(path)
    try:
        abs_path = os.path.abspath(str(path))
        if not os.path.isfile(abs_path):
            report["errors"].append("file not found")
            return report
        if os.path.getsize(abs_path) > _MAX_FILE_BYTES:
            report["errors"].append("file too large (>50MB)")
            return report
        ext = os.path.splitext(abs_path)[1].lower()
        if ext == ".json":
            _import_json(conn, abs_path, report)
        elif ext in _HTML_EXTS:
            _import_html(conn, abs_path, report)
        else:
            _import_document(conn, abs_path, report)
    except Exception as exc:  # noqa: BLE001 — parser contract: never raise
        logger.warning("Zendesk import failed for %s: %s", path, exc)
        report["errors"].append(f"import failed: {exc}")
    return report


def import_paths(conn, paths: list) -> dict:
    """Import a batch of files; a failing file never stops the batch."""
    files = [import_file(conn, p) for p in (paths or [])]
    totals = _zero_totals()
    totals["files"] = len(files)
    for r in files:
        totals["imported"] += r["imported"]
        totals["updated"] += r["updated"]
        totals["skipped_unchanged"] += r["skipped_unchanged"]
        totals["conflicts"] += len(r["conflicts"])
        totals["errors"] += len(r["errors"])
    return {"ok": True, "files": files, "totals": totals}


def import_folder(conn, folder: str) -> dict:
    """Import every supported file at the top level of ``folder``
    (*.json, *.html, *.htm, *.docx + doc_reader text formats). Unsupported
    extensions are ignored, not errors."""
    try:
        entries = sorted(os.listdir(folder))
    except (OSError, TypeError):
        return {"ok": False, "error": "folder_not_found", "files": [],
                "totals": _zero_totals()}
    paths = []
    for name in entries:
        p = os.path.join(str(folder), name)
        if (os.path.splitext(name)[1].lower() in _FOLDER_EXTS
                and os.path.isfile(p)):
            paths.append(p)
    return import_paths(conn, paths)


def pull_mirror(conn, client=None, *, locale: str = "en-us") -> dict:
    """Read-only mirror refresh: categories → sections → articles → macros
    via the paged GET client methods, upserted with origin='pull'.

    Degrades gracefully, never raises: no credentials →
    ``{"ok": False, "error": "zendesk_not_connected"}``; a network/auth
    failure → ``{"ok": False, "error": <message>}``. ``truncated`` is True
    when any family's page cap cut the fetch short of Zendesk's own count.
    """
    if client is None:
        try:
            from src.data.zendesk_client import ZendeskClient
            client = ZendeskClient.from_settings()
        except Exception:  # noqa: BLE001 — missing keyring backend etc.
            client = None
    if client is None:
        return {"ok": False, "error": "zendesk_not_connected"}
    try:
        categories, cat_total = client.get_categories_paged(locale=locale)
        sections, sec_total = client.get_sections_paged(locale=locale)
        articles, art_total = client.get_articles_paged(locale=locale)
        macros, mac_total = client.list_macros_paged()
    except Exception as exc:  # noqa: BLE001 — auth/rate/network all degrade
        logger.warning("Zendesk pull failed: %s", exc)
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    try:
        for a in articles:
            if isinstance(a, dict):
                a.setdefault("locale", locale)
        upsert_categories(conn, categories, origin="pull")
        upsert_sections(conn, sections, origin="pull")
        upsert_articles(conn, articles, origin="pull")
        upsert_macros(conn, macros, origin="pull")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Zendesk pull upsert failed: %s", exc)
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    truncated = (len(categories) < cat_total or len(sections) < sec_total
                 or len(articles) < art_total or len(macros) < mac_total)
    return {"ok": True, "articles": len(articles), "macros": len(macros),
            "sections": len(sections), "categories": len(categories),
            "truncated": truncated}
