"""Tests for the torch-free content catalog (summary index + hybrid search) + tools."""

from __future__ import annotations

from src.data.content_catalog import indexer, store
from src.data.content_catalog.models import CatalogEntry
from src.data.content_catalog.search import rank_entries
from src.data.content_catalog.summarize import summarize_item
from src.data.content_catalog.vectorizer import (
    compute_idf, cosine, tfidf_vector, tokenize,
)


def _entry(iid, summary, topics=()):
    return CatalogEntry(item_id=iid, item_type="card",
                        title="[Renn] Aetna Copay — Agent Guide", source="guru",
                        summary=summary, topics=list(topics))


# ── vectoriser ──

def test_vectorizer_cosine_ranks_overlap_higher():
    idf = compute_idf([tokenize("telehealth copay waiver"), tokenize("refund overpayment charge")])
    a = tfidf_vector(tokenize("telehealth waiver"), idf)
    b = tfidf_vector(tokenize("telehealth waiver"), idf)
    c = tfidf_vector(tokenize("refund charge"), idf)
    assert cosine(a, b) > 0.99
    assert cosine(a, c) < cosine(a, b)


# ── ranker: the look-alike-titles case ──

def test_rank_disambiguates_identical_titles_by_content():
    entries = [
        _entry("telehealth", "Telehealth copays are waived for virtual visits effective 2026-07-01.", ["telehealth", "waiver"]),
        _entry("refund", "How to refund an overpayment or duplicate copay charge.", ["refund"]),
        _entry("eligibility", "Verify the patient's coverage and benefits before service.", ["eligibility"]),
    ]
    top = rank_entries("telehealth copay waiver", entries, limit=3)
    assert top[0].entry.item_id == "telehealth"
    assert "telehealth" in top[0].matched_terms and top[0].score > 0


def test_rank_empty_inputs():
    assert rank_entries("", [_entry("a", "x")]) == []
    assert rank_entries("x", []) == []


# ── store + .md ──

def test_store_roundtrip_and_md_export(empty_db, tmp_path):
    conn = empty_db.conn
    store.upsert_entry(conn, _entry("doc:1", "Telehealth waiver policy", ["telehealth"]))
    got = store.get_entry(conn, "doc:1")
    assert got and got.summary == "Telehealth waiver policy" and got.topics == ["telehealth"]
    assert len(store.all_entries(conn)) == 1
    path = store.write_catalog_md(conn, str(tmp_path / "cat.md"))
    text = open(path, encoding="utf-8").read()
    assert "Telehealth waiver policy" in text and "doc:1" in text


# ── indexer ──

def test_indexer_incremental_skip_and_reindex(empty_db):
    conn = empty_db.conn
    items = [{"item_id": "d1", "item_type": "doc", "title": "Doc", "text": "telehealth waiver content"}]
    assert indexer.index_items(conn, items) == {"indexed": 1, "skipped": 0, "total": 1}
    assert indexer.index_items(conn, items) == {"indexed": 0, "skipped": 1, "total": 1}
    items[0]["text"] = "telehealth waiver content UPDATED"
    assert indexer.index_items(conn, items)["indexed"] == 1


# ── summarize ──

def test_summarize_fallback_no_llm():
    s = summarize_item(None, "T", "  some   body  text here ")
    assert s["summary"] == "some body text here" and s["topics"] == []


def test_summarize_with_llm():
    class S:
        def generate(self, prompt, system_prompt="", timeout=120):
            return '```json\n{"summary":"Telehealth waiver policy.","topics":["telehealth","waiver"]}\n```'
    s = summarize_item(S(), "T", "body")
    assert s["summary"].startswith("Telehealth") and "telehealth" in s["topics"]


# ── tools ──

def test_index_and_search_content_tools(empty_db, monkeypatch):
    import src.data.chat_tools.enablement_tools as et
    import src.gemini.client_factory as cf
    from src.data import enablement_store as es
    conn = empty_db.conn
    es.save_document(conn, source="upload", doc_id="t1", name="Aetna Copay Note A",
                     full_text="Telehealth copays are waived for virtual visits.")
    es.save_document(conn, source="upload", doc_id="t2", name="Aetna Copay Note B",
                     full_text="Refund an overpayment or duplicate copay charge.")
    monkeypatch.setattr(cf, "build_client_for_task", lambda task, use_bridge=False: None)
    idx = et.handle_index_content(conn, {"scope": "docs"}, {})
    assert idx["ok"] and idx["indexed"] == 2
    res = et.handle_search_content(conn, {"query": "telehealth waiver"}, {})
    assert res["ok"] and res["count"] >= 1 and res["results"][0]["item_id"] == "doc:t1"


def test_search_content_empty_catalog(empty_db):
    import src.data.chat_tools.enablement_tools as et
    out = et.handle_search_content(empty_db.conn, {"query": "x"}, {})
    assert out["ok"] and out["count"] == 0 and "index_content" in out["note"]
