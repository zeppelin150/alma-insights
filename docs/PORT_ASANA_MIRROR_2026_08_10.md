# PORT — Asana-Mirror Web Task Panel (WS-D-WEB) → Mac box

**Source of truth: `origin/enablement-content-tabs` @ `0499e52` (pushed 2026-08-10).**
This guide ports the four commits `3946311 → 694e766 → 65ebb29 → 0499e52` as ONE
cumulative change. It is written for a Sonnet-class implementer working on the
Mac tree, which is OLDER than this branch and must NEVER be merged, pulled, or
bulk-overwritten — new files land whole; shared files get the exact surgical
hunks below and nothing else.

## 0. Execution contract (read before touching anything)

1. **NO `git merge`, NO `git pull`, NO `git checkout <branch>` of the whole
   tree, NO copying a whole directory over an existing one.** The only git
   write you may run is the path-scoped checkout in §2, which touches ONLY the
   listed paths (all of them new files on this box, plus the generated
   `dist/index.html`).
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
# SKIP this npm command entirely if this box has no Node toolchain —
# do NOT install Node/npm or run `npm install`/`npm ci` for this port.
# The shipped bundle is prebuilt + checksum-verified; the Python
# suites and the app smoke test are sufficient verification.
```

If the vitest count differs slightly because this box carries a different
web-test baseline, note the number — the post-port expectation in §5 is
"baseline + 96".

Also snapshot the tree before starting:

```bash
git add -A && git stash push -m "pre-asana-mirror-port snapshot" && git stash apply
# (leaves the working tree unchanged but records a recoverable snapshot)
```

## 2. New files — fetch by path (this is NOT a merge)

Every path below does not exist on this box (except `dist/index.html`, which
is a GENERATED artifact and safe to take whole). A path-scoped checkout
writes only these files and touches nothing else:

```bash
git fetch origin enablement-content-tabs
git checkout FETCH_HEAD -- \
  src/data/asana_rich.py \
  src/services/task_vm.py \
  src/services/task_web.py \
  src/ui/web/task_bridge.py \
  src/ui/web/task_host.py \
  migrations/058_task_extras_fields.sql \
  assets/help/plan/task-panel-mirror.md \
  tests/test_asana_rich.py \
  tests/test_task_bridge.py \
  tests/test_task_web_controller.py \
  web/src/task/Activity.jsx \
  web/src/task/AppsRow.jsx \
  web/src/task/Description.jsx \
  web/src/task/FieldGrid.jsx \
  web/src/task/Header.jsx \
  web/src/task/Subtasks.jsx \
  web/src/task/TaskApp.jsx \
  web/src/task/avatars.jsx \
  web/src/task/demo.js \
  web/src/task/shape.js \
  web/src/task/task.css \
  web/src/task/task.test.jsx \
  src/ui/web/dist/index.html
```

`src/ui/web/dist/index.html` is the committed single-file bundle built from
this exact web/src state — taking it whole means Node is NOT required on this
box. If you prefer to rebuild instead: `npm --prefix web run build` AFTER
§3-C, and expect a byte-different but equivalent file.

Verify: `git status` must show ONLY the paths listed above as new/modified.
If anything else changed, STOP and report.

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
# SKIP this npm command entirely if this box has no Node toolchain —
# do NOT install Node/npm or run `npm install`/`npm ci` for this port.
# The shipped bundle is prebuilt + checksum-verified; the Python
# suites and the app smoke test are sufficient verification.
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
# SKIP this npm command entirely if this box has no Node toolchain —
# do NOT install Node/npm or run `npm install`/`npm ci` for this port.
# The shipped bundle is prebuilt + checksum-verified; the Python
# suites and the app smoke test are sufficient verification.
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
