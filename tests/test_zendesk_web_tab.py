"""Flag + page integration for the web Zendesk tab (WS6).

Committed tests stay WebEngine-free per the headless convention: the web
branch is exercised with ``WebHost`` monkeypatched at page.py's import site
(``src.ui.web.web_host.WebHost``) — a plain QWidget stub that records its
kwargs — so no QWebEngineView is ever constructed here. The real WebEngine
round-trip lives in the gitignored ``tests/test_zendesk_web_local.py``
(run singly). Flag-OFF native-tab contracts stay owned by
``tests/test_zendesk_content.py`` (which pins the flag off).
"""

import sys

import pytest

pytestmark = pytest.mark.ui


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _stub_host_cls():
    """A QWidget standing in for WebHost: records construction kwargs,
    builds no WebEngine."""
    from PySide6.QtWidgets import QWidget

    class StubHost(QWidget):
        made = []

        def __init__(self, bridge=None, channel_name="almaBridge", route="",
                     accept_drops=False, log_name="alma.web",
                     extra_bridges=None, parent=None):
            super().__init__(parent)
            StubHost.made.append({
                "bridge": bridge, "channel_name": channel_name,
                "route": route, "accept_drops": accept_drops,
                "log_name": log_name,
                "extra_bridges": dict(extra_bridges or {})})

    return StubHost


def _bare_page(pg):
    """An EnablementPage shell for exercising _make_zendesk in isolation
    (skips the heavy __init__ — the calendar local test's pattern)."""
    _qapp()
    inst = pg.EnablementPage.__new__(pg.EnablementPage)
    inst.db = None
    inst.demo = True
    inst._demo_db = None
    inst._scroll = lambda w: w
    # the web branch publishes the shared chat bridge; no engine here → None
    inst._web_chat_bridge = None
    inst._engine = None
    return inst


# ── _make_zendesk flag branches ──────────────────────────────────────

def test_flag_off_returns_native_zendesk_page(monkeypatch):
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    from src.ui.pages.enablement.zendesk_tab import ZendeskPage

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "off")
    inst = _bare_page(pg)
    zd, tab = pg.EnablementPage._make_zendesk(inst)
    assert isinstance(zd, ZendeskPage)
    assert tab is zd     # _scroll stubbed to identity


def test_flag_calendar_keeps_zendesk_native(monkeypatch):
    """Solo calendar rollout must not flip the Zendesk tab."""
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    from src.ui.pages.enablement.zendesk_tab import ZendeskPage

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "calendar")
    inst = _bare_page(pg)
    zd, _tab = pg.EnablementPage._make_zendesk(inst)
    assert isinstance(zd, ZendeskPage)


@pytest.mark.parametrize("mode", ["zendesk", "all"])
def test_flag_on_takes_web_branch(monkeypatch, mode):
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh
    from src.services.zendesk_web import ZendeskWebController
    from src.ui.web.zendesk_bridge import ZendeskBridge

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: mode)
    stub = _stub_host_cls()
    monkeypatch.setattr(wh, "WebHost", stub)
    inst = _bare_page(pg)
    zd, tab = pg.EnablementPage._make_zendesk(inst)

    assert isinstance(zd, ZendeskWebController)
    assert isinstance(tab, stub)
    made = stub.made[-1]
    assert made["channel_name"] == "zendeskBridge"
    assert made["route"] == "/zendesk"
    assert made["log_name"] == "alma.enablement.web.zendesk"
    assert isinstance(made["bridge"], ZendeskBridge)
    # the shared Renn drawer bridge rides the same channel
    assert "almaBridge" in made["extra_bridges"]
    # GC guard: both controller and bridge are pinned as instance attrs
    assert inst._web_zd_ctrl is zd
    assert inst._web_zd_bridge is made["bridge"]


