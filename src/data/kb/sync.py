"""KB two-way sync engine (WS2-M3, renn-calendar-kb-studio plan).

Rules (pre-mortem-hardened):
- PULL WINS, ABSOLUTELY. Drive is source of truth for card content; humans may
  hand-edit cards there. On any push conflict signal the push is DISCARDED and
  the Drive version pulled — never re-push the same content twice in a row.
- Repair is FLAG-ONLY for files the app didn't create: a hand-dropped card
  with broken YAML gets mirror status ``needs_repair`` — the sync never
  rewrites a human's file (drive.file would 403 on foreign files anyway).
- ECHO SUPPRESSION: every push records the update-response modifiedTime as
  ``kb_cards.drive_modified``; the pull skips files whose modifiedTime equals
  it, so the app's own writes don't re-ingest (and can't ping-pong).
- Deletions are invisible to modifiedTime cursors — ``full_reconcile``
  enumerates the EC tree periodically and drops mirror rows whose Drive file
  vanished (mirror-only; the app never deletes in Drive).
- The per-topic ``_index.md`` head file (owner request) is regenerated
  DETERMINISTICALLY from the mirror after each sync cycle — code, not model —
  so every EC folder is human-browsable at a glance.

Qt-free; fresh connection per worker tick; plain execute+commit.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.data.kb import card_format, drive_kb, store

logger = logging.getLogger("alma.kb.sync")

INDEX_FILENAME = "_index.md"
_LEASE_MINUTES = 15
_MAX_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_sync(conn, folder_id: str, *, status: str, cursor: str | None = None,
               error: str | None = None) -> None:
    conn.execute(
        """INSERT INTO kb_sync_state (folder_id, cursor, last_run_at, last_status, last_error)
           VALUES (?, COALESCE(?, ''), ?, ?, ?)
           ON CONFLICT(folder_id) DO UPDATE SET
             cursor=COALESCE(?, kb_sync_state.cursor),
             last_run_at=excluded.last_run_at,
             last_status=excluded.last_status, last_error=excluded.last_error""",
        (folder_id, cursor, _now(), status, error, cursor))
    conn.commit()


def pull_folder(conn, folder_id: str, *, reader) -> int:
    """Pull changed card files from ONE EC folder into the mirror. Returns
    the number of cards absorbed."""
    row = conn.execute("SELECT cursor FROM kb_sync_state WHERE folder_id=?",
                       (folder_id,)).fetchone()
    cursor = (row[0] if row else "") or None
    try:
        files = reader.list_changed_files(folder_id, modified_after=cursor,
                                          recursive=False) or []
    except Exception as exc:  # noqa: BLE001
        _mark_sync(conn, folder_id, status="error", error=str(exc)[:200])
        return 0
    absorbed = 0
    newest = cursor or ""
    for f in files:
        modified = f.get("modified_time") or f.get("modifiedTime") or ""
        if modified and modified > newest:
            newest = modified
        name = f.get("name") or ""
        if not name.endswith(".md") or name == INDEX_FILENAME:
            continue
        mirror = store.get_card_by_file(conn, f.get("id"))
        if mirror is not None and modified and mirror.get("drive_modified") == modified:
            continue                                     # our own write — echo
        try:
            text = reader.export_text(f.get("id"), f.get("mime_type")
                                      or f.get("mimeType") or "text/markdown")
        except Exception as exc:  # noqa: BLE001
            logger.debug("card read failed for %s: %s", f.get("id"), exc)
            continue
        meta, body, issues = card_format.parse_card(text)
        card_id = (str(meta.get("card_id") or "").strip()
                   or card_format.card_id_from_filename(name)
                   or (mirror or {}).get("card_id")
                   or card_format.new_card_id())
        meta["card_id"] = card_id
        meta.setdefault("title", name[:-3])
        status = "needs_repair" if issues else "ok"
        # Capture the human's own frontmatter fields so they survive the DB
        # round-trip and are written back on the next push (finding 17).
        store.upsert_card(conn, meta, body, drive_file_id=f.get("id"),
                          topic_folder_id=folder_id, drive_modified=modified,
                          status=status, extra=card_format.extract_extra(meta))
        drive_kb._log(conn, "pull", card_id=card_id, drive_file_id=f.get("id"),
                      detail=("issues: " + ",".join(issues)) if issues else "")
        absorbed += 1
    _mark_sync(conn, folder_id, status="ok", cursor=newest or None)
    return absorbed


def pull_all(conn, *, reader) -> int:
    total = 0
    for f in drive_kb.topic_folders(conn):
        if f["status"] == "ok":
            total += pull_folder(conn, f["folder_id"], reader=reader)
    root = drive_kb.ec_root_id(conn)
    if root:
        total += pull_folder(conn, root, reader=reader)
    return total


def push_pending(conn, *, exporter=None, reader=None, limit: int = 10) -> int:
    """Drain queued ``write_card`` rows (claim → write → done). Check-and-set:
    a Drive-side change since our recorded drive_modified discards the push
    (pull wins) — the row completes as 'done' with a conflict log entry."""
    rows = conn.execute(
        "SELECT queue_id, target, payload_json FROM kb_queue "
        "WHERE kind='write_card' AND status='pending' "
        "ORDER BY queue_id LIMIT ?", (limit,)).fetchall()
    pushed = 0
    for queue_id, card_id, payload_json in rows:
        claimed = conn.execute(
            "UPDATE kb_queue SET status='claimed', claimed_at=? "
            "WHERE queue_id=? AND status='pending'", (_now(), queue_id))
        conn.commit()
        if claimed.rowcount != 1:
            continue                                     # another worker won
        try:
            done = _push_one(conn, card_id, exporter=exporter, reader=reader)
            conn.execute("UPDATE kb_queue SET status=? WHERE queue_id=?",
                         ("done" if done else "error", queue_id))
            conn.commit()
            pushed += 1 if done else 0
        except drive_kb.KBWriteDenied:
            conn.execute("UPDATE kb_queue SET status='error' WHERE queue_id=?",
                         (queue_id,))
            conn.commit()
            raise
        except Exception as exc:  # noqa: BLE001 — leave claimed; the lease retries
            logger.warning("card push failed for %s: %s", card_id, exc)
    return pushed


def _push_one(conn, card_id: str, *, exporter=None, reader=None) -> bool:
    card = store.get_card(conn, card_id)
    if card is None:
        return False
    folder_id = card.get("topic_folder_id") or drive_kb.ec_root_id(conn)
    if not folder_id:
        return False
    # Check-and-set (Drive has no compare-and-swap; this is best-effort with
    # pull-wins as the real guarantee).
    file_id = card.get("drive_file_id")
    if file_id and reader is not None:
        try:
            meta = reader.get_file(file_id)
            remote = meta.get("modified_time") or ""
            if remote and card.get("drive_modified") and remote != card["drive_modified"]:
                drive_kb._log(conn, "conflict", card_id=card_id,
                              drive_file_id=file_id,
                              detail="Drive changed since last sync — pull wins")
                pull_folder(conn, folder_id, reader=reader)
                return True                               # push discarded by design
        except Exception:  # noqa: BLE001 — CAS read is protection only
            pass
    meta_out = {k: card.get(k) for k in ("card_id", "title", "type", "source_id",
                                         "source_url", "source_mime",
                                         "source_modified", "summary")}
    meta_out["topics"] = card.get("topics") or []
    meta_out["key_facts"] = card.get("key_facts") or []
    # Merge back the human's own frontmatter fields so a Drive rewrite keeps
    # them (finding 17). extra holds only non-schema keys, so it can't shadow
    # the canonical fields above.
    for k, v in (card.get("extra") or {}).items():
        meta_out.setdefault(k, v)
    text = card_format.serialize_card(meta_out, card.get("body_md") or "")
    res = drive_kb.write_card_file(
        conn, folder_id, card_format.card_filename(card.get("title"), card_id),
        text, card_id=card_id, exporter=exporter)
    conn.execute(
        "UPDATE kb_cards SET drive_file_id=?, drive_modified=?, synced_at=? "
        "WHERE card_id=?",
        (res["drive_file_id"], res.get("modified_time") or "", _now(), card_id))
    conn.commit()
    return True


def drain_index_jobs(conn, *, reader=None, exporter=None, llm_client=None,
                     limit: int = 2) -> int:
    """Execute queued ``index_folder`` jobs (claim → ingest → done). Bounded
    per tick — one giant folder can't monopolize the worker thread."""
    from src.data import agent_jobs
    from src.data.kb import ingest
    rows = conn.execute(
        "SELECT queue_id, target, job_id FROM kb_queue "
        "WHERE kind='index_folder' AND status='pending' "
        "ORDER BY queue_id LIMIT ?", (limit,)).fetchall()
    done = 0
    for queue_id, folder_id, job_id in rows:
        claimed = conn.execute(
            "UPDATE kb_queue SET status='claimed', claimed_at=? "
            "WHERE queue_id=? AND status='pending'", (_now(), queue_id))
        conn.commit()
        if claimed.rowcount != 1:
            continue
        try:
            res = ingest.index_folder(conn, folder_id, reader=reader,
                                      exporter=exporter, llm_client=llm_client,
                                      job_id=job_id)
            status = "done" if res.get("ok") else "error"
            conn.execute("UPDATE kb_queue SET status=? WHERE queue_id=?",
                         (status, queue_id))
            conn.commit()
            if job_id:
                if res.get("ok"):
                    agent_jobs.update_job(
                        conn, job_id, status="done", progress_pct=100,
                        summary=(f"{res.get('indexed', 0)} docs indexed, "
                                 f"{res.get('skipped', 0)} unchanged"
                                 + (" (truncated — more on the next run)"
                                    if res.get("truncated") else "")))
                elif res.get("error") != "cancelled":
                    agent_jobs.update_job(conn, job_id, status="error",
                                          error=str(res.get("error"))[:200])
            done += 1
        except Exception as exc:  # noqa: BLE001 — lease retries it
            logger.warning("index job failed for %s: %s", folder_id, exc)
    return done


