"""
Alma Insights — Report Chat Widget (Build 7.0)
DRILLDOWN follow-up chat for AI reports.
Maintains conversation history and packs full context per Gemini call
(CLI is stateless — each call includes the full conversation).

Build 7.0: Data-grounded drilldown enrichment — when a follow-up question
references a specific TRC or date window, the chat worker queries the live
DB for a targeted mini data block, NLP aggregates, and sample tickets,
injecting fresh evidence alongside the original report text.
"""

import json
import logging
import re
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_INFO,
)

logger = logging.getLogger("alma.chat_widget")


# ═══════════════════════════════════════════════════════════
#  CHAT WORKER (runs in background thread)
# ═══════════════════════════════════════════════════════════

class ChatWorker(QThread):
    """Background thread for Gemini chat follow-up.

    Build 7.0: Optionally enriches the prompt with live DB data
    (mini data block, NLP aggregates, sample tickets) before calling Gemini.
    DB access happens in this thread — thread-safe separate connection.
    """
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, gemini_client, prompt, system_prompt="",
                 drilldown_ctx=None):
        super().__init__()
        self._client = gemini_client
        self._prompt = prompt
        self._system_prompt = system_prompt
        self._drilldown_ctx = drilldown_ctx  # None or dict

    def run(self):
        try:
            # Build 7.0: Inject drilldown enrichment if context available
            if self._drilldown_ctx:
                enrichment = self._build_enrichment()
                if enrichment:
                    self._prompt = enrichment + "\n\n" + self._prompt

            response = self._client.generate(self._prompt, self._system_prompt)
            self.finished.emit(response)
        except Exception as e:
            self.error.emit(str(e))

    def _build_enrichment(self):
        """Build drilldown data from live DB in worker thread.

        Returns formatted text block to prepend to prompt, or None.
        """
        ctx = self._drilldown_ctx
        if not ctx or not ctx.get("db_path"):
            return None

        parts = []
        db = None
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.report_builder import (
                build_data_block, format_data_block_for_prompt,
            )

            db = DatabaseManager(ctx["db_path"])
            db.initialize()

            d_start = ctx.get("date_start", "")
            d_end = ctx.get("date_end", "")
            trc = ctx.get("trc")

            # 1. Targeted mini data block
            mini = build_data_block(db, d_start, d_end, trc_filter=trc)
            formatted = format_data_block_for_prompt(mini)

            header = "DRILLDOWN DATA (queried from database for this question)"
            if trc:
                header += f" — filtered to TRC: {trc}"
            if d_start != ctx.get("full_date_start") or d_end != ctx.get("full_date_end"):
                header += f" — date window: {d_start} to {d_end}"
            parts.append(f"{header}:")
            parts.append(formatted)

            # 2. NLP aggregate context (if scan data exists)
            if trc and ctx.get("scan_id"):
                nlp_text = self._build_nlp_context(db, trc, ctx["scan_id"])
                if nlp_text:
                    parts.append(nlp_text)

            # 3. Sample tickets (if question asks for examples)
            if ctx.get("wants_samples"):
                sample_text = self._pull_samples(db, trc, d_start, d_end)
                if sample_text:
                    parts.append(sample_text)

        except Exception as e:
            logger.warning("Drilldown enrichment failed: %s", e)
            parts.append(f"(Drilldown enrichment partially failed: {e})")
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

        return "\n\n".join(parts) if parts else None

    def _build_nlp_context(self, db, trc, scan_id):
        """Format NLP classification aggregates for a TRC."""
        try:
            agg = db.get_nlp_aggregate_for_trc(trc, scan_id=scan_id)
            if not agg or agg.get("total_classified", 0) == 0:
                return None

            lines = [f"NLP CLASSIFICATION CONTEXT (TRC: {trc}, "
                     f"{agg['total_classified']} classified tickets):"]

            # Friction distribution
            friction = agg.get("friction_distribution", {})
            if friction:
                fd_parts = [f"{k}: {v}" for k, v in
                            sorted(friction.items(), key=lambda x: -x[1])]
                lines.append(f"  Friction types: {', '.join(fd_parts)}")

            # Sentiment distribution
            sentiment = agg.get("sentiment_distribution", {})
            if sentiment:
                sd_parts = [f"{k}: {v}" for k, v in
                            sorted(sentiment.items(), key=lambda x: -x[1])]
                lines.append(f"  Sentiment: {', '.join(sd_parts)}")

            # Avg intensity
            if agg.get("avg_sentiment_intensity") is not None:
                lines.append(
                    f"  Avg sentiment intensity: "
                    f"{agg['avg_sentiment_intensity']}"
                )

            # Anomaly counts
            anomalies = agg.get("anomaly_counts", {})
            if anomalies:
                af_parts = [f"{k}: {v}" for k, v in
                            sorted(anomalies.items(), key=lambda x: -x[1])]
                lines.append(f"  Anomaly flags: {', '.join(af_parts)}")

            # Top sub-clusters
            subs = agg.get("top_sub_clusters", [])
            if subs:
                sub_parts = [f"{label} ({cnt})" for label, cnt in subs[:5]]
                lines.append(f"  Top sub-patterns: {', '.join(sub_parts)}")

            # Top root cause hints
            hints = agg.get("top_root_cause_hints", [])
            if hints:
                hint_parts = [f"{h} ({cnt})" for h, cnt in hints[:5]]
                lines.append(f"  Root cause hints: {', '.join(hint_parts)}")

            return "\n".join(lines)

        except Exception as e:
            logger.debug("NLP context build failed for %s: %s", trc, e)
            return None

    def _pull_samples(self, db, trc, d_start, d_end, limit=5):
        """Pull redacted sample tickets for drilldown evidence."""
        try:
            from src.gemini.gemini_client import GeminiClient

            conditions = ["created_at >= ?", "created_at <= ?"]
            params = [d_start, d_end]
            if trc:
                conditions.append("trc_code = ?")
                params.append(trc)

            where = " AND ".join(conditions)
            from src.data.source_registry import SourceRegistry
            from src.data.warehouse_query import WarehouseQuery
            _wq = WarehouseQuery(db.conn, SourceRegistry(db.conn))
            rows = _wq.query_conversations_raw(f"""
                SELECT ticket_id, trc_code, created_at, subject,
                       csat_score, thread_preview
                FROM {{table}}
                WHERE {where} AND thread_preview != ''
                ORDER BY RANDOM() LIMIT ?
            """, params + [limit])

            if not rows:
                return None

            _sample_cols = ["ticket_id", "trc_code", "created_at", "subject", "csat_score", "thread_preview"]
            lines = [f"SAMPLE TICKETS ({len(rows)} redacted examples):"]
            for r in rows:
                row = dict(zip(_sample_cols, r)) if not isinstance(r, dict) else r
                # PII redaction
                subject = GeminiClient._redact_base(None, row.get("subject", ""))
                subject = GeminiClient._redact_aggressive(None, subject)
                preview = GeminiClient._redact_base(None, row.get("thread_preview", ""))
                preview = GeminiClient._redact_aggressive(None, preview)

                entry = json.dumps({
                    "ticket_id": row["ticket_id"],
                    "trc": row.get("trc_code", ""),
                    "created_at": row.get("created_at", ""),
                    "subject": subject,
                    "csat": row.get("csat_score"),
                    "preview": preview[:800],
                })
                lines.append(f"  {entry}")

            return "\n".join(lines)

        except Exception as e:
            logger.debug("Sample pull failed: %s", e)
            return None