def test_make_zendesk_injects_every_callable_the_bridge_exposes(monkeypatch):
    """SEC-2 regression. ``save_body_edit_fn`` was never passed here, so
    ``ZendeskBridge._save_body_edit_fn`` stayed None and the bridge's
    ``saveBodyEdit`` slot silently did nothing: the ENTIRE specialist
    body-edit feature was a no-op in the shipped app, with no error anywhere
    (the bridge swallows an absent callable by design, which is right for a
    relay and lethal for a wiring omission).

    Enumerate the bridge's injection points from its own signature rather
    than listing them here — a future slot that page.py forgets to wire
    fails CI on the day it is added, not on the day a user reports a dead
    button."""
    import inspect

    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh
    from src.ui.web.zendesk_bridge import ZendeskBridge

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", _stub_host_cls())
    inst = _bare_page(pg)
    ctrl, _tab = pg.EnablementPage._make_zendesk(inst)
    bridge = inst._web_zd_bridge

    params = inspect.signature(ZendeskBridge.__init__).parameters
    fn_params = [n for n in params if n.endswith("_fn")]
    assert len(fn_params) >= 17, "the callable scan broke, not the wiring"
    missing = [n for n in fn_params if getattr(bridge, "_" + n, None) is None]
    assert not missing, f"_make_zendesk never injects: {missing}"
    # the one that shipped broken, named explicitly
    assert bridge._save_body_edit_fn == ctrl.js_save_body_edit

    # and the outbound half: every signal the bridge declares is actually
    # fed by a controller signal (an unconnected one is the same class of
    # silent omission, in the other direction).
    from PySide6.QtCore import Signal
    bridge_signals = [n for n, v in vars(ZendeskBridge).items()
                      if isinstance(v, Signal)]
    assert len(bridge_signals) >= 10
    fired = set()
    for name in bridge_signals:
        getattr(bridge, name).connect(
            lambda _payload, n=name: fired.add(n))
    for name, value in vars(type(ctrl)).items():
        if not isinstance(value, Signal):
            continue
        try:
            getattr(ctrl, name).emit("probe")   # the str-carrying web feed
        except TypeError:
            continue                            # compat signals (int/void)
    assert set(bridge_signals) == fired, (
        f"never relayed: {sorted(set(bridge_signals) - fired)}")


def test_renn_bridge_shares_the_zendesk_channel_e2_structural_note(monkeypatch):
    """E2, pinned as a STRUCTURAL FACT rather than a hope.

    The Zendesk WebHost co-registers the Renn chat bridge on the SAME
    QWebChannel, and ``ChatBridge.send`` is a page-callable ``@Slot(str)``.
    So a page script chooses the exact text Renn receives, and Renn's
    propose tools write those page-chosen bytes into a draft VERBATIM —
    through a Python-side actor ``ZendeskWebController`` never observes.

    That is why the draft-copy gate cannot be authorship-based, and why the
    round-4 provenance ledger was deleted rather than patched. The behaviour
    is intentional (the in-page Renn drawer is the point of M5.5), so this
    test does not forbid it — it records it, next to the clipboard
    regression it forced (see tests/test_zendesk_bridge.py,
    ``test_e2_renn_written_draft_still_demands_a_confirm``).

    If a future change ever DOES isolate the channels, this test failing is
    the signal to re-read that reasoning — not to weaken the confirm."""
    from PySide6.QtCore import QMetaMethod

    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh
    from src.ui.web.chat_bridge import ChatBridge

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    stub = _stub_host_cls()
    monkeypatch.setattr(wh, "WebHost", stub)
    inst = _bare_page(pg)
    ctrl, _tab = pg.EnablementPage._make_zendesk(inst)

    made = stub.made[-1]
    assert "almaBridge" in made["extra_bridges"], (
        "the Renn bridge no longer shares the Zendesk channel — re-read the "
        "E2 reasoning before changing the copy gate")
    # ...and `send` really is page-callable: a QWebChannel exposes @Slot
    # methods to any script on the page, so the page picks Renn's input.
    meta = ChatBridge.staticMetaObject
    slots = {bytes(meta.method(i).name()).decode()
             for i in range(meta.methodCount())
             if meta.method(i).methodType() == QMetaMethod.Slot}
    assert "send" in slots

    # the controller therefore holds NO authorship ledger: the draft-copy
    # confirm is universal (module docstring item 6).
    assert not hasattr(ctrl, "_page_authored")


