"""Workbench imports — store-level: Guru card → linked draft, Drive doc →
stored document, ref parsing, publish-updates-not-creates."""

from src.data import enablement_store as store


class FakeGuru:
    def __init__(self):
        self.updated = []
        self.created = []

    def get_card(self, card_id):
        if card_id == "missing":
            return {}
        return {
            "id": card_id,
            "title": "SSO Setup",
            "content": "<h2>Steps</h2><ol><li>Open console</li></ol>",
        }

    def update_card(self, card_id, content, title=None):
        self.updated.append((card_id, title))
        return {"id": card_id}

    def create_card(self, collection_id, title, content):
        self.created.append(title)
        return {"id": "brand-new"}


class FakeDrive:
    def get_file(self, file_id):
        if file_id == "missing":
            return {}
        return {"id": file_id, "name": "Pricing Update.gdoc",
                "mime_type": "application/vnd.google-apps.document",
                "url": f"https://docs.google.com/document/d/{file_id}",
                "modified_time": "2026-06-10T00:00:00Z"}

    def export_text(self, file_id, mime_type):
        return "Pricing tiers change Aug 1, 2026."


class TestRefParsing:
    def test_guru_url(self):
        assert store.parse_guru_card_ref(
            "https://app.getguru.com/card/Tbgqkfdc/My-Card-Title"
        ) == "Tbgqkfdc"

    def test_guru_raw_id(self):
        assert store.parse_guru_card_ref("abc123") == "abc123"

    def test_drive_url_forms(self):
        fid = "1A2b3C4d5E6f7G8h9I0jKLMNOPqrstuv"
        assert store.parse_drive_file_ref(
            f"https://docs.google.com/document/d/{fid}/edit"
        ) == fid
        assert store.parse_drive_file_ref(
            f"https://drive.google.com/open?id={fid}"
        ) == fid
        assert store.parse_drive_file_ref(fid) == fid


class TestGuruImport:
    def test_import_links_for_update(self, empty_db):
        conn = empty_db.conn
        res = store.import_guru_card_to_draft(conn, FakeGuru(), "card-77")
        assert res["ok"]
        draft = store.get_draft(conn, res["draft_id"])
        assert draft["card_id"] == "card-77"
        assert draft["draft_type"] == "card_update"
        assert draft["source_ref"] == "guru:card-77"
        assert "## Steps" in draft["content"]
        # Qt's toMarkdown emits "1.  item" (two spaces); the stdlib
        # fallback emits "1. item" — accept either.
        assert "1." in draft["content"] and "Open console" in draft["content"]

    def test_publish_updates_not_creates(self, empty_db):
        conn = empty_db.conn
        guru = FakeGuru()
        res = store.import_guru_card_to_draft(conn, guru, "card-88")
        out = store.publish_draft(conn, res["draft_id"], guru_client=guru)
        assert out["ok"]
        assert guru.updated and guru.updated[0][0] == "card-88"
        assert not guru.created

    def test_missing_card(self, empty_db):
        res = store.import_guru_card_to_draft(empty_db.conn, FakeGuru(), "missing")
        assert not res["ok"]
        assert "card_not_found" in res["error"]


class TestDriveImport:
    def test_import_saves_document(self, empty_db):
        conn = empty_db.conn
        res = store.import_drive_doc(conn, FakeDrive(), "file-abc")
        assert res["ok"]
        doc = store.get_document(conn, res["doc_id"])
        assert doc["name"] == "Pricing Update.gdoc"
        assert "Aug 1, 2026" in doc["full_text"]
        assert doc["source_ref"] == "file-abc"

    def test_reimport_is_idempotent(self, empty_db):
        conn = empty_db.conn
        a = store.import_drive_doc(conn, FakeDrive(), "file-xyz")
        b = store.import_drive_doc(conn, FakeDrive(), "file-xyz")
        assert a["doc_id"] == b["doc_id"]
        n = conn.execute(
            "SELECT COUNT(*) FROM enablement_documents WHERE doc_id=?",
            (a["doc_id"],),
        ).fetchone()[0]
        assert n == 1

    def test_missing_file(self, empty_db):
        res = store.import_drive_doc(empty_db.conn, FakeDrive(), "missing")
        assert not res["ok"]
