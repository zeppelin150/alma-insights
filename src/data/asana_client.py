"""Asana REST client (urllib, stdlib only — no SDK).

Powers Renn's GID discovery: list the user's workspaces/projects and, per
project, the custom fields with their GIDs and enum-option GIDs — so a
non-technical operator never has to find them by hand.

API: https://app.asana.com/api/1.0, auth via a Personal Access Token
(``Authorization: Bearer <token>``). Key is stored under ``asana_api_key`` in
pat_store (OS keyring).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from urllib.parse import urlencode

logger = logging.getLogger("alma.asana")

_BASE = "https://app.asana.com/api/1.0"
_TIMEOUT = 30

# Fields fetched per task when polling a board for enablement sync. Includes the
# custom-field values (enum + people) the indicator/mapping logic reads, plus the
# task assignee as a fallback.
_TASK_FIELDS = (
    "name,due_on,permalink_url,completed,modified_at,assignee.name,"
    "custom_fields.gid,custom_fields.name,custom_fields.display_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name,"
    "custom_fields.people_value.gid,custom_fields.people_value.name"
)


class AsanaClient:
    """Minimal read client for project / custom-field / enum-option discovery."""

    def __init__(self, api_key: str, timeout: int = _TIMEOUT):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    @classmethod
    def from_store(cls) -> "AsanaClient":
        from src.data.pat_store import load_setting
        return cls(load_setting("asana_api_key", "") or "")

    # ── HTTP ──────────────────────────────────────────────────────
    def _get(self, path: str, params: dict | None = None):
        url = f"{_BASE}{path}"
        if params:
            url += "?" + urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("data")

    def _get_raw(self, path: str, params: dict | None = None) -> dict:
        """Like :meth:`_get` but returns the FULL payload (``data`` +
        ``next_page``) so paginating callers can follow ``next_page.offset``."""
        url = f"{_BASE}{path}"
        if params:
            url += "?" + urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _send(self, method: str, path: str, body: dict):
        """POST/PUT helper. Asana wraps request bodies in {"data": {...}}."""
        url = f"{_BASE}{path}"
        data = json.dumps({"data": body}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("data")

    # ── writes (Renn write-back; gated by the caller) ─────────────
    def create_subtask(self, parent_gid: str, name: str, *,
                       assignee_gid: str | None = None,
                       due_on: str | None = None) -> dict:
        """Create a real subtask under an Asana task.

        ``POST /tasks/{parent_gid}/subtasks`` with {name, assignee?, due_on?}.
        Returns the created subtask (carries its ``gid``).
        """
        body: dict = {"name": name}
        if assignee_gid:
            body["assignee"] = assignee_gid
        if due_on:
            body["due_on"] = due_on
        return self._send("POST", f"/tasks/{parent_gid}/subtasks", body) or {}

    def create_task(self, project_gid: str, name: str, *,
                    notes: str | None = None, due_on: str | None = None) -> dict:
        """Create a new Asana task inside a project.

        **Human-gated by the caller.** ``POST /tasks`` with
        ``{"data": {"name", "projects": [gid], notes?, due_on?}}`` (the
        ``_send`` helper supplies the ``data`` wrapper). Returns the created
        task's gid, name, and permalink (``permalink_url`` may be ``None`` if
        the create response omits it).
        """
        body: dict = {"name": name, "projects": [project_gid]}
        if notes is not None:
            body["notes"] = notes
        if due_on:
            body["due_on"] = due_on
        data = self._send("POST", "/tasks", body) or {}
        return {
            "ok": True,
            "gid": data.get("gid"),
            "name": name,
            "permalink_url": data.get("permalink_url"),
        }

    def add_comment(self, task_gid: str, text: str) -> dict:
        """Post a comment (story) on an Asana task.

        ``POST /tasks/{task_gid}/stories`` with {text}. Returns the story.
        """
        return self._send("POST", f"/tasks/{task_gid}/stories", {"text": text}) or {}

    def update_due_date(self, task_gid: str, due_on: str | None) -> dict:
        """Set/clear an Asana task's due date.

        ``PUT /tasks/{task_gid}`` with {due_on}. Pass an ISO date (YYYY-MM-DD)
        or None to clear. Returns the updated task.
        """
        return self._send("PUT", f"/tasks/{task_gid}", {"due_on": due_on or None}) or {}

    # ── reads ─────────────────────────────────────────────────────
    def test_connection(self) -> tuple[bool, str]:
        """Return (ok, name-or-error). Use to validate a pasted key.

        Also requests ``gid`` so the same call warms the cache for
        :meth:`whoami`; the return contract stays a 2-tuple — three callers
        unpack exactly ``ok, msg``, so do not widen it.
        """
        try:
            me = self._get("/users/me", {"opt_fields": "name,email,gid"})
            return (bool(me), (me.get("name", "") if me else "no data"))
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def whoami(self) -> dict:
        """Current PAT owner ``{gid, name, email}``, or ``{}`` on any error.

        Resolves the operator's Asana GID for the "only mine" task filter (M2).
        Mirrors :meth:`test_connection`'s error handling; never raises.
        """
        try:
            me = self._get("/users/me", {"opt_fields": "name,email,gid"})
            if not me:
                return {}
            return {"gid": me.get("gid", ""), "name": me.get("name", ""),
                    "email": me.get("email", "")}
        except urllib.error.HTTPError:
            return {}
        except Exception:  # noqa: BLE001
            return {}

    def list_workspaces(self) -> list[dict]:
        data = self._get("/workspaces", {"opt_fields": "name", "limit": 100}) or []
        return [{"gid": w["gid"], "name": w.get("name", "")} for w in data]

    def list_projects(self, workspace_gid: str) -> list[dict]:
        data = self._get("/projects", {
            "workspace": workspace_gid, "archived": "false",
            "opt_fields": "name", "limit": 100,
        }) or []
        return [{"gid": p["gid"], "name": p.get("name", "")} for p in data]

    def get_custom_fields(self, project_gid: str) -> list[dict]:
        """Custom fields for a project, with field GIDs + enum-option GIDs."""
        opt = ("custom_field.name,custom_field.resource_subtype,"
               "custom_field.enum_options.name,custom_field.enum_options.enabled")
        data = self._get(f"/projects/{project_gid}/custom_field_settings",
                         {"opt_fields": opt, "limit": 100}) or []
        fields: list[dict] = []
        for setting in data:
            cf = setting.get("custom_field") or {}
            field = {
                "gid": cf.get("gid"),
                "name": cf.get("name", ""),
                "type": cf.get("resource_subtype", ""),
            }
            opts = cf.get("enum_options")
            if opts:
                field["enum_options"] = [
                    {"gid": o["gid"], "name": o.get("name", "")}
                    for o in opts if o.get("enabled", True)
                ]
            fields.append(field)
        return fields

    def list_tasks(self, project_gid: str, *, opt_fields: str = _TASK_FIELDS,
                   modified_since: str | None = None, limit: int = 100) -> list[dict]:
        """Tasks in a project (with custom-field values) for enablement sync.

        ``modified_since`` (ISO 8601) makes the poll incremental — Asana only
        returns tasks modified at/after that time.
        """
        params: dict = {"project": project_gid, "opt_fields": opt_fields, "limit": limit}
        if modified_since:
            params["modified_since"] = modified_since
        return self._get("/tasks", params) or []

    def list_tasks_paged(self, project_gid: str, *, opt_fields: str = _TASK_FIELDS,
                         modified_since: str | None = None, page_size: int = 100,
                         max_pages: int = 10) -> list[dict]:
        """Enumerate ALL tasks in a project, following Asana pagination to the end.

        Asana pages via ``limit`` + an ``offset`` token returned in the response's
        ``next_page.offset``; this follows that cursor up to ``max_pages`` (a hard
        cap so a runaway cursor can't spin). This is the COMPLETE enumeration the
        "what tasks are on the board" tool needs — :meth:`list_tasks` returns only
        the first page.
        """
        out: list[dict] = []
        params: dict = {"project": project_gid, "opt_fields": opt_fields,
                        "limit": page_size}
        if modified_since:
            params["modified_since"] = modified_since
        offset: str | None = None
        for _ in range(max_pages):
            page_params = dict(params)
            if offset:
                page_params["offset"] = offset
            payload = self._get_raw("/tasks", page_params)
            data = payload.get("data") or []
            out.extend(data)
            nxt = payload.get("next_page") or {}
            offset = nxt.get("offset") if isinstance(nxt, dict) else None
            if not offset:
                break
        return out

    def get_task(self, task_gid: str, *,
                 opt_fields: str = "name,due_on,completed,assignee.name,modified_at") -> dict:
        """Fetch a single task's current state (for read-back reconciliation)."""
        return self._get(f"/tasks/{task_gid}", {"opt_fields": opt_fields}) or {}

    def list_subtasks(self, task_gid: str, *, limit: int = 100) -> list[dict]:
        """List an Asana task's subtasks (gid + name + completed) for read-back."""
        data = self._get(f"/tasks/{task_gid}/subtasks",
                         {"opt_fields": "name,completed", "limit": limit}) or []
        return [{"gid": s["gid"], "name": s.get("name", ""),
                 "completed": bool(s.get("completed"))} for s in data]

    def list_attachments(self, task_gid: str, *, limit: int = 100) -> list[dict]:
        """List a task's attachments (gid + name + subtype).

        ``GET /tasks/{task_gid}/attachments``. Returns lightweight rows; call
        ``get_attachment`` for a single attachment's download URL + host.
        """
        data = self._get(f"/tasks/{task_gid}/attachments",
                         {"opt_fields": "name,resource_subtype", "limit": limit}) or []
        return [{"gid": a["gid"], "name": a.get("name", ""),
                 "subtype": a.get("resource_subtype", "")} for a in data]

    def get_attachment(self, attachment_gid: str) -> dict:
        """Fetch one attachment's download URL + host.

        ``GET /attachments/{attachment_gid}``. Uploaded files carry a pre-signed
        ``download_url`` (host ``asana``); external hosts (``google_drive`` etc.)
        carry ``view_url``/``permanent_url`` that the caller resolves separately.
        """
        a = self._get(f"/attachments/{attachment_gid}", {
            "opt_fields": "name,download_url,host,view_url,permanent_url,resource_subtype",
        }) or {}
        return {
            "gid": a.get("gid", attachment_gid),
            "name": a.get("name", ""),
            "download_url": a.get("download_url", ""),
            "host": a.get("host", ""),
            "view_url": a.get("view_url", "") or a.get("permanent_url", ""),
            "subtype": a.get("resource_subtype", ""),
        }

    def list_workspace_users(self, workspace_gid: str) -> list[dict]:
        """Workspace members (gid → name/email) for resolving people fields."""
        data = self._get(f"/workspaces/{workspace_gid}/users",
                         {"opt_fields": "name,email", "limit": 100}) or []
        return [{"gid": u["gid"], "name": u.get("name", ""), "email": u.get("email", "")}
                for u in data]

    def discover(self) -> dict:
        """Workspace + projects + custom fields for the first project.

        Field lookups are limited to the first project to keep setup snappy;
        per-project fields for any other project come via get_custom_fields().
        Shape matches asana_setup.MOCK_DISCOVERY.
        """
        workspaces = self.list_workspaces()
        ws = workspaces[0] if workspaces else None
        projects = self.list_projects(ws["gid"]) if ws else []
        custom_fields: dict[str, list] = {}
        if projects:
            try:
                custom_fields[projects[0]["gid"]] = self.get_custom_fields(projects[0]["gid"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Asana custom-field lookup failed: %s", exc)
                custom_fields[projects[0]["gid"]] = []
        return {"workspace": ws, "projects": projects, "custom_fields": custom_fields}
