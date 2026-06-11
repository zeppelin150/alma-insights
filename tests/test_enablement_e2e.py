"""End-to-end tests for the Enablement Workbench — real pipeline, stubbed APIs.

Only the external boundaries are stubbed (LLM .generate(), GuruClient HTTP,
AsanaClient HTTP, the Drive reader). EVERYTHING else is the real thing: the
document store, the task spine, the chat-tool registry/dispatch, the Asana/Drive
poll_once cores, the EnablementMonitor orchestrator, and the live ChatEngine
tool loop. These prove the new functionality works wired together.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from src.data import asana_setup
from src.data import enablement_sources as sources
from src.data import enablement_store as S
from src.data import enablement_tasks as T
from src.data.chat_tools.registry import dispatch_tool
from src.data.connection_factory import get_connection

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ── stubs for the external API boundaries ────────────────────────────

class StubLLM:
    """Deterministic card generator in the TITLE:/---/body shape draft_card uses."""
    model = "stub-llm"

    def __init__(self, title="Generated Card", body="Card body."):
        self._title, self._body = title, body

    def generate(self, prompt, system_prompt="", **kw):
        return f"TITLE: {self._title}\n---\n{self._body}"


class FakeDriveReader:
    def __init__(self, files, texts):
        self._files, self._texts = files, texts

    def is_configured(self):
        return True

    def list_changed_files(self, folder_id, *, modified_after=None, recursive=True, mime_types=None):
        return [f for f in self._files if not modified_after or f.get("modifiedTime", "") > modified_after]

    def export_text(self, file_id, mime_type):
        return self._texts.get(file_id, "")


def _stub_asana(tasks):
    c = MagicMock()
    c.api_key = "stub"
    c.list_tasks.return_value = tasks
    return c


_DRIVE_FILES = [{"id": "f1", "name": "SSO Setup.gdoc",
                 "mimeType": "application/vnd.google-apps.document",
                 "modifiedTime": "2026-06-09T10:00:00Z", "webViewLink": "https://d/f1"}]
_DRIVE_TEXTS = {"f1": "Provider SSO self-serve launches June 24, 2026."}


def _asana_match_task():
    return {
        "gid": "atask1", "name": "Enable SSO for providers", "due_on": "2026-06-25",
        "permalink_url": "https://app.asana.com/0/1/atask1", "completed": False,
        "modified_at": "2026-06-09T09:00:00Z", "assignee": {"name": "M. Chen"},
        "custom_fields": [
            {"gid": "team", "name": "Assigned Team",
             "enum_value": {"gid": "enab", "name": "Enablement"}},
            {"gid": "urg", "name": "Urgency",
             "enum_value": {"gid": "hi", "name": "High"}, "display_value": "High"},
        ],
    }


def _configure_asana(conn):
    asana_setup.set_asana_board_config(
        conn, project_gid="proj1", project_name="Enablement Requests",
        indicator_field_gid="team", indicator_field_name="Assigned Team",
        indicator_value_gid="enab", indicator_value_name="Enablement",
        priority_field_gid="urg", assignee_field_gid=None)


# ── E2E 1: Drive doc → card draft → revise → push to Guru → done ─────

def test_e2e_drive_doc_to_guru_card(empty_db, mock_guru_client, monkeypatch):
    conn = empty_db.conn

    # (1) Extract: a stubbed Drive folder is ingested → indexed + drafted + tasked.
    sources.add_source(conn, source_type="drive", source_id="drive:fold1",
                       display_name="Product Docs", config={"folder_id": "fold1"})
    from src.data import drive_monitor
    out = drive_monitor.poll_once(conn, reader=FakeDriveReader(_DRIVE_FILES, _DRIVE_TEXTS),
                                  llm_client=StubLLM("SSO Setup", "Original intro."))
    assert len(out["documents"]) == 1 and len(out["drafts"]) == 1 and len(out["tasks"]) == 1
    draft_id, task_id = out["drafts"][0], out["tasks"][0]
    assert S.get_draft(conn, draft_id)["status"] == "pending"

    # (2) Transform: operator revises the draft through the chat tool (stubbed LLM).
    monkeypatch.setattr("src.gemini.client_factory.build_client_for_task",
                        lambda *a, **k: StubLLM("SSO Setup v2", "Tighter intro."))
    rev = json.loads(dispatch_tool("revise_draft",
                                   {"draft_id": draft_id, "instruction": "tighten the intro"}, conn))
    assert rev["ok"]
    assert S.get_draft(conn, draft_id)["title"] == "SSO Setup v2"

    # (3) Load: operator pushes to Guru through the chat tool (stubbed GuruClient).
    guru = MagicMock()
    guru.load_credentials.return_value = ("ops@alma.com", "tok")
    guru.return_value = mock_guru_client
    monkeypatch.setattr("src.data.guru_client.GuruClient", guru)
    pub = json.loads(dispatch_tool("push_guru_draft",
                                   {"draft_id": draft_id, "collection_id": "coll-1"}, conn))
    assert pub["ok"] and pub["status"] == "pushed"
    mock_guru_client.create_card.assert_called_once()

    # (4) Close the loop: mark the review task done.
    json.loads(dispatch_tool("update_task", {"task_id": task_id, "status": "done"}, conn))
    assert T.get_task(conn, task_id)["status"] == "done"


# ── E2E 2: Asana board poll → auto-created enablement task ───────────

def test_e2e_asana_board_to_task(empty_db):
    conn = empty_db.conn
    _configure_asana(conn)
    from src.data import asana_monitor
    created = asana_monitor.poll_once(conn, client=_stub_asana([_asana_match_task()]))
    assert len(created) == 1
    task = T.list_tasks(conn, source="asana")[0]
    assert task["title"] == "Enable SSO for providers"
    assert task["priority"] == "high"
    assert task["assignee"] == "M. Chen"
    assert task["due_date"] == "2026-06-25"


# ── E2E 3: the orchestrator polls BOTH sources in one scan ───────────

def test_e2e_orchestrator_scan_all(qapp, tmp_path, monkeypatch):
    from src.data.db_manager import DatabaseManager
    from src.data.asana_client import AsanaClient
    from src.data.drive_reader import DriveReader
    from src.data.enablement_monitor import EnablementMonitor

    db = DatabaseManager(db_path=tmp_path / "e2e_orch.db")
    db.initialize()
    conn = db.conn
    _configure_asana(conn)
    sources.add_source(conn, source_type="drive", source_id="drive:fold1",
                       display_name="Docs", config={"folder_id": "fold1"})

    monkeypatch.setattr(AsanaClient, "from_store",
                        staticmethod(lambda: _stub_asana([_asana_match_task()])))
    monkeypatch.setattr(DriveReader, "from_settings",
                        staticmethod(lambda: FakeDriveReader(_DRIVE_FILES, _DRIVE_TEXTS)))
    monkeypatch.setattr("src.gemini.client_factory.build_client_for_task",
                        lambda *a, **k: StubLLM("SSO Setup", "Body."))

    monitor = EnablementMonitor(db)
    fired = []
    monitor.changed.connect(lambda: fired.append(1))
    monitor._scan_all_worker()                     # synchronous core of scan_all()

    assert fired == [1]
    assert len(T.list_tasks(conn, source="asana")) == 1
    assert len(T.list_tasks(conn, source="drive")) == 1     # card_review from the Drive doc
    assert len(S.list_drafts(conn)) == 1


# ── E2E 4: the LIVE ChatEngine tool loop drives a real revise ───────

def test_e2e_renn_chat_tool_loop(qapp, tmp_path, monkeypatch):
    from src.data.db_manager import DatabaseManager
    from src.services.chat_engine import ChatEngine

    db = DatabaseManager(db_path=tmp_path / "e2e_chat.db")
    db.initialize()
    draft_id = S.save_card_draft(db.conn, title="SSO", content="old body")

    class ChatStub:
        model = "stub-chat"

        def generate(self, prompt, system_prompt="", **kw):
            # First turn → ask to revise; after the tool result → final answer.
            if "TOOL_RESULT" in prompt:
                return "Done — I tightened the intro and re-rendered the card."
            return ('TOOL_CALL: revise_draft '
                    f'{{"draft_id": {draft_id}, "instruction": "tighten the intro"}}')

    def factory(task_type, use_bridge=False):
        return StubLLM("SSO v2", "Tighter intro.") if task_type == "enablement_card_gen" else ChatStub()

    monkeypatch.setattr("src.gemini.client_factory.build_client_for_task", factory)

    engine = ChatEngine(system_prompt="You are Renn.", task_type="enablement_chat",
                        tools_enabled=True, use_mcp_tools=False, db_path=str(db.db_path))
    got: list[str] = []
    engine.response_ready.connect(lambda txt: got.append(txt))
    engine.send("tighten the intro of the active draft")

    deadline = time.time() + 10
    while not got and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    assert got, "ChatEngine never produced a final response"
    assert "tightened" in got[0].lower()
    # the tool ran the full loop → the draft was revised in the DB file
    fresh = get_connection(str(db.db_path))
    assert S.get_draft(fresh, draft_id)["title"] == "SSO v2"
    fresh.close()


# ── E2E 5: REAL Gemini — content in → Guru card out (opt-in) ─────────

_LIVE_DOC = """Bulk Claims Resubmission — Provider Portal (v2.4)

