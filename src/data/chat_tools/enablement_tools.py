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
    return {"ok": True, "count": len(cols),
            "collections": [{"id": c["id"], "name": c["name"]} for c in cols]}


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


def _search_guru_cards_impl(conn, query, *, collections=None, limit=10) -> dict:
    """Live Guru card search (POST /search/cardmgr). Returns id/title/snippet."""
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"ok": False, "error": "guru_not_connected"}
    try:
        cards = GuruClient(email, token).search_cards(query or "")
    except Exception as exc:  # noqa: BLE001 — surface as a failed search
        return {"ok": False, "error": str(exc)[:160]}
    allowed = {c.lower() for c in (collections or [])}
    out = []
    for c in cards:
        coll = (c.get("collection", "") or "").lower()
        coll_id = (c.get("collection_id", "") or "").lower()
        if allowed and coll not in allowed and coll_id not in allowed:
            continue
        out.append({"card_id": c.get("id"), "title": c.get("title"),
                    "collection": c.get("collection"),
                    "url": _card_url(c.get("id")),
                    "snippet": _strip_html(c.get("content", ""))[:200]})
        if len(out) >= int(limit or 10):
            break
    return {"ok": True, "query": query, "count": len(out), "cards": out}


def handle_search_guru_cards(conn, args, session_filters) -> dict:
    return _search_guru_cards_impl(conn, args.get("query", ""),
                                   collections=args.get("collections"),
                                   limit=args.get("limit", 10))


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


def _search_content_impl(conn, query, *, limit=5) -> dict:
    """Deterministic hybrid search over the catalog summaries (torch-free)."""
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


def handle_search_content(conn, args, session_filters) -> dict:
    return _search_content_impl(conn, args.get("query", ""), limit=args.get("limit", 5))


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