def test_web_controller_carries_zendesk_page_compat_surface(monkeypatch):
    """page.py's existing wiring (381-388) + _load_zendesk must work
    unchanged against the controller — the full ZendeskPage surface."""
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", _stub_host_cls())
    inst = _bare_page(pg)
    zd, _tab = pg.EnablementPage._make_zendesk(inst)
    for attr in ("sync_requested", "article_selected", "article_saved",
                 "article_push", "macro_selected", "macro_saved",
                 "macro_push", "set_articles", "set_macros",
                 "show_article_draft", "show_macro_draft", "set_status"):
        assert hasattr(zd, attr), attr


# ── the copy confirm actually SHOWS the bytes (D1/F1) ────────────────

LONG_PAYLOAD = ("Action required: re-verify your provider credentials at "
                "https://alma-health-verify.example/sso before Friday. "
                + ("Call 1-555-0142 and provide your SSN. " * 20))


def _copy_dialog(content, *, heading="head", title="Copy draft content?",
                 notes=None):
    """Build the REAL dialog page.py builds for a copy, with a plain QWidget
    as the parent (the builder only uses `self` for parenting)."""
    from PySide6.QtWidgets import QWidget

    import src.ui.pages.enablement.page as pg
    _qapp()
    parent = QWidget()
    dlg = pg.EnablementPage._build_copy_confirm_dialog(
        parent, title, heading, content, notes)
    return parent, dlg


def _text_views(dlg):
    from PySide6.QtWidgets import QPlainTextEdit, QTextEdit
    return dlg.findChildren(QPlainTextEdit) + dlg.findChildren(QTextEdit)


def test_copy_confirm_shows_the_bytes_visibly():
    """D1, at the layer that broke it.

    The controller hands the exact clipboard bytes to the host as `content`.
    They used to land in QMessageBox.setDetailedText — collapsed behind a
    "Show Details…" button nothing in src/ ever expands — so for every
    payload over 400 characters (i.e. every real article body) the dialog
    showed the operator NONE of the bytes it claimed to be showing.

    Now they must be in a widget that is VISIBLE without any further click,
    scrollable, read-only, and big enough to read."""
    from PySide6.QtWidgets import QPlainTextEdit
    assert len(LONG_PAYLOAD) > 400
    parent, dlg = _copy_dialog(LONG_PAYLOAD)
    try:
        views = [v for v in _text_views(dlg)
                 if v.toPlainText() == LONG_PAYLOAD]
        assert views, "the exact bytes are in no text view at all"
        view = views[0]
        assert isinstance(view, QPlainTextEdit)
        assert view.isReadOnly()
        # VISIBLE: shown as soon as the dialog is, not behind a toggle
        assert not view.isHidden() and view.isVisibleTo(dlg)
        # and sized so a meaningful amount is on screen unaided
        assert view.minimumHeight() >= 200
        assert dlg.minimumWidth() >= 600 and dlg.minimumHeight() >= 400
        # the dialog is modal and native — nothing Chromium can reach
        assert dlg.isModal()
    finally:
        dlg.deleteLater()
        parent.deleteLater()


def test_copy_confirm_defaults_to_cancel():
    """A stray Enter must never release bytes: Cancel is the default and the
    focused button, and the accept button explicitly is not."""
    from PySide6.QtWidgets import QDialogButtonBox, QPushButton
    parent, dlg = _copy_dialog("short")
    try:
        buttons = dlg.findChild(QDialogButtonBox)
        assert buttons is not None
        roles = {buttons.buttonRole(b): b for b in buttons.buttons()}
        cancel = roles[QDialogButtonBox.RejectRole]
        approve = roles[QDialogButtonBox.AcceptRole]
        assert isinstance(cancel, QPushButton) and cancel.isDefault()
        assert not approve.isDefault()
        assert "cancel" in cancel.text().lower()
        # every default-return path is a refusal: no button pressed → reject
        assert dlg.result() == 0
    finally:
        dlg.deleteLater()
        parent.deleteLater()


