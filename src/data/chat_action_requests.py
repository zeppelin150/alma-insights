"""Chat action requests — the M0 action channel for the standalone Agent chat.

An "action request" is a UI action Renn asks the operator to take: open a
folder/board/publish picker, or connect a Google account. Resolver tools (run in
the MCP subprocess) mint one row here; the main-process action poll
(``AgentChatController.poll_action_requests``) atomically claims unconsumed rows
and the bridge emits ``actionRequested`` so React opens the matching picker.

Design invariants (from the plan's MUST-FIX list):
  1. Dedicated table (``chat_action_requests``), AUTOINCREMENT + ``consumed``
     flag — not a ``rowid`` cursor on ``chat_tool_executions``.
  2. The claim is a single atomic ``UPDATE … WHERE consumed=0`` with a rowcount
     check, so exactly one poller wins (single-emit under concurrent pollers).
  4. ``request_id`` is server-minted ``uuid4`` here — any client/LLM-supplied id
     is ignored upstream; the resolver always calls ``create_action_request``.
  14. ``payload_json`` is tiny + non-PHI: only scalar *control* keys (e.g.
      ``have_client``) are allowed. Anything that looks like a name/listing is
      rejected — folder/board data loads later, in the main process, into React
      only.

Connection discipline: callers pass a connection (``get_connection`` in the main
process, or a raw subprocess ``sqlite3`` connection for resolver handlers). We
use explicit ``conn.commit()`` — never ``atomic()`` — so the same functions work
on both connection kinds and never open a nested transaction.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

# The only request types the channel recognizes. Mirrors migration 038's
# ``type`` column comment and the React ActionRouter's switch.
#
# ``confirm_write`` (M7b) is the human-gated WRITE channel: a resolver-style
# propose tool mints one of these so the app pops a Confirm/Cancel card before
# any non-idempotent live write (create a Guru folder, rename one, create an
# Asana task) runs. It rides the SAME table/poll/claim machinery as the pickers,
# but its payload carries an operational ``{op, summary, params}`` (see
# ``create_confirm_write`` for the scoped invariant-14 extension).
ACTION_TYPES = frozenset({
    "drive_folder_picker",
    "asana_board_picker",
    "guru_publish_picker",
    "google_connect",
    "confirm_write",
    "research_plan",
})

# payload_json may only carry these scalar control keys. Keeping the allowlist
# tiny is the enforcement for invariant 14 — no folder/board/collection names,
# no listing data, no PHI ever rides the channel. (This guards the PICKER/connect
# rows; confirm_write rows take the dedicated ``create_confirm_write`` path with a
# scoped, documented payload extension — they never pass through here.)
_ALLOWED_PAYLOAD_KEYS = frozenset({"have_client"})

_SCALAR_TYPES = (bool, int, float, str)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _row_to_dict(row) -> dict:
    if isinstance(row, sqlite3.Row):
        return {k: row[k] for k in row.keys()}
    return dict(row) if hasattr(row, "keys") else {}


def _validate_payload(payload) -> dict:
    """Return a sanitized, minimal, non-PHI control dict — or raise.

    Rejects anything that is not a tiny dict of allowlisted scalar control keys.
    This is the load-bearing guard for invariant 14: it makes it impossible for a
    resolver (or a forged caller) to smuggle a folder/board name or listing data
    through ``payload_json``.
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("action payload must be a dict of control keys")
    if len(payload) > len(_ALLOWED_PAYLOAD_KEYS):
        raise ValueError("action payload too large — control keys only")
    clean: dict = {}
    for key, value in payload.items():
        if key not in _ALLOWED_PAYLOAD_KEYS:
            raise ValueError(
                f"disallowed payload key {key!r} — only "
                f"{sorted(_ALLOWED_PAYLOAD_KEYS)} are permitted (non-PHI control "
                "keys only)"
            )
        if not isinstance(value, _SCALAR_TYPES):
            raise ValueError(
                f"payload value for {key!r} must be a scalar (no names/listings)"
            )
        # Defensively reject anything non-trivial smuggled into a string scalar:
        # control values are short flags, never free text / names.
        if isinstance(value, str) and len(value) > 32:
            raise ValueError(f"payload value for {key!r} too long — control only")
        clean[key] = value
    return clean


