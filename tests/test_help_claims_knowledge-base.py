"""Accuracy audit of the in-app Help Center — section: knowledge-base.

Every test below settles ONE falsifiable claim made by an article in
``assets/help/knowledge-base/``. Each test docstring names the article and
quotes (or closely paraphrases) the claim under test.

Where the article claims X and the code does NOT-X, the test asserts the
ARTICLE'S claim and is marked ``xfail(strict=True)`` with a reason naming what
the code actually does. Those are the CONTRADICTED findings; the suite stays
green while every discrepancy remains explicit and tracked.

Scope: ``src/data/kb/*.py`` (card_format, store, drive_kb, ingest, sync,
search, worker), ``src/data/chat_tools/kb_tools.py``, the KB card in
``src/ui/pages/enablement/settings.py``, and the KBWorker wiring in
``src/data/enablement_monitor.py``.

Headless, offline: fake Drive exporter/reader, fake LLM client, no network,
no credentials, no QtWebEngine.

Run: python -m pytest "tests/test_help_claims_knowledge-base.py" -q
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import src.data.kb.drive_kb as DK  # noqa: E402
from src.data.kb import card_format as CF  # noqa: E402
from src.data.kb import ingest as ING  # noqa: E402
from src.data.kb import search as SEARCH  # noqa: E402
from src.data.kb import store as STORE  # noqa: E402
from src.data.kb import sync as SYNC  # noqa: E402
from src.data.kb import worker as WORKER  # noqa: E402
from src.data.kb.drive_kb import KBWriteDenied  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
KB_DIR = REPO / "src" / "data" / "kb"
ARTICLES = REPO / "assets" / "help" / "knowledge-base"


# ── fake Drive ───────────────────────────────────────────────────────

class FakeExporter:
    """In-memory stand-in for GoogleDriveExporter's throttled write surface."""

    def __init__(self):
        self.folders: dict[str, dict] = {}
        self.files: dict[str, dict] = {}
        self.counter = 0
        self.calls: list[tuple] = []
        self.fail_updates = False

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    def _stamp(self) -> str:
        self.counter += 1
        return f"2026-07-13T{self.counter:02d}:00:00Z"

    def create_folder(self, name, parent_id=None, *, app_properties=None):
        fid = self._id("fold")
        self.folders[fid] = {"id": fid, "name": name, "parent": parent_id,
                             "trashed": False, "canAddChildren": True}
        self.calls.append(("create_folder", name, parent_id))
        return {"id": fid, "name": name}

    def upload_file(self, filename, data, mime_type, folder_id=None,
                    *, app_properties=None):
        fid = self._id("file")
        self.files[fid] = {"id": fid, "name": filename, "folder": folder_id,
                           "content": data, "props": dict(app_properties or {}),
                           "modifiedTime": self._stamp()}
        self.calls.append(("upload_file", filename, folder_id))
        return {"id": fid, "name": filename}

    def update_file(self, file_id, content, mime_type="text/markdown"):
        if self.fail_updates:
            raise RuntimeError("HTTP 403: insufficient permissions")
        f = self.files[file_id]
        f["content"] = content
        f["modifiedTime"] = self._stamp()
        self.calls.append(("update_file", file_id))
        return {"id": file_id, "modifiedTime": f["modifiedTime"]}

    def get_file_meta(self, file_id, fields=""):
        obj = self.folders.get(file_id) or self.files.get(file_id)
        if obj is None:
            raise RuntimeError("HTTP 404: not found")
        return {"id": file_id, "trashed": obj.get("trashed", False),
                "modifiedTime": obj.get("modifiedTime", ""),
                "capabilities": {
                    "canAddChildren": obj.get("canAddChildren", True)}}

    def find_child_by_app_property(self, folder_id, key, value):
        for f in self.files.values():
            if f.get("folder") == folder_id and f["props"].get(key) == value:
                return {"id": f["id"], "name": f["name"],
                        "modifiedTime": f.get("modifiedTime", "")}
        return None

    # ── helpers the KB code must never reach ──
    def text_of(self, file_id: str) -> str:
        data = self.files[file_id]["content"]
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)

    def file_named(self, name: str) -> dict | None:
        return next((f for f in self.files.values() if f["name"] == name), None)

    def put_human_file(self, folder_id: str, name: str, text: str) -> str:
        """A file a HUMAN created — no kb_card_id appProperty."""
        fid = self._id("hfile")
        self.files[fid] = {"id": fid, "name": name, "folder": folder_id,
                           "content": text.encode("utf-8"), "props": {},
                           "modifiedTime": self._stamp()}
        return fid

    def touch(self, file_id: str, text: str | None = None) -> str:
        f = self.files[file_id]
        if text is not None:
            f["content"] = text.encode("utf-8")
        f["modifiedTime"] = self._stamp()
        return f["modifiedTime"]


class FakeReader:
    """Read side of the same fake Drive."""

    def __init__(self, exporter: FakeExporter):
        self.ex = exporter
        self.subfolders: dict[str, list[str]] = {}

    def list_changed_files(self, folder_id, *, modified_after=None,
                           recursive=True, mime_types=None):
        out = []
        for f in self.ex.files.values():
            if f.get("folder") != folder_id or f["name"] == DK.MARKER_NAME:
                continue
            if modified_after and f.get("modifiedTime", "") <= modified_after:
                continue
            out.append({"id": f["id"], "name": f["name"],
                        "mime_type": "text/markdown",
                        "modified_time": f.get("modifiedTime", ""),
                        "url": f"https://drive.test/{f['id']}"})
        return out

    def list_folders(self, folder_id):
        return [{"id": fid} for fid in self.subfolders.get(folder_id, [])]

    def export_text(self, file_id, mime_type=""):
        return self.ex.text_of(file_id)

    def get_file(self, file_id):
        f = self.ex.files.get(file_id)
        if f is None:
            raise RuntimeError("HTTP 404: not found")
        return {"id": file_id, "name": f["name"],
                "modified_time": f.get("modifiedTime", "")}


class FailingLLM:
    """An LLM client that always dies — exercises the fallback path."""

    def generate(self, prompt):
        raise RuntimeError("model unavailable")


class ScriptedLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    def generate(self, prompt):
        return json.dumps(self.payload)


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def settings_stub(tmp_path, monkeypatch):
    """Isolate settings.yaml reads/writes from the dev machine's real file."""
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_settings_path", lambda: tmp_path / "settings.yaml")
    return tmp_path / "settings.yaml"


@pytest.fixture
def kb(empty_db, settings_stub):
    """A bootstrapped KB: EC root + one topic folder, on a fake Drive."""
    conn = empty_db.conn
    ex = FakeExporter()
    root = DK.ensure_ec_root(conn, exporter=ex)["folder_id"]
    topic = DK.resolve_topic_folder(conn, "eligibility recheck",
                                    exporter=ex)["folder_id"]
    reader = FakeReader(ex)
    return {"conn": conn, "ex": ex, "reader": reader, "root": root,
            "topic": topic}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _card_meta(card_id="kb-aaaa1111", **over):
    meta = {"card_id": card_id, "title": "Aetna recheck policy",
            "type": "source_summary", "topics": ["eligibility"],
            "source_id": "src-doc-1", "source_url": "https://drive.test/src-doc-1",
            "source_mime": "application/vnd.google-apps.document",
            "source_modified": "2026-05-01T00:00:00Z",
            "summary": "How Aetna eligibility rechecks are handled.",
            "key_facts": ["recheck within 30 days"]}
    meta.update(over)
    return meta


def _pending(conn, kind="write_card"):
    return conn.execute(
        "SELECT queue_id, target, status FROM kb_queue WHERE kind=? AND "
        "status='pending'", (kind,)).fetchall()


# ═════════════════════════════════════════════════════════════════════
# ARTICLE: kb-what-it-is.md
# ═════════════════════════════════════════════════════════════════════

def test_knowledge_base_root_folder_is_named_EC(empty_db, settings_stub):
    """kb-what-it-is: "The app creates a folder named EC in your Drive and
    treats it as the home of the knowledge base"."""
    ex = FakeExporter()
    res = DK.ensure_ec_root(empty_db.conn, exporter=ex)
    assert res["ok"] is True
    created = [c for c in ex.calls if c[0] == "create_folder"]
    assert created and created[0][1] == "EC"
    assert ex.folders[res["folder_id"]]["name"] == "EC"


def test_published_guru_cards_are_archived_into_a_published_cards_folder(kb):
    """kb-what-it-is: "`published-cards` — An archive of every card Renn
    publishes to Guru", and "when you publish a card to Guru, a copy of the
    published content is archived into the knowledge base automatically"."""
    conn, ex = kb["conn"], kb["ex"]
    conn.execute(
        "INSERT INTO guru_content_drafts (friction_type, draft_type, title, "
        "content, status) VALUES ('gap', 'card', 'Recheck runbook', "
        "'The full published body of the card.', 'pushed')")
    conn.commit()
    draft_id = conn.execute("SELECT MAX(id) FROM guru_content_drafts").fetchone()[0]

    res = ING.distill_draft(conn, draft_id, exporter=ex)

    assert res["ok"] is True
    card = STORE.get_card(conn, res["card_id"])
    assert card["type"] == "published_card"
    assert card["body_md"] == "The full published body of the card."
    folder_row = conn.execute(
        "SELECT topic FROM kb_folders WHERE folder_id=?",
        (card["topic_folder_id"],)).fetchone()
    assert folder_row[0] == "published-cards"


