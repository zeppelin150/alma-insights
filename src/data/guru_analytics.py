"""Guru analytics — sync engine + read API for the Analytics page.

Sync pulls usage events (watermark + 1-day overlap, deduped via a
content-derived event key), a team-stats snapshot, the verification
queue, and open comments for the most-active cards. Each section is
failure-isolated so one endpoint outage never zeroes the page; the
events watermark only advances on success. Aggregates (top cards, KPIs,
due cards) are computed at read time — volumes are small at a 90-day
window.

All functions take a sqlite3 connection from connection_factory; chunked
writes each use their own ``atomic()`` (never nested).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.data.connection_factory import atomic

EVENT_WINDOW_DAYS = 90          # rolling retention for raw events
DUE_SOON_DAYS = 14              # "verification due" horizon
COMMENT_CARD_CAP = 50           # most-active cards whose comments sync


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime | None:
    """Tolerant ISO parse (Guru mixes '+0000', 'Z' and millis forms)."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if len(v) >= 5 and (v[-5] in "+-") and v[-3] != ":":
        v = v[:-2] + ":" + v[-2:]
    for candidate in (v, v[:19]):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _event_key(ev: dict) -> str:
    """Guru events carry no id — derive a stable key for overlap dedup.

    sha1 is fine here: dedup, not security (bandit B324 precedent).
    """
    props = ev.get("properties") or {}
    base = "|".join((
        str(ev.get("type", "")),
        str(ev.get("user", "")),
        str(ev.get("eventDate", "")),
        str(props.get("cardId", "")),
    ))
    return hashlib.sha1(base.encode("utf-8"), usedforsecurity=False).hexdigest()


# ── sync state KV ─────────────────────────────────────────────────────

