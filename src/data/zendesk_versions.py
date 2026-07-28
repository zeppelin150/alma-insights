"""Zendesk version history — derived capture layer (migration 053).

Two history tables, both derived data that can never alter their sources:

* ``zendesk_article_versions`` — the SUPERSEDED mirror state, captured by
  ``zendesk_store.upsert_articles`` whenever an existing row's
  ``content_hash`` changes. The CURRENT state is never duplicated here;
  it lives on the mirror row. "Restore" is non-destructive: it creates a
  PENDING draft through the existing draft layer
  (``zendesk_store.save_article_draft``), so it rides the established
  pending -> ready -> copied review lifecycle and the mirror baseline
  stays hash-faithful to remote. No function in this module ever writes
  ``zendesk_articles``.
* ``zendesk_draft_versions`` — one row per body-changing save of an
  article draft (Renn propose / specialist edit / native editor), state
  AS OF that save, with a per-save ``author`` ('renn' | 'specialist' |
  'user'). Rollback is just another recorded save
  (``save_kind='rollback'``), pending-only; draft status is never
  written by this module.

Retention: ``_ARTICLE_VERSION_CAP`` / ``_DRAFT_VERSION_CAP`` rows per
article/draft, enforced synchronously at capture time (newest kept by
``version_id DESC``). Orphan cleanup is ``prune_orphans`` — called by the
store after ``delete_draft`` / ``purge_mirror`` — which automatically
preserves the histories of retained copied/pushed drafts.

Transaction discipline: mutators use the store's ``_txn`` (pass-through
when the caller already holds a transaction, ``atomic()`` otherwise).
The capture functions are therefore *caller-transaction participants*:
when ``upsert_articles`` / ``save_article_draft`` invoke them inside
their own ``_txn``, a rolled-back outer write also rolls back its
capture. All ``zendesk_store`` imports are lazy (call-time) — the
store's hooks lazily import this module, so neither imports the other at
module load and there is no cycle.
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone

_ARTICLE_VERSION_CAP = 50
_DRAFT_VERSION_CAP = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_date(iso) -> str:
    """Human date ('Jul 20, 2026') from an ISO timestamp; raw on failure."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    except (ValueError, TypeError):
        return str(iso or "")


def _txn(conn: sqlite3.Connection):
    from src.data.zendesk_store import _txn as store_txn
    return store_txn(conn)


def derive_author(source_ref, rationale) -> str:
    """Author provenance for a draft save when no explicit author is given.

    ``source_ref == 'specialist-edit'`` wins over the rationale rule —
    the specialist's article-target creates carry both markers."""
    if source_ref == "specialist-edit":
        return "specialist"
    if rationale is not None:
        return "renn"
    return "user"


# ── article versions (superseded mirror states) ──────────────────────

def capture_article_version(conn, row: dict, *, replaced_by_origin,
                            now=None) -> int:
    """Record the FULL pre-update ``zendesk_articles`` row before it is
    superseded. Plain INSERT (never OR REPLACE), then prune beyond
    ``_ARTICLE_VERSION_CAP``. Participates in the caller's open
    transaction (upsert_articles' ``_txn``) — a rolled-back upsert also
    rolls back its capture."""
    aid = int(row["article_id"])
    with _txn(conn):
        cur = conn.execute(
            "INSERT INTO zendesk_article_versions (article_id, content_hash, "
            "title, body_html, body_text, section_id, labels_json, "
            "author_name, draft, outdated, position, updated_at, origin, "
            "replaced_by_origin, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, row.get("content_hash"), row.get("title") or "",
             row.get("body_html"), row.get("body_text"),
             row.get("section_id"), row.get("labels_json") or "[]",
             row.get("author_name"), row.get("draft") or 0,
             row.get("outdated") or 0, row.get("position"),
             row.get("updated_at"), row.get("origin") or "pull",
             replaced_by_origin, now or _now()))
        version_id = int(cur.lastrowid)
        conn.execute(
            "DELETE FROM zendesk_article_versions WHERE article_id=? "
            "AND version_id NOT IN (SELECT version_id "
            "FROM zendesk_article_versions WHERE article_id=? "
            "ORDER BY version_id DESC LIMIT ?)",
            (aid, aid, _ARTICLE_VERSION_CAP))
    return version_id