def test_misc_folder_absorbs_overflow_when_the_topic_budget_is_exhausted(kb):
    """kb-what-it-is: "`misc` — Overflow when the topic limit is reached"."""
    conn, ex = kb["conn"], kb["ex"]
    DK.resolve_topic_folder(conn, DK.MISC_TOPIC, exporter=ex)

    res = DK.resolve_topic_folder(conn, "a-brand-new-unseeded-topic",
                                  exporter=ex, new_folder_budget=0)

    assert res["ok"] is True
    assert res["topic"] == DK.MISC_TOPIC
    assert res.get("overflow") is True


def test_index_md_head_file_summarises_each_folders_contents(kb):
    """kb-what-it-is: "`_index.md` — An auto-written summary of each folder's
    contents"."""
    conn, ex = kb["conn"], kb["ex"]
    STORE.upsert_card(conn, _card_meta(), "body",
                      topic_folder_id=kb["topic"])

    assert SYNC.regenerate_index_md(conn, kb["topic"], exporter=ex) is True

    index_file = ex.file_named(SYNC.INDEX_FILENAME)
    assert index_file is not None
    assert index_file["folder"] == kb["topic"]
    text = ex.text_of(index_file["id"])
    assert "Aetna recheck policy" in text
    assert "1 cards" in text


def test_a_card_header_carries_the_documented_fields(kb):
    """kb-what-it-is: "a small block of structured fields at the top — title,
    topics, a one-or-two sentence summary, key facts, and a pointer back to
    the document it was built from"."""
    text = CF.serialize_card(_card_meta(), "body")
    meta, body, issues = CF.parse_card(text)

    assert issues == []
    assert meta["title"] == "Aetna recheck policy"
    assert meta["topics"] == ["eligibility"]
    assert meta["summary"].startswith("How Aetna")
    assert meta["key_facts"] == ["recheck within 30 days"]
    assert meta["source_id"] == "src-doc-1"
    assert meta["source_url"] == "https://drive.test/src-doc-1"
    assert body == "body"


def test_search_runs_off_the_local_copy_with_no_drive_round_trip(kb):
    """kb-what-it-is: "The app keeps a local copy of every card so search is
    fast and works without a round trip to Drive"."""
    conn, ex = kb["conn"], kb["ex"]
    STORE.upsert_card(conn, _card_meta(), "body about rechecks",
                      topic_folder_id=kb["topic"])
    calls_before = len(ex.calls)

    results = SEARCH.kb_search(conn, "Aetna", limit=5)

    assert [r["title"] for r in results][:1] == ["Aetna recheck policy"]
    assert len(ex.calls) == calls_before, "search must not touch Drive"


def test_indexing_a_drive_folder_writes_one_card_per_document(kb):
    """kb-what-it-is: "You can ask Renn to index a Drive folder, which reads
    the documents in it and writes one card per document"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    product = ex.create_folder("Product docs")["id"]
    ex.put_human_file(product, "Recheck runbook.gdoc", "Recheck within 30 days.")
    ex.put_human_file(product, "Denials primer.gdoc", "Denial codes explained.")

    res = ING.index_folder(conn, product, reader=reader, exporter=ex,
                           llm_client=FailingLLM())

    assert res["ok"] is True
    assert res["indexed"] == 2
    titles = {c["title"] for c in STORE.list_cards(conn)}
    assert titles == {"Recheck runbook.gdoc", "Denials primer.gdoc"}


def test_writes_inside_ec_are_ungated_and_writes_outside_ec_are_refused(kb):
    """kb-what-it-is: "Writing inside the EC folder does not open a Confirm
    card ... Writes anywhere else in Drive still wait for your click"."""
    conn, ex = kb["conn"], kb["ex"]

    res = DK.write_card_file(conn, kb["topic"], "card--kb-aaaa1111.md",
                             "hello", card_id="kb-aaaa1111", exporter=ex)
    assert res["ok"] is True and res["drive_file_id"] in ex.files

    with pytest.raises(KBWriteDenied):
        DK.write_card_file(conn, "some-other-drive-folder", "x.md", "hello",
                           card_id="kb-bbbb2222", exporter=ex)


def test_topic_listing_reports_topic_names_and_not_drive_folder_ids(kb):
    """kb-what-it-is: "Renn will not read a Drive folder name back to you ...
    It refers to topics by their topic name instead." Tested as: the
    kb_list_topics payload exposes the topic name and no Drive folder id."""
    from src.data.chat_tools.kb_tools import handle_kb_list_topics
    conn = kb["conn"]

    out = handle_kb_list_topics(conn, {}, {})

    assert out["ok"] is True
    assert out["topics"], "the fixture created a topic folder"
    for topic in out["topics"]:
        assert topic["topic"] == "eligibility-recheck"
        assert "folder_id" not in topic
        assert kb["topic"] not in json.dumps(topic)


def test_knowledge_base_ships_turned_off(monkeypatch):
    """kb-what-it-is / kb-bootstrapping: "The knowledge base ships turned off"
    and "A fresh install has no knowledge base configuration at all"."""
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {})

    assert WORKER.kb_enabled() is False


def test_shipped_settings_file_carries_no_kb_configuration():
    """kb-bootstrapping: "A fresh install has no knowledge base configuration
    at all, so the knowledge base is off and no EC folder exists"."""
    import yaml
    path = REPO / "data" / "settings.yaml"
    if not path.exists():
        pytest.skip("no local settings.yaml to inspect")
    cfg = yaml.safe_load(path.read_text("utf-8")) or {}

    assert "kb" not in (cfg.get("enablement") or {})


def test_writes_are_scoped_but_the_oauth_read_scope_is_broad():
    """kb-what-it-is (corrected): writes are limited to app-created/picked
    files (drive.file), but "Your own Google sign-in ... grants read access
    across your Drive". This pins the ACTUAL scopes so the article stays
    honest: drive.file for writes AND drive.readonly for reads are both
    requested on the per-user OAuth path. If drive.readonly is ever dropped,
    this fails and the article can reclaim the narrower promise."""
    from src.data import google_oauth

    granted = set(google_oauth.scopes())

    assert "https://www.googleapis.com/auth/drive.file" in granted, (
        "the write scope is gone — writes are no longer app-scoped")
    assert "https://www.googleapis.com/auth/drive.readonly" in granted, (
        "drive.readonly was dropped — the user-OAuth read scope is now narrow, "
        "so kb-what-it-is may reclaim 'only files you picked'")


# ═════════════════════════════════════════════════════════════════════
# ARTICLE: kb-index-cards.md
# ═════════════════════════════════════════════════════════════════════

def test_card_filename_is_the_title_slug_plus_the_card_identifier():
    """kb-index-cards: "Each card file is named after its title with a short
    identifier attached, like
    `eligibility-recheck-policy--kb-3f9a2c17.md`"."""
    name = CF.card_filename("Eligibility Recheck Policy", "kb-3f9a2c17")

    assert name == "eligibility-recheck-policy--kb-3f9a2c17.md"
    assert re.fullmatch(r"kb-[0-9a-f]{8}", CF.new_card_id())


def test_renaming_the_title_half_of_the_filename_is_safe():
    """kb-index-cards: "The identifier half is what the app matches on, so
    renaming the title half is safe"."""
    original = CF.card_filename("Eligibility Recheck Policy", "kb-3f9a2c17")
    renamed = "MY OWN NAME for this thing--kb-3f9a2c17.md"

    assert CF.card_id_from_filename(original) == "kb-3f9a2c17"
    assert CF.card_id_from_filename(renamed) == "kb-3f9a2c17"
    assert CF.card_id_from_filename("no-identifier-here.md") is None