# ── D7/F7: the confirm may never INTERPRET content-derived text ──────

# A row title crafted to destroy the disclosure. Two payloads in one:
# an unterminated HTML comment (everything after it disappears when the text
# is parsed as rich text — including the markup-divergence warning) and a
# styled fake affordance to reassure whoever is left reading.
HOSTILE_TITLE = ('<!--<span style="color:#2f3941">Nothing sensitive here. '
                 "Approved by Security.</span>")


def _hostile_heading():
    """The REAL heading the controller builds for a draft copy whose row
    title is page-written. Both write paths are live: js_save_draft's rename
    allowlist and Renn's propose_article_update(title=…) over the
    co-registered chat bridge."""
    import src.services.zendesk_web as zw
    ctrl = zw.ZendeskWebController()
    title, heading, content, notes = ctrl._copy_confirm_text(
        "article_draft", 7, "body_html", "WIRE 9910-2231", None,
        HOSTILE_TITLE, ["transform:scale(0)"])
    # The report is its OWN value now (G3), so a test that wants to see it
    # in the heading has to render the LEGACY host's shape explicitly.
    return title, zw.ZendeskWebController._with_notes(heading, notes), content


def test_copy_confirm_renders_a_markup_title_literally():
    """D7. The heading QLabel had no textFormat, so it defaulted to
    Qt::AutoText — and Qt::mightBeRichText only scans up to the FIRST
    NEWLINE, which is exactly where _copy_confirm_text interpolates the row
    TITLE. A title starting '<!--' made Qt parse the whole heading as HTML
    and swallow the rest of the disclosure, the markup-divergence warning
    included, on the one surface Python can prove a human perceived.

    The title must appear LITERALLY, and the warning must survive."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QLabel

    title, heading, content = _hostile_heading()
    parent, dlg = _copy_dialog(content, heading=heading, title=title)
    try:
        head = dlg.findChild(QLabel, "zendeskCopyHeading")
        assert head is not None
        # THE FIX: no content-derived string is ever interpreted as markup
        assert head.textFormat() == Qt.PlainText
        # the crafted title is on screen verbatim, tags and all
        assert "<!--" in head.text()
        assert "Approved by Security." in head.text()
        # ...and the disclosure it was built to delete is still there
        assert "EXACT text that will go on the clipboard" in head.text()
        assert "markup the preview does not display faithfully" in head.text()
        assert "transform:scale(0)" in head.text()
        # every other label in this dialog is plain too (a future edit that
        # adds one cannot reintroduce the class)
        for lbl in dlg.findChildren(QLabel):
            assert lbl.textFormat() == Qt.PlainText

        # THE VECTOR WAS REAL: parsed as rich text, this exact heading loses
        # the warning entirely. That is what the operator used to be shown.
        doc = QTextDocument()
        doc.setHtml(heading)
        rendered = doc.toPlainText()
        assert "markup the preview does not display faithfully" not in rendered
        assert "EXACT text that will go on the clipboard" not in rendered
    finally:
        dlg.deleteLater()
        parent.deleteLater()


def test_copy_confirm_title_cannot_forge_the_heading_structure():
    """Even as plain text, a title full of newlines could forge a second
    'Field:' row or scroll the real lines away. Whitespace is collapsed at
    the source, so the heading's line structure is Python's alone."""
    import src.services.zendesk_web as zw
    ctrl = zw.ZendeskWebController()
    forged = ("Invoice\nField: title    Size: 3 characters\n\nThis is SAFE "
              "content.\n" + "\n" * 40)
    _t, heading, _c, _n = ctrl._copy_confirm_text(
        "article_draft", 7, "body_html", "WIRE 9910-2231", None, forged)
    lines = heading.splitlines()
    # the forged rows cannot BECOME rows: they stay inside the quoted title
    assert len([ln for ln in lines if ln.startswith("Field:")]) == 1
    assert lines[0].startswith("article draft 7 — “Invoice")
    assert "This is SAFE content." in lines[0]     # shown, not obeyed
    assert lines[0].endswith("”")
    # and the blank-line flood cannot scroll the real disclosure away
    assert "\n\n\n" not in heading
    # G3: the heading is a FIXED number of lines whatever anyone writes —
    # the markup report used to be appended here and could add three more
    assert len(lines) == zw._CONFIRM_HEADING_LINES


def test_g3_report_renders_in_its_own_widget_not_the_heading():
    """G3, at the layer that has to hold it.

    The markup report used to be joined into the confirm HEADING, and the
    report is attacker-reachable: a page-callable body whose sanitizer report
    carried an embedded newline in a PROPERTY name grew that heading from
    five lines to eight and forged its structure — defeating exactly what the
    row title's whitespace collapse exists to protect.

    Now it arrives as its own argument and lands in its own read-only,
    height-BOUNDED widget: the fixed disclosure lines above it cannot be
    displaced however long or however crafted the report gets."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    import src.services.zendesk_web as zw
    ctrl = zw.ZendeskWebController()
    _t, heading, content, _n = ctrl._copy_confirm_text(
        "article", 101, "body_html", "WIRE 9910-2231", None, "Password reset")
    # a report that WOULD have flooded the old heading
    notes = [f"prop-{i}:value-{i}" for i in range(40)]
    parent, dlg = _copy_dialog(content, heading=heading, notes=notes)
    try:
        head = dlg.findChild(QLabel, "zendeskCopyHeading")
        report = dlg.findChild(QPlainTextEdit, "zendeskCopyNotes")
        bytes_view = dlg.findChild(QPlainTextEdit, "zendeskCopyBytes")
        assert head is not None and report is not None
        # the heading is untouched by the report — every fixed line survives
        assert head.text() == heading
        assert len(head.text().splitlines()) == zw._CONFIRM_HEADING_LINES
        for entry in notes:
            assert entry not in head.text()
        # the report IS shown, in its own plain-text, read-only widget...
        assert report is not bytes_view
        assert report.isReadOnly() and not report.isHidden()
        assert notes[0] in report.toPlainText()
        # ...bounded, so it can never push the bytes view off the dialog
        assert 0 < report.maximumHeight() <= 128
        assert bytes_view.minimumHeight() >= 200
        assert bytes_view.toPlainText() == content
        # every label in this dialog is still literal
        for lbl in dlg.findChildren(QLabel):
            assert lbl.textFormat() == Qt.PlainText
    finally:
        dlg.deleteLater()
        parent.deleteLater()

    # ...and with no report there is no widget at all (no empty scare box)
    parent2, dlg2 = _copy_dialog(content, heading=heading, notes=[])
    try:
        assert dlg2.findChild(QPlainTextEdit, "zendeskCopyNotes") is None
    finally:
        dlg2.deleteLater()
        parent2.deleteLater()


