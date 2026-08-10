"""task_vm builder contracts + the no-Asana-write AST fence + native parity.

The builders are pure functions of the SAME enriched task dict page.py hands
the native TaskDetailPanel — the parity tests at the bottom feed one dict to
both surfaces and compare what each renders, guarding the deliberate
formatting duplication (src/services must not import src/ui).
"""

import ast
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.services import task_vm  # noqa: E402

NOW = datetime(2026, 8, 10, 12, 0, 0)
FETCHED = datetime(2026, 8, 10, 11, 55, 0, tzinfo=timezone.utc).isoformat()

EXTRAS = {
    "fetched_at": FETCHED,
    "html_notes": (
        '<p><strong>Name</strong></p><p>Dana Whitfield</p>'
        '<script>steal(document.cookie)</script>'
        '<p><a href="https://docs.example.com/outline">the outline</a></p>'),
    "custom_fields": [
        {"gid": "f1", "name": "Request Type", "resource_subtype": "enum",
         "display_value": "Guru: Update",
         "enum_value": {"gid": "e1", "name": "Guru: Update", "color": "green"}},
        {"gid": "f2", "name": "Urgent?", "display_value": "No",
         "enum_value": {"name": "No", "color": "red"}},
        {"gid": "f3", "name": "Audience", "resource_subtype": "multi_enum",
         "display_value": "Care Navigators, Billing",
         "multi_enum_values": [{"name": "Care Navigators", "color": "aqua"},
                               {"name": "Billing", "color": "martian-teal"}]},
        {"gid": "f4", "name": "Requested By", "resource_subtype": "people",
         "display_value": "Dana Whitfield",
         "people_value": [{"gid": "u1", "name": "Dana Whitfield"}]},
        {"gid": "f5", "name": "Go-Live", "resource_subtype": "date",
         "display_value": "2026-10-17", "date_value": {"date": "2026-10-17"}},
        {"gid": "f6", "name": "Effort", "resource_subtype": "number",
         "display_value": "12", "number_value": 12},
        {"gid": "f7", "name": "Reviewed", "resource_subtype": "checkbox",
         "display_value": "true"},
        {"gid": "f8", "name": "Empty text", "resource_subtype": "text",
         "display_value": ""},
        {"gid": "f9", "name": "", "display_value": "nameless — dropped"},
        {"gid": "f10", "name": "Martian", "resource_subtype": "martian",
         "display_value": "?"},
    ],
    "attachments": [
        {"gid": "a1", "name": "#cx thread", "host": "SLACK"},
        {"gid": "a2", "name": "tiers.png", "host": "asana"},
        {"gid": "", "name": "", "host": "asana"},
    ],
    "stories": [
        {"gid": "s1", "text": "added this task to CX Requests",
         "created_at": "2026-07-01T12:00:00Z", "author": "Jordan Avery",
         "subtype": "added_to_project"},
        {"gid": "s2", "text": "see https://x.example/y for context",
         "created_at": "2026-07-02T12:00:00Z", "author": "Dana Whitfield",
         "subtype": "comment_added"},
        {"gid": "s3", "text": "When Task is overdue → Comment on Task",
         "created_at": "2026-07-03T12:00:00Z", "author": "",
         "subtype": "comment_added"},
        {"gid": "s4", "text": "legacy pre-058 row, no subtype key",
         "created_at": "2026-07-04T12:00:00Z", "author": "Priya Nair"},
        {"gid": "s5",
         "html": ('<body>ping <a href="https://app.asana.com/0/profile/9">'
                  '@Priya Nair</a> — see <a href="https://doc.example/z">the doc'
                  '</a><script>evil()</script></body>'),
         "text": "ping @Priya Nair — see the doc",
         "created_at": "2026-07-05T12:00:00Z", "author": "Marcus Lee",
         "subtype": "comment_added"},
    ],
    "task_fields": {
        "start_on": "2026-08-15", "due_on": "2026-09-01", "completed": False,
        "followers": [{"name": "Jordan Avery"}, {"name": "Priya Nair"}],
        "permalink_url": "https://app.asana.com/0/1/900100",
    },
}

TASK = {
    "task_id": "t1", "source": "asana", "source_ref": "900100",
    "title": "BCBSMA copay update [[no value]]", "status": "in_progress",
    "assignee": "Jordan Avery", "due_iso": "2026-09-01",
    "due_date": "2026-09-01",
    "source_url": "https://app.asana.com/0/1/900100",
    "board_names": ["CX Requests"],
    "description": "plain fallback",
    "subtasks": [
        {"text": "Draft card", "done": True, "assignee": "Renn Ops",
         "due": "2026-08-01"},
        ("Legacy tuple", False),
        {"text": "", "done": False},
    ],
    "extras": EXTRAS,
}


