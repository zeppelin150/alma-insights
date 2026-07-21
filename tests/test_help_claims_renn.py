"""Accuracy audit of the in-app Help Center — section: renn (8 articles).

Every test here settles ONE falsifiable claim made by an article under
``assets/help/renn/``. Each test's docstring names the article and quotes (or
closely paraphrases) the claim it settles.

Where the article claims X and the code does NOT-X the test still asserts the
ARTICLE's claim and is marked ``xfail(strict=True)`` with the actual behaviour
in the reason string — so the suite stays green while the discrepancy stays
tracked and will start failing loudly the day someone "fixes" it.

Headless: no network, no credentials, no QtWebEngine. Live clients are
monkeypatched at their import sites.
"""
from __future__ import annotations

import json
import os
from datetime import date

import pytest

from src.data import chat_action_requests as car
from src.data.chat_tools import enablement_tools as ET
from src.data.chat_tools.registry import get_tool_registry


# ── shared fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    """A single QApplication for the whole module (widget tests only — no
    QtWebEngine, no screenshots; assertions go through widget APIs)."""
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def model_tool_names() -> set[str]:
    """The tool names Renn actually sees — chat_mcp_server.TOOL_SCHEMAS."""
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS
    return {t["name"] for t in TOOL_SCHEMAS}


@pytest.fixture(scope="module")
def registry_names() -> set[str]:
    return set(get_tool_registry().keys())


@pytest.fixture(scope="module")
def renn_prompt() -> str:
    """RENN_SYSTEM_PROMPT. Imported from the enablement page module (Qt widgets
    only — no QtWebEngine, no QApplication needed for a module constant)."""
    from src.ui.pages.enablement.page import RENN_SYSTEM_PROMPT
    return RENN_SYSTEM_PROMPT


@pytest.fixture
def chat_session(empty_db, tmp_path, monkeypatch):
    """An active chat session id, exposed the way the resolver tools read it
    (the ALMA_CHAT_SESSION_FILE pointer written by the host page)."""
    from src.services.chat_session import create_session
    sid = create_session("enablement", conn=empty_db.conn)
    pointer = tmp_path / ".current_chat_session"
    pointer.write_text(sid, encoding="utf-8")
    monkeypatch.setenv("ALMA_CHAT_SESSION_FILE", str(pointer))
    return sid


def _make_task(conn, *, title, assignee=None, **kw):
    """create_task + the assignee stamp (assignee is an update-only field)."""
    from src.data import enablement_tasks as et
    tid = et.create_task(conn, source=kw.pop("source", "asana"),
                         kind=kw.pop("kind", "todo"), title=title, **kw)
    if assignee is not None:
        et.update_task(conn, tid, assignee=assignee)
    return tid


def _action_rows(conn, type_=None):
    sql = "SELECT type, payload_json FROM chat_action_requests"
    if type_:
        sql += f" WHERE type = '{type_}'"
    return [dict(r) for r in conn.execute(sql).fetchall()]


# ═════════════════════════════════════════════════════════════════════
#  Article: what-renn-can-do.md
# ═════════════════════════════════════════════════════════════════════

def test_find_capability_tools_are_on_the_model_surface(model_tool_names):
    """what-renn-can-do: "Find things — search your Guru cards, Zendesk
    articles, Asana tasks, the business Drive, and the knowledge base"."""
    for name in ("search_guru_cards", "search_zendesk_articles",
                 "search_asana_tasks", "query_business_drive",
                 "search_drive_docs", "kb_search"):
        assert name in model_tool_names, f"{name} missing from TOOL_SCHEMAS"


def test_card_draft_trio_is_on_the_model_surface(model_tool_names):
    """what-renn-can-do: "read the current draft, apply an edit you describe
    in plain language, and publish to Guru"."""
    for name in ("render_card_preview", "revise_draft", "push_guru_draft"):
        assert name in model_tool_names


def test_work_management_tools_are_on_the_model_surface(model_tool_names):
    """what-renn-can-do: "list tasks, create them, add subtasks, update the
    scratchpad on a task"."""
    for name in ("list_tasks", "create_task", "add_subtask",
                 "update_scratchpad"):
        assert name in model_tool_names


def test_asana_setup_tools_exist_and_prompt_forbids_asking_for_gids(
        model_tool_names, renn_prompt):
    """what-renn-can-do: "discover your Asana projects and save a board
    configuration, without ever asking you for an ID"."""
    assert "asana_discover" in model_tool_names
    assert "set_asana_board_config" in model_tool_names
    assert "Never ask the user for GIDs" in renn_prompt


def test_content_generation_tools_cover_the_five_documented_artifacts(
        model_tool_names):
    """what-renn-can-do / tool-reference: "diagrams, knowledge-check quizzes,
    one-pagers, battle cards, and branded decks"."""
    from src.data.chat_tools.artifact_tools import _DOC_STYLES
    for name in ("generate_diagram", "generate_quiz", "generate_doc",
                 "generate_deck"):
        assert name in model_tool_names
    assert "one_pager" in _DOC_STYLES and "battle_card" in _DOC_STYLES


def test_prompt_gates_guru_publishing_on_an_explicit_request(renn_prompt):
    """what-renn-can-do: "It will not publish to Guru unless you explicitly
    ask." (a prompt-level rule — this asserts the rule is present)."""
    assert ("Only publish to Guru (push_guru_draft) when the operator "
            "explicitly asks") in renn_prompt