def test_confirm_notes_signature_matches_the_controller_contract():
    """The fourth parameter is checked by NAME, exactly like `content` — a
    host whose fourth argument means something else must fall back to the
    append path rather than being handed the report."""
    import inspect

    import src.services.zendesk_web as zw
    import src.ui.pages.enablement.page as pg

    params = list(inspect.signature(
        pg.EnablementPage._web_zendesk_confirm).parameters)
    assert params[3] in zw._CONFIRM_CONTENT_PARAMS
    assert params[4] in zw._CONFIRM_NOTES_PARAMS

    ok = zw.ZendeskWebController(
        confirm_fn=lambda title, text, content=None, notes=None: True)
    assert ok._confirm_shows_notes() is True
    bad = zw.ZendeskWebController(
        confirm_fn=lambda title, text, content=None, parent=None: True)
    assert bad._confirm_shows_content() is True
    assert bad._confirm_shows_notes() is False


def test_native_dialog_styling_forces_plain_text_on_labels():
    """The same defect class, closed for every native Enablement dialog:
    style_native_dialog (which they all go through) sets Qt::PlainText, so a
    task title / card name / API error interpolated into a QMessageBox is
    displayed, never interpreted."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QMessageBox

    from src.ui.pages.enablement._common import style_native_dialog
    _qapp()
    box = QMessageBox()
    try:
        box.setText('Move “<!--<b>x</b>” to Friday?')
        style_native_dialog(box)
        assert box.textFormat() == Qt.PlainText
        for lbl in box.findChildren(QLabel):
            assert lbl.textFormat() == Qt.PlainText
    finally:
        box.deleteLater()


def test_page_never_routes_copy_content_into_a_collapsed_pane():
    """The structural guard. setDetailedText is Qt's collapsed disclosure;
    it is used NOWHERE in page.py any more, so no future edit can quietly
    put the released bytes back behind a click."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "src" / "ui" / "pages"
           / "enablement" / "page.py").read_text(encoding="utf-8")
    assert ".setDetailedText(" not in src        # never CALLED anywhere
    # ...and the copy branch really is the purpose-built dialog, not a box
    assert "_build_copy_confirm_dialog" in src


