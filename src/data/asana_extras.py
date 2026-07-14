"""Rich Asana task extras (WS1-M3, renn-calendar-kb-studio plan).

Side-table store for the task context the poll's lean fields skip: html_notes
(raw, UNTRUSTED — render PlainText only), all custom-field values, attachments,
and comment stories. Kept out of ``enablement_tasks`` so the list/calendar
query stays cheap; the detail panel joins lazily per task.

Refresh discipline (the pre-mortem extras-storm fix): extras are STALE when
``for_modified_at != enablement_tasks.remote_modified_at`` — a same-source
comparison, never our clock vs Asana's. ``drain_stale`` refreshes up to a
per-poll cap (default 10; ``enablement.asana.extras_per_poll_cap``), so a
post-migration baseline that stamps every task self-drains over a few cycles
instead of firing hundreds of GETs in one poll. Story events (which never bump
task modified_at) force staleness via ``mark_stories_dirty``.

Qt-free; plain execute+commit (poll-thread + MCP-subprocess safe); NO atomic().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("alma.asana_extras")

_DEFAULT_CAP = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extras_cap() -> int:
    try:
        from src.data.settings_manager import get_section
        asana_cfg = (get_section("enablement", {}) or {}).get("asana") or {}
        return max(0, int(asana_cfg.get("extras_per_poll_cap", _DEFAULT_CAP)))
    except Exception:  # noqa: BLE001
        return _DEFAULT_CAP


def upsert_extras(conn, task_id: str, task_payload: dict, *, client=None) -> None:
    """Store extras for one task. Attachments/stories are fetched best-effort
    (each in its own try/except — partial data beats no data); html_notes and
    custom fields come from the already-fetched task payload."""
    gid = task_payload.get("gid") or ""
    attachments: list = []
    stories: list = []
    if client is not None and gid:
        try:
            attachments = client.list_attachments(gid) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("attachments fetch failed for %s: %s", gid, exc)
        try:
            stories = client.list_stories(gid) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("stories fetch failed for %s: %s", gid, exc)
    try:
        conn.execute(
            """INSERT INTO asana_task_extras
               (task_id, html_notes, custom_fields_json, attachments_json,
                stories_json, for_modified_at, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                 html_notes=excluded.html_notes,
                 custom_fields_json=excluded.custom_fields_json,
                 attachments_json=excluded.attachments_json,
                 stories_json=excluded.stories_json,
                 for_modified_at=excluded.for_modified_at,
                 fetched_at=excluded.fetched_at""",
            (task_id,
             task_payload.get("html_notes") or "",
             json.dumps(task_payload.get("custom_fields") or []),
             json.dumps(attachments),
             json.dumps(stories),
             task_payload.get("modified_at") or "",
             _now()),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise


def get_extras(conn, task_id: str) -> dict | None:
    """Extras for one task with JSON fields decoded, or None."""
    row = conn.execute(
        "SELECT * FROM asana_task_extras WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        return None
    out = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    for key in ("custom_fields_json", "attachments_json", "stories_json"):
        try:
            out[key[:-5]] = json.loads(out.get(key) or "[]")
        except (ValueError, TypeError):
            out[key[:-5]] = []
    return out


def stale_task_ids(conn, *, limit: int) -> list[tuple[str, str]]:
    """(task_id, source_ref) pairs whose extras are missing or out of date —
    the self-describing refresh queue. Dismissed tasks are skipped."""
    if limit <= 0:
        return []
    rows = conn.execute(
        """SELECT t.task_id, t.source_ref
           FROM enablement_tasks t
           LEFT JOIN asana_task_extras x ON x.task_id = t.task_id
           WHERE t.source='asana' AND t.status != 'dismissed'
             AND COALESCE(t.remote_modified_at,'') != ''
             AND COALESCE(x.for_modified_at,'') != COALESCE(t.remote_modified_at,'')
           ORDER BY t.updated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def mark_stories_dirty(conn, gids: list) -> int:
    """Force staleness for tasks whose comment stream changed (story events
    never bump task modified_at, so the normal comparison can't see them)."""
    if not gids:
        return 0
    placeholders = ",".join("?" for _ in gids)
    try:
        cur = conn.execute(
            f"""UPDATE asana_task_extras SET for_modified_at=''
                WHERE task_id IN (SELECT task_id FROM enablement_tasks
                                  WHERE source='asana' AND source_ref IN ({placeholders}))""",
            tuple(gids),
        )
        conn.commit()
        return cur.rowcount
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise


def drain_stale(conn, client, *, cap: int | None = None,
                payload_cache: dict | None = None) -> int:
    """Refresh extras for up to ``cap`` stale tasks. ``payload_cache``
    (gid → already-fetched task payload, filled by the events path) saves the
    re-fetch; misses cost one get_task + the two best-effort reads."""
    cap = extras_cap() if cap is None else cap
    payload_cache = payload_cache or {}
    done = 0
    for task_id, gid in stale_task_ids(conn, limit=cap):
        payload = payload_cache.get(gid)
        if payload is None:
            try:
                from src.data.asana_client import _TASK_FIELDS
                payload = client.get_task(gid, opt_fields=_TASK_FIELDS)
            except Exception as exc:  # noqa: BLE001
                logger.debug("extras task fetch failed for %s: %s", gid, exc)
                continue
        try:
            upsert_extras(conn, task_id, payload, client=client)
            done += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("extras upsert failed for %s: %s", task_id, exc)
    return done