def create_action_request(conn, session_id: str, type: str, payload=None) -> str:
    """Mint a server-side ``request_id`` (uuid4), validate the payload is a tiny
    non-PHI control dict, INSERT one row, and return the ``request_id``.

    The ``request_id`` is minted HERE (never taken from the caller/LLM) — that is
    invariant 4's non-forgeable half. ``payload`` is sanitized by
    :func:`_validate_payload` — invariant 14.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if type not in ACTION_TYPES:
        raise ValueError(f"unknown action type {type!r}")
    if type == "confirm_write":
        # confirm_write carries an operational {op, summary, params} payload that
        # the picker allowlist (_validate_payload) deliberately rejects. Mint it
        # only through create_confirm_write so the scoped invariant-14 extension
        # (and its non-PHI invariant) is enforced in one place.
        raise ValueError("use create_confirm_write for confirm_write rows")
    clean = _validate_payload(payload)
    request_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO chat_action_requests
           (session_id, request_id, type, payload_json, consumed, created_at)
           VALUES (?, ?, ?, ?, 0, ?)""",
        (session_id, request_id, type,
         json.dumps(clean) if clean else None, _now()),
    )
    conn.commit()
    return request_id


# ── confirm_write (M7b): the human-gated, non-idempotent WRITE channel ──
#
# A confirm_write row's ``payload_json`` carries ``{op, summary, params}`` — an
# OPERATIONAL envelope, NOT a picker control flag, so it intentionally does NOT
# pass through ``_validate_payload``'s scalar-control-key allowlist. This is the
# documented, SCOPED invariant-14 extension for confirm_write:
#
#   * ``op``       — one of the allowlisted write ops below (which controller
#                    write to dispatch). A label, never data.
#   * ``params``   — the write's arguments: operator/model-composed operational
#                    TITLES + IDS (collection_id, folder_id, project_gid, a folder
#                    or task title, optional notes/due_on). These are enablement
#                    metadata (KB structure, project/board names, card titles) —
#                    the SAME class of operational names the Asana/Guru pickers
#                    already surface (invariant 13, source-aware). They are NOT
#                    PHI/ticket content: the enablement lane is decoupled from the
#                    PHI ticket warehouse, and the propose tools compose these from
#                    KB/board structure, never from patient/case records.
#   * ``summary``  — a short human display string shown on the Confirm card.
#
# Anything PHI-adjacent (Drive/Shared-Drive folder names) is OUT OF SCOPE: there
# is no confirm_write op that writes a Drive folder name, by design.
_CONFIRM_WRITE_OPS = frozenset({
    "create_guru_folder",
    "rename_guru_folder",
    "create_asana_task",
})


def create_confirm_write(conn, session_id: str, op: str, summary: str,
                         params: dict) -> str:
    """Mint a server-side ``request_id`` (uuid4) and INSERT one ``confirm_write``
    row carrying ``{op, summary, params}``, returning the ``request_id``.

    The id is minted HERE (never from the caller/LLM) — invariant 4's
    non-forgeable half, identical to ``create_action_request``. ``op`` must be one
    of the allowlisted write ops (so a forged caller can't smuggle an arbitrary
    dispatch). ``params`` is the operational (non-PHI) write envelope per the
    scoped invariant-14 extension documented above.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if op not in _CONFIRM_WRITE_OPS:
        raise ValueError(f"unknown confirm_write op {op!r}")
    if not isinstance(params, dict):
        raise ValueError("confirm_write params must be a dict")
    payload = {"op": op, "summary": str(summary or ""), "params": params}
    request_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO chat_action_requests
           (session_id, request_id, type, payload_json, consumed, created_at)
           VALUES (?, ?, 'confirm_write', ?, 0, ?)""",
        (session_id, request_id, json.dumps(payload), _now()),
    )
    conn.commit()
    return request_id


