"""Tests for catalog-backed card selection + multi-card fan-out."""

from __future__ import annotations

from content_update_helpers import FakeGuru, StubLLM

from src.data.content_catalog import store as cat_store
from src.data.content_catalog.models import CatalogEntry
from src.data.content_update import (
    ContentUpdateRequest, Deps, find_card_candidates, run_fanout_update,
)


def _seed_card_entry(conn, cid, title, summary, topics=()):
    cat_store.upsert_entry(conn, CatalogEntry(
        item_id=f"card:{cid}", item_type="card", title=title, source="guru",
        summary=summary, topics=list(topics)))


class _NoChangeLLM:
    def generate(self, prompt, system_prompt="", timeout=120):
        if "JSON object" in prompt:
            return '```json\n{"summary":"already current","changes":[]}\n```'
        return "TITLE: X\n---\nbody"


# ── candidate finder ──

def test_find_candidates_catalog_first(empty_db):
    conn = empty_db.conn
    _seed_card_entry(conn, "c1", "Copay policy", "telehealth copay waiver virtual visits", ["telehealth"])
    _seed_card_entry(conn, "c2", "Refund policy", "refund an overpayment", ["refund"])
    cands = find_card_candidates(conn, "telehealth waiver", guru_client=FakeGuru(cards=[]))
    assert cands and cands[0]["card_id"] == "c1" and cands[0]["via"] == "catalog"


def test_find_candidates_guru_fallback(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Telehealth copay", "content": "x"},
                           {"id": "c2", "title": "Refund", "content": "x"}])
    cands = find_card_candidates(empty_db.conn, "telehealth", guru_client=guru)
    assert cands and cands[0]["via"] == "guru" and cands[0]["card_id"] == "c1"


# ── fan-out ──

def test_fanout_stages_each_changed_card(empty_db):
    conn = empty_db.conn
    _seed_card_entry(conn, "c1", "Copay policy", "telehealth copay waiver", ["telehealth"])
    _seed_card_entry(conn, "c2", "Eligibility", "verify coverage copay before service", ["eligibility"])
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy", "content": "<p>collect the copay</p>"},
                           {"id": "c2", "title": "Eligibility", "content": "<p>verify coverage</p>"}])
    deps = Deps(llm_client=StubLLM(title="Updated", body="Telehealth copays are waived. " * 8),
                guru_client=guru)
    req = ContentUpdateRequest(source_text="Telehealth copays are waived.", search_query="telehealth copay")
    res = run_fanout_update(conn, req, deps, max_cards=5)

    assert res["ok"] and res["staged"] >= 1
    assert all(c.get("via") == "catalog" for c in res["cards"])
    from src.data import enablement_store as es
    for c in [c for c in res["cards"] if c["status"] == "staged"]:
        assert c["draft_id"] > 0 and es.get_draft(conn, c["draft_id"])["card_id"] == c["card_id"]
        assert c["diff"]


def test_fanout_no_changes_creates_no_draft(empty_db):
    conn = empty_db.conn
    _seed_card_entry(conn, "c1", "Copay", "telehealth copay", [])
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay", "content": "<p>x</p>"}])
    res = run_fanout_update(conn, ContentUpdateRequest(source_text="x", search_query="telehealth copay"),
                            Deps(llm_client=_NoChangeLLM(), guru_client=guru))
    assert res["ok"] and res["staged"] == 0
    assert res["cards"][0]["status"] == "no_changes"
    assert conn.execute("SELECT COUNT(*) FROM guru_content_drafts").fetchone()[0] == 0


def test_fanout_no_source_fails(empty_db):
    res = run_fanout_update(empty_db.conn, ContentUpdateRequest(),
                            Deps(llm_client=StubLLM(), guru_client=FakeGuru()))
    assert not res["ok"] and res["stage"] == "load_source"


def test_fanout_no_candidates_fails(empty_db):
    res = run_fanout_update(empty_db.conn,
                            ContentUpdateRequest(source_text="x", search_query="nothing matches"),
                            Deps(llm_client=StubLLM(), guru_client=FakeGuru(cards=[])))
    assert not res["ok"] and res["stage"] == "find_cards"


# ── tool ──

def test_update_cards_from_doc_tool(empty_db, monkeypatch):
    import src.data.chat_tools.enablement_tools as et
    import src.data.guru_client as gc
    import src.gemini.client_factory as cf
    from src.data import enablement_store as es

    conn = empty_db.conn
    es.save_document(conn, source="upload", doc_id="d1", name="Telehealth policy",
                     full_text="Telehealth copays are waived.")
    _seed_card_entry(conn, "c1", "Copay", "telehealth copay", [])

    class FakeGuruClient(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=[{"id": "c1", "title": "Copay", "content": "<p>collect copay</p>"}])

    monkeypatch.setattr(gc, "GuruClient", FakeGuruClient)
    monkeypatch.setattr(cf, "build_client_for_task",
                        lambda task, use_bridge=False: StubLLM(title="Copay", body="Telehealth waived " * 8))
    out = et.handle_update_cards_from_doc(conn, {"doc_query": "telehealth", "search": "telehealth copay"}, {})
    assert out["ok"] and out["staged"] >= 1
