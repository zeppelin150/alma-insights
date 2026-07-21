"""drive_query.query_business_drive — the ``live_client`` seam nobody uses.

``query_business_drive`` (src/data/drive_query.py:27-58) branches on an
injected ``live_client``. That parameter is the ONLY way to reach live Drive
search from the chat tool, and NO production caller passes it:

    src/data/chat_tools/enablement_tools.py:28-31   handle_query_business_drive
    src/llm/claude_tools.py:1213-1216               _query_business_drive

Both call ``query_business_drive(conn, query, limit=...)`` and omit
``live_client``, so the tool ALWAYS takes the local_index branch — regardless
of ``enablement.drive.read_enabled``. The returned ``configured`` flag reports
True while the results came from the local mirror, which is the confusing part
worth pinning.

MEASURE-ONLY. These tests document current behaviour. The "no caller injects"
tests are the load-bearing ones: they will fail the day someone wires the seam,
which is exactly when this file should be revisited.

No network, no credentials, no Google libs. The fake client is defined locally
(tests/conftest.py is owned by another workstream — do not add fixtures there).
"""

from __future__ import annotations

import inspect

import pytest

from src.data import enablement_store as S
from src.data.drive_query import query_business_drive


# ── local fakes (deliberately NOT in conftest.py) ──────────────────────────

class FakeLiveClient:
    """Stands in for DriveReader on the live_client seam."""

    def __init__(self, results=None, raises: Exception | None = None):
        self._results = results if results is not None else []
        self._raises = raises
        self.calls: list[tuple[str, int]] = []

    def search_files(self, query, limit=20):
        self.calls.append((query, limit))
        if self._raises is not None:
            raise self._raises
        return list(self._results)


_LIVE_ROWS = [
    {"name": "Prior Auth Runbook", "id": "d1",
     "url": "https://drive/d1", "mime_type": "application/pdf"},
    {"name": "Claims 2026", "id": "d2",
     "url": "https://drive/d2", "mime_type": "application/vnd.google-apps.document"},
]


def _seed_doc(conn, *, source, name, text, doc_id=None,
              modified="2026-06-01T00:00:00Z"):
    return S.save_document(conn, source=source, doc_id=doc_id, name=name,
                           source_ref=doc_id or name, mime_type="text/plain",
                           web_url=f"https://x/{name}", modified_time=modified,
                           full_text=text)


# ── live branch ────────────────────────────────────────────────────────────

def test_live_client_results_pass_through_unmangled(empty_db):
    client = FakeLiveClient(_LIVE_ROWS)
    out = query_business_drive(empty_db.conn, "prior auth", limit=5, live_client=client)

    assert out["mode"] == "live"
    assert out["configured"] is True
    assert out["count"] == 2
    # rows are handed back exactly as the client produced them — no reshaping,
    # no filtering, no re-ranking happens in this layer.
    assert out["results"] == _LIVE_ROWS
    # and the query/limit reached the client untouched
    assert client.calls == [("prior auth", 5)]


def test_live_client_error_is_returned_not_raised(empty_db):
    """A Drive failure must degrade to a result dict the chat tool can render,
    never an exception that unwinds the tool call."""
    client = FakeLiveClient(raises=RuntimeError("HttpError 403 insufficient scope"))
    out = query_business_drive(empty_db.conn, "q", live_client=client)

    assert out["mode"] == "live"
    assert out["configured"] is True
    assert out["results"] == []
    assert "403" in out["error"]
    assert "count" not in out          # the error shape omits count (drive_query.py:46)


def test_live_branch_never_touches_the_local_mirror(empty_db):
    """A live client short-circuits before store.search_documents — so a live
    run cannot be silently topped up with stale mirror rows."""
    conn = empty_db.conn
    _seed_doc(conn, source="drive", name="Mirror Only Doc", text="prior auth mirror text")
    out = query_business_drive(conn, "prior auth", live_client=FakeLiveClient([]))

    assert out["mode"] == "live"
    assert out["results"] == []        # mirror row NOT merged in


# ── local_index branch ─────────────────────────────────────────────────────

def test_local_branch_filters_strictly_to_drive_source(empty_db):
    """store.search_documents returns every source; query_business_drive keeps
    only source == 'drive' (drive_query.py:48-49)."""
    conn = empty_db.conn
    _seed_doc(conn, source="drive", name="Drive Runbook", text="shared needle text")
    _seed_doc(conn, source="guru", name="Guru Runbook", text="shared needle text")
    _seed_doc(conn, source="zendesk", name="Zendesk Runbook", text="shared needle text")

    out = query_business_drive(conn, "shared needle", limit=10)

    assert out["mode"] == "local_index"
    assert {r["source"] for r in out["results"]} == {"drive"}
    assert out["count"] == 1


