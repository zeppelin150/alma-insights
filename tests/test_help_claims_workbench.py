"""Accuracy audit of the in-app Help Center — section: workbench.

Every test here settles one falsifiable claim made by an article under
``assets/help/workbench/``. Each test's docstring names the article and quotes
(or closely paraphrases) the claim it settles.

Where the article claims X and the code does NOT-X, the test asserts the
ARTICLE's claim and is marked ``xfail(strict=True)`` with the actual behaviour
in the reason — so the suite stays green while the discrepancy stays tracked.

Articles covered:
  overview.md · bringing-documents-in.md · editing.md · review-changes.md
  publish-guru.md · publish-drive.md · style-guides.md

No network, no credentials, no QtWebEngine. Qt widgets run offscreen.
"""

from __future__ import annotations

import os
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QUrl  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QDropEvent, QKeySequence, QShortcut, QTextCursor,
)
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit  # noqa: E402

from src.data import enablement_store as store  # noqa: E402
from src.ui.pages.enablement.page import EnablementPage  # noqa: E402

pytestmark = pytest.mark.ui


# ── fixtures / helpers ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def wb(qapp):
    """A WorkbenchPage with the built-in sample workspaces cleared."""
    from src.ui.pages.enablement.workbench import WorkbenchPage
    w = WorkbenchPage()
    w.set_pending_drafts([])
    return w


@pytest.fixture()
def fake_settings(monkeypatch):
    """Dict-backed settings_manager so guide tests never touch settings.yaml."""
    state: dict = {}

    def get_section(name, default=None):
        return state.get(name, default if default is not None else {})

    def set_section(name, value):
        state[name] = dict(value)
        return True

    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", get_section)
    monkeypatch.setattr(sm, "set_section", set_section)
    return state


class Shim:
    """Minimal stand-in for EnablementPage when calling its methods unbound.

    The methods under test only touch plain Python attributes, so binding them
    to this object exercises the real logic without building the whole page
    (which would read live settings and hit the warehouse).
    """

    def __init__(self, **kw):
        self.demo = False
        self.status: list[str] = []
        self.chat: list[tuple[str, str]] = []
        self.calls: list[tuple] = []
        self._drafts: dict = {}
        self._guru_client = None
        self._existing_cards_loaded = False
        for k, v in kw.items():
            setattr(self, k, v)

    # page surface the handlers use
    def _set_status(self, text):
        self.status.append(text)

    def _chat_say(self, who, text):
        self.chat.append((who, text))

    def _load_live(self, prefer_draft_id=None):
        self.calls.append(("_load_live", prefer_draft_id))

    def select_tab(self, key):
        self.calls.append(("select_tab", key))

    def _refresh_style_guide_status(self):
        self.calls.append(("refresh_style", None))

    def _refresh_card_template_status(self):
        self.calls.append(("refresh_template", None))

    def _read_local_text(self, path):
        return EnablementPage._read_local_text(path)

    def _scoped_instruction(self, instruction, selection):
        return EnablementPage._scoped_instruction(instruction, selection)

    def _card_from_draft(self, conn, draft):
        return EnablementPage._card_from_draft(self, conn, draft)

    def _bundled_card_template_text(self):
        return EnablementPage._bundled_card_template_text()


class StubLLM:
    """Records the prompt it was handed and returns a parseable card."""

    def __init__(self, body="Stub body."):
        self.prompts: list[str] = []
        self._body = body

    def generate(self, prompt):
        self.prompts.append(prompt)
        return f"TITLE: Stub Card\n---\n{self._body}"


class FakeGuru:
    """In-memory Guru client: records create/update instead of calling out."""

    def __init__(self, card=None):
        self.created: list[tuple] = []
        self.updated: list[tuple] = []
        self._card = card

    def create_card(self, collection_id, title, html, folder_ids=None):
        self.created.append((collection_id, title, html, folder_ids))
        return {"id": "NEW-CARD-ID"}

    def update_card(self, card_id, html, title):
        self.updated.append((card_id, html, title))
        return {"id": card_id}

    def get_card(self, card_id):
        return self._card


_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def _docx(tmp_path, body, *, rels="", name="d.docx") -> str:
    p = tmp_path / name
    doc = (f'<?xml version="1.0"?><w:document {_NS}><w:body>{body}'
           f'</w:body></w:document>')
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        if rels:
            z.writestr("word/_rels/document.xml.rels", rels)
    return str(p)


def _para(text, ppr="", rpr=""):
    return (f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{text}'
            f'</w:t></w:r></w:p>')


def _new_draft(conn, *, title="Draft", content="body", **kw) -> int:
    return store.save_card_draft(conn, title=title, content=content, **kw)


# ══════════════════════════════════════════════════════════════════════
# overview.md — "The Workbench: workspaces, drafts and views"
# ══════════════════════════════════════════════════════════════════════

class TestOverviewWorkspaces:
    def test_four_workspaces_can_be_open_at_once(self, wb):
        """overview.md: 'You can keep four drafts open at a time.'"""
        from src.ui.pages.enablement.workbench import WorkbenchPage
        assert WorkbenchPage.MAX_WORKSPACES == 4
        for i in range(1, 5):
            wb.open_workspace({"id": i, "title": f"D{i}", "source": "drive"})
        assert [d["id"] for d in wb._current_drafts] == [1, 2, 3, 4]
        assert len(wb._chip_widgets) == 4
        assert wb._count_badge.text() == "4"

    def test_fifth_draft_drops_the_oldest_workspace(self, wb):
        """overview.md: 'Opening a fifth draft does not fail — the oldest
        workspace is dropped to make room, along with any unsaved state.'"""
        for i in range(1, 5):
            wb.open_workspace({"id": i, "title": f"D{i}", "source": "drive"})
        wb.switch_workspace(1)
        wb._editor.setPlainText("unsaved text for draft 1")
        wb._set_view("edit")
        wb.open_workspace({"id": 5, "title": "D5", "source": "drive"})
        assert [d["id"] for d in wb._current_drafts] == [2, 3, 4, 5]
        assert 1 not in wb._ws_state          # its unsaved state went with it
        assert wb.active_draft_id == 5

    def test_ctrl_1_through_4_jump_to_the_nth_chip(self, wb):
        """overview.md: 'press Ctrl+1 through Ctrl+4 to jump to the first
        through fourth chip.'"""
        keys = {sc.key().toString() for sc in wb.findChildren(QShortcut)}
        for n in range(1, 5):
            assert QKeySequence(f"Ctrl+{n}").toString() in keys
        for i in (11, 22, 33, 44):
            wb.open_workspace({"id": i, "title": f"D{i}", "source": "drive"})
        picked: list[int] = []
        wb.draft_selected.connect(picked.append)
        for idx in range(4):
            wb._switch_to_index(idx)
        assert picked == [11, 22, 33, 44]

    def test_chip_close_button_emits_workspace_closed(self, wb):
        """overview.md: 'The small x on a chip closes that workspace.'"""
        from PySide6.QtWidgets import QPushButton
        wb.open_workspace({"id": 7, "title": "Seven", "source": "drive"})
        closed: list[int] = []
        wb.workspace_closed.connect(closed.append)
        chip = wb._chip_widgets[0]
        close_btn = [b for b in chip.findChildren(QPushButton)
                     if b.text() == "×"]
        assert len(close_btn) == 1
        close_btn[0].click()
        assert closed == [7]

    def test_host_close_handler_removes_the_chip(self, wb):
        """overview.md: closing a chip drops that workspace, keeping the rest."""
        for i in (1, 2, 3):
            wb.open_workspace({"id": i, "title": f"D{i}", "source": "drive"})
        page = Shim(workbench=wb, _conn=lambda: None)
        EnablementPage._on_workspace_closed(page, 2)
        assert [d["id"] for d in wb._current_drafts] == [1, 3]


class TestOverviewViews:
    def test_four_named_view_buttons_swap_the_body(self, wb):
        """overview.md: four buttons — Guru preview / Rich text / Edit
        markdown / Review changes — 'swap what the body shows'."""
        assert wb._preview_btn.text() == "Guru preview"
        assert wb._rich_btn.text() == "Rich text"
        assert wb._edit_btn.text() == "Edit markdown"
        assert wb._diff_btn.text() == "Review changes"
        wb._rich_btn.click()
        assert wb._body_stack.currentWidget() is wb._rich
        wb._edit_btn.click()
        assert wb._body_stack.currentWidget() is wb._editor
        wb._diff_btn.click()
        assert wb._body_stack.currentWidget() is wb._diff
        wb._preview_btn.click()
        assert wb._body_stack.currentWidget() is wb._body

    def test_expand_button_opens_its_own_editor_pair(self, wb):
        """overview.md: 'A fifth button expands the editor ... It carries its
        own rich-text and markdown pair.'"""
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        assert wb._expand_btn.text().endswith("Expand")
        wb.show_draft({"title": "T", "markdown": "# Hi"})
        wb._open_expand()
        assert isinstance(wb._overlay._rich, RichTextEditor)
        assert isinstance(wb._overlay._source, QPlainTextEdit)
        assert wb._overlay._rich is not wb._rich

    def test_expanded_editor_commit_flows_back_into_the_draft(self, wb):
        """overview.md: 'what you commit there flows back into the draft.'"""
        wb.show_draft({"title": "T", "markdown": "original"})
        wb.set_pending_drafts([{"id": 9, "title": "T", "source": "drive"}],
                              active_id=9)
        edits: list[tuple] = []
        wb.content_edited.connect(lambda i, m: edits.append((i, m)))
        wb._open_expand()
        wb._overlay._set_mode("markdown")
        wb._overlay._source.setPlainText("edited in the overlay")
        wb._overlay._collapse()
        assert edits == [(9, "edited in the overlay")]
        assert wb._current_md == "edited in the overlay"


