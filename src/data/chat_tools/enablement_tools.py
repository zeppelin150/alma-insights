"""Enablement chat tools for the Gemini/MCP path.

Mirrors the Claude-path tools (src/llm/claude_tools.py): let the chat find
locally-stored enablement documents/drafts and query the business Google Drive.
Handlers use the registry signature (conn, args, filters) -> dict.
"""

from __future__ import annotations


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


def _push_guru_draft_impl(conn, draft_id, collection_id=None) -> dict:
    from src.data import enablement_store as store
    try:
        did = int(draft_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "draft_id_required"}
    if not collection_id:
        # Renn rarely knows the Guru collection — fall back to the operator's
        # configured publish target so chat-initiated pushes land correctly.
        try:
            from src.data.settings_manager import get_section
            collection_id = ((get_section("enablement", {}) or {}).get("guru")
                             or {}).get("publish_collection_id") or None
        except Exception:
            collection_id = None
    client = None
    try:
        from src.data.guru_client import GuruClient
        email, token = GuruClient.load_credentials()
        if email and token:
            client = GuruClient(email, token)
    except Exception:  # noqa: BLE001 — no creds → local mark-pushed
        client = None
    return store.publish_draft(conn, did, guru_client=client, collection_id=collection_id)


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
    return {"ok": tasks.update_task(conn, str(task_id), **(fields or {}))}


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
    return _push_guru_draft_impl(conn, args.get("draft_id"), args.get("collection_id"))


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
