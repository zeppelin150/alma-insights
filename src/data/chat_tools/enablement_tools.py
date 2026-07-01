"""Enablement chat tools for the Gemini/MCP path.

Mirrors the Claude-path tools (src/llm/claude_tools.py): let the chat find
locally-stored enablement documents/drafts and query the business Google Drive.
Handlers use the registry signature (conn, args, filters) -> dict.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("alma.enablement.tools")


def handle_search_local_documents(conn, args: dict, filters: dict) -> dict:
    """Search stored enablement documents + card drafts ("look up an old doc")."""
    from src.data import enablement_store as store
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    docs = store.search_documents(conn, query, limit=limit)
    drafts = store.search_drafts(conn, query, limit=limit)
    return {
        "documents": docs, "drafts": drafts,
        "doc_count": len(docs), "draft_count": len(drafts),
    }


def handle_query_business_drive(conn, args: dict, filters: dict) -> dict:
    """Query the connected business Drive (live when configured, else local mirror)."""
    from src.data.drive_query import query_business_drive
    return query_business_drive(conn, args.get("query", ""), limit=int(args.get("limit", 10)))


def handle_asana_discover(conn, args: dict, filters: dict) -> dict:
    """Discover Asana projects + custom-field/enum GIDs (so the user never hunts them)."""
    from src.data.asana_setup import discover
    return discover(project_gid=args.get("project_gid"))


def handle_set_asana_board_config(conn, args: dict, filters: dict) -> dict:
    """Renn's ONLY write — persist an Asana board's resolved GIDs (scoped to monitor_sources)."""
    from src.data.asana_setup import set_asana_board_config
    return set_asana_board_config(
        conn,
        project_gid=args["project_gid"], project_name=args["project_name"],
        indicator_field_gid=args["indicator_field_gid"], indicator_field_name=args["indicator_field_name"],
        indicator_value_gid=args["indicator_value_gid"], indicator_value_name=args["indicator_value_name"],
        priority_field_gid=args.get("priority_field_gid"), assignee_field_gid=args.get("assignee_field_gid"),
    )


# ═══════════════════════════════════════════════════════════════════════
#  Resolver-tool CONTRACT scaffolding (M0 — Renn action channel).
#
#  Resolver tools (request_google_connect, request_drive_picker,
#  request_asana_board_picker, request_guru_publish_picker) run in the MCP
#  subprocess and DO NOT touch Google/Asana/Guru. Each one will:
#    1. mint a request_id server-side (uuid4) + INSERT a chat_action_requests
#       row via _emit_action_request, and
#    2. return ONLY the minimal imperative string below to the model.
#  The actual picker opens in the app off the free-running action poll. The
#  tools themselves are registered in a later milestone; M0 lands the contract
#  + the allowlist + the emit helper so the channel is non-forgeable.
# ═══════════════════════════════════════════════════════════════════════

# Invariant 4 — the non-forgeable allowlist. ONLY these tool names may open a UI
# action; a data-tool result can never forge one (the channel is the table, not
# model-visible text). The picker resolvers plus the M7b WRITE-propose tools.
#
# The three request_* WRITE-propose tools (M7b) are added here too: each opens a
# Confirm/Cancel card via _emit_confirm_write. THERE IS NO DIRECT WRITE TOOL — the
# only path to a non-idempotent live write (create/rename a Guru folder, create an
# Asana task) is a confirm card + an operator click.
RESOLVER_ACTION_TOOLS = frozenset({
    "request_google_connect",
    "request_drive_picker",
    "request_asana_board_picker",
    "request_guru_publish_picker",
    "request_create_guru_folder",
    "request_rename_guru_folder",
    "request_create_asana_task",
})

# Invariant 3 — the ONLY thing a resolver returns to the model. It carries no
# data, just an instruction to stop and wait for the operator's pick; the picker
# never rides result_json/model text, so the human stays in the loop and there's
# no prompt-injection surface.
_RESOLVER_WAIT_MESSAGE = (
    "A picker is open in the app. STOP and wait for the operator to choose; do "
    "not call any Drive/Asana/Guru tool until you receive a [SYSTEM: operator "
    "selected …] message."
)


def _emit_action_request(conn, session_id: str, action_type: str,
                         payload=None) -> str:
    """Mint a request_id + INSERT a chat_action_requests row, then return the
    minimal model-facing wait string (NOT the id, NOT any data).

    This is the single chokepoint every resolver tool calls. ``create_action_request``
    validates that ``payload`` is a tiny non-PHI control dict (invariant 14) and
    mints the server-side ``request_id`` (invariant 4). The return value is the
    fixed imperative string (invariant 3) — the envelope/id stays out of the
    model's context entirely.
    """
    from src.data.chat_action_requests import create_action_request
    # request_id is intentionally discarded here: the model must NOT learn it
    # (invariant 4 — it can't pin/replay a request the operator hasn't resolved).
    create_action_request(conn, session_id, action_type, payload)
    return _RESOLVER_WAIT_MESSAGE


# ═══════════════════════════════════════════════════════════════════════
#  M7b — GATED CONFIRM write channel.
#
#  The model gets NO direct-execute write tool. Each WRITE-propose tool below
#  (request_create_guru_folder / request_rename_guru_folder /
#  request_create_asana_task) composes a human-readable summary and mints ONE
#  confirm_write row via _emit_confirm_write. The app pops a Confirm/Cancel card;
#  only an operator CLICK runs the (non-idempotent, live-API) write. The propose
#  tool returns ONLY the minimal wait string below — never the request_id, never a
#  way to execute. This is the human-in-the-loop gate (invariants 5/13) made
#  unbypassable: the propose tool literally cannot write.
# ═══════════════════════════════════════════════════════════════════════

# Invariant 3 (write variant) — the ONLY thing a WRITE-propose tool returns to the
# model. It tells the model a confirm card is open and that it CANNOT run writes
# itself; it must stop and wait for the [SYSTEM: operator confirmed/cancelled …]
# follow-up. No request_id, no data — keeps the human in the loop, no
# prompt-injection / direct-execute surface.
_CONFIRM_WRITE_WAIT_MESSAGE = (
    "A confirmation card is open in the app — STOP and wait for the operator to "
    "Confirm or Cancel; you cannot run writes directly. You'll get a "
    "[SYSTEM: operator confirmed/cancelled …] message."
)


def _emit_confirm_write(conn, session_id: str, op: str, summary: str,
                        params: dict) -> str:
    """Mint a confirm_write row ({op, summary, params}) and return ONLY the
    minimal model-facing wait string (NOT the id, NOT a way to execute).

    The single chokepoint every WRITE-propose tool calls. ``create_confirm_write``
    mints the server-side ``request_id`` (invariant 4) and INSERTs the row with the
    operational (non-PHI) payload (the scoped invariant-14 extension lives there).
    The return value is the fixed write-wait string (invariant 3) — the model never
    learns the id and has no direct-execute path.
    """
    from src.data.chat_action_requests import create_confirm_write
    # request_id is intentionally discarded: the model must NOT learn it and has
    # NO tool to execute a write — only the operator's click (execute_write) can.
    create_confirm_write(conn, session_id, op, summary, params)
    return _CONFIRM_WRITE_WAIT_MESSAGE


def _active_session_id() -> str | None:
    """The active chat session id, from the host's pointer file.

    Resolver tools run in the MCP subprocess, which has no session context; the
    host page writes the active session id to ``ALMA_CHAT_SESSION_FILE`` (the
    same mechanism job_tools / the MCP server use to tag executions). Falls back
    to ``None`` when unset/unreadable — ``create_action_request`` then raises and
    the resolver surfaces a clear error rather than minting an orphan row.
    """
    import os
    path = os.environ.get("ALMA_CHAT_SESSION_FILE", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


# ── Resolver tools (M2+): action-only, NEVER touch Google/Asana/Guru ──
#
# Each resolver checks any cheap precondition (e.g. whether a GCP OAuth client
# is even available), mints ONE chat_action_requests row via _emit_action_request,
# and returns the fixed wait string. The picker/connect UI then opens in the main
# process off the free-running action poll. Invariant 15: a resolver MUST NOT call
# google_oauth.load_active_credentials()/reconnect() — no tokens cross the
# subprocess boundary; all Google HTTP runs in the main-process controller.


def _request_google_connect_impl(conn, session_id: str | None) -> str:
    """Open the in-chat "Connect Google" card.

    ``have_client`` (whether a GCP OAuth client is configured) is a non-PHI
    control flag (invariant 14) so the card can guide the operator to supply one
    when missing rather than offering a dead button. Computing it here only reads
    local client config — it does NOT load credentials or open a browser.
    """
    from src.data import google_oauth
    sid = session_id or _active_session_id()
    if not sid:
        # No active session → can't route the action back to a chat. Return a
        # plain string (resolver contract: model only ever sees a string).
        return ("No active chat session — open the Agent chat and try again so the "
                "Connect Google card can be shown.")
    return _emit_action_request(
        conn, sid, "google_connect", {"have_client": bool(google_oauth.have_client())})


def handle_request_google_connect(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal wait STRING."""
    return _request_google_connect_impl(conn, _active_session_id())


# ── Drive folder picker (M3): resolver + human-gated by-id writer ────
#
# request_drive_picker is the picker entry point: it mints a drive_folder_picker
# row and returns the wait string; the lazy tree + the resolve round-trip happen
# in the MAIN process (controller.list_drive_folders/resolve_drive_folder). The
# subprocess NEVER touches Drive (invariant 15).
#
# set_drive_folder is the by-id FALLBACK path (the model already knows a folder
# id) AND the human-gate enforcement point (invariant 5): it refuses with
# {needs_picker:True} while a picker is OPEN-but-unresolved for the session, so
# the model can't act on a guessed id and bypass the human pick.


def _request_drive_picker_impl(conn, session_id: str | None) -> str:
    """Open the in-chat Drive folder picker (resolver — no Drive HTTP here).

    Minimal/empty payload (invariant 14): the picker loads its tree later, in the
    main process, into React only. Returns the fixed wait string (invariant 3).
    """
    sid = session_id or _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so the "
                "Drive folder picker can be shown.")
    return _emit_action_request(conn, sid, "drive_folder_picker", {})