class TestOverviewSaving:
    def test_leaving_an_editing_view_commits_without_a_save_button(self, wb):
        """overview.md: 'There is no save button. Leaving an editing view ...
        reads your changes back and writes them to the draft.'"""
        from PySide6.QtWidgets import QPushButton
        wb.set_pending_drafts([{"id": 3, "title": "D", "source": "drive"}],
                              active_id=3)
        wb.show_draft({"title": "D", "markdown": "before"})
        labels = {b.text() for b in wb.findChildren(QPushButton)}
        assert not any("save" in t.lower() for t in labels)
        edits: list[tuple] = []
        wb.content_edited.connect(lambda i, m: edits.append((i, m)))
        wb._set_view("edit")
        wb._editor.setPlainText("after")
        wb._set_view("preview")
        assert edits == [(3, "after")]

    def test_switching_chips_preserves_view_text_and_cursor(self, wb):
        """overview.md: 'Switching between chips preserves the view you were
        in, the unsaved text, and your cursor position.'"""
        wb.open_workspace({"id": 1, "title": "A", "source": "drive"})
        wb.show_draft({"title": "A", "markdown": "aaa"})
        wb.open_workspace({"id": 2, "title": "B", "source": "drive"})
        wb.switch_workspace(1, {"title": "A", "markdown": "aaa"})
        wb._set_view("edit")
        wb._editor.setPlainText("hello world")
        cur = wb._editor.textCursor()
        cur.setPosition(5)
        wb._editor.setTextCursor(cur)
        wb.switch_workspace(2, {"title": "B", "markdown": "bbb"})
        assert wb._body_stack.currentWidget() is wb._body
        wb.switch_workspace(1, {"title": "A", "markdown": "aaa"})
        assert wb._body_stack.currentWidget() is wb._editor
        assert wb._editor.toPlainText() == "hello world"
        assert wb._editor.textCursor().position() == 5

    def test_published_draft_is_frozen_against_edits(self, empty_db, wb):
        """overview.md: 'A draft that has already been published to Guru is
        frozen: edits to it are not written back.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="Pushed", content="original", status="pushed")
        page = Shim(workbench=wb, _conn=lambda: conn)
        EnablementPage._on_content_edited(page, did, "operator typed this")
        assert store.get_draft(conn, did)["content"] == "original"

    def test_pending_draft_edit_is_written_back(self, empty_db, wb):
        """overview.md counterpart: an unpublished draft DOES persist edits."""
        conn = empty_db.conn
        did = _new_draft(conn, title="Pending", content="original")
        page = Shim(workbench=wb, _conn=lambda: conn)
        EnablementPage._on_content_edited(page, did, "operator typed this")
        assert store.get_draft(conn, did)["content"] == "operator typed this"


class TestOverviewKnownGaps:
    def test_sample_sso_card_is_shown_at_startup(self, qapp):
        """overview.md: 'a card about SSO setup for providers that you never
        created ... is the built-in sample content ... displayed at startup.'"""
        from src.ui.pages.enablement.workbench import WorkbenchPage
        fresh = WorkbenchPage()
        assert fresh._title.text() == "Setting up SSO for Providers"
        assert len(fresh._chip_widgets) == 3

    def test_sample_card_survives_a_load_with_zero_drafts(self, wb):
        """overview.md: 'when you have no pending drafts at all, the sample
        card stays on screen with zero workspace chips.'

        _load_live only calls show_draft when there is an active draft, so an
        empty pending set leaves the sample card rendered.
        """
        from src.ui.pages.enablement.workbench import WorkbenchPage
        fresh = WorkbenchPage()
        fresh.set_pending_drafts([])           # what _load_live does with 0 drafts
        assert fresh._title.text() == "Setting up SSO for Providers"
        assert fresh._chip_widgets == []
        assert fresh._count_badge.text() == "0"

    def test_subtask_strip_is_fixed_display_text(self, wb):
        """overview.md: 'The subtask counter and scratch-pad note under the
        canvas never change ... currently fixed display text.'"""
        from PySide6.QtWidgets import QLabel
        texts = {lbl.text() for lbl in wb.findChildren(QLabel)}
        assert "Subtasks 2/5" in texts
        assert any(t.startswith("•  Scratch pad:") for t in texts)
        # no API exists to feed real subtask state into the canvas
        assert not [n for n in dir(wb)
                    if "subtask" in n.lower() or "scratch" in n.lower()]


# ══════════════════════════════════════════════════════════════════════
# bringing-documents-in.md
# ══════════════════════════════════════════════════════════════════════

class TestImportRoutes:
    def test_guru_import_opens_a_picker_not_a_url_prompt(self, monkeypatch, wb):
        """bringing-documents-in.md: 'An existing Guru card ... A picker opens
        against your connected Guru account.' publish-guru.md adds: 'There is
        no import-by-URL route for a Guru card.'"""
        import PySide6.QtWidgets as QtW
        import src.ui.pages.enablement.card_picker as picker_mod

        opened: list[str] = []

        class FakePicker:
            def __init__(self, client, parent=None):
                opened.append("picker")
                self.selected_card_id = "CARD-1"

            def exec(self):
                return 1

        class NoInput:
            @staticmethod
            def getText(*a, **kw):
                raise AssertionError("Guru import must not prompt for a URL")

        monkeypatch.setattr(picker_mod, "GuruCardPickerDialog", FakePicker)
        monkeypatch.setattr(QtW, "QInputDialog", NoInput)

        page = Shim(workbench=wb, _guru_client=object(),
                    _run_import=lambda k, r: page.calls.append(("import", k, r)))
        EnablementPage._on_import_requested(page, "guru")
        assert opened == ["picker"]
        assert ("import", "guru", "CARD-1") in page.calls

    def test_drive_import_prompts_for_a_url_or_file_id(self, monkeypatch, wb):
        """bringing-documents-in.md: 'A Google Doc or Drive URL ... You are
        asked for a Google Doc URL or a file id.'"""
        import PySide6.QtWidgets as QtW
        import src.ui.pages.enablement.card_picker as picker_mod

        prompts: list[str] = []

        class FakeInput:
            @staticmethod
            def getText(parent, title, label):
                prompts.append(label)
                return "https://docs.google.com/document/d/" + "F" * 24, True

        class NoPicker:
            def __init__(self, *a, **kw):
                raise AssertionError("Drive import must not open the card picker")

        monkeypatch.setattr(QtW, "QInputDialog", FakeInput)
        monkeypatch.setattr(picker_mod, "GuruCardPickerDialog", NoPicker)

        page = Shim(workbench=wb,
                    _run_import=lambda k, r: page.calls.append(("import", k, r)))
        EnablementPage._on_import_requested(page, "drive")
        assert prompts and "URL or file id" in prompts[0]
        assert page.calls[0][0:2] == ("import", "drive")

    def test_guru_import_without_a_connection_says_connect_first(self, wb):
        """bringing-documents-in.md: 'The Guru import says to connect Guru
        first. The card picker needs a live Guru connection.'"""
        page = Shim(workbench=wb, _guru_client=None)
        EnablementPage._on_import_requested(page, "guru")
        assert page.status == ["Connect Guru first (Settings → Guru)."]

    def test_upload_file_picker_offers_the_documented_extensions(
            self, monkeypatch, wb):
        """bringing-documents-in.md: 'The file picker offers .docx, .md,
        .markdown, .txt and .csv, with an all-files fallback.'"""
        import src.ui.pages.enablement.workbench as wb_mod
        seen: dict = {}

        class FakeDialog:
            @staticmethod
            def getOpenFileName(parent, caption, directory, filt):
                seen["filter"] = filt
                return "", ""

        monkeypatch.setattr(wb_mod, "QFileDialog", FakeDialog)
        wb._on_upload()
        for ext in ("*.docx", "*.md", "*.markdown", "*.txt", "*.csv"):
            assert ext in seen["filter"]
        assert "All files (*)" in seen["filter"]

    def test_dropping_a_file_anywhere_requests_a_load(self, wb, tmp_path):
        """bringing-documents-in.md: 'or drop a file anywhere on the
        Workbench.'"""
        target = tmp_path / "note.md"
        target.write_text("# hi", encoding="utf-8")
        requested: list[str] = []
        wb.load_file_requested.connect(requested.append)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(target))])
        from PySide6.QtCore import Qt
        evt = QDropEvent(QPoint(5, 5), Qt.CopyAction, mime,
                         Qt.LeftButton, Qt.NoModifier)
        wb.dropEvent(evt)
        assert [os.path.normcase(os.path.abspath(p)) for p in requested] == \
            [os.path.normcase(os.path.abspath(str(target)))]

    def test_drop_zone_in_the_tools_bar_also_requests_a_load(self, wb):
        """bringing-documents-in.md: 'the drag-and-drop strip along the bottom
        of the tools bar'."""
        from src.ui.pages.enablement.workbench import _DropZone
        zones = wb.findChildren(_DropZone)
        assert len(zones) == 1
        requested: list[str] = []
        wb.load_file_requested.connect(requested.append)
        zones[0].dropped.emit("C:/x/doc.docx")
        assert requested == ["C:/x/doc.docx"]

    def test_imports_run_off_the_main_thread(self, monkeypatch, wb, tmp_path):
        """bringing-documents-in.md: 'Imports run off the main thread.'"""
        import threading
        started: list[dict] = []

        class FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                started.append({"daemon": daemon, "target": target})

            def start(self):
                started[-1]["started"] = True

        p = tmp_path / "a.txt"
        p.write_text("body", encoding="utf-8")
        monkeypatch.setattr(threading, "Thread", FakeThread)
        page = Shim(workbench=wb, _engine_db_path=lambda: ":memory:")
        EnablementPage._on_load_file(page, str(p))
        assert started and started[0]["daemon"] is True
        assert started[0].get("started") is True

    def test_finished_import_switches_to_and_focuses_the_new_draft(self, wb):
        """bringing-documents-in.md: 'The Workbench switches to the new draft
        and focuses it when the import finishes.'"""
        page = Shim(workbench=wb)
        EnablementPage._on_import_finished(
            page, {"ok": True, "kind": "upload", "name": "a.docx",
                   "chars": 10, "draft_id": 42})
        assert ("_load_live", 42) in page.calls
        assert ("select_tab", "workbench") in page.calls

    def test_failed_model_call_still_saves_the_document(self, wb):
        """bringing-documents-in.md: 'If the model call fails, the document is
        still saved and you are told the auto-draft was skipped.'"""
        page = Shim(workbench=wb)
        EnablementPage._on_import_finished(
            page, {"ok": True, "kind": "drive", "name": "Q3.gdoc",
                   "chars": 900, "draft_error": "no_llm_client"})
        assert any("auto-draft skipped" in s for s in page.status)
        assert ("select_tab", "workbench") not in page.calls

    def test_import_failure_names_what_failed(self, wb):
        """bringing-documents-in.md: 'An import failure should tell you what
        failed — the fetch, the export, or the draft — not fail silently.'"""
        class BadFetch:
            def get_file(self, fid):
                raise RuntimeError("403")

        class BadExport:
            def get_file(self, fid):
                return {"id": fid, "name": "n", "mime_type": "m"}

            def export_text(self, fid, mime):
                raise RuntimeError("boom")

        class BadGuru:
            def get_card(self, cid):
                raise RuntimeError("401")

        import sqlite3
        conn = sqlite3.connect(":memory:")
        assert store.import_drive_doc(conn, BadFetch(), "x" * 25)["error"] \
            .startswith("drive_fetch_failed")
        assert store.import_drive_doc(conn, BadExport(), "x" * 25)["error"] \
            .startswith("drive_export_failed")
        assert store.import_guru_card_to_draft(conn, BadGuru(), "abc")["error"] \
            .startswith("guru_fetch_failed")
        page = Shim(workbench=None)
        EnablementPage._on_import_finished(page, {"ok": False, "error": "drive_fetch_failed: 403"})
        assert any("drive_fetch_failed: 403" in s for s in page.status)