# ═══════════════════════════════════════════════════════════
#  CHAT BUBBLE
# ═══════════════════════════════════════════════════════════

class ChatBubble(QFrame):
    """Styled chat message bubble."""

    def __init__(self, text, role="user", parent=None):
        super().__init__(parent)
        self._role = role
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)

        if role == "user":
            self.setStyleSheet(f"""
                ChatBubble {{
                    background: {ALMA_INFO}; border-radius: 10px;
                    margin-left: 80px; margin-right: 4px;
                }}
                QLabel {{
                    color: white; font-size: 12px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                ChatBubble {{
                    background: {ALMA_CREAM}; border-radius: 10px;
                    border: none;
                    margin-left: 4px; margin-right: 80px;
                }}
                QLabel {{
                    color: {ALMA_TEXT_DARK}; font-size: 12px;
                }}
            """)


# ═══════════════════════════════════════════════════════════
#  REPORT CHAT WIDGET
# ═══════════════════════════════════════════════════════════

class ReportChatWidget(QWidget):
    """DRILLDOWN follow-up chat embedded below AI report output.

    Build 7.0: Data-grounded drilldown — when the user asks about a specific
    TRC or time window, the chat worker queries the live database and injects
    a targeted mini data block, NLP aggregates, and sample tickets alongside
    the original report text.  This lets Gemini answer with actual data
    instead of just paraphrasing the report prose.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history = []  # [{"role": "...", "content": "..."}, ...]
        self._data_block_text = ""
        self._report_text = ""
        self._gemini_client = None
        self._worker = None  # legacy: kept for FindingWorker path

        # Build 7.0: DB context for data-grounded drilldown
        self._db_path = None
        self._date_start = None
        self._date_end = None
        self._scan_id = None
        self._known_trcs = []   # cached list of {code, label} dicts
        self._trc_codes = set()  # lowercase codes for fast lookup
        self._trc_labels = {}    # lowercase label → code mapping

        # ChatEngine — warm client path, custom packer + drilldown context
        from src.services.chat_engine import ChatEngine
        self._engine = ChatEngine(
            system_prompt=(
                "You are a Support Analytics engine in follow-up mode. "
                "When DRILLDOWN DATA is provided, use it to give precise, "
                "evidence-based answers with specific metrics and ticket examples. "
                "Cite TRC codes, ticket counts, CSAT scores, and dates."
            ),
            task_type="report_generation",
            context_provider=self._provide_drilldown_context,
            history_packer=self._pack_followup_history,
        )
        self._engine.response_ready.connect(self._on_engine_response)
        self._engine.error_occurred.connect(self._on_error)
        self._engine.busy_changed.connect(self._on_engine_busy)
        self._engine.status_update.connect(self._on_engine_status)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title = QLabel("Follow-Up Chat")
        title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_MID};")
        header.addWidget(title)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        header.addStretch()
        header.addWidget(self._status_label)
        layout.addLayout(header)

        # Chat area (scrollable)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setMinimumHeight(150)
        self._scroll.setMaximumHeight(400)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {ALMA_WHITE}; border: none;
                border-radius: 8px;
            }}
        """)

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(8, 8, 8, 8)
        self._chat_layout.setSpacing(6)
        self._chat_layout.addStretch()
        self._scroll.setWidget(self._chat_container)
        layout.addWidget(self._scroll)

        # Input row
        input_row = QHBoxLayout()

        self._input_edit = QTextEdit()
        self._input_edit.setPlaceholderText("Ask a follow-up question about the report...")
        self._input_edit.setMaximumHeight(50)
        self._input_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 6px; font-size: 12px;
            }}
            QTextEdit:focus {{ border-color: {ALMA_GREEN_MID}; }}
        """)
        input_row.addWidget(self._input_edit, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setEnabled(False)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 8px 16px;
                font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._send_btn.clicked.connect(self._send_message)
        input_row.addWidget(self._send_btn)

        layout.addLayout(input_row)

        # Placeholder
        self._placeholder = QLabel("Generate a report to enable follow-up chat")
        self._placeholder.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 11px;")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._chat_layout.insertWidget(0, self._placeholder)

    # ─── Public API ───────────────────────────────────────

    def set_gemini_client(self, client):
        """Set the Gemini client for chat calls (warm path)."""
        self._gemini_client = client
        self._engine.set_client(client)

    def set_report_context(self, data_block_text, report_text):
        """Initialize context after report generation."""
        self._data_block_text = data_block_text
        self._report_text = report_text
        self._history = [
            {"role": "system", "content": data_block_text},
            {"role": "assistant", "content": report_text},
        ]
        self._engine.set_history(self._history)
        self._send_btn.setEnabled(True)
        self._placeholder.hide()
        self._status_label.setText("Ready for questions")

    def set_db_context(self, db_path, date_start, date_end, scan_id=None):
        """Provide live DB access for data-grounded drilldown (Build 7.0).

        Called by the AI Reports page after report generation so the chat
        widget can query the database for targeted enrichment.

        Args:
            db_path: Path to SQLite database file.
            date_start: Start date string (YYYY-MM-DD).
            date_end: End date string (YYYY-MM-DD).
            scan_id: Optional NLP scan ID for NLP aggregate context.
        """
        self._db_path = str(db_path) if db_path else None
        self._date_start = date_start
        self._date_end = date_end
        self._scan_id = scan_id

        # Cache TRC list for fast reference detection
        # (opens a quick read-only connection in the main thread)
        self._known_trcs = []
        self._trc_codes = set()
        self._trc_labels = {}
        if self._db_path:
            try:
                from src.data.db_manager import DatabaseManager
                db = DatabaseManager(self._db_path)
                db.initialize()
                trcs = db.get_trc_codes()
                db.close()

                self._known_trcs = trcs
                for t in trcs:
                    code = t.get("code", "")
                    label = t.get("label", "")
                    if code:
                        self._trc_codes.add(code.lower())
                    if label and code:
                        self._trc_labels[label.lower()] = code
                        # Also index meaningful sub-phrases of labels
                        # e.g. "Billing Resolution" → "billing resolution"
                        # Single-word fragments are too noisy, skip them
                        words = label.lower().split()
                        if len(words) >= 2:
                            for i in range(len(words)):
                                for j in range(i + 2, len(words) + 1):
                                    phrase = " ".join(words[i:j])
                                    if len(phrase) >= 6:  # Avoid short fragments
                                        self._trc_labels[phrase] = code

                logger.debug("Chat widget cached %d TRC codes for drilldown",
                             len(self._trc_codes))
            except Exception as e:
                logger.warning("Failed to cache TRC list: %s", e)

    def load_chat_history(self, history):
        """Restore chat history from a saved report."""
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except (json.JSONDecodeError, TypeError):
                return
        if not history:
            return

        self._history = history
        self._placeholder.hide()

        # Rebuild UI bubbles for user/assistant messages
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                self._add_bubble(msg["content"], msg["role"])

        # Re-extract context
        for msg in history:
            if msg["role"] == "system":
                self._data_block_text = msg["content"]
            elif msg["role"] == "assistant" and not self._report_text:
                self._report_text = msg["content"]

        self._send_btn.setEnabled(bool(self._gemini_client))

    def get_chat_history(self):
        """Return chat history for persistence."""
        return json.dumps(self._history)

    def clear(self):
        """Reset chat state."""
        self._history = []
        self._engine.clear_history()
        self._data_block_text = ""
        self._report_text = ""
        self._nlp_finding_id = None
        self._db_path = None
        self._date_start = None
        self._date_end = None
        self._scan_id = None
        self._known_trcs = []
        self._trc_codes = set()
        self._trc_labels = {}
        self._send_btn.setEnabled(False)
        self._status_label.setText("")
        self._placeholder.show()

        # Remove all bubbles
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget() and item.widget() != self._placeholder:
                item.widget().deleteLater()

    # ─── Message Dispatch ─────────────────────────────────

    def _send_message(self):
        question = self._input_edit.toPlainText().strip()
        if not question or not self._gemini_client:
            return

        # Add user bubble
        self._add_bubble(question, "user")
        self._input_edit.clear()

        # Check for NLP finding-aware drilldown (bypasses engine)
        finding_id = getattr(self, '_nlp_finding_id', None)
        if finding_id:
            self._history.append({"role": "user", "content": question})
            self._send_finding_drilldown(finding_id, question)
            return

        # Sync engine history with widget history before sending
        self._engine.set_history(self._history)

        # Send via ChatEngine (engine appends user msg to its own history)
        self._engine.send(question)

    def _send_finding_drilldown(self, finding_id, question):
        """Use NLP synthesizer for finding-aware drilldown response."""
        self._send_btn.setEnabled(False)
        self._status_label.setText("Analyzing finding...")

        class FindingWorker(QThread):
            finished = Signal(str)
            error = Signal(str)

            def __init__(self, db_path, finding_id, question, gemini_client):
                super().__init__()
                self._db_path = db_path
                self._finding_id = finding_id
                self._question = question
                self._gemini = gemini_client

            def run(self):
                try:
                    from src.data.db_manager import DatabaseManager
                    from src.data.nlp_synthesis import NLPSynthesizer
                    db = DatabaseManager(self._db_path)
                    db.initialize()
                    synth = NLPSynthesizer(db, self._gemini)
                    result = synth.synthesize_single_finding(
                        self._finding_id, user_question=self._question
                    )
                    db.close()
                    self.finished.emit(result)
                except Exception as e:
                    self.error.emit(str(e))

        # Get db_path — prefer stored context, fall back to parent walk
        db_path = self._db_path
        if not db_path:
            try:
                parent = self.parent()
                while parent:
                    if hasattr(parent, 'db'):
                        db_path = parent.db.db_path
                        break
                    parent = parent.parent()
            except Exception:
                pass

        if not db_path:
            self._on_error("Cannot find database path for finding drilldown")
            return

        self._worker = FindingWorker(db_path, finding_id, question, self._gemini_client)
        self._worker.finished.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    # ─── Prompt Assembly ──────────────────────────────────

    def _build_followup_prompt(self, question):
        """Pack the full conversation context into a single prompt."""
        parts = []

        parts.append("ORIGINAL REPORT:")
        parts.append(self._report_text[:8000])  # Truncate if very long

        # Last 5 Q&A turns
        qa_turns = [m for m in self._history if m["role"] in ("user", "assistant")]
        # Skip the initial report (first assistant message)
        qa_turns = qa_turns[1:]  # Remove initial assistant response
        recent = qa_turns[-10:]  # Last 5 pairs = 10 messages

        if recent:
            parts.append("\nPRIOR Q&A:")
            for m in recent:
                prefix = "Q" if m["role"] == "user" else "A"
                parts.append(f"{prefix}: {m['content'][:2000]}")

        parts.append(f"\nNEW QUESTION: {question}")
        parts.append(
            "\nIf the question asks for a drilldown on a specific finding, produce:\n"
            "WHAT IT IS / EVIDENCE / WHO-WHERE / WHY / WHAT TO DO\n"
            "If it's a simple question, answer directly.\n"
            "Always cite specific metrics from the original report.\n"
            "If DRILLDOWN DATA was provided above, integrate those fresh "
            "statistics and ticket samples into your answer."
        )

        return "\n\n".join(parts)

    # ─── Build 7.0: Drilldown Intent Detection ───────────

    def _build_drilldown_intent(self, question):
        """Detect if a question warrants live DB enrichment.

        Returns a drilldown context dict for ChatWorker, or None if the
        question is generic and doesn't reference specific TRCs/dates.
        """
        if not self._db_path or not self._date_start:
            return None

        trc_match = self._detect_trc_reference(question)
        date_match = self._detect_date_reference(question)
        wants_samples = self._detect_sample_request(question)

        # Only enrich if there's a specific TRC, date window, or sample request
        if not trc_match and not date_match and not wants_samples:
            return None

        # Determine effective date window
        d_start = date_match[0] if date_match else self._date_start
        d_end = date_match[1] if date_match else self._date_end

        return {
            "db_path": self._db_path,
            "date_start": d_start,
            "date_end": d_end,
            "full_date_start": self._date_start,
            "full_date_end": self._date_end,
            "trc": trc_match,
            "scan_id": self._scan_id,
            "wants_samples": wants_samples,
        }

    def _detect_trc_reference(self, question):
        """Detect if the question references a specific TRC code or label.

        Returns the TRC code (str) or None.
        """
        if not self._trc_codes:
            return None

        q_lower = question.lower()

        # 1. Exact code match (e.g. "TRC-123", "billing_resolution")
        for code in self._trc_codes:
            # Match the code with word boundaries to avoid partial matches
            # Allow common delimiters: space, comma, period, colon, quotes
            pattern = r'(?:^|[\s,.:;"\'])' + re.escape(code) + r'(?:$|[\s,.:;"\'])'
            if re.search(pattern, q_lower):
                return code  # Return original-case from known_trcs
                # Actually return the proper-case code
        # Need proper-case — rebuild
        code_map = {}
        for t in self._known_trcs:
            c = t.get("code", "")
            if c:
                code_map[c.lower()] = c

        for code_lower in self._trc_codes:
            pattern = r'(?:^|[\s,.:;"\'])' + re.escape(code_lower) + r'(?:$|[\s,.:;"\'])'
            if re.search(pattern, q_lower):
                return code_map.get(code_lower, code_lower)

        # 2. Label match — try longest labels first to avoid partial matches
        sorted_labels = sorted(self._trc_labels.keys(), key=len, reverse=True)
        for label_lower in sorted_labels:
            if label_lower in q_lower:
                return self._trc_labels[label_lower]

        # 3. "TRC" keyword followed by something that looks like a code
        trc_mention = re.search(r'\btrc[\s:=]+([a-zA-Z0-9_\-]+)', q_lower)
        if trc_mention:
            candidate = trc_mention.group(1)
            # Check if it's a known code
            proper = code_map.get(candidate.lower())
            if proper:
                return proper

        return None

    def _detect_date_reference(self, question):
        """Detect date/time window references in the question.

        Returns (date_start, date_end) tuple or None.
        Supports: "week N", month names, "first/second half", "last N days".
        """
        if not self._date_start or not self._date_end:
            return None

        q_lower = question.lower()

        try:
            range_start = datetime.strptime(self._date_start, "%Y-%m-%d")
            range_end = datetime.strptime(self._date_end, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

        total_days = (range_end - range_start).days

        # "week N" (e.g. "week 1", "week 3")
        week_match = re.search(r'\bweek\s*(\d+)\b', q_lower)
        if week_match:
            week_num = int(week_match.group(1))
            w_start = range_start + timedelta(days=(week_num - 1) * 7)
            w_end = min(w_start + timedelta(days=6), range_end)
            if w_start <= range_end:
                return (w_start.strftime("%Y-%m-%d"),
                        w_end.strftime("%Y-%m-%d"))

        # "first half" / "second half"
        if "first half" in q_lower:
            mid = range_start + timedelta(days=total_days // 2)
            return (self._date_start, mid.strftime("%Y-%m-%d"))
        if "second half" in q_lower:
            mid = range_start + timedelta(days=total_days // 2 + 1)
            return (mid.strftime("%Y-%m-%d"), self._date_end)

        # "last N days" / "past N days"
        last_n = re.search(r'(?:last|past)\s+(\d+)\s+days?', q_lower)
        if last_n:
            n = int(last_n.group(1))
            d_start = max(range_end - timedelta(days=n), range_start)
            return (d_start.strftime("%Y-%m-%d"), self._date_end)

        # Month names (January, Feb, etc.)
        months = {
            "january": 1, "jan": 1, "february": 2, "feb": 2,
            "march": 3, "mar": 3, "april": 4, "apr": 4,
            "may": 5, "june": 6, "jun": 6,
            "july": 7, "jul": 7, "august": 8, "aug": 8,
            "september": 9, "sep": 9, "sept": 9,
            "october": 10, "oct": 10, "november": 11, "nov": 11,
            "december": 12, "dec": 12,
        }
        for name, month_num in months.items():
            if re.search(r'\b' + name + r'\b', q_lower):
                # Use the year from the data range
                year = range_start.year
                # If the data range spans year boundary, pick the right year
                if month_num < range_start.month and range_end.year > range_start.year:
                    year = range_end.year

                m_start = datetime(year, month_num, 1)
                # End of month
                if month_num == 12:
                    m_end = datetime(year + 1, 1, 1) - timedelta(days=1)
                else:
                    m_end = datetime(year, month_num + 1, 1) - timedelta(days=1)

                # Clamp to data range
                m_start = max(m_start, range_start)
                m_end = min(m_end, range_end)

                if m_start <= m_end:
                    return (m_start.strftime("%Y-%m-%d"),
                            m_end.strftime("%Y-%m-%d"))
                break  # Only match first month

        return None

    @staticmethod
    def _detect_sample_request(question):
        """Check if the question asks for ticket examples/samples."""
        keywords = [
            "example", "sample", "ticket", "show me", "show ticket",
            "specific case", "actual ticket", "real ticket",
            "can you show", "evidence", "proof",
        ]
        q_lower = question.lower()
        return any(kw in q_lower for kw in keywords)

    # ─── Response Handling ────────────────────────────────

    def _on_response(self, text):
        """Legacy handler for FindingWorker path."""
        self._add_bubble(text, "assistant")
        self._history.append({"role": "assistant", "content": text})
        self._send_btn.setEnabled(True)
        self._status_label.setText("Ready")

    def _on_engine_response(self, text):
        """Handler for ChatEngine path."""
        self._add_bubble(text, "assistant")
        # Sync widget history from engine (engine already appended)
        self._history = self._engine.history
        self._status_label.setText("Ready")

    def _on_error(self, error_text):
        self._add_bubble(f"Error: {error_text}", "assistant")
        self._send_btn.setEnabled(True)
        self._status_label.setText("Error occurred")

    def _on_engine_busy(self, busy):
        self._send_btn.setEnabled(not busy)

    def _on_engine_status(self, text):
        if text:
            self._status_label.setText(text)

    # ── ChatEngine callbacks ──

    def _provide_drilldown_context(self, user_message, history):
        """Context provider: build drilldown enrichment from live DB."""
        drilldown_ctx = self._build_drilldown_intent(user_message)
        if not drilldown_ctx:
            return ""

        self._status_label.setText("Querying data + thinking...")

        # Execute enrichment synchronously (fast DB queries <100ms)
        try:
            from src.data.db_manager import DatabaseManager
            from src.data.report_builder import build_data_block, format_data_block_for_prompt

            db = DatabaseManager(drilldown_ctx["db_path"])
            db.initialize()

            d_start = drilldown_ctx.get("date_start", "")
            d_end = drilldown_ctx.get("date_end", "")
            trc = drilldown_ctx.get("trc")

            mini = build_data_block(db, d_start, d_end, trc_filter=trc)
            formatted = format_data_block_for_prompt(mini)

            header = "DRILLDOWN DATA (queried from database for this question)"
            if trc:
                header += f" — filtered to TRC: {trc}"
            parts = [f"{header}:", formatted]

            db.close()
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning("Drilldown enrichment failed: %s", e)
            return ""

    def _pack_followup_history(self, user_message, history):
        """Custom history packer: last 5 Q&A turns + original report text."""
        parts = []
        parts.append("ORIGINAL REPORT:")
        parts.append(self._report_text[:8000])

        qa_turns = [m for m in history if m["role"] in ("user", "assistant")]
        qa_turns = qa_turns[1:]  # skip initial assistant (report)
        recent = qa_turns[-10:]  # last 5 pairs

        if recent:
            parts.append("\nPRIOR Q&A:")
            for m in recent:
                prefix = "Q" if m["role"] == "user" else "A"
                parts.append(f"{prefix}: {m['content'][:2000]}")

        parts.append(f"\nNEW QUESTION: {user_message}")
        parts.append(
            "\nIf the question asks for a drilldown on a specific finding, produce:\n"
            "WHAT IT IS / EVIDENCE / WHO-WHERE / WHY / WHAT TO DO\n"
            "If it's a simple question, answer directly.\n"
            "Always cite specific metrics from the original report.\n"
            "If DRILLDOWN DATA was provided above, integrate those fresh "
            "statistics and ticket samples into your answer."
        )
        return "\n\n".join(parts)

    def _add_bubble(self, text, role):
        """Add a chat bubble to the scroll area."""
        bubble = ChatBubble(text, role)
        # Insert before the stretch
        idx = self._chat_layout.count() - 1
        self._chat_layout.insertWidget(idx, bubble)

        # Scroll to bottom
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))
