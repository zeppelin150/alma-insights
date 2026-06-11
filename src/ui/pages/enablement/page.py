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
    "You have tools (function calls) to do real work — USE THEM rather than describing "
    "what you would do:\n"
    "- Find content: search_local_documents, query_business_drive, search_drive_docs, get_drive_doc.\n"
    "- Work a card draft: render_card_preview (read the current draft), revise_draft "
    "(apply an edit and re-render), push_guru_draft (publish to Guru). Use the active "
    "draft id from the [ENABLEMENT SCOPE] context unless the user names another draft.\n"
    "- Manage work: list_tasks, create_task, update_task, draft_subtasks, add_subtask, "
    "toggle_subtask, update_scratchpad.\n"
    "- Set up Asana: asana_discover (find projects + field/enum GIDs), then "
    "set_asana_board_config (save the board config). Never ask the user for GIDs — "
    "discover them yourself.\n"
    "- run_monitor_now to pull fresh items from the configured sources.\n\n"
    "Rules:\n"
    "- Only publish to Guru (push_guru_draft) when the operator explicitly asks to push or publish.\n"
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
    import_finished = Signal(dict)  # off-thread import result → UI refresh

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
        self._engine = None
        self._chat_session_id = None
        self._existing_cards_loaded = False
        self.setStyleSheet(f"background:{ALMA_CREAM};")
        self._build()
        self._setup_engine()

    # ── layout ────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 12)
        outer.setSpacing(10)
        outer.addLayout(self._header())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("AnalysisTab")
        self.tabs.setUsesScrollButtons(False)
        self.calendar = CalendarPage()
        self.tasks = TasksPage()
        self.workbench = WorkbenchPage()
        self.settings = SettingsPage()
        # Scroll-wrap the tall pages so content scrolls instead of compressing
        # (compression was overlapping rows on Settings). Workbench fills exactly.
        cal_tab = self._scroll(self.calendar)
        tasks_tab = self._scroll(self.tasks)
        settings_tab = self._scroll(self.settings)
        self.tabs.addTab(cal_tab, "Calendar")
        self.tabs.addTab(tasks_tab, "Tasks")
        self.tabs.addTab(self.workbench, "Workbench")
        self.tabs.addTab(settings_tab, "Settings")
        self._tab_widgets = {
            "calendar": cal_tab,
            "tasks": tasks_tab,
            "workbench": self.workbench,
            "settings": settings_tab,
        }
        self.tabs.setCurrentWidget(self.workbench)
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
        self.workbench.publish_requested.connect(self._on_publish)
        self.workbench.load_file_requested.connect(self._on_load_file)
        self.workbench.open_chat_requested.connect(self._open_chat)
        self.calendar.event_clicked.connect(self._on_calendar_event)
        self.settings.asana_setup_requested.connect(self._on_asana_setup)
        self.workbench.existing_cards_requested.connect(self._fetch_existing_cards)
        self.workbench.import_requested.connect(self._on_import_requested)
        self.workbench.content_edited.connect(self._on_content_edited)
        self.settings.drive_folder_added.connect(self._add_drive_folder)
        self.settings.style_guide_action.connect(self._on_style_guide_action)
        self.import_finished.connect(self._on_import_finished)
        self.connection_status_ready.connect(
            lambda key, ok, detail: self.settings.set_connection_status(key, ok, detail))
        self._refresh_style_guide_status()

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
                "status": t["status"] if t["status"] in ("open", "in_progress", "done") else "open",
                "title": t["title"],
                "source": t["source"] if t["source"] in ("drive", "guru", "asana") else "drive",
                "due": self._fmt_due(t.get("due_date")),
                "priority": t.get("priority") or "normal",
                "assignee": t.get("assignee") or "—",
                "subs": f"{t.get('subtask_done', 0)} / {t.get('subtask_total', 0)}",
                "subtasks": [(s["text"], bool(s["done"])) for s in subs],
                "scratch": t.get("scratchpad") or "",
            })
        return rows

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
            self.chat.add_message(
                "a",
                f"Scan complete — pulled {len(summary['documents'])} docs from the Drive and "
                f"drafted {len(drafts)} Guru cards. Pick one above to review, then say “push to Guru”."
            )
            self.tabs.setCurrentWidget(self.workbench)
            self._open_chat()
        except Exception as exc:  # noqa: BLE001 — surface in the status line
            self._set_status(f"Demo scan error: {exc}")

    def _load_live(self, prefer_draft_id=None):
        """Reload the four pages from the active DB (warehouse, or the demo DB in
        demo mode). Keeps prefer_draft_id in focus if it's still a pending draft."""
        from src.data import enablement_store as store
        from src.data import enablement_tasks as tasks
        conn = self._conn()
        task_list = tasks.list_tasks(conn)
        rows = self._tasks_to_rows(task_list, conn)
        self._all_tasks = rows
        self.tasks.load_tasks(rows)
        self.calendar.set_tasks(task_list)
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
        self._set_status(f"{len(rows)} tasks · {len(drafts)} pending drafts.")

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
            self.workbench.set_active_draft(int(draft_id))
            self.workbench.show_draft(self._card_from_draft(self._conn(), d))

    def _on_chat(self, text: str):
        # ChatPanel already appended the user's bubble; just dispatch to the engine.
        self._dispatch_chat(text)

    def _send_quick(self, prompt: str):
        """A quick-action button sends a canned instruction to Renn."""
        self.chat.add_message("u", prompt)
        self._dispatch_chat(prompt)

    def _dispatch_chat(self, text: str):
        if self._engine is None:
            self.chat.add_message("a", "The assistant isn't available in this build.")
            return
        if self._engine.is_busy:
            self.chat.add_message("a", "One moment — I'm still working on the last request.")
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
            return (
                f"[ENABLEMENT SCOPE] {n_docs} indexed documents, {n_drafts} pending card "
                f"drafts, {n_open} open tasks.{active_line} Tools: search_local_documents / "
                f"query_business_drive to find content; revise_draft + push_guru_draft to work "
                f"a card; create_task / list_tasks / draft_subtasks to manage work; "
                f"asana_discover + set_asana_board_config to set up an Asana board."
            )
        except Exception:
            return ""

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
            from src.services.chat_session import create_session
            self._chat_session_id = create_session("enablement", conn=self._conn())
            self._engine.set_session_id(self._chat_session_id)
            # tell the MCP tool server which session to tag its tool calls with
            db_path = self._engine_db_path()
            if db_path:
                Path(db_path).parent.joinpath(".current_chat_session").write_text(
                    self._chat_session_id, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — telemetry only; tools still work
            logger.debug("enablement session create failed: %s", exc)

    def _prepare_provider(self):
        """Boot the Gemini ACP warm bridge, or drop it for the Claude path."""
        try:
            from src.gemini.client_factory import resolve_provider_for_task
            prov = resolve_provider_for_task("enablement_chat")
        except Exception:
            prov = "gemini"
        if prov == "gemini":
            self._ensure_warm_bridge()
        else:
            if self._warm_bridge is not None:
                self._on_bridge_recycle()
            try:
                self._engine.set_client(None)   # Claude builds a client per message
            except Exception:
                pass

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

    def _on_bridge_recycle(self):
        if self._warm_bridge is not None:
            try:
                self._warm_bridge.shutdown()
            except Exception:
                pass
            self._warm_bridge = None

    def _on_engine_response(self, text: str):
        self.chat.add_message("a", text)
        # A tool may have revised / pushed / created — refresh the views, keeping the
        # active draft in focus if it's still pending.
        try:
            self._load_live(prefer_draft_id=self.workbench.active_draft_id)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Refresh error: {exc}")

    def _on_engine_error(self, message: str):
        self.chat.add_message("a", f"Sorry — I hit an error: {message}")

    # ── live connections + existing Guru cards (Phase 7) ──────────
    def _fetch_existing_cards(self):
        """Populate the Workbench 'Existing Guru card' submenu with real cards."""
        if self.demo or self._guru_client is None or self._existing_cards_loaded:
            return
        try:
            cards = self._guru_client.search_cards("") or []
            rows = [{"id": c.get("id"),
                     "title": c.get("preferredPhrase") or c.get("title") or c.get("id")}
                    for c in cards[:25] if c.get("id")]
            if rows:
                self.workbench.set_existing_cards(rows)
            self._existing_cards_loaded = True
        except Exception as exc:  # noqa: BLE001
            self.chat.add_message("a", f"Couldn't load Guru cards: {exc}")

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
            self.chat.add_message("a", "No draft selected to push.")
            return
        from src.data import enablement_store as store
        try:
            res = store.publish_draft(
                self._conn(), did,
                guru_client=self._guru_for_push(),
                collection_id=self._publish_collection_id(),
            )
        except Exception as exc:  # noqa: BLE001 — surface in chat
            self.chat.add_message("a", f"Publish failed: {exc}")
            return
        if res.get("ok"):
            where = "Provider Enablement" if self.demo else "Guru"
            self.chat.add_message("a", f"Published the draft to {where} and re-rendered the live card.")
            self._set_status(f"Draft {did} published{' (demo)' if self.demo else ''}.")
            if not self.demo:
                self._load_live()
        else:
            self.chat.add_message("a", f"Publish failed: {res.get('error')}")

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
                self.chat.add_message("a", f"Couldn't target card {key}: {exc}")
                return
            self._set_status(f"Updating Guru card {key} from the active draft…")
            self._on_push(did)
            return
        msg = {
            "drive_new": "Created a new Google Doc in the Drive from the active draft (demo).",
            "drive_update": "AI is updating the selected Google Doc via the Drive API (demo).",
        }.get(dest, "Done.")
        self._set_status(msg)
        self.chat.add_message("a", msg)

    def _on_load_file(self, path: str):
        import os
        name = os.path.basename(path) if path else "a file"
        self._set_status(f"Uploaded “{name}”.")
        self.chat.set_chat([("a",
            f"You uploaded “{name}”. Here's what you can do next:\n"
            f"  •  Draft a Guru card from it\n"
            f"  •  Make edits, then push to a new or existing Guru card\n"
            f"  •  Save it to Google Drive\n"
            f"Tell me what you'd like, or use the buttons below.")])
        self._open_chat()

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
            self.chat.add_message("a", f"Import failed: {res.get('error')}")
            return
        kind = res.get("kind")
        if kind == "style":
            self._set_status("Style guide imported from Drive — generation now follows it.")
            self._refresh_style_guide_status()
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
        """Persist Edit-view changes to the draft (pushed drafts stay frozen)."""
        from src.data import enablement_store as store
        try:
            did = int(draft_id)
            draft = store.get_draft(self._conn(), did)
            if draft and draft.get("status") != "pushed":
                store.update_draft_content(self._conn(), did, content=md)
                self._set_status(f"Draft {did} updated from the editor.")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Edit save failed: {exc}")

    # ── style guide ─────────────────────────────────────────────────

    def _on_style_guide_action(self, action: str):
        from src.data import enablement_store as store
        conn = self._conn()
        if action == "paste":
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getMultiLineText(
                self, "Card style guide",
                "Paste the style guide card generation should follow:",
                store.get_style_guide(conn),
            )
            if ok:
                if text.strip():
                    store.set_style_guide(conn, text)
                    self._set_status("Style guide saved — card generation and revisions now follow it.")
                else:
                    store.clear_style_guide()
                    self._set_status("Style guide cleared.")
        elif action == "drive":
            if self.demo:
                store.set_style_guide(
                    conn,
                    "Tone: confident, plain language. Cards open with a one-line "
                    "summary, use numbered steps for any process, and end with a "
                    "short FAQ.",
                    name="Enablement style guide (demo)",
                )
                self._set_status("Demo style guide loaded.")
            else:
                from PySide6.QtWidgets import QInputDialog
                ref, ok = QInputDialog.getText(
                    self, "Style guide from Drive", "Google Doc URL or file id:"
                )
                if ok and ref.strip():
                    self._run_import("style", ref.strip())
                    return
        elif action == "clear":
            store.clear_style_guide()
            self._set_status("Style guide cleared.")
        self._refresh_style_guide_status()

    def _refresh_style_guide_status(self):
        try:
            from src.data import enablement_store as store
            text = store.get_style_guide(self._conn())
            if text.strip():
                self.settings.set_style_guide_status(f"Set — {len(text):,} chars")
            else:
                self.settings.set_style_guide_status("Not set")
        except Exception:
            pass

    def _on_asana_setup(self):
        """Setup mode: Renn discovers Asana GIDs and writes the (scoped) board config."""
        from src.data import asana_setup
        disc = asana_setup.discover()
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
                self.chat.add_message("a",
                    f"Done — saved “{proj['name']}” to your Enablement settings (source {res['source_id']}). "
                    f"I only touched the Asana source config; nothing else.")
                self._set_status(f"Asana board configured by Renn: {proj['name']}")
        except Exception as exc:  # noqa: BLE001 — surface in chat
            self.chat.add_message("a", f"Setup hit an error: {exc}")
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
        panel = TaskDetailPanel(task)
        panel.open_in_workbench.connect(lambda: self.tabs.setCurrentWidget(self.workbench))
        self._drilldown.show_widget("Task", task.get("source", "").capitalize(), panel)
        self._set_status(f"Opened “{task.get('title', 'task')}”.")

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

    def set_monitor(self, monitor):
        """Host wires the EnablementMonitor; its 'changed' signal refreshes the views."""
        self._monitor = monitor
        if monitor is not None:
            try:
                monitor.changed.connect(lambda: self._load_live())
            except Exception:
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