def drain_distill_jobs(conn, *, exporter=None, limit: int = 5) -> int:
    """Execute queued published-card archives (WS2-M5)."""
    from src.data.kb import ingest
    rows = conn.execute(
        "SELECT queue_id, target FROM kb_queue "
        "WHERE kind='distill' AND status='pending' ORDER BY queue_id LIMIT ?",
        (limit,)).fetchall()
    done = 0
    for queue_id, draft_id in rows:
        claimed = conn.execute(
            "UPDATE kb_queue SET status='claimed', claimed_at=? "
            "WHERE queue_id=? AND status='pending'", (_now(), queue_id))
        conn.commit()
        if claimed.rowcount != 1:
            continue
        try:
            res = ingest.distill_draft(conn, draft_id, exporter=exporter)
            conn.execute("UPDATE kb_queue SET status=? WHERE queue_id=?",
                         ("done" if res.get("ok") else "error", queue_id))
            conn.commit()
            done += 1 if res.get("ok") else 0
        except Exception as exc:  # noqa: BLE001 — lease retries it
            logger.warning("distill failed for draft %s: %s", draft_id, exc)
    return done


def scan_stale_sources(conn, *, reader, exporter=None, llm_client=None,
                       cap: int = 20) -> dict:
    """Update detection (WS2-M5): for cards summarizing a Drive source, compare
    the SOURCE's live modifiedTime to the card's recorded source_modified.
    Stale → re-extract → re-summarize → diff digest → '## Changelog' append →
    re-queue the Drive push → an attention-queue task STAMPED WITH THE OPERATOR
    (cross-cutting: unassigned agent tasks vanish from the default 'Mine'
    calendar scope). A 404'd source marks the card source_missing (never
    deleted — the knowledge may still be useful)."""
    from src.data import enablement_tasks
    from src.data.kb import ingest
    from src.data.text_diff import diff_rows
    cards = conn.execute(
        """SELECT card_id FROM kb_cards
           WHERE status='ok' AND source_id != ''
             AND source_id NOT LIKE 'guru:%' AND source_id NOT LIKE 'asana:%'
             AND type='source_summary'
           ORDER BY synced_at ASC LIMIT ?""", (cap,)).fetchall()
    checked = refreshed = 0
    for (card_id,) in cards:
        card = store.get_card(conn, card_id)
        if not card:
            continue
        checked += 1
        try:
            meta = reader.get_file(card["source_id"])
        except Exception:  # noqa: BLE001 — deleted/unreachable source
            store.mark_status(conn, card_id, "source_missing")
            continue
        remote = meta.get("modified_time") or ""
        if not remote or remote <= (card.get("source_modified") or ""):
            store.upsert_card(conn, {  # touch synced_at so the scan rotates
                **{k: card.get(k) for k in ("card_id", "title", "type",
                                            "source_id", "source_url",
                                            "source_mime", "source_modified",
                                            "summary")},
                "topics": card.get("topics") or [],
                "key_facts": card.get("key_facts") or [],
            }, card.get("body_md") or "",
                drive_modified=card.get("drive_modified") or "")
            continue
        try:
            text = reader.export_text(card["source_id"],
                                      card.get("source_mime") or "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("stale re-extract failed for %s: %s", card_id, exc)
            continue
        fresh = ingest.summarize_doc(card.get("title") or "", text,
                                     llm_client=llm_client)
        changed = [r for r in diff_rows(card.get("summary") or "",
                                        fresh["summary"]) if r["tag"] != "equal"]
        digest = "; ".join(r["text"] for r in changed if r["tag"] == "add")[:300] \
            or "source content changed"
        body = (card.get("body_md") or "").rstrip()
        if "## Changelog" not in body:
            body += "\n\n## Changelog"
        body += f"\n- {_now()[:10]}: source updated — {digest}"
        meta_out = {
            "card_id": card_id, "title": card.get("title"),
            "type": card.get("type"), "topics": card.get("topics") or [],
            "source_id": card["source_id"], "source_url": card.get("source_url"),
            "source_mime": card.get("source_mime"), "source_modified": remote,
            "summary": fresh["summary"], "key_facts": fresh["key_facts"],
        }
        store.upsert_card(conn, meta_out, body,
                          topic_folder_id=card.get("topic_folder_id"))
        ingest._enqueue_write(conn, card_id)
        # Attention task — visible in the operator's default 'Mine' scope.
        try:
            from src.data import enablement_identity as ident
            tid = enablement_tasks.create_task(
                conn, source="kb", kind="card_review",
                title=f"Source updated: {card.get('title', 'card')}"[:120],
                source_ref=f"{card_id}@{remote}",
                source_url=card.get("source_url") or "",
                summary=digest, created_by="agent")
            upd = {}
            if ident.operator_asana_gid():
                upd["assignee_gid"] = ident.operator_asana_gid()
            if ident.operator_name():
                upd["assignee"] = ident.operator_name()
            if upd:
                enablement_tasks.update_task(conn, tid, **upd)
        except Exception as exc:  # noqa: BLE001 — the card refresh stands
            logger.debug("attention task failed for %s: %s", card_id, exc)
        drive_kb._log(conn, "update", card_id=card_id,
                      detail=f"source refreshed → {remote}")
        refreshed += 1
    return {"checked": checked, "refreshed": refreshed}


def expire_stale_pending(conn, *, days: int = 7) -> int:
    """Pending queue rows older than N days dead-letter (a disconnected
    session must not stockpile a reconnect flood); their jobs error out."""
    from datetime import timedelta
    from src.data import agent_jobs
    threshold = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT queue_id, job_id FROM kb_queue "
        "WHERE status='pending' AND created_at < ?", (threshold,)).fetchall()
    for queue_id, job_id in rows:
        conn.execute("UPDATE kb_queue SET status='dead' WHERE queue_id=?",
                     (queue_id,))
        if job_id:
            try:
                agent_jobs.update_job(conn, job_id, status="error",
                                      error="expired before Google connected")
            except Exception:  # noqa: BLE001
                pass
    conn.commit()
    return len(rows)