def test_source_change_appends_a_dated_changelog_line_instead_of_overwriting(kb):
    """kb-index-cards: "When a source document changes, the app appends a
    dated line under a `## Changelog` heading in the body rather than silently
    overwriting the card"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    source_id = ex.put_human_file(kb["topic"], "Source.gdoc", "v1 text")
    STORE.upsert_card(conn, _card_meta(source_id=source_id,
                                       source_modified="2026-01-01T00:00:00Z"),
                      "Original human-authored body.",
                      topic_folder_id=kb["topic"])
    ex.touch(source_id, "v2 text with a materially different statement")

    res = SYNC.scan_stale_sources(conn, reader=reader, exporter=ex,
                                  llm_client=FailingLLM())

    assert res["refreshed"] == 1
    body = STORE.get_card(conn, "kb-aaaa1111")["body_md"]
    assert body.startswith("Original human-authored body."), \
        "the prior body must be preserved, not overwritten"
    assert "## Changelog" in body
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert f"- {today}: source updated" in body


def test_unknown_header_fields_survive_a_serialize_parse_round_trip():
    """kb-index-cards: "Fields the app does not recognise are kept"."""
    meta = _card_meta()
    meta["operator_note"] = "hand-added by a human"

    text = CF.serialize_card(meta, "body")
    parsed, _body, issues = CF.parse_card(text)

    assert issues == []
    assert parsed["operator_note"] == "hand-added by a human"


def test_unknown_header_field_survives_when_the_app_rewrites_the_card(kb):
    """kb-index-cards: "Fields the app does not recognise are kept ... a field
    you add by hand survives the app's rewrites, not just its reads." The pull
    path captures a human's non-schema frontmatter key via
    card_format.extract_extra into kb_cards.extra_json (migration 049); get_card
    returns it under card["extra"]; and sync._push_one merges it back before
    serialize — so the hand-added header field survives the app's own rewrite of
    the card, alongside the body."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    meta = _card_meta()
    meta["operator_note"] = "hand-added by a human"
    ex.put_human_file(kb["topic"], "aetna-recheck-policy--kb-aaaa1111.md",
                      CF.serialize_card(meta, "body a human wrote"))

    SYNC.pull_folder(conn, kb["topic"], reader=reader)
    # The pull captures the human's own field into the mirror.
    assert STORE.get_card(conn, "kb-aaaa1111")["extra"] == \
        {"operator_note": "hand-added by a human"}

    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)

    written_id = STORE.get_card(conn, "kb-aaaa1111")["drive_file_id"]
    rewritten, body, _i = CF.parse_card(ex.text_of(written_id))
    assert rewritten.get("operator_note") == "hand-added by a human", \
        "the hand-added header field survives the app's rewrite"
    assert body == "body a human wrote", "the body is preserved word for word"


def test_pushing_a_hand_dropped_card_leaves_the_humans_file_untouched(kb):
    """kb-index-cards: "The app never rewrites a file it did not create, so a
    card you dropped in by hand stays yours." Documents the mechanism: write
    idempotency is keyed on appProperties.kb_card_id, which a hand-dropped
    file lacks, so the push creates a SEPARATE app-owned file beside it."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    original = CF.serialize_card(_card_meta(), "body")
    human_file = ex.put_human_file(
        kb["topic"], "aetna-recheck-policy--kb-aaaa1111.md", original)

    SYNC.pull_folder(conn, kb["topic"], reader=reader)
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)

    assert ex.text_of(human_file) == original, "the human's bytes are untouched"
    assert human_file not in [c[1] for c in ex.calls if c[0] == "update_file"]
    written_id = STORE.get_card(conn, "kb-aaaa1111")["drive_file_id"]
    assert written_id != human_file, \
        "the app writes its own file rather than editing the human's"


def test_a_drive_edit_is_absorbed_and_becomes_what_search_returns(kb):
    """kb-index-cards: "On the next sync your version is read back into the
    app and becomes what search returns"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    STORE.upsert_card(conn, _card_meta(), "app body",
                      topic_folder_id=kb["topic"])
    edited = _card_meta(summary="HUMAN EDIT: rechecks are filed within "
                                "seven calendar days.")
    ex.put_human_file(kb["topic"], "aetna-recheck-policy--kb-aaaa1111.md",
                      CF.serialize_card(edited, "human body"))

    absorbed = SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert absorbed == 1
    assert STORE.get_card(conn, "kb-aaaa1111")["summary"].startswith("HUMAN EDIT")
    hits = SEARCH.kb_search(conn, "rechecks", limit=5)
    assert any(h["summary"].startswith("HUMAN EDIT") for h in hits)


def test_a_broken_header_is_flagged_for_repair_and_never_rewritten(kb):
    """kb-index-cards: "the app does not fail and does not repair it. The card
    is marked as needing repair and is left exactly as you wrote it. The app
    never rewrites a file it did not create"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    broken = ('---\n'
              'card_id: kb-cccc3333\n'
              'title: "unclosed quote\n'
              'topics: [a, b\n'
              '---\n\n'
              'the body the human wrote\n')
    file_id = ex.put_human_file(kb["topic"], "hand-dropped--kb-cccc3333.md",
                                broken)

    absorbed = SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert absorbed == 1
    card = STORE.get_card(conn, "kb-cccc3333")
    assert card["status"] == "needs_repair"
    assert ex.text_of(file_id) == broken, "the human's file must be untouched"
    assert _pending(conn) == [], "no rewrite may be queued for a broken card"


def test_index_md_is_never_ingested_as_a_card(kb):
    """kb-index-cards: "`_index.md` is not a card"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    ex.put_human_file(kb["topic"], SYNC.INDEX_FILENAME,
                      "# handwritten notes a human put here\n")

    absorbed = SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert absorbed == 0
    assert STORE.list_cards(conn) == []


def test_index_md_is_regenerated_from_scratch_and_says_so_in_its_own_text(kb):
    """kb-index-cards: "It is regenerated from scratch after each sync and says
    so in its own text. Anything you write there is overwritten"."""
    conn, ex = kb["conn"], kb["ex"]
    STORE.upsert_card(conn, _card_meta(), "body", topic_folder_id=kb["topic"])
    SYNC.regenerate_index_md(conn, kb["topic"], exporter=ex)
    index_id = ex.file_named(SYNC.INDEX_FILENAME)["id"]
    ex.touch(index_id, "# my own notes, which I expect to be overwritten\n")

    STORE.upsert_card(conn, _card_meta(card_id="kb-bbbb2222",
                                       title="Second card"), "body",
                      topic_folder_id=kb["topic"])
    SYNC.regenerate_index_md(conn, kb["topic"], exporter=ex)

    regenerated = ex.text_of(index_id)
    assert "my own notes" not in regenerated, "the human's text is discarded"
    assert "2 cards" in regenerated
    assert "Second card" in regenerated
    assert "overwritten" in regenerated
    assert "edit the cards instead" in regenerated.lower()


def test_a_card_with_no_header_id_falls_back_to_the_filename_identifier(kb):
    """kb-index-cards: "`card_id` is how the app matches your file to its
    record. If you remove it, the app falls back to the identifier in the
    filename"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    meta = _card_meta()
    meta.pop("card_id")
    ex.put_human_file(kb["topic"], "renamed-by-hand--kb-dddd4444.md",
                      CF.serialize_card(meta, "body"))

    SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert STORE.get_card(conn, "kb-dddd4444") is not None


def test_a_card_with_no_identifier_anywhere_is_treated_as_new(kb):
    """kb-index-cards: "if that is gone too, the file is treated as a brand
    new card"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    meta = _card_meta()
    meta.pop("card_id")
    ex.put_human_file(kb["topic"], "just-some-notes.md",
                      CF.serialize_card(meta, "body"))

    SYNC.pull_folder(conn, kb["topic"], reader=reader)

    cards = STORE.list_cards(conn)
    assert len(cards) == 1
    assert re.fullmatch(r"kb-[0-9a-f]{8}", cards[0]["card_id"])
    assert cards[0]["card_id"] != "kb-aaaa1111"


def test_a_known_file_keeps_its_card_id_when_both_identifiers_are_removed(kb):
    """kb-index-cards, troubleshooting (corrected): "Editing a card in place
    does not cause this. Even if you strip the identifier from both the header
    and the filename, the app still recognises the file it already knows and
    keeps it as the same card." For a file the mirror already knows,
    sync.pull_folder falls back to the MIRROR's card_id (sync.py:87-89), so no
    duplicate is created."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    file_id = ex.put_human_file(kb["topic"],
                                "aetna-recheck-policy--kb-aaaa1111.md",
                                CF.serialize_card(_card_meta(), "body"))
    SYNC.pull_folder(conn, kb["topic"], reader=reader)
    stripped = _card_meta()
    stripped.pop("card_id")
    ex.touch(file_id, CF.serialize_card(stripped, "edited body"))

    SYNC.pull_folder(conn, kb["topic"], reader=reader)

    cards = STORE.list_cards(conn)
    assert len(cards) == 1, "same Drive file id → mirror card_id reused"
    assert cards[0]["card_id"] == "kb-aaaa1111"


def test_a_copy_of_a_known_card_with_stripped_identifiers_becomes_a_duplicate(kb):
    """kb-index-cards, troubleshooting (corrected): "Duplication comes from a
    copy: when you duplicate a card file in Drive, the copy gets a fresh
    identity the app has never seen, and if that copy carries no `--kb-`
    identifier ... it is read as a brand new card while the original stays." A
    copy is a NEW drive_file_id the mirror can't fall back on, so with no
    identifier its card_id is freshly minted — the real cause of a card showing
    up twice, unlike an in-place edit."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    ex.put_human_file(kb["topic"], "aetna-recheck-policy--kb-aaaa1111.md",
                      CF.serialize_card(_card_meta(), "body"))
    SYNC.pull_folder(conn, kb["topic"], reader=reader)
    # A COPY: a NEW Drive file (new id) whose identifiers were both stripped.
    stripped = _card_meta()
    stripped.pop("card_id")
    ex.put_human_file(kb["topic"], "copy-of-recheck.md",
                      CF.serialize_card(stripped, "copied body"))

    absorbed = SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert absorbed == 1, "only the unseen copy is absorbed on the second pass"
    cards = STORE.list_cards(conn)
    assert len(cards) == 2, "a copy with no identifier is read as a new card"
    ids = {c["card_id"] for c in cards}
    assert "kb-aaaa1111" in ids, "the original card is untouched"
    assert any(cid != "kb-aaaa1111" and re.fullmatch(r"kb-[0-9a-f]{8}", cid)
               for cid in ids), "the copy was minted a fresh card_id"