def test_prompt_forbids_repeating_a_drive_folder_name(renn_prompt):
    """what-renn-can-do / pickers: "It will not repeat a Google Drive folder
    name back to you ... refers to 'the active folder' instead"."""
    assert "NEVER repeat a Google Drive FOLDER name" in renn_prompt
    assert "the active folder" in renn_prompt


def test_retired_ungated_asana_write_tools_are_off_the_model_surface(
        model_tool_names, registry_names):
    """what-renn-can-do: "It cannot perform writes on its own. Anything that
    changes Asana ... opens a Confirm card." The three legacy direct-execute
    Asana write tools must be absent from BOTH the model surface and the
    dispatch registry (their impls survive for the TaskDetailPanel's own
    click path, which is not a Renn surface)."""
    for name in ("create_asana_subtask", "post_asana_comment",
                 "update_asana_due_date"):
        assert name not in model_tool_names, f"{name} is model-callable"
        assert name not in registry_names, f"{name} is dispatchable"


def test_every_gated_write_op_is_reachable_only_through_a_request_tool():
    """confirm-card: "There is no direct-execute write tool." Every allowlisted
    confirm_write op is minted by a request_* propose tool, and the
    confirm_write row type cannot be minted through the generic
    create_action_request path."""
    assert car._CONFIRM_WRITE_OPS == {
        "create_guru_folder", "rename_guru_folder", "create_asana_task",
        "asana_task_update", "upload_artifact_to_drive",
    }
    with pytest.raises(ValueError, match="use create_confirm_write"):
        car.create_action_request(None, "sid", "confirm_write", None)


# ═════════════════════════════════════════════════════════════════════
#  Article: asking-well.md
# ═════════════════════════════════════════════════════════════════════

def test_list_tools_report_a_total_count_over_a_complete_enumeration(
        empty_db, monkeypatch):
    """asking-well: "Listing returns all items and a total count." Asserts
    list_guru_cards paginates the whole collection and reports `total`
    independently of the returned window."""
    class _FakeGuru:
        def __init__(self, *a, **k):
            pass

        @staticmethod
        def load_credentials():
            return ("e@x.com", "tok")

        def list_cards(self, collection_id):
            return [{"id": f"c{i}", "title": f"Card {i}", "collection": "Billing"}
                    for i in range(25)]

    monkeypatch.setattr("src.data.guru_client.GuruClient", _FakeGuru)
    out = ET._list_guru_cards_impl(empty_db.conn, "coll-1", limit=5, offset=0)
    assert out["ok"] is True
    assert out["total"] == 25, "total must reflect the FULL collection"
    assert out["count"] == 5 and len(out["cards"]) == 5


def test_search_content_fans_out_to_guru_zendesk_and_drive_labelling_each_row(
        empty_db, monkeypatch):
    """asking-well: "sends one query across Guru, Zendesk and Drive at once and
    returns a single merged list labelled by source"."""
    monkeypatch.setattr(ET, "_search_guru_cards_impl", lambda c, q, limit=8: {
        "ok": True, "cards": [{"card_id": "g1", "title": "G", "snippet": "s",
                               "url": "u"}]})
    monkeypatch.setattr(ET, "_search_zendesk_articles_impl", lambda c, q, limit=8: {
        "ok": True, "articles": [{"id": "z1", "title": "Z", "html_url": "u"}]})
    monkeypatch.setattr(ET, "_search_drive_docs_impl", lambda c, q, limit=10: {
        "documents": [{"doc_id": "d1", "name": "D", "text_excerpt": "x"}]})

    out = ET._search_content_impl(empty_db.conn, "prior auth")
    assert out["ok"] is True
    assert {r["source"] for r in out["results"]} == {"guru", "zendesk", "drive"}
    assert set(out["sources"]) == {"guru", "zendesk", "drive"}


def test_a_disconnected_source_is_reported_not_raised(empty_db, monkeypatch):
    """asking-well: "A source you have not connected is skipped and reported,
    not treated as an error" / "should produce a note ... never an error and
    never silence"."""
    monkeypatch.setattr(ET, "_search_guru_cards_impl", lambda c, q, limit=8: {
        "ok": False, "error": "guru_not_connected"})
    monkeypatch.setattr(ET, "_search_zendesk_articles_impl", lambda c, q, limit=8: {
        "ok": False, "error": "zendesk_not_connected"})
    monkeypatch.setattr(ET, "_search_drive_docs_impl", lambda c, q, limit=10: {
        "documents": []})

    out = ET._search_content_impl(empty_db.conn, "refunds")
    assert out["ok"] is True, "a not-connected source must not fail the call"
    assert out["sources"]["guru"]["status"] == "not_connected"
    assert out["sources"]["zendesk"]["status"] == "not_connected"
    assert out["sources"]["drive"]["status"] == "ok"


def test_prompt_routes_product_questions_to_the_knowledge_base_first(renn_prompt):
    """asking-well: "For product or policy questions, Renn checks the knowledge
    base it maintains before going out to the live sources"."""
    assert "KNOWLEDGE BASE (check it FIRST for product/policy questions)" in renn_prompt


def test_prompt_distinguishes_list_tools_from_search_tools(renn_prompt):
    """asking-well: listing is complete, searching is ranked and may miss."""
    assert "LIST tools enumerate COMPLETELY and report a total count" in renn_prompt
    assert "search may miss" in renn_prompt