def reset_stale_claims(conn) -> int:
    """Claim leases: rows stuck 'claimed' past the lease window go back to
    pending (attempts+1); beyond max attempts they dead-letter."""
    from datetime import timedelta
    threshold = (datetime.now(timezone.utc)
                 - timedelta(minutes=_LEASE_MINUTES)).isoformat()
    cur = conn.execute(
        """UPDATE kb_queue
           SET status = CASE WHEN attempts + 1 >= ? THEN 'dead' ELSE 'pending' END,
               attempts = attempts + 1
           WHERE status='claimed' AND claimed_at < ?""",
        (_MAX_ATTEMPTS, threshold))
    conn.commit()
    return cur.rowcount


def full_reconcile(conn, *, reader, exporter=None) -> dict:
    """Periodic full pass: verify folders (trash/quarantine), drop mirror rows
    whose Drive card file vanished, regenerate _index.md head files."""
    dropped = 0
    for folder in drive_kb.topic_folders(conn) + (
            [{"folder_id": drive_kb.ec_root_id(conn), "status": "ok"}]
            if drive_kb.ec_root_id(conn) else []):
        fid = folder["folder_id"]
        if folder.get("status") != "ok" or not fid:
            continue
        try:
            listed = {f.get("id") for f in
                      reader.list_changed_files(fid, recursive=False) or []}
        except Exception as exc:  # noqa: BLE001
            logger.debug("reconcile list failed for %s: %s", fid, exc)
            continue
        for card in store.list_cards(conn, topic_folder_id=fid, limit=2000):
            if card.get("drive_file_id") and card["drive_file_id"] not in listed:
                store.delete_card(conn, card["card_id"])
                drive_kb._log(conn, "pull", card_id=card["card_id"],
                              detail="card file removed in Drive — mirror dropped")
                dropped += 1
        regenerate_index_md(conn, fid, exporter=exporter)
    return {"dropped": dropped}


