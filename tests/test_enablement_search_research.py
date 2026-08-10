"""Tests for Renn's search + research chat tools (Guru live search, cross-source research)."""

from __future__ import annotations

import pytest
from content_update_helpers import FakeGuru

import src.data.chat_tools.enablement_tools as et
import src.data.guru_client as gc


@pytest.fixture(autouse=True)
def _no_configured_scope(monkeypatch):
    """Keep these tests off the machine's real settings — a configured
    ``enablement.guru.search_collections`` default (G2) would silently narrow
    every unscoped search below and make assertions env-dependent."""
    from src.data import settings_manager
    monkeypatch.setattr(settings_manager, "get_section", lambda *a, **k: {})


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


# ── WS-F3 (pilot feedback): scope honesty + collection resolution ────

_CX_CARDS = [
    {"id": "c1", "title": "Payer credentialing status", "content": "<p>BCBSMA</p>",
     "collection": "CX Team", "collection_id": "col-cx"},
    {"id": "c2", "title": "Activation guide", "content": "<p>x</p>",
     "collection": "Activation", "collection_id": "col-act"},
]


def _patch_guru_with_collections(monkeypatch, cards, cols):
    class FakeGuruClient(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=cards)

        def list_collections(self):
            return cols

    monkeypatch.setattr(gc, "GuruClient", FakeGuruClient)


def test_search_scope_fields_always_present(empty_db, monkeypatch):
    _patch_guru(monkeypatch, _CX_CARDS)
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "credentialing"}, {})
    assert out["ok"]
    assert out["pre_filter_count"] == 2 and out["post_filter_count"] == 2
    # The literal query always runs first; further variants depend on the
    # entity dictionaries, so assert the invariant, not the exact list.
    assert out["variants_run"][0] == "credentialing"
    assert set(out["collections_seen"]) == {"CX Team", "Activation"}
    assert out["scope"] == {"kind": "all_collections"}
    assert "why_zero" not in out


def test_collection_substring_resolution(empty_db, monkeypatch):
    """The pilot's exact failure: 'CX' must find the 'CX Team' collection —
    the old exact-lowercase filter silently dropped everything."""
    _patch_guru_with_collections(
        monkeypatch, _CX_CARDS,
        [{"id": "col-cx", "name": "CX Team"}, {"id": "col-act", "name": "Activation"}])
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "credentialing", "collections": ["CX"]}, {})
    assert out["ok"] and out["total"] == 1
    assert out["cards"][0]["card_id"] == "c1"
    assert out["scope"]["resolved"] == [{"id": "col-cx", "name": "CX Team"}]
    assert out["scope"]["unresolved"] == []


def test_unknown_collection_reported_not_silent(empty_db, monkeypatch):
    _patch_guru_with_collections(
        monkeypatch, _CX_CARDS,
        [{"id": "col-cx", "name": "CX Team"}, {"id": "col-act", "name": "Activation"}])
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "credentialing", "collections": ["Actvation"]}, {})
    assert out["ok"] and out["total"] == 0
    assert out["why_zero"] == "collection_not_found"
    assert out["scope"]["unresolved"][0]["requested"] == "Actvation"
    assert out["scope"]["unresolved"][0]["closest"] == "Activation"
    assert "Actvation" in out["message"] and "Activation" in out["message"]


def test_why_zero_filtered_by_collection(empty_db, monkeypatch):
    _patch_guru_with_collections(
        monkeypatch, _CX_CARDS,
        [{"id": "col-cx", "name": "CX Team"}, {"id": "col-act", "name": "Activation"},
         {"id": "col-bill", "name": "Billing"}])
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "credentialing", "collections": ["Billing"]}, {})
    assert out["ok"] and out["total"] == 0
    assert out["why_zero"] == "filtered_by_collection"
    assert out["pre_filter_count"] == 2 and out["post_filter_count"] == 0


def test_why_zero_no_match(empty_db, monkeypatch):
    _patch_guru(monkeypatch, [])
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "unicorn"}, {})
    assert out["ok"] and out["total"] == 0
    assert out["why_zero"] == "no_match"


def test_collection_list_unavailable_degrades_with_note(empty_db, monkeypatch):
    class Failing(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=_CX_CARDS)

        def list_collections(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(gc, "GuruClient", Failing)
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "x", "collections": ["CX Team"]}, {})
    # Exact name still matches on the legacy path; the degradation is stated.
    assert out["ok"] and out["total"] == 1
    assert "collection list unavailable" in out["scope"]["note"]


def test_not_connected_carries_why_zero(empty_db, monkeypatch):
    class NoCreds(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("", "")

    monkeypatch.setattr(gc, "GuruClient", NoCreds)
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "x"}, {})
    assert not out["ok"] and out["why_zero"] == "not_connected"


# ── WS-F2: bounded variants, union+rank, content-scan · G2: default scope ──