def test_multiword_query_misses_a_doc_whose_words_appear_apart(empty_db):
    """asking-well: "Several search paths in the app match multi-word phrases
    only when the words appear together, so 'prior authorization escalation'
    can miss a document that contains those words apart."

    Settles it against the real store: search_documents builds ONE contiguous
    LIKE '%query%', so a doc containing every word separately does not match.
    """
    from src.data import enablement_store as store
    empty_db.conn.execute(
        """INSERT INTO enablement_documents
           (doc_id, source, name, mime_type, full_text, indexed_at)
           VALUES ('d1', 'drive', 'Payer Playbook', 'text/plain', ?, '2026-07-01')""",
        ("We handle prior authorization requests. Escalation goes to the lead.",),
    )
    empty_db.conn.commit()

    together = store.search_documents(empty_db.conn, "prior authorization")
    apart = store.search_documents(empty_db.conn, "prior authorization escalation")
    assert len(together) == 1, "contiguous phrase should match"
    assert apart == [], ("documented limitation: the words must appear "
                         "together for the LIKE to match")


# ═════════════════════════════════════════════════════════════════════
#  Article: confirm-card.md
# ═════════════════════════════════════════════════════════════════════

def test_request_create_guru_folder_mints_one_card_and_writes_nothing(
        empty_db, chat_session, monkeypatch):
    """confirm-card: "When Renn wants to make a change, it *proposes* one and a
    Confirm card appears; your click is what performs the write."

    Asserts the propose tool creates exactly one unconsumed confirm_write row
    and never reaches a Guru client.
    """
    monkeypatch.setattr(ET, "_guru_client_or_none", lambda: None)
    out = ET.handle_request_create_guru_folder(
        empty_db.conn,
        {"collection_id": "coll-1", "title": "Onboarding",
         "collection_name": "Billing"}, {})

    rows = _action_rows(empty_db.conn, "confirm_write")
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["op"] == "create_guru_folder"
    assert payload["params"]["title"] == "Onboarding"
    assert isinstance(out, str) and "STOP" in out


def test_the_confirm_card_summary_names_the_specific_change(
        empty_db, chat_session, monkeypatch):
    """confirm-card: "The Confirm card should name the specific thing being
    changed, not a generic 'apply changes'"."""
    monkeypatch.setattr(ET, "_guru_client_or_none", lambda: None)
    ET.handle_request_create_guru_folder(
        empty_db.conn,
        {"collection_id": "c1", "title": "Onboarding",
         "collection_name": "Billing"}, {})
    summary = json.loads(
        _action_rows(empty_db.conn, "confirm_write")[0]["payload_json"])["summary"]
    assert "Onboarding" in summary and "Billing" in summary
    assert "apply changes" not in summary.lower()


def test_propose_tools_never_hand_the_model_the_request_id(
        empty_db, chat_session, monkeypatch):
    """confirm-card: "your click is what performs the write" — the model gets a
    wait instruction only, so it cannot replay/pin an unresolved request."""
    monkeypatch.setattr(ET, "_guru_client_or_none", lambda: None)
    out = ET.handle_request_rename_guru_folder(
        empty_db.conn, {"folder_id": "f1", "new_title": "Renamed"}, {})
    rid = empty_db.conn.execute(
        "SELECT request_id FROM chat_action_requests").fetchone()[0]
    assert isinstance(out, str)
    assert rid not in out, "the request_id leaked into the model-facing string"


def test_readonly_guru_collection_is_refused_before_a_card_opens(
        empty_db, chat_session, monkeypatch):
    """confirm-card: "a read-only, Guru-managed collection cannot take a new
    folder ... Renn tells you plainly instead of opening a Confirm card for
    something doomed to fail"."""
    class _RO:
        def get_collection(self, cid):
            return {"id": cid, "name": "Guru Managed", "readOnly": True}

        def list_collections(self):
            return [{"id": "w1", "name": "Writable", "read_only": False}]

    monkeypatch.setattr(ET, "_guru_client_or_none", lambda: _RO())
    out = ET.handle_request_create_guru_folder(
        empty_db.conn, {"collection_id": "ro1", "title": "Nope"}, {})

    assert "read-only" in out
    assert _action_rows(empty_db.conn, "confirm_write") == [], \
        "no doomed Confirm card should be minted"


def test_unknown_asana_board_is_refused_before_a_card_opens(
        empty_db, chat_session, monkeypatch):
    """confirm-card: "Before the card even opens, the target is checked for
    feasibility." (Asana arm: an unknown board gid.)"""
    monkeypatch.setattr(ET, "_list_asana_projects_impl", lambda conn: {
        "ok": True, "count": 1, "projects": [{"gid": "111", "name": "Tracker"}]})
    out = ET.handle_request_create_asana_task(
        empty_db.conn, {"project_gid": "999", "name": "Do a thing"}, {})

    assert "can't find that Asana board" in out
    assert "Tracker" in out
    assert _action_rows(empty_db.conn, "confirm_write") == []


def test_asana_task_update_refuses_a_task_that_is_not_asana_sourced(
        empty_db, chat_session):
    """confirm-card / tool-reference: request_asana_task_update "acts only on
    Asana-sourced tasks" — a local task returns a steer and mints no card."""
    from src.data import enablement_tasks as et
    tid = et.create_task(empty_db.conn, source="manual", kind="todo",
                         title="Local only")
    out = ET.handle_request_asana_task_update(
        empty_db.conn, {"task_id": tid, "action": "complete"}, {})
    assert "isn't linked to Asana" in out
    assert _action_rows(empty_db.conn, "confirm_write") == []