def create_research_plan(conn, session_id: str, task_id: str, summary: str,
                         plan_steps: list) -> str:
    """Mint a server-side ``request_id`` (uuid4) and INSERT one ``research_plan``
    row carrying ``{task_id, summary, steps}`` — the ASK-FIRST envelope.

    Like ``create_confirm_write`` this takes its own scoped path (not the picker
    scalar allowlist): the payload is operational enablement metadata (a task id, a
    short summary, and step LABELS), never PHI/listing data. The id is minted HERE
    (invariant 4). The row rides the SAME free-running action channel + single-
    winner claim as the pickers, so a double-approve can't spawn two jobs.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if not task_id:
        raise ValueError("task_id is required")
    steps = [str(s) for s in (plan_steps or []) if str(s).strip()]
    payload = {"task_id": str(task_id), "summary": str(summary or ""), "steps": steps}
    request_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO chat_action_requests
           (session_id, request_id, type, payload_json, consumed, created_at)
           VALUES (?, ?, 'research_plan', ?, 0, ?)""",
        (session_id, request_id, json.dumps(payload), _now()),
    )
    conn.commit()
    return request_id


def claim_pending_actions(conn, session_id: str) -> list[dict]:
    """Atomically claim this session's unconsumed action requests.

    SELECTs ``WHERE session_id=? AND consumed=0 ORDER BY id``; for each candidate
    runs a single ``UPDATE … SET consumed=1 WHERE id=? AND consumed=0`` and
    includes the row ONLY if ``rowcount == 1`` (single-winner — invariant 2). A
    second poller racing on the same row sees ``rowcount == 0`` and drops it, so
    every action emits exactly once.

    Returns ``[{id, request_id, type, payload}]`` (payload parsed back to a dict).
    """
    if not session_id:
        return []
    rows = conn.execute(
        """SELECT id, request_id, type, payload_json
           FROM chat_action_requests
           WHERE session_id=? AND consumed=0
           ORDER BY id""",
        (session_id,),
    ).fetchall()

    claimed: list[dict] = []
    for row in rows:
        d = _row_to_dict(row)
        cur = conn.execute(
            "UPDATE chat_action_requests SET consumed=1 "
            "WHERE id=? AND consumed=0",
            (d["id"],),
        )
        conn.commit()
        if cur.rowcount != 1:
            continue   # another poller won this row — drop it (single-winner)
        payload = {}
        raw = d.get("payload_json")
        if raw:
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        claimed.append({
            "id": d["id"],
            "request_id": d["request_id"],
            "type": d["type"],
            "payload": payload,
        })
    return claimed


def get_action_request(conn, request_id: str) -> dict | None:
    """Fetch one action request by ``request_id`` (for resolve-time session
    validation in a later milestone). Returns the row as a dict, or ``None``."""
    if not request_id:
        return None
    row = conn.execute(
        """SELECT id, session_id, request_id, type, payload_json, consumed,
                  resolved, created_at
           FROM chat_action_requests WHERE request_id=?""",
        (request_id,),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def has_pending_action(conn, session_id: str) -> bool:
    """Whether the session has any UNRESOLVED action request — the predicate the
    human-gate (invariant 5) uses to refuse a privileged write while a picker is
    open.

    Keys off ``resolved=0`` (NOT ``consumed=0``): ``consumed`` flips the instant
    the action poll EMITS the request (the picker opens), so a ``consumed``-based
    gate would re-open the moment the picker rendered — exactly the open-but-not-
    yet-picked window the gate must cover. ``resolved`` stays 0 until the operator
    actually picks (``mark_resolved``), so the gate stays closed for the whole
    open window. Covers both ``created`` (consumed=0,resolved=0) and
    ``emitted/open`` (consumed=1,resolved=0).
    """
    if not session_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM chat_action_requests "
        "WHERE session_id=? AND resolved=0 LIMIT 1",
        (session_id,),
    ).fetchone()
    return row is not None


def mark_resolved(conn, request_id: str) -> bool:
    """Atomically resolve one action request — the single-winner pick commit.

    Runs ``UPDATE … SET resolved=1 WHERE request_id=? AND resolved=0`` and
    returns ``rowcount == 1``. A second resolve of the same ``request_id`` (a
    double-pick / replayed bridge call) sees ``rowcount == 0`` and returns
    ``False``, so a pick commits exactly once (dedupes double-pick — the resolve
    twin of ``claim_pending_actions``' single-winner emit). Does NOT touch
    ``consumed``: a resolved row stays consumed=1,resolved=1.
    """
    if not request_id:
        return False
    cur = conn.execute(
        "UPDATE chat_action_requests SET resolved=1 "
        "WHERE request_id=? AND resolved=0",
        (request_id,),
    )
    conn.commit()
    return cur.rowcount == 1
