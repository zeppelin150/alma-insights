"""Live Google Drive search tools for Renn + the local/live split.

Covers the tools wired in the "seamless local + live Drive search" build:
  * search_google_drive  — live Drive via the API (find-without-sync)
  * import_drive_doc      — the bridge: pull a Drive file into the local library
  * search_everywhere     — merge local + live, deduped by Drive file id
  * query_business_drive  — folder_id passthrough on the live seam

Hermetic: ``build_live_drive_client`` is monkeypatched, so nothing reads
settings or touches the network. Also asserts the three new tools are registered
on every surface the two chat providers dispatch through.

Run: python -m pytest tests/test_drive_live_search_tools.py -q
"""

from __future__ import annotations

import pytest

from src.data import enablement_store as S
from src.data.chat_tools import enablement_tools as ET

_BLDR = "src.data.drive_query.build_live_drive_client"


class _FakeReader:
    """Stands in for a live DriveReader on the search_files seam."""

    def __init__(self, files):
        self._files = files
        self.calls: list[dict] = []

    def search_files(self, query, limit=20, folder_id=None):
        self.calls.append({"query": query, "limit": limit, "folder_id": folder_id})
        return list(self._files)


# ── search_google_drive (live) ───────────────────────────────────────
#
# Since 2026-07-22 the handler AUTO-SCOPES to enablement.drive.active_folders
# (recursive) unless the model passes folder_id or scope='all' — so these
# tests pin settings via temp_settings instead of letting the handler read the
# operator's real config. The result's `scope` block must always state what
# was ACTUALLY searched (the anti-confabulation contract).


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch):
    import src.data.settings_manager as sm
    path = tmp_path / "settings.yaml"
    monkeypatch.setattr(sm, "get_settings_path", lambda: path)
    return path


def _set_active_folders(folders):
    from src.data.settings_manager import set_section
    set_section("enablement", {"drive": {"active_folders": folders}})


def test_search_google_drive_returns_live_results(empty_db, temp_settings, monkeypatch):
    fake = _FakeReader([{"id": "f1", "name": "Deck.pptx", "url": "u",
                         "mime_type": "m", "modified": "2026-07-01"}])
    monkeypatch.setattr(_BLDR, lambda: fake)
    out = ET.handle_search_google_drive(empty_db.conn, {"query": "pricing", "limit": 5}, {})
    assert out["ok"] and out["mode"] == "live" and out["count"] == 1
    assert out["results"][0]["id"] == "f1"
    assert fake.calls[0]["query"] == "pricing" and fake.calls[0]["limit"] == 5


def test_search_google_drive_defaults_to_active_folders_recursively(
        empty_db, temp_settings, monkeypatch):
    """The 2026-07-22 finding: the picker set an active folder but searches ran
    whole-Drive (and the model narrated them as scoped). Default scope is now
    the active folder(s) — including LEGACY plain-string entries."""
    fake = _FakeReader([])
    monkeypatch.setattr(_BLDR, lambda: fake)
    _set_active_folders(["LEGACY-WRAPPER", {"id": "TOP", "name": "top"}])
    out = ET.handle_search_google_drive(empty_db.conn, {"query": "q"}, {})
    assert fake.calls[0]["folder_id"] == ["LEGACY-WRAPPER", "TOP"]
    assert out["scope"] == {"kind": "active_folders",
                            "folder_ids": ["LEGACY-WRAPPER", "TOP"],
                            "recursive": True}


def test_search_google_drive_explicit_folder_wins(empty_db, temp_settings, monkeypatch):
    fake = _FakeReader([])
    monkeypatch.setattr(_BLDR, lambda: fake)
    _set_active_folders([{"id": "ACTIVE-1"}])
    out = ET.handle_search_google_drive(
        empty_db.conn, {"query": "q", "folder_id": "FOLDER-9"}, {})
    assert fake.calls[0]["folder_id"] == "FOLDER-9"
    assert out["scope"]["kind"] == "explicit_folder"
    assert out["scope"]["folder_ids"] == ["FOLDER-9"]


def test_search_google_drive_scope_all_bypasses_active_folders(
        empty_db, temp_settings, monkeypatch):
    fake = _FakeReader([])
    monkeypatch.setattr(_BLDR, lambda: fake)
    _set_active_folders([{"id": "ACTIVE-1"}])
    out = ET.handle_search_google_drive(
        empty_db.conn, {"query": "q", "scope": "all"}, {})
    assert fake.calls[0]["folder_id"] is None
    assert out["scope"]["kind"] == "all_visible"


def test_search_google_drive_no_active_folders_searches_all_and_says_so(
        empty_db, temp_settings, monkeypatch):
    fake = _FakeReader([])
    monkeypatch.setattr(_BLDR, lambda: fake)
    out = ET.handle_search_google_drive(empty_db.conn, {"query": "q"}, {})
    assert fake.calls[0]["folder_id"] is None
    assert out["scope"]["kind"] == "all_visible"
    assert "no active Drive folder" in out["scope"].get("note", "")


def test_search_google_drive_not_configured(empty_db, monkeypatch):
    monkeypatch.setattr(_BLDR, lambda: None)
    out = ET.handle_search_google_drive(empty_db.conn, {"query": "q"}, {})
    assert out["ok"] is False and out["error"] == "drive_not_configured"