def test_drafting_and_revising_open_no_confirm_card(empty_db, chat_session):
    """confirm-card: "Two things have no Confirm card and that is intentional:
    drafting and revising a card draft"."""
    out = ET.handle_create_card_draft(
        empty_db.conn, {"title": "Payments v2", "content": "# Body"}, {})
    assert out.get("ok") is not False
    assert _action_rows(empty_db.conn) == [], \
        "creating a draft must not mint an action request"


def test_publishing_a_fresh_draft_requires_a_recorded_human_signoff(empty_db):
    """confirm-card: "nothing leaves the app until you publish" + the publish
    path's own human gate. A draft is created with require_approval=1, so
    push_guru_draft refuses until an operator signs off in the Review panel."""
    from src.data import enablement_store as store
    did = store.save_card_draft(empty_db.conn, title="T", content="B")
    row = store.get_draft(empty_db.conn, did)
    assert row["require_approval"] == 1 and row["approved_at"] is None

    out = ET.handle_push_guru_draft(empty_db.conn, {"draft_id": did}, {})
    assert out["ok"] is False
    assert out["error"] == "approval_required"


def test_there_is_no_way_to_delete_a_guru_folder(model_tool_names,
                                                 registry_names):
    """confirm-card: "there is no way to delete a Guru folder from this app"."""
    from src.data.guru_client import GuruClient
    assert not hasattr(GuruClient, "delete_folder")
    assert "delete_guru_folder" not in car._CONFIRM_WRITE_OPS
    assert not [n for n in (model_tool_names | registry_names)
                if "delete" in n and "folder" in n]


def test_a_confirm_row_can_only_be_claimed_once(empty_db, chat_session):
    """confirm-card: "you click to confirm or cancel ... cancelling should leave
    nothing behind" — the single-winner claim behind one-click-one-write. A
    second resolve (a double click, or a confirm after a cancel) loses."""
    rid = car.create_confirm_write(
        empty_db.conn, chat_session, "create_guru_folder", "s", {"a": 1})
    assert car.mark_resolved(empty_db.conn, rid) is True
    assert car.mark_resolved(empty_db.conn, rid) is False


def test_asana_drift_between_propose_and_confirm_returns_a_conflict(
        empty_db, tmp_path, monkeypatch):
    """confirm-card: "If Asana rejects the change because the task moved
    underneath you, you will see a 'task changed — refreshed, please retry'
    message, which is expected."

    Settles the BEHAVIOUR (a drift conflict is detected and surfaced before the
    write runs). NB: the shipped wording differs from the article's quote.
    """
    from src.data import enablement_tasks as et
    from src.services.agent_chat import _write_asana_task_update

    tid = et.create_task(empty_db.conn, source="asana", kind="todo",
                         title="Ship it", source_ref="gid-1")
    et.update_task(empty_db.conn, tid, remote_modified_at="2026-07-01T00:00:00Z")

    class _Client:
        api_key = "pat"

        def get_task(self, gid, opt_fields=None):
            return {"modified_at": "2026-07-19T00:00:00Z", "due_on": None}

    monkeypatch.setattr("src.data.asana_client.AsanaClient.from_store",
                        classmethod(lambda cls: _Client()))
    res = _write_asana_task_update(
        {"task_id": tid, "action": "complete",
         "expected_modified_at": "2026-07-01T00:00:00Z"},
        {"db_path": str(empty_db.db_path)})

    assert res["ok"] is False
    assert res.get("conflict") is True
    assert "changed in Asana" in res["error"]


def test_kb_drive_writes_are_allowlist_enforced_not_confirm_gated(empty_db):
    """confirm-card: "the knowledge base writing its own index cards into your
    EC folder (that folder is an allowlisted destination the app owns)" — a
    write outside the allowlist is refused hard, not routed to a Confirm card."""
    from src.data.kb import drive_kb

    assert drive_kb.allowed_folder(empty_db.conn, "not-in-allowlist") is False
    with pytest.raises(drive_kb.KBWriteDenied):
        drive_kb.write_card_file(empty_db.conn, "not-in-allowlist", "a.md",
                                 "body", card_id="c1")


# ═════════════════════════════════════════════════════════════════════
#  Article: pickers.md
# ═════════════════════════════════════════════════════════════════════

def test_exactly_four_pickers_exist_on_the_model_surface(model_tool_names):
    """pickers: "Four pickers exist" — Connect Google, Drive folder, Asana
    board, Guru publish target."""
    pickers = {"request_google_connect", "request_drive_picker",
               "request_asana_board_picker", "request_guru_publish_picker"}
    assert pickers <= model_tool_names
    assert {n for n in model_tool_names
            if n.startswith("request_") and
            (n.endswith("_picker") or n == "request_google_connect")} == pickers
    assert car.ACTION_TYPES >= {"drive_folder_picker", "asana_board_picker",
                                "guru_publish_picker", "google_connect"}


def test_a_picker_returns_only_a_stop_and_wait_instruction(
        empty_db, chat_session):
    """pickers: "The picker reads nothing on Renn's behalf ... Renn stops
    entirely while a picker is open." The resolver mints a row and returns a
    fixed wait string carrying no data and no request id."""
    out = ET.handle_request_drive_picker(empty_db.conn, {}, {})
    rows = _action_rows(empty_db.conn, "drive_folder_picker")
    assert len(rows) == 1
    assert rows[0]["payload_json"] is None, "the picker row carries no data"
    assert out == ET._RESOLVER_WAIT_MESSAGE
    assert "STOP and wait" in out


