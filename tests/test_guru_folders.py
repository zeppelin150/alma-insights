"""Phase 1 — Guru folders (the sub-folder model that replaced Boards in 2022).

Covers GuruClient.list_folders / get_folder_items and folder-targeted create_card
(POST /cards/extended with folderIds), publish_draft threading folder_id, and the
list_guru_folders / push_guru_draft chat tools' folder targeting.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.data.guru_client import GuruClient


def _client(request_return):
    c = GuruClient("e@x.com", "tok")
    c._request = MagicMock(return_value=request_return)
    return c


def test_list_folders_parses_and_filters_by_collection():
    rows = [
        {"id": "f1", "title": "Home", "slug": "s1", "home": True, "numberOfFacts": 3,
         "collection": {"id": "c1", "name": "Templates"}},
        {"id": "f2", "title": "Onboarding", "slug": "s2", "home": False,
         "numberOfFacts": 5, "collection": {"id": "c1", "name": "Templates"}},
    ]
    c = _client(rows)
    out = c.list_folders("c1")
    assert c._request.call_args[0][1] == "/folders?collection=c1"
    assert out[0] == {"id": "f1", "title": "Home", "slug": "s1", "home": True,
                      "item_count": 3, "collection_id": "c1",
                      "collection_name": "Templates"}
    assert out[1]["home"] is False


def test_list_folders_no_collection_uses_bare_path():
    c = _client([])
    c.list_folders()
    assert c._request.call_args[0][1] == "/folders"


def test_get_folder_items_normalizes_cards_and_subfolders():
    items = [
        {"id": "card1", "itemId": "i1", "type": "card", "preferredPhrase": "How to X"},
        {"id": "fold1", "itemId": "i2", "type": "folder", "title": "Subfolder"},
    ]
    c = _client(items)
    out = c.get_folder_items("f1")
    assert c._request.call_args[0][1] == "/folders/f1/items"
    assert out[0] == {"id": "card1", "item_id": "i1", "type": "card", "title": "How to X"}
    assert out[1]["type"] == "folder" and out[1]["title"] == "Subfolder"


def test_create_card_no_folder_uses_plain_endpoint():
    c = _client({"id": "newcard"})
    c.create_card("c1", "Title", "<p>body</p>")
    assert (c._request.call_args[0][0], c._request.call_args[0][1]) == ("POST", "/cards")
    body = c._request.call_args[1]["body"]
    assert "folderIds" not in body
    assert body["collection"] == {"id": "c1"}


def test_create_card_with_folder_uses_extended_and_folderids():
    c = _client({"id": "newcard"})
    c.create_card("c1", "Title", "<p>body</p>", folder_ids=["f2", ""])
    assert (c._request.call_args[0][0], c._request.call_args[0][1]) == ("POST", "/cards/extended")
    body = c._request.call_args[1]["body"]
    assert body["folderIds"] == ["f2"]        # empties filtered out
    assert body["collection"] == {"id": "c1"}
    assert body["preferredPhrase"] == "Title"


def test_publish_draft_threads_folder_to_create_card(empty_db):
    from src.data import enablement_store as store
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content="hello body")
    gc = MagicMock()
    gc.create_card.return_value = {"id": "card-new"}
    res = store.publish_draft(conn, did, guru_client=gc,
                              collection_id="c1", folder_id="f2")
    assert res["ok"]
    assert gc.create_card.call_args.kwargs.get("folder_ids") == ["f2"]


def test_publish_draft_captures_card_id_on_folder_path(empty_db):
    # Regression: folder-publish path must record the created card's id so a later
    # edit UPDATES the same card instead of creating a duplicate.
    from src.data import enablement_store as store
    conn = empty_db.conn
    did = store.save_card_draft(conn, title="T", content="body")
    gc = MagicMock()
    gc.create_card.return_value = {"id": "card-folder-99"}
    res = store.publish_draft(conn, did, guru_client=gc, collection_id="c1", folder_id="f2")
    assert res["ok"] and res["card_id"] == "card-folder-99"
    assert store.get_draft(conn, did)["card_id"] == "card-folder-99"   # linked for updates


# ── chat-tool wiring (provider-agnostic impls) ───────────────────────

class _FakeGuru:
    @staticmethod
    def load_credentials():
        return ("e@x.com", "tok")

    def __init__(self, *a, **k):
        pass

    def list_collections(self):
        return [{"id": "c1", "name": "Templates"}]

    def list_folders(self, collection_id=None):
        assert collection_id == "c1"
        return [{"id": "f1", "title": "Home", "slug": "s", "home": True,
                 "item_count": 2, "collection_id": "c1", "collection_name": "Templates"}]


def test_list_guru_folders_tool_resolves_collection_name(monkeypatch, empty_db):
    monkeypatch.setattr("src.data.guru_client.GuruClient", _FakeGuru)
    from src.data.chat_tools.enablement_tools import _list_guru_folders_impl
    out = _list_guru_folders_impl(empty_db.conn, "Templates")   # by NAME
    assert out["ok"] and out["collection_id"] == "c1"
    assert out["folders"][0]["title"] == "Home" and out["folders"][0]["home"] is True


def test_push_guru_draft_tool_threads_folder(monkeypatch, empty_db):
    from src.data import enablement_store as store
    did = store.save_card_draft(empty_db.conn, title="T", content="body")
    store.approve_draft(empty_db.conn, did, approved_by="reviewer")  # M5: sign-off gate
    captured = {}

    def fake_publish(conn, draft_id, *, guru_client=None, collection_id=None,
                     folder_id=None, **kw):
        captured.update(collection_id=collection_id, folder_id=folder_id)
        return {"ok": True}

    monkeypatch.setattr("src.data.enablement_store.publish_draft", fake_publish)
    monkeypatch.setattr("src.data.guru_client.GuruClient.load_credentials",
                        staticmethod(lambda: ("", "")))
    from src.data.chat_tools.enablement_tools import _push_guru_draft_impl
    _push_guru_draft_impl(empty_db.conn, did, "c1", "f2")
    assert captured == {"collection_id": "c1", "folder_id": "f2"}
