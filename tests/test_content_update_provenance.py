"""Provenance (audit trail) + effectiveness feedback on push."""

from __future__ import annotations

from content_update_helpers import FakeGuru, StubLLM

from src.data.content_update import provenance
from src.data.content_update.effectiveness import card_effectiveness


# ── provenance ──

def test_provenance_record_finalize_history(empty_db):
    conn = empty_db.conn
    provenance.record_proposal(conn, 7, "c1", source_ref="doc:d1", source_title="Telehealth policy",
                               summary="add telehealth waiver",
                               changes=[{"type": "update", "section": "Policy", "reason": "r"}],
                               dropped=[{"type": "add", "section": "X", "reason": "ungrounded"}])
    h = provenance.history(conn, "c1")
    assert len(h) == 1 and h[0]["status"] == "staged"
    assert h[0]["source_title"] == "Telehealth policy" and h[0]["dropped"]

    provenance.finalize_publish(conn, 7, approved_by="chris", card_id="c1")
    h = provenance.history(conn, "c1")
    assert h[0]["status"] == "published" and h[0]["approved_by"] == "chris" and h[0]["pushed_at"]


def test_publish_draft_finalizes_provenance(empty_db):
    conn = empty_db.conn
    from src.data import enablement_store as es
    did = es.save_card_draft(conn, title="Card", content="body")
    es.set_draft_card_id(conn, did, "c1")
    provenance.record_proposal(conn, did, "c1", summary="s",
                               changes=[{"type": "update", "section": "P", "reason": "r"}])
    res = es.publish_draft(conn, did, guru_client=FakeGuru(cards=[{"id": "c1", "title": "Card", "content": "<p>x</p>"}]),
                           approved_by="chris")
    assert res["ok"] and res["status"] == "pushed"
    rec = provenance.get(conn, did)
    assert rec["status"] == "published" and rec["approved_by"] == "chris"


def test_orchestrator_records_provenance(empty_db):
    conn = empty_db.conn
    from src.data.content_update import ContentUpdateRequest, Deps, run_content_update
    guru = FakeGuru(cards=[{"id": "c1", "title": "Copay policy", "content": "<p>Old copay rules.</p>"}])
    deps = Deps(llm_client=StubLLM(title="Copay policy", body="Telehealth copays are waived. " * 6),
                guru_client=guru)
    res = run_content_update(conn, ContentUpdateRequest(source_text="Telehealth copays are waived.",
                                                        source_title="Telehealth policy",
                                                        target_card_ref="c1"), deps)
    assert res.ok and res.status == "staged"
    h = provenance.history(conn, "c1")
    assert h and h[0]["status"] == "staged" and h[0]["source_title"] == "Telehealth policy"


# ── effectiveness ──

def test_card_effectiveness_surfaces_measured(empty_db):
    conn = empty_db.conn
    conn.execute("PRAGMA foreign_keys=OFF")  # test-only: seed an effectiveness row without its parent
    conn.execute(
        "CREATE TABLE IF NOT EXISTS guru_effectiveness (id INTEGER PRIMARY KEY, card_id TEXT, "
        "friction_type TEXT, measurement_date TEXT, source TEXT, pre_volume INT, post_volume INT, "
        "pre_window_days INT, post_window_days INT, delta_pct REAL, is_significant INT, created_at TEXT)")
    conn.execute("INSERT INTO guru_effectiveness (card_id, friction_type, measurement_date, "
                 "pre_volume, post_volume, post_window_days, delta_pct, is_significant) "
                 "VALUES ('c1','billing','2026-06-20',40,28,14,-30.0,1)")
    conn.commit()
    provenance.record_proposal(conn, 5, "c1", summary="s",
                               changes=[{"type": "update", "section": "P", "reason": "r"}])
    provenance.finalize_publish(conn, 5, approved_by="chris", card_id="c1")

    eff = card_effectiveness(conn, "c1")
    assert eff["ok"] and eff["measured"] and eff["measured"][0]["delta_pct"] == -30.0
    assert "delta -30.0%" in eff["note"]
    assert eff["updates"][0]["status"] == "published"


def test_card_effectiveness_pending_when_unmeasured(empty_db):
    conn = empty_db.conn
    provenance.record_proposal(conn, 9, "c2", summary="s", changes=[{"type": "update", "section": "P", "reason": "r"}])
    provenance.finalize_publish(conn, 9, approved_by="chris", card_id="c2")
    eff = card_effectiveness(conn, "c2")
    assert eff["ok"] and eff["measured"] == []
    assert "anchored" in eff["note"]


# ── tools ──

def test_card_history_and_effectiveness_tools(empty_db):
    import src.data.chat_tools.enablement_tools as et
    conn = empty_db.conn
    provenance.record_proposal(conn, 3, "c1", summary="s", changes=[{"type": "update", "section": "P", "reason": "r"}])
    hist = et.handle_card_history(conn, {"card_ref": "https://app.getguru.com/card/c1"}, {})
    assert hist["ok"] and hist["card_id"] == "c1" and len(hist["history"]) == 1
    eff = et.handle_card_effectiveness(conn, {"card_ref": "c1"}, {})
    assert eff["ok"] and "note" in eff