def test_the_picker_channel_rejects_folder_and_board_names(empty_db,
                                                           chat_session):
    """pickers: "Renn is also told your folder ID and never your folder *name*"
    — the action channel's payload allowlist makes a name unsmugglable."""
    with pytest.raises(ValueError, match="disallowed payload key"):
        car.create_action_request(empty_db.conn, chat_session,
                                  "drive_folder_picker",
                                  {"folder_name": "Enablement 2026"})


def test_prompt_carries_the_picker_stop_and_wait_contract(renn_prompt):
    """pickers: "Renn stops entirely while a picker is open and resumes once
    you have chosen"."""
    assert "PICKER CONTRACT" in renn_prompt
    assert "do NOT call any further tools" in renn_prompt


def test_routing_report_returns_drive_ids_and_a_count_never_a_folder_name(
        monkeypatch, empty_db):
    """pickers: "Renn reports the active Drive folders and their count, the
    active Asana board, and the Guru publish target" — and never a Drive
    folder NAME."""
    monkeypatch.setattr(
        "src.data.settings_manager.get_section",
        lambda name, default=None: {
            "drive": {"active_folders": [
                {"id": "fid-1", "name": "Enablement Secret Folder"},
                {"id": "fid-2", "name": "Another"}]},
            "asana": {"active_board": {"project_gid": "77",
                                       "project_name": "Tracker"}},
            "guru": {"publish_collection_id": "col-1",
                     "publish_folder_id": "fol-1"},
        } if name == "enablement" else (default or {}))
    monkeypatch.setattr(ET, "_asana_connected", lambda: True)
    monkeypatch.setattr(ET, "_guru_connected", lambda: True)

    out = ET.handle_get_enablement_routing(empty_db.conn, {}, {})
    assert out["drive"]["active_folder_ids"] == ["fid-1", "fid-2"]
    assert out["drive"]["count"] == 2
    assert "Enablement Secret Folder" not in json.dumps(out)
    assert out["asana"]["active_board_name"] == "Tracker"
    assert out["guru"]["publish_collection_id"] == "col-1"


def test_by_id_setters_refuse_while_a_picker_is_open(empty_db, chat_session):
    """pickers: "You should never be asked to paste an ID" — the by-id fallback
    setters refuse with needs_picker while an unresolved picker is open, so a
    guessed id cannot bypass the operator's pick."""
    ET.handle_request_drive_picker(empty_db.conn, {}, {})
    out = ET.handle_set_drive_folder(empty_db.conn, {"folder_id": "guessed"}, {})
    assert out["ok"] is False
    assert out["needs_picker"] is True


# ═════════════════════════════════════════════════════════════════════
#  Article: daily-plan.md
# ═════════════════════════════════════════════════════════════════════

def test_the_plan_block_is_built_from_the_real_task_store(empty_db,
                                                          monkeypatch):
    """daily-plan: "the app pre-loads your real tasks and hands them to Renn as
    part of the conversation context. It is the same data the Tasks board shows
    — pulled from the task store, not invented and not summarised by a model
    first"."""
    from src.data import enablement_tasks as et
    from src.data import startup_greeting

    today = date(2026, 7, 20)
    _make_task(empty_db.conn, title="Overdue thing", assignee="Chris Guffey",
               due_date="2026-07-18", status="open")
    _make_task(empty_db.conn, title="Today thing", assignee="Chris Guffey",
               due_date="2026-07-20", status="open")

    block = startup_greeting.build_greeting_block(
        empty_db.conn, today=today, aliases={"chris guffey"})

    assert "[TODAY'S PLAN]" in block
    assert "Overdue thing" in block and "Today thing" in block
    assert "Overdue: 1" in block and "Due today: 1" in block


def test_the_plan_block_declares_the_data_authoritative(empty_db):
    """daily-plan: "Renn should never hedge about whether this data is real ...
    It should also not re-query just to double-check the data it was already
    given"."""
    from src.data import enablement_tasks as et
    from src.data import startup_greeting

    _make_task(empty_db.conn, title="X", assignee="chris@cambric.ai",
               due_date="2026-07-20", status="open")
    block = startup_greeting.build_greeting_block(
        empty_db.conn, today=date(2026, 7, 20), aliases={"chris@cambric.ai"})

    assert "accurate and authoritative" in block
    assert "do NOT re-query" in block
    assert "NEVER describe it as invented/fabricated" in block


def test_prompt_reinforces_that_the_plan_is_real(renn_prompt):
    """daily-plan: "Renn describes your task list as fabricated, invented or
    illustrative. That is a bug — the data is authoritative"."""
    assert "REAL task data, pre-loaded from the task store" in renn_prompt
    assert "NEVER describe it as invented or fabricated" in renn_prompt


def test_the_plan_is_injected_on_the_first_turn_only(monkeypatch):
    """daily-plan: "At the start of a turn, the app pre-loads your real tasks"
    — asserts the one-shot behaviour of the Agent surface's context provider:
    the full plan block rides turn 1 and is not repeated on turn 2."""
    from src.services.agent_chat import AgentChatController

    monkeypatch.setattr(
        "src.data.enablement_identity.operator_context_line",
        lambda: "[OPERATOR] You are assisting chris@cambric.ai.")

    fake = type("F", (), {})()
    fake._greeting_block = "[TODAY'S PLAN] two tasks"
    first = AgentChatController._agent_context(fake, "hi", [])
    second = AgentChatController._agent_context(fake, "and then?", [])

    assert "[TODAY'S PLAN]" in first
    assert "[TODAY'S PLAN]" not in second
    assert "[OPERATOR]" in first and "[OPERATOR]" in second