def list_article_versions(conn, article_id: int, *, limit=50) -> list[dict]:
    """Newest-first version summaries. List rows never carry full bodies —
    only ``chars`` (len of body_text)."""
    rows = conn.execute(
        "SELECT version_id, title, content_hash, origin, replaced_by_origin, "
        "captured_at, updated_at, COALESCE(LENGTH(body_text), 0) AS chars "
        "FROM zendesk_article_versions WHERE article_id=? "
        "ORDER BY version_id DESC LIMIT ?", (article_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_article_version(conn, version_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_article_versions WHERE version_id=?",
        (version_id,)).fetchone()
    return dict(row) if row else None


def restore_article_version(conn, version_id: int, *,
                            author="specialist") -> dict:
    """NON-destructive restore: creates a PENDING draft carrying the
    version's content verbatim (``body_html`` byte-exact -> copy-exact
    restore). NEVER touches ``zendesk_articles``. The rationale being set
    keeps the draft invisible to the native tab's ``include_ai=False``
    lists, so the native Push button stays structurally unreachable."""
    v = get_article_version(conn, version_id)
    if v is None:
        return {"ok": False, "draft_id": None, "error": "version_not_found"}
    from src.data import zendesk_store
    if zendesk_store.get_article(conn, v["article_id"]) is None:
        return {"ok": False, "draft_id": None, "error": "article_gone"}
    date = _display_date(v.get("captured_at"))
    kwargs = dict(
        title=v.get("title") or "",
        body=v.get("body_text") or "",
        body_html=v.get("body_html"),
        article_id=v["article_id"],
        source_ref="version-restore",
        rationale=f"Restored from the version captured {date}.",
        sources_json=[{"ref": f"article-version:{version_id}",
                       "label": "Version history"}])
    # The editing lane adds a keyword-only ``author`` parameter to
    # save_article_draft; until that lands the parameter does not exist.
    # Pass it only when the signature carries it, so this module works on
    # both sides of the landing. (A blanket try/TypeError retry would mask
    # genuine TypeErrors raised inside the store, so inspect instead.)
    try:
        params = inspect.signature(zendesk_store.save_article_draft).parameters
        if "author" in params:
            kwargs["author"] = author
    except (TypeError, ValueError):  # pragma: no cover — C-level callables
        pass
    draft_id = zendesk_store.save_article_draft(conn, **kwargs)
    return {"ok": True, "draft_id": int(draft_id), "error": None}


# ── draft versions (per-save edit history) ───────────────────────────

def capture_draft_version(conn, draft_id: int, *, title, body, body_html,
                          author, save_kind="save", rollback_of=None) -> int:
    """Record an article draft's state as of a body-changing save.
    ``seq = COALESCE(MAX(seq),0)+1`` per draft; prune beyond
    ``_DRAFT_VERSION_CAP``. Caller-transaction participant (the store's
    save/update hooks call this inside their own ``_txn``)."""
    did = int(draft_id)
    with _txn(conn):
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM zendesk_draft_versions "
            "WHERE draft_id=?", (did,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO zendesk_draft_versions (draft_id, seq, title, body, "
            "body_html, author, save_kind, rollback_of, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (did, seq, title, body, body_html, author or "user",
             save_kind, rollback_of, _now()))
        version_id = int(cur.lastrowid)
        conn.execute(
            "DELETE FROM zendesk_draft_versions WHERE draft_id=? "
            "AND version_id NOT IN (SELECT version_id "
            "FROM zendesk_draft_versions WHERE draft_id=? "
            "ORDER BY version_id DESC LIMIT ?)",
            (did, did, _DRAFT_VERSION_CAP))
    return version_id


def list_draft_versions(conn, draft_id: int, *, limit=50) -> list[dict]:
    """Newest-first save summaries — no full bodies, only ``chars``
    (len of the saved markdown body)."""
    rows = conn.execute(
        "SELECT version_id, seq, title, author, save_kind, rollback_of, "
        "created_at, COALESCE(LENGTH(body), 0) AS chars "
        "FROM zendesk_draft_versions WHERE draft_id=? "
        "ORDER BY version_id DESC LIMIT ?", (draft_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_draft_version(conn, version_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM zendesk_draft_versions WHERE version_id=?",
        (version_id,)).fetchone()
    return dict(row) if row else None


def rollback_draft_to_version(conn, draft_id: int, version_id: int, *,
                              author="specialist") -> dict:
    """Roll an article draft back to a recorded save. Pending-only —
    ready/copied/pushed refuse with ``draft_not_editable`` (copied/pushed
    drafts are immutable INCLUDING history rollback). One ``_txn``:
    title/body/body_html restored byte-exact from the version row, then a
    ``save_kind='rollback'`` version recorded with ``rollback_of``. Draft
    status is NEVER written — the lifecycle stays untouched."""
    v = get_draft_version(conn, version_id)
    if v is None or int(v["draft_id"]) != int(draft_id):
        return {"ok": False, "new_version_id": None,
                "error": "version_not_found"}
    from src.data import zendesk_store
    d = zendesk_store.get_article_draft(conn, draft_id)
    if d is None:
        return {"ok": False, "new_version_id": None,
                "error": "draft_not_found"}
    if d.get("status") != "pending":
        return {"ok": False, "new_version_id": None,
                "error": "draft_not_editable"}
    # Drafts table has NOT NULL title/body; a defensively-coalesced value
    # is also what gets recorded so the rollback row matches the draft.
    title = v["title"] if v["title"] is not None else ""
    body = v["body"] if v["body"] is not None else ""
    with _txn(conn):
        conn.execute(
            "UPDATE zendesk_article_drafts SET title=?, body=?, body_html=?, "
            "updated_at=? WHERE id=?",
            (title, body, v["body_html"], _now(), draft_id))
        new_version_id = capture_draft_version(
            conn, draft_id, title=title, body=body,
            body_html=v["body_html"], author=author, save_kind="rollback",
            rollback_of=version_id)
    return {"ok": True, "new_version_id": new_version_id, "error": None}


# ── maintenance (053 post-hook + orphan sweep) ───────────────────────

def seed_draft_baselines(conn) -> int:
    """Seed a seq-1 'create' version from each EXISTING article draft's
    current state so pre-053 drafts have a rollback baseline. Guarded:
    only drafts with ZERO version rows are seeded (re-run safe). Returns
    the number of drafts seeded."""
    seeded = 0
    with _txn(conn):
        rows = conn.execute(
            "SELECT d.id, d.title, d.body, d.body_html, d.source_ref, "
            "d.rationale FROM zendesk_article_drafts d "
            "WHERE NOT EXISTS (SELECT 1 FROM zendesk_draft_versions v "
            "WHERE v.draft_id = d.id) ORDER BY d.id").fetchall()
        for r in rows:
            capture_draft_version(
                conn, int(r[0]), title=r[1], body=r[2], body_html=r[3],
                author=derive_author(r[4], r[5]), save_kind="create")
            seeded += 1
    return seeded


def prune_orphans(conn) -> dict:
    """Delete version rows whose article/draft no longer exists. Called by
    the store after ``delete_draft`` / ``purge_mirror`` — retained
    copied/pushed drafts keep their histories automatically (their rows
    still exist), purged content loses its history."""
    with _txn(conn):
        articles = conn.execute(
            "DELETE FROM zendesk_article_versions WHERE article_id NOT IN "
            "(SELECT article_id FROM zendesk_articles)").rowcount
        drafts = conn.execute(
            "DELETE FROM zendesk_draft_versions WHERE draft_id NOT IN "
            "(SELECT id FROM zendesk_article_drafts)").rowcount
    return {"article_versions": articles, "draft_versions": drafts}