def test_a_very_large_card_is_truncated_rather_than_loaded_whole():
    """kb-index-cards: "Very large files are truncated rather than loaded
    whole"."""
    body = "x" * (CF._MAX_CARD_CHARS + 5000)
    text = CF.serialize_card(_card_meta(), body)

    meta, parsed_body, issues = CF.parse_card(text)

    assert "too_large" in issues
    assert len(parsed_body) < len(body)
    assert meta["card_id"] == "kb-aaaa1111", "truncation must not break parsing"


def test_files_that_do_not_end_in_md_are_skipped_entirely(kb):
    """kb-index-cards: "Files that do not end in `.md` are skipped
    entirely"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    ex.put_human_file(kb["topic"], "notes--kb-eeee5555.txt",
                      CF.serialize_card(_card_meta(card_id="kb-eeee5555"), "b"))

    absorbed = SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert absorbed == 0
    assert STORE.get_card(conn, "kb-eeee5555") is None


# ═════════════════════════════════════════════════════════════════════
# ARTICLE: kb-sync-rules.md
# ═════════════════════════════════════════════════════════════════════

def test_a_drive_change_discards_the_queued_push_and_pulls_instead(kb):
    """kb-sync-rules: "the Drive version wins ... Before sending a queued
    write, the app checks whether the file changed in Drive since it last
    looked. If it did, the write is dropped and your Drive version is read in
    instead. Nothing is merged"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    # The app's own card, already on Drive.
    STORE.upsert_card(conn, _card_meta(), "app body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)
    file_id = STORE.get_card(conn, "kb-aaaa1111")["drive_file_id"]

    # A human edits it in Drive; meanwhile the app queues its own update.
    human = _card_meta(summary="HUMAN WINS: filed within seven days.")
    ex.touch(file_id, CF.serialize_card(human, "human body"))
    STORE.upsert_card(conn, _card_meta(summary="APP VERSION: thirty days."),
                      "app body 2", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")

    SYNC.push_pending(conn, exporter=ex, reader=reader)

    assert STORE.get_card(conn, "kb-aaaa1111")["summary"].startswith("HUMAN WINS")
    on_drive, _b, _i = CF.parse_card(ex.text_of(file_id))
    assert on_drive["summary"].startswith("HUMAN WINS"), \
        "the app must not have overwritten the human's Drive file"
    logged = [r[0] for r in conn.execute(
        "SELECT action FROM kb_sync_log").fetchall()]
    assert "conflict" in logged


def test_a_discarded_push_is_not_retried_with_the_same_content(kb):
    """kb-sync-rules: "the app does not try again with the same content"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    STORE.upsert_card(conn, _card_meta(), "app body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)
    file_id = STORE.get_card(conn, "kb-aaaa1111")["drive_file_id"]
    ex.touch(file_id, CF.serialize_card(
        _card_meta(summary="HUMAN WINS"), "human body"))
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)
    updates_after_conflict = len([c for c in ex.calls if c[0] == "update_file"])

    SYNC.push_pending(conn, exporter=ex, reader=reader)

    assert _pending(conn) == [], "the conflicted row is completed, not requeued"
    assert len([c for c in ex.calls if c[0] == "update_file"]) == \
        updates_after_conflict


def test_each_sync_pass_pushes_queued_writes_before_it_reads_drive_back(kb,
                                                                        monkeypatch):
    """kb-sync-rules: "Each pass does the same sequence: send any card writes
    the app has queued up, then read back everything that changed in Drive
    since last time"."""
    from src.data import google_oauth
    order: list[str] = []
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)
    monkeypatch.setattr(SYNC, "reset_stale_claims", lambda conn: 0)
    monkeypatch.setattr(SYNC, "expire_stale_pending", lambda conn, **k: 0)
    monkeypatch.setattr(SYNC, "drain_index_jobs", lambda conn, **k: 0)
    monkeypatch.setattr(SYNC, "drain_distill_jobs", lambda conn, **k: 0)
    monkeypatch.setattr(SYNC, "push_pending",
                        lambda conn, **k: order.append("push") or 0)
    monkeypatch.setattr(SYNC, "pull_all",
                        lambda conn, **k: order.append("pull") or 0)

    WORKER.tick_once(kb["conn"], reader=kb["reader"], exporter=kb["ex"])

    assert order == ["push", "pull"]


def test_the_apps_own_write_is_not_re_imported_on_the_next_read(kb):
    """kb-sync-rules: "When it saves a card it remembers the resulting
    timestamp, so the next read pass skips that file rather than re-importing
    it. That is what stops a card from bouncing back and forth"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    STORE.upsert_card(conn, _card_meta(), "app body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)
    card = STORE.get_card(conn, "kb-aaaa1111")
    assert card["drive_modified"], "the push must record the response timestamp"

    # A fresh pull with no cursor still sees the file — and must skip it.
    conn.execute("DELETE FROM kb_sync_state")
    conn.commit()
    absorbed = SYNC.pull_folder(conn, kb["topic"], reader=reader)

    assert absorbed == 0
    pulls = conn.execute(
        "SELECT COUNT(*) FROM kb_sync_log WHERE action='pull'").fetchone()[0]
    assert pulls == 0


def test_a_card_deleted_in_drive_is_dropped_from_the_mirror_on_the_full_pass(kb):
    """kb-sync-rules: "Deleting a card in Drive removes it from the app on the
    next full pass"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    STORE.upsert_card(conn, _card_meta(), "app body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)
    file_id = STORE.get_card(conn, "kb-aaaa1111")["drive_file_id"]
    del ex.files[file_id]                       # the human deleted it in Drive

    res = SYNC.full_reconcile(conn, reader=reader, exporter=ex)

    assert res["dropped"] == 1
    assert STORE.get_card(conn, "kb-aaaa1111") is None


def test_the_app_never_deletes_anything_in_drive(kb):
    """kb-sync-rules: "the app never deletes anything in your Drive"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    STORE.upsert_card(conn, _card_meta(), "app body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")
    SYNC.push_pending(conn, exporter=ex, reader=reader)
    keeper = ex.put_human_file(kb["topic"], "keep-me--kb-ffff6666.md",
                               CF.serialize_card(
                                   _card_meta(card_id="kb-ffff6666"), "b"))
    SYNC.pull_folder(conn, kb["topic"], reader=reader)
    files_before = set(ex.files)

    STORE.delete_card(conn, "kb-ffff6666")
    SYNC.full_reconcile(conn, reader=reader, exporter=ex)

    assert keeper in ex.files
    assert files_before <= set(ex.files), "no Drive file may disappear"
    assert not [c for c in ex.calls if "delete" in c[0] or "trash" in c[0]]

    source = "\n".join((KB_DIR / p).read_text("utf-8")
                       for p in os.listdir(KB_DIR) if p.endswith(".py"))
    assert not re.search(r"\.(delete_file|trash_file|delete)\s*\(", source), \
        "no KB module may call a Drive delete/trash API"


def test_a_card_whose_source_vanished_is_marked_missing_and_kept(kb):
    """kb-sync-rules: "If a source document disappears, the card built from it
    is marked as having a missing source and kept"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    source_id = ex.put_human_file(kb["topic"], "Source.gdoc", "v1")
    STORE.upsert_card(conn, _card_meta(source_id=source_id), "body",
                      topic_folder_id=kb["topic"])
    del ex.files[source_id]

    SYNC.scan_stale_sources(conn, reader=reader, exporter=ex,
                            llm_client=FailingLLM())

    card = STORE.get_card(conn, "kb-aaaa1111")
    assert card is not None, "the card must be kept"
    assert card["status"] == "source_missing"


def test_a_pass_with_no_google_connection_does_nothing_and_records_it(kb,
                                                                      monkeypatch):
    """kb-sync-rules: "Sync only runs while Google is connected for the
    session. A pass with no connection does nothing and records that it was
    skipped"."""
    from src.data import google_oauth
    conn = kb["conn"]
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)
    STORE.upsert_card(conn, _card_meta(), "body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")
    calls_before = len(kb["ex"].calls)

    res = WORKER.tick_once(conn, reader=kb["reader"], exporter=kb["ex"])

    assert res == {"skipped": True, "reason": "google_not_connected"}
    assert len(kb["ex"].calls) == calls_before, "no Drive traffic while offline"
    assert _pending(conn), "the queued write is retained for the next pass"
    status = conn.execute(
        "SELECT last_status FROM kb_sync_state WHERE folder_id=?",
        (kb["root"],)).fetchone()[0]
    assert status.startswith("skipped")


