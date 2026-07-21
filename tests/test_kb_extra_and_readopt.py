"""KB fixes 17 (preserve hand-added header fields) and 18 (re-adopt a restored
quarantined EC folder).

17: a custom frontmatter key on a card must survive the DB round-trip and be
written back on the next push, instead of being dropped because kb_cards had no
column for it.

18: once the EC folder is quarantined (trashed/unwritable), re-bootstrap must
re-verify and re-adopt it when it is healthy again, rather than minting a
brand-new EC folder and orphaning the restored one.
"""

import pytest

from src.data.kb import store, sync, card_format, drive_kb


# ── 17: header passthrough ─────────────────────────────────────────────

def test_migration_049_added_extra_json(empty_db):
    cols = {r[1] for r in empty_db.conn.execute(
        "PRAGMA table_info(kb_cards)").fetchall()}
    assert "extra_json" in cols


def test_unknown_header_field_survives_the_db_roundtrip(empty_db):
    conn = empty_db.conn
    # A card pulled from Drive with a human's own frontmatter key.
    meta = {"card_id": "kb-1", "title": "Policy", "type": "source_summary",
            "topics": ["billing"], "source_id": "drive:1",
            "owner": "j.rivera", "review_cycle": "quarterly"}
    store.upsert_card(conn, meta, "body text",
                      extra=card_format.extract_extra(meta))
    card = store.get_card(conn, "kb-1")
    assert card["extra"] == {"owner": "j.rivera", "review_cycle": "quarterly"}


def test_extra_fields_are_serialized_back_into_the_card(empty_db):
    conn = empty_db.conn
    meta = {"card_id": "kb-2", "title": "Policy", "type": "source_summary",
            "source_id": "drive:2", "owner": "m.chen"}
    store.upsert_card(conn, meta, "body",
                      extra=card_format.extract_extra(meta))
    card = store.get_card(conn, "kb-2")
    # _push_one merges extra into meta_out; serialize_card writes it.
    meta_out = {k: card.get(k) for k in ("card_id", "title", "type", "source_id")}
    for k, v in (card.get("extra") or {}).items():
        meta_out.setdefault(k, v)
    text = card_format.serialize_card(meta_out, card.get("body_md") or "")
    assert "owner: m.chen" in text


def test_regeneration_without_extra_preserves_stored_extra(empty_db):
    """A source-update path rebuilds meta from the canonical columns and calls
    upsert_card with no extra — it must NOT wipe the human's fields."""
    conn = empty_db.conn
    meta = {"card_id": "kb-3", "title": "P", "type": "source_summary",
            "source_id": "drive:3", "owner": "a.osei"}
    store.upsert_card(conn, meta, "v1", extra=card_format.extract_extra(meta))
    # Regenerate (no extra passed) — the summary changed.
    store.upsert_card(conn, {"card_id": "kb-3", "title": "P",
                             "type": "source_summary", "source_id": "drive:3",
                             "summary": "new"}, "v2")
    card = store.get_card(conn, "kb-3")
    assert card["extra"] == {"owner": "a.osei"}, "regeneration wiped extra fields"


def test_editing_an_extra_field_is_detected_as_a_change(empty_db):
    conn = empty_db.conn
    base = {"card_id": "kb-4", "title": "P", "type": "source_summary",
            "source_id": "drive:4"}
    h1 = store.content_hash(base, "body", extra={"owner": "x"})
    h2 = store.content_hash(base, "body", extra={"owner": "y"})
    assert h1 != h2, "a changed custom field must change the content hash"


# ── 18: quarantine re-adoption ─────────────────────────────────────────

class _Exporter:
    def __init__(self):
        self.folders = {}
        self.files = {}
        self.n = 0
        self.created = 0

    def create_folder(self, name, parent_id=None, *, app_properties=None):
        self.n += 1
        self.created += 1
        fid = f"fold{self.n}"
        self.folders[fid] = {"trashed": False, "canAddChildren": True}
        return {"id": fid, "name": name}

    def get_file_meta(self, file_id, fields=""):
        obj = self.folders.get(file_id) or self.files.get(file_id)
        if obj is None:
            raise RuntimeError("404")
        return {"id": file_id, "trashed": obj.get("trashed", False),
                "capabilities": {"canAddChildren": obj.get("canAddChildren", True)}}

    def upload_file(self, filename, data, mime_type, folder_id=None,
                    *, app_properties=None):
        self.n += 1
        fid = f"file{self.n}"
        self.files[fid] = {"folder": folder_id}
        return {"id": fid, "name": filename}

    def update_file(self, file_id, content, mime_type="text/markdown"):
        return {"id": file_id}


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_settings_path", lambda: tmp_path / "settings.yaml")


def test_restored_quarantined_folder_is_readopted_not_recreated(
        empty_db, settings_file):
    conn = empty_db.conn
    ex = _Exporter()
    # Bootstrap an EC root.
    first = drive_kb.ensure_ec_root(conn, exporter=ex)
    assert first["ok"]
    root = first["folder_id"]
    assert ex.created == 1

    # Trash it → next verify quarantines it.
    ex.folders[root]["trashed"] = True
    verdict = drive_kb.ensure_ec_root(conn, exporter=ex)
    assert not verdict["ok"]
    assert conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                        (root,)).fetchone()[0] == "quarantined"

    # Operator restores it in Drive; re-bootstrap must RE-ADOPT, not recreate.
    ex.folders[root]["trashed"] = False
    again = drive_kb.ensure_ec_root(conn, exporter=ex)
    assert again["ok"] and again["folder_id"] == root, "did not re-adopt"
    assert ex.created == 1, "a duplicate EC folder was created"
    assert conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                        (root,)).fetchone()[0] == "ok"


def test_still_trashed_folder_is_not_readopted(empty_db, settings_file):
    conn = empty_db.conn
    ex = _Exporter()
    first = drive_kb.ensure_ec_root(conn, exporter=ex)
    root = first["folder_id"]
    ex.folders[root]["trashed"] = True
    drive_kb.ensure_ec_root(conn, exporter=ex)   # quarantines

    # Still trashed → re-adopt must decline, and a fresh folder is created.
    again = drive_kb.ensure_ec_root(conn, exporter=ex)
    assert again["ok"] and again["folder_id"] != root
    assert ex.created == 2, "expected a new EC folder when the old stays trashed"