def test_confirm_signature_matches_the_controller_content_contract():
    """The controller decides by PARAMETER NAME whether a host can show the
    bytes (a `(title, text, parent=None)` host would otherwise have been
    handed the payload as a parent widget). Bind the two sides."""
    import inspect

    import src.services.zendesk_web as zw
    import src.ui.pages.enablement.page as pg

    params = list(inspect.signature(
        pg.EnablementPage._web_zendesk_confirm).parameters)
    assert params[:3] == ["self", "title", "text"]
    assert params[3] in zw._CONFIRM_CONTENT_PARAMS

    ctrl = zw.ZendeskWebController(
        confirm_fn=lambda title, text, content=None: True)
    assert ctrl._confirm_shows_content() is True
    ctrl_bad = zw.ZendeskWebController(
        confirm_fn=lambda title, text, parent=None: True)
    assert ctrl_bad._confirm_shows_content() is False


# ── failure → native fallback ────────────────────────────────────────

def test_import_failure_falls_back_to_native(monkeypatch):
    """A missing/broken web module (WebEngine absent) lands on the native
    ZendeskPage, never a crash."""
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    from src.ui.pages.enablement.zendesk_tab import ZendeskPage

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    # None in sys.modules makes `import src.services.zendesk_web` raise
    monkeypatch.setitem(sys.modules, "src.services.zendesk_web", None)
    inst = _bare_page(pg)
    zd, _tab = pg.EnablementPage._make_zendesk(inst)
    assert isinstance(zd, ZendeskPage)


def test_construction_failure_falls_back_to_native(monkeypatch):
    """ANY construction failure (here: WebHost raising) falls back to the
    native tab and leaves no half-built web state behind."""
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh
    from src.ui.pages.enablement.zendesk_tab import ZendeskPage

    class BoomHost:
        def __init__(self, *a, **k):
            raise RuntimeError("no WebEngine on this box")

    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", BoomHost)
    inst = _bare_page(pg)
    zd, _tab = pg.EnablementPage._make_zendesk(inst)
    assert isinstance(zd, ZendeskPage)
    assert inst._web_zd_ctrl is None
    assert inst._web_zd_bridge is None


# ── full page with the flag on (stub host, no WebEngine) ─────────────

def test_full_page_flag_on_builds_web_tab(empty_db, monkeypatch):
    """EnablementPage with web_tabs='zendesk': the Zendesk tab widget is the
    (stub) WebHost, the controller sits behind the compat wiring, and the
    boot-time _load_zendesk feed runs clean against it."""
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh
    from src.services.zendesk_web import ZendeskWebController

    _qapp()
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    stub = _stub_host_cls()
    monkeypatch.setattr(wh, "WebHost", stub)
    page = pg.EnablementPage(empty_db, demo=True)

    assert isinstance(page.zendesk, ZendeskWebController)
    assert isinstance(page._tab_widgets["zendesk"], stub)
    # calendar/workbench stayed native under the solo 'zendesk' value
    from src.ui.pages.enablement.calendar import CalendarPage
    from src.ui.pages.enablement.workbench import WorkbenchPage
    assert isinstance(page.calendar, CalendarPage)
    assert isinstance(page.workbench, WorkbenchPage)
    # push signals exist for wiring parity but the controller never emits
    # them — the structural no-live-writes guarantee (locked by WS3 tests)
    assert hasattr(page.zendesk, "article_push")
    assert hasattr(page.zendesk, "macro_push")


