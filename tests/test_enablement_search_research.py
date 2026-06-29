"""Tests for Renn's search + research chat tools (Guru live search, cross-source research)."""

from __future__ import annotations

from content_update_helpers import FakeGuru

import src.data.chat_tools.enablement_tools as et
import src.data.guru_client as gc


def _patch_guru(monkeypatch, cards):
    class FakeGuruClient(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=cards)

    monkeypatch.setattr(gc, "GuruClient", FakeGuruClient)


def test_search_guru_cards_returns_snippets(empty_db, monkeypatch):
    _patch_guru(monkeypatch, [
        {"id": "c1", "title": "Aetna Copay", "content": "<p>Collect the copay.</p>",
         "collection": "Billing", "collection_id": "col-1"},
        {"id": "c2", "title": "Claims FAQ", "content": "<p>x</p>",
         "collection": "Claims", "collection_id": "col-2"},
    ])
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "copay"}, {})
    assert out["ok"] and out["count"] == 2
    assert out["cards"][0]["card_id"] == "c1"
    assert "Collect the copay" in out["cards"][0]["snippet"]
    assert out["cards"][0]["url"].endswith("/card/c1")  # clickable link for "let me see that card"


def test_search_guru_cards_collection_scope(empty_db, monkeypatch):
    _patch_guru(monkeypatch, [
        {"id": "c1", "title": "Aetna Copay", "content": "<p>x</p>",
         "collection": "Billing", "collection_id": "col-1"},
        {"id": "c2", "title": "Claims FAQ", "content": "<p>x</p>",
         "collection": "Claims", "collection_id": "col-2"},
    ])
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "x", "collections": ["Billing"]}, {})
    assert out["ok"] and out["count"] == 1 and out["cards"][0]["card_id"] == "c1"


def test_search_guru_cards_not_connected(empty_db, monkeypatch):
    class NoCreds(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("", "")

    monkeypatch.setattr(gc, "GuruClient", NoCreds)
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "x"}, {})
    assert not out["ok"] and out["error"] == "guru_not_connected"


def test_research_topic_gathers_sources(empty_db, monkeypatch):
    from src.data import enablement_store as store
    store.save_document(empty_db.conn, source="upload", doc_id="d1",
                        name="Copay policy doc", full_text="copay policy text")
    _patch_guru(monkeypatch, [{"id": "c1", "title": "Aetna Copay", "content": "<p>x</p>"}])
    out = et.handle_research_topic(empty_db.conn, {"topic": "copay"}, {})
    assert out["ok"]
    assert out["guru_cards"] and out["guru_cards"][0]["card_id"] == "c1"
    assert out["documents"] and out["documents"][0]["doc_id"] == "d1"
    assert "ticket_signals" in out  # best-effort source; may be empty on an empty DB


def test_research_topic_tokenized_doc_recall(empty_db, monkeypatch):
    """Multi-word topic finds a doc even when it isn't a contiguous substring."""
    from src.data import enablement_store as store
    store.save_document(empty_db.conn, source="upload", doc_id="d1",
                        name="Aetna Copay Policy Update — Telehealth Waiver",
                        full_text="Telehealth copays are waived.")
    _patch_guru(monkeypatch, [{"id": "c1", "title": "Aetna Copay", "content": "<p>x</p>"}])
    # "aetna copay telehealth" is NOT a substring of the name, but each token is.
    out = et.handle_research_topic(empty_db.conn, {"topic": "Aetna copay telehealth"}, {})
    assert any(d["doc_id"] == "d1" for d in out["documents"])
    assert out["guru_cards"][0].get("url", "").endswith("/card/c1")


def test_open_guru_card_opens_browser(empty_db, monkeypatch):
    import webbrowser
    captured = {}
    monkeypatch.setattr(webbrowser, "open", lambda u: captured.update(url=u) or True)
    out = et.handle_open_guru_card(empty_db.conn,
                                   {"card_ref": "https://app.getguru.com/card/abc123"}, {})
    assert out["ok"] and out["opened"]
    assert captured["url"] == "https://app.getguru.com/card/abc123"


def test_open_guru_card_requires_ref(empty_db):
    out = et.handle_open_guru_card(empty_db.conn, {}, {})
    assert not out["ok"] and out["error"] == "card_ref_required"