def _vm(task=TASK, **kw):
    kw.setdefault("now", NOW)
    return task_vm.build_task_vm(task, **kw)


# ── contract keys: MUST stay in lockstep with web/src/task fixtures ──────

def test_top_level_keys_match_js_contract():
    assert sorted(_vm()) == [
        "attachments", "capabilities", "connected", "demo", "description",
        "fields", "header", "stories", "subtasks", "task_id",
    ]


def test_header_keys_match_js_contract():
    assert sorted(_vm()["header"]) == [
        "assignee", "collaborators", "completed", "completed_on",
        "due_display", "due_iso", "freshness", "overdue", "permalink",
        "projects", "status_pill", "title",
    ]


def test_capabilities_keys_match_js_contract():
    assert sorted(_vm()["capabilities"]) == [
        "comment", "complete", "description", "due", "refresh", "subtask"]


def test_description_markdown_round_trip_for_the_editor():
    desc = _vm()["description"]
    assert sorted(desc) == ["markdown", "srcdoc"]
    assert "Name" in desc["markdown"]           # html→md of the rich body
    assert "<" not in desc["markdown"].replace("<https", "")  # no tags leak
    plain = _vm(dict(TASK, extras={}, description="plain body"))
    assert plain["description"]["markdown"] == "plain body"


# ── header ───────────────────────────────────────────────────────────────

def test_due_range_renders_start_to_due():
    h = _vm()["header"]
    assert h["due_display"] == "Aug 15, 2026 – Sep 1, 2026"
    assert h["due_iso"] == "2026-09-01"
    assert h["overdue"] is False


def test_overdue_when_due_past_and_open():
    task = dict(TASK, due_iso="2026-08-01", due_date="2026-08-01")
    assert _vm(task)["header"]["overdue"] is True


def test_completed_via_status_kills_overdue():
    task = dict(TASK, status="done", due_iso="2026-08-01")
    h = _vm(task)["header"]
    assert h["completed"] is True and h["overdue"] is False


def test_collaborators_exclude_the_assignee():
    h = _vm()["header"]
    assert h["assignee"]["name"] == "Jordan Avery"
    assert [c["name"] for c in h["collaborators"]] == ["Priya Nair"]
    assert h["assignee"]["initials"] == "JA"


def test_freshness_and_not_synced_wording():
    assert _vm()["header"]["freshness"].startswith("Updated ")
    task = dict(TASK, extras={})
    assert _vm(task)["header"]["freshness"] == "Not synced yet"


def test_projects_from_board_names_and_permalink():
    h = _vm()["header"]
    assert h["projects"] == [{"board": "CX Requests", "section": ""}]
    assert h["permalink"] == "https://app.asana.com/0/1/900100"


def test_dash_assignee_becomes_none():
    task = dict(TASK, assignee="—")
    assert _vm(task)["header"]["assignee"] is None


# ── fields: the typed mapping table ──────────────────────────────────────

def test_field_kinds_and_pills():
    fields = {f["name"]: f for f in _vm()["fields"]}
    assert fields["Request Type"]["kind"] == "enum"
    assert fields["Request Type"]["pills"] == [
        {"text": "Guru: Update", "color": "green"}]
    assert fields["Urgent?"]["kind"] == "enum"          # inferred, no subtype
    assert fields["Urgent?"]["pills"][0]["color"] == "red"
    assert fields["Audience"]["kind"] == "multi_enum"
    assert [p["text"] for p in fields["Audience"]["pills"]] == [
        "Care Navigators", "Billing"]
    assert fields["Audience"]["pills"][1]["color"] == ""   # unknown color name
    assert fields["Requested By"]["kind"] == "people"
    assert fields["Requested By"]["people"][0]["initials"] == "DW"
    assert fields["Go-Live"]["kind"] == "date"
    assert fields["Go-Live"]["value"] == "Oct 17, 2026"
    assert fields["Effort"]["kind"] == "number"
    assert fields["Reviewed"]["kind"] == "checkbox"
    assert fields["Reviewed"]["checked"] is True
    assert fields["Empty text"]["value"] == ""
    assert fields["Martian"]["kind"] == "text"           # unknown subtype
    assert "" not in fields                              # nameless dropped


def test_field_value_capped_at_6000():
    extras = {"custom_fields": [
        {"gid": "x", "name": "Big", "resource_subtype": "text",
         "display_value": "y" * 9000}]}
    vm = _vm(dict(TASK, extras=extras))
    assert len(vm["fields"][0]["value"]) == 6000


