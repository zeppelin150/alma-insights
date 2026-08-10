"""WS-D (pilot-feedback plan) — Asana-parity TaskDetailPanel.

Covers the four WS-D deliverables:
  D1 — DrilldownPanel widget mode hosts embedded panels in a QScrollArea.
  D2 — status pill classification, ALL custom fields render (no 6-field cap),
       rich description (html_notes → markdown → QTextBrowser) with plain-text
       fallback, card-style comment rows, per-subtask assignee/due meta.
  D3 — attachments resolve on click via AsanaClient.get_attachment off the UI
       thread, then webbrowser.open; failure restores the button + shows text.
  D4 — "Updated <relative> · Refresh" freshness row emitting refresh_requested.

Offscreen Qt; no network (attachment resolution is fed a fake client).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton, QScrollArea, QTextBrowser, QWidget,
)

from src.ui.pages.enablement.task_detail import TaskDetailPanel  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _task(**kw):
    t = {"task_id": "t1", "title": "Aetna rate-change escalation",
         "source": "asana", "priority": "high", "assignee": "Renn Ops",
         "status": "in_progress", "due": "Jul 15", "due_iso": "2026-07-15",
         "subtasks": [], "scratch": "", "source_ref": "900100"}
    t.update(kw)
    return t


def _label_texts(panel) -> list[str]:
    return [lbl.text() for lbl in panel.findChildren(QLabel)]


# ── D2: custom fields grid ────────────────────────────────────────────

def test_all_custom_fields_render_uncapped(qapp):
    """Every custom field renders (the old chip row capped at 6 and dropped
    empty values); an unset value shows as an em dash."""
    fields = [{"gid": f"c{i}", "name": f"F{i}", "display_value": f"V{i}"}
              for i in range(9)]
    fields.append({"gid": "c9", "name": "Empty field", "display_value": ""})
    p = TaskDetailPanel(_task(extras={"custom_fields": fields}))
    texts = _label_texts(p)
    for i in range(9):
        assert f"F{i}" in texts and f"V{i}" in texts, f"field {i} missing"
    assert "Empty field" in texts and "—" in texts


# ── D2: description ───────────────────────────────────────────────────

def test_description_renders_markdown_from_html_notes(qapp):
    html = ("<h1>Rollout</h1><p><b>Bold move</b> to "
            "<a href=\"https://example.com/doc\">the doc</a></p>"
            "<ul><li>alpha</li><li>beta</li></ul>")
    p = TaskDetailPanel(_task(extras={"html_notes": html},
                              description="plain fallback ignored"))
    browsers = p.findChildren(QTextBrowser)
    assert len(browsers) == 1
    b = browsers[0]
    assert b.openExternalLinks() is True
    plain = b.toPlainText()
    for expected in ("Rollout", "Bold move", "alpha", "beta"):
        assert expected in plain
    md = b.toMarkdown()
    assert "# Rollout" in md                # heading survived as markdown
    assert "[the doc](https://example.com/doc)" in md   # link survived
    assert "- alpha" in md                  # list survived
    # NOTE: bold/italic MARKS are not asserted — Qt 6.10's setHtml→toMarkdown
    # (html_markdown's GUI path) drops them while keeping the text; the
    # headless fallback parser emits them. Content fidelity is what the
    # panel contract requires.


def test_description_falls_back_to_plain_text(qapp):
    p = TaskDetailPanel(_task(description="Plain body text here."))
    browsers = p.findChildren(QTextBrowser)
    assert len(browsers) == 1
    assert "Plain body text here." in browsers[0].toPlainText()


def test_no_description_section_when_both_sources_empty(qapp):
    p = TaskDetailPanel(_task())
    assert p.findChildren(QTextBrowser) == []


# ── D2: comments ──────────────────────────────────────────────────────

def test_comment_cards_built_from_stories(qapp):
    stories = [
        {"author": "Ann Chen", "text": "First line\nSecond line",
         "created_at": "2026-07-12T09:00:00.000Z"},
        {"author": "", "text": "anon note", "created_at": "bogus-stamp"},
    ]
    p = TaskDetailPanel(_task(extras={"stories": stories}))
    texts = _label_texts(p)
    heads = [t for t in texts if t.startswith("Ann Chen · ")]
    assert heads, "author header row missing"
    assert ("AM" in heads[0]) or ("PM" in heads[0])   # localized h:mm AM/PM
    assert "First line\nSecond line" in texts          # pre-wrap body intact
    assert any(t.startswith("someone") for t in texts)  # empty author fallback
    assert any(t.startswith("COMMENTS (2)") for t in texts)
    # the composer is untouched: asana task still has the comment row
    assert hasattr(p, "_comment_edit")


# ── D2: status pill ───────────────────────────────────────────────────

def test_status_pill_classification(qapp):
    from src.ui.theme import ALMA_INFO, ALMA_SUCCESS, ALMA_WARNING
    cases = [("done", "Done", ALMA_SUCCESS),
             ("in_progress", "In progress", ALMA_INFO),
             ("On hold", "On hold", ALMA_WARNING),
             ("weird", "Weird", None)]
    for status, disp, color in cases:
        p = TaskDetailPanel(_task(status=status))
        pill = p._status_pill_lbl
        assert pill.text() == disp
        if color is not None:
            assert color in pill.styleSheet()
        else:
            assert "#ECEAE5" in pill.styleSheet()   # default gray


# ── D2: subtask meta ──────────────────────────────────────────────────

def test_subtask_meta_renders_for_dict_entries(qapp):
    subs = [{"text": "Draft comms", "done": False,
             "assignee": "Renn Ops", "due": "2026-08-01"},
            ("Old tuple shape", True)]
    p = TaskDetailPanel(_task(subtasks=subs))
    texts = _label_texts(p)
    assert "Draft comms" in texts
    assert "Renn Ops · 2026-08-01" in texts   # assignee · due meta line
    assert "Old tuple shape" in texts          # legacy tuples still render


# ── D3: attachments ───────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, result=None, exc=None):
        self.calls = []
        self._result = result
        self._exc = exc

    def get_attachment(self, gid):
        self.calls.append(gid)
        if self._exc is not None:
            raise self._exc
        return self._result


def _click_and_settle(qapp, panel, btn):
    btn.click()
    for t in list(panel._att_threads):
        t.join(timeout=10)
    qapp.processEvents()


def test_attachment_click_resolves_and_opens(qapp, monkeypatch):
    fake = _FakeClient(result={"gid": "a77", "name": "policy.pdf",
                               "view_url": "https://files.example/p.pdf",
                               "download_url": "", "host": "asana",
                               "subtype": "asana"})
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    p = TaskDetailPanel(
        _task(extras={"attachments": [
            {"gid": "a77", "name": "policy.pdf", "subtype": "asana"}]}),
        client_factory=lambda: fake)
    btn = next(b for b in p.findChildren(QPushButton)
               if b.text() == "policy.pdf ›")
    _click_and_settle(qapp, p, btn)
    assert fake.calls == ["a77"]
    assert opened == ["https://files.example/p.pdf"]
    assert btn.isEnabled() and btn.text() == "policy.pdf ›"   # restored


def test_attachment_failure_restores_button_and_shows_error(qapp, monkeypatch):
    fake = _FakeClient(exc=RuntimeError("HTTP 401: not authorized"))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    p = TaskDetailPanel(
        _task(extras={"attachments": [
            {"gid": "a1", "name": "sheet.xlsx", "subtype": "asana"}]}),
        client_factory=lambda: fake)
    btn = next(b for b in p.findChildren(QPushButton)
               if b.text() == "sheet.xlsx ›")
    _click_and_settle(qapp, p, btn)
    assert opened == []
    assert btn.isEnabled() and btn.text() == "sheet.xlsx ›"
    assert p._att_error.isVisibleTo(p)
    assert "sheet.xlsx" in p._att_error.text()
    assert "HTTP 401" in p._att_error.text()


def test_attachment_refuses_non_web_url(qapp, monkeypatch):
    fake = _FakeClient(result={"gid": "a2", "name": "evil", "host": "asana",
                               "view_url": "file:///C:/Windows/system32",
                               "download_url": "", "subtype": "asana"})
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    p = TaskDetailPanel(
        _task(extras={"attachments": [{"gid": "a2", "name": "evil",
                                       "subtype": "asana"}]}),
        client_factory=lambda: fake)
    btn = next(b for b in p.findChildren(QPushButton) if b.text() == "evil ›")
    _click_and_settle(qapp, p, btn)
    assert opened == []                       # never opened a file: URL
    assert p._att_error.isVisibleTo(p)


# ── D4: freshness row ─────────────────────────────────────────────────

def test_refresh_row_shows_relative_time_and_emits(qapp):
    now_iso = datetime.now(timezone.utc).isoformat()
    p = TaskDetailPanel(_task(extras={"fetched_at": now_iso}))
    assert p._updated_lbl.text() == "Updated just now"
    fired = []
    p.refresh_requested.connect(lambda: fired.append(True))
    p._refresh_btn.click()
    assert fired == [True]
    assert not p._refresh_btn.isEnabled()          # locked while in flight
    assert p._refresh_btn.text() == "Refreshing…"


def test_refresh_row_without_extras_says_not_synced(qapp):
    p = TaskDetailPanel(_task())
    assert p._updated_lbl.text() == "Not synced yet"


def test_non_asana_task_has_no_refresh_row(qapp):
    p = TaskDetailPanel(_task(source="drive"))
    assert not hasattr(p, "_refresh_btn")


# ── D1: drilldown widget-mode scrolling ───────────────────────────────

def test_drilldown_widget_mode_hosts_inside_scroll_area(qapp):
    from src.ui.widgets.drilldown_panel import PANEL_WIDTH, DrilldownPanel
    dp = DrilldownPanel()
    inner = QWidget()
    dp.show_widget("Task", "Asana", inner)
    host = dp._detail_stack.widget(1)
    assert isinstance(host, QScrollArea)
    assert host.widgetResizable()
    assert inner.parent() is host.widget()     # hosted inside the scroll body
    assert PANEL_WIDTH == 440                  # width unchanged (D1)
    assert dp.minimumWidth() == 440 and dp.maximumWidth() == 440
    # detach still works (caller keeps ownership)
    dp._detach_hosted_widget()
    assert inner.parent() is None


def test_drilldown_persistent_widget_mode_never_reparents(qapp):
    """WS-D-WEB blank-panel fix: the web task host is added to the detail
    stack ONCE and survives close/reopen with its parent intact. The old
    show_widget path ran setParent(None) on every close — reparenting a
    QWebEngineView tears down its Chromium compositor and every reopen
    after the first rendered a white view."""
    from src.ui.widgets.drilldown_panel import DrilldownPanel
    dp = DrilldownPanel()
    web_host = QWidget()

    dp.show_persistent_widget("Task", "Asana", web_host)
    stack = dp._detail_stack
    idx = stack.indexOf(web_host)
    assert idx >= 2, "index 1 (the native scroll area) must not shift"
    assert isinstance(stack.widget(1), QScrollArea)
    assert stack.currentIndex() == idx
    assert web_host.parent() is not None

    dp._on_close_finished()                     # the close path
    assert web_host.parent() is not None, "close must NEVER orphan the host"

    dp.show_persistent_widget("Task", "Asana", web_host)
    assert stack.indexOf(web_host) == idx, "added once, reused forever"
    assert stack.currentIndex() == idx
    count = stack.count()
    dp.show_persistent_widget("Task", "Asana", web_host)
    assert stack.count() == count

    # a native panel can still take over (web → native fallback mid-session)
    native = QWidget()
    dp.show_widget("Task", "Asana", native)
    assert stack.currentIndex() == 1
    assert web_host.parent() is not None        # persistent page untouched
    dp._on_close_finished()
    assert native.parent() is None              # native panels still detach


# ── DB round-trip: extras as stored by asana_extras render faithfully ─

def test_extras_db_roundtrip_renders(qapp, empty_db):
    from src.data import asana_extras
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request",
                         title="Round trip", source_ref="g9")

    class _Puller:
        def list_attachments(self, gid):
            return [{"gid": "a1", "name": "sheet.xlsx", "subtype": "asana"}]

        def list_stories(self, gid):
            return [{"author": "Ann", "text": "hi there",
                     "created_at": "2026-08-01T00:00:00.000Z"}]

    payload = {"gid": "g9", "modified_at": "2026-08-01T00:00:00.000Z",
               "html_notes": "<b>DB bold</b> body",
               "custom_fields": [{"gid": "c1", "name": "Urgency",
                                  "display_value": "High"}]}
    asana_extras.upsert_extras(conn, tid, payload, client=_Puller())
    extras = asana_extras.get_extras(conn, tid)
    assert extras is not None

    p = TaskDetailPanel(_task(task_id=tid, extras=extras))
    texts = _label_texts(p)
    assert "Urgency" in texts and "High" in texts
    assert any(t.startswith("Updated ") for t in texts)     # fetched_at row
    assert any(t.startswith("COMMENTS (1)") for t in texts)
    assert "DB bold body" in p.findChildren(QTextBrowser)[0].toPlainText()
    assert any(b.text() == "sheet.xlsx ›" for b in p.findChildren(QPushButton))


def test_freshness_label_paints_transparent(qapp):
    """2026-08-10 report: inside the drilldown scroll host a bare QLabel
    inherits an opaque ancestor background and paints a white box over its
    neighbors. Offscreen grabs are blank, so lock the property itself."""
    p = TaskDetailPanel(_task(extras={"fetched_at": "2026-08-10T12:00:00"}))
    assert "background:transparent" in p._updated_lbl.styleSheet()