def _get_state(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute(
        "SELECT value FROM guru_sync_state WHERE key = ?", (key,)
    ).fetchone()
    return (row[0] if row else "") or ""


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    with atomic(conn):
        conn.execute(
            "INSERT INTO guru_sync_state(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (key, value, _now()),
        )


def ensure_team_id(conn: sqlite3.Connection, client) -> str:
    """Cached team id (whoami/teams discovery on first use)."""
    cached = _get_state(conn, "team_id")
    if cached:
        return cached
    team_id = ""
    try:
        team_id = client.get_team_id() or ""
    except Exception:
        team_id = ""
    if team_id:
        _set_state(conn, "team_id", team_id)
    return team_id


def last_sync_at(conn: sqlite3.Connection) -> str:
    return _get_state(conn, "last_sync_at")


# ── sync ──────────────────────────────────────────────────────────────

def sync(conn: sqlite3.Connection, client, *, days_back: int = 30,
         progress=None) -> dict:
    """Pull events / stats / verification / comments into the local store."""

    def _tick(msg: str):
        if progress is not None:
            try:
                progress(msg)
            except Exception:
                pass

    summary: dict = {"ok": True, "sections": {}}
    team_id = ensure_team_id(conn, client)
    if not team_id:
        return {"ok": False, "error": "no_team_id",
                "sections": {}}

    # 1. Events (watermark − 1 day overlap; event_key dedups the overlap)
    _tick("Syncing usage events…")
    try:
        watermark = _get_state(conn, "events_watermark")
        wm_dt = _parse_dt(watermark)
        if wm_dt is not None:
            from_date = (wm_dt - timedelta(days=1)).isoformat()
        else:
            from_date = _iso_days_ago(days_back)
        events = client.get_analytics(team_id, from_date=from_date)
        inserted = 0
        max_date = watermark
        now = _now()
        with atomic(conn):
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                props = ev.get("properties") or {}
                cur = conn.execute(
                    "INSERT OR IGNORE INTO guru_events "
                    "(event_key, event_type, user_email, event_date, card_id, "
                    " properties, fetched_at) VALUES (?,?,?,?,?,?,?)",
                    (_event_key(ev), ev.get("type", ""), ev.get("user", ""),
                     ev.get("eventDate", ""), str(props.get("cardId", "") or ""),
                     json.dumps(props), now),
                )
                inserted += max(cur.rowcount, 0)
                d = ev.get("eventDate", "")
                if d and d > max_date:
                    max_date = d
            conn.execute(
                "DELETE FROM guru_events WHERE event_date < ?",
                (_iso_days_ago(EVENT_WINDOW_DAYS),),
            )
        if max_date and max_date != watermark:
            _set_state(conn, "events_watermark", max_date)
        summary["sections"]["events"] = {
            "fetched": len(events), "inserted": inserted,
        }
    except Exception as exc:  # noqa: BLE001 — section isolation
        summary["ok"] = False
        summary["sections"]["events"] = {"error": str(exc)}

    # 2. Team stats snapshot
    _tick("Snapshotting team stats…")
    try:
        stats = client.get_team_stats(team_id) or {}
        if stats:
            with atomic(conn):
                conn.execute(
                    "INSERT OR REPLACE INTO guru_team_stats(snapshot_at, stats_json) "
                    "VALUES (?,?)", (_now(), json.dumps(stats)),
                )
        summary["sections"]["stats"] = {"ok": bool(stats)}
    except Exception as exc:  # noqa: BLE001
        summary["ok"] = False
        summary["sections"]["stats"] = {"error": str(exc)}

    # 3. Verification queue (full refresh — the table mirrors the queue)
    _tick("Refreshing verification queue…")
    try:
        cards = client.list_unverified_cards() or []
        now = _now()
        with atomic(conn):
            conn.execute("DELETE FROM guru_card_verification")
            for c in cards:
                conn.execute(
                    "INSERT OR REPLACE INTO guru_card_verification "
                    "(card_id, title, collection_id, collection_name, "
                    " verification_state, verification_reason, "
                    " next_verification_date, verification_interval, "
                    " last_verified_at, last_modified, comment_count, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (c.get("id", ""), c.get("title", ""),
                     c.get("collection_id", ""), c.get("collection", ""),
                     c.get("verification_state", ""),
                     c.get("verification_reason", ""),
                     c.get("next_verification_date", ""),
                     c.get("verification_interval", ""),
                     c.get("last_verified", ""), c.get("last_modified", ""),
                     int(c.get("comment_count") or 0), now),
                )
        summary["sections"]["verification"] = {"cards": len(cards)}
    except Exception as exc:  # noqa: BLE001
        summary["ok"] = False
        summary["sections"]["verification"] = {"error": str(exc)}

    # 4. Open comments for the most-active cards (capped)
    _tick("Fetching open comments…")
    try:
        cutoff = _iso_days_ago(days_back)
        active = [r[0] for r in conn.execute(
            "SELECT card_id FROM guru_events "
            "WHERE card_id != '' AND event_date >= ? "
            "GROUP BY card_id ORDER BY COUNT(*) DESC LIMIT ?",
            (cutoff, COMMENT_CARD_CAP),
        ).fetchall()]
        synced = 0
        now = _now()
        for card_id in active:
            comments = client.get_card_comments(card_id, status="OPEN") or []
            title_row = conn.execute(
                "SELECT COALESCE("
                "  (SELECT title FROM guru_card_verification WHERE card_id=?),"
                "  (SELECT title FROM guru_articles WHERE card_id=?), '')",
                (card_id, card_id),
            ).fetchone()
            card_title = title_row[0] if title_row else ""
            fetched_ids = [c.get("id", "") for c in comments if c.get("id")]
            with atomic(conn):
                for c in comments:
                    if not c.get("id"):
                        continue
                    conn.execute(
                        "INSERT INTO guru_card_comments "
                        "(comment_id, card_id, card_title, author, text, "
                        " created_at, status, fetched_at) "
                        "VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(comment_id) DO UPDATE SET "
                        "  text=excluded.text, status=excluded.status, "
                        "  card_title=excluded.card_title, "
                        "  fetched_at=excluded.fetched_at",
                        (c["id"], card_id, card_title, c.get("author", ""),
                         c.get("content", ""), c.get("created_at", ""),
                         c.get("status", "OPEN"), now),
                    )
                    synced += 1
                # Anything we previously held as OPEN for this card but the
                # API no longer returns as OPEN got resolved in Guru.
                placeholders = ",".join("?" * len(fetched_ids)) or "''"
                conn.execute(
                    f"UPDATE guru_card_comments SET status='RESOLVED' "
                    f"WHERE card_id=? AND status='OPEN' "
                    f"AND comment_id NOT IN ({placeholders})",
                    [card_id, *fetched_ids],
                )
        summary["sections"]["comments"] = {
            "cards_checked": len(active), "synced": synced,
        }
    except Exception as exc:  # noqa: BLE001
        summary["ok"] = False
        summary["sections"]["comments"] = {"error": str(exc)}

    _set_state(conn, "last_sync_at", _now())
    _tick("Sync complete.")
    return summary