def test_a_write_that_keeps_failing_is_retried_a_few_times_then_set_aside(kb):
    """kb-sync-rules: "A write that keeps failing is retried a few times and
    then set aside"."""
    conn = kb["conn"]
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO kb_queue (kind, target, status, attempts, created_at, "
        "claimed_at) VALUES ('write_card', 'kb-aaaa1111', 'claimed', 0, ?, ?)",
        (stale, stale))
    conn.commit()

    def _status():
        return conn.execute(
            "SELECT status, attempts FROM kb_queue WHERE target='kb-aaaa1111'"
        ).fetchone()

    seen = []
    for _ in range(SYNC._MAX_ATTEMPTS):
        SYNC.reset_stale_claims(conn)
        seen.append(_status()[0])
        conn.execute("UPDATE kb_queue SET status='claimed', claimed_at=? "
                     "WHERE target='kb-aaaa1111' AND status='pending'", (stale,))
        conn.commit()

    assert SYNC._MAX_ATTEMPTS == 3
    assert seen == ["pending", "pending", "dead"]


def test_queued_work_waiting_about_a_week_is_dropped(kb):
    """kb-sync-rules: "anything still waiting after about a week is dropped
    rather than released as a flood when you reconnect"."""
    import inspect
    conn = kb["conn"]
    assert inspect.signature(
        SYNC.expire_stale_pending).parameters["days"].default == 7
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO kb_queue (kind, target, status, created_at) "
                 "VALUES ('write_card', 'kb-old', 'pending', ?)", (old,))
    conn.execute("INSERT INTO kb_queue (kind, target, status, created_at) "
                 "VALUES ('write_card', 'kb-new', 'pending', ?)", (fresh,))
    conn.commit()

    dropped = SYNC.expire_stale_pending(conn)

    assert dropped == 1
    rows = dict(conn.execute(
        "SELECT target, status FROM kb_queue").fetchall())
    assert rows["kb-old"] == "dead"
    assert rows["kb-new"] == "pending"


def test_deletion_detection_runs_on_the_periodic_full_pass_not_every_pass(
        kb, monkeypatch):
    """kb-sync-rules: "Periodically it also does a fuller pass that checks for
    cards you deleted" / "Deletion removal happens on the periodic full pass,
    not every pass"."""
    from src.data import google_oauth
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)
    conn = kb["conn"]

    ordinary = WORKER.tick_once(conn, reader=kb["reader"], exporter=kb["ex"])
    full = WORKER.tick_once(conn, reader=kb["reader"], exporter=kb["ex"],
                            do_reconcile=True)

    assert "reconcile" not in ordinary
    assert "reconcile" in full
    assert WORKER._RECONCILE_EVERY > 1


# ═════════════════════════════════════════════════════════════════════
# ARTICLE: kb-searching.md
# ═════════════════════════════════════════════════════════════════════

def test_ranking_weights_title_above_topics_and_key_facts_above_summary_and_body(
        empty_db):
    """kb-searching: "with the title weighted most heavily, then the topics
    and key facts, then the summary and body"."""
    conn = empty_db.conn
    columns = re.search(
        r"CREATE VIRTUAL TABLE kb_cards_fts USING fts5\(\s*([^)]*?),\s*content=",
        conn.execute("SELECT sql FROM sqlite_master WHERE name='kb_cards_fts'"
                     ).fetchone()[0], re.DOTALL).group(1)
    order = [c.strip() for c in columns.split(",") if c.strip()]
    weights = [float(w) for w in re.search(
        r"bm25\(kb_cards_fts,\s*([^)]*)\)",
        (KB_DIR / "store.py").read_text("utf-8")).group(1).split(",")]
    by_column = dict(zip(order, weights))

    assert order == ["title", "summary", "key_facts", "body", "topics"]
    assert by_column["title"] > by_column["topics"]
    assert by_column["topics"] == by_column["key_facts"]
    assert by_column["key_facts"] > by_column["summary"]
    assert by_column["summary"] == by_column["body"]


def test_a_title_match_outranks_a_body_only_match(empty_db):
    """kb-searching: the behavioural consequence of "the title weighted most
    heavily" plus "The score also rewards ... a match in the title"."""
    conn = empty_db.conn
    STORE.upsert_card(conn, _card_meta(card_id="kb-title001",
                                       title="Zephyrine appeals policy",
                                       summary="a policy", key_facts=[]),
                      "unrelated body")
    STORE.upsert_card(conn, _card_meta(card_id="kb-body0001",
                                       title="Unrelated handbook",
                                       summary="nothing relevant",
                                       key_facts=[]),
                      "zephyrine appears only down here in the body")

    results = SEARCH.kb_search(conn, "zephyrine", limit=5)

    assert [r["card_id"] for r in results][:2] == ["kb-title001", "kb-body0001"]
    assert results[0]["score"] > results[1]["score"]


def test_query_expansion_folds_plurals_to_singulars():
    """kb-searching: "your query is expanded in code — plurals are folded to
    singulars"."""
    assert "policy" in SEARCH.expand_query("policies")
    assert "denial" in SEARCH.expand_query("denials")
    assert "process" in SEARCH.expand_query("process"), \
        "a trailing 'ss' must not be stripped"


def test_query_expansion_adds_known_payer_aliases():
    """kb-searching: "payer and product-area names are expanded to their known
    aliases. So a query using one name for a payer can still find a card that
    used a different one"."""
    terms = SEARCH.expand_query("cvs health denial")

    assert "aetna" in terms, "CVS Health is an Aetna alias in payers.json"
    assert "denial" in terms


def test_an_alias_query_finds_a_card_written_with_the_canonical_name(empty_db):
    """kb-searching: the behavioural consequence of alias expansion."""
    conn = empty_db.conn
    STORE.upsert_card(conn, _card_meta(card_id="kb-alias001",
                                       title="Aetna appeals",
                                       summary="Aetna appeal windows.",
                                       key_facts=[]), "body")

    results = SEARCH.kb_search(conn, "cvs health", limit=5)

    assert any(r["card_id"] == "kb-alias001" for r in results)


def test_the_full_text_floor_returns_a_snippet_when_card_hits_are_thin(empty_db):
    """kb-searching: "If the card layer returns fewer results than asked for,
    the search falls through to the stored full text of every indexed document
    and returns a snippet from around the hit"."""
    from src.data import enablement_store
    conn = empty_db.conn
    buried = ("preamble " * 200) + "the reconciliation window is 47 days" + \
             (" trailing" * 200)
    enablement_store.save_document(conn, source="drive", name="Big deck.pptx",
                                   source_ref="doc-1", full_text=buried,
                                   web_url="https://drive.test/doc-1")

    results = SEARCH.kb_search(conn, "reconciliation window", limit=5)

    floor = [r for r in results if r["match"] == "fulltext"]
    assert floor, "the full-text floor must engage when no cards match"
    assert floor[0]["title"] == "Big deck.pptx"
    assert "reconciliation window" in floor[0]["summary"]
    assert len(floor[0]["summary"]) < len(buried), "a windowed snippet, not all"


def test_cards_with_a_missing_source_are_excluded_from_ranked_results(empty_db):
    """kb-searching: "Cards whose source document has gone missing are
    excluded from the ranked results"."""
    conn = empty_db.conn
    STORE.upsert_card(conn, _card_meta(card_id="kb-live0001",
                                       title="Zephyrine live"), "body")
    STORE.upsert_card(conn, _card_meta(card_id="kb-gone0001",
                                       title="Zephyrine gone"), "body")
    STORE.mark_status(conn, "kb-gone0001", "source_missing")

    ids = {r["card_id"] for r in SEARCH.kb_search(conn, "zephyrine", limit=10)}

    assert "kb-live0001" in ids
    assert "kb-gone0001" not in ids


def test_there_are_no_embeddings_or_vector_search_in_the_kb_path():
    """kb-searching: "There are no embeddings and no vector search ... There is
    no semantic model anywhere in that path, by decision"."""
    banned = re.compile(
        r"sentence_transformers|SentenceTransformer|embed_texts|"
        r"cosine_similarity|faiss|\bvector_search\b|embedding_model",
        re.IGNORECASE)
    offenders = []
    for name in sorted(os.listdir(KB_DIR)):
        if not name.endswith(".py"):
            continue
        text = (KB_DIR / name).read_text("utf-8")
        # Strip the module docstring's own prose about NOT using embeddings.
        body = text.split('"""', 2)[-1] if text.lstrip().startswith('"""') else text
        if banned.search(body):
            offenders.append(name)
    offenders += [n for n in ("kb_tools.py",)
                  if banned.search((REPO / "src" / "data" / "chat_tools" / n
                                    ).read_text("utf-8"))]

    assert offenders == []