Summary: Providers can now resubmit up to 500 denied claims in a single batch from
the Claims tab, instead of one at a time. This ships to all providers on
July 15, 2026.

Details:
- A new "Resubmit Batch" button appears on the Claims > Denied view.
- Supported denial reasons: CO-16 (missing information), CO-97 (bundled), and PR-1 (deductible).
- Each batch is capped at 500 claims; larger sets are split automatically.
- Resubmissions are processed within 48 hours; status shows under Claims > Activity.
- Integrations: works with Availity and Change Healthcare clearinghouses.
- Claims older than 365 days cannot be resubmitted and are skipped with a warning.
- The "claims.resubmit" admin permission is required; it is granted to the
  Billing Manager role by default.
"""


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ALMA_LIVE"),
                    reason="real-Gemini test — set ALMA_LIVE=1 to run")
def test_e2e_live_gemini_card_from_doc(empty_db):
    """Content in → Guru card out via the REAL Gemini CLI. Prints the test doc and
    Gemini's output (run with -s to see them), then asserts the card is grounded,
    non-trivial, and un-mangled (aggressive redaction stays off for enablement)."""
    from src.gemini.client_factory import build_client_for_task
    conn = empty_db.conn

    print("\n" + "=" * 72)
    print("TEST DOC  (input to Gemini)")
    print("=" * 72)
    print(_LIVE_DOC)

    doc_id = S.save_document(conn, source="drive", doc_id="live-claims-1",
                             name="Bulk Claims Resubmission.gdoc", full_text=_LIVE_DOC)
    llm = build_client_for_task("enablement_card_gen")
    assert getattr(llm, "pii_redaction", None) is False, "enablement must disable aggressive redaction"
    print(f"\n[gemini] {type(llm).__name__} model={getattr(llm, 'model', '?')} — generating card…\n")

    draft = S.draft_card_from_document(conn, doc_id, llm, collection="Provider Enablement")
    content = draft["content"] or ""

    print("=" * 72)
    print(f"GEMINI OUTPUT  (Guru card draft id={draft['id']}, {len(content)} chars)")
    print("=" * 72)
    print(f"# {draft['title']}\n")
    print(content)
    print("=" * 72)

    # Real, grounded, un-mangled.
    assert len(content) > 200, "card is suspiciously short"
    assert "[NAME]" not in content, "aggressive name-redaction leaked into the card"
    facts = ["Availity", "Change Healthcare", "500", "July 15", "Billing Manager", "48 hours"]
    kept = [f for f in facts if f.lower() in content.lower()]
    assert len(kept) >= 3, f"card dropped too many source facts; kept only {kept}"