def test_full_page_mirror_done_routes_to_controller(empty_db, monkeypatch):
    """zendesk_mirror_done (worker completion) lands on the main-thread slot:
    the controller's claim is released via notify_*_done and the report is
    re-emitted verbatim on pull_resolved / import_resolved."""
    import json

    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh

    _qapp()
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", _stub_host_cls())
    # keep the settings file untouched by the last_pull stamp
    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "update_section", lambda *a, **k: True)
    page = pg.EnablementPage(empty_db, demo=True)

    pulls, imports = [], []
    page.zendesk.pull_resolved.connect(lambda s: pulls.append(json.loads(s)))
    page.zendesk.import_resolved.connect(lambda s: imports.append(json.loads(s)))

    page._on_zendesk_mirror_done(
        {"kind": "pull", "report": {"ok": True, "articles": 3, "macros": 2}})
    assert pulls and pulls[-1]["ok"] is True and pulls[-1]["articles"] == 3

    page._on_zendesk_mirror_done(
        {"kind": "import",
         "report": {"ok": True, "files": [], "totals": {"files": 0}}})
    assert imports and imports[-1]["ok"] is True


# ── fix regressions (2026-07-24 review) ──────────────────────────────

class _InlineThread:
    """threading.Thread stand-in: start() runs the target synchronously so
    the worker lane is deterministic (no event-loop pumping needed)."""

    def __init__(self, target=None, daemon=None, **_kw):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def test_import_folder_lane_end_to_end(empty_db, monkeypatch, tmp_path):
    """C2 regression: 'Import folder…' must expand the picked directory via
    import_folder — the old wiring fed the folder path to import_file, which
    reported 'file not found' for every folder, every time. Drives the REAL
    lane: js_request_import_folder → folder picker → import_runner →
    _run_zendesk_import worker → notify_import_done → import_resolved."""
    import json
    import threading

    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh

    _qapp()
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", _stub_host_cls())
    # Patch the CLASS before construction — the controller binds the picker
    # at _make_zendesk time. The page never opens a native dialog here.
    monkeypatch.setattr(pg.EnablementPage, "_web_zendesk_folder_pick",
                        lambda self: str(tmp_path))
    (tmp_path / "export.json").write_text(json.dumps(
        [{"id": 7101, "title": "Folder article", "body": "<p>from folder</p>"}]),
        encoding="utf-8")

    page = pg.EnablementPage(empty_db, demo=True)
    monkeypatch.setattr(threading, "Thread", _InlineThread)

    reports = []
    page.zendesk.import_resolved.connect(
        lambda s: reports.append(json.loads(s)))
    page.zendesk.js_request_import_folder()

    assert reports, "import_resolved never fired"
    rep = reports[-1]
    assert rep["ok"] is True
    assert rep["totals"]["files"] == 1
    assert rep["totals"]["imported"] == 1
    assert rep["totals"]["errors"] == 0
    assert all("file not found" not in e
               for f in rep["files"] for e in f["errors"])
    # the article really landed in the DB the worker used
    row = page._ensure_demo_db().conn.execute(
        "SELECT title FROM zendesk_articles WHERE article_id=7101").fetchone()
    assert row is not None and row[0] == "Folder article"