# ── stories: kind split + tokens ─────────────────────────────────────────

def test_story_kind_split():
    kinds = {s["gid"]: s["kind"] for s in _vm()["stories"]}
    assert kinds == {"s1": "system", "s2": "comment", "s3": "automation",
                     "s4": "comment", "s5": "comment"}


def test_plain_text_autolinks():
    s2 = next(s for s in _vm()["stories"] if s["gid"] == "s2")
    assert {"t": "link", "v": "https://x.example/y",
            "href": "https://x.example/y"} in s2["tokens"]


def test_html_text_yields_mention_and_link_tokens_and_drops_script():
    s5 = next(s for s in _vm()["stories"] if s["gid"] == "s5")
    kinds = [(t["t"], t["v"]) for t in s5["tokens"]]
    assert ("mention", "Priya Nair") in kinds
    assert ("link", "the doc") in kinds
    joined = "".join(t["v"] for t in s5["tokens"])
    assert "evil()" not in joined
    assert "steal" not in joined


def test_story_text_capped_at_6000():
    story = {"gid": "s", "text": "z" * 9000, "subtype": "comment_added",
             "author": "A", "created_at": "2026-07-01T12:00:00Z"}
    vm = task_vm.story_vm(story)
    assert sum(len(t["v"]) for t in vm["tokens"]) == 6000


def test_story_timestamp_formats_and_degrades():
    assert task_vm.fmt_story_ts("not-a-date")[:10] == "not-a-date"[:10]
    out = task_vm.fmt_story_ts("2026-07-05T12:00:00Z")
    assert out.startswith("Jul ") and ("AM" in out or "PM" in out)


# ── description: the ONE html surface, sanitized ─────────────────────────

def test_description_sanitized_srcdoc():
    doc = _vm()["description"]["srcdoc"]
    assert "<strong>Name</strong>" in doc
    assert "the outline" in doc
    assert "<script" not in doc
    assert "steal(" not in doc


def test_description_plain_fallback_escapes():
    task = dict(TASK, extras={}, description="a <script> & b\nc")
    doc = _vm(task)["description"]["srcdoc"]
    assert "<script>" not in doc
    assert "a " in doc and "b" in doc


def test_description_empty_when_no_sources():
    task = dict(TASK, extras={}, description="")
    assert _vm(task)["description"]["srcdoc"] == ""


# ── subtasks / attachments ───────────────────────────────────────────────

def test_subtasks_rich_and_legacy_shapes():
    subs = _vm()["subtasks"]
    assert [s["name"] for s in subs] == ["Draft card", "Legacy tuple"]
    assert subs[0]["done"] is True and subs[0]["promoted"] is True
    assert subs[0]["assignee"] == "Renn Ops"
    assert subs[0]["due"] == "Aug 1"          # same-year short form
    assert subs[1]["promoted"] is False


def test_attachments_require_names_and_lower_hosts():
    atts = _vm()["attachments"]
    assert atts == [{"gid": "a1", "name": "#cx thread", "host": "slack"},
                    {"gid": "a2", "name": "tiers.png", "host": "asana"}]


# ── degradation: no extras at all ────────────────────────────────────────

def test_missing_extras_yields_total_empty_sections():
    vm = _vm({"task_id": "t9", "source": "asana", "title": "Bare"})
    assert vm["fields"] == [] and vm["stories"] == [] and vm["attachments"] == []
    assert vm["header"]["title"] == "Bare"
    assert vm["description"]["srcdoc"] == ""


def test_garbage_input_never_raises():
    for garbage in (None, 7, "x", {"extras": "nope", "subtasks": 3}):
        vm = task_vm.build_task_vm(garbage)
        assert sorted(vm) == sorted(_vm())


# ── link registry ────────────────────────────────────────────────────────

def test_link_registry_collects_permalink_and_token_hrefs():
    vm = _vm()
    links = task_vm.link_registry(vm)
    assert "https://app.asana.com/0/1/900100" in links
    assert "https://doc.example/z" in links
    assert "https://x.example/y" in links
    assert "https://evil.example/" not in links


# ── the AST no-Asana-write fence (mirrors test_guru_scope_removal_guard) ─

_ASANA_WRITE_IDENTIFIERS = {
    "create_task", "create_subtask", "add_comment", "update_due_date",
    "update_task", "_send",
    "set_completed_in_asana", "update_due_in_asana", "post_comment_to_asana",
    "create_subtask_in_asana", "set_subtask_completed_in_asana",
    "update_description_in_asana", "toggle_subtask", "asana_writeback",
}