# ── read API ──────────────────────────────────────────────────────────

def top_cards(conn: sqlite3.Connection, *, days: int = 30,
              collection_id: str | None = None, domain: str | None = None,
              limit: int = 20) -> list[dict]:
    """Most-used cards in the window, joinable to collection/domain."""
    cutoff = _iso_days_ago(days)
    sql = (
        "SELECT e.card_id, "
        "  COALESCE(NULLIF(v.title,''), NULLIF(a.title,''), e.card_id) AS title, "
        "  COALESCE(NULLIF(v.collection_name,''), NULLIF(a.collection_name,''), '') AS collection, "
        "  SUM(CASE WHEN e.event_type='card-viewed' THEN 1 ELSE 0 END) AS views, "
        "  SUM(CASE WHEN e.event_type='card-copied' THEN 1 ELSE 0 END) AS copies, "
        "  COUNT(*) AS events "
        "FROM guru_events e "
        "LEFT JOIN guru_card_verification v ON v.card_id = e.card_id "
        "LEFT JOIN guru_articles a ON a.card_id = e.card_id "
        "WHERE e.card_id != '' AND e.event_date >= ? "
    )
    params: list = [cutoff]
    if collection_id:
        sql += "AND (v.collection_id = ? OR a.collection_id = ?) "
        params += [collection_id, collection_id]
    if domain:
        sql += ("AND e.card_id IN "
                "(SELECT card_id FROM guru_card_domains WHERE domain = ?) ")
        params.append(domain)
    sql += "GROUP BY e.card_id ORDER BY views DESC, events DESC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # guru_articles/domains may not exist on a minimal DB — degrade
        rows = conn.execute(
            "SELECT card_id, card_id, '', "
            "  SUM(CASE WHEN event_type='card-viewed' THEN 1 ELSE 0 END), "
            "  SUM(CASE WHEN event_type='card-copied' THEN 1 ELSE 0 END), "
            "  COUNT(*) FROM guru_events "
            "WHERE card_id != '' AND event_date >= ? "
            "GROUP BY card_id ORDER BY 4 DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [
        {"card_id": r[0], "title": r[1], "collection": r[2],
         "views": r[3], "copies": r[4], "events": r[5]}
        for r in rows
    ]


def verification_kpis(conn: sqlite3.Connection) -> dict:
    states: dict = {}
    for state, n in conn.execute(
        "SELECT COALESCE(verification_state,''), COUNT(*) "
        "FROM guru_card_verification GROUP BY 1"
    ).fetchall():
        states[state or "UNKNOWN"] = n
    due_cutoff = (datetime.now(timezone.utc)
                  + timedelta(days=DUE_SOON_DAYS)).isoformat()
    due_soon = conn.execute(
        "SELECT COUNT(*) FROM guru_card_verification "
        "WHERE next_verification_date != '' AND next_verification_date <= ?",
        (due_cutoff,),
    ).fetchone()[0]
    open_comments_n = conn.execute(
        "SELECT COUNT(*) FROM guru_card_comments WHERE status='OPEN'"
    ).fetchone()[0]
    stats_row = conn.execute(
        "SELECT stats_json FROM guru_team_stats ORDER BY snapshot_at DESC LIMIT 1"
    ).fetchone()
    try:
        team_stats = json.loads(stats_row[0]) if stats_row else {}
    except Exception:
        team_stats = {}
    return {"states": states, "queue_total": sum(states.values()),
            "due_soon": due_soon, "open_comments": open_comments_n,
            "team_stats": team_stats,
            "last_sync_at": last_sync_at(conn)}