class TestGuruCardImportStamping:
    def test_import_stamps_the_target_card_id(self, empty_db):
        """bringing-documents-in.md: 'the draft is stamped with that card's id,
        so publishing later updates the same card instead of creating a
        duplicate.'"""
        conn = empty_db.conn
        client = FakeGuru(card={"id": "CARD-9", "title": "Payments v2",
                                "content": "<h1>Payments</h1><p>Body</p>"})
        res = store.import_guru_card_to_draft(conn, client, "CARD-9")
        assert res["ok"] is True
        draft = store.get_draft(conn, res["draft_id"])
        assert draft["card_id"] == "CARD-9"
        assert draft["draft_type"] == "card_update"
        assert draft["source_ref"] == "guru:CARD-9"

    def test_imported_card_publishes_as_an_update_not_a_duplicate(self, empty_db):
        """bringing-documents-in.md / publish-guru.md: 'Publishing that draft
        updates the original' — no duplicate card is created."""
        conn = empty_db.conn
        client = FakeGuru(card={"id": "CARD-9", "title": "Payments v2",
                                "content": "<p>Body</p>"})
        res = store.import_guru_card_to_draft(conn, client, "CARD-9")
        out = store.publish_draft(conn, res["draft_id"], guru_client=client,
                                  collection_id="COL-1")
        assert out["ok"] is True
        assert client.created == []                      # nothing new created
        assert [u[0] for u in client.updated] == ["CARD-9"]

    def test_import_converts_html_to_markdown_and_keeps_source_html(self, empty_db):
        """bringing-documents-in.md: 'The card's HTML is converted to markdown
        so you can edit it ... The original HTML is kept alongside.'"""
        conn = empty_db.conn
        html = "<h2>Steps</h2><ul><li>One</li><li>Two</li></ul>"
        client = FakeGuru(card={"id": "C1", "title": "Steps", "content": html})
        res = store.import_guru_card_to_draft(conn, client, "C1")
        draft = store.get_draft(conn, res["draft_id"])
        assert "## Steps" in draft["content"]
        assert "One" in draft["content"] and "<h2>" not in draft["content"]
        assert draft["content_html"] == html

    def test_guru_card_url_and_bare_id_are_interchangeable(self):
        """bringing-documents-in.md: 'So should a Guru card URL and a raw card
        id.'"""
        assert store.parse_guru_card_ref(
            "https://app.getguru.com/card/iKxx9dpT") == "iKxx9dpT"
        assert store.parse_guru_card_ref("iKxx9dpT") == "iKxx9dpT"

    def test_drive_url_and_bare_file_id_are_interchangeable(self):
        """bringing-documents-in.md: 'A Drive URL and a raw file id should be
        interchangeable.'"""
        fid = "1A" + "b" * 26
        assert store.parse_drive_file_ref(
            f"https://docs.google.com/document/d/{fid}/edit") == fid
        assert store.parse_drive_file_ref(fid) == fid