def test_import_mixed_files_and_folders_merge(empty_db, monkeypatch, tmp_path):
    """The expanded lane merges folder + plain-file reports; a plain file
    still goes through import_paths untouched."""
    import json
    import threading

    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh

    _qapp()
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", _stub_host_cls())
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(
        [{"id": 7201, "title": "In folder", "body": "<p>a</p>"}]),
        encoding="utf-8")
    lone = tmp_path / "b.json"
    lone.write_text(json.dumps(
        [{"id": 7202, "title": "Lone file", "body": "<p>b</p>"}]),
        encoding="utf-8")

    page = pg.EnablementPage(empty_db, demo=True)
    monkeypatch.setattr(threading, "Thread", _InlineThread)

    reports = []
    page.zendesk.import_resolved.connect(
        lambda s: reports.append(json.loads(s)))
    # InlineThread runs the worker (and the done slot, via the direct
    # same-thread signal) before _run_zendesk_import returns.
    assert page._run_zendesk_import([str(folder), str(lone)]) is True

    assert reports and reports[-1]["ok"] is True
    assert reports[-1]["totals"]["files"] == 2
    assert reports[-1]["totals"]["imported"] == 2
    assert reports[-1]["totals"]["errors"] == 0


def test_demo_pull_does_not_stamp_last_pull(empty_db, monkeypatch):
    """A demo 'pull' only re-seeds the throwaway demo DB — it must never
    persist enablement.zendesk.last_pull into the real settings file. A live
    pull report (no demo marker) still stamps."""
    import src.data.settings_manager as sm
    import src.ui.pages.enablement.page as pg
    import src.ui.web.web_flags as wf
    import src.ui.web.web_host as wh

    _qapp()
    monkeypatch.setattr(wf, "web_tabs_mode", lambda: "zendesk")
    monkeypatch.setattr(wh, "WebHost", _stub_host_cls())
    writes = []
    monkeypatch.setattr(sm, "update_section",
                        lambda *a, **k: writes.append(a) or True)
    page = pg.EnablementPage(empty_db, demo=True)
    writes.clear()      # ignore any construction-time settings writes

    page._on_zendesk_mirror_done(
        {"kind": "pull",
         "report": {"ok": True, "articles": 3, "macros": 2, "demo": True}})
    assert writes == []

    page._on_zendesk_mirror_done(
        {"kind": "pull", "report": {"ok": True, "articles": 3, "macros": 2}})
    assert len(writes) == 1


def test_demo_zendesk_seed_is_idempotent(empty_db):
    """Every demo Pull click re-runs seed_demo_zendesk; the AI draft pair
    must not duplicate (the articles/macros above it are hash-deduped
    upserts already)."""
    from src.data import zendesk_store
    from src.data.enablement_sim import seed_demo_zendesk

    first = seed_demo_zendesk(empty_db.conn)
    second = seed_demo_zendesk(empty_db.conn)
    assert second["article_draft"] == first["article_draft"]
    assert second["macro_draft"] == first["macro_draft"]
    revs = zendesk_store.list_revisions(empty_db.conn)
    assert len([r for r in revs if r["kind"] == "article"]) == 1
    assert len([r for r in revs if r["kind"] == "macro"]) == 1


def test_list_revisions_ceiling_covers_long_audit_trails(empty_db):
    """The Revision Center reads list_revisions' default limit; 100 silently
    truncated long copied/pushed audit trails. The default now covers 500,
    clamped, and explicit smaller limits still apply."""
    from src.data import zendesk_store

    conn = empty_db.conn
    for n in range(120):
        zendesk_store.save_article_draft(
            conn, title=f"Draft {n}", body="b", source_ref=f"t{n}")
    assert len(zendesk_store.list_revisions(conn)) == 120
    assert len(zendesk_store.list_revisions(conn, limit=5)) == 5
    assert len(zendesk_store.list_revisions(conn, limit=10 ** 9)) == 120
    assert len(zendesk_store.list_revisions(conn, limit="bogus")) == 120


def test_renn_prompt_uses_mirror_era_zendesk_guidance():
    """The stale '(Zendesk not connected)' example is unreachable since the
    zendesk search arm was repointed at the local mirror — the prompt must
    steer Renn to the pull/import remedy instead."""
    import src.ui.pages.enablement.page as pg

    prompt = pg.RENN_SYSTEM_PROMPT
    assert "(Zendesk not connected)" not in prompt
    assert "(Guru not connected)" in prompt
    # empty mirror → run the pull or import; only the pull needs credentials
    assert "empty mirror" in prompt
    assert "only the pull needs Zendesk credentials" in prompt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