def test_local_branch_reports_mode_and_note(empty_db):
    out = query_business_drive(empty_db.conn, "anything")
    assert out["mode"] == "local_index"
    assert out["results"] == []
    assert "locally-indexed mirror" in out["note"]


def test_local_branch_limit_is_applied_before_the_drive_filter(empty_db):
    """DEFECT (measure-only, UNCHANGED by the search fix): the limit is passed to
    search_documents, then the source filter is applied to what came back. Higher-
    ranked non-drive documents therefore consume the budget. Here 10 guru docs
    whose TITLES match the query outrank the single drive doc (title miss, body
    hit), so a limit=10 drive query returns ZERO drive rows even though the drive
    doc matches and clears the relevance floor. The fix landed in the ranker, not
    in this filter/limit ordering — which is why this defect still stands.
    """
    conn = empty_db.conn
    # Drive doc matches in the BODY only (title miss) -> ranks below title matches.
    _seed_doc(conn, source="drive", name="Reference Sheet",
              text="provider onboarding overview for new hires", doc_id="drive-body")
    for i in range(10):
        _seed_doc(conn, source="guru", name=f"Provider Onboarding {i}",
                  text="provider onboarding steps", doc_id=f"guru-{i}")

    out = query_business_drive(conn, "provider onboarding", limit=10)

    # The drive row matches and is retrievable at a generous limit...
    assert any(d["source"] == "drive"
               for d in S.search_documents(conn, "provider onboarding", limit=50)), \
        "sanity: the drive row is retrievable"
    # ...but the 10 title-matching guru docs fill the limited page before the filter.
    assert out["count"] == 0, (
        "if this now returns the drive row, the limit/filter order was fixed — "
        "update docs/DRIVE_SEARCH_EVAL.md")


def test_local_branch_tokenizes_multi_word_queries(empty_db):
    """FIXED (was the single biggest recall limiter and the reason the eval
    exists): enablement_store.search_documents now tokenizes and ranks over
    enablement_documents_fts (migration 050 + src/data/enablement_doc_search.py)
    instead of wrapping the whole query in one `LIKE %<query>%`. A multi-word
    question — words in any order, with natural-language filler — now matches.
    """
    conn = empty_db.conn
    _seed_doc(conn, source="drive", name="Prior Authorization Runbook",
              text="Aetna requires a prior authorization for imaging in 2026.")

    # exact contiguous phrase -> hit (unchanged)
    assert query_business_drive(conn, "prior authorization", limit=5)["count"] == 1
    # same words, natural question phrasing -> now a HIT (was a MISS under LIKE)
    assert query_business_drive(conn, "aetna prior authorization", limit=5)["count"] == 1
    # reordered -> now a HIT
    assert query_business_drive(conn, "authorization prior", limit=5)["count"] == 1


# ── the seam is never populated in production ──────────────────────────────

def test_enablement_tools_caller_omits_live_client():
    """handle_query_business_drive (enablement_tools.py:28-31) forwards only
    conn/query/limit."""
    from src.data.chat_tools import enablement_tools
    src = inspect.getsource(enablement_tools.handle_query_business_drive)
    assert "query_business_drive(" in src
    assert "live_client" not in src, (
        "the seam is now wired — Stage 2 is reachable from chat; revisit "
        "docs/DRIVE_SEARCH_EVAL.md and this test")


def test_claude_tools_caller_omits_live_client():
    """claude_tools._query_business_drive (claude_tools.py:1213-1216) likewise."""
    from src.llm import claude_tools
    src = inspect.getsource(claude_tools._query_business_drive)
    assert "query_business_drive(" in src
    assert "live_client" not in src, (
        "the seam is now wired — revisit docs/DRIVE_SEARCH_EVAL.md and this test")


def test_read_enabled_does_not_reach_the_live_branch(empty_db, monkeypatch):
    """The confusing shape: with read_enabled=True the response says
    configured=True while mode is still local_index. `configured` describes a
    SETTING, not the branch that ran — a report reader must not read it as
    'these results came from Drive'."""
    import src.data.drive_query as dq
    monkeypatch.setattr(dq, "is_live_drive_configured", lambda: True)

    out = query_business_drive(empty_db.conn, "q")
    assert out["configured"] is True
    assert out["mode"] == "local_index"


# ── the recursive= parameter that is never read ────────────────────────────