class TestLocalFileConversion:
    def test_local_upload_uses_no_llm_client(self, empty_db, tmp_path, monkeypatch):
        """bringing-documents-in.md: 'This conversion is plain Python — no AI,
        no network, no provider call.'"""
        import src.gemini.client_factory as cf
        monkeypatch.setattr(cf, "build_client_for_task", lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("local upload must not build an LLM client")))
        p = tmp_path / "policy.txt"
        p.write_text("EFFECTIVE JUNE 1\n\nProviders must re-verify.", encoding="utf-8")
        res = EnablementPage._ingest_local_file(empty_db.conn, str(p))
        assert res["ok"] is True and res["draft_id"]

    def test_local_conversion_is_deterministic(self, empty_db, tmp_path):
        """bringing-documents-in.md: 'it is fast, offline, repeatable, and
        never invents content.'"""
        text = "ROLLOUT\n\nTier B moves to usage-based billing on Aug 1, 2026."
        from src.data.doc_to_card import card_from_document
        a = card_from_document("Pricing.docx", text)
        b = card_from_document("Pricing.docx", text)
        assert a == b

    def test_conversion_invents_no_sentences(self, tmp_path):
        """bringing-documents-in.md: the draft 'should not contain sentences
        that were not in the original.'"""
        from src.data.doc_to_card import card_from_document
        text = ("## Overview\n\nTier B moves to usage-based billing.\n\n"
                "- Update saved replies\n")
        _title, body = card_from_document("Pricing.md", text)
        assert body == text.strip()      # already-structured input passes through

    def test_docx_structure_is_resolved_into_markdown(self, tmp_path):
        """bringing-documents-in.md: 'heading styles, bold, italic,
        strikethrough, bulleted and numbered lists with nesting, hyperlinks and
        tables are resolved into markdown.'"""
        from src.data.doc_reader import read_document
        body = (
            _para("Overview", ppr='<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>')
            + _para("strong", rpr="<w:rPr><w:b/></w:rPr>")
            + _para("slanted", rpr="<w:rPr><w:i/></w:rPr>")
            + _para("gone", rpr="<w:rPr><w:strike/></w:rPr>")
            + '<w:p><w:hyperlink r:id="rId7"><w:r><w:t>the doc</w:t>'
              '</w:r></w:hyperlink></w:p>'
            + '<w:tbl><w:tr><w:tc>' + _para("H1") + '</w:tc><w:tc>'
            + _para("H2") + '</w:tc></w:tr><w:tr><w:tc>' + _para("a")
            + '</w:tc><w:tc>' + _para("b") + '</w:tc></w:tr></w:tbl>'
        )
        rels = ('<?xml version="1.0"?><Relationships '
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId7" Target="https://x.test/g"/></Relationships>')
        md = read_document(_docx(tmp_path, body, rels=rels))
        assert "# Overview" in md
        assert "**strong**" in md and "*slanted*" in md and "~~gone~~" in md
        assert "[the doc](https://x.test/g)" in md
        assert "| H1 | H2 |" in md and "| a | b |" in md

    def test_docx_reader_skips_unparseable_elements(self, tmp_path):
        """bringing-documents-in.md: 'The reader is deliberately tolerant — an
        element it cannot parse is skipped rather than crashing the import.'"""
        from src.data.doc_reader import read_document
        body = (_para("Kept before", ppr='<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>')
                + '<w:tbl><w:tr><w:tc></w:tc></w:tr><w:bogus/></w:tbl>'
                + '<w:sdt><w:sdtContent>' + _para("inside a content control")
                + '</w:sdtContent></w:sdt>'
                + _para("Kept after"))
        md = read_document(_docx(tmp_path, body))
        assert "# Kept before" in md
        assert "Kept after" in md          # import survived the odd elements

    def test_markdown_file_passes_through_untouched(self, tmp_path):
        """bringing-documents-in.md: 'A .md file passes through untouched.'"""
        from src.data.doc_reader import read_document
        raw = "# Title\n\n- one\n- two\n\n**bold** and `code`\n"
        p = tmp_path / "in.md"
        p.write_text(raw, encoding="utf-8")
        assert read_document(str(p)) == raw

    def test_html_is_converted_and_unknown_read_as_text(self, qapp, tmp_path):
        """bringing-documents-in.md: 'HTML is converted. Anything else is read
        as text.'"""
        from src.data.doc_reader import read_document
        h = tmp_path / "a.html"
        h.write_text("<h1>Head</h1><ul><li>One</li><li>Two</li></ul>",
                     encoding="utf-8")
        md = read_document(str(h))
        assert "# Head" in md
        assert "- One" in md and "- Two" in md
        assert "<h1>" not in md and "<li>" not in md
        odd = tmp_path / "a.weirdext"
        odd.write_text("just some text", encoding="utf-8")
        assert read_document(str(odd)) == "just some text"

    def test_html_conversion_drops_inline_emphasis_in_the_running_app(
            self, qapp, tmp_path):
        """bringing-documents-in.md, fidelity limit of the HTML route.

        html_to_markdown takes a Qt QTextDocument path whenever a QGuiApplication
        exists — which is always true inside the app. That path keeps headings
        and lists but drops <b>/<strong>/<em> entirely. Recorded here so the
        behaviour is pinned; the articles do not claim emphasis survives this
        route (only the .docx route, which is stdlib and does preserve it).
        """
        from src.data.html_markdown import html_to_markdown
        md = html_to_markdown("<p><strong>strong</strong> and <em>em</em></p>")
        assert "strong" in md and "em" in md
        assert "**" not in md and "*" not in md

    def test_pptx_is_refused_by_the_local_route(self, tmp_path):
        """bringing-documents-in.md: 'a binary format such as .pdf or .pptx is
        reported rather than silently converted.'

        .pptx is in doc_reader._BINARY_EXT, so strict read_document raises
        UnsupportedDocumentError instead of building slides from decoded
        garbage; the lenient default returns a clear stub note, never slide
        text or structured markdown.
        """
        from src.data.doc_reader import (
            _BINARY_EXT, UnsupportedDocumentError, read_document,
        )
        assert ".pptx" in _BINARY_EXT
        src = _docx(tmp_path, _para("SECRET SLIDE TEXT"), name="deck.pptx")
        with pytest.raises(UnsupportedDocumentError):
            read_document(src, strict=True)
        stub = read_document(src)                  # lenient default → stub note
        assert "SECRET SLIDE TEXT" not in stub     # no slide extraction
        assert "# " not in stub                    # no structured markdown
        assert "deck.pptx" in stub                 # names the unreadable file

    def test_rollout_date_is_surfaced_as_a_callout(self):
        """bringing-documents-in.md: 'A rollout or effective date in the source
        is surfaced as a callout near the top.'"""
        from src.data.doc_to_card import card_from_document
        text = ("Alma is updating provider pricing tiers.\n\n"
                "Rollout: effective Aug 1, 2026 for all orgs.\n\n"
                "Support owners should update saved replies.")
        _title, body = card_from_document("Pricing Update.gdoc", text)
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert "> [!NOTE]" in lines
        assert lines.index("> [!NOTE]") <= 2
        assert any("Aug 1, 2026" in ln and ln.startswith(">") for ln in lines)

    def test_same_local_file_uploaded_twice_refreshes_one_draft(
            self, empty_db, tmp_path):
        """bringing-documents-in.md: 're-importing the same file refreshes the
        one draft you already have rather than making a duplicate.'

        _ingest_local_file now passes a stable source_ref ('upload:' + abspath)
        to save_document, so a re-upload resolves to the SAME doc_id and
        draft_card_from_document's idempotency (keyed on doc_id) refreshes the
        single draft in place — one document, one draft (see
        tests/test_ingest_dedupe.py).
        """
        conn = empty_db.conn
        p = tmp_path / "policy.txt"
        p.write_text("Providers must re-verify by Aug 1, 2026.", encoding="utf-8")
        first = EnablementPage._ingest_local_file(conn, str(p))
        second = EnablementPage._ingest_local_file(conn, str(p))
        # A refresh of the one draft — not a second draft.
        assert second["draft_id"] == first["draft_id"]
        assert conn.execute(
            "SELECT COUNT(*) FROM guru_content_drafts").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM enablement_documents").fetchone()[0] == 1

    def test_drive_reimport_does_refresh_one_draft(self, empty_db, fake_settings):
        """bringing-documents-in.md, Drive half of the same claim: a Drive
        re-import keys on the file id, so it refreshes rather than duplicates."""
        conn = empty_db.conn

        class Reader:
            def __init__(self, text):
                self._text = text

            def get_file(self, fid):
                return {"id": fid, "name": "Q3 Pricing.gdoc",
                        "mime_type": "application/vnd.google-apps.document"}

            def export_text(self, fid, mime):
                return self._text

        fid = "1A" + "b" * 26
        r1 = store.import_drive_doc(conn, Reader("v1 body text"), fid)
        d1 = store.draft_card_from_document(conn, r1["doc_id"])
        r2 = store.import_drive_doc(conn, Reader("v2 body text"), fid)
        d2 = store.draft_card_from_document(conn, r2["doc_id"])
        assert r1["doc_id"] == r2["doc_id"]
        assert d1["id"] == d2["id"]
        assert conn.execute(
            "SELECT COUNT(*) FROM guru_content_drafts").fetchone()[0] == 1

    def test_published_draft_is_left_alone_on_reimport(self, empty_db, fake_settings):
        """bringing-documents-in.md: '...unless the first was already
        published, in which case the published draft is left alone.'"""
        conn = empty_db.conn
        doc_id = store.save_document(conn, source="drive", name="Doc",
                                     doc_id="DOC-1", full_text="body one")
        d1 = store.draft_card_from_document(conn, doc_id)
        store.publish_draft(conn, d1["id"])           # local mark-pushed
        store.save_document(conn, source="drive", name="Doc",
                            doc_id="DOC-1", full_text="body two")
        d2 = store.draft_card_from_document(conn, doc_id)
        assert d2["id"] == d1["id"] and d2["status"] == "pushed"
        assert store.get_draft(conn, d1["id"])["content"] == d1["content"]


class TestDriveImportUsesAnLLM:
    def test_drive_card_generation_calls_the_model(self, empty_db, fake_settings):
        """bringing-documents-in.md: 'Unlike the local-file route, this path
        does call an LLM to turn the document into a card.'"""
        conn = empty_db.conn
        doc_id = store.save_document(conn, source="drive", name="Q3.gdoc",
                                     doc_id="D1", full_text="Pricing changes.")
        llm = StubLLM()
        out = store.draft_card_from_document(conn, doc_id, llm)
        assert len(llm.prompts) == 1
        assert out["title"] == "Stub Card"

    def test_local_route_and_drive_route_differ_on_the_llm(self, empty_db, fake_settings):
        """bringing-documents-in.md: the same store call is deterministic with
        no client and model-driven with one."""
        conn = empty_db.conn
        doc_id = store.save_document(conn, source="upload", name="Q3.txt",
                                     doc_id="D2", full_text="Pricing changes.")
        det = store.draft_card_from_document(conn, doc_id)
        assert det["title"] == "Q3 — Enablement Guide"
        assert "Stub" not in det["content"]


# ══════════════════════════════════════════════════════════════════════
# editing.md
# ══════════════════════════════════════════════════════════════════════

class TestEditingViews:
    def test_markdown_view_keeps_text_verbatim(self, wb):
        """editing.md: 'Edit markdown gives you the raw source ... What you
        type is kept verbatim.'"""
        wb.set_pending_drafts([{"id": 1, "title": "A", "source": "drive"}],
                              active_id=1)
        raw = "# Head\n\n    indented\n\n<!-- comment -->\n"
        wb.show_draft({"title": "A", "markdown": "x"})
        wb._set_view("edit")
        wb._editor.setPlainText(raw)
        wb._set_view("preview")
        assert wb._current_md == raw

    def test_rich_toolbar_offers_the_documented_controls(self, qapp):
        """editing.md: 'Rich text gives you a toolbar: headings, bold, italic,
        underline, strikethrough, inline code, text colour, highlight,
        bulleted, numbered and task lists, blockquotes, code blocks, tables,
        links, images, horizontal rules, and insertable callout, collapsible
        and card-link blocks.'"""
        from PySide6.QtWidgets import QToolButton
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        tips = " ".join(b.toolTip().lower()
                        for b in ed.findChildren(QToolButton))
        for word in ("bold", "italic", "underline", "strikethrough",
                     "inline code", "colour", "highlight", "bullet list",
                     "numbered list", "checklist", "blockquote", "link"):
            assert word in tips, f"no toolbar control mentioning {word!r}"
        menu_items = " ".join(a.text().lower() for m in ed.findChildren(QMenu)
                              for a in m.actions())
        # tables, images, horizontal rules and the Guru-native blocks live in
        # the "+" insert menu; headings in the text-style menu
        for word in ("heading 1", "table", "divider", "code block", "image",
                     "callout", "collapsible", "card link"):
            assert word in menu_items, f"no menu entry mentioning {word!r}"

    def test_rich_edit_captures_html_and_markdown_edit_clears_it(self, wb):
        """editing.md: 'Editing in rich text also captures a cleaned HTML copy
        ... Editing the markdown source clears that HTML copy.'"""
        wb.set_pending_drafts([{"id": 1, "title": "A", "source": "drive"}],
                              active_id=1)
        wb.show_draft({"title": "A", "markdown": "start"})
        wb._set_view("rich")
        wb._rich.editor.setPlainText("rich body")
        wb._set_view("preview")
        assert wb.current_html() is not None
        assert "rich body" in wb.current_html()
        wb._set_view("edit")
        wb._editor.setPlainText("markdown body")
        wb._set_view("preview")
        assert wb.current_html() is None

    def test_markdown_only_edit_persists_a_null_content_html(self, empty_db, wb):
        """editing.md: 'the publish step re-derives it from your markdown
        instead' — the stored content_html is cleared."""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="old", content_html="<p>old</p>")
        page = Shim(workbench=wb, _conn=lambda: conn)
        wb._current_html = None
        EnablementPage._on_content_edited(page, did, "new markdown")
        assert store.get_draft(conn, did)["content_html"] is None


