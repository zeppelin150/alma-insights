"""Regression tests for the full content_update pipeline + publish."""

from __future__ import annotations

from content_update_helpers import FakeGuru, SequencedLLM, StubLLM

from src.data.content_update import (
    ContentUpdateRequest, Deps, approve_and_publish, run_content_update,
)


def test_pipeline_stages_draft_then_publishes_update(empty_db):
    conn = empty_db.conn
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy",
                            "content": "<p>Old copay rules apply.</p>",
                            "collection": "Billing", "collection_id": "col-1"}])
    deps = Deps(
        llm_client=StubLLM(title="Copay policy",
                           body="Updated: Aetna copays are waived effective today.\n"
                                "Contact billing for exceptions."),
        guru_client=guru,
    )
    req = ContentUpdateRequest(
        source_text="New policy: Aetna copays are waived effective today.",
        source_title="Copay update", target_card_ref="c1",
    )
    res = run_content_update(conn, req, deps)

    assert res.ok and res.status == "staged"
    assert res.draft_id > 0 and res.card_id == "c1"
    assert "copays are waived" in res.proposed.content_md
    assert res.diff

    from src.data import enablement_store as store
    assert "copays are waived" in store.get_draft(conn, res.draft_id)["content"]

    out = approve_and_publish(conn, guru, res.draft_id, approved_by="chris")
    assert out["ok"] and out["status"] == "pushed"
    assert len(guru.updated) == 1 and guru.updated[0]["card_id"] == "c1"


def test_pipeline_ambiguous_card_stops_for_human(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy A", "content": "<p>x</p>"},
                           {"id": "c2", "title": "Copay policy B", "content": "<p>x</p>"}])
    deps = Deps(llm_client=StubLLM(), guru_client=guru)
    req = ContentUpdateRequest(source_text="copay update", source_title="Copay policy")
    res = run_content_update(empty_db.conn, req, deps)
    assert not res.ok and res.status == "ambiguous_card"
    assert len(res.candidates) == 2


def test_pipeline_identify_failure_is_graceful(empty_db):
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy", "content": "<p>x</p>"}])
    deps = Deps(llm_client=SequencedLLM(["garbage", "garbage", "garbage"]), guru_client=guru)
    req = ContentUpdateRequest(source_text="x", source_title="Copay policy",
                               target_card_ref="c1")
    res = run_content_update(empty_db.conn, req, deps)
    assert not res.ok and res.status == "failed" and res.stage == "identify"
    assert res.draft_id > 0  # the card was pulled before the failure


def test_pipeline_no_source_fails_at_load(empty_db):
    deps = Deps(llm_client=StubLLM(), guru_client=FakeGuru())
    res = run_content_update(empty_db.conn, ContentUpdateRequest(), deps)
    assert not res.ok and res.stage == "load_source"


def test_update_card_from_doc_handler_stages(empty_db, monkeypatch):
    """The Renn chat tool wraps the pipeline: doc + card -> staged draft."""
    import src.data.guru_client as gc
    import src.gemini.client_factory as cf
    import src.data.chat_tools.enablement_tools as et
    from src.data import enablement_store as store

    conn = empty_db.conn
    store.save_document(conn, source="upload", doc_id="d1", name="Telehealth policy",
                        full_text="Telehealth copays are waived effective 2026-07-01.")

    class FakeGuruClient(FakeGuru):
        @staticmethod
        def load_credentials():
            return ("e@x", "tok")

        def __init__(self, *a, **k):
            super().__init__(cards=[{"id": "c1", "title": "Aetna Copay",
                                     "content": "<p>Collect the copay at time of service.</p>"}])

    monkeypatch.setattr(cf, "build_client_for_task",
                        lambda task, use_bridge=False: StubLLM(
                            title="Aetna Copay",
                            body="Telehealth copays are waived effective 2026-07-01."))
    monkeypatch.setattr(gc, "GuruClient", FakeGuruClient)

    out = et.handle_update_card_from_doc(conn, {"doc_ref": "d1", "card_ref": "c1"}, {})
    assert out["ok"] and out["status"] == "staged"
    assert out["draft_id"] > 0 and out["card_id"] == "c1"
    assert out.get("changes") and "diff" in out


def test_update_card_from_doc_handler_requires_source(empty_db, monkeypatch):
    import src.data.chat_tools.enablement_tools as et
    out = et.handle_update_card_from_doc(empty_db.conn, {"card_ref": "c1"}, {})
    assert not out["ok"] and "source_doc_required" in out["error"]