def test_the_full_text_fallback_only_matches_the_whole_query_contiguously(
        empty_db):
    """kb-searching, known limitation: "The fallback looks for your whole query
    as one continuous string, so 'eligibility recheck timeline' only matches
    text where those three words appear together in exactly that order"."""
    from src.data import enablement_store
    conn = empty_db.conn
    enablement_store.save_document(
        conn, source="drive", name="Ops deck.pptx", source_ref="doc-2",
        full_text="Eligibility rules are here. Separately, the recheck "
                  "timeline is 30 days.")

    scattered = SEARCH.kb_search(conn, "eligibility recheck timeline", limit=5)
    contiguous = SEARCH.kb_search(conn, "recheck timeline", limit=5)

    assert scattered == [], "words present but not contiguous → no hit"
    assert [r["title"] for r in contiguous] == ["Ops deck.pptx"]


def test_topic_and_card_listings_enumerate_completely_rather_than_ranking(kb):
    """kb-searching: "Renn can list every topic with its card count, and list
    every card inside one topic. Those enumerate completely rather than
    ranking"."""
    from src.data.chat_tools.kb_tools import (handle_kb_list_cards,
                                              handle_kb_list_topics)
    conn = kb["conn"]
    for i in range(7):
        STORE.upsert_card(conn, _card_meta(card_id=f"kb-enum000{i}",
                                           title=f"Card {i}"), "body",
                          topic_folder_id=kb["topic"])

    topics = handle_kb_list_topics(conn, {}, {})
    cards = handle_kb_list_cards(conn, {"topic": "eligibility-recheck"}, {})

    assert topics["topics"][0]["card_count"] == 7
    assert cards["count"] == 7
    assert len(cards["cards"]) == 7
    assert all("score" not in c for c in cards["cards"])


def test_an_unknown_topic_steers_with_the_available_topics(kb):
    """kb-searching / kb-index-cards: the listing tools "never a dead end" —
    an unknown topic returns the topics that do exist."""
    from src.data.chat_tools.kb_tools import handle_kb_list_cards

    out = handle_kb_list_cards(kb["conn"], {"topic": "no-such-topic"}, {})

    assert out["ok"] is False
    assert out["error"] == "unknown_topic"
    assert "eligibility-recheck" in out["available_topics"]


# ═════════════════════════════════════════════════════════════════════
# ARTICLE: kb-bootstrapping.md
# ═════════════════════════════════════════════════════════════════════

def test_setup_writes_a_marker_file_and_updates_it_to_prove_writability(
        empty_db, settings_stub):
    """kb-bootstrapping: "It creates the EC folder if there is none, then
    uploads a small marker file into it and updates that file, to prove this
    build can actually write there ... enabling the knowledge base is a change
    to your Drive, not an inspection of it"."""
    ex = FakeExporter()

    res = DK.ensure_ec_root(empty_db.conn, exporter=ex)

    assert res["ok"] is True
    kinds = [c[0] for c in ex.calls]
    assert kinds == ["create_folder", "upload_file", "update_file"]
    marker = ex.file_named(DK.MARKER_NAME)
    assert marker is not None and marker["folder"] == res["folder_id"]


def test_setup_fails_loudly_when_the_folder_exists_but_cannot_be_written(
        empty_db, settings_stub):
    """kb-bootstrapping: "Read access is not the same as write access" — the
    probe must surface a failure rather than report success."""
    ex = FakeExporter()
    ex.fail_updates = True

    res = DK.ensure_ec_root(empty_db.conn, exporter=ex)

    assert res["ok"] is False
    assert res["error"] == "ec_folder_not_writable"
    assert res["needs_rebootstrap"] is True
    assert DK.ec_root_id(empty_db.conn) is None


def test_an_index_request_is_queued_rather_than_run_immediately(kb, monkeypatch):
    """kb-bootstrapping: "That request is queued rather than run immediately;
    the background sync picks it up on its next pass, and progress appears in
    the jobs list"."""
    from src.data.chat_tools.kb_tools import handle_index_drive_folder
    import src.data.settings_manager as sm
    conn = kb["conn"]
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": False, "kb": {"enabled": True, "ec_folder_id": kb["root"]}})
    calls_before = len(kb["ex"].calls)

    out = handle_index_drive_folder(conn, {"folder_id": "prod-folder-1"}, {})

    assert out["ok"] is True and out["queued"] is True
    assert len(kb["ex"].calls) == calls_before, "enqueue must not touch Drive"
    queued = _pending(conn, "index_folder")
    assert [q[1] for q in queued] == ["prod-folder-1"]
    job = conn.execute("SELECT status FROM agent_jobs WHERE job_id=?",
                       (out["job_id"],)).fetchone()
    assert job is not None, "a job row must exist for the sidebar"


def test_documents_per_indexing_job_is_capped_at_fifty_and_reports_truncation(kb):
    """kb-bootstrapping: "Documents per indexing job | 50" and "Hitting the
    document cap is reported as truncated"."""
    ex, reader = kb["ex"], kb["reader"]
    assert ING.MAX_DOCS_PER_JOB == 50
    product = ex.create_folder("Huge folder")["id"]
    for i in range(60):
        ex.put_human_file(product, f"doc-{i}.gdoc", f"content {i}")

    files, truncated = ING.enumerate_folder(reader, product)

    assert len(files) == ING.MAX_DOCS_PER_JOB == 50
    assert truncated is True


def test_subfolder_depth_searched_is_three_levels_below_the_picked_folder(kb):
    """kb-bootstrapping: "Subfolder depth searched | 3 levels below the folder
    you pick" and "Folders nested more than three levels deep are not visited
    at all"."""
    ex, reader = kb["ex"], kb["reader"]
    assert ING.MAX_DEPTH == 3
    levels = []
    parent = ex.create_folder("L0")["id"]
    levels.append(parent)
    for depth in range(1, 5):
        child = ex.create_folder(f"L{depth}")["id"]
        reader.subfolders.setdefault(levels[-1], []).append(child)
        levels.append(child)
    for depth, fid in enumerate(levels):
        ex.put_human_file(fid, f"doc-at-depth-{depth}.gdoc", "text")

    files, truncated = ING.enumerate_folder(reader, levels[0])

    names = {f["name"] for f in files}
    assert truncated is False
    assert names == {f"doc-at-depth-{d}.gdoc" for d in range(4)}, \
        "depths 0..3 inclusive — 3 levels below the picked folder"
    assert "doc-at-depth-4.gdoc" not in names


def test_indexing_falls_back_to_a_plain_excerpt_when_the_model_fails(kb):
    """kb-bootstrapping: "Indexing should never fail because the summarising
    model had a bad day. If the model cannot produce a usable summary, the app
    falls back to a plain excerpt from the document and still creates the
    card"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    product = ex.create_folder("Product docs")["id"]
    ex.put_human_file(product, "Runbook.gdoc",
                      "Rechecks are filed within thirty days of denial.")

    res = ING.index_folder(conn, product, reader=reader, exporter=ex,
                           llm_client=FailingLLM())

    assert res["ok"] is True and res["indexed"] == 1
    card = STORE.list_cards(conn)[0]
    assert card["summary"].startswith("Rechecks are filed within thirty days")
    assert ING.summarize_doc("d", "some text", llm_client=FailingLLM())["via"] \
        == "deterministic"


def test_the_full_text_of_every_indexed_document_is_stored(kb):
    """kb-bootstrapping: "The full text of every document it indexes is
    stored, not just the summary, so a fact the summary skipped is still
    findable"."""
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    product = ex.create_folder("Product docs")["id"]
    body = "Intro. " * 50 + "The obscure fact is on slide thirty-seven."
    ex.put_human_file(product, "Deck.pptx", body)

    ING.index_folder(conn, product, reader=reader, exporter=ex,
                     llm_client=ScriptedLLM({"summary": "A deck.",
                                             "key_facts": [], "topics": [],
                                             "type": "source_summary"}))

    stored = conn.execute(
        "SELECT full_text FROM enablement_documents WHERE name='Deck.pptx'"
    ).fetchone()
    assert stored is not None
    assert "slide thirty-seven" in stored[0]
    assert "slide thirty-seven" not in STORE.list_cards(conn)[0]["summary"]


def test_renn_asks_for_a_picker_rather_than_a_raw_drive_folder_id(kb, monkeypatch):
    """kb-bootstrapping: "Renn should never ask you for a raw Drive folder
    identifier. It should offer you a picker instead"."""
    from src.data.chat_tools.kb_tools import handle_index_drive_folder
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": False, "kb": {"enabled": True, "ec_folder_id": kb["root"]}})

    out = handle_index_drive_folder(kb["conn"], {}, {})

    assert out["ok"] is False
    assert out["error"] == "folder_id_required"
    assert "request_drive_picker" in out["message"]