class TestInlineAI:
    def test_four_fixed_ai_actions_only(self, qapp):
        """editing.md: 'Both offer the same four actions: rewrite more
        concisely, rewrite to be AI-readable, turn the passage into numbered
        steps, or rewrite to match the configured style guide.' and 'You cannot
        type your own instruction.'"""
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        actions = RichTextEditor._AI_ACTIONS
        assert len(actions) == 4
        joined = " ".join(i for _l, i in actions).lower()
        assert "concisely" in joined
        assert "ai-readable" in joined
        assert "numbered list" in joined
        assert "style guide" in joined

    def test_slash_menu_only_triggers_at_the_start_of_a_line(self, qapp):
        """editing.md: 'Type / at the start of a line' / 'It only triggers at
        the start of a line.'"""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        ed.editor.setPlainText("hello")
        evt = QKeyEvent(QEvent.KeyPress, Qt.Key_Slash, Qt.NoModifier, "/")
        cur = ed.editor.textCursor()
        cur.movePosition(QTextCursor.StartOfBlock)
        ed.editor.setTextCursor(cur)
        assert ed._is_slash_keypress(evt) is True
        cur.movePosition(QTextCursor.EndOfBlock)
        ed.editor.setTextCursor(cur)
        assert ed._is_slash_keypress(evt) is False

    def test_slash_menu_carries_the_four_actions(self, qapp):
        """editing.md: 'A menu opens at the cursor and the slash is not
        inserted.'"""
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        ed.editor.setPlainText("a line of text")
        menu = ed._build_slash_menu()
        assert [a.text() for a in menu.actions()] == \
            [lbl for lbl, _i in RichTextEditor._AI_ACTIONS]

    def test_slash_keypress_is_swallowed_not_inserted(self, qapp, monkeypatch):
        """editing.md: 'the slash is not inserted.'"""
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        ed.editor.setPlainText("")
        monkeypatch.setattr(RichTextEditor, "_open_slash_menu", lambda self: None)
        evt = QKeyEvent(QEvent.KeyPress, Qt.Key_Slash, Qt.NoModifier, "/")
        handled = ed.eventFilter(ed.editor, evt)
        assert handled is True
        assert ed.editor.toPlainText() == ""

    def test_slash_is_a_plain_slash_in_the_markdown_view(self, wb):
        """editing.md: 'In the markdown view, / is just a slash.'"""
        from PySide6.QtTest import QTest
        wb._set_view("edit")
        wb._editor.setPlainText("")
        wb._editor.setFocus()
        QTest.keyClicks(wb._editor, "/")
        assert wb._editor.toPlainText() == "/"

    def test_context_submenu_is_disabled_without_a_selection(self, qapp):
        """editing.md: 'An "ask Renn" submenu appears; it is greyed out when
        nothing is selected.'"""
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        ed.editor.setPlainText("a passage of text")
        menu = ed._build_context_menu()
        sub = [a.menu() for a in menu.actions()
               if a.menu() and "Renn" in a.text()]
        assert len(sub) == 1
        assert sub[0].isEnabled() is False
        cur = ed.editor.textCursor()
        cur.select(QTextCursor.Document)
        ed.editor.setTextCursor(cur)
        menu2 = ed._build_context_menu()
        sub2 = [a.menu() for a in menu2.actions()
                if a.menu() and "Renn" in a.text()][0]
        assert sub2.isEnabled() is True
        assert len(sub2.actions()) == 4

    def test_selection_is_folded_into_the_instruction(self):
        """editing.md: 'The highlighted passage is attached to the request with
        a note telling the model to apply the change to that passage only and
        return the full revised card.'"""
        out = EnablementPage._scoped_instruction("Rewrite this.", "the passage")
        assert "Rewrite this." in out
        assert "the passage" in out
        assert "selected passage" in out.lower()
        assert "full revised card" in out.lower()

    def test_empty_selection_applies_to_the_whole_card(self):
        """editing.md: 'With genuinely nothing to scope to, the instruction
        applies to the whole card.'"""
        assert EnablementPage._scoped_instruction("Rewrite this.", "") == \
            "Rewrite this."
        assert EnablementPage._scoped_instruction("Rewrite this.", "   ") == \
            "Rewrite this."

    def test_slash_menu_with_no_selection_uses_the_current_line(self, qapp):
        """editing.md: 'From the / menu with nothing selected, the current line
        is used.'"""
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        ed.editor.setPlainText("line one\nline two")
        cur = ed.editor.textCursor()
        cur.movePosition(QTextCursor.Start)
        cur.movePosition(QTextCursor.Down)
        ed.editor.setTextCursor(cur)
        assert ed._selection_text() == "line two"

    def test_inline_ai_ask_reaches_the_host(self, wb):
        """editing.md: the rich editor's ask is routed out of the canvas for
        the host to run."""
        seen: list[tuple] = []
        wb.ai_edit_requested.connect(lambda i, s: seen.append((i, s)))
        wb._rich.ai_edit_requested.emit("Rewrite", "sel")
        assert seen == [("Rewrite", "sel")]

    def test_expanded_editor_ai_ask_is_discarded(self, wb):
        """editing.md: 'The AI actions do nothing inside the expanded editor
        ... its inline AI menu is not connected to anything — the menu appears
        and the action is discarded.'"""
        wb.show_draft({"title": "T", "markdown": "body"})
        wb._open_expand()
        seen: list[tuple] = []
        wb.ai_edit_requested.connect(lambda i, s: seen.append((i, s)))
        wb._overlay._rich.ai_edit_requested.emit("Rewrite", "sel")
        assert seen == []                      # overlay ask goes nowhere
        wb._rich.ai_edit_requested.emit("Rewrite", "sel")
        assert seen == [("Rewrite", "sel")]    # inline ask still routed

    def test_revision_runs_off_the_main_thread(self, monkeypatch, wb):
        """editing.md: 'The revision runs off the main thread against your
        active draft.'"""
        import threading
        started: list[dict] = []

        class FakeThread:
            def __init__(self, target=None, daemon=None, **kw):
                started.append({"daemon": daemon})

            def start(self):
                started[-1]["started"] = True

        monkeypatch.setattr(threading, "Thread", FakeThread)
        wb.set_pending_drafts([{"id": 5, "title": "A", "source": "drive"}],
                              active_id=5)
        page = Shim(workbench=wb, _engine_db_path=lambda: ":memory:")
        EnablementPage._on_ai_edit(page, "Rewrite", "sel")
        assert started and started[0]["daemon"] is True
        assert any("revising" in s for s in page.status)

    def test_revision_writes_to_the_draft(self, empty_db, fake_settings, monkeypatch):
        """editing.md: 'The revision is written to the draft in the database,
        so it is already saved when you see it.'"""
        import src.gemini.client_factory as cf
        from src.data.chat_tools.enablement_tools import _revise_draft_impl
        conn = empty_db.conn
        did = _new_draft(conn, title="Old", content="old body")
        monkeypatch.setattr(cf, "build_client_for_task",
                            lambda *a, **kw: StubLLM("revised body"))
        res = _revise_draft_impl(conn, did, "Rewrite this.")
        assert res["ok"] is True
        assert store.get_draft(conn, did)["content"] == "revised body"

    def test_revision_failure_leaves_the_draft_unchanged(self, empty_db, monkeypatch):
        """editing.md: 'Your draft is unchanged when a revision fails — nothing
        is written unless the model returned a card.' and 'The most common is
        no model client available.'"""
        import src.gemini.client_factory as cf
        from src.data.chat_tools.enablement_tools import _revise_draft_impl
        conn = empty_db.conn
        did = _new_draft(conn, title="Old", content="old body")
        monkeypatch.setattr(cf, "build_client_for_task", lambda *a, **kw: None)
        res = _revise_draft_impl(conn, did, "Rewrite this.")
        assert res == {"ok": False, "error": "no_llm_client"}
        assert store.get_draft(conn, did)["content"] == "old body"

    def test_revision_status_line_reports_success_and_failure(self, wb):
        """editing.md: 'The status line should say a revision is running, then
        say the draft was revised and reloaded. A failure should say so.'"""
        reloads: list = []
        page = Shim(workbench=wb,
                    _reload_active_draft_canvas=lambda did=None: reloads.append(did))
        EnablementPage._on_ai_edit_done(page, {"ok": True, "draft_id": 7})
        assert any("revised" in s and "reloaded" in s for s in page.status)
        EnablementPage._on_ai_edit_done(page, {"ok": False, "error": "boom"})
        assert any("AI edit failed" in s and "boom" in s for s in page.status)

    def test_revision_lands_on_the_draft_it_was_dispatched_for(self, wb):
        """editing.md: 'A revision you dispatched on one draft, then switched
        away from, should still land on the draft it was dispatched for.'"""
        wb.set_pending_drafts([{"id": 2, "title": "B", "source": "drive"}],
                              active_id=2)
        reloads: list = []
        page = Shim(workbench=wb,
                    _reload_active_draft_canvas=lambda did=None: reloads.append(did))
        EnablementPage._on_ai_edit_done(page, {"ok": True, "draft_id": 1})
        assert reloads == [1]              # dispatched draft, not the active one

    def test_reload_refreshes_only_the_dispatched_draft_cache(self, empty_db, wb):
        """editing.md: 'the canvas reloads in place from the newly-stored
        content.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="A", content="fresh from the model")
        wb.set_pending_drafts([{"id": did, "title": "A", "source": "drive"}],
                              active_id=did)
        wb.show_draft({"title": "A", "markdown": "stale"})
        page = Shim(workbench=wb, _conn=lambda: conn)
        EnablementPage._reload_active_draft_canvas(page, did)
        assert wb._current_md == "fresh from the model"
        assert page._drafts[did]["content"] == "fresh from the model"


# ══════════════════════════════════════════════════════════════════════
# review-changes.md
# ══════════════════════════════════════════════════════════════════════

class TestDiffRendering:
    def test_lines_are_classed_add_del_equal(self):
        """review-changes.md: 'Removed lines are shown in red with a minus in
        the gutter, added lines in green with a plus, unchanged lines in
        neutral.'"""
        from src.ui.pages.enablement.diff_view import _ROW_STYLE, diff_lines
        rows = diff_lines("keep\ndrop", "keep\nadd")
        assert rows == [("equal", "keep"), ("del", "drop"), ("add", "add")]
        assert _ROW_STYLE["add"][2] == "+"
        assert _ROW_STYLE["del"][2] == "−"
        assert _ROW_STYLE["equal"][2] == " "

    def test_replaced_block_lists_removals_then_additions(self):
        """review-changes.md: 'Where a block was replaced, the removed lines
        are listed first and the added lines directly after.'"""
        from src.ui.pages.enablement.diff_view import diff_lines
        rows = diff_lines("a1\na2\nz", "b1\nb2\nz")
        assert [t for t, _ in rows] == ["del", "del", "add", "add", "equal"]

    def test_identical_sides_render_a_no_changes_row(self, qapp):
        """review-changes.md: 'When the two sides are identical, the view says
        so rather than showing an empty box.'"""
        from src.ui.pages.enablement.diff_view import DiffView
        from PySide6.QtWidgets import QFrame, QLabel
        v = DiffView()
        v.set_diff("same\ntext", "same\ntext")
        empties = [f for f in v.findChildren(QFrame)
                   if f.property("diffEmpty")]
        assert len(empties) == 1
        assert any("No changes" in lbl.text()
                   for lbl in empties[0].findChildren(QLabel))

    def test_switching_into_review_commits_the_pending_edit(self, wb):
        """review-changes.md: 'Switching into this view first commits whatever
        you were editing, so the diff always reflects your latest text.'"""
        wb.set_pending_drafts([{"id": 4, "title": "D", "source": "drive"}],
                              active_id=4)
        wb.show_draft({"title": "D", "markdown": "before"})
        edits: list[tuple] = []
        wb.content_edited.connect(lambda i, m: edits.append((i, m)))
        wb._set_view("edit")
        wb._editor.setPlainText("after the edit")
        wb._set_view("diff")
        assert edits == [(4, "after the edit")]
        assert any(text == "after the edit"
                   for _tag, text in wb._diff.rows())