def test_identity_is_supplied_to_renn_every_turn(monkeypatch):
    """daily-plan: "Your identity is supplied the same way, so 'what's assigned
    to me' resolves without you naming yourself." / what-renn-can-do: "Renn
    claims it cannot identify you ... Your identity is supplied to it
    automatically"."""
    from src.data import enablement_identity as ident

    monkeypatch.setattr(ident, "_cfg", lambda: {
        "operator_email": "chris@cambric.ai", "operator_name": "Chris Guffey"})
    line = ident.operator_context_line()
    assert "chris@cambric.ai" in line
    assert line.startswith("[OPERATOR]")


def test_the_plan_is_empty_when_identity_is_unset(empty_db):
    """daily-plan: "The plan is empty but your board has tasks. Two likely
    causes: your identity is not set..." — the briefing fails CLOSED rather
    than briefing you on other people's work."""
    from src.data import enablement_tasks as et
    from src.data import startup_greeting

    _make_task(empty_db.conn, title="Someone else's", assignee="Other Person",
               due_date="2026-07-20", status="open")
    assert startup_greeting.build_greeting_block(
        empty_db.conn, today=date(2026, 7, 20), aliases=set()) == ""


def test_mine_filter_shows_everything_without_an_asana_id_or_display_name(
        monkeypatch):
    """daily-plan: "The 'mine' filter falls back to showing everything when
    neither an Asana ID nor a display name is set for you — deliberately, so a
    new user does not see a blank board"."""
    from src.data import enablement_identity as ident
    from src.ui.pages.enablement.page import EnablementPage

    fake = type("F", (), {})()
    fake._task_scope = "mine"

    monkeypatch.setattr(ident, "operator_identity", lambda: {
        "email": "chris@cambric.ai", "name": "", "asana_gid": "",
        "source": "settings"})
    assert EnablementPage._list_filters(fake) == {}, \
        "email-only identity must fall back to show-all"

    monkeypatch.setattr(ident, "operator_identity", lambda: {
        "email": "", "name": "Chris Guffey", "asana_gid": "",
        "source": "settings"})
    assert EnablementPage._list_filters(fake) == {"assignee": "Chris Guffey"}


# ═════════════════════════════════════════════════════════════════════
#  Article: where-renn-appears.md
# ═════════════════════════════════════════════════════════════════════

def test_the_in_page_drawer_flag_ships_off():
    """where-renn-appears: "The React versions of those tabs are behind the
    `enablement.web_tabs` setting, which is not enabled in the shipped
    configuration — the native Qt tabs render instead"."""
    from src.ui.web import web_flags

    assert web_flags.web_tabs_mode() == "off"
    assert "off" in web_flags.VALID_MODES


def test_web_tabs_flag_degrades_to_off_for_any_unrecognized_value(monkeypatch):
    """where-renn-appears: the drawer is off unless the flag is deliberately
    turned on — an unrecognized value must not enable it."""
    from src.ui.web import web_flags

    for raw in ("drawer", "", "true", None, 1):
        monkeypatch.setattr(
            "src.data.settings_manager.get_section",
            lambda n, d=None, _r=raw: {"web_tabs": _r} if n == "enablement" else {})
        assert web_flags.web_tabs_mode() == "off"


def test_workbench_open_assistant_button_is_connected(qt_app):
    """where-renn-appears: "'Open Assistant ›' from the Workbench, which puts
    Renn beside the draft you are editing". Asserts the button exists AND that
    clicking it actually emits the host-facing request signal."""
    from PySide6.QtWidgets import QPushButton
    from src.ui.pages.enablement.workbench import WorkbenchPage

    page = WorkbenchPage()
    buttons = [b for b in page.findChildren(QPushButton)
               if "Open Assistant" in b.text()]
    assert len(buttons) == 1, "the Open Assistant button is missing"

    fired = []
    page.open_chat_requested.connect(lambda: fired.append(True))
    buttons[0].click()
    assert fired == [True], "Open Assistant is not wired to a handler"


def test_the_workbench_chat_context_names_the_active_draft(monkeypatch,
                                                           empty_db):
    """where-renn-appears: "The active draft should be picked up automatically
    when you open the assistant from the Workbench — you should be able to say
    'tighten the second section' without identifying which card"."""
    from src.ui.pages.enablement.page import EnablementPage

    fake = type("F", (), {})()
    fake._conn = lambda: empty_db.conn
    fake.workbench = type("W", (), {"active_draft_id": 42})()
    fake._greeting_block = None
    monkeypatch.setattr("src.data.enablement_identity.operator_email",
                        lambda resolve=True: "chris@cambric.ai")

    ctx = EnablementPage._chat_context(fake, "tighten section 2", [])
    assert "[ENABLEMENT SCOPE]" in ctx
    assert "The active draft id is 42" in ctx


def test_voice_is_absent_from_the_workbench_assistant_panel():
    """where-renn-appears: "Voice input does not appear. Voice lives on the
    Agent page only, not the Workbench panel." (Asserts the negative half —
    the Agent page's own voice wiring is QtWebEngine-bound and out of scope
    for a headless run.)"""
    import importlib

    from src.ui.pages.enablement import chat_panel

    assert importlib.util.find_spec("src.services.voice") is not None, \
        "the voice stack should exist for the Agent page"
    src = open(chat_panel.__file__, encoding="utf-8").read().lower()
    assert "voice" not in src and "microphone" not in src