def test_refusals_name_demo_mode_then_disabled_then_not_bootstrapped(kb,
                                                                     monkeypatch):
    """kb-bootstrapping: "Renn refuses to index and says the knowledge base is
    disabled, in demo mode, or not set up. All three are real states with a
    real fix, and the message names which one"."""
    from src.data.chat_tools.kb_tools import handle_index_drive_folder
    import src.data.settings_manager as sm
    conn = kb["conn"]

    def _cfg(cfg):
        monkeypatch.setattr(sm, "get_section", lambda name, default=None: cfg)

    _cfg({"demo_mode": True, "kb": {"enabled": True, "ec_folder_id": "x"}})
    demo = handle_index_drive_folder(conn, {"folder_id": "f"}, {})
    _cfg({"demo_mode": False, "kb": {"enabled": False}})
    disabled = handle_index_drive_folder(conn, {"folder_id": "f"}, {})
    _cfg({"demo_mode": False, "kb": {"enabled": True}})
    unbootstrapped = handle_index_drive_folder(conn, {"folder_id": "f"}, {})

    assert demo["error"] == "demo_mode" and "demo mode" in demo["message"]
    assert disabled["error"] == "kb_disabled"
    assert "disabled" in disabled["message"]
    assert unbootstrapped["error"] == "ec_not_bootstrapped"
    assert "bootstrap" in unbootstrapped["message"].lower()
    assert _pending(conn, "index_folder") == [], "a refusal queues nothing"


def test_the_kb_worker_is_disabled_in_demo_mode(monkeypatch):
    """kb-bootstrapping: the status line names "demo mode on" as a reason a
    background capability is idle — demo mode must win over an enabled KB."""
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": True, "kb": {"enabled": True, "ec_folder_id": "fold1"}})

    assert WORKER.kb_enabled() is False

    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": False, "kb": {"enabled": True, "ec_folder_id": "fold1"}})
    assert WORKER.kb_enabled() is True


def test_background_sync_is_wired_at_app_start_only_if_the_kb_is_already_on(
        qapp, empty_db, monkeypatch):
    """kb-bootstrapping: "Expect to restart the app after enabling the
    knowledge base. The background sync is set up when the app starts, and
    only if the knowledge base is already on at that moment"."""
    import src.data.enablement_monitor as EM
    from src.data import asana_setup
    from src.data.drive_reader import DriveReader
    import src.data.kb.worker as W
    started: list[str] = []

    class RecordingWorker:
        def __init__(self, db):
            started.append("constructed")

        def start(self, interval):
            started.append("started")

        def stop(self):
            pass

    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: False)
    monkeypatch.setattr(DriveReader, "from_settings",
                        classmethod(lambda cls: type("R", (), {
                            "is_configured": lambda self: False})()))
    monkeypatch.setattr(W, "KBWorker", RecordingWorker)

    monkeypatch.setattr(W, "kb_enabled", lambda: False)
    monitor_off = EM.EnablementMonitor(empty_db)
    monitor_off.start(interval_seconds=600)
    assert started == [], "KB off at start → no background sync is wired"
    monitor_off.stop()

    monkeypatch.setattr(W, "kb_enabled", lambda: True)
    monitor_on = EM.EnablementMonitor(empty_db)
    monitor_on.start(interval_seconds=600)
    assert started == ["constructed", "started"]
    monitor_on.stop()


def test_the_kb_settings_card_offers_a_status_line_a_toggle_and_a_setup_button(
        qapp, monkeypatch):
    """kb-bootstrapping: "It shows a status line, a control to turn the
    knowledge base on or off, and a separate control to create and verify the
    EC folder"."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": False, "kb": {}})
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)
    from src.ui.pages.enablement.settings import SettingsPage

    page = SettingsPage()
    try:
        assert page._kb_status.text()
        assert page._kb_toggle_btn.text() == "Enable KB"
        assert page._kb_bootstrap_btn.text() == "Bootstrap EC folder"
        tab_names = [page._tabs.tabText(i) for i in range(page._tabs.count())]
        assert "Sources" in tab_names
        # disconnect() raises if the handler was never connected.
        page._kb_toggle_btn.clicked.disconnect(page._on_kb_toggle)
        page._kb_bootstrap_btn.clicked.disconnect(page._on_kb_bootstrap)
    finally:
        page.deleteLater()


def test_the_toggle_turns_the_knowledge_base_on(qapp, monkeypatch):
    """kb-bootstrapping: "a control to turn the knowledge base on or off" —
    clicking it must actually flip the persisted setting."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    from src.ui.pages.enablement.settings import SettingsPage
    state = {"demo_mode": False, "kb": {"enabled": False}}
    written: list[tuple] = []
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: state)
    monkeypatch.setattr(sm, "set_section",
                        lambda name, value: written.append((name, value)))
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)

    page = SettingsPage()
    try:
        page._kb_toggle_btn.click()

        assert written, "the toggle must persist the new state"
        section, value = written[-1]
        assert section == "enablement"
        assert value["kb"]["enabled"] is True
    finally:
        page.deleteLater()


def test_the_setup_control_stays_disabled_until_the_kb_is_turned_on(qapp,
                                                                    monkeypatch):
    """kb-bootstrapping: "Turning it on comes first. The folder setup control
    stays unavailable until the knowledge base is enabled" / "The folder setup
    control does nothing or is unavailable. Check the knowledge base is turned
    on first"."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    from src.ui.pages.enablement.settings import SettingsPage
    state = {"demo_mode": False, "kb": {"enabled": False}}
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: state)
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)

    page = SettingsPage()
    try:
        page.kb_conn_factory = lambda: None      # host wiring present
        page.refresh_kb_status()
        assert page._kb_bootstrap_btn.isEnabled() is False

        state["kb"] = {"enabled": True}
        page.refresh_kb_status()
        assert page._kb_bootstrap_btn.isEnabled() is True
        assert page._kb_toggle_btn.text() == "Disable KB"
    finally:
        page.deleteLater()


def test_the_status_line_names_the_specific_reason_each_capability_is_idle(
        qapp, monkeypatch):
    """kb-bootstrapping: "The status line is the single place that explains why
    any background capability is idle. It names the specific reason — demo
    mode on, no Asana token, Google not connected this session, knowledge base
    disabled, or enabled but the folder was never created"."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    from src.ui.pages.enablement.settings import SettingsPage
    state = {"demo_mode": True, "kb": {"enabled": False}}
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: state)
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: False)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    page = SettingsPage()
    try:
        page.refresh_kb_status()
        text = page._kb_status.text()
        assert "Demo mode is ON" in text
        assert "Asana: no token" in text
        assert "Google: not connected this session" in text
        assert "Knowledge base: disabled" in text

        state.update({"demo_mode": False, "kb": {"enabled": True}})
        page.refresh_kb_status()
        assert "isn't bootstrapped yet" in page._kb_status.text()
    finally:
        page.deleteLater()


def test_the_status_line_reports_active_with_card_and_topic_counts(
        qapp, empty_db, settings_stub, monkeypatch):
    """kb-bootstrapping: "After enabling and running setup, the status line
    should say the knowledge base is active and show a card and topic
    count"."""
    import src.data.settings_manager as sm
    from src.data import asana_setup, google_oauth
    from src.data.connection_factory import get_connection
    from src.ui.pages.enablement.settings import SettingsPage
    conn, ex = empty_db.conn, FakeExporter()
    root = DK.ensure_ec_root(conn, exporter=ex)["folder_id"]
    topic = DK.resolve_topic_folder(conn, "denials", exporter=ex)["folder_id"]
    STORE.upsert_card(conn, _card_meta(), "body", topic_folder_id=topic)
    STORE.upsert_card(conn, _card_meta(card_id="kb-bbbb2222", title="Two"),
                      "body", topic_folder_id=topic)

    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": False, "kb": {"enabled": True, "ec_folder_id": root}})
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)

    page = SettingsPage()
    try:
        page.kb_conn_factory = lambda: get_connection(str(empty_db.db_path))
        page.refresh_kb_status()

        text = page._kb_status.text()
        assert "Knowledge base: active" in text
        assert "2 cards across 1 topics" in text
    finally:
        page.deleteLater()


def test_bootstrap_reports_that_google_must_be_connected_for_the_session(
        qapp, monkeypatch):
    """kb-bootstrapping: "Creating the folder requires Google connected for
    this session" / "Setup reports that Google is not connected"."""
    import src.data.settings_manager as sm
    from PySide6.QtCore import QCoreApplication
    from src.data import asana_setup, google_oauth
    from src.ui.pages.enablement.settings import SettingsPage
    monkeypatch.setattr(sm, "get_section", lambda name, default=None: {
        "demo_mode": False, "kb": {"enabled": True}})
    monkeypatch.setattr(asana_setup, "is_asana_connected", lambda: True)
    monkeypatch.setattr(google_oauth, "is_active", lambda: False)

    page = SettingsPage()
    try:
        seen: list[str] = []
        page._kb_bootstrap_finished.connect(seen.append)
        page.kb_conn_factory = lambda: pytest.fail(
            "no DB connection may be opened while Google is disconnected")

        page._on_kb_bootstrap()
        deadline = time.time() + 10
        while not seen and time.time() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)

        assert seen, "the bootstrap worker must report back"
        assert "Google isn't connected this session" in seen[0]
    finally:
        page.deleteLater()