# ── import_drive_doc (the bridge) ────────────────────────────────────

def test_import_drive_doc_requires_ref(empty_db, monkeypatch):
    monkeypatch.setattr(_BLDR, lambda: object())
    out = ET.handle_import_drive_doc(empty_db.conn, {}, {})
    assert out["ok"] is False and out["error"] == "drive_ref_required"


def test_import_drive_doc_not_configured(empty_db, monkeypatch):
    monkeypatch.setattr(_BLDR, lambda: None)
    out = ET.handle_import_drive_doc(empty_db.conn, {"drive_ref": "abc"}, {})
    assert out["ok"] is False and out["error"] == "drive_not_configured"


def test_import_drive_doc_makes_it_locally_searchable(empty_db, monkeypatch):
    class _R:
        def get_file(self, fid):
            return {"id": fid, "name": "Imported Primer.gdoc",
                    "mime_type": "application/vnd.google-apps.document",
                    "url": "https://drive/x", "modified_time": "2026-07-01"}

        def export_text(self, fid, mime):
            return "The eligibility recheck timeline is thirty days for this payer."

    monkeypatch.setattr(_BLDR, lambda: _R())
    out = ET.handle_import_drive_doc(empty_db.conn, {"drive_ref": "FILEID1"}, {})
    assert out["ok"] and out["doc_id"] == "FILEID1"
    # the bridge worked: the freshly-imported doc is now tokenized-searchable
    hits = S.search_documents(empty_db.conn, "eligibility recheck timeline")
    assert hits and hits[0]["doc_id"] == "FILEID1"


# ── search_everywhere (merge local + live) ───────────────────────────

def test_search_everywhere_dedupes_against_whole_library(empty_db, monkeypatch):
    conn = empty_db.conn
    # a doc that lives ONLY in Alma (never uploaded to Drive)
    S.save_document(conn, source="upload", doc_id="local-only",
                    name="Internal Reference", full_text="secret pricing model details")
    # a mirrored Drive doc that MATCHES the query (doc_id == Drive file id)
    S.save_document(conn, source="drive", doc_id="DRIVE-1", source_ref="DRIVE-1",
                    name="Shared Deck", full_text="pricing model overview")
    # a mirrored Drive doc that does NOT match the query — so it is NOT a local
    # search hit, but it IS in the library and must still be deduped.
    S.save_document(conn, source="drive", doc_id="DRIVE-3", source_ref="DRIVE-3",
                    name="Unrelated", full_text="veterinary onboarding checklist")
    # live Drive returns both mirrored docs + a genuinely new one
    fake = _FakeReader([{"id": "DRIVE-1", "name": "Shared Deck"},
                        {"id": "DRIVE-3", "name": "Unrelated"},
                        {"id": "DRIVE-2", "name": "New Product Folder Doc"}])
    monkeypatch.setattr(_BLDR, lambda: fake)

    out = ET.handle_search_everywhere(conn, {"query": "pricing model", "limit": 10}, {})
    assert out["ok"] and out["drive_available"] is True
    assert {d["doc_id"] for d in out["local"]} == {"local-only", "DRIVE-1"}
    # Only the genuinely-new Drive doc survives: DRIVE-1 (a local hit) AND DRIVE-3
    # (mirrored but not a local hit) are both deduped against the library.
    assert {d["id"] for d in out["google_drive"]} == {"DRIVE-2"}


def test_search_everywhere_without_drive(empty_db, monkeypatch):
    S.save_document(empty_db.conn, source="upload", doc_id="d1",
                    name="Local Doc", full_text="onboarding steps for new hires")
    monkeypatch.setattr(_BLDR, lambda: None)
    out = ET.handle_search_everywhere(empty_db.conn, {"query": "onboarding"}, {})
    assert out["ok"] and out["drive_available"] is False
    assert out["drive_count"] == 0 and out["local_count"] == 1


# ── query_business_drive folder_id passthrough ───────────────────────

def test_query_business_drive_forwards_folder_id_when_set(empty_db):
    from src.data.drive_query import query_business_drive
    fake = _FakeReader([{"id": "x"}])
    query_business_drive(empty_db.conn, "q", live_client=fake, folder_id="F1")
    assert fake.calls[-1]["folder_id"] == "F1"


# ── registration on every dispatch surface ───────────────────────────

_NEW_TOOLS = ["search_google_drive", "import_drive_doc", "search_everywhere"]


@pytest.mark.parametrize("name", _NEW_TOOLS)
def test_new_tool_registered_in_shared_registry(name):
    from src.data.chat_tools.registry import get_tool_registry
    assert name in get_tool_registry()


@pytest.mark.parametrize("name", _NEW_TOOLS)
def test_new_tool_registered_for_claude(name):
    from src.llm import claude_tools
    assert name in claude_tools._DISPATCH
    assert name in {d["name"] for d in claude_tools.TOOL_DEFINITIONS}


@pytest.mark.parametrize("name", _NEW_TOOLS)
def test_new_tool_declared_for_mcp(name):
    from src.mcp import chat_mcp_server
    assert name in {t["name"] for t in chat_mcp_server.TOOL_SCHEMAS}
