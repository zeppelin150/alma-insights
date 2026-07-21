"""Enablement section container — 4 tabs + a demo controller.

Mounts the Calendar / Tasks / Workbench / Settings pages under one nav entry
(takes the legacy Guru slot in main_window behind `enablement.workbench_enabled`).

Demo mode (`enablement.demo_mode`, default on) makes the Workbench interactive
against the REAL backend: "Scan all (demo)" runs the simulation pipeline into a
throwaway demo DB, loads the generated drafts into the Workbench, and the chat /
push / revise actions exercise enablement_store. No live creds or warehouse
writes — the demo DB lives in the temp dir.

Exposes the same no-op setter API as GuruWipPage so main_window's wiring stays
drop-in compatible.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from src.ui.pages.enablement.calendar import CalendarPage
from src.ui.pages.enablement.chat_panel import ChatPanel
from src.ui.pages.enablement.settings import SettingsPage
from src.ui.pages.enablement.task_detail import TaskDetailPanel
from src.ui.pages.enablement.tasks import TasksPage
from src.ui.pages.enablement.workbench import WorkbenchPage
from src.ui.theme import (
    ALMA_CREAM, ALMA_GREEN_DARK, ALMA_SUCCESS, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
    ALMA_TEXT_ON_DARK,
)

import logging

logger = logging.getLogger("alma.enablement")

RENN_SYSTEM_PROMPT = (
    "You are Renn, the enablement assistant inside the Alma Enablement Workbench. "
    "You help the enablement team turn product docs into Guru knowledge cards, manage "
    "their task list, and connect their Asana board.\n\n"
    "The operator you're assisting is named in your turn context (an 'Operator:' or "
    "'[OPERATOR]' line carrying their email). When they ask who they are or to "
    "confirm their identity, answer directly from that line — don't say you can't.\n\n"
    "When your turn context includes a '[TODAY'S PLAN]' block, that is the operator's "
    "REAL task data, pre-loaded from the task store (equivalent to a list_tasks result). "
    "It is accurate and authoritative — present it confidently as the day's briefing, "
    "NEVER describe it as invented or fabricated, and don't re-query just to verify it.\n\n"
    "You have tools (function calls) to do real work — USE THEM rather than describing "
    "what you would do:\n"
    "- Find content: search_local_documents, query_business_drive, search_drive_docs, get_drive_doc.\n"
    "\nFinding content — pick the RIGHT kind of tool:\n"
    "- To ENUMERATE what's IN a collection / folder / board (the operator asks 'what's "
    "in X', 'list everything in this folder', 'what cards are in that collection', "
    "'what tasks are on the board'), use a LIST tool — NOT search: list_guru_cards "
    "(every card in a collection), list_guru_folder_items (a folder's cards + sub-"
    "folders), list_zendesk_articles (the Help Center), list_asana_tasks (a board's "
    "tasks). LIST tools enumerate COMPLETELY and report a total count; search may miss "
    "items. When the operator wants what's IN a collection/folder/board, always LIST.\n"
    "- To look up a topic/keyword WITHIN ONE source, use that source's search_* tool: "
    "search_guru_cards, search_zendesk_articles, search_asana_tasks (query-ranked — may "
    "be partial).\n"
    "- To check 'do we have anything on X ANYWHERE', use search_content — it fans one "
    "query out across LIVE Guru + Zendesk + Drive at once and returns a single merged "
    "list, each result labeled with its source; a source that isn't connected is simply "
    "skipped and reported (e.g. tell the operator '(Zendesk not connected)'), never an "
    "error. Use search_catalog only for the local pre-indexed summary catalog.\n"
    "- KNOWLEDGE BASE (check it FIRST for product/policy questions): kb_search "
    "(query-ranked over the index cards Renn built, plus a full-text floor over "
    "stored docs), kb_list_topics / kb_list_cards (complete enumeration), "
    "kb_get_card (read one card in full). index_drive_folder queues a product "
    "folder for indexing (runs on the next sync tick; progress in the jobs "
    "sidebar). KB index cards are maintained automatically — you never write "
    "them directly.\n"
    "- CONTENT STUDIO (generated artifacts): generate_diagram (Mermaid, for "
    "process-shaped content), generate_quiz (knowledge checks), generate_doc "
    "(style: one_pager | battle_card), generate_deck (branded .pptx as a tracked "
    "job). Each takes EXACTLY ONE source (source_task_id | source_research_id | "
    "source_doc_id | source_text). list_artifacts finds what exists. "
    "attach_artifact_to_draft puts a diagram/quiz/doc into a card draft (the "
    "operator still reviews + approves before publish); "
    "request_upload_artifact_to_drive publishes a rendered file (e.g. a deck) to "
    "the knowledge-base Drive folder behind a Confirm card.\n"
    "- Work a card draft: render_card_preview (read the current draft), revise_draft "
    "(apply an edit and re-render), push_guru_draft (publish to Guru). Use the active "
    "draft id from the [ENABLEMENT SCOPE] context unless the user names another draft.\n"
    "- Choose where to publish: list_guru_collections, then list_guru_folders (pass a "
    "collection id or name) to find the sub-folder; pass collection_id + folder_id to "
    "push_guru_draft to publish a card straight into that folder.\n"
    "- Help Center (how THIS app works): help_search answers questions ABOUT "
    "THE APP — how a feature works and whether it's actually available. Use it "
    "when the operator asks 'how do I …', 'why can't I …', or 'is X working' "
    "about the app itself (Calendar, Workbench, pickers, publishing, the KB, "
    "Settings). Every result carries a status; when it is 'partial', "
    "'flag-gated', or 'not-available', STATE THAT LIMITATION before describing "
    "the feature — never present a gated-off feature as if it works. This is "
    "for the APP's own docs; for the operator's content use search_content / "
    "kb_search instead.\n"
    "- Style guides: list_style_guides (find them), get_style_guide (read the active "
    "one to follow it), set_active_style_guide (switch which one card generation uses).\n"
    "- Manage work: list_tasks, create_task, update_task, draft_subtasks, add_subtask, "
    "toggle_subtask, update_scratchpad.\n"
    "- Two-way Asana: request_asana_task_update (task_id + action: complete | reopen | "
    "set_due | comment | add_subtask, plus a value for the last three) — opens a "
    "Confirm card; the operator's click performs the Asana write. STOP and wait for "
    "the [SYSTEM: operator confirmed/cancelled] message. Acts only on Asana-sourced tasks.\n"
    "- Set up Asana: asana_discover (find projects + field/enum GIDs), then "
    "set_asana_board_config (save the board config). Never ask the user for GIDs — "
    "discover them yourself.\n"
    "- run_monitor_now to pull fresh items from the configured sources.\n"
    "- Guru analytics: get_guru_analytics (metric=top_cards|verification|comments|"
    "due_cards), import_guru_card (bring an existing card in as an editable draft — "
    "publishing updates that same card), create_task_from_comment (turn an open card "
    "comment into a task).\n\n"
    "Connect & configure (Google Drive / Asana / Guru routing):\n"
    "- Connect Google + pick routing with the request_*_picker tools: "
    "request_google_connect (open the Connect Google card), request_drive_picker "
    "(pick the active Drive folder), request_asana_board_picker (pick the active "
    "Asana board), request_guru_publish_picker (pick where cards publish). These do "
    "NOT read Drive/Asana/Guru themselves — each opens a picker in the app.\n"
    "- PICKER CONTRACT: when you call a request_*_picker tool, a picker opens in the "
    "app. STOP and wait — do NOT call any further tools — until you receive a "
    "'[SYSTEM: operator selected …]' message. Only then continue.\n"
    "- Read/set the routing: list_asana_projects (name the boards available), "
    "list_asana_tasks (answers 'what tasks are on the board' — defaults to the active "
    "board the operator set), and the by-id setters set_drive_folder / set_asana_board "
    "/ set_guru_publish_target. Prefer the pickers: the by-id setters REFUSE with "
    "needs_picker while a picker is open, so only use them when you already know the id.\n"
    "- get_enablement_routing reports the CURRENT setup (active Drive folder ids + "
    "count, active Asana board, Guru publish target) — call it to answer 'what's "
    "set up' / 'what's connected'.\n\n"
    "Gated writes (Guru folders, Asana tasks/updates, Drive uploads):\n"
    "- You CANNOT run these writes yourself — there is no direct-execute tool. To "
    "make a change you PROPOSE it with a request_* tool: request_create_guru_folder "
    "(collection_id + title; optional parent_folder_id), request_rename_guru_folder "
    "(folder_id + new_title), request_create_asana_task (project_gid + name; optional "
    "notes, due_on), request_asana_task_update (see Two-way Asana above), "
    "request_upload_artifact_to_drive (artifact_id).\n"
    "- WRITE CONTRACT: calling a request_* write tool opens a Confirm card in the "
    "app. STOP and wait — do NOT call any further tools — until you receive a "
    "'[SYSTEM: operator confirmed/cancelled …]' message. Only the operator's click "
    "runs the write; you cannot execute it. After a confirm, continue (e.g. offer to "
    "set a new folder as the publish target). After a cancel, do not retry unless the "
    "operator asks.\n"
    "- Before proposing a write, the target is pre-flight checked for feasibility: if "
    "it isn't writable/accessible (a read-only, Guru-managed collection can't take a "
    "folder — publishing there also fails — or an unknown Asana board), the tool "
    "returns a plain steer instead of opening a Confirm card; relay it and help the "
    "operator pick a valid, writable target rather than retrying the doomed one.\n"
    "- There is NO way to DELETE a Guru folder via the app — if the operator wants to "
    "delete a folder, tell them to delete it in the Guru web app.\n\n"
    "Background research (NOT AVAILABLE YET):\n"
    "- You have NO background-research tools in this build: you cannot start research "
    "jobs, write research manifests, or open a research-plan approval card. If the "
    "operator asks you to 'research' a task, say the gated research feature isn't "
    "enabled yet, then offer what you CAN do right now (search_content / "
    "search_drive_docs / search_guru_cards over their existing content). NEVER claim "
    "research is running, queued, or will be delivered later.\n\n"
    "Rules:\n"
    "- Only publish to Guru (push_guru_draft) when the operator explicitly asks to push or publish.\n"
    "- NEVER repeat a Google Drive FOLDER name in your replies — refer to Drive "
    "folders by id or generically ('the active folder'). Asana board names and Guru "
    "collection names are fine to use.\n"
    "- Ground every claim in tool results from THIS turn; never invent document names, "
    "task ids, or card contents.\n"
    "- Use ONLY the enablement/Asana tools above. Do not invoke, name, or mention shell, "
    "file, web, or other tools — if asked, redirect to enablement work.\n"
    "- Be concise and action-oriented."
)


class _DemoGuruClient:
    """Canned get_card for the demo-mode 'import existing Guru card' flow —
    exercises the real store/converter path with fake content."""

    def get_card(self, card_id):
        return {
            "id": card_id,
            "title": "Payments v2 Overview",
            "content": (
                "<h2>What changed</h2>"
                "<ol><li>Unified remittance ledger across payers</li>"
                "<li>Auto-matching for ERA lines</li></ol>"
                "<p><strong>Rollout:</strong> June 30, 2026.</p>"
                "<h2>FAQ</h2>"
                "<p><strong>Do saved replies change?</strong> Yes — update "
                "links to the new ledger view.</p>"
            ),
        }


class EnablementPage(QWidget):
    """The Enablement tab: Calendar · Tasks · Workbench · Settings."""

    connection_changed = Signal()   # compat with GuruPage/GuruWipPage
    connection_status_ready = Signal(str, bool, str)  # (source_key, ok, detail) from the check worker
    identity_resolved = Signal(str, str, str)  # (email, asana_gid, asana_name) → main-thread persist (M1)
    import_finished = Signal(dict)  # off-thread import result → UI refresh
    analytics_synced = Signal(dict)  # off-thread Guru analytics sync result
    task_action_done = Signal(dict)  # off-thread Asana write-back result
    pptx_modeled = Signal(dict)      # off-thread deck-model result
    zendesk_synced = Signal(dict)    # off-thread Zendesk sync result
    ai_edit_done = Signal(dict)      # off-thread inline AI-edit (revise) result

    def __init__(self, db=None, demo: bool = True, parent=None):
        super().__init__(parent)
        self.db = db
        self.demo = demo
        self._demo_db = None
        self._drafts: dict[int, dict] = {}
        self._guru_client = None
        self._monitor = None
        self._drilldown = None
        self._all_tasks = []
        self._warm_bridge = None
        self._claude_client = None
        self._engine = None
        self._chat_session_id = None
        self._existing_cards_loaded = False
        self._greeting_sent = False       # one-shot "here's your day" greeting (M5)
        self._greeting_block = None
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        # Engine BEFORE the UI: _build() constructs the web tabs, which register
        # the shared chat bridge (_get_web_chat_bridge) on their channels — that
        # bridge needs the engine to exist NOW, or the M5.5 Renn drawer is dead
        # for the page's lifetime (WebHost registers channel objects exactly
        # once). _chat_context is a callback (invoked at chat time, not here), so
        # it may reference self.workbench, which _build() creates next.
        self._setup_engine()
        self._build()
        # Proactive "here's your day" greeting — deferred so the UI finishes
        # building first; no-ops when there's no engine / identity / dated tasks.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._open_greeting)
        self._load_pptx()
        self._load_zendesk()

    # ── layout ────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 12)
        outer.setSpacing(10)
        outer.addLayout(self._header())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("AnalysisTab")
        self.tabs.setUsesScrollButtons(False)
        self.calendar, self._calendar_tab = self._make_calendar()
        self.tasks = TasksPage()
        self.workbench, self._workbench_tab = self._make_workbench()
        self.settings = SettingsPage()
        # KB card (WS2-M7): give Settings a connection factory so it can show
        # card counts and run the EC bootstrap off-thread.
        self.settings.kb_conn_factory = self._conn
        # Scroll-wrap the tall pages so content scrolls instead of compressing
        # (compression was overlapping rows on Settings). Workbench fills exactly.
        from src.ui.pages.enablement.analytics import AnalyticsPage
        from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab
        from src.ui.pages.enablement.pptx_tab import PptxPage
        from src.ui.pages.enablement.zendesk_tab import ZendeskPage
        self.attention = AttentionQueueTab()
        self.analytics = AnalyticsPage()
        self.pptx = PptxPage()
        self.zendesk = ZendeskPage()
        home_tab = self._scroll(self.attention)
        cal_tab = self._calendar_tab
        tasks_tab = self._scroll(self.tasks)
        analytics_tab = self._scroll(self.analytics)
        pptx_tab = self._scroll(self.pptx)
        zendesk_tab = self._scroll(self.zendesk)
        settings_tab = self._scroll(self.settings)
        self.tabs.addTab(home_tab, "Home")
        self.tabs.addTab(cal_tab, "Calendar")
        self.tabs.addTab(tasks_tab, "Tasks")
        self.tabs.addTab(self._workbench_tab, "Workbench")
        self.tabs.addTab(analytics_tab, "Analytics")
        self.tabs.addTab(pptx_tab, "PowerPoint")
        self.tabs.addTab(zendesk_tab, "Zendesk")
        help_tab = self._make_help()
        self.tabs.addTab(help_tab, "Help")
        self.tabs.addTab(settings_tab, "Settings")
        self._tab_widgets = {
            "home": home_tab,
            "calendar": cal_tab,
            "tasks": tasks_tab,
            "workbench": self._workbench_tab,
            "analytics": analytics_tab,
            "powerpoint": pptx_tab,
            "zendesk": zendesk_tab,
            "help": help_tab,
            "settings": settings_tab,
        }
        # Land on the attention queue; Workbench stays reachable via its tab.
        self.tabs.setCurrentWidget(home_tab)
        outer.addWidget(self.tabs, 1)

        # Assistant chat is hosted in the shared drilldown (chat_panel.ChatPanel)
        self.chat = ChatPanel()
        self.chat.set_chat([("a", "Hi, I'm Renn — your enablement assistant. Run a scan to pull docs and "
                                  "draft cards, ask me to revise or publish a draft, or set up your Asana board.")])
        self.chat.chat_submitted.connect(self._on_chat)
        self.chat.push_requested.connect(lambda: self._on_push(self.workbench.active_draft_id))
        self.chat.revise_requested.connect(
            lambda: self._send_quick("Revise the current draft to tighten it up, then re-render it."))
        self.chat.draft_subtasks_requested.connect(
            lambda: self._send_quick("Draft a subtask checklist to ship the current card and attach it to the task."))
        # workbench actions
        self.workbench.draft_selected.connect(self._on_draft_selected)
        self.workbench.find_task_requested.connect(self._open_task_search)
        self.workbench.workspace_closed.connect(self._on_workspace_closed)
        self.workbench.publish_requested.connect(self._on_publish)
        self.workbench.load_file_requested.connect(self._on_load_file)
        self.workbench.open_chat_requested.connect(self._open_chat)
        self.calendar.event_clicked.connect(self._on_calendar_event)
        from src.data.settings_manager import get_section as _gs
        _scope0 = (_gs("enablement", {}) or {}).get("tasks_default_scope", "mine")
        self._task_scope = _scope0 if _scope0 in ("mine", "all") else "mine"
        self.tasks.set_scope(self._task_scope)
        self.calendar.set_scope(self._task_scope)
        self.tasks.scope_changed.connect(self._on_scope_changed)
        self.calendar.scope_changed.connect(self._on_scope_changed)
        self.settings.asana_setup_requested.connect(self._on_asana_setup)
        self.settings.identity_detect_email_requested.connect(self._on_detect_operator_email)
        self.settings.identity_resolve_asana_gid_requested.connect(self._on_resolve_operator_gid)
        self.identity_resolved.connect(self._on_identity_resolved)
        self.workbench.existing_cards_requested.connect(self._fetch_existing_cards)
        self.workbench.import_requested.connect(self._on_import_requested)
        self.workbench.content_edited.connect(self._on_content_edited)
        # Inline "/"-menu + highlight-to-edit → revise the active draft off-thread,
        # then reload the canvas live (result delivered on the main thread).
        self.workbench.ai_edit_requested.connect(self._on_ai_edit)
        self.ai_edit_done.connect(self._on_ai_edit_done)
        self.settings.drive_folder_added.connect(self._add_drive_folder)
        self.settings.style_guide_action.connect(self._on_style_guide_action)
        self.settings.style_guide_activate.connect(self._on_style_guide_activate)
        self.settings.style_guide_delete.connect(self._on_style_guide_delete)
        self.settings.style_guide_saved.connect(self._on_style_guide_saved)
        self.settings.card_template_action.connect(self._on_card_template_action)
        self.settings.card_template_activate.connect(self._on_card_template_activate)
        self.settings.card_template_delete.connect(self._on_card_template_delete)
        self.settings.card_template_saved.connect(self._on_card_template_saved)
        self.import_finished.connect(self._on_import_finished)
        self.connection_status_ready.connect(
            lambda key, ok, detail: self.settings.set_connection_status(key, ok, detail))
        # attention queue (Home) — off-thread health load + open/dismiss wiring
        self.attention.connect_signals()
        self.attention.open_update_requested.connect(self._on_attention_open)
        self.attention.reload()
        # analytics
        self.analytics.refresh_requested.connect(self._run_analytics_sync)
        self.analytics.filters_changed.connect(self._refresh_analytics)
        self.analytics.comment_task_requested.connect(self._on_comment_task)
        self.analytics.targeted_update_requested.connect(self._on_targeted_update)
        self.analytics_synced.connect(self._on_analytics_synced)
        self.task_action_done.connect(self._on_task_action_done)
        self.calendar.event_activated.connect(self._on_calendar_event_activated)
        self.calendar.day_expanded.connect(self._on_calendar_day)
        # PowerPoint
        self.pptx.model_topic_requested.connect(self._on_pptx_model_topic)
        self.pptx.deck_selected.connect(self._on_pptx_deck_selected)
        self.pptx.outline_saved.connect(self._on_pptx_outline_saved)
        self.pptx.export_requested.connect(self._on_pptx_export)
        self.pptx.doc_dropped.connect(self._on_pptx_doc_dropped)
        self.pptx_modeled.connect(self._on_pptx_modeled)
        # Zendesk
        self.zendesk.sync_requested.connect(self._on_zendesk_sync)
        self.zendesk.article_selected.connect(self._on_zd_article_selected)
        self.zendesk.article_saved.connect(self._on_zd_article_saved)
        self.zendesk.article_push.connect(self._on_zd_article_push)
        self.zendesk.macro_selected.connect(self._on_zd_macro_selected)
        self.zendesk.macro_saved.connect(self._on_zd_macro_saved)
        self.zendesk.macro_push.connect(self._on_zd_macro_push)
        self.zendesk_synced.connect(self._on_zendesk_synced)
        self._refresh_style_guide_status()
        self._refresh_card_template_status()

        if not self.demo:
            try:
                self._load_live()
            except Exception as exc:  # noqa: BLE001 — surface in the status line
                self._set_status(f"Load error: {exc}")
        self._refresh_drive_folders()

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel("Enablement")
        title.setStyleSheet(f"color:{ALMA_TEXT_DARK}; font-size:22px; font-weight:700; border:none;")
        row.addWidget(title)
        if self.demo:
            pill = QLabel("DEMO")
            pill.setStyleSheet(
                f"background:#E4EFE9; color:{ALMA_SUCCESS}; border-radius:9px; padding:2px 9px; "
                f"font-size:10px; font-weight:700; border:none;"
            )
            row.addWidget(pill)
        row.addStretch(1)
        default_status = ("Demo mode — click “Scan all” to pull docs and draft cards."
                          if self.demo else "Live mode — watching your configured sources.")
        self._status = QLabel(default_status)
        self._status.setStyleSheet(f"color:{ALMA_TEXT_LIGHT}; font-size:12px; border:none;")
        row.addWidget(self._status)
        scan = QPushButton("Scan all (demo)" if self.demo else "Scan now")
        scan.setCursor(Qt.PointingHandCursor)
        scan.setStyleSheet(
            f"QPushButton{{background:{ALMA_GREEN_DARK}; color:{ALMA_TEXT_ON_DARK}; border:none; "
            f"border-radius:8px; padding:8px 18px; font-size:12.5px; font-weight:600;}}"
        )
        scan.clicked.connect(self._on_scan if self.demo else self._on_scan_now)
        row.addWidget(scan)
        return row

    def _scroll(self, w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setStyleSheet("QScrollArea{border:none; background:transparent;}")
        sa.setWidget(w)
        return sa

    def _set_status(self, text: str):
        self._status.setText(text)

    # ── demo controller ───────────────────────────────────────────
    def _ensure_demo_db(self):
        if self._demo_db is not None:
            return self._demo_db
        import os
        import tempfile
        from src.data.db_manager import DatabaseManager
        path = os.path.join(tempfile.gettempdir(), "alma_enablement_demo.db")
        for suffix in ("", "-wal", "-shm"):       # start each session from a clean demo DB
            try:
                os.remove(path + suffix)
            except OSError:
                pass
        db = DatabaseManager(db_path=path)
        db.initialize()
        self._demo_db = db
        return db

    def _conn(self):
        """The connection the controller reads/writes: the throwaway demo DB in
        demo mode, the real warehouse otherwise."""
        if self.demo or self.db is None:
            return self._ensure_demo_db().conn
        return self.db.conn

    def _guru_for_push(self):
        """Real Guru client only in live mode — demo pushes stay local."""
        return None if self.demo else self._guru_client

    def _publish_collection_id(self):
        from src.data.settings_manager import get_section
        cfg = get_section("enablement", {}) or {}
        guru = cfg.get("guru") or {}
        return guru.get("publish_collection_id") or None

    def _card_from_draft(self, conn, draft: dict) -> dict:
        from src.data import enablement_store as store
        ref = draft.get("source_ref") or ""
        doc = store.get_document(conn, ref) if ref else None
        name = doc["name"] if doc else "document"
        return {
            "breadcrumb": "GURU › PROVIDER ENABLEMENT",
            "title": draft.get("title") or "Untitled",
            "source": f"From: {name}",
            "markdown": draft.get("content") or "",
            # carry any stored rich HTML so an unedited publish keeps fidelity
            "content_html": draft.get("content_html"),
        }

    @staticmethod
    def _fmt_due(iso) -> str:
        if not iso:
            return "—"
        try:
            import datetime
            return datetime.date.fromisoformat(str(iso)[:10]).strftime("%b %d").replace(" 0", " ")
        except Exception:
            return str(iso)

    def _tasks_to_rows(self, task_list: list[dict], conn) -> list[dict]:
        from src.data import enablement_tasks as tasks
        rows = []
        for t in task_list:
            subs = tasks.list_subtasks(conn, t["task_id"])
            rows.append({
                "task_id": t["task_id"],
                "status": t["status"] if t["status"] in ("open", "in_progress", "done") else "open",
                "title": t["title"],
                "source": t["source"] if t["source"] in ("drive", "guru", "asana") else "drive",
                "due": self._fmt_due(t.get("due_date")),
                "due_iso": (t.get("due_date") or "")[:10],
                "priority": t.get("priority") or "normal",
                "assignee": t.get("assignee") or "—",
                "subs": f"{t.get('subtask_done', 0)} / {t.get('subtask_total', 0)}",
                "subtasks": [(s["text"], bool(s["done"])) for s in subs],
                "scratch": t.get("scratchpad") or "",
                "due_date": t.get("due_date"),
                "description": t.get("description") or "",
                "submitter": t.get("submitter") or "",
                "source_url": t.get("source_url") or "",
            })
        return rows

    def _make_help(self):
        """The Help Center tab. Content is bundled under assets/help/ and
        loaded into help_articles on first open, so a content correction is a
        file replacement rather than a code change."""
        from src.ui.pages.enablement.help_tab import HelpTab
        tab = HelpTab(self._conn)
        tab.flag_bug_requested.connect(self._open_bug_form)
        self._help_tab = tab
        return tab

    def _open_bug_form(self, article_id: str = ""):
        """Open the configured Asana bug-report form in the SYSTEM browser.

        Deliberately external: the form is filled by the operator in their own
        browser, so the app never captures or transmits logs, screenshots or
        document text — which keeps a bug report clear of the PHI/redaction
        surface entirely. Never opened inside the embedded QtWebEngine view.

        Unset URL → a plain explanation rather than a dead link.
        """
        from PySide6.QtWidgets import QMessageBox
        url = ""
        try:
            from src.data.settings_manager import get_section
            url = str((get_section("enablement", {}) or {})
                      .get("help", {}).get("bug_form_url", "") or "").strip()
        except Exception:  # noqa: BLE001 — a settings failure must not block help
            url = ""
        if not url:
            QMessageBox.information(
                self, "Flag a bug",
                "No bug-report form has been configured yet.\n\n"
                "An administrator can set enablement.help.bug_form_url in "
                "settings to point this button at your team's Asana form.")
            return
        if not url.lower().startswith(("http://", "https://")):
            QMessageBox.warning(
                self, "Flag a bug",
                "The configured bug-report form URL is not a web address, so "
                "it was not opened.")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _make_calendar(self):
        """The Calendar tab: the React/WebHost build behind
        ``enablement.web_tabs`` (values ``calendar``/``all``), else the native
        Qt CalendarPage. The controller mirrors CalendarPage's surface
        (set_tasks/set_scope + signals), so every feed and connect in this
        file works identically against either. Returns (calendar, tab_widget)."""
        from src.ui.web.web_flags import web_tabs_mode
        if web_tabs_mode() in ("calendar", "all"):
            try:
                from src.services.enablement_web import CalendarWebController
                from src.ui.web.calendar_bridge import CalendarBridge
                from src.ui.web.web_host import WebHost
            except Exception:  # noqa: BLE001 — WebEngine absent → native tab
                pass
            else:
                ctrl = CalendarWebController(
                    brief_lookup=self._web_brief_lookup,
                    # M2 gated drag-reschedule: the confirm is a NATIVE dialog
                    # (unreachable from page scripts); the write rides the same
                    # CAS-guarded off-thread path as the detail panel's due
                    # field, whose completion already reloads every view.
                    confirm_fn=self._web_reschedule_confirm,
                    write_fn=lambda tid, due: self._run_task_writeback(
                        "update_due_in_asana", tid, due),
                )
                self._web_cal_bridge = CalendarBridge(
                    data_signal=ctrl.calendar_data,
                    brief_signal=ctrl.brief_ready,
                    refresh_fn=ctrl.request_refresh,
                    scope_fn=ctrl.request_scope,
                    open_fn=ctrl.open_task,
                    brief_fn=ctrl.request_brief,
                    reschedule_fn=ctrl.request_reschedule,
                    resolved_signal=ctrl.reschedule_resolved,
                )
                host = WebHost(bridge=self._web_cal_bridge,
                               channel_name="calendarBridge", route="/calendar",
                               log_name="alma.enablement.web.calendar",
                               extra_bridges={
                                   "almaBridge": self._get_web_chat_bridge()})
                return ctrl, host
        page = CalendarPage()
        return page, self._scroll(page)

    def _web_reschedule_confirm(self, task, new_date) -> bool:
        """The layer-4 human gate for a web-calendar drag: a NATIVE QMessageBox
        no page script can click. Defaults to No."""
        from PySide6.QtWidgets import QMessageBox
        title = str(task.get("title") or "Task")
        if len(title) > 78:
            title = title[:77] + "…"
        return QMessageBox.question(
            self, "Move task",
            f"Move “{title}” to {self._fmt_due(new_date)} ({new_date})?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def _get_web_chat_bridge(self):
        """One ChatBridge over THIS page's enablement ChatEngine, published as
        ``almaBridge`` on every web tab's channel (M5.5) — so the in-page Renn
        drawer is the SAME assistant, session, and canvas coupling as the Qt
        ChatPanel, just rendered by the updated web chat. Built once; None when
        the engine is unavailable (the drawer shows its unavailable state and
        the web button falls back to the Qt drilldown)."""
        if getattr(self, "_web_chat_bridge", None) is not None:
            return self._web_chat_bridge
        if self._engine is None:
            return None
        try:
            from src.ui.web.chat_bridge import ChatBridge
            self._web_chat_bridge = ChatBridge(
                self._engine, send_fn=self._web_chat_send,
                tool_poll=self._web_chat_tool_poll, parent=self)
        except Exception:  # noqa: BLE001 — chat degrades, the tabs still work
            self._web_chat_bridge = None
        return self._web_chat_bridge

    def _web_chat_send(self, text):
        """A web-drawer send: mirror the user turn into the Qt ChatPanel (the
        canonical transcript) AND to every open drawer, then dispatch to the
        shared engine. The originating drawer already rendered its own bubble
        and dedupes this echo (by text); a second drawer (flag=all) renders it,
        so the transcript stays coherent across surfaces."""
        # Refuse a send while a publish confirm modal is open: the modal blocks
        # human input, so any send arriving now is a page script — and a chat
        # revise_draft during the publish would write the DB out from under the
        # confirmed content. (Zero legit-UX cost: a human can't send then.)
        if getattr(getattr(self, "workbench", None), "is_publish_inflight", False):
            return
        try:
            self.chat.add_message("u", text)
        except Exception:  # noqa: BLE001
            pass
        self._mirror_user_turn(text)
        self._dispatch_chat(text)

    def _mirror_user_turn(self, text):
        """Broadcast a user turn to the web drawers (they dedupe their own)."""
        bridge = getattr(self, "_web_chat_bridge", None)
        if bridge is not None:
            bridge.push_notice("u", text)

    def _web_chat_tool_poll(self, since_id: int = 0) -> list[dict]:
        """Newly-finished tool executions for this page's chat session — the
        drawer's live tool rows (same query as the Agent's timeline)."""
        session_id = getattr(self, "_chat_session_id", None)
        db_path = self._engine_db_path()
        if not session_id or not db_path:
            return []
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(db_path, readonly=True)
            try:
                rows = conn.execute(
                    "SELECT rowid, tool_name, result_rows, elapsed_ms, error "
                    "FROM chat_tool_executions WHERE session_id=? AND rowid>? "
                    "ORDER BY rowid", (session_id, int(since_id))).fetchall()
            finally:
                conn.close()
            return [{"id": r[0], "name": r[1], "rows": r[2],
                     "ms": round(r[3] or 0), "ok": not r[4], "error": r[4]}
                    for r in rows]
        except Exception:  # noqa: BLE001 — the timeline is best-effort
            return []

    def _chat_say(self, role, text):
        """Say something in Renn's transcript everywhere it renders: the Qt
        ChatPanel (canonical) AND any open web drawer (bridge notice). Engine
        turns do NOT come through here — the bridge already relays those."""
        try:
            self.chat.add_message(role, text)
        except Exception:  # noqa: BLE001
            pass
        bridge = getattr(self, "_web_chat_bridge", None)
        if bridge is not None:
            bridge.push_notice(role, text)

    def _make_workbench(self):
        """The Workbench tab: the React/WebHost build behind
        ``enablement.web_tabs: all``, else the native Qt WorkbenchPage. The
        controller mirrors WorkbenchPage's surface (data setters, signals, and
        the ``_current_drafts`` attribute this file reads), so every feed and
        connect works identically. Returns (workbench, tab_widget)."""
        from src.ui.web.web_flags import web_tabs_mode
        if web_tabs_mode() == "all":
            try:
                from src.services.enablement_web import WorkbenchWebController
                from src.ui.web.web_host import WebHost
                from src.ui.web.workbench_bridge import WorkbenchBridge
            except Exception:  # noqa: BLE001 — WebEngine absent → native tab
                pass
            else:
                from src.data.enablement_checks import run_checks
                ctrl = WorkbenchWebController(
                    checks_fn=lambda md: run_checks(md),
                    # M4: publishing from the web menu confirms via a NATIVE
                    # dialog (unreachable from page scripts) before page.py's
                    # existing _on_publish path runs.
                    publish_confirm_fn=self._web_workbench_publish_confirm,
                    # M6 fix: the publish belt compares the DB body publish_draft
                    # will actually read (guards a cache/DB divergence from a
                    # de-focused revise).
                    content_lookup=self._web_publish_content_lookup,
                    # M6 fix: refuse publish while a chat turn (which may run a
                    # revise_draft tool that writes the DB) is in flight.
                    busy_lookup=lambda: bool(
                        getattr(self, "_engine", None) is not None
                        and self._engine.is_busy))
                self._web_wb_bridge = WorkbenchBridge(
                    data_signal=ctrl.workbench_data,
                    draft_signal=ctrl.draft_loaded,
                    diff_signal=ctrl.diff_ready,
                    preview_signal=ctrl.preview_updated,
                    cards_signal=ctrl.existing_cards_data,
                    publish_signal=ctrl.publish_resolved,
                    ai_resolved_signal=ctrl.ai_edit_resolved,
                    refresh_fn=ctrl.request_refresh,
                    switch_fn=ctrl.js_switch_workspace,
                    close_fn=ctrl.js_close_workspace,
                    diff_fn=ctrl.js_request_diff,
                    upload_fn=ctrl.js_upload,
                    find_task_fn=ctrl.js_find_task,
                    open_chat_fn=ctrl.js_open_chat,
                    edit_fn=ctrl.js_content_edited,
                    ai_edit_fn=ctrl.js_ai_edit,
                    publish_fn=ctrl.js_request_publish,
                    import_fn=ctrl.js_request_import,
                    cards_fn=ctrl.js_existing_cards,
                )
                # accept_drops: a file dropped anywhere on the tab lands on the
                # Qt host (web File objects carry no paths) and rides the same
                # load_file_requested path as the Qt drop zone.
                host = WebHost(bridge=self._web_wb_bridge,
                               channel_name="workbenchBridge",
                               route="/workbench", accept_drops=True,
                               log_name="alma.enablement.web.workbench",
                               extra_bridges={
                                   "almaBridge": self._get_web_chat_bridge()})
                host.dropped.connect(ctrl.load_file_requested)
                ctrl.upload_requested.connect(self._web_workbench_upload)
                return ctrl, host
        page = WorkbenchPage()
        return page, page

    def _web_workbench_publish_confirm(self, dest_key: str, title: str) -> bool:
        """The layer-4 human gate for a web-workbench publish: a NATIVE
        QMessageBox no page script can click. Defaults to No."""
        from PySide6.QtWidgets import QMessageBox
        what = {
            "guru_new": "publish this draft as a NEW Guru card",
            "drive_new": "save this draft as a new Google Doc",
            "drive_update": "update the selected Google Doc from this draft",
        }.get(dest_key)
        if what is None and dest_key.startswith("guru_existing:"):
            what = f"update Guru card “{dest_key.split(':', 1)[1]}” from this draft"
        if len(title) > 60:
            title = title[:59] + "…"
        return QMessageBox.question(
            self, "Confirm publish",
            f"“{title}” — {what}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes

    def _web_publish_content_lookup(self, draft_id):
        """The draft's current stored body — the exact bytes store.publish_draft
        will read. The web publish belt snapshots this at confirm-open and
        re-checks it at approve, so a body the operator didn't see can't ship."""
        from src.data import enablement_store as store
        draft = store.get_draft(self._conn(), int(draft_id))
        return (draft or {}).get("content")

    def _web_workbench_upload(self):
        """The web Upload button → the NATIVE file dialog (the page never sees
        the filesystem); the chosen path rides the normal load flow."""
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload a document", "",
            "Documents (*.docx *.md *.markdown *.txt *.csv);;All files (*)")
        if path:
            self._on_load_file(path)

    def _web_brief_lookup(self, task_id):
        """The web calendar's on-hover brief join — the same lazy contract as
        the detail panel (_show_task_detail): the feed stays lean, the stored
        Haiku brief loads per-task on demand. None when absent/unparseable."""
        import json as _json
        from src.data import enablement_tasks
        from src.data.connection_factory import get_connection
        conn = get_connection(self._engine_db_path())
        try:
            fresh = enablement_tasks.get_task(conn, task_id) or {}
        finally:
            conn.close()
        if fresh.get("brief_status") == "ok" and fresh.get("brief_json"):
            try:
                return _json.loads(fresh["brief_json"])
            except (ValueError, TypeError):
                return None
        return None

    def _on_scan(self):
        if not self.demo:
            return
        try:
            self._set_status("Scanning the watched Drive folder…")
            from src.data import enablement_store as store
            from src.data.enablement_sim import run_simulation, seed_demo_tasks
            db = self._ensure_demo_db()
            summary = run_simulation(db.conn, publish=False)
            all_tasks = seed_demo_tasks(db.conn)
            # drive the Task list + Calendar from the real demo-DB tasks
            rows = self._tasks_to_rows(all_tasks, db.conn)
            self._all_tasks = rows
            self.tasks.load_tasks(rows)
            self.calendar.set_tasks(all_tasks)
            drafts = store.list_drafts(db.conn, status="pending")
            self._drafts = {int(d["id"]): d for d in drafts}
            chips = [{"id": int(d["id"]),
                      "title": (d["title"] or "Untitled").split(" — ")[0],
                      "source": "drive"} for d in drafts]
            if drafts:
                self.workbench.set_pending_drafts(chips, active_id=int(drafts[0]["id"]))
                self.workbench.show_draft(self._card_from_draft(db.conn, drafts[0]))
            else:
                self.workbench.set_pending_drafts(chips)
            self._set_status(
                f"Indexed {len(summary['documents'])} docs → {len(drafts)} drafts, "
                f"{len(all_tasks)} tasks. Review in the Workbench."
            )
            self._chat_say(
                "a",
                f"Scan complete — pulled {len(summary['documents'])} docs from the Drive and "
                f"drafted {len(drafts)} Guru cards. Pick one above to review, then say “push to Guru”."
            )
            self.tabs.setCurrentWidget(self._workbench_tab)
            self._open_chat()
        except Exception as exc:  # noqa: BLE001 — surface in the status line
            self._set_status(f"Demo scan error: {exc}")

    def on_page_shown(self):
        """Refresh hook fired by MainWindow when this page becomes active again.

        Pages are built once, so returning here would otherwise show whatever
        was loaded on first mount. This re-reads the local DB (cheap — no
        network; a scan/monitor tick is still what pulls fresh remote data), so
        a surface the operator left is current when they come back."""
        try:
            self._load_live()
        except Exception:  # noqa: BLE001 — a refresh must never break navigation
            pass

    def _load_live(self, prefer_draft_id=None):
        """Reload the four pages from the active DB (warehouse, or the demo DB in
        demo mode). Keeps prefer_draft_id in focus if it's still a pending draft."""
        from src.data import enablement_store as store
        from src.data import enablement_tasks as tasks
        conn = self._conn()
        task_list = tasks.list_tasks(conn, **self._list_filters())
        rows = self._tasks_to_rows(task_list, conn)
        self._all_tasks = rows
        self.tasks.load_tasks(rows)
        # Feed the calendar the SAME enriched rows the list uses, so a chip's
        # payload carries the detail-panel-ready fields (description, submitter,
        # source_url, subtasks) and the two views never disagree.
        cal_rows = list(rows)
        try:
            from src.data import guru_analytics as ga
            for c in ga.cards_due_for_update(conn):
                cal_rows.append({
                    "due_date": c.get("due_date", ""),
                    "source": "guru",
                    "title": f"Card due: {c.get('title', '')}",
                    "kind": "guru_card_due",
                    "card_id": c.get("card_id", ""),
                })
        except Exception:  # noqa: BLE001 — analytics tables may not exist yet
            pass
        self.calendar.set_tasks(cal_rows)
        drafts = store.list_drafts(conn, status="pending")
        self._drafts = {int(d["id"]): d for d in drafts}
        chips = [{"id": int(d["id"]),
                  "title": (d["title"] or "Untitled").split(" — ")[0],
                  "source": "drive"} for d in drafts]
        active_id = None
        if prefer_draft_id is not None and int(prefer_draft_id) in self._drafts:
            active_id = int(prefer_draft_id)
        elif drafts:
            active_id = int(drafts[0]["id"])
        if active_id is not None:
            self.workbench.set_pending_drafts(chips, active_id=active_id)
            self.workbench.show_draft(self._card_from_draft(conn, self._drafts[active_id]))
        else:
            self.workbench.set_pending_drafts(chips)
        self._refresh_analytics()
        self._load_pptx()
        self._load_zendesk()
        self._set_status(f"{len(rows)} tasks · {len(drafts)} pending drafts.")

    # ── "only mine" task scope (M2) ───────────────────────────────
    def _list_filters(self) -> dict:
        """The 'only mine' filter for list_tasks when scope='mine' and an identity
        is set; empty (show-all) otherwise so an un-onboarded operator isn't
        staring at a blank board."""
        if getattr(self, "_task_scope", "mine") != "mine":
            return {}
        from src.data import enablement_identity as ident
        idn = ident.operator_identity()
        gid = idn.get("asana_gid") or ""
        # assignee stores the Asana display NAME / gid, never an email — so an
        # email-only identity must NOT become a name filter (it matches nothing);
        # fall through to show-all instead of a silently empty board.
        name = idn.get("name") or ""
        if not gid and not name:
            return {}
        filt: dict = {}
        if gid:
            filt["assignee_gid"] = gid
        if name:
            filt["assignee"] = name
        return filt

    def _on_scope_changed(self, scope: str):
        """Mine/All toggle → persist, mirror to the other view (no echo), reload."""
        self._task_scope = scope
        from src.data.settings_manager import update_section
        update_section("enablement", {"tasks_default_scope": scope})
        self.tasks.set_scope(scope)
        self.calendar.set_scope(scope)
        self._load_live()

    def _on_scan_now(self):
        """Live 'Scan now' — trigger the monitor if wired (Phase 6), else reload from DB."""
        if self._monitor is not None:
            self._set_status("Scanning your configured sources…")
            self._monitor.scan_all()      # emits changed → _load_live
        else:
            self._load_live()
            self._open_chat()

    def _on_draft_selected(self, draft_id):
        d = self._drafts.get(int(draft_id))
        if d:
            # switch_workspace preserves each workspace's unsaved edits + view + cursor
            self.workbench.switch_workspace(
                int(draft_id), self._card_from_draft(self._conn(), d))

    def _open_task_search(self):
        """'+ Find a task' → search drafts and open the chosen one as a workspace."""
        from src.ui.pages.enablement.task_search import TaskSearchDialog
        conn = self._conn()
        dlg = TaskSearchDialog(conn, self)
        if dlg.exec() and dlg.selected_draft_id is not None:
            self._open_draft_workspace(int(dlg.selected_draft_id))

    def _open_draft_workspace(self, draft_id: int):
        """Load a draft (even if not currently in the pending set) into a workspace."""
        from src.data import enablement_store as store
        conn = self._conn()
        d = self._drafts.get(draft_id) or store.get_draft(conn, draft_id)
        if not d:
            self._set_status("That draft could not be opened.")
            return
        self._drafts[draft_id] = d
        chip = {"id": draft_id,
                "title": (d.get("title") or "Untitled").split(" — ")[0],
                "source": "drive"}
        self.workbench.open_workspace(chip)
        self.workbench.show_draft(self._card_from_draft(conn, d))
        self.tabs.setCurrentWidget(self._workbench_tab)

    def _on_workspace_closed(self, draft_id):
        """Close a workspace chip; activate the next open one (if any)."""
        cur = [d for d in self.workbench._current_drafts if d["id"] != int(draft_id)]
        new_active = cur[0]["id"] if cur else None
        self.workbench.set_pending_drafts(cur, active_id=new_active)
        if new_active is not None and new_active in self._drafts:
            self.workbench.show_draft(
                self._card_from_draft(self._conn(), self._drafts[new_active]))

    def _on_chat(self, text: str):
        # ChatPanel already appended the user's bubble; mirror it to any open
        # web drawer (none originated it → all render it), then dispatch.
        self._mirror_user_turn(text)
        self._dispatch_chat(text)

    def _send_quick(self, prompt: str):
        """A quick-action button sends a canned instruction to Renn."""
        self._chat_say("u", prompt)
        self._dispatch_chat(prompt)

    def _dispatch_chat(self, text: str):
        if self._engine is None:
            self._chat_say("a", "The assistant isn't available in this build.")
            return
        if self._engine.is_busy:
            self._chat_say("a", "One moment — I'm still working on the last request.")
            return
        if self.demo:
            self._ensure_demo_db()          # the tools' DB must exist before they run
        self._ensure_session()
        self._prepare_provider()
        self._engine.send(text)

    # ── live assistant (ChatEngine) ───────────────────────────────
    def _engine_db_path(self) -> str | None:
        """The DB the chat tools hit: the demo DB in demo mode, else the warehouse."""
        if self.demo or self.db is None:
            import os
            import tempfile
            return os.path.join(tempfile.gettempdir(), "alma_enablement_demo.db")
        return str(self.db.db_path) if hasattr(self.db, "db_path") else None

    def _setup_engine(self):
        """Build the live ChatEngine (the ACP bridge boots lazily on first send)."""
        try:
            from src.services.chat_engine import ChatEngine
            self._engine = ChatEngine(
                system_prompt=RENN_SYSTEM_PROMPT,
                task_type="enablement_chat",
                context_provider=self._chat_context,
                tools_enabled=True,
                use_mcp_tools=True,
                db_path=self._engine_db_path(),
                # M5.5: the web Renn drawer streams; response_ready still
                # finalizes whole turns, so the Qt ChatPanel is unaffected.
                stream=True,
            )
            self._engine.response_ready.connect(self._on_engine_response)
            self._engine.error_occurred.connect(self._on_engine_error)
            self._engine.bridge_recycle_requested.connect(self._on_bridge_recycle)
        except Exception as exc:  # noqa: BLE001 — chat degrades, the page still works
            logger.warning("Enablement chat engine unavailable: %s", exc)
            self._engine = None

    def _chat_context(self, user_message, history):
        try:
            from src.data import enablement_store as store
            from src.data import enablement_tasks as tasks
            conn = self._conn()
            n_docs = len(store.list_documents(conn, limit=500))
            n_drafts = len(store.list_drafts(conn, status="pending"))
            n_open = len(tasks.list_tasks(conn, status="open", limit=500))
            active = self.workbench.active_draft_id
            active_line = f" The active draft id is {active}." if active else ""
            from src.data import enablement_identity as ident
            who = ident.operator_email(resolve=False)
            who_line = f" Operator: {who}." if who else ""
            kb_line = ""
            try:
                from src.data.kb import store as kb_store
                n_cards = kb_store.cards_count(conn)
                if n_cards:
                    n_topics = conn.execute(
                        "SELECT COUNT(*) FROM kb_folders WHERE role='topic' "
                        "AND status='ok'").fetchone()[0]
                    kb_line = (f" KB: {n_cards} cards across {n_topics} topics "
                               f"(kb_search checks it first).")
            except Exception:  # noqa: BLE001 — scope line is enrichment
                kb_line = ""
            scope = (
                f"[ENABLEMENT SCOPE] {n_docs} indexed documents, {n_drafts} pending card "
                f"drafts, {n_open} open tasks.{who_line}{kb_line}{active_line} Tools: search_local_documents / "
                f"query_business_drive to find content; revise_draft + push_guru_draft to work "
                f"a card; create_task / list_tasks / draft_subtasks to manage work; "
                f"asana_discover + set_asana_board_config to set up an Asana board."
            )
            block = self._greeting_block
            if block:
                self._greeting_block = None   # one-shot: inject the plan on the first turn only
                return block + "\n\n" + scope
            return scope
        except Exception:
            return ""

    def _open_greeting(self):
        """One-shot proactive greeting: arm the [TODAY'S PLAN] context block and
        auto-send a summarize-only opening turn (no user bubble). No-ops without
        an engine, identity, or dated tasks (fail-closed)."""
        if self._greeting_sent or self._engine is None:
            return
        try:
            from src.data import startup_greeting
            block = startup_greeting.build_greeting_block(self._conn())
        except Exception:  # noqa: BLE001 — a greeting must never break page load
            block = ""
        if not block:
            return
        self._greeting_block = block
        self._greeting_sent = True
        trigger = ("Give me my morning briefing: what is due today, what is overdue, "
                   "and what I should tackle first. Do not spend any budget on research "
                   "— just summarize my current tasks.")
        self._dispatch_chat(trigger)

    def select_tab(self, key: str):
        """Select a tab by key — the enablement-mode sidebar drives this."""
        w = getattr(self, "_tab_widgets", {}).get(key)
        if w is not None:
            self.tabs.setCurrentWidget(w)

    def set_tab_bar_visible(self, visible: bool):
        """Hide the internal tab bar when the sidebar owns navigation."""
        self.tabs.tabBar().setVisible(visible)

    def _build_mcp_config(self) -> list[dict]:
        import sys
        from pathlib import Path
        db_path = self._engine_db_path() or ""
        pointer = str(Path(db_path).parent / ".current_chat_session") if db_path else ""
        env = [
            {"name": "ALMA_DB_PATH", "value": db_path},
            {"name": "ALMA_CHAT_SESSION_FILE", "value": pointer},
        ]
        try:
            from src.ui import app_modes
            if app_modes.current_mode() == app_modes.MODE_ENABLEMENT:
                # Keeps the torch/Qwen3 stack unloadable in enablement mode
                env.append({"name": "ALMA_MCP_EXCLUDE_TOOLS",
                            "value": "semantic_search"})
        except Exception:
            pass
        return [{
            "name": "alma-chat-tools",
            "command": sys.executable,
            "args": ["-m", "src.mcp.chat_mcp_server"],
            "env": env,
        }]

    def _ensure_session(self):
        if self._chat_session_id or self._engine is None:
            return
        try:
            from pathlib import Path
            from src.services.chat_session import resolve_or_create_session
            db_path = self._engine_db_path()
            pointer_dir = Path(db_path).parent if db_path else None
            # Share ONE session with the Agent page via the pointer file, so the
            # two surfaces are the same conversation (finding 5).
            self._chat_session_id = resolve_or_create_session(
                "enablement", self._conn(), pointer_dir=pointer_dir)
            self._engine.set_session_id(self._chat_session_id)
        except Exception as exc:  # noqa: BLE001 — telemetry only; tools still work
            logger.debug("enablement session create failed: %s", exc)

    def _prepare_provider(self):
        """Wire Renn's tools for the active provider.

        BOTH providers need the chat-tool MCP server wired the SAME way, or Renn
        has no tools: the Gemini warm bridge gets ``set_mcp_config`` (ACP native
        tools); the Claude/Bedrock CLI client ALSO needs ``set_mcp_config`` so
        ``claude -p`` runs the tool loop natively (Claude refuses text-injected
        tool results). Previously the Claude branch did ``set_client(None)`` and
        the per-message client was never given the MCP config — so Renn on the
        Claude route had ZERO tools (the UAT failure).
        """
        try:
            from src.gemini.client_factory import resolve_provider_for_task
            prov = resolve_provider_for_task("enablement_chat")
        except Exception:
            prov = "gemini"
        if prov == "gemini":
            self._teardown_claude_client()
            self._ensure_warm_bridge()
        else:
            self._teardown_warm_bridge()
            self._ensure_claude_client()

    def _ensure_warm_bridge(self):
        if self._warm_bridge is not None or self._engine is None:
            return
        try:
            from src.agents.report_bridge_client import ReportBridgeClient
            bridge = ReportBridgeClient(model="gemini-2.5-flash")
            bridge.set_mcp_config(self._build_mcp_config())
            self._warm_bridge = bridge
            self._engine.set_client(bridge)
        except Exception as exc:  # noqa: BLE001 — fall back to build-per-message
            logger.warning("Enablement warm bridge boot failed: %s", exc)
            self._warm_bridge = None

    def _ensure_claude_client(self):
        """Build a native-MCP-wired Claude CLI client (the Bedrock/Claude route).

        Mirrors _ensure_warm_bridge: the chat_mcp tool server is wired via
        ``set_mcp_config`` so ``claude -p`` exposes the enablement tools to Claude
        natively and runs the tool loop itself. Kept warm + reused; falls back to
        a per-message client (no tools) only if the build fails.
        """
        if self._engine is None:
            return
        if self._claude_client is not None:
            self._engine.set_client(self._claude_client)
            return
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("enablement_chat")
            if client is not None and hasattr(client, "set_mcp_config"):
                client.set_mcp_config(self._build_mcp_config())
            self._claude_client = client
            self._engine.set_client(client)
        except Exception as exc:  # noqa: BLE001 — degrade to a per-message client
            logger.warning("Enablement Claude client wiring failed: %s", exc)
            self._claude_client = None
            try:
                self._engine.set_client(None)
            except Exception:
                pass

    def _teardown_warm_bridge(self):
        if self._warm_bridge is not None:
            try:
                self._warm_bridge.shutdown()
            except Exception:
                pass
            self._warm_bridge = None

    def _teardown_claude_client(self):
        if self._claude_client is not None:
            try:
                self._claude_client.shutdown()
            except Exception:
                pass
            self._claude_client = None

    def _on_bridge_recycle(self):
        # Degraded-quality recycle (or provider switch): drop whichever warm
        # client we hold so the next send rebuilds + re-wires the MCP tools.
        self._teardown_warm_bridge()
        self._teardown_claude_client()

    def _on_engine_response(self, text: str):
        self.chat.add_message("a", text)
        # A tool may have revised / pushed / created — refresh the views, keeping the
        # active draft in focus if it's still pending.
        try:
            self._load_live(prefer_draft_id=self.workbench.active_draft_id)
            # A revise_draft / update_card_from_doc turn replaced the active draft's
            # content server-side; reload its canvas in place so the conversational
            # edit shows up without a manual refresh.
            self._reload_active_draft_canvas()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Refresh error: {exc}")

    def _on_engine_error(self, message: str):
        self.chat.add_message("a", f"Sorry — I hit an error: {message}")

    # ── live connections + existing Guru cards (Phase 7) ──────────
    def _fetch_existing_cards(self):
        """Populate the Workbench 'Existing Guru card' submenu with real cards.

        Always feeds set_existing_cards a definite result (even []), so the web
        submenu — which resets to a spinner on every open and waits for a push —
        shows 'No cards found' instead of hanging. The one-shot flag only guards
        the (network) Guru fetch; the web controller re-serves its cache on
        repeat opens, so this fetching once is enough."""
        if self._existing_cards_loaded:
            return
        if self.demo or self._guru_client is None:
            # No live source to query — hand the submenu an empty result so it
            # resolves. Not flagged loaded, so a later live connect can refetch.
            self.workbench.set_existing_cards([])
            return
        try:
            cards = self._guru_client.search_cards("") or []
            rows = [{"id": c.get("id"),
                     "title": c.get("preferredPhrase") or c.get("title") or c.get("id")}
                    for c in cards[:25] if c.get("id")]
            self.workbench.set_existing_cards(rows)   # always, even when empty
            self._existing_cards_loaded = True
        except Exception as exc:  # noqa: BLE001
            self.workbench.set_existing_cards([])     # unwedge the spinner
            self._chat_say("a", f"Couldn't load Guru cards: {exc}")

    def check_connections(self):
        """Live-mode only: verify each source connection off the UI thread."""
        if self.demo:
            return
        import threading
        threading.Thread(target=self._check_connections_worker, daemon=True).start()

    def _check_connections_worker(self):
        # Asana
        try:
            from src.data import asana_setup
            from src.data.asana_client import AsanaClient
            if asana_setup.is_asana_connected():
                ok, msg = AsanaClient.from_store().test_connection()
            else:
                ok, msg = False, "no credentials"
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        self.connection_status_ready.emit("asana", bool(ok), "" if ok else (msg or "not connected"))
        # Guru
        try:
            from src.data.guru_client import GuruClient
            email, token = GuruClient.load_credentials()
            if email and token:
                ok = bool(GuruClient(email, token).test_connection())
                msg = "" if ok else "auth failed"
            else:
                ok, msg = False, "no credentials"
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        self.connection_status_ready.emit("guru", bool(ok), "" if ok else msg)
        # Drive
        try:
            from src.data.drive_reader import DriveReader
            reader = DriveReader.from_settings()
            if reader.is_configured():
                ok, msg = reader.test_connection()
            else:
                ok, msg = False, "not configured"
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        self.connection_status_ready.emit("drive", bool(ok), "" if ok else (msg or "not configured"))

    # ── operator identity (M1) ────────────────────────────────────
    def _on_detect_operator_email(self):
        """Resolve the operator email from the connected Google account off-thread."""
        import threading
        threading.Thread(target=self._detect_operator_email_worker, daemon=True).start()

    def _detect_operator_email_worker(self):
        try:
            from src.data import google_oauth
            profile = google_oauth.fetch_account_profile()
        except Exception:  # noqa: BLE001
            profile = {}
        email = profile.get("email") or ""
        if email:
            from src.data.settings_manager import get_section
            gid = (get_section("enablement", {}) or {}).get("operator_asana_gid", "") or ""
            # Carry Google's display name — the "only mine" filter matches Asana
            # assignees by name until a GID is resolved.
            self.identity_resolved.emit(email, gid, profile.get("name") or "")
        else:
            self.connection_status_ready.emit(
                "drive", False, "Connect your Google account first")

    def _on_resolve_operator_gid(self):
        import threading
        threading.Thread(target=self._resolve_operator_gid_worker, daemon=True).start()

    def _resolve_operator_gid_worker(self):
        try:
            from src.data.asana_client import AsanaClient
            who = AsanaClient.from_store().whoami()
        except Exception:  # noqa: BLE001
            who = {}
        gid = who.get("gid", "")
        if gid:
            from src.data.settings_manager import get_section
            cfg = get_section("enablement", {}) or {}
            email = cfg.get("operator_email", "") or who.get("email", "") or ""
            self.identity_resolved.emit(email, gid, who.get("name", ""))
        else:
            self.connection_status_ready.emit(
                "asana", False, "Connect Asana first (paste your API key)")

    def _on_identity_resolved(self, email: str, asana_gid: str, asana_name: str):
        """Main-thread slot: persist the resolved identity + reflect it in Settings.

        A deliberate Settings override wins over auto-detect (the precedence
        contract in enablement_identity): the detected email is always cached, but
        only populates the override when none is set."""
        from src.data.settings_manager import get_section, update_section
        override = ((get_section("enablement", {}) or {}).get("operator_email") or "").strip()
        updates: dict = {}
        if email:
            updates["detected_email"] = email
            if not override:
                updates["operator_email"] = email
        if asana_gid:
            updates["operator_asana_gid"] = asana_gid
        if asana_name:
            updates["operator_name"] = asana_name
        if updates:
            update_section("enablement", updates)
        shown = override or email
        if shown:
            self.settings.set_operator_email(shown)
        if asana_gid:
            self.settings.set_operator_asana_gid(asana_gid, asana_name)

    # ── Drive folder config (Settings → monitor_sources) ──────────
    def _add_drive_folder(self, folder_id: str, name: str):
        try:
            from src.data import enablement_sources as sources
            sources.add_source(self._conn(), source_type="drive",
                               source_id=f"drive:{folder_id}", display_name=name,
                               config={"folder_id": folder_id, "recursive": True})
            self._refresh_drive_folders()
            self._set_status(f"Watching Drive folder “{name}”.")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Couldn't add folder: {exc}")

    def _refresh_drive_folders(self):
        # don't force-create the demo DB just to list folders
        if self.demo and self._demo_db is None:
            return
        try:
            from src.data import enablement_sources as sources
            self.settings.set_drive_folders(sources.list_sources(self._conn(), "drive"))
        except Exception:  # noqa: BLE001
            pass

    def _on_push(self, draft_id):
        try:
            did = int(draft_id)
        except (TypeError, ValueError):
            self._chat_say("a", "No draft selected to push.")
            return
        from src.data import enablement_store as store
        try:
            res = store.publish_draft(
                self._conn(), did,
                guru_client=self._guru_for_push(),
                collection_id=self._publish_collection_id(),
            )
        except Exception as exc:  # noqa: BLE001 — surface in chat
            self._chat_say("a", f"Publish failed: {exc}")
            return
        if res.get("ok"):
            where = "Provider Enablement" if self.demo else "Guru"
            self._chat_say("a", f"Published the draft to {where} and re-rendered the live card.")
            self._set_status(f"Draft {did} published{' (demo)' if self.demo else ''}.")
            if not self.demo:
                self._load_live()
        else:
            self._chat_say("a", f"Publish failed: {res.get('error')}")

    def _on_publish(self, dest: str):
        if dest == "guru_new":
            self._on_push(self.workbench.active_draft_id)
            return
        if dest.startswith("guru_existing:"):
            key = dest.split(":", 1)[1]
            if self.demo:
                self._set_status(f"Editing existing Guru card: {key}")
                self.chat.set_chat([("a",
                    f"Loaded the existing Guru card “{key}”. Tell me the inline edits to make "
                    f"(e.g. “add a Rollout section”, “tighten the intro”), then say “push” to update the card.")])
                self._open_chat()
                return
            # Live: stamp the active draft's target card id so publish updates it.
            did = self.workbench.active_draft_id
            try:
                from src.data import enablement_store as store
                store.set_draft_card_id(self._conn(), int(did), key)
            except Exception as exc:  # noqa: BLE001
                self._chat_say("a", f"Couldn't target card {key}: {exc}")
                return
            self._set_status(f"Updating Guru card {key} from the active draft…")
            self._on_push(did)
            return
        msg = {
            "drive_new": "Created a new Google Doc in the Drive from the active draft (demo).",
            "drive_update": "AI is updating the selected Google Doc via the Drive API (demo).",
        }.get(dest, "Done.")
        self._set_status(msg)
        self._chat_say("a", msg)

    def _on_load_file(self, path: str):
        """A dropped / uploaded local file → ingest its text and draft a card
        from it (off-thread; demo uses the stub LLM so it works with no
        network). Results flow back through import_finished → the workbench
        reloads and focuses the new draft."""
        import os
        if not path or not os.path.isfile(path):
            self._set_status("Could not read that file.")
            return
        self._set_status(f"Loading “{os.path.basename(path)}”…")
        if self.demo:
            self._ensure_demo_db()
        import threading
        db_path = self._engine_db_path()
        demo = self.demo

        def worker():
            res = {"ok": False, "kind": "upload"}
            conn = None
            try:
                from src.data.connection_factory import get_connection
                conn = get_connection(db_path)
                res = self._ingest_local_file(conn, path, demo)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "kind": "upload", "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self.import_finished.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _read_local_text(path: str) -> str:
        """Read a dropped/uploaded document into markdown, resolving the
        source formatting (docx headings/bold/lists/links/tables, html, …)
        via the loose document reader."""
        from src.data.doc_reader import read_document
        return read_document(path)

    @staticmethod
    def _ingest_local_file(conn, path: str, demo: bool = False) -> dict:
        """Save a local file as an enablement document and build a card from it
        with a DETERMINISTIC Python conversion — no LLM (rendering a doc into a
        card is a text transformation, not a reasoning task). The same in demo
        and live; AI summarising stays an explicit "polish with AI" via Renn.
        Synchronous + connection-injected so it's unit-testable."""
        import os
        from src.data import enablement_store as store
        name = os.path.basename(path)
        text = EnablementPage._read_local_text(path)
        # Stable source_ref from the file path so re-importing the same file
        # (including after an edit in place) resolves to the SAME document and
        # refreshes its one draft, instead of minting a fresh uuid each time and
        # spawning duplicates (finding 19).
        source_ref = "upload:" + os.path.abspath(path)
        doc_id = store.save_document(conn, source="upload", name=name,
                                     source_ref=source_ref, full_text=text)
        draft = store.draft_card_from_document(conn, doc_id)   # deterministic
        return {"ok": True, "kind": "upload", "name": name,
                "chars": len(text), "draft_id": draft.get("id")}

    # ── workbench imports (Google Doc / existing Guru card) ────────

    def _on_import_requested(self, kind: str):
        if self.demo:
            self._demo_import(kind)
            return
        if kind == "guru":
            if self._guru_client is None:
                self._set_status("Connect Guru first (Settings → Guru).")
                return
            from src.ui.pages.enablement.card_picker import GuruCardPickerDialog
            dlg = GuruCardPickerDialog(self._guru_client, self)
            if dlg.exec() and dlg.selected_card_id:
                self._run_import("guru", dlg.selected_card_id)
        else:
            from PySide6.QtWidgets import QInputDialog
            ref, ok = QInputDialog.getText(
                self, "Import from Drive", "Google Doc / Drive URL or file id:"
            )
            if ok and ref.strip():
                self._run_import("drive", ref.strip())

    def _run_import(self, kind: str, ref: str):
        """Network imports run off-thread; results come back via the
        import_finished signal (same pattern as check_connections)."""
        import threading
        self._set_status("Importing…")
        db_path = self._engine_db_path()
        client = self._guru_client

        def worker():
            from src.data import enablement_store as store
            from src.data.connection_factory import get_connection
            res = {"ok": False, "error": "unknown"}
            conn = None
            try:
                conn = get_connection(db_path)
                if kind == "guru":
                    res = store.import_guru_card_to_draft(conn, client, ref)
                else:
                    from src.data.drive_reader import DriveReader
                    reader = DriveReader.from_settings()
                    if not reader.is_configured():
                        res = {"ok": False, "error":
                               "Drive read is not configured (Settings → Drive)."}
                    else:
                        res = store.import_drive_doc(conn, reader, ref)
                        if res.get("ok") and kind == "style":
                            doc = store.get_document(conn, res["doc_id"]) or {}
                            store.set_style_guide(
                                conn, doc.get("full_text", ""),
                                name=doc.get("name") or "Card style guide",
                            )
                        elif res.get("ok") and kind == "template":
                            doc = store.get_document(conn, res["doc_id"]) or {}
                            store.set_card_template(
                                conn, doc.get("full_text", ""),
                                name=doc.get("name") or "Card/article template",
                            )
                        elif res.get("ok"):
                            try:
                                from src.gemini.client_factory import build_client_for_task
                                llm = build_client_for_task("enablement_card_gen")
                                draft = store.draft_card_from_document(
                                    conn, res["doc_id"], llm
                                )
                                res["draft_id"] = draft.get("id")
                            except Exception as exc:  # noqa: BLE001
                                res["draft_error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            res["kind"] = kind
            self.import_finished.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_import_finished(self, res: dict):
        if not res.get("ok"):
            self._set_status(f"Import failed: {res.get('error')}")
            self._chat_say("a", f"Import failed: {res.get('error')}")
            return
        kind = res.get("kind")
        if kind == "style":
            self._set_status("Style guide imported from Drive — generation now follows it.")
            self._refresh_style_guide_status()
            return
        if kind == "template":
            self._set_status("Card template imported from Drive — generation now follows it.")
            self._refresh_card_template_status()
            return
        if kind == "guru":
            self._set_status(
                f"Imported Guru card “{res.get('title', '')}” as an editable draft — "
                "publishing will update the same card."
            )
        else:
            note = f"Imported “{res.get('name', 'document')}” ({res.get('chars', 0):,} chars)"
            if res.get("draft_id"):
                note += f" and drafted a card from it"
            elif res.get("draft_error"):
                note += f" (auto-draft skipped: {res['draft_error']})"
            self._set_status(note + ".")
        self._load_live(prefer_draft_id=res.get("draft_id"))
        if res.get("draft_id"):
            self.select_tab("workbench")

    def _demo_import(self, kind: str):
        """Scripted imports against the demo DB — same store code, canned data."""
        from src.data import enablement_store as store
        conn = self._conn()
        if kind == "guru":
            res = store.import_guru_card_to_draft(
                conn, _DemoGuruClient(), "demo-card-payments"
            )
            self._set_status("Imported demo Guru card as an editable draft (demo).")
            self._load_live(prefer_draft_id=res.get("draft_id"))
        else:
            doc_id = store.save_document(
                conn, source="drive", name="Q3 Pricing Update.gdoc",
                full_text=(
                    "Alma is updating provider pricing tiers effective Aug 1, 2026. "
                    "Tier A keeps current rates; Tier B moves to usage-based billing. "
                    "Support owners should update saved replies by July 15."
                ),
            )
            from src.data.enablement_sim import _StubLLM
            draft = store.draft_card_from_document(conn, doc_id, _StubLLM())
            self._set_status("Imported demo Google Doc and drafted a card (demo).")
            self._load_live(prefer_draft_id=draft.get("id"))

    def _on_content_edited(self, draft_id, md: str):
        """Persist Edit-view changes to the draft (pushed drafts stay frozen).

        Also persists the rich editor's cleaned HTML (when the last edit was
        in rich mode) so the Guru publish path can submit color/highlight;
        a markdown-only edit passes None, clearing stale HTML."""
        from src.data import enablement_store as store
        try:
            did = int(draft_id)
            draft = store.get_draft(self._conn(), did)
            if draft and draft.get("status") != "pushed":
                html = self.workbench.current_html()
                store.update_draft_content(self._conn(), did, content=md, content_html=html)
                # Keep the in-memory _drafts cache in step with the store, or a
                # chip switch-back rebuilds the card from stale pre-edit content
                # (_on_draft_selected reads _card_from_draft(self._drafts[did]))
                # and the committed edit visibly reverts.
                cached = self._drafts.get(did)
                if cached is not None:
                    cached["content"] = md
                    cached["content_html"] = html
                self._set_status(f"Draft {did} updated from the editor.")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Edit save failed: {exc}")

    # ── inline AI edit ("/"-menu + highlight-to-edit → revise active draft) ──

    @staticmethod
    def _scoped_instruction(instruction: str, selection: str) -> str:
        """Fold the highlighted text into the instruction so the revise targets
        just that passage; whole-doc actions (empty selection) pass through."""
        sel = (selection or "").strip()
        if not sel:
            return instruction
        return (f"{instruction}\n\nApply this to the following selected passage "
                f"only, returning the full revised card:\n\"\"\"\n{sel}\n\"\"\"")

    def _clear_web_ai_inflight(self):
        """Release the web publish block if a revise never actually dispatched
        (an _on_ai_edit early-return) — belt-and-suspenders so the block can
        never strand. No-op on the native Qt path."""
        notify = getattr(self.workbench, "notify_ai_edit_done", None)
        if callable(notify):
            notify(False)

    def _on_ai_edit(self, instruction: str, selection: str):
        """Run the rich editor's inline AI edit against the active draft.

        Reuses the chat-tool revise impl (LLM via build_client_for_task) off the
        UI thread; the result returns on the main thread (ai_edit_done) to reload
        the canvas. Guards when there is no active draft."""
        draft_id = self.workbench.active_draft_id
        if not draft_id:
            self._set_status("Open a draft before asking for an AI edit.")
            self._clear_web_ai_inflight()   # never strand the publish block
            return
        if not (instruction or "").strip():
            self._clear_web_ai_inflight()
            return
        import threading
        self._set_status("Renn is revising the draft…")
        if self.demo:
            self._ensure_demo_db()
        db_path = self._engine_db_path()
        prompt = self._scoped_instruction(instruction, selection)
        did = int(draft_id)

        def worker():
            from src.data.chat_tools.enablement_tools import _revise_draft_impl
            from src.data.connection_factory import get_connection
            res = {"ok": False, "draft_id": did}
            conn = None
            try:
                conn = get_connection(db_path)
                res = _revise_draft_impl(conn, did, prompt)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "draft_id": did, "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self.ai_edit_done.emit(res or {})

        threading.Thread(target=worker, daemon=True).start()

    def _on_ai_edit_done(self, res: dict):
        """AI-edit finished off-thread → reload the canvas in place (main thread)."""
        ok = bool(res.get("ok"))
        # Always un-busy the web AI-edit bar, success or failure — otherwise a
        # failed revise (offline CLI, etc.) leaves it stuck on "Renn is
        # revising…" until an unrelated draftLoaded happens to arrive.
        notify = getattr(self.workbench, "notify_ai_edit_done", None)
        if callable(notify):
            notify(ok)
        if not ok:
            self._set_status(f"AI edit failed: {res.get('error', 'unknown')}")
            return
        self._set_status(f"Draft {res.get('draft_id')} revised — reloaded the card.")
        # Refresh the draft the WORKER captured, NOT whatever is active now: a
        # revise dispatched for D1 then de-focused to D2 wrote DB[D1], and
        # refreshing "active" (D2) would leave _drafts[D1]/_cards[D1] stale —
        # so a switch-back (and any publish, which reads the DB) would show/ship
        # divergent content (the operator approves stale, publishes fresh).
        self._reload_active_draft_canvas(res.get("draft_id"))

    def _reload_active_draft_canvas(self, draft_id=None):
        """Re-render a draft's canvas from freshly-stored content so a revise
        (inline AI edit OR a chat turn) shows up without a manual refresh.

        ``draft_id`` defaults to the active draft; pass the revise's captured id
        for a possibly-de-focused revise. The _drafts cache is refreshed for
        that draft either way (so switch-back and publish see the truth); the
        VISIBLE canvas reloads only when that draft is the active one."""
        from src.data import enablement_store as store
        did = draft_id if draft_id is not None else self.workbench.active_draft_id
        if not did:
            return
        try:
            conn = self._conn()
            draft = store.get_draft(conn, int(did))
            if not draft:
                return
            self._drafts[int(did)] = draft
            if int(did) == self.workbench.active_draft_id:
                self.workbench.reload_active_canvas(self._card_from_draft(conn, draft))
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Canvas reload error: {exc}")

    # ── Guru analytics (P7) ─────────────────────────────────────────

    def _refresh_analytics(self):
        """Re-render the Analytics tab from the local store (cheap reads)."""
        try:
            from src.data import guru_analytics as ga
            conn = self._conn()
            if self.demo:
                n = conn.execute(
                    "SELECT COUNT(*) FROM guru_events"
                ).fetchone()[0]
                if n == 0:
                    from src.data.enablement_sim import seed_demo_analytics
                    seed_demo_analytics(conn)
            self.analytics.set_filters(ga.collections(conn), ga.domains(conn))
            self.analytics.set_data(
                ga.verification_kpis(conn),
                ga.top_cards(
                    conn, days=self.analytics.days(),
                    collection_id=self.analytics.collection_id(),
                    domain=self.analytics.domain(),
                ),
                ga.open_comments(conn),
                ga.cards_due_for_update(conn, days=self.analytics.days()),
            )
        except Exception as exc:  # noqa: BLE001 — never break the page
            logger.debug("analytics refresh skipped: %s", exc)

    def _run_analytics_sync(self):
        """Refresh button: live → pull from Guru off-thread; demo → re-seed."""
        if self.demo:
            from src.data.enablement_sim import seed_demo_analytics
            seed_demo_analytics(self._conn())
            self._refresh_analytics()
            self._set_status("Analytics refreshed (demo).")
            return
        import threading
        self._set_status("Syncing Guru analytics…")
        db_path = self._engine_db_path()

        def worker():
            res = {"ok": False, "error": "unknown"}
            conn = None
            try:
                from src.data.connection_factory import get_connection
                from src.data.guru_client import GuruClient
                email, token = GuruClient.load_credentials()
                if not (email and token):
                    res = {"ok": False,
                           "error": "Connect Guru first (Settings → Guru)."}
                else:
                    from src.data import guru_analytics as ga
                    conn = get_connection(db_path)
                    res = ga.sync(conn, GuruClient(email, token))
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self.analytics_synced.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_analytics_synced(self, res: dict):
        # Reload FIRST — its last act sets the "N tasks · M drafts" status —
        # then write the sync outcome, so the outcome is what the operator sees
        # rather than being clobbered by the reload (finding 21).
        self._load_live(prefer_draft_id=self.workbench.active_draft_id)
        if res.get("ok"):
            self._set_status("Guru analytics synced.")
        else:
            sections = res.get("sections") or {}
            errors = "; ".join(
                f"{k}: {v.get('error')}" for k, v in sections.items()
                if isinstance(v, dict) and v.get("error")
            )
            self._set_status(
                f"Analytics sync issue: {res.get('error') or errors}"
            )

    def _on_comment_task(self, comment_id: str):
        try:
            from src.data import guru_analytics as ga
            res = ga.create_task_from_comment(self._conn(), comment_id)
            if res.get("ok"):
                self._set_status("Comment converted to an enablement task.")
                self._load_live()
            else:
                self._set_status(f"Couldn't create task: {res.get('error')}")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Couldn't create task: {exc}")

    def _on_targeted_update(self, card_id: str):
        """Analytics/calendar 'targeted update' → import the card as a draft
        and land in the Workbench (the P4 import flow end-to-end)."""
        if not card_id:
            return
        if self.demo:
            from src.data import enablement_store as store
            res = store.import_guru_card_to_draft(
                self._conn(), _DemoGuruClient(), card_id
            )
            self._load_live(prefer_draft_id=res.get("draft_id"))
            self.select_tab("workbench")
            self._set_status("Card imported for a targeted update (demo).")
            return
        if self._guru_client is None:
            self._set_status("Connect Guru first (Settings → Guru).")
            return
        self._run_import("guru", card_id)

    def _on_calendar_event_activated(self, task: dict):
        if task.get("kind") == "guru_card_due" and task.get("card_id"):
            self._on_targeted_update(task["card_id"])
        elif task.get("task_id"):
            # a real task chip → open its detail panel directly (robust; avoids the
            # fragile label-substring match in _find_task)
            self._show_task_detail(task)

    def _open_source_url(self, url: str):
        """Open a task's source URL in the browser — http/https ONLY (reject
        file:/javascript:/custom schemes from an untrusted Asana permalink)."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        u = QUrl(url)
        if u.scheme().lower() in ("http", "https"):
            QDesktopServices.openUrl(u)
        else:
            self._set_status("Refused to open a non-web source link.")

    def _on_calendar_day(self, iso: str):
        """Calendar '+N more' → show that day's tasks as a compact clickable list."""
        if self._drilldown is None:
            return
        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget
        day_tasks = [t for t in (self._all_tasks or []) if (t.get("due_iso") or "") == iso]
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(6)
        for t in day_tasks:
            # Untrusted Asana title: QPushButton never renders rich text, but '&'
            # becomes a mnemonic (swallowed/underlined) — escape it and cap the
            # length, matching the calendar chips' PlainText hardening (cc4e708).
            b = QPushButton(str(t.get("title") or "Task").replace("&", "&&")[:160])
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#FFFFFF; color:#2C2A26; border:1px solid #E4E1DA; "
                "border-radius:8px; padding:9px 12px; font-size:12.5px; font-weight:600; text-align:left;}"
                "QPushButton:hover{border:1px solid #B7CFC7;}"
            )
            b.clicked.connect(lambda _=False, task=t: self._show_task_detail(task))
            lay.addWidget(b)
        lay.addStretch(1)
        self._drilldown.show_widget(iso, f"{len(day_tasks)} tasks", panel)

    def _on_attention_open(self, card_id: str):
        """Home → 'Open targeted update': land in the Workbench and route the
        card. A staged-draft pseudo id ('draft:<n>') opens that draft directly;
        a real card id flows through the targeted-update import."""
        self.tabs.setCurrentWidget(self._workbench_tab)
        if not card_id:
            return
        if card_id.startswith("draft:"):
            try:
                self._open_draft_workspace(int(card_id.split(":", 1)[1]))
            except (TypeError, ValueError):
                pass
            return
        self._on_targeted_update(card_id)

    # ── PowerPoint (E4) ─────────────────────────────────────────────

    def _load_pptx(self):
        """Refresh the PowerPoint tab's deck list (demo seeds on first load)."""
        try:
            from src.data import pptx_store
            conn = self._conn()
            if self.demo and not pptx_store.list_decks(conn):
                from src.data.enablement_sim import seed_demo_decks
                seed_demo_decks(conn)
            self.pptx.set_decks(pptx_store.list_decks(conn))
        except Exception as exc:  # noqa: BLE001
            logger.debug("pptx load skipped: %s", exc)

    def _on_pptx_deck_selected(self, deck_id: int):
        from src.data import pptx_store
        deck = pptx_store.get_deck(self._conn(), deck_id)
        if deck:
            self.pptx.show_deck(deck)

    def _on_pptx_outline_saved(self, deck_id: int, title: str, outline: dict):
        from src.data import pptx_store
        res = pptx_store.update_deck_outline(self._conn(), deck_id,
                                             title=title, outline=outline)
        if res.get("ok"):
            self.pptx.set_status(f"Saved — {res['slides']} slides.")
            self._load_pptx()

    def _on_pptx_model_topic(self, topic: str):
        """Model a deck from a topic via the LLM (off-thread)."""
        self.pptx.set_status("Modeling a deck…")
        if self.demo:
            self._ensure_demo_db()
        import threading
        db_path = self._engine_db_path()
        demo = self.demo

        def worker():
            res = {"ok": False, "error": "unknown"}
            conn = None
            try:
                from src.data import pptx_store
                from src.data.connection_factory import get_connection
                conn = get_connection(db_path)
                if demo:
                    from src.data.enablement_sim import _StubLLM
                    llm = _StubLLM()
                else:
                    from src.gemini.client_factory import build_client_for_task
                    llm = build_client_for_task("enablement_pptx_gen")
                if llm is None:
                    res = {"ok": False, "error": "no_llm_client"}
                else:
                    res = pptx_store.generate_deck_from_topic(conn, topic, llm)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self.pptx_modeled.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_pptx_modeled(self, res: dict):
        if res.get("ok"):
            self.pptx.set_status(f"Modeled “{res.get('title', '')}” ({res.get('slides', 0)} slides).")
            self._load_pptx()
            self._on_pptx_deck_selected(res["deck_id"])
        else:
            self.pptx.set_status(f"Modeling failed: {res.get('error')}")

    def _on_pptx_export(self, deck_id: int):
        from PySide6.QtWidgets import QFileDialog
        from src.data import pptx_store
        deck = pptx_store.get_deck(self._conn(), deck_id)
        default = (deck.get("title", "deck") if deck else "deck").replace(" ", "_") + ".pptx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export deck", default, "PowerPoint (*.pptx)")
        if not path:
            return
        res = pptx_store.export_pptx(self._conn(), deck_id, path)
        if res.get("ok"):
            self.pptx.set_status(f"Exported {res['slides']} slides → {path}")
            self._load_pptx()
        else:
            self.pptx.set_status(f"Export failed: {res.get('error')}")

    def _on_pptx_doc_dropped(self, path: str):
        """Drag-drop a document → model a deck from it deterministically
        (no LLM): read the doc (resolving formatting) and turn its headings /
        sections into slides."""
        import os
        if not path or not os.path.isfile(path):
            self.pptx.set_status("Could not read that file.")
            return
        try:
            from src.data import pptx_store
            from src.data.doc_reader import read_document
            name = os.path.basename(path)
            # strict: an unreadable format raises rather than decoding garbage
            # into slides — the except below surfaces "couldn't model a deck".
            md = read_document(path, strict=True)
            outline = pptx_store.outline_from_markdown(name, md)
            deck_id = pptx_store.save_deck(
                self._conn(), title=outline["title"], outline=outline,
                source_ref=f"upload:{name}")
            self.pptx.set_status(
                f"Modeled “{outline['title']}” ({len(outline['slides'])} slides) from {name}.")
            self._load_pptx()
            self._on_pptx_deck_selected(deck_id)
        except Exception as exc:  # noqa: BLE001
            self.pptx.set_status(f"Couldn't model a deck: {exc}")

    # ── Zendesk (E5) ────────────────────────────────────────────────

    def _load_zendesk(self):
        try:
            from src.data import zendesk_store
            conn = self._conn()
            if self.demo and not zendesk_store.list_articles(conn):
                from src.data.enablement_sim import seed_demo_zendesk
                seed_demo_zendesk(conn)
            self.zendesk.set_articles(
                len(zendesk_store.list_articles(conn)),
                zendesk_store.list_article_drafts(conn))
            self.zendesk.set_macros(
                len(zendesk_store.list_macros(conn)),
                zendesk_store.list_macro_drafts(conn))
        except Exception as exc:  # noqa: BLE001
            logger.debug("zendesk load skipped: %s", exc)

    def _zendesk_client(self):
        if self.demo:
            return None
        try:
            from src.data.zendesk_client import ZendeskClient
            return ZendeskClient.from_settings()
        except Exception:
            return None

    def _on_zendesk_sync(self):
        if self.demo:
            from src.data.enablement_sim import seed_demo_zendesk
            seed_demo_zendesk(self._conn())
            self._load_zendesk()
            self.zendesk.set_status("Synced (demo).")
            return
        client = self._zendesk_client()
        if client is None:
            self.zendesk.set_status("Connect Zendesk first (Settings).")
            return
        self.zendesk.set_status("Syncing from Zendesk…")
        import threading
        db_path = self._engine_db_path()

        def worker():
            res = {"ok": False}
            conn = None
            try:
                from src.data import zendesk_store
                from src.data.connection_factory import get_connection
                conn = get_connection(db_path)
                a = zendesk_store.sync_articles(conn, client)
                m = zendesk_store.sync_macros(conn, client)
                res = {"ok": a.get("ok") and m.get("ok"),
                       "articles": a.get("count", 0), "macros": m.get("count", 0),
                       "error": a.get("error") or m.get("error")}
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self.zendesk_synced.emit(res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_zendesk_synced(self, res: dict):
        if res.get("ok"):
            self.zendesk.set_status(
                f"Synced {res.get('articles', 0)} articles, {res.get('macros', 0)} macros.")
        else:
            self.zendesk.set_status(f"Sync issue: {res.get('error')}")
        self._load_zendesk()

    def _on_zd_article_selected(self, draft_id: int):
        from src.data import zendesk_store
        d = zendesk_store.get_article_draft(self._conn(), draft_id)
        if d:
            self.zendesk.show_article_draft(d)

    def _on_zd_article_saved(self, draft_id: int, title: str, body_md: str):
        from src.data import zendesk_store
        zendesk_store.update_article_draft(self._conn(), draft_id, title=title, body=body_md)
        self.zendesk.set_status("Article draft saved.")
        self._load_zendesk()

    def _on_zd_article_push(self, draft_id: int):
        from src.data import zendesk_store
        res = zendesk_store.publish_article_draft(
            self._conn(), draft_id, zendesk_client=self._zendesk_client())
        if res.get("ok"):
            self.zendesk.set_status(
                f"Article pushed{' (demo)' if self.demo else ''}.")
            self._load_zendesk()
        else:
            self.zendesk.set_status(f"Push failed: {res.get('error')}")

    def _on_zd_macro_selected(self, draft_id: int):
        from src.data import zendesk_store
        d = zendesk_store.get_macro_draft(self._conn(), draft_id)
        if d:
            self.zendesk.show_macro_draft(d)

    def _on_zd_macro_saved(self, draft_id: int, name: str, reply: str):
        from src.data import zendesk_store
        d = zendesk_store.get_macro_draft(self._conn(), draft_id)
        if not d:
            return
        # keep any non-comment actions; replace the public reply
        actions = [a for a in d.get("actions", [])
                   if a.get("field") not in ("comment_value", "comment_value_html")]
        actions.insert(0, {"field": "comment_value", "value": reply})
        zendesk_store.update_macro_draft(self._conn(), draft_id, name=name, actions=actions)
        self.zendesk.set_status("Macro draft saved.")
        self._load_zendesk()

    def _on_zd_macro_push(self, draft_id: int):
        from src.data import zendesk_store
        res = zendesk_store.publish_macro_draft(
            self._conn(), draft_id, zendesk_client=self._zendesk_client())
        if res.get("ok"):
            self.zendesk.set_status(f"Macro pushed{' (demo)' if self.demo else ''}.")
            self._load_zendesk()
        else:
            self.zendesk.set_status(f"Push failed: {res.get('error')}")

    # ── style guide + card template (tagged guide documents) ────────
    # One generalized handler drives both sections; the per-kind bits
    # (store functions, demo seed, import kind) are parameterized.

    _GUIDE_FILE_FILTER = ("Documents (*.md *.markdown *.txt *.docx *.html *.htm)"
                          ";;All files (*)")
    _GUIDE_EXTS = {".md", ".markdown", ".txt", ".docx", ".html", ".htm"}

    def _on_style_guide_action(self, action: str):
        from src.data import enablement_store as store
        self._guide_action(
            action, label="Style guide", import_kind="style",
            getter=store.get_style_guide, setter=store.set_style_guide,
            clear=store.clear_style_guide, slug_prefix="style-guide",
            refresh=self._refresh_style_guide_status,
            demo_loader=lambda conn: store.set_style_guide(
                conn,
                "Tone: confident, plain language. Cards open with a one-line "
                "summary, use numbered steps for any process, and end with a "
                "short FAQ.",
                name="Enablement style guide (demo)",
            ),
            paste_prompt="Paste the style guide card generation should follow:",
        )

    def _on_card_template_action(self, action: str):
        from src.data import enablement_store as store
        self._guide_action(
            action, label="Card template", import_kind="template",
            getter=store.get_card_template, setter=store.set_card_template,
            clear=store.clear_card_template, slug_prefix="card-template",
            refresh=self._refresh_card_template_status,
            demo_loader=self._load_bundled_card_template,
            paste_prompt="Paste the card/article template (exact headings) "
                         "generation should follow:",
        )

    def _guide_action(self, action: str, *, label, import_kind, getter, setter,
                      clear, slug_prefix, refresh, demo_loader, paste_prompt):
        import os
        conn = self._conn()
        if action == "paste":
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getMultiLineText(
                self, label, paste_prompt, getter(conn))
            if ok:
                if text.strip():
                    setter(conn, text)
                    self._set_status(f"{label} saved — generation now follows it.")
                else:
                    clear()
                    self._set_status(f"{label} cleared.")
        elif action == "upload":
            from PySide6.QtWidgets import QFileDialog
            # Start at home (macOS native dialogs otherwise open on an opaque
            # default) and keep an "All files" fallback so documents the name
            # filter greys out stay reachable. Multi-select: uploading several
            # guides at once stores each; the last becomes active.
            paths, _ = QFileDialog.getOpenFileNames(
                self, f"Upload {label.lower()}", os.path.expanduser("~"),
                self._GUIDE_FILE_FILTER)
            if paths:
                self._store_guide_files(conn, paths, setter, slug_prefix, label)
        elif action == "upload_folder":
            from PySide6.QtWidgets import QFileDialog
            folder = QFileDialog.getExistingDirectory(
                self, f"Upload a folder of {label.lower()}s",
                os.path.expanduser("~"),
                QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks)
            if folder:
                files = self._guide_files_in_folder(folder)
                if not files:
                    self._set_status(
                        "No readable documents (.md .txt .docx .html) in that folder.")
                else:
                    self._store_guide_files(conn, files, setter, slug_prefix, label)
        elif action == "drive":
            if self.demo:
                demo_loader(conn)
                self._set_status(f"Demo {label.lower()} loaded.")
            else:
                from PySide6.QtWidgets import QInputDialog
                ref, ok = QInputDialog.getText(
                    self, f"{label} from Drive", "Google Doc URL or file id:")
                if ok and ref.strip():
                    self._run_import(import_kind, ref.strip())
                    return
        elif action == "clear":
            clear()
            self._set_status(f"{label} cleared.")
        refresh()

    def _store_guide_files(self, conn, paths, setter, slug_prefix, label):
        """Read each document (docx/html resolved to markdown) and store it as
        a tagged guide doc; the last stored becomes active. Reports a per-file
        read error only when NOTHING could be stored."""
        import os
        import re
        count, last, first_err = 0, "", ""
        for path in paths:
            try:
                text = self._read_local_text(path)
            except Exception as exc:  # noqa: BLE001
                first_err = first_err or f"{os.path.basename(path)}: {exc}"
                continue
            if not text.strip():
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "doc"
            setter(conn, text, name=stem, doc_id=f"{slug_prefix}-{slug}")
            count += 1
            last = stem
        if count == 0:
            self._set_status(
                f"Could not read those files — nothing stored."
                f"{' (' + first_err + ')' if first_err else ''}")
        elif count == 1:
            self._set_status(
                f"{label} “{last}” uploaded — generation now follows it.")
        else:
            self._set_status(
                f"{count} documents uploaded — “{last}” is the active {label.lower()}.")

    @classmethod
    def _guide_files_in_folder(cls, folder: str, cap: int = 50) -> list[str]:
        """Supported documents inside a picked folder (recursive, sorted,
        capped; skips hidden/Office-lock files)."""
        from pathlib import Path
        out: list[str] = []
        for p in sorted(Path(folder).rglob("*")):
            if len(out) >= cap:
                break
            if not p.is_file() or p.name.startswith((".", "~$")):
                continue
            if p.suffix.lower() in cls._GUIDE_EXTS:
                out.append(str(p))
        return out

    # bundled default template (assets/ ships with the app; used as the demo
    # seed and as the fallback when no template has been uploaded yet)
    @staticmethod
    def _bundled_card_template_text() -> str:
        from pathlib import Path
        p = (Path(__file__).resolve().parents[4]
             / "assets" / "templates" / "support_center_article_template.md")
        try:
            return p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return ""

    def _load_bundled_card_template(self, conn):
        from src.data import enablement_store as store
        text = self._bundled_card_template_text() or (
            "# [Article Title — clear and action-oriented]\n\n"
            "[2–3 sentences stating the purpose — used as the metadata "
            "description, ~140 characters]\n\n## [H2 action-based title]\n"
            "Step 1: [Heading for first action]\n\n## FAQs\n"
            "### [Question exactly as a user would search it]\n[Answer]\n\n"
            "## Still Need Help?\nContact us by selecting the Help button in "
            "the bottom right of the page.\n"
        )
        store.set_card_template(
            conn, text, name="Unified Support Center/Guru Article Template",
            doc_id="card-template-unified-support-center")

    def _refresh_style_guide_status(self):
        try:
            from src.data import enablement_store as store
            conn = self._conn()
            text = store.get_style_guide(conn)
            if text.strip():
                self.settings.set_style_guide_status(f"Set — {len(text):,} chars")
            else:
                self.settings.set_style_guide_status("Not set")
            try:
                self.settings.set_style_guide_content(text)
            except Exception:
                pass
            try:
                self.settings.set_style_guides(store.list_style_guides(conn))
            except Exception:
                pass
        except Exception:
            pass

    def _refresh_card_template_status(self):
        try:
            from src.data import enablement_store as store
            conn = self._conn()
            text = store.get_card_template(conn)
            if text.strip():
                self.settings.set_card_template_status(f"Set — {len(text):,} chars")
            else:
                self.settings.set_card_template_status("Not set")
            try:
                self.settings.set_card_template_content(text)
            except Exception:
                pass
            try:
                self.settings.set_card_templates(store.list_card_templates(conn))
            except Exception:
                pass
        except Exception:
            pass

    def _on_style_guide_saved(self, text: str):
        """Inline-editor Save → rewrite the ACTIVE guide in place (name kept)."""
        from src.data import enablement_store as store
        if text.strip():
            store.update_style_guide_text(self._conn(), text)
            self._set_status("Style guide edits saved.")
        else:
            store.clear_style_guide()
            self._set_status("Style guide cleared.")
        self._refresh_style_guide_status()

    def _on_card_template_saved(self, text: str):
        from src.data import enablement_store as store
        if text.strip():
            store.update_card_template_text(self._conn(), text)
            self._set_status("Card template edits saved.")
        else:
            store.clear_card_template()
            self._set_status("Card template cleared.")
        self._refresh_card_template_status()

    def _on_style_guide_activate(self, doc_id):
        from src.data import enablement_store as store
        if store.set_active_style_guide(self._conn(), str(doc_id)):
            self._set_status("Active style guide switched.")
        self._refresh_style_guide_status()

    def _on_style_guide_delete(self, doc_id):
        from src.data import enablement_store as store
        store.delete_style_guide(self._conn(), str(doc_id))
        self._set_status("Style guide removed.")
        self._refresh_style_guide_status()

    def _on_card_template_activate(self, doc_id):
        from src.data import enablement_store as store
        if store.set_active_card_template(self._conn(), str(doc_id)):
            self._set_status("Active card template switched.")
        self._refresh_card_template_status()

    def _on_card_template_delete(self, doc_id):
        from src.data import enablement_store as store
        store.delete_card_template(self._conn(), str(doc_id))
        self._set_status("Card template removed.")
        self._refresh_card_template_status()

    def _on_asana_setup(self):
        """Setup mode: Renn discovers Asana GIDs and writes the (scoped) board config."""
        from src.data import asana_setup
        disc = asana_setup.discover()
        # Refuse to persist a config built from mock GIDs into a real source.
        # The mock is fine to SHOW in demo mode, but saving it outside demo
        # would write fabricated project/field ids into monitor_sources.
        if disc.get("mock") and not self.demo:
            self.chat.set_chat([
                ("a", "I can't set up your Asana board yet — no Asana API key "
                      "is connected, so I'd only have sample projects to work "
                      "from, not your real ones. Ask an administrator to "
                      "connect Asana, then run setup again.")])
            self._set_status("Asana setup skipped — not connected")
            self._open_chat()
            return
        projects = disc.get("projects", [])
        proj = projects[0] if projects else None
        fields = disc.get("custom_fields", {}).get(proj["gid"], []) if proj else []
        team = next((f for f in fields if f["name"] == "Assigned Team"), None)
        enab = next((o for o in (team.get("enum_options") or []) if o["name"] == "Enablement"), None) if team else None
        urg = next((f for f in fields if f["name"] == "Urgency"), None)
        ppl = next((f for f in fields if f["name"] == "Assigned People"), None)

        msgs = [("a", "Hi, I'm Renn. Let's connect your Asana board — you won't need to hunt for any GIDs.")]
        if projects:
            msgs.append(("u", "Find my Asana projects."))
            msgs.append(("a", "Found these projects:\n" +
                         "\n".join(f"  •  {p['name']}  (gid {p['gid']})" for p in projects)))
        if proj and team and enab:
            msgs.append(("u", f"Set up “{proj['name']}”."))
            opts = ", ".join(f"{o['name']}={o['gid']}" for o in team["enum_options"])
            detail = (f"In “{proj['name']}” I found:\n"
                      f"  •  {team['name']}  (gid {team['gid']}) — values: {opts}\n")
            if urg:
                detail += f"  •  {urg['name']}  (gid {urg['gid']})\n"
            if ppl:
                detail += f"  •  {ppl['name']}  (gid {ppl['gid']})"
            msgs.append(("a", detail))
            msgs.append(("a", f"I'll create a task when {team['name']} = Enablement "
                              f"(value gid {enab['gid']}), map priority from Urgency and assignee from "
                              f"Assigned People. Saving…"))
        self.chat.set_chat(msgs)

        try:
            conn = self._conn()
            if proj and team and enab:
                res = asana_setup.set_asana_board_config(
                    conn, project_gid=proj["gid"], project_name=proj["name"],
                    indicator_field_gid=team["gid"], indicator_field_name=team["name"],
                    indicator_value_gid=enab["gid"], indicator_value_name=enab["name"],
                    priority_field_gid=(urg["gid"] if urg else None),
                    assignee_field_gid=(ppl["gid"] if ppl else None))
                self._chat_say("a",
                    f"Done — saved “{proj['name']}” to your Enablement settings (source {res['source_id']}). "
                    f"I only touched the Asana source config; nothing else.")
                self._set_status(f"Asana board configured by Renn: {proj['name']}")
        except Exception as exc:  # noqa: BLE001 — surface in chat
            self._chat_say("a", f"Setup hit an error: {exc}")
        self._open_chat()

    def _on_calendar_event(self, label):
        # clicking a task opens its detail panel in the drilldown — NOT the Workbench
        self._show_task_detail(self._find_task(label))

    def _find_task(self, label):
        key = (label or "").rstrip("…")
        for t in self._all_tasks:
            title = t.get("title", "")
            if key and (key in title or title.startswith(key)):
                return t
        return {"title": label or "Task", "source": "drive", "due": "—",
                "priority": "normal", "assignee": "—", "subtasks": [], "scratch": ""}

    def _show_task_detail(self, task: dict):
        if self._drilldown is None:
            return
        # Lazy per-task extras + brief join (WS1-M3/M4): the list/calendar
        # query stays lean; comments/attachments/custom fields and the Haiku
        # brief load only when a panel opens.
        tid_for_extras = task.get("task_id")
        if tid_for_extras and task.get("source") == "asana" and "extras" not in task:
            try:
                import json as _json
                from src.data import asana_extras, enablement_tasks
                from src.data.connection_factory import get_connection
                conn = get_connection(self._engine_db_path())
                try:
                    extras = asana_extras.get_extras(conn, tid_for_extras)
                    fresh = enablement_tasks.get_task(conn, tid_for_extras) or {}
                finally:
                    conn.close()
                task = dict(task)
                if extras:
                    task["extras"] = extras
                if fresh.get("brief_status") == "ok" and fresh.get("brief_json"):
                    try:
                        task["brief"] = _json.loads(fresh["brief_json"])
                    except (ValueError, TypeError):
                        pass
            except Exception:  # noqa: BLE001 — extras/brief are enrichment only
                pass
        panel = TaskDetailPanel(task)
        panel.open_in_workbench.connect(lambda: self.tabs.setCurrentWidget(self._workbench_tab))
        panel.open_source.connect(self._open_source_url)
        tid = task.get("task_id")
        if tid:
            panel.subtask_added.connect(
                lambda text, tid=tid: self._run_task_writeback("create_subtask_in_asana", tid, text))
            panel.comment_posted.connect(
                lambda text, tid=tid: self._run_task_writeback("post_comment_to_asana", tid, text))
            panel.due_changed.connect(
                lambda due, tid=tid: self._run_task_writeback("update_due_in_asana", tid, due or None))
            panel.completed_changed.connect(
                lambda done, tid=tid: self._run_task_writeback("set_completed_in_asana", tid, done))
        self._open_task_title = task.get("title")
        # WS1-M5 fix: reopen-by-id — the title-substring _find_task match is
        # ambiguous once writes flow both ways.
        self._open_task_id = task.get("task_id")
        self._drilldown.show_widget("Task", task.get("source", "").capitalize(), panel)
        self._set_status(f"Opened “{task.get('title', 'task')}”.")

    def _run_task_writeback(self, fn_name: str, task_id: str, *args):
        """Run an Asana write-back off-thread (the API call can block), then refresh."""
        import threading
        self._set_status("Syncing with Asana…")
        db_path = self._engine_db_path()

        def worker():
            res = {"ok": False, "error": "unknown"}
            conn = None
            try:
                from src.data.connection_factory import get_connection
                from src.data import asana_writeback as awb
                conn = get_connection(db_path)
                res = getattr(awb, fn_name)(conn, task_id, *args)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self.task_action_done.emit(res or {})

        threading.Thread(target=worker, daemon=True).start()

    def _on_task_action_done(self, res: dict):
        if res.get("ok"):
            if res.get("synced") is True:
                self._set_status("Done — synced to Asana.")
            elif res.get("synced") is False:
                note = res.get("note")
                self._set_status(f"Saved locally{(' — ' + note) if note else ''}.")
            else:
                self._set_status("Done.")
        elif res.get("conflict"):
            # CAS blocked the write (WS1-M5) — normal concurrency, not an error.
            self._set_status("Task changed in Asana — refreshed. Please retry.")
        else:
            self._set_status(f"Asana action failed: {res.get('error', 'unknown')}")
        # Refresh the task views, then re-open the same task's detail with fresh data.
        try:
            self._load_live()
            tid = getattr(self, "_open_task_id", None)
            if tid:
                fresh = next((t for t in self._all_tasks
                              if t.get("task_id") == tid), None)
                if fresh:
                    self._show_task_detail(fresh)
                    return
            title = getattr(self, "_open_task_title", None)
            if title:
                self._show_task_detail(self._find_task(title))
        except Exception as exc:  # noqa: BLE001
            logger.debug("task refresh after action failed: %s", exc)

    # ── GuruPage-compat setters (so main_window wiring is drop-in) ──
    def set_guru_client(self, client=None, *_a, **_k):
        self._guru_client = client

    def set_friction_pipeline(self, *_a, **_k):
        pass

    def set_content_pipeline(self, *_a, **_k):
        pass

    def set_effectiveness_tracker(self, *_a, **_k):
        pass

    def set_drilldown_panel(self, panel=None, *_a, **_k):
        self._drilldown = panel
        # The expand overlay parents to the same content-area widget the
        # drilldown drawer uses, so it covers almost the whole window.
        try:
            host = panel.parentWidget() if panel is not None else None
            self.workbench.set_overlay_host(host)
            self.pptx.set_overlay_host(host)
            self.analytics.set_overlay_host(host)
        except Exception:
            pass

    def set_monitor(self, monitor):
        """Host wires the EnablementMonitor; its 'changed' signal refreshes the
        views. WS1-M7: refreshes are DEBOUNCED (500ms single-shot) so a burst
        of Asana events doesn't thrash the calendar repaint, and a changed
        Asana task auto-refreshes an OPEN detail panel with a status note —
        no modal, no interruption."""
        self._monitor = monitor
        if monitor is None:
            return
        try:
            from PySide6.QtCore import QTimer
            if not hasattr(self, "_refresh_debounce"):
                self._refresh_debounce = QTimer(self)
                self._refresh_debounce.setSingleShot(True)
                self._refresh_debounce.setInterval(500)
                self._refresh_debounce.timeout.connect(self._load_live)
            monitor.changed.connect(self._refresh_debounce.start)
            asana = getattr(monitor, "asana", None)
            if asana is not None and hasattr(asana, "tasks_updated"):
                asana.tasks_updated.connect(self._on_asana_tasks_updated)
        except Exception:
            pass

    def _on_asana_tasks_updated(self, task_ids):
        """A background reconcile changed tracked tasks (WS1-M7): if one of
        them is open in the drilldown, silently re-open it with fresh data
        after the debounced reload lands."""
        open_id = getattr(self, "_open_task_id", None)
        if not open_id or open_id not in (task_ids or []):
            return
        try:
            from PySide6.QtCore import QTimer

            def _reopen():
                fresh = next((t for t in self._all_tasks
                              if t.get("task_id") == open_id), None)
                if fresh:
                    self._show_task_detail(fresh)
                    self._set_status("Updated from Asana.")
            # After the debounced _load_live (500ms) has refreshed _all_tasks.
            QTimer.singleShot(700, _reopen)
        except Exception:  # noqa: BLE001 — freshness polish, never fatal
            pass

    def _open_chat(self):
        """Show Renn (the enablement chat) in the shared right-side Drill Down panel."""
        try:
            from src.gemini.client_factory import resolve_provider_for_task
            self.chat.set_provider_label(resolve_provider_for_task("enablement_chat").capitalize())
        except Exception:
            pass
        if self._drilldown is not None:
            self._drilldown.show_widget("Renn", "Enablement assistant", self.chat)