_WEB_TASK_SURFACES = [
    Path("src/services/task_vm.py"),
    Path("src/services/task_web.py"),
    Path("src/ui/web/task_bridge.py"),
    Path("src/ui/web/task_host.py"),
]


def _identifiers(path: Path) -> set:
    tree = ast.parse(path.read_text("utf-8"))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_web_task_surfaces_reference_no_asana_write():
    """Writes must relay through the injected host lane; the lane NAMES are
    held as strings (AST identifiers don't see them), so any identifier hit
    here means someone wired a direct write path."""
    root = Path(__file__).resolve().parent.parent
    for path in _WEB_TASK_SURFACES:
        hit = _ASANA_WRITE_IDENTIFIERS & _identifiers(root / path)
        assert not hit, (
            f"{path.name} references Asana write path(s) {sorted(hit)} — "
            "web task writes relay ONLY through the injected host lanes")


def test_controller_still_reads_attachments():
    # read-only is not read-nothing
    root = Path(__file__).resolve().parent.parent
    assert "get_attachment" in _identifiers(root / "src/services/task_web.py")


# ── page wiring: construction fallback + one-host reuse ─────────────────

def _page_stub():
    from types import SimpleNamespace
    return SimpleNamespace(
        _run_task_writeback=lambda *a: None,
        _run_task_refresh=lambda *a: None,
        _open_source_url=lambda u: None,
        _open_subtask_from_web=lambda *a: None,
        _set_status=lambda s: None,
        task_action_done=SimpleNamespace(connect=lambda fn: None),
    )


def test_page_marks_session_native_on_construction_failure(monkeypatch):
    import src.ui.web.task_host as th
    from src.ui.pages.enablement.page import EnablementPage

    def boom(**_kw):
        raise RuntimeError("no webengine")

    monkeypatch.setattr(th, "build_task_web_triple", boom)
    stub = _page_stub()
    ctrl, host = EnablementPage._get_task_web_host(stub)
    assert ctrl is None and host is None
    assert stub._task_web_failed is True
    assert EnablementPage._task_web_available(stub) is False


def test_page_builds_the_web_host_once_and_reuses_it(monkeypatch):
    from types import SimpleNamespace
    import src.ui.web.task_host as th
    from src.ui.pages.enablement.page import EnablementPage

    fake_ctrl = SimpleNamespace(
        status_text=SimpleNamespace(connect=lambda _fn: None),
        notify_action_outcome=lambda res: None)
    fake_bridge, fake_host = object(), object()
    calls = []

    def fake(**kw):
        calls.append(kw)
        return fake_ctrl, fake_bridge, fake_host

    monkeypatch.setattr(th, "build_task_web_triple", fake)
    stub = _page_stub()
    first = EnablementPage._get_task_web_host(stub)
    second = EnablementPage._get_task_web_host(stub)
    assert first == (fake_ctrl, fake_host)
    assert second == (fake_ctrl, fake_host)   # same objects — ONE Chromium
    assert len(calls) == 1
    assert stub._task_web_bridge is fake_bridge   # GC guard held


# ── native parity: one dict, two surfaces ────────────────────────────────

@pytest.mark.ui
def test_native_and_web_render_the_same_data():
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication.instance()
    if app is not None and not isinstance(app, QApplication):
        pytest.skip("a non-widget QCoreApplication owns this process")
    app = app or QApplication([])
    from src.ui.pages.enablement.task_detail import TaskDetailPanel

    panel = TaskDetailPanel(dict(TASK))
    labels = [w.text() for w in panel.findChildren(QLabel)]
    vm = _vm()

    # Field names: every field the native grid draws exists in the web vm.
    native_fields = {cf["name"] for cf in EXTRAS["custom_fields"]
                     if (cf.get("name") or "").strip()}
    assert {f["name"] for f in vm["fields"]} == native_fields
    for name in native_fields:
        assert any(name in t for t in labels)

    # Stories: the native panel renders every comment_added story as a card
    # ("someone" for rule-posted ones); the web splits those into comment +
    # ⚡ automation rows. Parity holds over the union — no story is lost.
    native_comments = {s["text"] for s in EXTRAS["stories"]
                       if (s.get("subtype") or "comment_added") == "comment_added"}
    web_comment_like = [s for s in vm["stories"]
                        if s["kind"] in ("comment", "automation")]
    assert len(web_comment_like) == len(native_comments)

    # Attachments + title parity.
    assert any("BCBSMA copay update" in t for t in labels)
    assert {a["name"] for a in vm["attachments"]} == {"#cx thread", "tiers.png"}


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