class TestDiffBaselineGap:
    def test_workbench_exposes_a_baseline_setter(self, wb):
        """review-changes.md: 'The Workbench has a way to receive the linked
        card's current content.'"""
        assert callable(wb.set_linked_card_md)
        wb.show_draft({"title": "D", "markdown": "new line\nshared"})
        wb.set_linked_card_md("old line\nshared")
        wb._set_view("diff")
        tags = [t for t, _ in wb._diff.rows()]
        assert "del" in tags and "add" in tags

    def test_nothing_in_the_app_calls_the_baseline_setter(self):
        """review-changes.md: 'nothing in the app calls it.'"""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[1] / "src"
        callers = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"\.set_linked_card_md\s*\(", text):
                line = text[:m.start()].count("\n") + 1
                callers.append(f"{path.name}:{line}")
        assert callers == [], f"unexpected caller(s): {callers}"

    def test_card_payload_carries_no_baseline(self, empty_db, wb):
        """review-changes.md: 'the card data handed to the canvas does not
        carry it.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        page = Shim(workbench=wb)
        card = EnablementPage._card_from_draft(page, conn, store.get_draft(conn, did))
        assert "linked_card_md" not in card

    def test_diff_renders_all_green_with_no_baseline(self, empty_db, wb):
        """review-changes.md: 'In practice the baseline is empty, so the diff
        has nothing to compare against and renders your entire draft as green
        additions with no red.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="line one\nline two")
        page = Shim(workbench=wb)
        wb.show_draft(EnablementPage._card_from_draft(
            page, conn, store.get_draft(conn, did)))
        wb._set_view("diff")
        tags = [t for t, _ in wb._diff.rows()]
        assert tags == ["add", "add"]
        assert "del" not in tags


class TestWordLevelDiffIsWebOnly:
    def test_word_level_diff_exists_in_code(self):
        """review-changes.md: 'A word-level version of this diff ... exists in
        the code.'"""
        from src.data.text_diff import diff_words
        rows = diff_words("the quick fox", "the slow fox")
        changed = [r for r in rows if r.get("spans")]
        assert changed, "no intra-line word spans produced"

    def test_word_level_diff_is_not_used_by_the_qt_view(self):
        """review-changes.md: '...but only in the React version of the
        Workbench' — the shipped Qt view is line-level."""
        import inspect
        from src.ui.pages.enablement import diff_view, workbench
        assert "diff_words" not in inspect.getsource(diff_view)
        assert "diff_words" not in inspect.getsource(workbench)
        import src.services.enablement_web as web
        assert "diff_words" in inspect.getsource(web)

    def test_react_workbench_is_off_by_default(self, monkeypatch):
        """review-changes.md: '...which does not render in the shipped
        configuration.'"""
        import src.data.settings_manager as sm
        from src.ui.web import web_flags
        monkeypatch.setattr(sm, "get_section", lambda name, default=None: {})
        assert web_flags.web_tabs_mode() == "off"

    def test_renn_publish_from_chat_requires_signoff(self, empty_db):
        """review-changes.md: 'its chat-driven publish path additionally
        requires a recorded sign-off.'"""
        from src.data.chat_tools.enablement_tools import _push_guru_draft_impl
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        assert store.get_draft(conn, did)["require_approval"] == 1
        res = _push_guru_draft_impl(conn, did)
        assert res["ok"] is False
        assert res["error"] == "approval_required"
        assert store.get_draft(conn, did)["status"] == "pending"


# ══════════════════════════════════════════════════════════════════════
# publish-guru.md
# ══════════════════════════════════════════════════════════════════════

class TestGuruPublishMenu:
    def test_tools_menu_offers_new_and_existing_guru_destinations(self, wb):
        """publish-guru.md: 'The Tools menu offers two Guru destinations: a new
        card, or an existing card picked from a submenu.'"""
        # use the retained submenu refs: re-wrapping via QAction.menu() trips
        # PySide's addMenu(str) ownership footgun and deletes the C++ object
        _imp, guru, _drive = wb._tools_submenus
        assert guru.title() == "Push to Guru"
        assert "Push to Guru" in [a.text() for a in wb._tools_menu.actions()]
        labels = [a.text() for a in guru.actions()]
        assert "New Guru card" in labels
        assert "Existing Guru card" in labels
        dests: list[str] = []
        wb.publish_requested.connect(dests.append)
        [a for a in guru.actions() if a.text() == "New Guru card"][0].trigger()
        assert dests == ["guru_new"]

    def test_existing_card_submenu_lists_at_most_twenty_five(self, wb):
        """publish-guru.md: 'Opening the submenu fetches your Guru cards; up to
        twenty-five are listed.' and 'it fetches once.'"""
        class ManyCards:
            def __init__(self):
                self.calls = 0

            def search_cards(self, q):
                self.calls += 1
                return [{"id": f"c{i}", "preferredPhrase": f"Card {i}"}
                        for i in range(40)]

        client = ManyCards()
        page = Shim(workbench=wb, _guru_client=client)
        EnablementPage._fetch_existing_cards(page)
        assert len(wb._existing_menu.actions()) == 25
        EnablementPage._fetch_existing_cards(page)
        assert client.calls == 1                 # one-shot fetch

    def test_empty_submenu_says_no_cards_found(self, wb):
        """publish-guru.md: 'The existing-card submenu is empty or says no
        cards were found.'"""
        wb.set_existing_cards([])
        acts = wb._existing_menu.actions()
        assert len(acts) == 1
        assert acts[0].text() == "No cards found"
        assert acts[0].isEnabled() is False

    def test_choosing_an_existing_card_stamps_then_publishes(self, empty_db, wb):
        """publish-guru.md: 'Choosing one stamps the draft with that card's id
        and then publishes, which takes the update path.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        wb.set_pending_drafts([{"id": did, "title": "D", "source": "drive"}],
                              active_id=did)
        pushed: list[int] = []
        page = Shim(workbench=wb, _conn=lambda: conn,
                    _on_push=pushed.append)
        EnablementPage._on_publish(page, "guru_existing:CARD-77")
        assert store.get_draft(conn, did)["card_id"] == "CARD-77"
        assert pushed == [did]

    def test_card_picker_offers_a_collection_filter_and_search(self, qapp):
        """publish-guru.md: 'use the Guru import instead, which opens a picker
        with a collection filter and a search box rather than a capped
        list.'"""
        from src.ui.pages.enablement.card_picker import GuruCardPickerDialog

        class Client:
            def list_collections(self):
                return [{"id": "C1", "name": "Enablement"}]

            def list_cards(self, collection_id=None):
                return [{"id": "a", "title": "Alpha"}]

            def search_cards(self, q):
                return [{"id": "b", "title": "Beta"}]

        dlg = GuruCardPickerDialog(Client())
        assert dlg._collections.count() == 2          # "All collections" + one
        assert dlg._search.placeholderText() == "Search cards…"
        assert dlg._list.count() == 1
        dlg._search.setText("beta")
        dlg._reload()
        assert dlg._list.item(0).text() == "Beta"


class TestGuruPublishBehaviour:
    def test_new_card_html_matches_the_preview_converter(self, empty_db):
        """publish-guru.md: 'The draft's markdown is converted to HTML with the
        same converter the preview uses, native block directives such as
        callouts and collapsibles are expanded.'"""
        from src.data.guru_blocks import expand_blocks
        from src.data.html_markdown import markdown_to_html
        conn = empty_db.conn
        md = "# Head\n\n:::note\nWatch out.\n:::\n"
        did = _new_draft(conn, title="D", content=md)
        client = FakeGuru()
        store.publish_draft(conn, did, guru_client=client, collection_id="COL-1")
        sent_html = client.created[0][2]
        assert sent_html == expand_blocks(markdown_to_html(md))
        assert "<h1>Head</h1>" in sent_html

    def test_captured_rich_html_is_sent_instead_when_present(self, empty_db):
        """publish-guru.md: 'If you last edited in the rich-text view, the
        captured HTML is sent instead so colour and highlight survive.'"""
        conn = empty_db.conn
        rich = '<p><span style="color:#c00">red words</span></p>'
        did = _new_draft(conn, title="D", content="red words",
                         content_html=rich)
        client = FakeGuru()
        store.publish_draft(conn, did, guru_client=client, collection_id="COL-1")
        assert "color:#c00" in client.created[0][2]

    def test_new_card_id_is_recorded_on_the_draft(self, empty_db):
        """publish-guru.md: 'The new card's id is recorded on the draft. A
        later edit and republish therefore updates that same card.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        client = FakeGuru()
        out = store.publish_draft(conn, did, guru_client=client,
                                  collection_id="COL-1")
        assert out["card_id"] == "NEW-CARD-ID"
        assert store.get_draft(conn, did)["card_id"] == "NEW-CARD-ID"

    def test_publish_marks_pushed_and_writes_an_audit_record(self, empty_db):
        """publish-guru.md: 'the draft is marked as pushed, which freezes it
        against further edits, and an audit record of the update is
        written.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        store.publish_draft(conn, did, guru_client=FakeGuru(),
                            collection_id="COL-1")
        assert store.get_draft(conn, did)["status"] == "pushed"
        row = conn.execute(
            "SELECT status, card_id FROM card_update_provenance WHERE draft_id=?",
            (did,)).fetchone()
        assert row is not None and row[0] == "published"
        assert row[1] == "NEW-CARD-ID"

    def test_second_publish_reports_success_without_writing(self, empty_db):
        """publish-guru.md: 'The second attempt against an already-pushed draft
        reports success without doing anything.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        client = FakeGuru()
        store.publish_draft(conn, did, guru_client=client, collection_id="C")
        again = store.publish_draft(conn, did, guru_client=client,
                                    collection_id="C")
        assert again == {"ok": True, "draft_id": did, "status": "pushed",
                         "already": True}
        assert len(client.created) == 1

    def test_rejected_publish_leaves_the_draft_editable(self, empty_db, wb):
        """publish-guru.md: 'A publish that Guru rejects should surface the
        error and leave the draft unpublished and still editable.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")

        class Rejecting:
            def create_card(self, *a, **kw):
                raise RuntimeError("403 forbidden")

        res = store.publish_draft(conn, did, guru_client=Rejecting(),
                                  collection_id="C")
        assert res["ok"] is False
        assert "403 forbidden" in res["error"]
        assert store.get_draft(conn, did)["status"] == "pending"
        page = Shim(workbench=wb, _conn=lambda: conn)
        EnablementPage._on_content_edited(page, did, "still editable")
        assert store.get_draft(conn, did)["content"] == "still editable"

    def test_menu_publish_has_no_signoff_gate(self, empty_db):
        """publish-guru.md: 'There is no confirmation step on this path ... The
        separate sign-off gate ... applies to publishes that Renn initiates
        from chat, not to this menu.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        assert store.get_draft(conn, did)["require_approval"] == 1
        assert store.get_draft(conn, did)["approved_at"] is None
        res = store.publish_draft(conn, did, guru_client=FakeGuru(),
                                  collection_id="C")
        assert res["ok"] is True                     # no gate on this path

    def test_menu_publish_passes_the_collection_but_no_folder(
            self, empty_db, wb, monkeypatch):
        """publish-guru.md: 'The Workbench menu publishes into the configured
        collection; it does not pass a folder when creating a card, even when a
        publish folder is set in settings.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        wb.set_pending_drafts([{"id": did, "title": "D", "source": "drive"}],
                              active_id=did)
        seen: dict = {}
        monkeypatch.setattr(store, "publish_draft",
                            lambda *a, **kw: seen.update(kw) or {"ok": True})
        page = Shim(workbench=wb, _conn=lambda: conn,
                    _guru_for_push=lambda: FakeGuru(),
                    _publish_collection_id=lambda: "COL-CONFIGURED")
        EnablementPage._on_push(page, did)
        assert seen["collection_id"] == "COL-CONFIGURED"
        assert "folder_id" not in seen

    def test_renn_push_path_does_read_the_publish_folder(self):
        """publish-guru.md contrast: the chat push path is the one that reads
        publish_folder_id — since WS-B/G3 it does so through
        resolve_publish_target (explicit arg > per-kind map > the configured
        global default), so the lock follows the read."""
        import inspect
        from src.data import enablement_store
        from src.data.chat_tools import enablement_tools
        src = inspect.getsource(enablement_tools._push_guru_draft_impl)
        assert "resolve_publish_target" in src
        resolver = inspect.getsource(enablement_store.resolve_publish_target)
        assert "publish_folder_id" in resolver

    def test_uploaded_draft_has_no_card_link_until_publish(self, empty_db):
        """publish-guru.md: 'A draft built from an uploaded file has no card
        link until you publish it once or pick an existing card as the
        target.'"""
        conn = empty_db.conn
        doc_id = store.save_document(conn, source="upload", name="a.txt",
                                     doc_id="D1", full_text="body text here")
        d = store.draft_card_from_document(conn, doc_id)
        assert (store.get_draft(conn, d["id"])["card_id"] or "") == ""
        store.publish_draft(conn, d["id"], guru_client=FakeGuru(),
                            collection_id="C")
        assert store.get_draft(conn, d["id"])["card_id"] == "NEW-CARD-ID"