def test_agent_page_and_workbench_panel_share_one_session(monkeypatch,
                                                          empty_db,
                                                          tmp_path):
    """where-renn-appears: "The Agent page and the Workbench panel resolve to
    the same chat session ... one Renn, one transcript, one continuous history."

    Verifies the FIXED behaviour (finding 5): AgentChatController._ensure_session
    and EnablementPage._ensure_session both resolve through
    resolve_or_create_session against a SHARED .current_chat_session pointer.
    The first surface to open mints the session and writes the pointer; the
    second reads the pointer and REUSES it, so both land on ONE session id and
    one transcript. This fails the day the two surfaces diverge back onto
    separate sessions.
    """
    from src.data.connection_factory import get_connection
    from src.services.agent_chat import AgentChatController
    from src.ui.pages.enablement.page import EnablementPage

    class _Engine:
        def __init__(self):
            self.session_id = None

        def set_session_id(self, sid):
            self.session_id = sid

    # Both surfaces point at the SAME warehouse DB, so both derive the same
    # pointer directory (Path(db_path).parent) and share one session store —
    # exactly the real deployment. AgentChatController._ensure_session closes
    # the connection it opens, so give it its own handle onto that file.
    agent = type("A", (), {})()
    agent._session_id = None
    agent._engine = _Engine()
    agent._db_path = lambda: str(empty_db.db_path)
    agent._open_conn = lambda readonly=False: get_connection(empty_db.db_path)
    agent._write_session_pointer = lambda sid: None

    bench = type("B", (), {})()
    bench._chat_session_id = None
    bench._engine = _Engine()
    bench._conn = lambda: empty_db.conn
    bench._engine_db_path = lambda: str(empty_db.db_path)

    AgentChatController._ensure_session(agent)
    EnablementPage._ensure_session(bench)

    assert agent._session_id and bench._chat_session_id, \
        "both surfaces should have resolved a session"

    assert agent._session_id == bench._chat_session_id, (
        "the two surfaces are documented to SHARE one session — if they now "
        "diverge onto separate sessions, the article and this test must be "
        "corrected back")

    # And it is genuinely one row, not two that happen to collide.
    n = empty_db.conn.execute(
        "SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    assert n == 1, "a duplicate enablement session was created"


# ═════════════════════════════════════════════════════════════════════
#  Article: background-research.md
# ═════════════════════════════════════════════════════════════════════

def test_no_background_research_tool_is_on_the_model_surface(model_tool_names,
                                                            registry_names):
    """background-research: "There are no background-research tools available
    to Renn. It cannot start a research job, write a research manifest, or open
    a research-plan approval card."

    The impls exist in the codebase but are registered nowhere, so Renn cannot
    call them.
    """
    for name in ("request_research_plan", "get_task_research"):
        assert name not in model_tool_names, f"{name} is model-callable"
        assert name not in registry_names, f"{name} is dispatchable"
    assert hasattr(ET, "handle_request_research_plan"), (
        "the impl is expected to exist unregistered — if it is gone, this "
        "test's premise changed")


def test_prompt_tells_renn_to_decline_research_and_offer_search(renn_prompt):
    """background-research: "Renn is explicitly instructed to say so rather than
    pretend" and "should decline plainly and immediately offer what it *can* do
    right now, which is search the content you already have"."""
    assert "Background research (NOT AVAILABLE YET)" in renn_prompt
    assert "You have NO background-research tools in this build" in renn_prompt
    for alternative in ("search_content", "search_drive_docs",
                        "search_guru_cards"):
        assert alternative in renn_prompt


def test_prompt_forbids_claiming_research_is_running_or_queued(renn_prompt):
    """background-research: "It should never tell you research is running,
    queued, or will be delivered later"."""
    assert ("NEVER claim research is running, queued, or will be delivered "
            "later") in renn_prompt


# ═════════════════════════════════════════════════════════════════════
#  Article: tool-reference.md
# ═════════════════════════════════════════════════════════════════════

def test_renn_can_search_the_help_center(model_tool_names, registry_names):
    """tool-reference: "Renn can search this Help Center and quote it back to
    you, including whether a feature is actually available."

    help_search is wired across all four sites: registered in the chat-tool
    registry, declared in the MCP TOOL_SCHEMAS the model sees, backed by a
    handler, and announced in RENN_SYSTEM_PROMPT (asserted separately). Fails if
    any site is dropped."""
    assert "help_search" in registry_names, "help_search not in the chat registry"
    assert "help_search" in model_tool_names, "help_search not in TOOL_SCHEMAS"

    from src.data.chat_tools.help_tools import handle_help_search
    assert callable(handle_help_search)


def test_help_search_handler_returns_status_bearing_results(empty_db):
    """tool-reference: results include "whether a feature is actually
    available". The handler loads the corpus on demand and each result carries
    its status."""
    from src.data.chat_tools.help_tools import handle_help_search

    out = handle_help_search(empty_db.conn, {"query": "background research"}, {})
    assert out["ok"] and out["results"], "help_search found nothing in the corpus"
    top = out["results"][0]
    assert top["article_id"] == "renn-background-research"
    assert top["status"] == "not-available"
    # The handler flags the limitation so the model states it up front.
    assert "status_note" in out


def test_help_search_prompt_paragraph_present(renn_prompt):
    """The tool is useless if the model is never told to call it."""
    assert "help_search" in renn_prompt
    assert "not-available" in renn_prompt or "STATE THAT LIMITATION" in renn_prompt


def test_finding_content_families_map_to_real_tools(model_tool_names):
    """tool-reference: the "Finding content" table — complete listing with a
    total count / ranked single-source search / one query fanned across Guru,
    Zendesk and Drive / knowledge base first / one document in full."""
    for name in ("list_guru_cards", "list_guru_folder_items",
                 "list_zendesk_articles", "list_asana_tasks",
                 "search_guru_cards", "search_zendesk_articles",
                 "search_content", "kb_search", "get_drive_doc"):
        assert name in model_tool_names


def test_publish_target_chain_lists_collections_then_folders_then_publishes(
        model_tool_names):
    """tool-reference: "List collections, then list folders inside one, then
    publish straight into the folder you name"."""
    from src.mcp.chat_mcp_server import TOOL_SCHEMAS

    assert {"list_guru_collections", "list_guru_folders"} <= model_tool_names
    push = next(t for t in TOOL_SCHEMAS if t["name"] == "push_guru_draft")
    props = push["inputSchema"]["properties"]
    assert "collection_id" in props and "folder_id" in props


def test_importing_a_guru_card_updates_that_card_on_publish(empty_db,
                                                            monkeypatch):
    """tool-reference: "Renn can also import an existing Guru card as an
    editable draft — publishing then updates that same card rather than
    creating a duplicate." Asserts the imported draft carries the source
    card_id, which is what makes the publish an update."""
    from src.data import enablement_store as store

    class _Guru:
        def __init__(self, *a, **k):
            pass

        @staticmethod
        def load_credentials():
            return ("e@x.com", "tok")

        def get_card(self, card_id):
            return {"id": card_id, "title": "Payments v2",
                    "content": "<p>Body</p>"}

    monkeypatch.setattr("src.data.guru_client.GuruClient", _Guru)
    out = ET.handle_import_guru_card(empty_db.conn, {"card_ref": "card-9"}, {})
    assert out["ok"] is True
    draft = store.get_draft(empty_db.conn, out["draft_id"])
    assert draft["card_id"] == "card-9", \
        "publishing would create a duplicate without the source card_id"


def test_style_guide_family_is_present(model_tool_names):
    """tool-reference: "Find them, read the active one so drafts follow it, and
    switch which one card generation uses"."""
    assert {"list_style_guides", "get_style_guide",
            "set_active_style_guide"} <= model_tool_names


def test_each_generator_takes_exactly_one_source(empty_db):
    """tool-reference: "Each takes exactly one source." Asserts the rule is
    ENFORCED — zero sources and two sources are both rejected before any
    generation happens."""
    from src.data.chat_tools import artifact_tools

    none_given = artifact_tools.handle_generate_diagram(empty_db.conn, {}, {})
    assert none_given["ok"] is False
    assert none_given["error"] == "source_required"

    two_given = artifact_tools.handle_generate_diagram(
        empty_db.conn, {"source_text": "a", "source_doc_id": "d1"}, {})
    assert two_given["ok"] is False
    assert two_given["error"] == "source_required"


def test_attached_artifacts_stay_review_gated(empty_db):
    """tool-reference: "Generated artifacts can be attached to a card draft,
    which you still review and approve before publishing"."""
    from src.data import artifact_store
    from src.data.chat_tools import artifact_tools

    aid = artifact_store.create_artifact(
        empty_db.conn, kind="diagram", title="Flow",
        spec={"mermaid": "graph TD; A-->B;"})
    out = artifact_tools.handle_attach_artifact(
        empty_db.conn, {"artifact_id": aid, "new_draft_title": "Card"}, {})

    assert out["ok"] is True
    assert "approves it in the Review panel" in out["note"]
    from src.data import enablement_store as store
    assert store.get_draft(empty_db.conn, out["draft_id"])["require_approval"] == 1


def test_renn_has_no_shell_file_or_web_tools(model_tool_names, renn_prompt):
    """tool-reference: "Renn should use only these capabilities. If it mentions
    shell access, file access, or browsing the web, something is wrong"."""
    banned = ("bash", "shell", "exec", "run_command", "read_file",
              "write_file", "web_fetch", "web_search", "browse")
    offenders = [n for n in model_tool_names
                 if any(b in n.lower() for b in banned)]
    assert offenders == [], f"unexpected escape-hatch tools: {offenders}"
    assert ("Do not invoke, name, or mention shell, file, web, or other tools"
            in renn_prompt)


def test_prompt_requires_every_claim_to_be_grounded_in_a_tool_result(
        renn_prompt):
    """tool-reference / what-renn-can-do: "Every answer should be grounded in a
    result from that turn. Renn should not name a document, task ID or card
    that did not come back from a tool"."""
    assert ("Ground every claim in tool results from THIS turn; never invent "
            "document names") in renn_prompt


def test_proposing_changes_covers_the_five_documented_write_proposals(
        model_tool_names):
    """tool-reference: "Asana updates, Guru folder creation and renaming, and
    Drive uploads are all proposals that open a Confirm card"."""
    assert {"request_asana_task_update", "request_create_asana_task",
            "request_create_guru_folder", "request_rename_guru_folder",
            "request_upload_artifact_to_drive"} <= model_tool_names
    assert ET.RESOLVER_ACTION_TOOLS >= {
        "request_asana_task_update", "request_create_asana_task",
        "request_create_guru_folder", "request_rename_guru_folder",
        "request_upload_artifact_to_drive"}


def test_setup_and_reporting_family_is_present(model_tool_names):
    """tool-reference: "Discover your Asana projects and save a board config;
    open pickers ...; report what is currently connected; pull fresh items from
    your sources"."""
    assert {"asana_discover", "set_asana_board_config",
            "get_enablement_routing", "run_monitor_now"} <= model_tool_names
