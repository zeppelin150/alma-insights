"""End-to-end tests for the Enablement data layer + simulation pipeline.

Covers the task spine, local document/draft store, and the data-flow simulation
(Drive pull → store → draft → task → publish). All headless via empty_db
(migration 027 applied by DatabaseManager.initialize()), no live creds.
"""

import pytest

from src.data import enablement_store as S
from src.data import enablement_sim as SIM
from src.data import enablement_tasks as T


# ── task spine ───────────────────────────────────────────────────────

def test_create_task_and_dedup(empty_db):
    conn = empty_db.conn
    t1 = T.create_task(conn, source="drive", kind="card_review", title="Card A", source_ref="doc1")
    t2 = T.create_task(conn, source="drive", kind="card_review", title="Card A", source_ref="doc1")
    assert t1 == t2                      # re-poll must not duplicate
    rows = T.list_tasks(conn)
    assert len(rows) == 1
    assert rows[0]["title"] == "Card A"


def test_update_task_status_completes(empty_db):
    conn = empty_db.conn
    tid = T.create_task(conn, source="manual", kind="request", title="Do thing")
    assert T.update_task(conn, tid, status="done", priority="high")
    task = T.get_task(conn, tid)
    assert task["status"] == "done"
    assert task["priority"] == "high"
    assert task["completed_at"]


def test_subtasks_add_toggle_order(empty_db):
    conn = empty_db.conn
    tid = T.create_task(conn, source="manual", kind="request", title="Parent")
    ids = T.draft_subtasks(conn, tid, ["a", "b", "c"])     # AI-drafted breakdown
    assert len(ids) == 3
    subs = T.list_subtasks(conn, tid)
    assert [s["ordinal"] for s in subs] == [0, 1, 2]
    assert all(s["done"] == 0 for s in subs)
    T.toggle_subtask(conn, ids[1], True)
    assert T.list_subtasks(conn, tid)[1]["done"] == 1
    row = T.list_tasks(conn)[0]
    assert row["subtask_total"] == 3 and row["subtask_done"] == 1


def test_scratchpad_roundtrip(empty_db):
    conn = empty_db.conn
    tid = T.create_task(conn, source="manual", kind="request", title="Notes task")
    assert T.set_scratchpad(conn, tid, "launch date pending; confirm regions")
    assert "launch date" in T.get_task(conn, tid)["scratchpad"]


def test_list_tasks_filters(empty_db):
    conn = empty_db.conn
    T.create_task(conn, source="drive", kind="card_review", title="D1")
    T.create_task(conn, source="asana", kind="request", title="A1", due_date="2026-06-10")
    assert len(T.list_tasks(conn, source="drive")) == 1
    assert len(T.list_tasks(conn, source="asana")) == 1
    assert len(T.list_tasks(conn, due_before="2026-06-15")) == 1
    assert len(T.list_tasks(conn, due_before="2026-06-05")) == 0


# ── local document + draft store ─────────────────────────────────────

def test_save_and_search_document(empty_db):
    conn = empty_db.conn
    doc_id = S.save_document(conn, source="drive", doc_id="d1", name="SSO Setup.gdoc",
                             full_text="Provider SSO self-serve launches June 24, 2026.")
    doc = S.get_document(conn, doc_id)
    assert "SSO" in doc["full_text"]               # full text stored locally
    assert S.search_documents(conn, "SSO")[0]["doc_id"] == "d1"       # lookup by name
    assert S.search_documents(conn, "self-serve")[0]["doc_id"] == "d1"  # lookup by body
    # re-index same id updates in place — no duplicate
    S.save_document(conn, source="drive", doc_id="d1", name="SSO Setup.gdoc", full_text="Updated.")
    assert len(S.list_documents(conn)) == 1


def test_draft_card_from_document(empty_db):
    conn = empty_db.conn
    doc_id = S.save_document(conn, source="drive", doc_id="d2", name="Returns Policy.gdoc",
                             full_text="Returns window extended to 30 days, effective July 1, 2026.")
    draft = S.draft_card_from_document(conn, doc_id, SIM._StubLLM())
    assert draft["status"] == "pending" and draft["title"]
    assert S.get_draft(conn, draft["id"])["source_ref"] == "d2"      # draft ↔ doc link
    assert S.get_document(conn, doc_id)["card_draft_id"] == draft["id"]
    assert S.search_drafts(conn, "Returns")                          # draft searchable


def test_demo_card_reflects_document_content(empty_db):
    """A demo card must reflect the REAL uploaded document, not a generic
    canned template (the bug: uploaded doc → stub template ignoring content)."""
    conn = empty_db.conn
    text = (
        "COB reduction pilot. Providers must verify secondary coverage before "
        "submitting a claim.\n\nRollout: effective 2026-08-01 for the pilot cohort.\n\n"
        "Tier B accounts move to usage-based billing."
    )
    doc_id = S.save_document(conn, source="upload",
                             name="COB_Reduction_Pilot_PRD.docx", full_text=text)
    draft = S.draft_card_from_document(conn, doc_id, SIM._StubLLM())
    body = S.get_draft(conn, draft["id"])["content"]
    assert "secondary coverage" in body          # the REAL content surfaces
    assert "usage-based billing" in body
    assert "Update training material" not in body  # not the old canned template


def test_redraft_is_idempotent(empty_db):
    conn = empty_db.conn
    doc_id = S.save_document(conn, source="drive", doc_id="d9", name="X.gdoc", full_text="body")
    d1 = S.draft_card_from_document(conn, doc_id, SIM._StubLLM())
    d2 = S.draft_card_from_document(conn, doc_id, SIM._StubLLM())     # re-scan same doc
    assert d1["id"] == d2["id"]                                       # no duplicate draft
    assert len(S.list_drafts(conn)) == 1


def test_publish_draft_calls_guru(empty_db, mock_guru_client):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="SSO Guide", content="Body", source_ref="d3")
    res = S.publish_draft(conn, did, guru_client=mock_guru_client, collection_id="coll-1")
    assert res["ok"] and res["status"] == "pushed"
    mock_guru_client.create_card.assert_called_once()
    assert S.get_draft(conn, did)["status"] == "pushed"
    # idempotent: re-publish is a no-op
    res2 = S.publish_draft(conn, did, guru_client=mock_guru_client)
    assert res2.get("already") is True
    mock_guru_client.create_card.assert_called_once()


# ── full pipeline simulation (data in → out) ─────────────────────────

def test_run_simulation_end_to_end(empty_db, mock_guru_client):
    conn = empty_db.conn
    summary = SIM.run_simulation(conn, publish=True, guru_client=mock_guru_client)
    n = len(SIM.MOCK_DOCS)
    assert len(summary["documents"]) == n
    assert len(summary["drafts"]) == n
    assert len(summary["tasks"]) == n
    assert len(summary["published"]) == n
    # data landed and rounds back out
    assert len(S.list_documents(conn)) == n
    assert len(T.list_tasks(conn)) == n
    assert all(t["status"] == "done" for t in T.list_tasks(conn))     # completed on publish
    assert all(d["status"] == "pushed" for d in S.list_drafts(conn))
    # "look up an old document" via the same store the chat tool will use
    hits = S.search_documents(conn, "SSO")
    assert hits and "SSO" in hits[0]["name"]
    # re-running the poll dedups tasks (idempotent)
    SIM.run_simulation(conn, publish=False)
    assert len(T.list_tasks(conn)) == n