def handle_request_drive_picker(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal wait STRING."""
    return _request_drive_picker_impl(conn, _active_session_id())


def _set_drive_folder_impl(conn, folder_id, folder_name=None, drive_id=None) -> dict:
    """Persist an active Drive folder into ``enablement.drive.active_folders``.

    Append-or-replace by id in the list of ``{id, name, drive_id}`` (a re-pick of
    the same id updates its name/drive_id in place). ``set_section`` writes the
    whole section atomically (temp-file rename, no DB transaction) — invariant 11.
    This is the single source of truth for the chosen folder, written BEFORE Renn
    is notified on the resolve path (invariant 7).
    """
    from src.data.settings_manager import get_section, set_section
    fid = str(folder_id or "").strip()
    if not fid:
        return {"ok": False, "error": "folder_id_required"}
    en = get_section("enablement", {}) or {}
    drive = dict(en.get("drive") or {})
    folders = list(drive.get("active_folders") or [])
    entry = {"id": fid,
             "name": (folder_name or None),
             "drive_id": (drive_id or None)}
    folders = [f for f in folders if (f or {}).get("id") != fid]
    folders.append(entry)
    drive["active_folders"] = folders
    en["drive"] = drive
    ok = bool(set_section("enablement", en))
    return {"ok": ok, "folder_id": fid, "count": len(folders)}


def _set_drive_folder_gated(conn, session_id, folder_id, folder_name=None,
                            drive_id=None) -> dict:
    """Human-gate FIRST, then persist (the by-id fallback writer).

    Invariant 5: if an UNRESOLVED action request exists for the session (a picker
    is open but the operator hasn't picked yet), refuse with ``needs_picker`` so
    the model can't set a guessed id out of band and bypass the human pick. Only
    once nothing is pending does the by-id write proceed.
    """
    from src.data.chat_action_requests import has_pending_action
    sid = session_id or _active_session_id()
    if sid and has_pending_action(conn, sid):
        return {"ok": False, "needs_picker": True,
                "message": "A folder picker is open — choose in the app first."}
    return _set_drive_folder_impl(conn, folder_id, folder_name, drive_id)


def handle_set_drive_folder(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Human-gated by-id Drive folder write."""
    return _set_drive_folder_gated(
        conn, _active_session_id(),
        args.get("folder_id"), args.get("folder_name"), args.get("drive_id"))


# ── Asana board picker (M4): resolver + live reads + human-gated writer ──
#
# Mirrors the M3 Drive folder surface, but for Asana over the SHARED PAT (no
# OAuth). request_asana_board_picker mints an asana_board_picker row + returns the
# wait string; the project list + the resolve round-trip happen in the MAIN
# process (controller.list_asana_projects_for_picker / resolve_asana_board).
#
# list_asana_projects / list_asana_tasks are the TEXT read path so Renn can answer
# "what tasks are on the board" without forcing the picker. They run live against
# AsanaClient (stdlib urllib) using the shared PAT from the keyring.
#
# set_asana_board is the by-gid FALLBACK writer AND the human-gate enforcement
# point (invariant 5): it refuses with {needs_picker:True} while a picker is
# OPEN-but-unresolved for the session — the picker uses the controller resolve
# path instead.


def _request_asana_board_picker_impl(conn, session_id: str | None) -> str:
    """Open the in-chat Asana board picker (resolver — no Asana HTTP here).

    Minimal/empty payload (invariant 14): the picker loads its project list later,
    in the main process, into React only. Returns the fixed wait string
    (invariant 3). ('asana_board_picker' is already in RESOLVER_ACTION_TOOLS via
    request_asana_board_picker + ACTION_TYPES.)
    """
    sid = session_id or _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so the "
                "Asana board picker can be shown.")
    return _emit_action_request(conn, sid, "asana_board_picker", {})


def handle_request_asana_board_picker(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal wait STRING."""
    return _request_asana_board_picker_impl(conn, _active_session_id())


def _list_asana_projects_impl(conn) -> dict:
    """Live-list the Asana projects the shared PAT can see (gid + name only).

    The TEXT read path so Renn can name boards without opening the picker. Uses
    AsanaClient.discover() (workspace → projects) over the shared PAT. No PAT →
    ``{ok:False, error:'asana_not_connected'}`` (mirrors the Guru-not-connected
    shape). Returns ``{ok, count, projects:[{gid, name}]}``.
    """
    from src.data.asana_client import AsanaClient
    client = AsanaClient.from_store()
    if not client.api_key:
        return {"ok": False, "error": "asana_not_connected"}
    try:
        disco = client.discover()
    except Exception as exc:  # noqa: BLE001 — surface as a failed read
        return {"ok": False, "error": str(exc)[:160]}
    projects = [{"gid": p.get("gid"), "name": p.get("name", "")}
                for p in (disco.get("projects") or [])]
    return {"ok": True, "count": len(projects), "projects": projects}


def handle_list_asana_projects(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Live project list over the shared PAT."""
    return _list_asana_projects_impl(conn)


# The ONLY task fields Renn ever sees — a deliberate, minimal projection of
# AsanaClient._TASK_FIELDS. We STRIP custom_fields / enum_value / people_value /
# notes: those carry indicator/mapping internals (and free text that could carry
# PHI), none of which "what's on the board" needs. assignee_name flattens the
# nested assignee.name into a plain string (or None).
def _filter_asana_task(task: dict) -> dict:
    """Project one raw Asana task down to {name, due_on, permalink_url,
    assignee_name} ONLY (invariant 13-style minimization). assignee_name =
    task.assignee.name or None; everything else (custom_fields, notes, …) dropped."""
    assignee = task.get("assignee") or {}
    assignee_name = assignee.get("name") if isinstance(assignee, dict) else None
    return {
        "name": task.get("name"),
        "due_on": task.get("due_on"),
        "permalink_url": task.get("permalink_url"),
        "assignee_name": assignee_name,
    }


def _list_asana_tasks_impl(conn, project_gid=None) -> dict:
    """Live-list the tasks on an Asana board — the tool that answers "what tasks
    are on the board".

    ``project_gid`` DEFAULTS to ``enablement.asana.active_board.project_gid`` (the
    board the operator picked/set) when omitted; if neither is available, returns
    ``{ok:False, error:'no_board', message:…}`` so Renn asks the operator to pick
    one first. No PAT → ``{ok:False, error:'asana_not_connected'}``.

    Each task is FILTERED to {name, due_on, permalink_url, assignee_name} only —
    custom_fields / enum_values / people_value / notes are stripped (the board view
    never needs the indicator internals, and stripping them keeps free text out).

    PAGINATES to completion via ``list_tasks_paged`` (follows Asana's offset cursor
    to the end, up to a sane page cap) so the reported ``count`` is the TRUE board
    total, not a single page.
    """
    from src.data.asana_client import AsanaClient
    gid = (project_gid or "").strip() if project_gid else ""
    if not gid:
        from src.data.settings_manager import get_section
        board = ((get_section("enablement", {}) or {}).get("asana") or {}).get("active_board") or {}
        gid = (board.get("project_gid") or "").strip()
    if not gid:
        return {"ok": False, "error": "no_board",
                "message": "No Asana board set — pick one first."}
    client = AsanaClient.from_store()
    if not client.api_key:
        return {"ok": False, "error": "asana_not_connected"}
    try:
        raw = client.list_tasks_paged(gid)
    except Exception as exc:  # noqa: BLE001 — surface as a failed read
        return {"ok": False, "error": str(exc)[:160]}
    tasks = [_filter_asana_task(t) for t in (raw or [])]
    return {"ok": True, "project_gid": gid, "count": len(tasks), "tasks": tasks}


def handle_list_asana_tasks(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Live board task list (filtered)."""
    return _list_asana_tasks_impl(conn, args.get("project_gid"))


def _search_asana_tasks_impl(conn, text, *, project_gid=None) -> dict:
    """SEARCH the tasks on an Asana board by a case-insensitive name substring.

    Lists the (paginated-to-completion) board tasks and filters client-side to
    those whose ``name`` contains ``text`` (Asana's server-side task search is a
    premium endpoint we don't require). ``project_gid`` DEFAULTS to the active
    board (same as ``list_asana_tasks``). Each match is filtered to {name, due_on,
    permalink_url, assignee_name}. Returns
    ``{ok, project_gid, query, count, tasks:[...]}``; degrades identically to
    ``list_asana_tasks`` (no_board / asana_not_connected).
    """
    from src.data.asana_client import AsanaClient
    needle = str(text or "").strip().lower()
    if not needle:
        return {"ok": False, "error": "text_required"}
    gid = (project_gid or "").strip() if project_gid else ""
    if not gid:
        from src.data.settings_manager import get_section
        board = ((get_section("enablement", {}) or {}).get("asana") or {}).get("active_board") or {}
        gid = (board.get("project_gid") or "").strip()
    if not gid:
        return {"ok": False, "error": "no_board",
                "message": "No Asana board set — pick one first."}
    client = AsanaClient.from_store()
    if not client.api_key:
        return {"ok": False, "error": "asana_not_connected"}
    try:
        raw = client.list_tasks_paged(gid)
    except Exception as exc:  # noqa: BLE001 — surface as a failed read
        return {"ok": False, "error": str(exc)[:160]}
    matches = [_filter_asana_task(t) for t in (raw or [])
               if needle in str(t.get("name") or "").lower()]
    return {"ok": True, "project_gid": gid, "query": text,
            "count": len(matches), "tasks": matches}


def handle_search_asana_tasks(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Substring search over board tasks."""
    return _search_asana_tasks_impl(conn, args.get("text", ""),
                                    project_gid=args.get("project_gid"))


def _set_asana_board_impl(conn, project_gid, project_name=None) -> dict:
    """Persist the active Asana board into ``enablement.asana.active_board``.

    Replace-by-gid in a single ``{project_gid, project_name}`` dict (the chat
    selects ONE default board; deep indicator/field config stays in
    monitor_sources via set_asana_board_config). ``set_section`` writes the whole
    section atomically (temp-file rename, no DB transaction) — invariant 11. This
    is the source of truth ``list_asana_tasks`` reads as its default.
    """
    from src.data.settings_manager import get_section, set_section
    gid = str(project_gid or "").strip()
    if not gid:
        return {"ok": False, "error": "project_gid_required"}
    en = get_section("enablement", {}) or {}
    asana = dict(en.get("asana") or {})
    asana["active_board"] = {"project_gid": gid,
                             "project_name": (project_name or None)}
    en["asana"] = asana
    ok = bool(set_section("enablement", en))
    return {"ok": ok, "project_gid": gid}


def _set_asana_board_gated(conn, session_id, project_gid, project_name=None) -> dict:
    """Human-gate FIRST, then persist (the by-gid fallback writer).

    Invariant 5: if an UNRESOLVED action request exists for the session (a picker
    is open but the operator hasn't picked yet), refuse with ``needs_picker`` so
    the model can't set a guessed gid out of band and bypass the human pick. Only
    once nothing is pending does the by-gid write proceed.
    """
    from src.data.chat_action_requests import has_pending_action
    sid = session_id or _active_session_id()
    if sid and has_pending_action(conn, sid):
        return {"ok": False, "needs_picker": True,
                "message": "A board picker is open — choose in the app first."}
    return _set_asana_board_impl(conn, project_gid, project_name)


def handle_set_asana_board(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Human-gated by-gid Asana board write."""
    return _set_asana_board_gated(
        conn, _active_session_id(),
        args.get("project_gid"), args.get("project_name"))


# ── Guru publish-target picker (M5): resolver + human-gated writer ──
#
# Mirrors the M3 Drive / M4 Asana surfaces, but for the Guru publish destination
# (where push_guru_draft lands new cards). request_guru_publish_picker mints a
# guru_publish_picker row + returns the wait string; the TWO-LEVEL list
# (collections → that collection's folders) + the resolve round-trip happen in the
# MAIN process (controller.list_guru_targets / resolve_guru_target). The subprocess
# NEVER touches Guru here — it only mints the row (invariant 15-style: all Guru HTTP
# runs in the main-process controller worker thread, reusing _list_guru_*_impl).
#
# set_guru_publish_target is the by-id FALLBACK writer (the model already knows the
# collection/folder ids) AND the human-gate enforcement point (invariant 5): it
# refuses with {needs_picker:True} while a picker is OPEN-but-unresolved for the
# session, so the model can't set a guessed target out of band and bypass the human
# pick. ``folder_id`` is OPTIONAL — some Guru setups publish at the collection level
# (the home folder), so a collection-only target is valid.


def _request_guru_publish_picker_impl(conn, session_id: str | None) -> str:
    """Open the in-chat Guru publish-target picker (resolver — no Guru HTTP here).

    Minimal/empty payload (invariant 14): the picker loads its collections →
    folders later, in the main process, into React only. Returns the fixed wait
    string (invariant 3). ('guru_publish_picker' is already in
    RESOLVER_ACTION_TOOLS + ACTION_TYPES.)
    """
    sid = session_id or _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so the "
                "Guru publish-target picker can be shown.")
    return _emit_action_request(conn, sid, "guru_publish_picker", {})


def handle_request_guru_publish_picker(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal wait STRING."""
    return _request_guru_publish_picker_impl(conn, _active_session_id())


def _set_guru_publish_target_impl(conn, collection_id, folder_id=None) -> dict:
    """Persist the Guru publish target into ``enablement.guru.publish_collection_id``
    (+ optional ``publish_folder_id``).

    These are exactly the ids ``_push_guru_draft_impl`` already reads as its
    fallback target, so setting them here makes chat-initiated pushes land in the
    operator's chosen collection/folder. ``folder_id`` is optional — when omitted
    the target is the collection (its home folder). ``set_section`` writes the whole
    section atomically (temp-file rename, no DB transaction) — invariant 11.
    """
    from src.data.settings_manager import get_section, set_section
    cid = str(collection_id or "").strip()
    if not cid:
        return {"ok": False, "error": "collection_id_required"}
    fid = str(folder_id or "").strip() or None
    en = get_section("enablement", {}) or {}
    guru = dict(en.get("guru") or {})
    guru["publish_collection_id"] = cid
    guru["publish_folder_id"] = fid
    en["guru"] = guru
    ok = bool(set_section("enablement", en))
    return {"ok": ok, "publish_collection_id": cid, "publish_folder_id": fid}


def _set_guru_publish_target_gated(conn, session_id, collection_id, folder_id=None) -> dict:
    """Human-gate FIRST, then persist (the by-id fallback writer).

    Invariant 5: if an UNRESOLVED action request exists for the session (a picker is
    open but the operator hasn't picked yet), refuse with ``needs_picker`` so the
    model can't set a guessed target out of band and bypass the human pick. Only once
    nothing is pending does the by-id write proceed.
    """
    from src.data.chat_action_requests import has_pending_action
    sid = session_id or _active_session_id()
    if sid and has_pending_action(conn, sid):
        return {"ok": False, "needs_picker": True,
                "message": "A Guru publish-target picker is open — choose in the app first."}
    return _set_guru_publish_target_impl(conn, collection_id, folder_id)


def handle_set_guru_publish_target(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Human-gated by-id Guru publish-target write."""
    return _set_guru_publish_target_gated(
        conn, _active_session_id(),
        args.get("collection_id"), args.get("folder_id"))


# ── Routing report (M6): read-only "what's set up across the 3 touchpoints" ──
#
# get_enablement_routing is the tool Renn calls to report the CURRENT routing —
# the active Drive folder(s), the active Asana board, and the Guru publish target.
# It is READ-ONLY and runs in the MCP SUBPROCESS, so it MUST honor invariant 15:
# it NEVER calls google_oauth.is_active()/load_active_credentials()/reconnect()
# (those RAISE under ALMA_MCP_MODE). Drive connection state is read from SETTINGS
# ONLY — never via the live OAuth session. And per invariant 13, the Drive block
# returns folder IDS + a count + a `configured` flag, NEVER folder NAMES (those
# can mirror patient/case files). Asana board names + Guru collection/folder ids
# are operational metadata and MAY be returned.


def _drive_configured(drive: dict) -> bool:
    """Whether Drive read is CONFIGURED, from settings ONLY (invariant 15).

    Mirrors ``DriveReader.is_configured`` MINUS the live ``google_oauth.is_active()``
    check (which RAISES in the MCP subprocess). The bright line is the saved
    config, not the session's live-credential state:
      * oauth_user  → configured when ``read_enabled`` is on (the operator
        authorized; whether it is reconnected THIS session is a main-process
        concern we deliberately do not probe here).
      * service account → configured when a credentials_path is set AND
        ``read_enabled`` is on.
    """
    if not drive.get("read_enabled"):
        return False
    auth_type = drive.get("auth_type", "service_account")
    if auth_type == "oauth_user":
        return True
    return bool(drive.get("credentials_path"))


def _asana_connected() -> bool:
    """Whether a shared Asana PAT exists (keyring presence — same check the live
    read tools use). Never raises; treats any keyring fault as not-connected."""
    try:
        from src.data.asana_client import AsanaClient
        return bool(AsanaClient.from_store().api_key)
    except Exception:  # noqa: BLE001 — keyring unavailable → not connected
        return False


def _guru_connected() -> bool:
    """Whether Guru creds (email + token) exist — the same presence check the
    Guru read tools use (``GuruClient.load_credentials``). Never raises."""
    try:
        from src.data.guru_client import GuruClient
        email, token = GuruClient.load_credentials()
        return bool(email and token)
    except Exception:  # noqa: BLE001 — keyring unavailable → not connected
        return False


def _get_enablement_routing_impl(conn) -> dict:
    """Report the CURRENT enablement routing across the 3 provider touchpoints.

    Read-only; SETTINGS + keyring-presence ONLY (invariant 15 — no
    ``google_oauth.is_active()``/``load_active_credentials()``/``reconnect()``,
    which raise under ALMA_MCP_MODE). Shapes:

      drive:  {active_folder_ids:[...], count, configured}  — IDS ONLY, no folder
              NAMES (invariant 13; Drive/Shared-Drive names can mirror PHI).
      asana:  {active_board_gid, active_board_name, connected}  — board name is
              operational metadata and MAY be returned.
      guru:   {publish_collection_id, publish_folder_id, connected}  — KB
              collection/folder ids are operational metadata.
    """
    from src.data.settings_manager import get_section
    en = get_section("enablement", {}) or {}
    drive = en.get("drive") or {}
    asana = en.get("asana") or {}
    guru = en.get("guru") or {}

    # Drive — IDS ONLY (invariant 13: never echo a folder NAME here).
    folders = drive.get("active_folders") or []
    folder_ids = [f.get("id") for f in folders
                  if isinstance(f, dict) and f.get("id")]

    board = asana.get("active_board") or {}

    return {
        "ok": True,
        "drive": {
            "active_folder_ids": folder_ids,
            "count": len(folder_ids),
            "configured": _drive_configured(drive),
        },
        "asana": {
            "active_board_gid": board.get("project_gid"),
            "active_board_name": board.get("project_name"),
            "connected": _asana_connected(),
        },
        "guru": {
            "publish_collection_id": guru.get("publish_collection_id"),
            "publish_folder_id": guru.get("publish_folder_id"),
            "connected": _guru_connected(),
        },
    }


def handle_get_enablement_routing(conn, args, session_filters) -> dict:
    """Registry handler (Gemini/MCP path). Read-only routing report (settings only)."""
    return _get_enablement_routing_impl(conn)


# ── WRITE-propose tools (M7b): the ONLY path to a non-idempotent live write ──
#
# Each propose tool composes a human display summary + an operational params
# envelope, mints ONE confirm_write row via _emit_confirm_write, and returns ONLY
# the minimal wait string. THERE IS NO DIRECT WRITE TOOL — the controller's
# execute_write (off a real operator click on the Confirm card) is the only thing
# that runs the live API write. The model can propose; only the human commits.
#
# ── M8 PRE-FLIGHT feasibility (the propose-time gate before the gate) ──
#
# Before minting a confirm_write row, each propose tool runs a CHEAP live
# feasibility read (Guru get_collection / Asana list_projects — same urllib +
# keyring clients the read tools already use off the main thread). If the target
# is clearly unworkable (read-only Guru collection, unknown Asana board), the tool
# returns a PLAIN STEERING STRING and mints NO row — so no doomed Confirm card
# opens; Renn relays the steer and asks for a valid target. The Confirm-gate is
# UNCHANGED: a passing pre-check still only OPENS a card; the operator's click
# still executes. GRACEFUL DEGRADE (invariant): if the feasibility read itself
# ERRORS (network/API fault), we DON'T block — we fall through to opening the card
# (the execute-time guard in create_folder still protects) and log it.


def _guru_client_or_none():
    """A GuruClient from stored creds, or None when Guru isn't connected.

    Used by the M8 pre-flight reads (get_collection). Never raises — a missing
    creds / keyring fault yields None so the caller degrades gracefully.
    """
    try:
        from src.data.guru_client import GuruClient
        email, token = GuruClient.load_credentials()
        if email and token:
            return GuruClient(email, token)
    except Exception:  # noqa: BLE001 — keyring/import fault → treat as not connected
        pass
    return None


def _writable_guru_collections(client) -> list[str]:
    """Best-effort list of writable (non-read_only) collection NAMES, for a steer.

    Returns ``[]`` on any error — the steer just omits the suggestion list.
    """
    try:
        cols = client.list_collections()
    except Exception:  # noqa: BLE001 — best-effort; a failed list just drops the names
        return []
    return [c.get("name") or c.get("id") or "" for c in cols
            if not c.get("read_only")]


def _request_create_guru_folder_impl(conn, session_id, collection_id, title, *,
                                     collection_name=None, parent_folder_id=None,
                                     parent_folder_name=None) -> str:
    """Propose creating a Guru folder → opens a Confirm card. No write here.

    M8 PRE-FLIGHT: before opening the card, read the target collection. If it is
    read-only (Guru-managed), return a plain steer to a writable collection and
    mint NO row. If the collection can't be found, steer likewise. Only a writable
    collection opens the Confirm card (unchanged M7 path). A feasibility-read error
    degrades gracefully — fall through to the card (execute-time guard protects).
    """
    sid = session_id or _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so the "
                "confirmation card can be shown.")
    cid = str(collection_id or "").strip()
    ttl = str(title or "").strip()
    if not cid or not ttl:
        return ("I need both a collection and a folder title to propose creating a "
                "Guru folder.")

    # ── M8 pre-flight: is this collection writable? ──────────────────
    # Only when we're targeting the collection root (no parent_folder_id) does the
    # collection's read-only flag gate folder creation — a parent folder implies an
    # already-writable collection, and mapping a folder back to its collection's
    # read-only state would over-engineer this (per spec, don't).
    if not parent_folder_id:
        client = _guru_client_or_none()
        if client is not None:
            try:
                collection = client.get_collection(cid)
            except Exception:  # noqa: BLE001 — feasibility read failed → DON'T block
                logger.debug("Guru pre-flight get_collection failed for %r; "
                             "falling through to the confirm card", cid)
                collection = None
            if collection is not None:
                if not collection:
                    # Empty dict → collection not found. Steer, mint no row.
                    writable = _writable_guru_collections(client)
                    tail = (f" Your writable collections are: {', '.join(writable)}."
                            if writable else "")
                    return (f"I couldn't find that collection (id {cid}). Which "
                            f"collection should I create the folder in?{tail}")
                if collection.get("readOnly"):
                    nm = collection.get("name") or cid
                    writable = _writable_guru_collections(client)
                    tail = (f" Your writable collections are: {', '.join(writable)}. "
                            "Which should I use?"
                            if writable else " Which writable collection should I use?")
                    return (f'That collection ("{nm}") is read-only (Guru-managed) — '
                            f"folders can only be added to a writable collection.{tail}")

    where = (f'"{parent_folder_name}"' if parent_folder_name
             else f'"{collection_name}"' if collection_name
             else "the selected collection")
    summary = f'Create a new Guru folder "{ttl}" in {where}.'
    params = {"collection_id": cid, "title": ttl,
              "parent_folder_id": (str(parent_folder_id).strip() or None
                                   if parent_folder_id else None)}
    return _emit_confirm_write(conn, sid, "create_guru_folder", summary, params)


def handle_request_create_guru_folder(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal write-wait STRING."""
    return _request_create_guru_folder_impl(
        conn, _active_session_id(),
        args.get("collection_id"), args.get("title"),
        collection_name=args.get("collection_name"),
        parent_folder_id=args.get("parent_folder_id"),
        parent_folder_name=args.get("parent_folder_name"))


def _request_rename_guru_folder_impl(conn, session_id, folder_id, new_title, *,
                                     current_name=None) -> str:
    """Propose renaming a Guru folder → opens a Confirm card. No write here.

    M8 PRE-FLIGHT NOTE: there is intentionally NO cheap folder-existence check
    here. Guru's public API has no ``get_folder`` and ``list_folders`` requires a
    COLLECTION id (which a rename-by-folder-id request doesn't carry), so a
    by-id existence probe isn't available without over-engineering a
    folder→collection scan. Per the M8 spec we KEEP the M7 behavior: the
    execute-time path (rename_folder → PUT /folders/{id}) already surfaces a clear
    API error if the folder is absent. So this stays a straight propose → Confirm
    card; only the required-args check gates it.
    """
    sid = session_id or _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so the "
                "confirmation card can be shown.")
    fid = str(folder_id or "").strip()
    ttl = str(new_title or "").strip()
    if not fid or not ttl:
        return ("I need the folder and a new title to propose renaming a Guru folder.")
    if current_name:
        summary = f'Rename the Guru folder "{current_name}" to "{ttl}".'
    else:
        summary = f'Rename a Guru folder to "{ttl}".'
    params = {"folder_id": fid, "new_title": ttl}
    return _emit_confirm_write(conn, sid, "rename_guru_folder", summary, params)


def handle_request_rename_guru_folder(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal write-wait STRING."""
    return _request_rename_guru_folder_impl(
        conn, _active_session_id(),
        args.get("folder_id"), args.get("new_title"),
        current_name=args.get("current_name"))


def _request_create_asana_task_impl(conn, session_id, project_gid, name, *,
                                    board_name=None, notes=None, due_on=None) -> str:
    """Propose creating an Asana task → opens a Confirm card. No write here.

    M8 PRE-FLIGHT: before opening the card, validate the project_gid is among the
    boards the shared PAT can see (list_asana_projects). If not found, return a
    plain steer naming the available boards and mint NO row. A feasibility-read
    error (or Asana not connected) degrades gracefully — fall through to the card
    (the execute-time create_task guard surfaces any real API error).
    """
    sid = session_id or _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so the "
                "confirmation card can be shown.")
    gid = str(project_gid or "").strip()
    nm = str(name or "").strip()
    if not gid or not nm:
        return ("I need both a board and a task name to propose creating an Asana task.")

    # ── M8 pre-flight: is this board reachable by the shared PAT? ─────
    # Reuse the live project list (same urllib + keyring read path). Only steer on
    # a DEFINITIVE "not among the boards"; if the list can't be read (not
    # connected / API error) we degrade gracefully and open the card.
    disco = _list_asana_projects_impl(conn)
    if disco.get("ok"):
        projects = disco.get("projects") or []
        gids = {str(p.get("gid")) for p in projects}
        if gid not in gids:
            names = [p.get("name") or p.get("gid") or "" for p in projects]
            tail = (f" Your boards are: {', '.join(names)}. Which one?"
                    if names else " Which board should I use?")
            return (f"I can't find that Asana board (gid {gid}).{tail}")

    where = f'"{board_name}"' if board_name else "the selected board"
    due_part = f" (due {due_on})" if due_on else ""
    summary = f'Create an Asana task "{nm}" on {where}{due_part}.'
    params = {"project_gid": gid, "name": nm,
              "notes": (str(notes) if notes else None),
              "due_on": (str(due_on).strip() or None if due_on else None)}
    return _emit_confirm_write(conn, sid, "create_asana_task", summary, params)


def handle_request_create_asana_task(conn, args, session_filters) -> str:
    """Registry handler (Gemini/MCP path). Returns the minimal write-wait STRING."""
    return _request_create_asana_task_impl(
        conn, _active_session_id(),
        args.get("project_gid"), args.get("name"),
        board_name=args.get("board_name"),
        notes=args.get("notes"), due_on=args.get("due_on"))


# ═══════════════════════════════════════════════════════════════════════
#  Workbench action tools (the "give Renn tools" surface).
#
#  Business logic lives in the _*_impl(conn, ...) helpers so BOTH chat paths
#  reuse it: the Gemini/registry handlers below (conn, args, filters) and the
#  Claude handlers in src/llm/claude_tools.py (args, db) call the same impl.
#
#  TRANSACTIONS: a handler must call AT MOST one atomic()-wrapped store/task
#  function and never open its own atomic() — the dispatch paths use a fresh
#  connection (in_transaction is False at entry), so one BEGIN/COMMIT leaves the
#  connection idle for the registry's telemetry write. (See registry.dispatch_tool.)
# ═══════════════════════════════════════════════════════════════════════


def _revise_draft_impl(conn, draft_id, instruction) -> dict:
    from src.data import enablement_store as store
    from src.gemini.client_factory import build_client_for_task
    try:
        did = int(draft_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "draft_id_required"}
    draft = store.get_draft(conn, did)
    if not draft:
        return {"ok": False, "error": "draft_not_found"}
    client = build_client_for_task("enablement_card_gen")
    if client is None:
        return {"ok": False, "error": "no_llm_client"}
    try:
        style_block = store.style_guide_block(conn)
    except Exception:
        style_block = ""
    prompt = (
        "You are revising a Guru knowledge-base card. Apply the requested change "
        "and return the COMPLETE revised card in this exact format:\n"
        "TITLE: <card title>\n---\n<card body in Markdown>\n"
        f"{style_block}\n"
        f"REQUESTED CHANGE:\n{instruction}\n\n"
        f"CURRENT CARD:\nTITLE: {draft.get('title', '')}\n---\n{draft.get('content', '')}\n"
    )
    text = client.generate(prompt)
    title, content = store._parse_card(text, draft.get("title") or "Untitled")
    store.update_draft_content(conn, did, title=title, content=content)
    return {"ok": True, "draft_id": did, "title": title}


def _push_guru_draft_impl(conn, draft_id, collection_id=None, folder_id=None) -> dict:
    from src.data import enablement_store as store
    try:
        did = int(draft_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "draft_id_required"}
    draft = store.get_draft(conn, did)
    if not draft:
        return {"ok": False, "error": "draft_not_found"}
    guru_cfg = {}
    try:
        from src.data.settings_manager import get_section
        guru_cfg = (get_section("enablement", {}) or {}).get("guru") or {}
    except Exception:
        guru_cfg = {}
    if not collection_id:
        # Renn rarely knows the Guru collection — fall back to the operator's
        # configured publish target so chat-initiated pushes land correctly.
        collection_id = guru_cfg.get("publish_collection_id") or None
    if not folder_id:
        folder_id = guru_cfg.get("publish_folder_id") or None
    # ── Approval gate (M5) ──────────────────────────────────────────
    # A draft cannot be published to Guru without a recorded human sign-off.
    # When blocked, we record the intended target so an in-UI approval can
    # complete the push, and return a clear, actionable error.
    if draft.get("require_approval") and not draft.get("approved_at"):
        store.mark_push_requested(conn, did, collection_id, folder_id)
        return {"ok": False, "error": "approval_required", "draft_id": did,
                "message": "This edit needs your sign-off before it publishes to "
                           "Guru — open the Review panel in the Agent to approve it."}
    client = None
    try:
        from src.data.guru_client import GuruClient
        email, token = GuruClient.load_credentials()
        if email and token:
            client = GuruClient(email, token)
    except Exception:  # noqa: BLE001 — no creds → local mark-pushed
        client = None
    return store.publish_draft(conn, did, guru_client=client,
                               collection_id=collection_id, folder_id=folder_id)


def _list_guru_collections_impl(conn) -> dict:
    """List Guru collections so Renn can pick a publish target."""
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    try:
        cols = GuruClient(email, token).list_collections()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    # read_only (from the collection's readOnly flag) flows through so the
    # publish picker can badge Guru-managed collections and the M8 pre-flight can
    # list the writable ones when steering folder creation.
    return {"ok": True, "count": len(cols),
            "collections": [{"id": c["id"], "name": c["name"],
                             "read_only": bool(c.get("read_only"))}
                            for c in cols]}


def _list_guru_folders_impl(conn, collection=None) -> dict:
    """List a Guru collection's folders (sub-folders) so Renn can target one.

    ``collection`` may be a collection id or a collection NAME (resolved here so
    Renn can pass whatever the user said).
    """
    import re
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    client = GuruClient(email, token)
    cid = collection or None
    if collection and not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", str(collection), re.I):
        try:
            cols = client.list_collections()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        match = next((c for c in cols
                      if (c.get("name") or "").lower() == str(collection).lower()), None)
        if not match:
            return {"ok": False, "error": f"collection_not_found: {collection}",
                    "available": [c["name"] for c in cols]}
        cid = match["id"]
    try:
        folders = client.list_folders(cid)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "collection_id": cid, "count": len(folders),
            "folders": [{"id": f["id"], "title": f["title"],
                         "home": f["home"], "items": f["item_count"]}
                        for f in folders]}


def _create_card_draft_impl(conn, title, content) -> dict:
    from src.data import enablement_store as store
    title = (title or "").strip()
    content = (content or "").strip()
    if not title or not content:
        return {"ok": False, "error": "title_and_content_required"}
    did = store.save_card_draft(conn, title=title, content=content)
    return {"ok": True, "draft_id": did, "status": "pending",
            "note": "Draft saved for review. Publish with push_guru_draft."}


def _render_card_preview_impl(conn, draft_id) -> dict:
    from src.data import enablement_store as store
    try:
        did = int(draft_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "draft_id_required"}
    d = store.get_draft(conn, did)
    if not d:
        return {"ok": False, "error": "draft_not_found"}
    return {"ok": True, "draft_id": did, "title": d.get("title"),
            "content": d.get("content"), "status": d.get("status")}


def _draft_subtasks_impl(conn, task_id, items) -> dict:
    from src.data import enablement_tasks as tasks
    if isinstance(items, str):
        items = [s.strip() for s in items.splitlines() if s.strip()]
    items = [str(s) for s in (items or []) if str(s).strip()]
    if not task_id or not items:
        return {"ok": False, "error": "task_id_and_items_required"}
    ids = tasks.draft_subtasks(conn, str(task_id), items)
    return {"ok": True, "task_id": str(task_id), "added": len(ids), "subtask_ids": ids}


def _add_subtask_impl(conn, task_id, text) -> dict:
    from src.data import enablement_tasks as tasks
    if not task_id or not str(text).strip():
        return {"ok": False, "error": "task_id_and_text_required"}
    sid = tasks.add_subtask(conn, str(task_id), str(text))
    return {"ok": True, "subtask_id": sid}


def _toggle_subtask_impl(conn, subtask_id, done) -> dict:
    from src.data import enablement_tasks as tasks
    if not subtask_id:
        return {"ok": False, "error": "subtask_id_required"}
    return {"ok": tasks.toggle_subtask(conn, str(subtask_id), bool(done))}


def _update_scratchpad_impl(conn, task_id, text) -> dict:
    from src.data import enablement_tasks as tasks
    if not task_id:
        return {"ok": False, "error": "task_id_required"}
    return {"ok": tasks.set_scratchpad(conn, str(task_id), str(text or ""))}


def _create_asana_subtask_impl(conn, task_id, text) -> dict:
    """Add a subtask AND create it back in Asana under the parent task."""
    from src.data import asana_writeback as awb
    if not task_id:
        return {"ok": False, "error": "task_id_required"}
    return awb.create_subtask_in_asana(conn, str(task_id), text)


def _post_asana_comment_impl(conn, task_id, text) -> dict:
    """Post a comment back to the linked Asana task."""
    from src.data import asana_writeback as awb
    if not task_id:
        return {"ok": False, "error": "task_id_required"}
    return awb.post_comment_to_asana(conn, str(task_id), text)


def _update_asana_due_date_impl(conn, task_id, due_on) -> dict:
    """Update the due date locally and push it to the linked Asana task."""
    from src.data import asana_writeback as awb
    if not task_id:
        return {"ok": False, "error": "task_id_required"}
    return awb.update_due_in_asana(conn, str(task_id), due_on)


def _create_task_impl(conn, **kw) -> dict:
    from src.data import enablement_tasks as tasks
    title = kw.get("title")
    if not title:
        return {"ok": False, "error": "title_required"}
    tid = tasks.create_task(
        conn,
        source=kw.get("source", "manual"),
        kind=kw.get("kind", "request"),
        title=title,
        source_ref=kw.get("source_ref"),
        source_url=kw.get("source_url"),
        summary=kw.get("summary"),
        due_date=kw.get("due_date"),
        priority=kw.get("priority", "normal"),
        status=kw.get("status", "open"),
        created_by=kw.get("created_by", "agent"),
    )
    return {"ok": True, "task_id": tid}


def _update_task_impl(conn, task_id, fields) -> dict:
    from src.data import enablement_tasks as tasks
    if not task_id:
        return {"ok": False, "error": "task_id_required"}
    # Distinguish a missing task from a valid-but-no-op update — a bare
    # {"ok": False} hid both (the task could not be found OR no field changed).
    if tasks.get_task(conn, str(task_id)) is None:
        return {"ok": False, "error": "task_not_found", "task_id": str(task_id)}
    if not tasks.update_task(conn, str(task_id), **(fields or {})):
        return {"ok": False, "error": "no_updatable_fields", "task_id": str(task_id)}
    return {"ok": True, "task_id": str(task_id)}


def _list_tasks_impl(conn, *, status=None, source=None, kind=None, due_before=None, limit=50) -> dict:
    from src.data import enablement_tasks as tasks
    rows = tasks.list_tasks(conn, source=source, status=status, kind=kind,
                            due_before=due_before, limit=int(limit or 50))
    return {"tasks": rows, "count": len(rows)}


def _search_drive_docs_impl(conn, query, limit=10) -> dict:
    from src.data import enablement_store as store
    docs = store.search_documents(conn, query or "", limit=int(limit or 10))
    return {"documents": docs, "count": len(docs)}


def _get_drive_doc_impl(conn, doc_id) -> dict:
    from src.data import enablement_store as store
    if not doc_id:
        return {"ok": False, "error": "doc_id_required"}
    d = store.get_document(conn, str(doc_id))
    if not d:
        return {"ok": False, "error": "document_not_found"}
    return {"ok": True, "document": d}


def _list_style_guides_impl(conn) -> dict:
    """List the operator's stored style guides (tagged docs), active flagged."""
    from src.data import enablement_store as store
    guides = store.list_style_guides(conn)
    return {"ok": True, "count": len(guides), "style_guides": guides}


def _get_style_guide_impl(conn) -> dict:
    """Return the ACTIVE style-guide text so a card can be written to follow it."""
    from src.data import enablement_store as store
    text = store.get_style_guide(conn)
    return {"ok": True, "has_style_guide": bool(text.strip()),
            "chars": len(text), "style_guide": text}


def _set_active_style_guide_impl(conn, doc_id) -> dict:
    """Switch the active style guide (the one injected into card-gen/revise)."""
    from src.data import enablement_store as store
    if not doc_id:
        return {"ok": False, "error": "doc_id_required"}
    if store.set_active_style_guide(conn, str(doc_id)):
        return {"ok": True, "active_doc_id": str(doc_id)}
    return {"ok": False, "error": "style_guide_not_found"}


def _run_monitor_now_impl(conn, source=None) -> dict:
    """Run a one-off poll of the configured monitors (module-level poll_once so
    it works from the separate MCP-server process). Degrades gracefully until the
    monitors land (Phases 4/5)."""
    ran, errors = {}, {}
    targets = (source,) if source else ("asana", "drive")
    for name in targets:
        try:
            mod = __import__(f"src.data.{name}_monitor", fromlist=["poll_once"])
            res = mod.poll_once(conn)
            ran[name] = len(res) if isinstance(res, list) else res
        except ImportError:
            errors[name] = "not_available"
        except Exception as exc:  # noqa: BLE001 — surface per-source
            errors[name] = str(exc)
    return {"ran": ran, "errors": errors}


# ── Gemini/registry-path handlers (conn, args, filters) → dict ───────

def handle_revise_draft(conn, args, filters):
    return _revise_draft_impl(conn, args.get("draft_id"), args.get("instruction", ""))


def handle_push_guru_draft(conn, args, filters):
    return _push_guru_draft_impl(conn, args.get("draft_id"),
                                 args.get("collection_id"), args.get("folder_id"))


def handle_list_guru_collections(conn, args, filters):
    return _list_guru_collections_impl(conn)


def handle_list_guru_folders(conn, args, filters):
    return _list_guru_folders_impl(conn, args.get("collection"))


def handle_create_card_draft(conn, args, filters):
    return _create_card_draft_impl(conn, args.get("title", ""), args.get("content", ""))


def handle_render_card_preview(conn, args, filters):
    return _render_card_preview_impl(conn, args.get("draft_id"))


def handle_draft_subtasks(conn, args, filters):
    return _draft_subtasks_impl(conn, args.get("task_id"), args.get("items"))


def handle_add_subtask(conn, args, filters):
    return _add_subtask_impl(conn, args.get("task_id"), args.get("text", ""))


def handle_toggle_subtask(conn, args, filters):
    return _toggle_subtask_impl(conn, args.get("subtask_id"), args.get("done", True))


def handle_update_scratchpad(conn, args, filters):
    return _update_scratchpad_impl(conn, args.get("task_id"), args.get("text", ""))


def handle_create_asana_subtask(conn, args, filters):
    return _create_asana_subtask_impl(conn, args.get("task_id"), args.get("text", ""))


def handle_post_asana_comment(conn, args, filters):
    return _post_asana_comment_impl(conn, args.get("task_id"), args.get("text", ""))


def handle_update_asana_due_date(conn, args, filters):
    return _update_asana_due_date_impl(conn, args.get("task_id"), args.get("due_on"))


def handle_create_task(conn, args, filters):
    return _create_task_impl(conn, **args)


def handle_update_task(conn, args, filters):
    fields = {k: v for k, v in args.items() if k != "task_id"}
    return _update_task_impl(conn, args.get("task_id"), fields)


def handle_list_tasks(conn, args, filters):
    return _list_tasks_impl(conn, status=args.get("status"), source=args.get("source"),
                            kind=args.get("kind"), due_before=args.get("due_before"),
                            limit=args.get("limit", 50))


def handle_search_drive_docs(conn, args, filters):
    return _search_drive_docs_impl(conn, args.get("query", ""), args.get("limit", 10))


def handle_get_drive_doc(conn, args, filters):
    return _get_drive_doc_impl(conn, args.get("doc_id"))


def handle_list_style_guides(conn, args, filters):
    return _list_style_guides_impl(conn)


def handle_get_style_guide(conn, args, filters):
    return _get_style_guide_impl(conn)


def handle_set_active_style_guide(conn, args, filters):
    return _set_active_style_guide_impl(conn, args.get("doc_id"))


def handle_run_monitor_now(conn, args, filters):
    return _run_monitor_now_impl(conn, args.get("source"))


# ── Guru analytics tools (P7 redesign) ───────────────────────────────

def _import_guru_card_impl(conn, card_ref) -> dict:
    from src.data import enablement_store as store
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    return store.import_guru_card_to_draft(
        conn, GuruClient(email, token), str(card_ref or "")
    )


def handle_import_guru_card(conn, args, session_filters) -> dict:
    return _import_guru_card_impl(conn, args.get("card_ref", ""))


def _get_guru_analytics_impl(conn, metric, days=30) -> dict:
    from src.data import guru_analytics as ga
    try:
        d = int(days or 30)
    except (TypeError, ValueError):
        d = 30
    metric = (metric or "top_cards").strip()
    if metric == "top_cards":
        return {"ok": True, "metric": metric, "rows": ga.top_cards(conn, days=d)}
    if metric == "verification":
        return {"ok": True, "metric": metric, "kpis": ga.verification_kpis(conn)}
    if metric == "comments":
        return {"ok": True, "metric": metric, "rows": ga.open_comments(conn)}
    if metric == "due_cards":
        return {"ok": True, "metric": metric,
                "rows": ga.cards_due_for_update(conn, days=d)}
    return {"ok": False, "error": f"unknown_metric: {metric}",
            "valid": ["top_cards", "verification", "comments", "due_cards"]}


def handle_get_guru_analytics(conn, args, session_filters) -> dict:
    return _get_guru_analytics_impl(conn, args.get("metric"), args.get("days", 30))


def _create_task_from_comment_impl(conn, comment_id) -> dict:
    from src.data import guru_analytics as ga
    return ga.create_task_from_comment(conn, str(comment_id or ""))


def handle_create_task_from_comment(conn, args, session_filters) -> dict:
    return _create_task_from_comment_impl(conn, args.get("comment_id", ""))


# ── Content-update pipeline (review a doc -> update an existing card) ──

def _update_card_from_doc_impl(conn, *, task_id=None, doc_ref=None, doc_query=None,
                               card_ref=None, card_name=None, search=None,
                               collections=None) -> dict:
    """Run the content-update pipeline: read a source doc, find the existing
    Guru card, identify what changed, write the update, and STAGE a draft.

    Staging only — publishing stays a separate, human-gated push_guru_draft.
    The LLM stages use the current provider routing (model-agnostic). A
    ``task_id`` may carry the source/target as a JSON hint in its scratchpad.
    """
    import json
    from src.data import enablement_store as store
    from src.data import enablement_tasks as tasks
    from src.data.content_update import ContentUpdateRequest, Deps, run_content_update
    from src.data.guru_client import GuruClient
    from src.gemini.client_factory import build_client_for_task

    # A task can carry the source doc + target card as a scratchpad JSON hint.
    if task_id:
        t = tasks.get_task(conn, str(task_id))
        if t:
            try:
                hint = json.loads(t.get("scratchpad") or "{}")
            except (ValueError, TypeError):
                hint = {}
            doc_ref = doc_ref or hint.get("source_doc_ref")
            doc_query = doc_query or hint.get("source_doc_query")
            card_ref = card_ref or hint.get("target_card_ref")
            card_name = card_name or hint.get("target_card_name")
            search = search or hint.get("search_query")

    source_doc_ref = doc_ref
    if not source_doc_ref and doc_query:
        docs = store.search_documents(conn, doc_query, limit=1)
        if docs:
            source_doc_ref = docs[0].get("doc_id")
        else:
            # A query WAS given but matched nothing — distinct from "no doc param".
            return {"ok": False,
                    "error": f"no_matching_doc: no stored document matched query '{doc_query}'"}
    if not source_doc_ref:
        return {"ok": False, "error": "source_doc_required: pass doc_ref, doc_query, or a task_id whose scratchpad names one"}

    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    llm = build_client_for_task("enablement_card_update")
    if llm is None:
        return {"ok": False, "error": "no_llm_client"}

    req = ContentUpdateRequest(
        source_doc_ref=source_doc_ref,
        target_card_ref=card_ref,
        target_card_name=card_name,
        search_query=search,
        collections=collections or [],
    )
    res = run_content_update(conn, req,
                             Deps(llm_client=llm, guru_client=GuruClient(email, token)))

    out = {"ok": res.ok, "status": res.status, "stage": res.stage,
           "draft_id": res.draft_id, "card_id": res.card_id}
    if res.error:
        out["error"] = res.error
    if res.status == "ambiguous_card":
        out["candidates"] = [{"card_id": c.card_id, "title": c.title, "score": c.score}
                             for c in res.candidates]
        out["note"] = "Several cards matched — ask the user which to update, then call again with card_ref."
    if res.plan:
        out["summary"] = res.plan.summary
        out["changes"] = [{"type": c.type, "section": c.section, "reason": c.reason}
                          for c in res.plan.changes]
    if res.issues:
        out["issues"] = res.issues
    if res.diff:
        out["diff"] = res.diff[:1500]
    if res.ok:
        out["note"] = (f"Draft {res.draft_id} staged (links Guru card {res.card_id}). "
                       "Show the summary + diff to the user; publish with "
                       "push_guru_draft once they approve.")
    return out


def handle_update_card_from_doc(conn, args, session_filters) -> dict:
    return _update_card_from_doc_impl(
        conn,
        task_id=args.get("task_id"),
        doc_ref=args.get("doc_ref"),
        doc_query=args.get("doc_query"),
        card_ref=args.get("card_ref"),
        card_name=args.get("card_name"),
        search=args.get("search"),
        collections=args.get("collections"),
    )


# ── Search + research (live Guru search + cross-source reference gathering) ──

def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _card_url(card_id) -> str:
    return f"https://app.getguru.com/card/{card_id}" if card_id else ""


_DOC_STOPWORDS = frozenset({
    "the", "and", "for", "with", "what", "show", "topic", "our", "from",
    "that", "this", "have", "already", "about", "can", "you", "see", "look",
})


def _search_documents_multi(conn, topic, *, limit=5) -> list[dict]:
    """Recall-friendly doc search: the whole phrase PLUS each significant word,
    unioned and ranked by how many terms hit. Fixes the substring-only
    ``search_documents`` missing e.g. 'Aetna copay telehealth' for a doc named
    'Aetna Copay Policy Update — Telehealth Waiver'.
    """
    import re
    from src.data import enablement_store as store
    phrase = (topic or "").strip()
    hits: dict[str, list] = {}
    for r in store.search_documents(conn, phrase, limit=limit):
        hits[r["doc_id"]] = [2, r]  # full-phrase match weighted highest
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", phrase.lower())
              if len(t) >= 3 and t not in _DOC_STOPWORDS]
    for tok in tokens:
        for r in store.search_documents(conn, tok, limit=limit):
            if r["doc_id"] in hits:
                hits[r["doc_id"]][0] += 1
            else:
                hits[r["doc_id"]] = [1, r]
    ranked = sorted(hits.values(), key=lambda x: x[0], reverse=True)
    return [r for _, r in ranked[:limit]]


def _search_guru_cards_impl(conn, query, *, collections=None, limit=25,
                            offset=0) -> dict:
    """SEMANTIC/QUERY Guru card search (POST /search/cardmgr).

    Query-ranked — it may MISS cards that don't match the query. To ENUMERATE a
    whole collection completely, use ``list_guru_cards``; to enumerate a folder's
    contents, use ``list_guru_folder_items``. Returns id/title/collection/snippet.
    Supports an ``offset`` for paging through a large result set (client-side over
    the ranked results). Back-compat: same result shape as before, higher default
    limit (25).
    """
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    try:
        cards = GuruClient(email, token).search_cards(query or "")
    except Exception as exc:  # noqa: BLE001 — surface as a failed search
        return {"ok": False, "error": str(exc)[:160]}
    allowed = {c.lower() for c in (collections or [])}
    matched = []
    for c in cards:
        coll = (c.get("collection", "") or "").lower()
        coll_id = (c.get("collection_id", "") or "").lower()
        if allowed and coll not in allowed and coll_id not in allowed:
            continue
        matched.append({"card_id": c.get("id"), "title": c.get("title"),
                        "collection": c.get("collection"),
                        "url": _card_url(c.get("id")),
                        "snippet": _strip_html(c.get("content", ""))[:200]})
    off = max(0, int(offset or 0))
    lim = int(limit or 25)
    out = matched[off:off + lim]
    return {"ok": True, "query": query, "count": len(out),
            "total": len(matched), "offset": off, "cards": out}


def handle_search_guru_cards(conn, args, session_filters) -> dict:
    return _search_guru_cards_impl(conn, args.get("query", ""),
                                   collections=args.get("collections"),
                                   limit=args.get("limit", 25),
                                   offset=args.get("offset", 0))


# ── LIST tools (deterministic enumeration — "what's IN this collection/folder") ──
#
# LIST tools ENUMERATE a collection/folder COMPLETELY (paginate to the end, up to
# a sane cap) — this is what answers "what cards are in collection X" or "what's
# in folder Y". Contrast with search_guru_cards, which is query-ranked and may
# miss cards. Every LIST result reports the total count.


def _list_guru_cards_impl(conn, collection_id, *, limit=100, offset=0) -> dict:
    """DETERMINISTICALLY enumerate the cards in a Guru collection (COMPLETE).

    Wraps ``GuruClient.list_cards(collection_id)`` which follows the Link cursor
    to the end — so this is the full list, not a query-ranked subset. Returns a
    ``total`` (all cards in the collection) plus a ``limit``/``offset`` window of
    ``cards`` [{id, title, collection_name, verification_state?}]. No creds →
    ``{ok:False, error:'guru_not_connected'}``.
    """
    from src.data.guru_client import GuruClient
    cid = str(collection_id or "").strip()
    if not cid:
        return {"ok": False, "error": "collection_id_required"}
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    try:
        cards = GuruClient(email, token).list_cards(cid)
    except Exception as exc:  # noqa: BLE001 — surface as a failed enumeration
        return {"ok": False, "error": str(exc)[:160]}
    off = max(0, int(offset or 0))
    lim = int(limit or 100)
    window = cards[off:off + lim]
    out = []
    for c in window:
        row = {"id": c.get("id"), "title": c.get("title"),
               "collection_name": c.get("collection")}
        # verification_state isn't in the normalized list shape; include when present.
        vs = c.get("verification_state") or c.get("status")
        if vs:
            row["verification_state"] = vs
        out.append(row)
    return {"ok": True, "collection_id": cid, "count": len(out),
            "total": len(cards), "offset": off, "cards": out}


def handle_list_guru_cards(conn, args, session_filters) -> dict:
    return _list_guru_cards_impl(conn, args.get("collection_id"),
                                 limit=args.get("limit", 100),
                                 offset=args.get("offset", 0))


def _list_guru_folder_items_impl(conn, folder_id) -> dict:
    """DETERMINISTICALLY enumerate a Guru folder's items — cards AND sub-folders.

    Wraps ``GuruClient.get_folder_items(folder_id)`` (``GET /folders/{id}/items``).
    Returns ``{ok, count, items:[{id, item_id, type('card'|'folder'), title}]}``
    — the complete contents of that folder, including nested sub-folders (which
    you can recurse into with another call). No creds →
    ``{ok:False, error:'guru_not_connected'}``.
    """
    from src.data.guru_client import GuruClient
    fid = str(folder_id or "").strip()
    if not fid:
        return {"ok": False, "error": "folder_id_required"}
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    try:
        items = GuruClient(email, token).get_folder_items(fid)
    except Exception as exc:  # noqa: BLE001 — surface as a failed enumeration
        return {"ok": False, "error": str(exc)[:160]}
    out = [{"id": it.get("id"), "item_id": it.get("item_id"),
            "type": it.get("type"), "title": it.get("title")}
           for it in (items or [])]
    return {"ok": True, "folder_id": fid, "count": len(out), "items": out}


def handle_list_guru_folder_items(conn, args, session_filters) -> dict:
    return _list_guru_folder_items_impl(conn, args.get("folder_id"))


# ── Zendesk Help Center tools (search + deterministic list) ──────────
#
# NEW surface: Renn previously had NO Zendesk tools. A shared ZendeskClient is
# built from stored creds (ZendeskClient.from_settings — mirrors how GuruClient is
# built from load_credentials). All degrade to {ok:False,'zendesk_not_connected'}
# when creds are absent, so the chat never crashes.
#
# search_zendesk_articles is a query-ranked SEARCH (Help Center search endpoint —
# may miss articles). list_zendesk_articles is DETERMINISTIC enumeration of the
# Help Center. list_zendesk_macros lists the account's macros.


def _zendesk_client_or_none():
    """A ZendeskClient from stored creds, or None when Zendesk isn't connected.

    Mirrors GuruClient's creds-presence gate. ``ZendeskClient.from_settings``
    returns None when subdomain/email/api_key are missing. Never raises.
    """
    try:
        from src.data.zendesk_client import ZendeskClient
        return ZendeskClient.from_settings()
    except Exception:  # noqa: BLE001 — keyring/import fault → treat as not connected
        return None


def _search_zendesk_articles_impl(conn, query, *, limit=25) -> dict:
    """SEARCH the Zendesk Help Center by query (query-ranked — may miss articles).

    Wraps ``ZendeskClient.search_articles(query)``. To ENUMERATE the Help Center
    completely use ``list_zendesk_articles``. No creds →
    ``{ok:False, error:'zendesk_not_connected'}``. Returns
    ``{ok, count, articles:[{id, title, html_url?, section?}]}``.
    """
    client = _zendesk_client_or_none()
    if client is None:
        return {"ok": False, "error": "zendesk_not_connected"}
    try:
        results = client.search_articles(query or "")
    except Exception as exc:  # noqa: BLE001 — surface as a failed search
        return {"ok": False, "error": str(exc)[:160]}
    lim = int(limit or 25)
    out = [{"id": a.get("id"), "title": a.get("title"),
            "html_url": a.get("html_url"),
            "section": a.get("section_id")}
           for a in (results or [])[:lim]]
    return {"ok": True, "query": query, "count": len(out), "articles": out}


def handle_search_zendesk_articles(conn, args, session_filters) -> dict:
    return _search_zendesk_articles_impl(conn, args.get("query", ""),
                                         limit=args.get("limit", 25))


def _list_zendesk_articles_impl(conn, *, limit=100, offset=0) -> dict:
    """DETERMINISTICALLY enumerate the Zendesk Help Center articles (COMPLETE).

    Wraps ``ZendeskClient.get_articles(per_page)``. Reports the ``total`` fetched
    plus a ``limit``/``offset`` window. No creds →
    ``{ok:False, error:'zendesk_not_connected'}``. Returns
    ``{ok, count, total, offset, articles:[{id, title, html_url?, section?}]}``.
    """
    client = _zendesk_client_or_none()
    if client is None:
        return {"ok": False, "error": "zendesk_not_connected"}
    lim = int(limit or 100)
    off = max(0, int(offset or 0))
    try:
        # Follow next_page to the END so the LIST is COMPLETE (not one page),
        # and report Zendesk's own `count` as the true Help Center total.
        articles, total = client.get_articles_paged(per_page=100)
    except Exception as exc:  # noqa: BLE001 — surface as a failed enumeration
        return {"ok": False, "error": str(exc)[:160]}
    window = (articles or [])[off:off + lim]
    out = [{"id": a.get("id"), "title": a.get("title"),
            "html_url": a.get("html_url"),
            "section": a.get("section_id")}
           for a in window]
    return {"ok": True, "count": len(out), "total": total,
            "offset": off, "articles": out}


def handle_list_zendesk_articles(conn, args, session_filters) -> dict:
    return _list_zendesk_articles_impl(conn, limit=args.get("limit", 100),
                                       offset=args.get("offset", 0))


def _list_zendesk_macros_impl(conn, *, limit=100) -> dict:
    """DETERMINISTICALLY list the Zendesk account's macros.

    Wraps ``ZendeskClient.list_macros``. No creds →
    ``{ok:False, error:'zendesk_not_connected'}``. Returns
    ``{ok, count, macros:[{id, title, active?}]}``.
    """
    client = _zendesk_client_or_none()
    if client is None:
        return {"ok": False, "error": "zendesk_not_connected"}
    try:
        # Follow next_page to the END so the macro list is COMPLETE.
        macros, total = client.list_macros_paged(per_page=100)
    except Exception as exc:  # noqa: BLE001 — surface as a failed enumeration
        return {"ok": False, "error": str(exc)[:160]}
    lim = int(limit or 100)
    out = [{"id": m.get("id"), "title": m.get("title"),
            "active": m.get("active")}
           for m in (macros or [])[:lim]]
    return {"ok": True, "count": len(out), "total": total, "macros": out}


def handle_list_zendesk_macros(conn, args, session_filters) -> dict:
    return _list_zendesk_macros_impl(conn, limit=args.get("limit", 100))


def _research_topic_impl(conn, topic, *, limit=5, collections=None) -> dict:
    """Gather reference points on a topic from every source Renn can reach:
    live Guru cards, locally-stored docs, and ticket signals. Each source is
    best-effort — a failure in one never blocks the others.
    """
    limit = int(limit or 5)  # a model/bridge may pass "5" as a string; coerce once
    out = {"ok": True, "topic": topic, "guru_cards": [], "documents": [], "ticket_signals": []}

    g = _search_guru_cards_impl(conn, topic, collections=collections, limit=limit)
    if g.get("ok"):
        out["guru_cards"] = [{"card_id": c["card_id"], "title": c["title"],
                              "url": c.get("url")} for c in g["cards"]]
    else:
        out["guru_error"] = g.get("error")

    try:
        docs = _search_documents_multi(conn, topic, limit=limit)
        out["documents"] = [{"doc_id": d.get("doc_id"), "name": d.get("name")} for d in docs]
    except Exception as exc:  # noqa: BLE001
        out["doc_error"] = str(exc)[:120]

    # Ticket signals are intentionally DISABLED pending a PHI-boundary decision.
    # The enablement lane disables aggressive PII redaction, so it must NEVER pull
    # raw ticket conversations (patient text). Do NOT wire this to
    # handle_legacy_search_conversations — its results carry raw subjects/snippets
    # (PHI). Re-enable later only as a DE-IDENTIFIED aggregate (counts by TRC, no
    # ticket text). (Previously read a wrong dict key, so it returned [] anyway;
    # made explicit here so a future "key fix" can't silently leak PHI.)
    out["ticket_signals"] = []
    out["ticket_signals_note"] = "Disabled pending a PHI-safe de-identified aggregate."

    out["note"] = ("Reference points gathered. Use these as authoritative input — "
                   "pass relevant card_ids/doc_ids as reference_refs when updating a card.")
    return out


def handle_research_topic(conn, args, session_filters) -> dict:
    return _research_topic_impl(conn, args.get("topic", ""),
                                limit=args.get("limit", 5),
                                collections=args.get("collections"))


def _open_guru_card_impl(conn, card_ref) -> dict:
    """Open a Guru card in the operator's default browser (Guru URLs only)."""
    import webbrowser
    from src.data.enablement_store import parse_guru_card_ref
    card_id = parse_guru_card_ref(str(card_ref or ""))
    if not card_id:
        return {"ok": False, "error": "card_ref_required"}
    url = _card_url(card_id)
    try:
        opened = bool(webbrowser.open(url))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:160], "url": url}
    return {"ok": True, "url": url, "opened": opened,
            "note": ("Opened the card in your default browser." if opened
                     else f"Could not launch a browser — open it manually: {url}")}


def handle_open_guru_card(conn, args, session_filters) -> dict:
    return _open_guru_card_impl(
        conn, args.get("card_ref") or args.get("card_id") or args.get("url"))


# ── Content catalog (summary index for scalable, torch-free retrieval) ──

def _doc_items(conn, limit) -> list[dict]:
    from src.data import enablement_store as store
    out = []
    for d in store.list_documents(conn, limit=limit):
        full = store.get_document(conn, d["doc_id"]) or {}
        out.append({"item_id": f"doc:{d['doc_id']}", "item_type": "doc",
                    "title": d.get("name", ""), "source": d.get("source", "upload"),
                    "url": d.get("web_url", "") or "",
                    "text": full.get("full_text", "") or d.get("text_excerpt", "")})
    return out


def _guru_items(conn, query, collections, limit) -> list[dict]:
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return []
    try:
        cards = GuruClient(email, token).search_cards(query or "")
    except Exception:  # noqa: BLE001
        return []
    allowed = {c.lower() for c in (collections or [])}
    out = []
    for c in (cards or [])[:limit]:
        cid = c.get("id")
        if not cid:
            continue  # a card with no id would index as 'card:None' and break pull/fetch
        coll = (c.get("collection", "") or "").lower()
        coll_id = (c.get("collection_id", "") or "").lower()
        if allowed and coll not in allowed and coll_id not in allowed:
            continue
        out.append({"item_id": f"card:{cid}", "item_type": "card",
                    "title": c.get("title", ""), "source": "guru",
                    "url": _card_url(cid), "text": _strip_html(c.get("content", ""))})
    return out


def _index_content_impl(conn, *, scope="docs", query=None, collections=None, limit=200) -> dict:
    """Build/refresh the summary catalog over PHI-free content (docs + Guru cards)."""
    from src.data.content_catalog import index_items, write_catalog_md
    from src.gemini.client_factory import build_client_for_task
    scope = (scope or "docs").lower()
    items: list[dict] = []
    if scope in ("docs", "all"):
        items += _doc_items(conn, limit)
    if scope in ("guru", "all"):
        items += _guru_items(conn, query, collections, limit)
    if not items:
        return {"ok": True, "indexed": 0, "skipped": 0,
                "note": "No PHI-free content found to index for that scope."}
    llm = build_client_for_task("enablement_card_gen")
    res = index_items(conn, items, llm_client=llm)
    try:
        from pathlib import Path
        from src.data.connection_factory import DEFAULT_DB_PATH
        res["catalog_md"] = write_catalog_md(
            conn, str(Path(DEFAULT_DB_PATH).parent / "content_catalog.md"))
    except Exception:  # noqa: BLE001 — md export is best-effort
        pass
    res["ok"] = True
    res["note"] = (f"Indexed {res['indexed']} item(s), {res['skipped']} unchanged. "
                   "Search with search_content.")
    return res


def handle_index_content(conn, args, session_filters) -> dict:
    return _index_content_impl(conn, scope=args.get("scope", "docs"),
                               query=args.get("query"), collections=args.get("collections"),
                               limit=int(args.get("limit", 200)))


def _search_catalog_impl(conn, query, *, limit=5) -> dict:
    """Deterministic hybrid search over the LOCAL summary catalog (torch-free).

    This is the summary-index search (docs + Guru cards indexed by
    ``index_content``); it disambiguates look-alike titles by content summary
    and needs ``index_content`` run first. For a LIVE cross-source lookup ("do we
    have anything on X anywhere") use ``search_content``, which fans out to the
    live Guru/Zendesk/Drive search tools without a pre-built catalog.
    """
    from src.data.content_catalog import all_entries, search_catalog
    if not all_entries(conn):
        return {"ok": True, "query": query, "count": 0, "results": [],
                "note": "Catalog is empty — run index_content first to build the summary index."}
    ranked = search_catalog(conn, query or "", limit=int(limit or 5))
    return {"ok": True, "query": query, "count": len(ranked),
            "results": [{"item_id": r.entry.item_id, "type": r.entry.item_type,
                         "title": r.entry.title, "url": r.entry.url,
                         "summary": r.entry.summary, "score": r.score,
                         "matched_terms": r.matched_terms} for r in ranked]}


def handle_search_catalog(conn, args, session_filters) -> dict:
    return _search_catalog_impl(conn, args.get("query", ""), limit=args.get("limit", 5))


# ── UNIFIED cross-source search (M9 part 2) ──────────────────────────
#
# search_content FANS OUT to the per-source LIVE SEARCH impls (Guru search_cards,
# Zendesk search_articles, Drive doc search) for the requested/available sources,
# merges into one ranked-ish list, and LABELS every result with its source. It
# answers "do we have anything on X ANYWHERE". It is READ-ONLY, un-gated, and
# degrades PER-SOURCE: a source that isn't connected is SKIPPED (its status is
# reported so Renn can say "(Zendesk not connected)") and never fails the whole
# call.
#
# This is DISTINCT from the LIST tools (list_guru_cards / list_guru_folder_items /
# list_zendesk_articles / list_asana_tasks), which ENUMERATE one collection/
# folder/board COMPLETELY — use those to answer "what's IN X", not search_content.
# It is also distinct from search_catalog (the local summary-index search).

# The sources search_content knows how to fan out to. Order is the merge order
# for ties. (Asana is intentionally NOT here — Asana boards carry task/roadmap
# items, not reference "content" you look up; use search_asana_tasks/list_asana_tasks.)
_UNIFIED_SEARCH_SOURCES = ("guru", "zendesk", "drive")


def _unified_guru_results(conn, query, limit) -> dict:
    """One fan-out arm: LIVE Guru card search → labeled result rows or a status.

    Returns ``{"results": [...], "status": "ok"|"not_connected"|"error", ...}``.
    Never raises — a not-connected/failed source is reported, not fatal.
    """
    r = _search_guru_cards_impl(conn, query, limit=limit)
    if not r.get("ok"):
        err = r.get("error", "")
        status = "not_connected" if "not_connected" in err else "error"
        return {"results": [], "status": status, "error": err}
    rows = [{"source": "guru", "id": c.get("card_id"), "title": c.get("title"),
             "snippet": c.get("snippet"), "url": c.get("url")}
            for c in r.get("cards", [])]
    return {"results": rows, "status": "ok"}


def _unified_zendesk_results(conn, query, limit) -> dict:
    """One fan-out arm: LIVE Zendesk Help Center search → labeled rows or a status."""
    r = _search_zendesk_articles_impl(conn, query, limit=limit)
    if not r.get("ok"):
        err = r.get("error", "")
        status = "not_connected" if "not_connected" in err else "error"
        return {"results": [], "status": status, "error": err}
    rows = [{"source": "zendesk", "id": a.get("id"), "title": a.get("title"),
             "snippet": None, "url": a.get("html_url")}
            for a in r.get("articles", [])]
    return {"results": rows, "status": "ok"}


def _unified_drive_results(conn, query, limit) -> dict:
    """One fan-out arm: local Drive-doc mirror search → labeled rows.

    ``_search_drive_docs_impl`` reads the locally-stored Drive mirror and always
    succeeds (no live creds needed), so this arm's status is always ``ok``.
    """
    r = _search_drive_docs_impl(conn, query, limit=limit)
    docs = r.get("documents", [])
    rows = [{"source": "drive", "id": d.get("doc_id"), "title": d.get("name"),
             "snippet": (d.get("text_excerpt") or "")[:200] or None,
             "url": d.get("web_url") or None}
            for d in docs]
    return {"results": rows, "status": "ok"}


_UNIFIED_SEARCH_ARMS = {
    "guru": _unified_guru_results,
    "zendesk": _unified_zendesk_results,
    "drive": _unified_drive_results,
}


def _search_content_impl(conn, query, *, sources=None, limit=8) -> dict:
    """UNIFIED cross-source SEARCH — "do we have anything on X anywhere".

    Fans out the query to the per-source LIVE SEARCH impls for each requested (or,
    by default, every) source, merges into one labeled list, and reports a
    per-source status. Every result is LABELED with its ``source``. Read-only,
    no gate. A source that isn't connected is SKIPPED (reported in ``sources``),
    never fatal. To ENUMERATE a collection/folder/board instead, use a LIST tool
    (list_guru_cards / list_guru_folder_items / list_zendesk_articles /
    list_asana_tasks) — this SEARCH is query-ranked and may be partial.

    Returns ``{ok, query, count, results:[{source, title, id, snippet, url?}],
    sources:{<source>:{status, count}}}``.
    """
    limit = int(limit or 8)  # a bridge may pass "8" as a string; coerce once
    # Normalize + validate the requested source list; unknown names are dropped.
    requested = [s for s in (sources or _UNIFIED_SEARCH_SOURCES)
                 if s in _UNIFIED_SEARCH_ARMS]
    if not requested:
        requested = list(_UNIFIED_SEARCH_SOURCES)
    # Keep a stable fan-out/merge order (guru, zendesk, drive) regardless of the
    # order the caller listed the sources in.
    ordered = [s for s in _UNIFIED_SEARCH_SOURCES if s in requested]

    merged: list[dict] = []
    status: dict[str, dict] = {}
    for src in ordered:
        arm = _UNIFIED_SEARCH_ARMS[src]
        try:
            res = arm(conn, query, limit)
        except Exception as exc:  # noqa: BLE001 — one arm failing never fails the call
            status[src] = {"status": "error", "count": 0, "error": str(exc)[:120]}
            continue
        rows = res.get("results", [])
        entry = {"status": res.get("status", "ok"), "count": len(rows)}
        if res.get("error"):
            entry["error"] = res["error"]
        status[src] = entry
        merged.extend(rows)

    # "Ranked-ish": interleave sources round-robin so no single source dominates
    # the head of the list, then cap at `limit`. Within a source, order is the
    # per-source rank order returned by its search impl.
    interleaved = _interleave_by_source(merged, ordered)
    out = interleaved[:limit]
    return {"ok": True, "query": query, "count": len(out),
            "results": out, "sources": status}


def _interleave_by_source(rows, order) -> list[dict]:
    """Round-robin merge so the top of the list samples every source that hit,
    instead of returning all of source A then all of source B."""
    buckets: dict[str, list] = {s: [] for s in order}
    for r in rows:
        buckets.setdefault(r.get("source"), []).append(r)
    out, idx = [], 0
    while True:
        added = False
        for s in order:
            b = buckets.get(s) or []
            if idx < len(b):
                out.append(b[idx])
                added = True
        if not added:
            break
        idx += 1
    return out


def handle_search_content(conn, args, session_filters) -> dict:
    return _search_content_impl(conn, args.get("query", ""),
                                sources=args.get("sources"),
                                limit=args.get("limit", 8))


# ── Multi-card fan-out (one source change -> the set of cards it affects) ──

def _update_cards_from_doc_impl(conn, *, task_id=None, doc_ref=None, doc_query=None,
                                search=None, collections=None, max_cards=5) -> dict:
    """Find every card a source change affects and stage an update for each
    (catalog-first card selection). Staging only — publish stays human-gated."""
    import json
    from src.data import enablement_store as store
    from src.data import enablement_tasks as tasks
    from src.data.content_update import ContentUpdateRequest, Deps, run_fanout_update
    from src.data.guru_client import GuruClient
    from src.gemini.client_factory import build_client_for_task

    if task_id:
        t = tasks.get_task(conn, str(task_id))
        if t:
            try:
                hint = json.loads(t.get("scratchpad") or "{}")
            except (ValueError, TypeError):
                hint = {}
            doc_ref = doc_ref or hint.get("source_doc_ref")
            doc_query = doc_query or hint.get("source_doc_query")
            search = search or hint.get("search_query")

    source_doc_ref = doc_ref
    if not source_doc_ref and doc_query:
        docs = store.search_documents(conn, doc_query, limit=1)
        if docs:
            source_doc_ref = docs[0].get("doc_id")
        else:
            # A query WAS given but matched nothing — distinct from "no doc param".
            return {"ok": False,
                    "error": f"no_matching_doc: no stored document matched query '{doc_query}'"}
    if not source_doc_ref:
        return {"ok": False, "error": "source_doc_required: pass doc_ref, doc_query, or a task_id"}

    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    llm = build_client_for_task("enablement_card_update")
    if llm is None:
        return {"ok": False, "error": "no_llm_client"}

    req = ContentUpdateRequest(source_doc_ref=source_doc_ref, search_query=search,
                               collections=collections or [])
    res = run_fanout_update(conn, req, Deps(llm_client=llm, guru_client=GuruClient(email, token)),
                            max_cards=int(max_cards or 5))
    if not res.get("ok"):
        return res
    for c in res["cards"]:
        if isinstance(c.get("diff"), str):
            c["diff"] = c["diff"][:1200]
    res["note"] = (f"Checked {res['candidates']} related card(s); staged {res['staged']} "
                   "update(s). Review each diff; publish each with push_guru_draft once approved.")
    return res


def handle_update_cards_from_doc(conn, args, session_filters) -> dict:
    return _update_cards_from_doc_impl(
        conn, task_id=args.get("task_id"), doc_ref=args.get("doc_ref"),
        doc_query=args.get("doc_query"), search=args.get("search"),
        collections=args.get("collections"), max_cards=args.get("max_cards", 5))


# ── Provenance + effectiveness (the audit trail + did-it-work feedback) ──

def _card_ref_arg(args):
    from src.data.enablement_store import parse_guru_card_ref
    ref = args.get("card_ref") or args.get("card_id") or args.get("url")
    return parse_guru_card_ref(str(ref or ""))


def handle_card_history(conn, args, session_filters) -> dict:
    """Audit trail: every update to a card — what changed, from where, who, when."""
    from src.data.content_update import provenance
    cid = _card_ref_arg(args)
    if not cid:
        return {"ok": False, "error": "card_ref_required"}
    return {"ok": True, "card_id": cid,
            "history": provenance.history(conn, cid),
            "evidence": provenance.evidence_pack(conn, card_id=cid)}


def handle_card_effectiveness(conn, args, session_filters) -> dict:
    """Did-it-work feedback: a card's update history + measured ticket-volume impact."""
    from src.data.content_update.effectiveness import card_effectiveness
    cid = _card_ref_arg(args)
    if not cid:
        return {"ok": False, "error": "card_ref_required"}
    return card_effectiveness(conn, cid)


# ── Task-shaped attention queue (reason over INTENT, not raw queries) ──
#
# Three verbs wrap enablement_health.compute_health so Renn asks "what needs
# my attention?" instead of running ad-hoc analytics. All three share one
# compute → filter → rank → shape pipeline (no copy-paste); each verb only
# differs in which bucket(s) it keeps. compute_health is already fully
# decoupled (Guru live + enablement-local tables) — no warehouse reads added.

_NOT_CONNECTED = {"ok": True, "cards": [], "note": "Guru not connected"}


def _health_guru_client():
    """Build a GuruClient from stored creds, or None when Guru isn't connected.

    Mirrors compute_health's own None-handling: an unconfigured client makes
    compute_health return [] (graceful), so callers never raise.
    """
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return None
    return GuruClient(email, token)


def _why(card) -> str:
    """A short, human reason a card is on the queue, from its bucket/signals."""
    sig = card.signals
    if card.bucket == "source_changed":
        return "A source document changed after this card was last updated."
    if card.bucket == "verification_overdue":
        days = int(round(sig.days_overdue)) if sig else 0
        return f"Verification is {days} day(s) overdue."
    if card.bucket == "gap_dup":
        if sig and sig.duplicate_of:
            return f"Duplicates {len(sig.duplicate_of)} other card(s): {', '.join(sig.duplicate_of)}."
        return "Covers a content gap not fully addressed by any card."
    return "Healthy — no outstanding attention items."


def _shape(card) -> dict:
    """One CardHealth → the compact attention-queue row Renn reads."""
    sig = card.signals
    return {
        "card_id": card.card_id,
        "title": (sig.title if sig else "") or "",
        "score": round(card.score, 4),
        "bucket": card.bucket,
        "why": _why(card),
    }


def _rank(cards) -> list:
    """Lowest health first — the cards most in need of attention lead."""
    return sorted(cards, key=lambda c: c.score)


def _coerce_limit(limit, default=10) -> int:
    """Coerce an optional (possibly string) limit to a positive int."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _attention_queue(conn, *, limit, bucket=None) -> dict:
    """Shared pipeline: compute health → optional bucket filter → rank → top-N.

    Returns the graceful not-connected payload when Guru isn't configured.
    """
    from src.data.enablement_health import compute_health
    guru_client = _health_guru_client()
    if guru_client is None:
        return dict(_NOT_CONNECTED)
    cards = compute_health(conn, guru_client)
    if bucket:
        cards = [c for c in cards if c.bucket == bucket]
    top = _rank(cards)[: _coerce_limit(limit)]
    return {"ok": True, "count": len(top), "cards": [_shape(c) for c in top]}


def handle_find_cards_to_update(conn, args, session_filters) -> dict:
    """Ranked attention queue across all buckets (optional single-bucket filter)."""
    return _attention_queue(conn, limit=args.get("limit", 10), bucket=args.get("bucket"))


def _freshness(card) -> float:
    """A card's freshness component (lower = staler); 1.0 when absent."""
    return card.components.get("freshness", 1.0) if card.components else 1.0


def handle_find_stale_cards(conn, args, session_filters) -> dict:
    """Cards whose verification is overdue; fall back to lowest-freshness."""
    from src.data.enablement_health import compute_health
    limit = _coerce_limit(args.get("limit", 10))
    guru_client = _health_guru_client()
    if guru_client is None:
        return dict(_NOT_CONNECTED)
    cards = compute_health(conn, guru_client)
    overdue = [c for c in cards if c.bucket == "verification_overdue"]
    pool = overdue if overdue else cards
    top = sorted(pool, key=_freshness)[:limit]
    return {"ok": True, "count": len(top), "cards": [_shape(c) for c in top]}


def handle_find_content_gaps(conn, args, session_filters) -> dict:
    """Cards flagged as a content gap or a duplicate, ranked."""
    return _attention_queue(conn, limit=args.get("limit", 10), bucket="gap_dup")
