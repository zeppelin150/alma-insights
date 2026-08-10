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
}


class TaskWebController(QObject):
    """Controller half of the #/task web triple (bridge = TaskBridge)."""

    task_data = Signal(str)        # JSON viewmodel (task_vm.build_task_vm)
    status_text = Signal(str)      # plain status line
    action_resolved = Signal(str)  # JSON {action, task_id, ok, error}
    _att_done = Signal(object)     # worker→UI hop for attachment resolution

    def __init__(self, *, write_fn=None, refresh_fn=None, open_url_fn=None,
                 client_factory=None, now_fn=None, parent=None):
        super().__init__(parent)
        self._write_fn = write_fn
        self._refresh_fn = refresh_fn
        self._open_url_fn = open_url_fn
        self._client_factory = client_factory
        self._now_fn = now_fn
        self._task_id = ""
        self._gid = ""
        self._vm = None
        self._links: set = set()
        self._att_gids: set = set()
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
            "refresh": bool(is_asana and gid and self._refresh_fn),
        }
        vm = task_vm.build_task_vm(
            task, connected=is_asana, capabilities=caps, now=self._now())
        self._task_id = vm["task_id"]
        self._gid = gid
        self._vm = vm
        self._links = task_vm.link_registry(vm)
        self._att_gids = {a["gid"] for a in vm["attachments"] if a["gid"]}
        self._inflight = False
        self._emit(self.task_data, vm)

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
        if not (vm.get("capabilities") or {}).get(action if action != "due" else "due"):
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