def test_query_variants_bounded_and_deterministic():
    from src.data.query_expand import query_variants
    q = "Alma is not currently credentialing with BCBSMA"
    v1 = query_variants(q)
    v2 = query_variants(q)
    assert v1 == v2 and len(v1) <= 5
    assert v1[0] == q                       # literal always first
    assert "alma credentialing bcbsma" in v1  # stopword-stripped key terms
    assert any(x.startswith("bcbs ") for x in v1)  # alias reorientation
    assert "alma credentialing" in v1       # topic-only (entity dropped)


def test_variant_union_finds_card_the_literal_query_misses(empty_db, monkeypatch):
    """The BCBSMA failure mode: the literal sentence matches nothing; a
    reoriented variant (negation-free key terms) finds the card."""
    needle = {"id": "c-needle", "title": "Payer credentialing status",
              "content": "<p>Alma is not credentialing with BCBSMA.</p>",
              "collection": "CX Team", "collection_id": "col-cx"}

    class VariantGuru(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=[needle])

        def search_cards(self, query):
            if "not" in (query or "").lower():
                return []          # the literal sentence finds nothing
            return [dict(needle)]

    monkeypatch.setattr(gc, "GuruClient", VariantGuru)
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "Alma is not currently credentialing with BCBSMA"}, {})
    assert out["ok"] and out["total"] == 1
    assert out["cards"][0]["card_id"] == "c-needle"
    assert len(out["variants_run"]) >= 2


def test_content_scan_finds_body_only_fact(empty_db, monkeypatch):
    """A generically-titled card whose BODY holds the fact is unreachable by
    every search variant — the scoped content scan is the backstop."""
    needle = {"id": "c-body", "title": "Payer status overview",
              "content": "<p>Alma is not currently credentialing with BCBSMA.</p>",
              "collection": "CX Team", "collection_id": "col-cx"}

    class ScanGuru(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=[])

        def search_cards(self, query):
            return []

        def list_collections(self):
            return [{"id": "col-cx", "name": "CX Team"}]

        def list_cards(self, collection_id=None, **kw):
            return [dict(needle)] if collection_id == "col-cx" else []

    monkeypatch.setattr(gc, "GuruClient", ScanGuru)
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "bcbsma credentialing", "collections": ["CX"]}, {})
    assert out["ok"] and out["total"] == 1
    assert out["cards"][0]["card_id"] == "c-body"
    scan = out["scope"]["content_scan"]
    assert scan["ran"] is True and scan["cards_scanned"] == 1


def test_strong_literal_short_circuits_variants(empty_db, monkeypatch):
    calls = []

    class CountingGuru(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=[])

        def search_cards(self, query):
            calls.append(query)
            return [{"id": f"c{i}", "title": "copay collection policy",
                     "content": "<p>copay collection policy</p>",
                     "collection": "Billing", "collection_id": "col-1"}
                    for i in range(3)]

    monkeypatch.setattr(gc, "GuruClient", CountingGuru)
    out = et.handle_search_guru_cards(
        empty_db.conn, {"query": "the copay collection policy"}, {})
    assert out["ok"] and out["total"] == 3
    assert calls == ["the copay collection policy"]  # variants never ran
    assert out["variants_run"] == calls


def test_configured_default_scope_applies_and_is_reported(empty_db, monkeypatch):
    """G2: with no collections arg, the operator's configured default scope
    narrows the search — visibly, never silently."""
    from src.data import settings_manager
    monkeypatch.setattr(
        settings_manager, "get_section",
        lambda *a, **k: {"guru": {"search_collections": [
            {"id": "col-cx", "name": "CX Team"}]}})
    _patch_guru_with_collections(
        monkeypatch, _CX_CARDS,
        [{"id": "col-cx", "name": "CX Team"}, {"id": "col-act", "name": "Activation"}])
    out = et.handle_search_guru_cards(empty_db.conn, {"query": "credentialing"}, {})
    assert out["ok"] and out["total"] == 1
    assert out["cards"][0]["card_id"] == "c1"
    assert out["scope"]["source"] == "configured_default"
    assert out["scope"]["resolved"] == [{"id": "col-cx", "name": "CX Team"}]


def test_unified_search_merge_is_coverage_ranked(empty_db, monkeypatch):
    """search_content: the best-covering row tops the list even when its
    source is later in the fan-out order (old behavior: round-robin)."""
    _patch_guru(monkeypatch, [
        {"id": "g1", "title": "Unrelated card", "content": "<p>nothing here</p>",
         "collection": "Billing", "collection_id": "col-1"}])
    from src.data import enablement_store as store
    store.save_document(empty_db.conn, source="drive", doc_id="d1",
                        name="BCBSMA credentialing status",
                        full_text="Alma is not currently credentialing with BCBSMA.")
    out = et.handle_search_content(
        empty_db.conn, {"query": "bcbsma credentialing", "sources": ["guru", "drive"]}, {})
    assert out["ok"] and out["count"] >= 1
    assert out["results"][0]["source"] == "drive"  # coverage beat fan-out order
