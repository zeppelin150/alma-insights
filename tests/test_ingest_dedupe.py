"""Re-uploading the same local file refreshes its one draft, not a duplicate.

Audit finding 19: _ingest_local_file called save_document with no source_ref,
so a fresh uuid was minted on every upload and draft_card_from_document's
doc_id-keyed idempotency was never reached — two uploads produced two documents
and two drafts. A stable source_ref (the file path) makes a re-upload resolve
to the same document and refresh the same draft.
"""

import pytest

from src.ui.pages.enablement.page import EnablementPage
from src.data import enablement_store as store


def _count_docs(conn):
    return conn.execute("SELECT COUNT(*) FROM enablement_documents").fetchone()[0]


def _count_drafts(conn):
    return conn.execute("SELECT COUNT(*) FROM guru_content_drafts").fetchone()[0]


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_same_file_uploaded_twice_makes_one_document_and_one_draft(
        empty_db, tmp_path):
    conn = empty_db.conn
    path = _write(tmp_path, "policy.md", "# Prior Auth\n\nOriginal content.")

    EnablementPage._ingest_local_file(conn, path)
    EnablementPage._ingest_local_file(conn, path)

    assert _count_docs(conn) == 1, "re-upload created a duplicate document"
    assert _count_drafts(conn) == 1, "re-upload created a duplicate draft"


def test_reupload_after_an_edit_refreshes_the_same_draft(empty_db, tmp_path):
    conn = empty_db.conn
    path = _write(tmp_path, "policy.md", "# Prior Auth\n\nFirst version.")
    first = EnablementPage._ingest_local_file(conn, path)

    # Edit the file in place and re-import to pick up the change.
    _write(tmp_path, "policy.md", "# Prior Auth\n\nSecond version, edited.")
    second = EnablementPage._ingest_local_file(conn, path)

    assert first["draft_id"] == second["draft_id"], \
        "an edit re-import spawned a new draft instead of refreshing the one"
    assert _count_drafts(conn) == 1
    draft = store.get_draft(conn, int(second["draft_id"]))
    assert "Second version" in draft["content"], "the edit was not picked up"


def test_two_different_files_make_two_drafts(empty_db, tmp_path):
    conn = empty_db.conn
    a = _write(tmp_path, "a.md", "# A")
    b = _write(tmp_path, "b.md", "# B")
    EnablementPage._ingest_local_file(conn, a)
    EnablementPage._ingest_local_file(conn, b)
    assert _count_docs(conn) == 2 and _count_drafts(conn) == 2