# ══════════════════════════════════════════════════════════════════════
# publish-drive.md
# ══════════════════════════════════════════════════════════════════════

class TestDriveDestinationsWriteNothing:
    def test_tools_menu_offers_two_drive_destinations(self, wb):
        """publish-drive.md: 'The Tools menu offers to save a draft to Drive as
        a new Google Doc or as an update to an existing one.'"""
        _imp, _guru, drive = wb._tools_submenus
        assert drive.title() == "Save to Drive"
        assert "Save to Drive" in [a.text() for a in wb._tools_menu.actions()]
        assert [a.text() for a in drive.actions()] == \
            ["New Google Doc", "Update existing doc"]
        dests: list[str] = []
        wb.publish_requested.connect(dests.append)
        for act in drive.actions():
            act.trigger()
        assert dests == ["drive_new", "drive_update"]

    @pytest.mark.parametrize("dest", ["drive_new", "drive_update"])
    def test_drive_destination_only_sets_a_message(self, empty_db, wb, dest,
                                                   monkeypatch):
        """publish-drive.md: 'Choosing either Drive destination sets a status
        message and posts a matching line into the assistant panel. That is the
        entire implementation — there is no Drive call, no document created, no
        document updated, and no error raised.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="body")
        wb.set_pending_drafts([{"id": did, "title": "D", "source": "drive"}],
                              active_id=did)
        monkeypatch.setattr(store, "publish_draft",
                            lambda *a, **kw: pytest.fail("publish must not run"))
        before = dict(store.get_draft(conn, did))
        page = Shim(workbench=wb, _conn=lambda: conn,
                    _on_push=lambda d: pytest.fail("no push on the Drive path"))
        EnablementPage._on_publish(page, dest)
        assert len(page.status) == 1
        assert page.chat == [("a", page.status[0])]
        assert dict(store.get_draft(conn, did)) == before

    @pytest.mark.parametrize("dest", ["drive_new", "drive_update"])
    def test_drive_messages_are_labelled_demonstration_text(self, wb, dest):
        """publish-drive.md: 'both are labelled as demonstration text.'"""
        page = Shim(workbench=wb)
        EnablementPage._on_publish(page, dest)
        assert page.status[0].endswith("(demo).")

    def test_no_drive_writing_capability_exists(self):
        """publish-drive.md: 'There is no Drive-writing code behind these menu
        entries in this build.'"""
        from src.data.drive_reader import DriveReader
        public = {n for n in dir(DriveReader) if not n.startswith("_")}
        writes = {n for n in public
                  if any(v in n for v in ("create", "update", "upload",
                                          "write", "insert", "delete"))}
        assert writes == set()

    def test_drive_reading_does_work(self, empty_db):
        """publish-drive.md: 'Reading from Drive is a different matter and does
        work ... Only writing back is missing.'"""
        class Reader:
            def get_file(self, fid):
                return {"id": fid, "name": "Q3 Pricing.gdoc",
                        "mime_type": "application/vnd.google-apps.document"}

            def export_text(self, fid, mime):
                return "Real Drive body text."

        fid = "1A" + "b" * 26
        res = store.import_drive_doc(empty_db.conn, Reader(), fid)
        assert res["ok"] is True and res["chars"] == len("Real Drive body text.")
        doc = store.get_document(empty_db.conn, res["doc_id"])
        assert doc["full_text"] == "Real Drive body text."

    def test_renn_drive_upload_is_gated_behind_a_confirmation(self):
        """publish-drive.md: 'Renn can upload generated artifacts to Drive, and
        that route does open a confirmation before writing.'"""
        from src.data.chat_tools import registry
        names = set(registry.get_tool_registry())
        assert "request_upload_artifact_to_drive" in names
        assert "upload_artifact_to_drive" not in names   # no ungated tool
        import inspect
        from src.data.chat_tools import artifact_tools
        src = inspect.getsource(artifact_tools.handle_request_upload_artifact)
        assert "_emit_confirm_write" in src


# ══════════════════════════════════════════════════════════════════════
# style-guides.md
# ══════════════════════════════════════════════════════════════════════