def open_comments(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT comment_id, card_id, card_title, author, text, created_at, "
        "       task_id FROM guru_card_comments WHERE status='OPEN' "
        "ORDER BY created_at DESC LIMIT ?", (limit,),
    ).fetchall()
    return [
        {"comment_id": r[0], "card_id": r[1], "card_title": r[2],
         "author": r[3], "text": r[4], "created_at": r[5], "task_id": r[6]}
        for r in rows
    ]


def collections(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(collection_id, name) pairs present in the local analytics tables."""
    rows = conn.execute(
        "SELECT DISTINCT collection_id, collection_name "
        "FROM guru_card_verification "
        "WHERE collection_id != '' ORDER BY collection_name"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def domains(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT DISTINCT domain FROM guru_card_domains ORDER BY domain"
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    except sqlite3.OperationalError:
        return []


def cards_due_for_update(conn: sqlite3.Connection, *, days: int = 30,
                         stale_days: int = 90, limit: int = 20) -> list[dict]:
    """Cards needing attention, for the calendar + targeted updates.

    Reasons (priority order): unverified | verification_due |
    stale_high_traffic (top-viewed but not modified in stale_days).
    """
    now = datetime.now(timezone.utc)
    due_cutoff = (now + timedelta(days=DUE_SOON_DAYS)).isoformat()
    out: dict[str, dict] = {}

    for r in conn.execute(
        "SELECT card_id, title, collection_name, next_verification_date, "
        "       verification_state FROM guru_card_verification "
        "WHERE verification_state IN ('NEEDS_VERIFICATION', 'STALE')"
    ).fetchall():
        out[r[0]] = {
            "card_id": r[0], "title": r[1], "collection": r[2],
            "reason": "unverified",
            "due_date": (r[3] or now.date().isoformat())[:10],
        }

    for r in conn.execute(
        "SELECT card_id, title, collection_name, next_verification_date "
        "FROM guru_card_verification "
        "WHERE next_verification_date != '' AND next_verification_date <= ?",
        (due_cutoff,),
    ).fetchall():
        out.setdefault(r[0], {
            "card_id": r[0], "title": r[1], "collection": r[2],
            "reason": "verification_due", "due_date": (r[3] or "")[:10],
        })

    stale_cutoff = (now - timedelta(days=stale_days)).isoformat()
    for card in top_cards(conn, days=days, limit=20):
        cid = card["card_id"]
        if cid in out:
            continue
        row = conn.execute(
            "SELECT last_modified FROM guru_card_verification WHERE card_id=?",
            (cid,),
        ).fetchone()
        last_mod = row[0] if row else ""
        if last_mod and last_mod < stale_cutoff:
            out[cid] = {
                "card_id": cid, "title": card["title"],
                "collection": card["collection"],
                "reason": "stale_high_traffic",
                "due_date": (now + timedelta(days=7)).date().isoformat(),
            }

    return list(out.values())[:limit]


def create_task_from_comment(conn: sqlite3.Connection, comment_id: str) -> dict:
    """Convert an open Guru comment into an enablement task (idempotent)."""
    row = conn.execute(
        "SELECT comment_id, card_id, card_title, author, text, task_id "
        "FROM guru_card_comments WHERE comment_id = ?", (comment_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "comment_not_found"}
    if row[5]:
        return {"ok": True, "task_id": row[5], "already": True}

    from src.data import enablement_tasks
    title = f"Guru comment on “{row[2] or row[1]}”"
    task_id = enablement_tasks.create_task(
        conn,
        source="guru",
        kind="card_comment",
        title=title,
        source_ref=row[1],
        summary=f"{row[3] or 'Someone'}: {(row[4] or '')[:300]}",
        key=f"guru_comment:{comment_id}",
    )
    with atomic(conn):
        conn.execute(
            "UPDATE guru_card_comments SET task_id=? WHERE comment_id=?",
            (task_id, comment_id),
        )
    return {"ok": True, "task_id": task_id}
