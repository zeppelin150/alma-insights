"""WS-B (pilot feedback): push a CCC draft into Guru's My Drafts.

Locks the store lane: publish_body bytes are what ships, the CCC row stays
'pending' with the guru_draft_id recorded, the undocumented-API failure mode
degrades loudly (never a silent no-op), collaborators skip the credential
identity, and the close-out mark requires a prior draft push. The live
endpoint contract is pinned separately by the creds-gated
tests/test_guru_drafts_local.py (gitignored convention).
"""

from __future__ import annotations

import pytest

from src.data import enablement_store as S


@pytest.fixture(autouse=True)
def _no_settings(monkeypatch):
    from src.data import settings_manager
    monkeypatch.setattr(settings_manager, "get_section", lambda *a, **k: {})


class SpyDraftsGuru:
    _email = "spec@alma.com"

    def __init__(self, *, fail_create=None):
        self.created = []
        self.contexts = []
        self.collaborators = []
        self._fail_create = fail_create

    def create_draft(self, title, content, json_content=None):
        if self._fail_create:
            raise self._fail_create
        self.created.append({"title": title, "content": content})
        return {"id": f"gd-{len(self.created)}"}

    def set_draft_context(self, draft_id, collection_id, *, folder_ids=None,
                          share_status="TEAM"):
        self.contexts.append({"draft_id": draft_id,
                              "collection_id": collection_id,
                              "folder_ids": folder_ids})
        return {}

    def add_draft_collaborator(self, draft_id, email):
        self.collaborators.append(email)
        return {}


def test_push_records_id_and_ships_publish_body(empty_db):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="Draft me", content="## Head\n\nbody")
    g = SpyDraftsGuru()
    out = S.push_draft_to_guru_draft(conn, did, g, collection_id="coll-9")
    assert out["ok"] and out["guru_draft_id"] == "gd-1"
    assert g.created[0]["content"] == S.publish_body(S.get_draft(conn, did))
    assert g.contexts[0] == {"draft_id": "gd-1", "collection_id": "coll-9",
                             "folder_ids": None}
    row = S.get_draft(conn, did)
    assert row["guru_draft_id"] == "gd-1" and row["guru_draft_created_at"]
    assert row["status"] == "pending"          # NOT frozen, NOT pushed


def test_undocumented_api_degrades_loudly(empty_db):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="D", content="x")
    g = SpyDraftsGuru(fail_create=RuntimeError("HTTP 404 Not Found"))
    out = S.push_draft_to_guru_draft(conn, did, g, collection_id="c")
    assert out["ok"] is False and out["error"] == "drafts_api_unavailable"
    assert "publish normally" in out["message"]
    assert S.get_draft(conn, did)["guru_draft_id"] is None


def test_no_client_and_no_collection_refuse(empty_db):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="D", content="x")
    assert S.push_draft_to_guru_draft(conn, did, None)["error"] == "guru_not_connected"
    out = S.push_draft_to_guru_draft(conn, did, SpyDraftsGuru())
    assert out["error"] == "no_collection"     # no arg, no settings default


def test_collaborators_skip_the_credential_identity(empty_db, monkeypatch):
    from src.data import settings_manager
    monkeypatch.setattr(
        settings_manager, "get_section",
        lambda *a, **k: {"guru": {
            "publish_collection_id": "coll-cfg",
            "draft_collaborators": ["spec@alma.com", "peer@alma.com"]}})
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="D", content="x")
    g = SpyDraftsGuru()                        # _email == spec@alma.com
    out = S.push_draft_to_guru_draft(conn, did, g)
    assert out["ok"] and out["collection_id"] == "coll-cfg"
    assert g.collaborators == ["peer@alma.com"]


def test_mark_published_requires_a_draft_push(empty_db):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="D", content="x")
    assert S.mark_published_in_guru(conn, did)["error"] == "no_guru_draft"
    S.push_draft_to_guru_draft(conn, did, SpyDraftsGuru(), collection_id="c")
    out = S.mark_published_in_guru(conn, did)
    assert out["ok"] and S.get_draft(conn, did)["status"] == "pushed"
    # a pushed row refuses another draft push
    again = S.push_draft_to_guru_draft(conn, did, SpyDraftsGuru(),
                                       collection_id="c")
    assert again["error"] == "already_pushed"


def test_per_kind_target_map_resolution(empty_db, monkeypatch):
    """G3: explicit arg > per-kind map > global default."""
    from src.data import settings_manager
    monkeypatch.setattr(
        settings_manager, "get_section",
        lambda *a, **k: {"guru": {
            "publish_collection_id": "coll-global",
            "publish_targets": {
                "battle_card": {"collection_id": "coll-battle",
                                "folder_id": "fold-battle"}}}})
    battle = {"draft_type": "battle_card"}
    plain = {"draft_type": "one_pager"}
    assert S.resolve_publish_target(battle) == ("coll-battle", "fold-battle")
    assert S.resolve_publish_target(plain) == ("coll-global", None)
    assert S.resolve_publish_target(battle, collection_id="coll-x") \
        == ("coll-x", "fold-battle")


def test_artifact_born_draft_carries_its_kind(empty_db):
    """G3 prereq: a draft created from an artifact records the artifact KIND
    as its draft_type — what resolve_publish_target routes on."""
    from src.data import artifact_store
    from src.data.chat_tools.artifact_tools import handle_attach_artifact
    conn = empty_db.conn
    aid = artifact_store.create_artifact(
        conn, kind="quiz", title="Escalation quiz",
        spec={"quiz": {"title": "Q", "questions": [
            {"question": "Where?", "choices": [
                {"text": "Desk", "correct": True},
                {"text": "Email", "correct": False}],
             "explanation": "x"}]},
              "quiz_md": "## Knowledge check\n\n- Q1"})
    res = handle_attach_artifact(
        conn, {"artifact_id": aid, "new_draft_title": "Quiz card"}, {})
    assert res.get("ok"), res
    assert S.get_draft(conn, res["draft_id"])["draft_type"] == "quiz"