class TestGuideManagement:
    def test_guide_section_offers_the_documented_verbs(self, qapp):
        """style-guides.md: 'paste the text directly ... upload one or more
        documents ... upload a whole folder ... import one from a Google Doc
        URL ... clear the setting.'"""
        from PySide6.QtWidgets import QPushButton
        from src.ui.pages.enablement.settings import _GuideSection
        sec = _GuideSection("STYLE GUIDE", "hint")
        verbs: list[str] = []
        sec.action.connect(verbs.append)
        for btn in sec.findChildren(QPushButton):
            if btn.text() in ("Paste…", "Upload…",
                              "Upload folder…", "From Drive…",
                              "Clear"):
                btn.click()
        assert set(verbs) == {"paste", "upload", "upload_folder", "drive", "clear"}

    def test_guide_section_supports_view_edit_switch_and_delete(self, qapp):
        """style-guides.md: 'view and edit the active one's text in place,
        switch which stored one is active, delete stored ones.'"""
        from src.ui.pages.enablement.settings import _GuideSection
        sec = _GuideSection("STYLE GUIDE", "hint")
        saved: list[str] = []
        activated: list[str] = []
        deleted: list[str] = []
        sec.saved.connect(saved.append)
        sec.activate.connect(activated.append)
        sec.delete_doc.connect(deleted.append)
        sec.set_content("original text")
        sec._begin_edit()
        sec._editor.setPlainText("edited text")
        sec._save_edit()
        assert saved == ["edited text"]
        sec.set_library([{"doc_id": "g1", "name": "[STYLE-GUIDE] A",
                          "chars": 10, "active": True},
                         {"doc_id": "g2", "name": "[STYLE-GUIDE] B",
                          "chars": 20, "active": False}])
        from PySide6.QtWidgets import QLabel, QPushButton
        rows = sec._library
        assert rows.count() == 2
        labels = {lbl.text() for i in range(rows.count())
                  for lbl in rows.itemAt(i).widget().findChildren(QLabel)}
        assert {"A", "B", "10 chars", "20 chars", "Active"} <= labels
        make_active = [b for i in range(rows.count())
                       for b in rows.itemAt(i).widget().findChildren(QPushButton)
                       if b.text() == "Make active"]
        assert len(make_active) == 1        # only the inactive row offers it
        make_active[0].click()
        assert activated == ["g2"]
        delete = [b for i in range(rows.count())
                  for b in rows.itemAt(i).widget().findChildren(QPushButton)
                  if b.text() == "Delete"]
        assert len(delete) == 2
        delete[0].click()
        assert deleted == ["g1"]

    def test_supported_guide_extensions(self):
        """style-guides.md: 'upload one or more documents (.md, .markdown,
        .txt, .docx, .html, .htm)'."""
        assert EnablementPage._GUIDE_EXTS == {
            ".md", ".markdown", ".txt", ".docx", ".html", ".htm"}
        for ext in ("*.md", "*.markdown", "*.txt", "*.docx", "*.html", "*.htm"):
            assert ext in EnablementPage._GUIDE_FILE_FILTER

    def test_folder_upload_is_recursive_capped_and_skips_hidden(self, tmp_path):
        """style-guides.md: 'supported files are found recursively, capped at
        fifty, with hidden and Office lock files skipped.'"""
        (tmp_path / "nested" / "deep").mkdir(parents=True)
        (tmp_path / "top.md").write_text("a", encoding="utf-8")
        (tmp_path / "nested" / "mid.txt").write_text("b", encoding="utf-8")
        (tmp_path / "nested" / "deep" / "low.docx").write_text("c", encoding="utf-8")
        (tmp_path / ".hidden.md").write_text("x", encoding="utf-8")
        (tmp_path / "~$lock.docx").write_text("x", encoding="utf-8")
        (tmp_path / "skip.pdf").write_text("x", encoding="utf-8")
        found = EnablementPage._guide_files_in_folder(str(tmp_path))
        names = sorted(os.path.basename(p) for p in found)
        assert names == ["low.docx", "mid.txt", "top.md"]

    def test_folder_upload_cap_is_fifty(self, tmp_path):
        """style-guides.md: 'the scan stops at fifty files.'"""
        for i in range(60):
            (tmp_path / f"g{i:03d}.md").write_text("x", encoding="utf-8")
        assert len(EnablementPage._guide_files_in_folder(str(tmp_path))) == 50

    def test_folder_scan_is_sorted(self, tmp_path):
        """style-guides.md: 'ordering follows the sort, not your selection
        order.'"""
        for name in ("zulu.md", "alpha.md", "mike.md"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        found = EnablementPage._guide_files_in_folder(str(tmp_path))
        assert [os.path.basename(p) for p in found] == \
            ["alpha.md", "mike.md", "zulu.md"]

    def test_multi_upload_stores_each_and_last_becomes_active(
            self, empty_db, fake_settings, tmp_path):
        """style-guides.md: 'Uploading several at once stores each of them; the
        last one becomes active.' and 'The last file stored wins.'"""
        conn = empty_db.conn
        paths = []
        for name in ("alpha.md", "bravo.md", "charlie.md"):
            p = tmp_path / name
            p.write_text(f"guide body for {name}", encoding="utf-8")
            paths.append(str(p))
        page = Shim()
        EnablementPage._store_guide_files(page, conn, paths,
                                          store.set_style_guide,
                                          "style-guide", "Style guide")
        guides = store.list_style_guides(conn)
        assert len(guides) == 3
        active = [g for g in guides if g["active"]]
        assert len(active) == 1
        assert active[0]["name"].endswith("charlie")
        assert store.get_style_guide(conn) == "guide body for charlie.md"

    def test_only_one_guide_and_one_template_active(self, empty_db, fake_settings):
        """style-guides.md: 'Only one style guide and one card template are
        active at a time.'"""
        conn = empty_db.conn
        store.set_style_guide(conn, "tone A", name="A", doc_id="sg-a")
        store.set_style_guide(conn, "tone B", name="B", doc_id="sg-b")
        store.set_card_template(conn, "skeleton", name="T", doc_id="ct-a")
        assert sum(1 for g in store.list_style_guides(conn) if g["active"]) == 1
        assert sum(1 for t in store.list_card_templates(conn) if t["active"]) == 1
        assert store.get_style_guide(conn) == "tone B"
        assert store.get_card_template(conn) == "skeleton"

    def test_library_rows_carry_char_counts_and_active_flag(
            self, empty_db, fake_settings):
        """style-guides.md: 'The settings panel should show the active guide's
        status and character count, and list every stored one with the active
        one marked.'"""
        conn = empty_db.conn
        store.set_style_guide(conn, "12345", name="Short", doc_id="sg-1")
        store.set_style_guide(conn, "1234567890", name="Long", doc_id="sg-2")
        rows = {g["name"]: g for g in store.list_style_guides(conn)}
        short = [v for k, v in rows.items() if k.endswith("Short")][0]
        long_ = [v for k, v in rows.items() if k.endswith("Long")][0]
        assert short["chars"] == 5 and long_["chars"] == 10
        assert long_["active"] is True and short["active"] is False


class TestGuideInjection:
    def test_style_guide_block_instructs_strict_adherence(
            self, empty_db, fake_settings):
        """style-guides.md: 'the active style guide is attached as a block
        instructing the model to follow it strictly for tone, structure and
        formatting.'"""
        conn = empty_db.conn
        assert store.style_guide_block(conn) == ""
        store.set_style_guide(conn, "Write plainly.")
        block = store.style_guide_block(conn)
        assert "follow it strictly for tone, structure and formatting" in block
        assert "Write plainly." in block

    def test_card_template_block_demands_the_exact_heading_layout(
            self, empty_db, fake_settings):
        """style-guides.md: 'instructing the model to reproduce its heading
        layout exactly — same headings, same order — filling the bracketed
        slots with real content and dropping the template's own instructional
        notes.'"""
        conn = empty_db.conn
        assert store.card_template_block(conn) == ""
        store.set_card_template(conn, "# [Title]\n## FAQs")
        block = store.card_template_block(conn)
        assert "same headings, same order" in block
        assert "[slot]" in block
        assert "not text to copy into the card" in block
        assert "## FAQs" in block

    def test_both_blocks_reach_drive_card_generation(self, empty_db, fake_settings):
        """style-guides.md: 'Both blocks are injected into card generation from
        a Drive import.'"""
        conn = empty_db.conn
        store.set_style_guide(conn, "TONE-MARKER")
        store.set_card_template(conn, "TEMPLATE-MARKER")
        doc_id = store.save_document(conn, source="drive", name="Q3.gdoc",
                                     doc_id="D1", full_text="Body.")
        llm = StubLLM()
        store.draft_card_from_document(conn, doc_id, llm)
        assert "TONE-MARKER" in llm.prompts[0]
        assert "TEMPLATE-MARKER" in llm.prompts[0]

    def test_both_blocks_reach_every_revision(self, empty_db, fake_settings,
                                              monkeypatch):
        """style-guides.md: '...and into every revision, including the four
        inline AI actions in the rich-text view.'"""
        import src.gemini.client_factory as cf
        from src.data.chat_tools.enablement_tools import _revise_draft_impl
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        conn = empty_db.conn
        store.set_style_guide(conn, "TONE-MARKER")
        store.set_card_template(conn, "TEMPLATE-MARKER")
        did = _new_draft(conn, title="D", content="body")
        llm = StubLLM()
        monkeypatch.setattr(cf, "build_client_for_task", lambda *a, **kw: llm)
        for _label, instruction in RichTextEditor._AI_ACTIONS:
            _revise_draft_impl(conn, did, instruction)
        assert len(llm.prompts) == 4
        assert all("TONE-MARKER" in p and "TEMPLATE-MARKER" in p
                   for p in llm.prompts)

    def test_match_style_guide_is_one_of_the_inline_actions(self):
        """style-guides.md: 'One of those actions is specifically "rewrite this
        to match the style guide".'"""
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        labels = [l for l, _i in RichTextEditor._AI_ACTIONS]
        assert "Match style guide" in labels

    def test_guides_do_not_apply_to_local_uploads(self, empty_db, fake_settings,
                                                  tmp_path):
        """style-guides.md: 'They do not apply to local file uploads ...
        converted by deterministic Python with no model involved.'"""
        conn = empty_db.conn
        store.set_style_guide(conn, "Always start with a haiku.")
        store.set_card_template(conn, "# [Only Heading]")
        p = tmp_path / "note.txt"
        p.write_text("PRICING\n\nTier B moves to usage-based billing.",
                     encoding="utf-8")
        res = EnablementPage._ingest_local_file(conn, str(p))
        draft = store.get_draft(conn, res["draft_id"])
        assert "haiku" not in draft["content"].lower()
        assert "[Only Heading]" not in draft["content"]
        assert "Tier B moves to usage-based billing." in draft["content"]

    def test_setting_a_guide_is_not_retroactive(self, empty_db, fake_settings):
        """style-guides.md: 'Setting a style guide should change the next
        generated or revised card, not existing ones. Nothing is
        retroactive.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="D", content="original body")
        store.set_style_guide(conn, "New tone rules.")
        store.set_card_template(conn, "# [Heading]")
        assert store.get_draft(conn, did)["content"] == "original body"

    def test_default_article_template_ships_with_the_app(self, empty_db,
                                                         fake_settings):
        """style-guides.md: 'The app ships with a default article template,
        used to seed the setting.'"""
        import pathlib
        asset = (pathlib.Path(__file__).resolve().parents[1] / "assets"
                 / "templates" / "support_center_article_template.md")
        assert asset.is_file()
        text = EnablementPage._bundled_card_template_text()
        assert text.strip() and text.lstrip().startswith("#")
        page = Shim(_bundled_card_template_text=EnablementPage._bundled_card_template_text)
        EnablementPage._load_bundled_card_template(page, empty_db.conn)
        assert store.get_card_template(empty_db.conn).strip() == text.strip()


class TestGuideSearch:
    def test_document_search_matches_individual_words(self, empty_db):
        """style-guides.md: 'Searching stored documents now matches your
        individual words, not just the whole phrase, so a multi-word search finds
        a guide whose words are scattered through it.'"""
        conn = empty_db.conn
        store.save_document(conn, source="manual", doc_id="d1",
                            name="[STYLE-GUIDE] Alma voice",
                            full_text="Confident plain language for providers.")
        assert len(store.search_documents(conn, "plain")) == 1
        assert len(store.search_documents(conn, "plain language")) == 1
        assert [d["doc_id"] for d in
                store.search_documents(conn, "confident providers")] == ["d1"]


# ══════════════════════════════════════════════════════════════════════
# editing-by-hand.md — "Editing a draft by hand"
# ══════════════════════════════════════════════════════════════════════

class TestEditingByHand:
    """The auto-commit and frozen-when-pushed claims are settled above
    (TestOverviewWorkspaces / the overview.md tests). These settle the two
    claims unique to this article."""

    def test_chat_drafts_and_workbench_drafts_are_the_same_records(self, empty_db):
        """editing-by-hand.md: 'chat drafts and Workbench drafts are the same
        records in the same store' — a draft Renn creates via the chat tool is
        the row the Workbench's edit path writes back to."""
        from src.data.chat_tools.registry import dispatch_tool
        import json as _json
        conn = empty_db.conn
        out = _json.loads(dispatch_tool(
            "create_card_draft",
            {"title": "Chat-born card", "content": "renn wrote this"}, conn))
        assert out["ok"]
        did = out["draft_id"]
        store.update_draft_content(conn, did, content="operator hand edit")
        assert store.get_draft(conn, did)["content"] == "operator hand edit"

    def test_hand_edit_drops_a_pending_scoped_sign_off(self, empty_db):
        """editing-by-hand.md: 'Editing an approved draft drops the approval
        ... the publish refuses because its scope changed.'"""
        conn = empty_db.conn
        did = _new_draft(conn, title="Approved then edited", content="reviewed bytes")
        fp = store.draft_fingerprint(store.get_draft(conn, did))
        claim = store.record_approval(conn, did, fingerprint=fp)

        released, why = store.approval_open(store.get_draft(conn, did), claim=claim)
        assert released and why == "claimed"

        store.update_draft_content(conn, did, content="edited after approval")
        released, why = store.approval_open(store.get_draft(conn, did), claim=claim)
        assert not released
        assert why == "sign_off_scope_changed"
