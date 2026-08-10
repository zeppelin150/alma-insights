"""Backfill the managed documents tree from existing app content.

Idempotent, never raises: every sub-step is fenced and failures land in the
report's ``errors`` list. What it does today:

1. ensure the tree (app_paths.ensure_docs_tree)
2. copy generated artifact files (decks etc. under data/artifacts/<id>/)
   into Exports/, prefixed with the FULL artifact id so two artifacts can
   never merge — copied only when missing or the size differs
3. export every Zendesk revision draft (article + macro, all statuses,
   uncapped via zendesk_store.iter_draft_ids) as a markdown file into
   "Zendesk Edits"/ — rewritten only when content changed (byte-exact
   comparison; files are written LF so CR-bearing drafts stay idempotent)

Qt-free; callers pass a connection or let ``run_backfill`` open the default
warehouse read-only.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from src.data import app_paths

_COPY_EXTS = {".pptx", ".docx", ".pdf", ".md", ".html", ".csv", ".txt"}


def _artifacts_root() -> Path:
    from src.data import artifact_store
    return artifact_store.artifacts_root()


def _default_conn() -> sqlite3.Connection:
    from src.data.connection_factory import DB_DIR, get_connection
    return get_connection(DB_DIR / "local_warehouse.db", readonly=True)


def _slug(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return (slug or "untitled")[:max_len]


def _write_if_changed(path: Path, content: str) -> bool:
    """Byte-exact compare + write. Bytes (not text mode) on purpose: text
    mode's newline translation turns CRLF content into ever-changing bytes
    on Windows, breaking idempotency and corrupting the file."""
    data = content.encode("utf-8")
    if path.exists():
        try:
            if path.read_bytes() == data:
                return False
        except OSError:
            pass
    path.write_bytes(data)
    return True


def _backfill_artifacts(report: dict) -> None:
    root = _artifacts_root()
    if not root.is_dir():
        return
    dest = app_paths.exports_dir()
    for adir in sorted(p for p in root.iterdir() if p.is_dir()):
        for src in sorted(adir.iterdir()):
            if not src.is_file() or src.suffix.lower() not in _COPY_EXTS:
                continue
            target = dest / f"{adir.name}_{src.name}"
            try:
                if target.exists() and target.stat().st_size == src.stat().st_size:
                    report["exports_skipped"] += 1
                    continue
                shutil.copy2(src, target)
                report["exports_copied"] += 1
            except OSError as exc:
                report["errors"].append(f"artifact copy {src.name}: {exc}")


def _macro_reply(full: dict) -> str:
    """A macro draft's reply lives in its actions (comment_value) unless a
    rendered reply_html was stored — Renn's propose tool and the doc parser
    never set reply_html, so the actions are the canonical content."""
    actions = full.get("actions")
    if not isinstance(actions, list):
        try:
            actions = json.loads(full.get("actions_json") or "[]")
        except (ValueError, TypeError):
            actions = []
    for action in actions:
        if isinstance(action, dict) and action.get("field") == "comment_value":
            return str(action.get("value") or "")
    return ""


def _draft_body(kind: str, full: dict) -> str:
    if kind == "macro":
        return full.get("reply_html") or _macro_reply(full)
    # Articles: body_html is optional; the mandatory `body` column is the
    # canonical content (publish converts body -> HTML at push time).
    return full.get("body_html") or full.get("body") or ""


def _draft_markdown(kind: str, title: str, full: dict) -> str:
    body = _draft_body(kind, full)
    lines = [
        f"# {title or 'Untitled draft'}",
        "",
        f"- Kind: {kind}",
        f"- Status: {full.get('status', '?')}",
        f"- Target: {full.get('article_id') or full.get('macro_id') or ''}",
        f"- Updated: {full.get('updated_at') or full.get('created_at') or ''}",
        "",
        "## Rationale",
        "",
        (full.get("rationale") or "").strip() or "(none recorded)",
        "",
        "## Content",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def _backfill_zendesk_edits(conn: sqlite3.Connection, report: dict) -> None:
    from src.data import zendesk_store
    dest = app_paths.zendesk_edits_dir()
    for kind, draft_id in zendesk_store.iter_draft_ids(conn):
        try:
            if kind == "macro":
                full = zendesk_store.get_macro_draft(conn, draft_id) or {}
                title = full.get("name") or ""
            else:
                full = zendesk_store.get_article_draft(conn, draft_id) or {}
                title = full.get("title") or ""
            name = f"{kind}-{draft_id}-{_slug(title)}.md"
            if _write_if_changed(dest / name, _draft_markdown(kind, title, full)):
                report["zendesk_edits_written"] += 1
            else:
                report["zendesk_edits_skipped"] += 1
        except (sqlite3.Error, OSError, KeyError) as exc:
            report["errors"].append(f"draft export {kind}-{draft_id}: {exc}")


def run_backfill(conn: sqlite3.Connection | None = None) -> dict:
    """Organize existing content into the documents tree; returns a report."""
    report: dict = {
        "tree": [], "exports_copied": 0, "exports_skipped": 0,
        "zendesk_edits_written": 0, "zendesk_edits_skipped": 0, "errors": [],
    }
    try:
        report["tree"] = app_paths.ensure_docs_tree()
    except OSError as exc:
        report["errors"].append(f"tree: {exc}")
        return report

    try:
        _backfill_artifacts(report)
    except Exception as exc:
        report["errors"].append(f"artifacts: {exc}")

    own_conn = conn is None
    try:
        if own_conn:
            conn = _default_conn()
        _backfill_zendesk_edits(conn, report)
    except Exception as exc:
        report["errors"].append(f"zendesk: {exc}")
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    return report