# ═════════════════════════════════════════════════════════════════════
# ARTICLE: kb-quarantine.md
# ═════════════════════════════════════════════════════════════════════

def test_a_trashed_ec_folder_is_quarantined(kb):
    """kb-quarantine: "It is in the trash ... Refusing is the only way to avoid
    quietly filing your knowledge base in the bin"."""
    conn, ex = kb["conn"], kb["ex"]
    ex.folders[kb["root"]]["trashed"] = True

    res = DK.ensure_ec_root(conn, exporter=ex)

    assert res["ok"] is False
    assert res["error"] == "ec_folder_quarantined"
    assert res["needs_rebootstrap"] is True
    assert "trashed" in res["message"]
    status = conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                          (kb["root"],)).fetchone()[0]
    assert status == "quarantined"


def test_an_unwritable_ec_folder_is_quarantined(kb):
    """kb-quarantine: "It is no longer writable by this sign-in"."""
    conn, ex = kb["conn"], kb["ex"]
    ex.folders[kb["root"]]["canAddChildren"] = False

    res = DK.ensure_ec_root(conn, exporter=ex)

    assert res["ok"] is False
    assert res["error"] == "ec_folder_quarantined"
    assert conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                        (kb["root"],)).fetchone()[0] == "quarantined"


def test_an_unreadable_ec_folder_is_quarantined(kb):
    """kb-quarantine: "It cannot be read at all — deleted, or created by a
    different build of the app"."""
    conn, ex = kb["conn"], kb["ex"]
    del ex.folders[kb["root"]]

    res = DK.ensure_ec_root(conn, exporter=ex)

    assert res["ok"] is False
    assert res["error"] == "ec_folder_unreachable"
    assert res["needs_rebootstrap"] is True
    assert "different app build" in res["message"]
    assert conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                        (kb["root"],)).fetchone()[0] == "quarantined"


def test_a_quarantined_folder_leaves_the_allowlist_and_refuses_every_write(kb):
    """kb-quarantine: "Once quarantined, the folder is no longer on the allowed
    list, and every attempt to write a card into it is refused outright"."""
    conn, ex = kb["conn"], kb["ex"]
    assert DK.allowed_folder(conn, kb["topic"]) is True
    DK._quarantine(conn, kb["topic"], "trashed")

    assert DK.allowed_folder(conn, kb["topic"]) is False
    with pytest.raises(KBWriteDenied):
        DK.write_card_file(conn, kb["topic"], "c--kb-aaaa1111.md", "x",
                           card_id="kb-aaaa1111", exporter=ex)


def test_the_quarantine_check_runs_on_re_bootstrap_not_on_the_routine_sync(
        kb, monkeypatch):
    """kb-quarantine: "The check runs when you re-run the folder setup, not
    continuously. The routine background sync does not re-test whether the EC
    folder is still in the trash or still writable ... writes can keep going
    into the trashed folder"."""
    from src.data import google_oauth
    conn, ex, reader = kb["conn"], kb["ex"], kb["reader"]
    monkeypatch.setattr(google_oauth, "is_active", lambda: True)
    ex.folders[kb["topic"]]["trashed"] = True
    STORE.upsert_card(conn, _card_meta(), "body", topic_folder_id=kb["topic"])
    ING._enqueue_write(conn, "kb-aaaa1111")

    WORKER.tick_once(conn, reader=reader, exporter=ex, do_reconcile=True)

    written = STORE.get_card(conn, "kb-aaaa1111")["drive_file_id"]
    assert written, "the routine sync wrote into the trashed folder"
    assert ex.files[written]["folder"] == kb["topic"]
    assert conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                        (kb["topic"],)).fetchone()[0] == "ok", \
        "no quarantine is recorded by the routine sync"

    DK.ensure_ec_root(conn, exporter=ex)          # the operator re-runs setup
    # ensure_ec_root verifies the ROOT; verifying this topic folder directly is
    # what setup's verification does for a folder it owns.
    verdict = DK._verify_folder(conn, kb["topic"], ex)
    assert verdict["ok"] is False
    assert conn.execute("SELECT status FROM kb_folders WHERE folder_id=?",
                        (kb["topic"],)).fetchone()[0] == "quarantined"


def test_topic_listing_carries_a_quarantined_marker(kb):
    """kb-quarantine: "Renn can list the knowledge base topics with a
    quarantined marker on each, so you can ask it which folders are
    affected"."""
    from src.data.chat_tools.kb_tools import handle_kb_list_topics
    conn, ex = kb["conn"], kb["ex"]
    healthy = DK.resolve_topic_folder(conn, "denials", exporter=ex)["folder_id"]
    DK._quarantine(conn, kb["topic"], "trashed")

    topics = {t["topic"]: t for t in
              handle_kb_list_topics(conn, {}, {})["topics"]}

    assert topics["eligibility-recheck"]["quarantined"] is True
    assert topics["denials"]["quarantined"] is False
    assert healthy


def test_re_running_setup_after_quarantine_creates_a_fresh_ec_folder(kb):
    """kb-quarantine: "Recovery is to run the folder setup again, which creates
    a fresh EC folder"."""
    conn, ex = kb["conn"], kb["ex"]
    ex.folders[kb["root"]]["trashed"] = True
    DK.ensure_ec_root(conn, exporter=ex)          # records the quarantine

    res = DK.ensure_ec_root(conn, exporter=ex)    # the operator re-runs setup

    assert res["ok"] is True
    assert res["folder_id"] != kb["root"]
    assert ex.folders[res["folder_id"]]["name"] == "EC"
    assert DK.ec_root_id(conn) == res["folder_id"]


def test_the_app_deletes_nothing_in_the_old_quarantined_folder(kb):
    """kb-quarantine: "Nothing in the old folder is deleted by the app; if it
    is in the trash, it stays in the trash for you to restore or empty"."""
    conn, ex = kb["conn"], kb["ex"]
    kept = ex.put_human_file(kb["root"], "old-card--kb-aaaa1111.md", "content")
    ex.folders[kb["root"]]["trashed"] = True
    DK.ensure_ec_root(conn, exporter=ex)

    DK.ensure_ec_root(conn, exporter=ex)

    assert kept in ex.files
    assert ex.text_of(kept) == "content"
    assert kb["root"] in ex.folders
    assert ex.folders[kb["root"]]["trashed"] is True


def test_restoring_a_trashed_folder_is_re_adopted_on_re_bootstrap(kb):
    """kb-quarantine: "Restoring the folder in Drive before re-bootstrap now
    works; it is re-adopted rather than replaced." drive_kb._readopt_quarantined_ec_root
    re-verifies a quarantined EC root before ensure_ec_root mints a new one: if
    the operator un-trashed it (or its permissions came back) it resets
    status='ok' and reuses the SAME folder, so no duplicate EC folder is
    created and the old cards stay where they were."""
    conn, ex = kb["conn"], kb["ex"]
    ex.folders[kb["root"]]["trashed"] = True
    DK.ensure_ec_root(conn, exporter=ex)          # detects + quarantines
    assert conn.execute(
        "SELECT status FROM kb_folders WHERE folder_id=?",
        (kb["root"],)).fetchone()[0] == "quarantined"
    creates_before = len([c for c in ex.calls if c[0] == "create_folder"])

    ex.folders[kb["root"]]["trashed"] = False     # the operator restores it
    res = DK.ensure_ec_root(conn, exporter=ex)

    assert res["ok"] is True
    assert res["folder_id"] == kb["root"], \
        "the restored folder must be re-adopted, not replaced"
    assert len([c for c in ex.calls if c[0] == "create_folder"]) == \
        creates_before, "no new EC folder may be created when the old is restored"
    # The old folder is reset to healthy and reused.
    assert conn.execute(
        "SELECT status FROM kb_folders WHERE folder_id=?",
        (kb["root"],)).fetchone()[0] == "ok"


def test_setup_names_the_problem_it_found_and_that_a_rebootstrap_is_needed(kb):
    """kb-quarantine: "the setup step should tell you plainly which problem it
    found and that a re-bootstrap is needed, rather than leaving you with
    repeated permission failures and no explanation"."""
    conn, ex = kb["conn"], kb["ex"]
    ex.folders[kb["root"]]["canAddChildren"] = False

    res = DK.ensure_ec_root(conn, exporter=ex)

    assert res["needs_rebootstrap"] is True
    assert "no longer writable" in res["message"]
    assert "re-bootstrap" in res["message"].lower()
    logged = conn.execute(
        "SELECT detail FROM kb_sync_log WHERE action='repair_flag'").fetchone()
    assert logged is not None and "quarantined" in logged[0]
