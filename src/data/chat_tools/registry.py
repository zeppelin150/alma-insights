"""Unified chat tool registry and dispatch.

All chat tools register here. The dispatcher merges session filters
with per-call args, routes to the handler, and logs via ToolLogger.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from src.data.chat_tools.tool_logger import ToolLogger

logger = logging.getLogger(__name__)

_MAX_RESULT_JSON_LEN = 4096  # PHI-safe: truncate result_json to 4KB

# Lazily populated on first import of handler modules
_CHAT_TOOLS: dict[str, dict[str, Any]] = {}
_logger_instance: ToolLogger | None = None

# Lazily populated map of tool_name -> strict MCP inputSchema. Only tools
# that opt in via additionalProperties:false (the enablement tools) appear
# here; everything else (and the whole Gemini text-loop) is never enforced.
_STRICT_SCHEMAS: dict[str, dict] | None = None

# Reserved arg key the dispatcher merges into session filters for every
# tool — never part of a tool's own declared schema, so exclude it from
# strict validation rather than rejecting it.
_RESERVED_ARG_KEYS = ("filters",)


def _strict_schema_for(tool_name: str) -> dict | None:
    """Return the strict inputSchema for a tool, or None when it has no
    strict schema (so the validator stays inert for it).

    Sourced from chat_mcp_server.TOOL_SCHEMAS, indexed once. Best-effort:
    any import/shape failure leaves enforcement off (fail-open) so a schema
    problem can never block dispatch."""
    global _STRICT_SCHEMAS
    if _STRICT_SCHEMAS is None:
        _STRICT_SCHEMAS = {}
        try:
            from src.data.chat_tools._schema import is_strict
            from src.mcp.chat_mcp_server import TOOL_SCHEMAS
            for t in TOOL_SCHEMAS:
                schema = t.get("inputSchema")
                if is_strict(schema):
                    _STRICT_SCHEMAS[t["name"]] = schema
        except Exception as exc:  # noqa: BLE001 — fail-open, never block dispatch
            logger.debug("Strict-schema index unavailable: %s", exc)
            _STRICT_SCHEMAS = {}
    return _STRICT_SCHEMAS.get(tool_name)


def _ensure_registered():
    """Populate the registry on first call (avoids circular imports)."""
    if _CHAT_TOOLS:
        return

    from src.data.chat_tools.fast_path import (
        handle_query_classifications,
        handle_list_tickets,
        handle_query_findings,
        handle_query_stats,
    )
    from src.data.chat_tools.thread_tools import (
        handle_read_thread,
        handle_read_threads_batch,
    )
    from src.data.chat_tools.report_tools import (
        handle_query_report,
        handle_run_report,
    )

    _register("query_ticket_classifications", handle_query_classifications,
              phi_level=0, desc="Count and group tickets by classification field")
    _register("list_tickets", handle_list_tickets,
              phi_level=1, desc="List individual tickets matching filters")
    _register("query_findings", handle_query_findings,
              phi_level=0, desc="Retrieve NLP scan findings and themes")
    _register("query_stats", handle_query_stats,
              phi_level=0, desc="Query anomalies, trends, baselines")
    _register("read_thread", handle_read_thread,
              phi_level=2, desc="Read full conversation thread (PII-redacted)")
    _register("read_threads_batch", handle_read_threads_batch,
              phi_level=2, desc="Read threads for up to 5 tickets")
    _register("query_report", handle_query_report,
              phi_level=0, desc="Read a previously generated analysis report")
    _register("run_report", handle_run_report,
              phi_level=3, desc="Trigger a new analysis report")

    # ── Entity lookup (Addendum Session 3) ──
    from src.data.chat_tools.fast_path import handle_query_entities
    _register("query_entities", handle_query_entities,
              phi_level=0, desc="Look up tickets by payer, product area, or feature")

    # ── Semantic search (Session 4) ──
    from src.data.chat_tools.semantic_tools import handle_semantic_search
    _register("semantic_search", handle_semantic_search,
              phi_level=1, desc="Find tickets by semantic similarity")

    # ── Phase 9: unified scope-aware issue query + tag audit ──
    from src.data.issue_query_handler import handle_query_issues
    _register("query_issues", handle_query_issues,
              phi_level=1, desc="Unified scope-aware issue query over canonical clusters/concepts")
    from src.data.tag_audit import handle_audit_tag_correlation
    _register("audit_tag_correlation", handle_audit_tag_correlation,
              phi_level=1, desc="Audit a tag against canonical clusters + rank likely mis-tags")

    # ── Enablement: local doc search + business Drive query + Asana setup ──
    from src.data.chat_tools.enablement_tools import (
        handle_search_local_documents,
        handle_query_business_drive,
        handle_search_google_drive,
        handle_import_drive_doc,
        handle_search_everywhere,
        handle_asana_discover,
        handle_set_asana_board_config,
        handle_create_card_draft,
        handle_revise_draft,
        handle_push_guru_draft,
        handle_list_guru_collections,
        handle_list_guru_folders,
        handle_render_card_preview,
        handle_draft_subtasks,
        handle_add_subtask,
        handle_toggle_subtask,
        handle_update_scratchpad,
        handle_request_asana_task_update,
        handle_create_task,
        handle_update_task,
        handle_list_tasks,
        handle_search_drive_docs,
        handle_get_drive_doc,
        handle_list_style_guides,
        handle_get_style_guide,
        handle_set_active_style_guide,
        handle_run_monitor_now,
    )
    # ── Resolver tools (M2+): action-only — open an in-chat picker/connect card ──
    from src.data.chat_tools.enablement_tools import (
        handle_request_google_connect,
        handle_request_drive_picker,
        handle_set_drive_folder,
        handle_request_asana_board_picker,
        handle_list_asana_projects,
        handle_list_asana_tasks,
        handle_set_asana_board,
        handle_request_guru_publish_picker,
        handle_set_guru_publish_target,
        handle_get_enablement_routing,
        handle_request_create_guru_folder,
        handle_request_rename_guru_folder,
        handle_request_create_asana_task,
    )
    _register("request_google_connect", handle_request_google_connect,
              phi_level=0, desc="Open the in-chat Connect Google card so the operator can authorize Drive read access; returns a wait instruction (the card opens in the app)")
    _register("request_drive_picker", handle_request_drive_picker,
              phi_level=0, desc="Open the in-chat Google Drive folder picker so the operator can browse and choose the active folder; returns a wait instruction (the picker opens in the app). STOP and wait for a [SYSTEM: operator selected …] message.")
    _register("set_drive_folder", handle_set_drive_folder,
              phi_level=0, desc="Set the active Drive folder by id (fallback when you already know the folder id). Refuses with needs_picker while a folder picker is open — let the operator pick in the app first.")
    # ── Asana board picker + live reads (M4) ──
    _register("request_asana_board_picker", handle_request_asana_board_picker,
              phi_level=0, desc="Open the in-chat Asana board picker so the operator can browse and choose the active board; returns a wait instruction (the picker opens in the app). STOP and wait for a [SYSTEM: operator set the active Asana board …] message.")
    _register("list_asana_projects", handle_list_asana_projects,
              phi_level=0, desc="List the Asana projects/boards the shared PAT can see (gid + name). Use to name boards without opening the picker.")
    _register("list_asana_tasks", handle_list_asana_tasks,
              phi_level=0, desc="LIST (enumerate) the tasks on an Asana board — paginated to completion (name, due date, link, assignee). Defaults to the active board when project_gid is omitted. THE tool that answers 'what tasks are on the board'; reports the true count.")
    _register("set_asana_board", handle_set_asana_board,
              phi_level=0, desc="Set the active Asana board by project_gid (fallback when you already know the gid). Refuses with needs_picker while a board picker is open — let the operator pick in the app first.")
    # ── Guru publish-target picker (M5) ──
    _register("request_guru_publish_picker", handle_request_guru_publish_picker,
              phi_level=0, desc="Open the in-chat Guru publish-target picker so the operator can choose a collection (and optionally a folder) where cards publish; returns a wait instruction (the picker opens in the app). STOP and wait for a [SYSTEM: operator set the Guru publish target …] message.")
    _register("set_guru_publish_target", handle_set_guru_publish_target,
              phi_level=0, desc="Set the Guru publish target by collection_id (+ optional folder_id) — fallback when you already know the ids (otherwise use request_guru_publish_picker so the operator picks). Refuses with needs_picker while a publish-target picker is open in the app.")
    # ── Routing report (M6) ──
    _register("get_enablement_routing", handle_get_enablement_routing,
              phi_level=0, desc="Report the CURRENT enablement routing across the 3 touchpoints — the active Drive folder ids + count + a configured flag (NO folder names), the active Asana board (gid + name + connected), and the Guru publish target (collection/folder ids + connected). Read-only; call this to answer 'what's set up'.")
    # ── Gated write proposals (M7b) — propose a write; the OPERATOR confirms ──
    _register("request_create_guru_folder", handle_request_create_guru_folder,
              phi_level=0, desc="PROPOSE creating a new Guru folder (collection_id + title; optional parent_folder_id). Opens a Confirm/Cancel card in the app — it does NOT create the folder. STOP and wait for a [SYSTEM: operator confirmed/cancelled …] message; you cannot run the write yourself.")
    _register("request_rename_guru_folder", handle_request_rename_guru_folder,
              phi_level=0, desc="PROPOSE renaming a Guru folder (folder_id + new_title). Opens a Confirm/Cancel card in the app — it does NOT rename. STOP and wait for a [SYSTEM: operator confirmed/cancelled …] message; you cannot run the write yourself. (There is no way to DELETE a Guru folder via the app.)")
    _register("request_create_asana_task", handle_request_create_asana_task,
              phi_level=0, desc="PROPOSE creating an Asana task (project_gid + name; optional notes, due_on). Opens a Confirm/Cancel card in the app — it does NOT create the task. STOP and wait for a [SYSTEM: operator confirmed/cancelled …] message; you cannot run the write yourself.")

    _register("search_local_documents", handle_search_local_documents,
              phi_level=0, desc="Tokenized search of documents saved LOCALLY in Alma (incl. docs never uploaded to Drive) + card drafts")
    _register("query_business_drive", handle_query_business_drive,
              phi_level=0, desc="Search the business Drive's LOCAL mirror (previously-pulled docs); for a live search use search_google_drive")
    _register("search_google_drive", handle_search_google_drive,
              phi_level=0, desc="LIVE Google Drive search via the API — defaults to the operator's ACTIVE Drive folder(s), searched recursively; scope='all' for everything visible, folder_id for one subtree. The result's scope block says what was searched — report it, never guess. Returns names/links, no sync")
    _register("import_drive_doc", handle_import_drive_doc,
              phi_level=0, desc="Bridge: pull a Google Drive file (id or URL from search_google_drive) into the local library so it becomes tokenized-searchable")
    _register("search_everywhere", handle_search_everywhere,
              phi_level=0, desc="Search the local Alma library + live Google Drive together (docs/files), labeled by source — use when a doc could be saved in Alma OR sitting in Drive. (For Guru/Zendesk content, use search_content instead.)")
    _register("asana_discover", handle_asana_discover,
              phi_level=0, desc="Discover Asana projects + custom-field/enum-value GIDs")
    _register("set_asana_board_config", handle_set_asana_board_config,
              phi_level=0, desc="Save an Asana board's config using resolved GIDs (the assistant's only write)")
    # ── Enablement Workbench action tools ──
    _register("create_card_draft", handle_create_card_draft,
              phi_level=0, desc="Create a new Guru card draft from a title + Markdown content")
    _register("revise_draft", handle_revise_draft,
              phi_level=0, desc="Revise a Guru card draft with an instruction and re-render it")
    _register("push_guru_draft", handle_push_guru_draft,
              phi_level=0, desc="Publish a card draft to Guru (creates a new card or updates an existing one); optional collection_id + folder_id target a sub-folder")
    _register("list_guru_collections", handle_list_guru_collections,
              phi_level=0, desc="List Guru collections to choose a publish target")
    _register("list_guru_folders", handle_list_guru_folders,
              phi_level=0, desc="List a Guru collection's folders (sub-folders) by collection id or name")
    _register("render_card_preview", handle_render_card_preview,
              phi_level=0, desc="Return a draft's current title + content for preview")
    _register("draft_subtasks", handle_draft_subtasks,
              phi_level=0, desc="Attach a checklist of subtasks to a task")
    _register("add_subtask", handle_add_subtask,
              phi_level=0, desc="Add one subtask to a task")
    _register("toggle_subtask", handle_toggle_subtask,
              phi_level=0, desc="Check or uncheck a subtask")
    _register("update_scratchpad", handle_update_scratchpad,
              phi_level=0, desc="Write freeform notes on a task")
    # ── Asana write-back (two-way sync) — WS1-M6: Confirm-gated ──
    # The three un-gated write tools (create_asana_subtask / post_asana_comment /
    # update_asana_due_date) are RETIRED from the model surface; every
    # Renn-initiated Asana mutation now rides the confirm_write rail. The
    # impls remain — the TaskDetailPanel's direct-click path uses them.
    _register("request_asana_task_update", handle_request_asana_task_update,
              phi_level=0, desc="Propose an Asana task update (complete/reopen/due/"
                                "comment/subtask) — opens a Confirm card")
    _register("create_task", handle_create_task,
              phi_level=0, desc="Create an enablement task")
    _register("update_task", handle_update_task,
              phi_level=0, desc="Update an enablement task's status/priority/due date/etc.")
    _register("list_tasks", handle_list_tasks,
              phi_level=0, desc="List enablement tasks with optional filters")

    # ── Agent jobs (M4: multi-phase work tracked in the sidebar) ──
    from src.data.chat_tools.job_tools import (
        handle_create_job, handle_update_job, handle_list_jobs,
    )
    _register("create_job", handle_create_job,
              phi_level=0, desc="Create a tracked multi-phase job (header + ordered steps)")
    _register("update_job", handle_update_job,
              phi_level=0, desc="Update a job's status/progress or one of its steps")
    _register("list_jobs", handle_list_jobs,
              phi_level=0, desc="List recent jobs for the active session")

    # ── Content-studio artifacts (WS3: diagrams/quizzes/decks/docs) ──
    from src.data.chat_tools.artifact_tools import (
        handle_list_artifacts, handle_generate_diagram, handle_generate_deck,
        handle_generate_quiz, handle_generate_doc, handle_attach_artifact,
    )
    _register("generate_quiz", handle_generate_quiz,
              phi_level=0, desc="Generate a knowledge-check quiz from a task/research/doc/inline source")
    _register("generate_doc", handle_generate_doc,
              phi_level=0, desc="Generate a one-pager or battle-card from a task/research/doc/inline source")
    _register("attach_artifact_to_draft", handle_attach_artifact,
              phi_level=0, desc="Attach a diagram/quiz/doc artifact to a Guru card draft (review-gated)")
    _register("list_artifacts", handle_list_artifacts,
              phi_level=0, desc="List content-studio artifacts (diagrams, quizzes, decks, docs)")
    _register("generate_diagram", handle_generate_diagram,
              phi_level=0, desc="Generate a Mermaid diagram from a task/research/doc/inline source")
    _register("generate_deck", handle_generate_deck,
              phi_level=0, desc="Generate a branded .pptx deck from a task/research/doc/inline source (tracked job)")
    from src.data.chat_tools.artifact_tools import handle_request_upload_artifact
    _register("request_upload_artifact_to_drive", handle_request_upload_artifact,
              phi_level=0, desc="Propose uploading a rendered artifact to the EC Drive folder — opens a Confirm card")

    # ── Drive knowledge base (WS2) ──
    from src.data.chat_tools.kb_tools import (
        handle_index_drive_folder, handle_kb_search, handle_kb_list_topics,
        handle_kb_list_cards, handle_kb_get_card,
    )
    _register("index_drive_folder", handle_index_drive_folder,
              phi_level=0, desc="Queue a Drive folder for KB indexing (runs on the next sync tick)")
    _register("kb_search", handle_kb_search,
              phi_level=0, desc="Ranked hybrid search over the knowledge base (+ full-text floor)")
    _register("kb_list_topics", handle_kb_list_topics,
              phi_level=0, desc="Enumerate ALL knowledge-base topics with card counts")
    _register("kb_list_cards", handle_kb_list_cards,
              phi_level=0, desc="Enumerate ALL cards in one knowledge-base topic")
    _register("kb_get_card", handle_kb_get_card,
              phi_level=0, desc="Read one knowledge-base card in full")

    # ── In-app Help Center ──
    from src.data.chat_tools.help_tools import handle_help_search
    _register("help_search", handle_help_search,
              phi_level=0, desc="Search the in-app Help Center — how the app's "
                                "own features work, and whether each is actually "
                                "available (every result carries a status)")

    _register("search_drive_docs", handle_search_drive_docs,
              phi_level=0, desc="Search indexed Drive documents")
    _register("list_style_guides", handle_list_style_guides,
              phi_level=0, desc="List the operator's stored style guides (the active one is flagged)")
    _register("get_style_guide", handle_get_style_guide,
              phi_level=0, desc="Read the active style guide's text so a card can be written to follow it")
    _register("set_active_style_guide", handle_set_active_style_guide,
              phi_level=0, desc="Switch which stored style guide is active (injected into card generation/revision)")
    _register("get_drive_doc", handle_get_drive_doc,
              phi_level=0, desc="Get one indexed Drive document by id")
    _register("run_monitor_now", handle_run_monitor_now,
              phi_level=0, desc="Run a one-off poll of the configured Asana/Drive monitors")
    # ── Guru analytics tools (P7 redesign) ──
    from src.data.chat_tools.enablement_tools import (
        handle_create_task_from_comment,
        handle_get_guru_analytics,
        handle_import_guru_card,
        handle_update_card_from_doc,
        handle_search_guru_cards,
        handle_list_guru_cards,
        handle_list_guru_folder_items,
        handle_search_zendesk_articles,
        handle_list_zendesk_articles,
        handle_list_zendesk_macros,
        handle_search_asana_tasks,
        handle_research_topic,
        handle_open_guru_card,
        handle_index_content,
        handle_search_catalog,
        handle_search_content,
        handle_update_cards_from_doc,
        handle_card_history,
        handle_card_effectiveness,
        handle_find_cards_to_update,
        handle_find_stale_cards,
        handle_find_content_gaps,
    )
    _register("import_guru_card", handle_import_guru_card,
              phi_level=0, desc="Import an existing Guru card as an editable draft (publish updates it)")
    _register("get_guru_analytics", handle_get_guru_analytics,
              phi_level=0, desc="Guru usage analytics: top_cards | verification | comments | due_cards")
    _register("create_task_from_comment", handle_create_task_from_comment,
              phi_level=0, desc="Convert an open Guru card comment into an enablement task")
    _register("update_card_from_doc", handle_update_card_from_doc,
              phi_level=0, desc="Review a source doc, find the existing Guru card, identify changes, write the update, and stage a draft for review")
    _register("search_guru_cards", handle_search_guru_cards,
              phi_level=0, desc="SEARCH LIVE Guru for cards by topic/title — query-ranked, MAY MISS cards that don't match (returns id, title, snippet; offset for paging). To ENUMERATE a whole collection completely use list_guru_cards; for a folder's contents use list_guru_folder_items.")
    _register("list_guru_cards", handle_list_guru_cards,
              phi_level=0, desc="LIST (enumerate) every card in a Guru collection — DETERMINISTIC + COMPLETE (paginates to the end). Answers 'what cards are in this collection'; reports the total count. Prefer this over search_guru_cards when you need ALL cards in a collection.")
    _register("list_guru_folder_items", handle_list_guru_folder_items,
              phi_level=0, desc="LIST (enumerate) a Guru folder's items — cards AND nested sub-folders (id, item_id, type, title). DETERMINISTIC; answers 'what's in this folder'. Recurse into sub-folders with another call.")
    _register("search_zendesk_articles", handle_search_zendesk_articles,
              phi_level=0, desc="SEARCH the LOCAL Zendesk mirror's Help Center articles by keyword — query-ranked FTS, MAY MISS articles; no network. To enumerate the mirror completely use list_zendesk_articles. Degrades to zendesk_mirror_empty when nothing has been pulled/imported. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")
    _register("list_zendesk_articles", handle_list_zendesk_articles,
              phi_level=0, desc="LIST (enumerate) the mirrored Zendesk Help Center articles — DETERMINISTIC + COMPLETE over the LOCAL mirror; reports the total count (id, title, url, section). Degrades to zendesk_mirror_empty when nothing has been pulled/imported. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")
    _register("list_zendesk_macros", handle_list_zendesk_macros,
              phi_level=0, desc="LIST (enumerate) the mirrored Zendesk macros — DETERMINISTIC + COMPLETE over the LOCAL mirror (id, title, active). Degrades to zendesk_mirror_empty when nothing has been pulled/imported. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")
    _register("search_asana_tasks", handle_search_asana_tasks,
              phi_level=0, desc="SEARCH the tasks on an Asana board by a case-insensitive name substring (lists the paginated board, then filters by name). Defaults to the active board when project_gid is omitted.")
    _register("research_topic", handle_research_topic,
              phi_level=1, desc="Gather reference points on a topic from every source: Guru cards + local docs + ticket signals")
    _register("open_guru_card", handle_open_guru_card,
              phi_level=0, desc="Open a Guru card in the operator's default web browser by id or URL")
    _register("index_content", handle_index_content,
              phi_level=0, desc="Build/refresh the summary catalog over PHI-free content (docs + Guru cards) for fast scalable search")
    _register("search_catalog", handle_search_catalog,
              phi_level=0, desc="Deterministic hybrid search over the LOCAL content-catalog summaries (torch-free; disambiguates look-alike titles by content). Needs index_content run first. For a LIVE cross-source lookup use search_content.")
    _register("search_content", handle_search_content,
              phi_level=0, desc="UNIFIED cross-source SEARCH: fan out one query to LIVE Guru + Zendesk + Drive search and return one merged list, each result LABELED with its source. Answers 'do we have anything on X ANYWHERE'. Query-ranked (may be partial); to ENUMERATE a collection/folder/board use a LIST tool (list_guru_cards / list_zendesk_articles / list_asana_tasks). A not-connected source is skipped (reported in sources), never fatal. sources? filters the fan-out; limit? default 8. Read-only everywhere; Zendesk results come from the LOCAL mirror and cannot be written back.")
    _register("update_cards_from_doc", handle_update_cards_from_doc,
              phi_level=0, desc="Fan-out: find the SET of Guru cards a source doc affects and stage an update for each changed card (human-gated publish)")
    _register("card_history", handle_card_history,
              phi_level=0, desc="Audit trail for a Guru card: every update — what changed, from what source, who approved, when")
    _register("card_effectiveness", handle_card_effectiveness,
              phi_level=1, desc="Did-it-work feedback for a card: update history + measured ticket-volume impact (delta_pct)")
    # ── Task-shaped attention queue (reason over INTENT, not raw queries) ──
    _register("find_cards_to_update", handle_find_cards_to_update,
              phi_level=0, desc="Call this when the user asks 'what should I work on / update next' — returns the ranked Guru-card attention queue (most-in-need first) with a reason per card; optional bucket filter (source_changed | verification_overdue | gap_dup | healthy)")
    _register("find_stale_cards", handle_find_stale_cards,
              phi_level=0, desc="Call this when the user asks which cards are stale / overdue for verification — returns overdue cards (falling back to the least-fresh cards) ranked staleest-first")
    _register("find_content_gaps", handle_find_content_gaps,
              phi_level=0, desc="Call this when the user asks about content gaps or duplicate cards — returns cards flagged as a coverage gap or a near-duplicate, each noting the gap vs the duplicated card ids")

    # ── Zendesk mirror tools (zendesk-clone-web WS5) ──
    # Mirror-only family: FTS search / full reads / pending-draft proposals
    # over the mig-051 local mirror. propose_* can never publish or change
    # a draft's status — the specialist copies into real Zendesk by hand.
    #
    # OWNER POLICY (locked, 2026-07-26): Zendesk is READ-ONLY on BOTH
    # dispatch paths. Never register a tool here that writes to Zendesk,
    # and keep the read-only sentence in every zendesk-family description —
    # tests/test_zendesk_mirror_tools.py enumerates this registry and fails
    # the build if a new zendesk tool appears or the wording is dropped.
    from src.data.chat_tools.zendesk_mirror_tools import (
        handle_search_zendesk_mirror,
        handle_get_zendesk_article,
        handle_get_zendesk_macro,
        handle_propose_article_update,
        handle_propose_macro_update,
        handle_list_zendesk_revisions,
    )
    _register("search_zendesk_mirror", handle_search_zendesk_mirror,
              phi_level=0, desc="SEARCH the LOCAL Zendesk mirror (Help Center articles + macros already pulled/imported) by keyword — query-ranked FTS, MAY MISS items; works offline, never touches the live API. To ENUMERATE completely use list_zendesk_articles / list_zendesk_macros. Degrades to zendesk_mirror_empty. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")
    _register("get_zendesk_article", handle_get_zendesk_article,
              phi_level=0, desc="Read ONE mirrored Help Center article in full (body text, section/category, labels, open revision count) by id from the LOCAL mirror — ids come from search_zendesk_mirror / list_zendesk_articles. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")
    _register("get_zendesk_macro", handle_get_zendesk_macro,
              phi_level=0, desc="Read ONE mirrored Zendesk macro in full (actions list included) by id from the LOCAL mirror — ids come from search_zendesk_mirror / list_zendesk_macros. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")
    _register("propose_article_update", handle_propose_article_update,
              phi_level=0, desc="PROPOSE a Help Center article revision (or a brand-new article when article_id is omitted): stages a PENDING draft in the Revision Center with a MANDATORY rationale + optional sources. Zendesk is READ-ONLY in this app — this does NOT touch real Zendesk, and no tool (here or anywhere) can publish, push or change draft status. The specialist reviews the diff and copies the approved text into the Zendesk editor by hand — say that, and do not offer to publish it yourself.")
    _register("propose_macro_update", handle_propose_macro_update,
              phi_level=0, desc="PROPOSE a Zendesk macro revision (or a new macro when macro_id is omitted): stages a PENDING draft — the reply becomes the comment action and a targeted macro's non-comment actions are preserved. MANDATORY rationale. Zendesk is READ-ONLY in this app — never touches real Zendesk, and no tool can publish or push it; the specialist copies the approved macro in by hand and owns every status change.")
    _register("list_zendesk_revisions", handle_list_zendesk_revisions,
              phi_level=0, desc="LIST the AI revision drafts for Zendesk content (articles + macros, unified) with optional status and kind (article | macro) filters — the audit view of proposed changes. Statuses: pending | ready | copied, plus the legacy value 'pushed' which predates the read-only lockout and can no longer be produced. Zendesk is READ-ONLY in this app: no tool can publish or push anything to Zendesk — a specialist copies approved content in by hand.")

    # ── Backward-compat aliases for old tool names ──
    # These map old names to new handlers so existing prompts keep working
    from src.data.chat_tools.fast_path import (
        handle_legacy_query_tickets,
        handle_legacy_ticket_detail,
        handle_legacy_query_trends,
        handle_legacy_query_anomalies,
        handle_legacy_compare_periods,
        handle_legacy_query_insights,
        handle_legacy_search_conversations,
    )
    _register("query_tickets", handle_legacy_query_tickets,
              phi_level=1, desc="(Legacy) Query tickets by filters")
    _register("ticket_detail", handle_legacy_ticket_detail,
              phi_level=1, desc="(Legacy) Get ticket detail by ID")
    _register("query_trends", handle_legacy_query_trends,
              phi_level=0, desc="(Legacy) Monthly volume trends")
    _register("query_anomalies", handle_legacy_query_anomalies,
              phi_level=0, desc="(Legacy) Anomaly flags")
    _register("compare_periods", handle_legacy_compare_periods,
              phi_level=0, desc="(Legacy) Period comparison")
    _register("query_insights", handle_legacy_query_insights,
              phi_level=0, desc="(Legacy) NLP insights")
    _register("search_conversations", handle_legacy_search_conversations,
              phi_level=1, desc="(Legacy) FTS conversation search")


def _register(name: str, handler, phi_level: int, desc: str):
    _CHAT_TOOLS[name] = {
        "handler": handler,
        "phi_level": phi_level,
        "description": desc,
    }


def get_tool_registry() -> dict[str, dict[str, Any]]:
    """Return the full tool registry dict."""
    _ensure_registered()
    return dict(_CHAT_TOOLS)


def dispatch_tool(
    tool_name: str,
    args: dict,
    conn,
    session_filters: dict | None = None,
    session_id: str | None = None,
    message_id: str | None = None,
) -> str:
    """Central tool dispatch. Returns JSON result string.

    Merges session_filters (base) with args (override), calls
    the handler, logs telemetry, persists to chat_tool_executions,
    and returns JSON.
    """
    global _logger_instance
    _ensure_registered()

    if _logger_instance is None:
        _logger_instance = ToolLogger()

    tool_def = _CHAT_TOOLS.get(tool_name)
    if tool_def is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    # Strict-schema arg validation (enablement tools only). Tools without a
    # declared strict schema — and the entire Gemini text-loop — skip this
    # entirely (_strict_schema_for returns None → validate_args is a no-op).
    schema = _strict_schema_for(tool_name)
    if schema is not None:
        from src.data.chat_tools._schema import validate_args
        to_check = {k: v for k, v in (args or {}).items()
                    if k not in _RESERVED_ARG_KEYS}
        ok, err = validate_args(schema, to_check)
        if not ok:
            logger.warning("Tool %s arg validation failed: %s", tool_name, err)
            return json.dumps({"error": f"invalid_arguments: {err}", "tool": tool_name})

    # Merge session filters as base, tool args override
    effective_filters = dict(session_filters or {})
    if "filters" in args:
        effective_filters.update(args["filters"])

    start = time.perf_counter()
    error = None
    result = None
    result_rows = None
    tables_touched = None
    try:
        handler = tool_def["handler"]
        result = handler(conn, args, effective_filters)

        # Estimate result_rows from common response shapes
        if isinstance(result, dict):
            for key in ("tickets", "results", "rows", "findings", "data"):
                if key in result and isinstance(result[key], list):
                    result_rows = len(result[key])
                    break
            if result_rows is None and "count" in result:
                result_rows = result.get("count")
        elif isinstance(result, list):
            result_rows = len(result)

        return json.dumps(result, default=str)
    except Exception as e:
        error = str(e)
        logger.warning("Tool %s failed: %s", tool_name, e)
        return json.dumps({"error": error})
    finally:
        elapsed = (time.perf_counter() - start) * 1000
        result_size = len(json.dumps(result, default=str)) if result else 0

        # JSONL file log (existing)
        _logger_instance.log_call(
            tool_name=tool_name,
            args=args,
            result_size=result_size,
            elapsed_ms=elapsed,
            error=error,
            session_id=session_id,
        )

        # Persist to chat_tool_executions table (migration 009)
        _persist_tool_execution(
            conn=conn,
            message_id=message_id,
            session_id=session_id,
            tool_name=tool_name,
            args=args,
            result=result,
            result_rows=result_rows,
            tables_touched=tables_touched,
            elapsed_ms=elapsed,
            error=error,
        )


def _persist_tool_execution(
    conn,
    message_id: str | None,
    session_id: str | None,
    tool_name: str,
    args: dict,
    result: Any,
    result_rows: int | None,
    tables_touched: list[str] | None,
    elapsed_ms: float,
    error: str | None,
) -> None:
    """Write a row to chat_tool_executions (best-effort, non-blocking).

    When session_id is None (programmatic probes, harness runs, any
    non-UI caller) we still write with a synthetic "adhoc_probe"
    session_id so the paper trail survives. Previously this silently
    dropped the row — which hid every programmatic invocation from
    the audit table.
    """
    effective_session_id = session_id or "adhoc_probe"

    try:
        # Check table exists (graceful on pre-009 databases)
        conn.execute("SELECT 1 FROM chat_tool_executions LIMIT 0")
    except Exception:
        return

    try:
        # Truncate result_json for PHI safety
        result_json = None
        if result is not None:
            raw = json.dumps(result, default=str)
            result_json = raw[:_MAX_RESULT_JSON_LEN] if len(raw) > _MAX_RESULT_JSON_LEN else raw

        conn.execute(
            """INSERT INTO chat_tool_executions
               (execution_id, message_id, session_id, tool_name,
                args_json, result_json, result_rows, tables_touched,
                elapsed_ms, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                message_id or "",
                effective_session_id,
                tool_name,
                json.dumps(args, default=str),
                result_json,
                result_rows,
                json.dumps(tables_touched) if tables_touched else None,
                int(elapsed_ms),
                error,
            ),
        )
        conn.commit()
    except Exception as e:
        # A failed telemetry write (e.g. a FOREIGN KEY violation on an adhoc/probe
        # session_id) must NOT leave the connection mid-transaction — the dangling
        # transaction would make the next atomic() on a reused connection raise
        # "atomic() cannot be nested". Roll back defensively so failure is inert.
        try:
            conn.rollback()
        except Exception:
            pass
        logger.debug("Failed to persist tool execution: %s", e)
