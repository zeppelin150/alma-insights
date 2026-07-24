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