def regenerate_index_md(conn, folder_id: str, *, exporter=None) -> bool:
    """The per-topic head file (owner request): a deterministic, human-first
    summary of what's in the folder, rebuilt from the mirror. An EC-internal
    write under the ratified gate exemption; idempotent via its own stable id."""
    cards = store.list_cards(conn, topic_folder_id=folder_id, limit=2000)
    cards = [c for c in cards if c.get("card_id", "").startswith("kb-")]
    row = conn.execute("SELECT topic, role FROM kb_folders WHERE folder_id=?",
                       (folder_id,)).fetchone()
    topic = (row[0] if row else "") or ("EC" if row and row[1] == "ec_root" else "")
    lines = [f"# Index — {topic or 'knowledge base'}", "",
             f"_{len(cards)} cards · auto-maintained by Renn · edits to THIS "
             f"file are overwritten (edit the cards instead)_", ""]
    for c in sorted(cards, key=lambda c: c.get("title") or ""):
        summary = (c.get("summary") or "").strip()
        lines.append(f"- **{c.get('title', 'card')}** ({c.get('type', '')}) — "
                     f"{summary[:160]}")
    content = "\n".join(lines) + "\n"
    try:
        drive_kb.write_card_file(conn, folder_id, INDEX_FILENAME, content,
                                 card_id=f"kb-index-{folder_id[:12]}",
                                 exporter=exporter)
        return True
    except Exception as exc:  # noqa: BLE001 — the head file is polish
        logger.debug("index regen failed for %s: %s", folder_id, exc)
        return False