class _RecursionProbeReader:
    """Records the folder ids it was asked about; serves a two-level tree.

    Mirrors the FakeDriveReader shape in tests/test_drive_monitor.py:22-37,
    kept local per the no-conftest-edits rule.
    """

    TREE = {
        "root": [{"id": "top", "name": "Top Level.gdoc",
                  "mimeType": "application/vnd.google-apps.document",
                  "modifiedTime": "2026-06-01T00:00:00Z",
                  "webViewLink": "https://docs/top"}],
        "sub": [{"id": "deep", "name": "Buried Needle.gdoc",
                 "mimeType": "application/vnd.google-apps.document",
                 "modifiedTime": "2026-06-02T00:00:00Z",
                 "webViewLink": "https://docs/deep"}],
    }

    def __init__(self):
        self.listed: list[tuple[str, bool]] = []

    def is_configured(self):
        return True

    def list_changed_files(self, folder_id, *, modified_after=None,
                           recursive=True, mime_types=None):
        self.listed.append((folder_id, recursive))
        return list(self.TREE.get(folder_id, []))

    def list_folders(self, parent_id):
        return [{"id": "sub", "name": "Subfolder", "drive_id": None}] \
            if parent_id == "root" else []

    def export_text(self, file_id, mime_type):
        return f"body text for {file_id}"


def test_real_reader_ignores_recursive_parameter():
    """DEFECT (measure-only): DriveReader.list_changed_files accepts
    ``recursive`` (drive_reader.py:160) and NEVER READS IT. The q it builds is
    always `<folder_id> in parents` — direct children only.

    Asserted structurally against the real method so it cannot pass by
    accident: the parameter is in the signature but absent from the body.
    """
    from src.data.drive_reader import DriveReader

    sig = inspect.signature(DriveReader.list_changed_files)
    assert "recursive" in sig.parameters, "parameter removed — defect fixed?"

    body = inspect.getsource(DriveReader.list_changed_files)
    # strip the signature line(s) so the parameter's declaration doesn't count
    body_only = body.split('"""', 2)[-1]
    assert "recursive" not in body_only, (
        "list_changed_files now reads `recursive` — the subfolder gap may be "
        "fixed; update docs/DRIVE_SEARCH_EVAL.md")


def test_monitor_passes_recursive_true_and_still_misses_subfolders(empty_db):
    """The live consequence: drive_monitor.poll_once passes recursive=True
    (drive_monitor.py:57-59) believing it will walk the tree. It does not —
    'Buried Needle' in the subfolder is never indexed.

    Contrast kb.ingest.enumerate_folder (ingest.py:85-105), which passes
    recursive=False and does its OWN breadth-first walk via list_folders. So
    the KB indexer DOES reach subfolders; only the monitor path is blind.
    """
    from src.data import enablement_sim as SIM
    from src.data import enablement_sources as sources
    from src.data.drive_monitor import poll_once

    conn = empty_db.conn
    sources.add_source(conn, source_type="drive", source_id="drive:root",
                       display_name="Root", config={"folder_id": "root",
                                                    "recursive": True})
    reader = _RecursionProbeReader()
    out = poll_once(conn, reader=reader, llm_client=SIM._StubLLM())

    # the monitor asked for recursion...
    assert reader.listed == [("root", True)]
    # ...and got exactly one direct child; the subfolder was never enumerated
    assert len(out["documents"]) == 1
    names = {d["name"] for d in S.list_documents(conn)}
    assert "Top Level.gdoc" in names
    assert "Buried Needle.gdoc" not in names, (
        "subfolder file now indexed — the recursive= gap is fixed")


def test_ingest_enumerate_folder_does_reach_subfolders():
    """The contrasting contract: kb.ingest.enumerate_folder walks explicitly,
    so depth-1 files ARE found. Pinning this keeps the defect scoped to the
    monitor rather than being blamed on the whole ingest path."""
    from src.data.kb.ingest import enumerate_folder

    reader = _RecursionProbeReader()
    files, truncated = enumerate_folder(reader, "root")

    assert not truncated
    assert {f["id"] for f in files} == {"top", "deep"}
    # it explicitly opts OUT of the broken parameter
    assert all(rec is False for _, rec in reader.listed)


@pytest.mark.parametrize("depth_cap,expected", [(0, {"top"}), (1, {"top", "deep"})])
def test_enumerate_folder_respects_max_depth(depth_cap, expected):
    from src.data.kb.ingest import enumerate_folder
    files, _ = enumerate_folder(_RecursionProbeReader(), "root", max_depth=depth_cap)
    assert {f["id"] for f in files} == expected
