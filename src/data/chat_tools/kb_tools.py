"""Chat-tool handlers for the Drive knowledge base (WS2, renn-calendar-kb-studio).

New-module rule (the seven-file merge-hotspot fix): KB tools live HERE, not in
enablement_tools.py. Handlers follow the registry contract
``handle_*(conn, args, filters) -> dict``.

Invariant 1 discipline: the MCP tool subprocess has NO Qt loop and
``google_oauth`` hard-raises under ALMA_MCP_MODE — so NO tool in this module
touches Drive. ``index_drive_folder`` only ENQUEUES (kb_queue + an agent_jobs
row for live sidebar progress); the main-process KBWorker executes on its next
tick. Pre-checks use ONLY settings/DB state that IS readable here, so a
disabled/demo/unbootstrapped KB refuses with a plain actionable message
instead of minting forever-stuck jobs (the pre-mortem dead-end fix).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone


def _active_session_id() -> str | None:
    path = os.environ.get("ALMA_CHAT_SESSION_FILE", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kb_precheck(conn) -> dict | None:
    """Subprocess-safe readiness check. None = proceed; dict = refusal."""
    try:
        from src.data.settings_manager import get_section
        en = get_section("enablement", {}) or {}
    except Exception:  # noqa: BLE001
        en = {}
    if en.get("demo_mode", True):
        return {"ok": False, "error": "demo_mode",
                "message": "The app is in demo mode — live KB indexing is off. "
                           "Ask the operator to disable demo mode in Settings."}
    kb = en.get("kb") or {}
    if not kb.get("enabled", False):
        return {"ok": False, "error": "kb_disabled",
                "message": "The knowledge base is disabled — ask the operator "
                           "to enable it in Settings."}
    if not kb.get("ec_folder_id"):
        return {"ok": False, "error": "ec_not_bootstrapped",
                "message": "The EC folder hasn't been bootstrapped — ask the "
                           "operator to run KB bootstrap in Settings."}
    return None


def handle_kb_search(conn, args: dict, filters: dict) -> dict:
    """Ranked hybrid search over the knowledge base (query-ranked — for
    complete enumeration use kb_list_topics / kb_list_cards)."""
    from src.data.kb.search import kb_search
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query_required",
                "message": "kb_search needs a query string."}
    topics = args.get("topics") if isinstance(args.get("topics"), list) else None
    results = kb_search(conn, query, topics=topics,
                        type=(args.get("type") or "").strip() or None,
                        limit=int(args.get("limit", 8)))
    if not results:
        from src.data.kb import store
        if store.cards_count(conn) == 0:
            return {"ok": True, "results": [], "note":
                    "The knowledge base is empty — index_drive_folder builds it."}
    return {"ok": True, "results": results, "count": len(results)}


def handle_kb_list_topics(conn, args: dict, filters: dict) -> dict:
    """COMPLETE enumeration of EC topics with card counts (LIST contract)."""
    rows = conn.execute(
        """SELECT f.topic, f.folder_id, f.status, COUNT(c.card_id) AS n,
                  MAX(c.synced_at) AS last
           FROM kb_folders f LEFT JOIN kb_cards c ON c.topic_folder_id = f.folder_id
           WHERE f.role='topic' GROUP BY f.folder_id ORDER BY f.topic""").fetchall()
    return {"ok": True, "topics": [
        {"topic": r[0], "card_count": r[3], "last_updated": r[4] or "",
         "quarantined": r[2] != "ok"} for r in rows]}


def handle_kb_list_cards(conn, args: dict, filters: dict) -> dict:
    """COMPLETE enumeration of the cards in one topic (LIST contract).
    An unknown topic steers with the available topics — never a dead end."""
    from src.data.kb import store
    topic = str(args.get("topic") or "").strip().lower()
    if not topic:
        return {"ok": False, "error": "topic_required",
                "message": "Pass a topic slug — kb_list_topics enumerates them."}
    row = conn.execute("SELECT folder_id FROM kb_folders WHERE role='topic' "
                       "AND topic=?", (topic,)).fetchone()
    if row is None:
        available = [r[0] for r in conn.execute(
            "SELECT topic FROM kb_folders WHERE role='topic' ORDER BY topic").fetchall()]
        return {"ok": False, "error": "unknown_topic",
                "message": f"No topic '{topic}'.",
                "available_topics": available}
    cards = store.list_cards(conn, topic_folder_id=row[0],
                             type=(args.get("type") or "").strip() or None)
    return {"ok": True, "topic": topic, "cards": [
        {"card_id": c["card_id"], "title": c.get("title"), "type": c.get("type"),
         "summary": c.get("summary") or "", "status": c.get("status")}
        for c in cards], "count": len(cards)}


def handle_kb_get_card(conn, args: dict, filters: dict) -> dict:
    """Full card (frontmatter fields + body) from the mirror. An unknown id
    steers with the nearest title matches (weak models hallucinate ids)."""
    from src.data.kb import store
    card_id = str(args.get("card_id") or "").strip()
    card = store.get_card(conn, card_id) if card_id else None
    if card is None:
        nearest = []
        try:
            from src.data.kb.search import kb_search
            nearest = kb_search(conn, card_id or "card", limit=3)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": "card_not_found",
                "message": f"No card '{card_id}'.",
                "nearest": [{"card_id": n.get("card_id"), "title": n.get("title")}
                            for n in nearest if n.get("card_id")]}
    return {"ok": True, "card": {
        "card_id": card["card_id"], "title": card.get("title"),
        "type": card.get("type"), "topics": card.get("topics") or [],
        "summary": card.get("summary") or "",
        "key_facts": card.get("key_facts") or [],
        "source_id": card.get("source_id") or "",
        "source_url": card.get("source_url") or "",
        "source_modified": card.get("source_modified") or "",
        "body_md": (card.get("body_md") or "")[:8000],
        "status": card.get("status")}}


def handle_index_drive_folder(conn, args: dict, filters: dict) -> dict:
    """Queue an 'index this Drive folder' job (enqueue-ONLY — the KBWorker
    executes it main-process on the next sync tick, Google-connected)."""
    blocked = _kb_precheck(conn)
    if blocked:
        return blocked
    folder_id = str(args.get("folder_id") or "").strip()
    if not folder_id:
        return {"ok": False, "error": "folder_id_required",
                "message": ("I need a Drive folder id. Use request_drive_picker "
                            "so the operator can pick one (note: a picked "
                            "folder also becomes an actively watched folder).")}
    topic = str(args.get("topic") or "").strip()

    from src.data import agent_jobs
    job_id = agent_jobs.create_job(
        conn, title=f"Index Drive folder {folder_id[:12]}…",
        kind="kb_index", session_id=_active_session_id(),
        steps=["queued (runs on the next KB sync tick)", "index"])
    try:
        conn.execute(
            "INSERT INTO kb_queue (kind, target, payload_json, job_id, created_at) "
            "VALUES ('index_folder', ?, ?, ?, ?)",
            (folder_id, __import__("json").dumps({"topic": topic}), job_id, _now()))
        conn.commit()
    except Exception:  # noqa: BLE001 — unique pending index = already queued
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        agent_jobs.update_job(conn, job_id, status="done", progress_pct=100,
                              summary="already queued")
        return {"ok": True, "job_id": job_id, "queued": False,
                "message": "That folder is already queued for indexing."}
    return {"ok": True, "job_id": job_id, "queued": True,
            "message": ("Queued. Indexing runs on the next KB sync tick "
                        "(requires Google connected this session); progress "
                        "shows in the jobs sidebar.")}
