"""Coverage for reference/source resolution (refs.py + load_source source_doc_ref)."""

from __future__ import annotations

from content_update_helpers import FakeDrive, FakeGuru

from src.data.content_update.load_source import load_source
from src.data.content_update.models import ContentUpdateRequest
from src.data.content_update.refs import resolve_ref

_DRIVE_ID = "A" * 20


def test_resolve_ref_guru_card(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay", "content": "<p>body text</p>"}])
    sd = resolve_ref(empty_db.conn, "https://app.getguru.com/card/c1", guru_client=guru)
    assert sd is not None and sd.ref == "guru:c1" and "body text" in sd.text


def test_resolve_ref_local_file(tmp_path, empty_db):
    p = tmp_path / "source.md"
    p.write_text("file source body", encoding="utf-8")
    sd = resolve_ref(empty_db.conn, str(p))
    assert sd is not None and sd.ref.startswith("file:") and sd.text == "file source body"


def test_resolve_ref_stored_doc(empty_db):
    from src.data import enablement_store as store
    store.save_document(empty_db.conn, source="manual", name="D", doc_id="d9",
                        full_text="stored body")
    sd = resolve_ref(empty_db.conn, "d9")
    assert sd is not None and sd.ref == "doc:d9" and sd.text == "stored body"


def test_resolve_ref_from_drive(empty_db):
    drive = FakeDrive(
        files={_DRIVE_ID: {"name": "Policy", "mime_type": "application/vnd.google-apps.document"}},
        texts={_DRIVE_ID: "drive policy text"},
    )
    url = f"https://docs.google.com/document/d/{_DRIVE_ID}/edit"
    sd = resolve_ref(empty_db.conn, url, drive_reader=drive)
    assert sd is not None and sd.ref.startswith("drive:") and sd.text == "drive policy text"


def test_resolve_ref_unresolvable_returns_none(empty_db):
    assert resolve_ref(empty_db.conn, "nonexistent-thing") is None


def test_load_source_from_source_doc_ref(empty_db):
    from src.data import enablement_store as store
    store.save_document(empty_db.conn, source="manual", name="D", doc_id="d10",
                        full_text="doc primary body")
    bundle = load_source(empty_db.conn, ContentUpdateRequest(source_doc_ref="d10"))
    assert bundle.ok and bundle.primary.text == "doc primary body"
