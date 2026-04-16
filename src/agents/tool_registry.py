"""
Alma Insights -- Tool Registry (Pass 5.0)

Defines the 7 tools available to worker agents during classification.
Each tool is a Python function backed by SQLite queries, ported from
the proven logic in scan_worker.py's helper methods.

Tools:
    1. query_taxonomy    — Active sub-patterns + n-gram fingerprints for a TRC
    2. get_stats_context — Poisson flags, CUSUM state, theta baselines, rising terms
    3. store_classification — Persist one ticket classification immediately
    4. flag_for_review   — Flag a ticket for human review
    5. get_full_thread   — Retrieve full conversation thread (untruncated)
    6. report_progress   — Write progress for UI polling
    7. check_cross_trc   — Check if a pattern exists in a different TRC

Per-ticket persistence: Unlike the old batch-level store, each ticket is
stored individually via store_classification. If a worker crashes mid-batch,
already-classified tickets survive in SQLite.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from src.data.connection_factory import get_connection

logger = logging.getLogger("alma.tools")


# ── Validation constants (ported from scan_worker._parse_response) ──
VALID_FRICTION = frozenset({
    "access_blocked", "self_serve_failure", "automation_loop",
    "incorrect_charge", "missing_information", "policy_confusion",
    "feature_broken", "feature_missing", "process_delay",
    "communication_gap", "escalation_demand", "repeat_contact",
    "positive_feedback", "other",
})

VALID_ANOMALY = frozenset({"normal", "unusual", "critical"})

VALID_POLARITY = frozenset({"positive", "negative", "mixed", "neutral"})

VALID_SENTIMENT_RANGE = (1, 5)  # min, max


class Tool:
    """Definition of a single tool available to workers."""

    __slots__ = ("name", "description", "parameters", "handler")

    def __init__(self, name, description, parameters, handler):
        self.name = name
        self.description = description
        self.parameters = parameters    # dict of param_name -> description
        self.handler = handler          # callable(args_dict) -> dict

    def __repr__(self):
        return f"Tool({self.name!r})"


class ToolRegistry:
    """
    Registry of all tools available to worker agents.

    Tools query SQLite and return dicts. Context (scan_id, batch_id, trc)
    is set per-batch via set_context() before tool execution begins.

    Usage:
        registry = ToolRegistry(db_path)
        registry.set_context(scan_id="scan_1", batch_id="b_1", trc="RCM_02")
        result = registry.execute("query_taxonomy", {"trc": "RCM_02"})
    """

    # P4: Buffered commits — commit every N inserts instead of per-ticket
    _COMMIT_INTERVAL = 10

    def __init__(self, db_path):
        """
        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._conn = None
        self._context = {}     # scan_id, batch_id, trc, agent_id
        self._tools = {}
        self._call_count = 0
        self._write_buffer: list[str] = []  # P4: buffered ticket IDs
        self._register_tools()

    @property
    def conn(self):
        """Lazy SQLite connection (per-thread safety)."""
        if self._conn is None:
            self._conn = get_connection(self.db_path)
        return self._conn

    def close(self):
        """Close the database connection."""
        self.flush()
        if self._conn:
            self._conn.close()
            self._conn = None

    def flush(self):
        """P4: Commit any buffered writes. Call after each batch completes."""
        if self._write_buffer and self._conn:
            try:
                self._conn.commit()
            except Exception:
                pass
            self._write_buffer.clear()

    def set_context(self, **kwargs):
        """
        Set per-batch context injected into tool handlers.

        Args:
            scan_id: Current scan ID
            batch_id: Current batch ID
            trc: TRC code for this batch (or comma-joined for mixed)
            agent_id: Worker agent ID
        """
        self._context.update(kwargs)

    def execute(self, name, args):
        """
        Execute a tool by name with the given arguments.

        Args:
            name: Tool name (e.g. "query_taxonomy")
            args: Dict of tool arguments

        Returns:
            dict with tool result (varies by tool)
        """
        tool = self._tools.get(name)
        if not tool:
            logger.warning(f"ToolRegistry: unknown tool '{name}'")
            return {
                "error": f"Unknown tool: {name}",
                "available_tools": list(self._tools.keys()),
            }

        self._call_count += 1
        try:
            result = tool.handler(args)
            return result
        except Exception as e:
            logger.error(f"ToolRegistry: {name} failed: {e}")
            return {"error": str(e), "tool": name}

    def get_prompt_description(self):
        """
        Generate tool documentation for inclusion in the system prompt.
        Workers need to know what tools exist and how to call them.

        Returns:
            str: Formatted tool descriptions for prompt injection.
        """
        lines = ["AVAILABLE TOOLS (call via fenced code blocks):", ""]
        for tool in self._tools.values():
            lines.append(f"## {tool.name}")
            lines.append(f"   {tool.description}")
            if tool.parameters:
                lines.append("   Parameters:")
                for param, desc in tool.parameters.items():
                    lines.append(f"     - {param}: {desc}")
            lines.append("")
        return "\n".join(lines)

    @property
    def call_count(self):
        return self._call_count

    # ──────────────────────────────────────────────────────────────────────
    # Tool registration
    # ──────────────────────────────────────────────────────────────────────

    def _register_tools(self):
        """Register all 7 tools."""

        self._tools["query_taxonomy"] = Tool(
            name="query_taxonomy",
            description=(
                "Get active sub-patterns and n-gram fingerprints for a TRC. "
                "Use this to see what patterns already exist before classifying."
            ),
            parameters={
                "trc": "TRC code (e.g. 'RCM_02') or comma-separated list",
            },
            handler=self._tool_query_taxonomy,
        )

        self._tools["get_stats_context"] = Tool(
            name="get_stats_context",
            description=(
                "Get statistical context: Poisson incident flags, theta "
                "anomaly flags, and rising TF-IDF terms for a TRC."
            ),
            parameters={
                "trc": "TRC code",
            },
            handler=self._tool_get_stats_context,
        )

        self._tools["store_classification"] = Tool(
            name="store_classification",
            description=(
                "Persist a single ticket classification immediately. "
                "Call this for EACH ticket after classification."
            ),
            parameters={
                "ticket_id": "Ticket ID (required)",
                "sub_cluster": "Sub-pattern label",
                "sub_cluster_confidence": "Confidence 0.0-1.0",
                "is_novel": "True if new pattern",
                "sentiment_intensity": "1-5 scale",
                "sentiment_polarity": "positive/negative/mixed/neutral",
                "friction_type": "One of 14 friction types",
                "anomaly_flag": "normal/unusual/critical or null",
                "anomaly_reason": "Reason if anomaly",
                "entities": "Dict of extracted entities",
                "key_phrases": "List of key phrases",
                "root_cause_hint": "Root cause hypothesis",
                "summary": "Brief summary",
            },
            handler=self._tool_store_classification,
        )

        self._tools["flag_for_review"] = Tool(
            name="flag_for_review",
            description=(
                "Flag a ticket for human review. Use when uncertain, "
                "when content is ambiguous, or when a critical anomaly "
                "is detected."
            ),
            parameters={
                "ticket_id": "Ticket ID to flag",
                "reason": "Why this ticket needs review",
                "severity": "low/medium/high/critical",
            },
            handler=self._tool_flag_for_review,
        )

        self._tools["get_full_thread"] = Tool(
            name="get_full_thread",
            description=(
                "Retrieve the full conversation thread for a ticket. "
                "Use when the batch excerpt is too short to classify."
            ),
            parameters={
                "ticket_id": "Ticket ID to retrieve",
            },
            handler=self._tool_get_full_thread,
        )

        self._tools["report_progress"] = Tool(
            name="report_progress",
            description=(
                "Report classification progress for UI display. "
                "Call periodically during long batches."
            ),
            parameters={
                "classified": "Number classified so far",
                "total": "Total in this batch",
                "message": "Optional status message",
            },
            handler=self._tool_report_progress,
        )

        self._tools["check_cross_trc"] = Tool(
            name="check_cross_trc",
            description=(
                "Check if a sub-pattern or n-gram signature exists in "
                "a different TRC. Use to detect cross-TRC patterns."
            ),
            parameters={
                "pattern_label": "Sub-pattern label to search for",
                "exclude_trc": "TRC to exclude from search (current TRC)",
            },
            handler=self._tool_check_cross_trc,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Tool handlers (ported from scan_worker.py)
    # ──────────────────────────────────────────────────────────────────────

    def _tool_query_taxonomy(self, args):
        """
        Load active sub-patterns + top n-grams for a TRC.
        Ported from scan_worker._build_sub_taxonomy() and
        _build_mixed_sub_taxonomy().
        """
        trc_input = args.get("trc", self._context.get("trc", ""))
        if not trc_input:
            return {"error": "Missing trc parameter"}

        # Handle mixed TRCs (comma-separated)
        trc_list = [t.strip() for t in trc_input.split(",") if t.strip()]

        if len(trc_list) == 1:
            return self._query_single_trc_taxonomy(trc_list[0])
        else:
            return self._query_mixed_trc_taxonomy(trc_list)

    def _query_single_trc_taxonomy(self, trc):
        """Query taxonomy for a single TRC."""
        try:
            patterns = self.conn.execute("""
                SELECT pattern_id, label, description, friction_type,
                       lifetime_tickets
                FROM sub_patterns
                WHERE trc = ? AND tier IN ('active', 'probationary')
                  AND merged_into IS NULL
                ORDER BY lifetime_tickets DESC
            """, (trc,)).fetchall()
        except Exception:
            patterns = []

        if not patterns:
            return {
                "trc": trc,
                "pattern_count": 0,
                "patterns": [],
                "message": "No existing patterns for this TRC.",
            }

        result_patterns = []
        for p in patterns:
            try:
                ngrams = self.conn.execute("""
                    SELECT ngram FROM sub_pattern_ngrams
                    WHERE pattern_id = ? AND specificity > 0.1
                    ORDER BY (frequency * specificity) DESC
                    LIMIT 8
                """, (p["pattern_id"],)).fetchall()
                ngram_list = [n["ngram"] for n in ngrams]
            except Exception:
                ngram_list = []

            result_patterns.append({
                "label": p["label"],
                "friction_type": p["friction_type"] or "unclassified",
                "lifetime_tickets": p["lifetime_tickets"],
                "ngrams": ngram_list,
            })

        return {
            "trc": trc,
            "pattern_count": len(result_patterns),
            "patterns": result_patterns,
        }

    def _query_mixed_trc_taxonomy(self, trc_list):
        """Query taxonomy across multiple TRCs (mixed batch)."""
        placeholders = ",".join("?" for _ in trc_list)
        try:
            patterns = self.conn.execute(f"""
                SELECT pattern_id, trc, label, description, friction_type,
                       lifetime_tickets
                FROM sub_patterns
                WHERE trc IN ({placeholders})
                  AND tier IN ('active', 'probationary')
                  AND merged_into IS NULL
                ORDER BY lifetime_tickets DESC
                LIMIT 30
            """, trc_list).fetchall()
        except Exception:
            patterns = []

        if not patterns:
            return {
                "trcs": trc_list,
                "pattern_count": 0,
                "patterns_by_trc": {},
            }

        by_trc = {}
        for p in patterns:
            trc = p["trc"]
            if trc not in by_trc:
                by_trc[trc] = []

            try:
                ngrams = self.conn.execute("""
                    SELECT ngram FROM sub_pattern_ngrams
                    WHERE pattern_id = ? AND specificity > 0.1
                    ORDER BY (frequency * specificity) DESC
                    LIMIT 8
                """, (p["pattern_id"],)).fetchall()
                ngram_list = [n["ngram"] for n in ngrams]
            except Exception:
                ngram_list = []

            by_trc[trc].append({
                "label": p["label"],
                "friction_type": p["friction_type"] or "unclassified",
                "lifetime_tickets": p["lifetime_tickets"],
                "ngrams": ngram_list,
            })

        return {
            "trcs": trc_list,
            "pattern_count": len(patterns),
            "patterns_by_trc": by_trc,
        }

    def _tool_get_stats_context(self, args):
        """
        Query statistical engines for TRC context.
        Ported from scan_worker._build_stats_context().
        """
        trc = args.get("trc", self._context.get("trc", ""))
        if not trc:
            return {"error": "Missing trc parameter"}

        result = {
            "trc": trc,
            "incident_flags": [],
            "theta_anomalies": [],
            "rising_terms": [],
        }

        # Poisson/CUSUM incident flags
        try:
            flags = self.conn.execute("""
                SELECT flag_type, flagged_date, description
                FROM incident_flags
                WHERE trc_code = ? AND status = 'open'
                ORDER BY flagged_date DESC LIMIT 5
            """, (trc,)).fetchall()
            result["incident_flags"] = [
                {
                    "type": f["flag_type"],
                    "date": f["flagged_date"],
                    "description": f["description"],
                }
                for f in flags
            ]
        except Exception:
            pass

        # Theta anomaly flags (sentiment + term_freq)
        try:
            flags = self.conn.execute("""
                SELECT metric_type, date, direction
                FROM anomaly_flags
                WHERE trc_code = ?
                  AND metric_type IN ('sentiment', 'term_freq')
                ORDER BY date DESC LIMIT 5
            """, (trc,)).fetchall()
            result["theta_anomalies"] = [
                {
                    "metric": f["metric_type"],
                    "date": f["date"],
                    "direction": f["direction"],
                }
                for f in flags
            ]
        except Exception:
            pass

        # Rising TF-IDF terms
        try:
            terms = self.conn.execute("""
                SELECT term, score FROM tfidf_scores
                WHERE trc = ?
                ORDER BY score DESC LIMIT 10
            """, (trc,)).fetchall()
            result["rising_terms"] = [
                {"term": t["term"], "score": round(t["score"], 3)}
                for t in terms
            ]
        except Exception:
            pass

        return result

    def _tool_store_classification(self, args):
        """
        Persist a single ticket classification.
        Ported from scan_worker._store_classifications() with
        field validation from _parse_response().
        """
        ticket_id = args.get("ticket_id", "")
        if not ticket_id:
            return {"error": "Missing ticket_id"}

        # ── 5.4: Ticket ID boundary guard ──
        # Reject classifications for tickets not in this batch's manifest.
        # Prevents model overflow (classifying sequential IDs beyond batch
        # boundary) which would assign the batch-level TRC fallback to
        # wrong tickets.
        ticket_trc_map = self._context.get("ticket_trc_map", {})
        if ticket_trc_map and ticket_id not in ticket_trc_map:
            logger.warning(
                "Boundary guard: rejected ticket_id=%s "
                "(not in batch manifest of %d tickets)",
                ticket_id, len(ticket_trc_map),
            )
            return {
                "status": "rejected",
                "ticket_id": ticket_id,
                "reason": "not_in_batch",
            }

        # Resolve per-ticket TRC: prefer explicit arg, then per-ticket
        # lookup (mixed batch), then batch-level fallback (5.3 fix)
        trc = (
            args.get("trc")
            or ticket_trc_map.get(ticket_id)
            or self._context.get("trc", "")
        )
        scan_id = self._context.get("scan_id", "")
        batch_id = self._context.get("batch_id", "")

        # Field validation and clamping (ported from scan_worker)
        sub_cluster = str(args.get("sub_cluster", ""))[:200]
        sub_cluster_confidence = max(0.0, min(1.0,
            float(args.get("sub_cluster_confidence", 0.5))))
        is_novel = 1 if args.get("is_novel") else 0

        sentiment_intensity = max(
            VALID_SENTIMENT_RANGE[0],
            min(VALID_SENTIMENT_RANGE[1],
                int(args.get("sentiment_intensity", 3)))
        )

        sentiment_polarity = args.get("sentiment_polarity", "neutral")
        if sentiment_polarity not in VALID_POLARITY:
            sentiment_polarity = "neutral"

        friction_type = args.get("friction_type", "other")
        if friction_type not in VALID_FRICTION:
            friction_type = "other"

        anomaly_flag = args.get("anomaly_flag")
        if anomaly_flag and anomaly_flag not in VALID_ANOMALY:
            anomaly_flag = None

        anomaly_reason = str(args.get("anomaly_reason", "") or "")[:500]

        entities = args.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}

        key_phrases = args.get("key_phrases", [])
        if not isinstance(key_phrases, list):
            key_phrases = []

        root_cause_hint = str(args.get("root_cause_hint", "") or "")[:500]
        summary = str(args.get("summary", "") or "")[:1000]

        classification_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO nlp_ticket_classifications (
                    classification_id, batch_id, scan_id, ticket_id,
                    trc, sub_cluster, sub_cluster_confidence, is_novel,
                    sentiment_intensity, sentiment_polarity, friction_type,
                    anomaly_flag, anomaly_reason, entities_json,
                    key_phrases, root_cause_hint, summary,
                    raw_classification, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                classification_id, batch_id, scan_id, ticket_id,
                trc, sub_cluster, sub_cluster_confidence, is_novel,
                sentiment_intensity, sentiment_polarity, friction_type,
                anomaly_flag, anomaly_reason,
                json.dumps(entities),
                json.dumps(key_phrases),
                root_cause_hint, summary,
                json.dumps(args),  # raw_classification
                now,
            ))

            # ── Build 11.0: Write to persistent ticket_index ──
            try:
                from src.services.ticket_index_writer import upsert_ticket_index
                upsert_ticket_index(
                    ticket_id=ticket_id,
                    scan_id=scan_id,
                    classification={
                        "summary": summary,
                        "friction_type": friction_type,
                        "sub_cluster": sub_cluster,
                        "sub_cluster_confidence": sub_cluster_confidence,
                        "sentiment_polarity": sentiment_polarity,
                        "sentiment_intensity": sentiment_intensity,
                        "anomaly_flag": anomaly_flag,
                        "anomaly_reason": anomaly_reason,
                        "root_cause_hint": root_cause_hint,
                        "entities": entities,
                        "key_phrases": key_phrases,
                        "is_novel": is_novel,
                    },
                    ticket_meta={"trc": trc},
                    conn=self.conn,
                )
            except Exception as ti_err:
                logger.warning("ticket_index upsert failed: %s", ti_err)

            # P4: Buffered commit — batch N inserts before fsync
            self._write_buffer.append(ticket_id)
            if len(self._write_buffer) >= self._COMMIT_INTERVAL:
                self.conn.commit()
                self._write_buffer.clear()

            return {
                "status": "stored",
                "classification_id": classification_id,
                "ticket_id": ticket_id,
                "trc": trc,
                "sub_cluster": sub_cluster,
                "is_novel": bool(is_novel),
            }
        except Exception as e:
            return {"error": f"Failed to store: {e}", "ticket_id": ticket_id}

    def _tool_flag_for_review(self, args):
        """Flag a ticket for human review."""
        ticket_id = args.get("ticket_id", "")
        reason = args.get("reason", "")
        if not ticket_id or not reason:
            return {"error": "Missing ticket_id or reason"}

        severity = args.get("severity", "medium")
        if severity not in ("low", "medium", "high", "critical"):
            severity = "medium"

        agent_id = self._context.get("agent_id", "")
        scan_id = self._context.get("scan_id", "")

        try:
            self.conn.execute("""
                INSERT INTO review_flags
                    (ticket_id, reason, severity, agent_id, scan_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                ticket_id, reason, severity, agent_id, scan_id,
                datetime.utcnow().isoformat(),
            ))
            self.conn.commit()

            return {
                "status": "flagged",
                "ticket_id": ticket_id,
                "severity": severity,
            }
        except Exception as e:
            return {"error": f"Failed to flag: {e}"}

    def _tool_get_full_thread(self, args):
        """Retrieve full conversation thread for a ticket.

        Handles two DB schemas:
          - full_thread column: single text blob (conversations table)
          - role/message columns: individual message rows (older schemas)
        """
        ticket_id = args.get("ticket_id", "")
        if not ticket_id:
            return {"error": "Missing ticket_id"}

        try:
            # First, try the full_thread column (primary schema) via warehouse
            from src.data.source_registry import SourceRegistry
            from src.data.warehouse_query import WarehouseQuery
            _wq = WarehouseQuery(self.conn, SourceRegistry(self.conn))
            _rows = _wq.query_conversations_raw(
                "SELECT full_thread, message_count, created_at FROM {table} WHERE ticket_id = ?",
                (ticket_id,),
            )
            row = _rows[0] if _rows else None

            _full_thread = row[0] if row else None
            _msg_count = row[1] if row else None
            if row and _full_thread:
                messages = []
                for line in _full_thread.split("\n"):
                    line = line.strip()
                    if line:
                        messages.append(line[:2000])

                return {
                    "ticket_id": ticket_id,
                    "message_count": _msg_count or len(messages),
                    "thread": messages[:50],
                }

            if not row:
                return {
                    "ticket_id": ticket_id,
                    "message_count": 0,
                    "thread": [],
                    "note": "No conversation found for this ticket.",
                }

            # Fallback: try individual comment rows if full_thread is empty
            try:
                _comment_rows = _wq.query_comments_raw(
                    "SELECT author_role, body, created_at FROM {table} WHERE ticket_id = ? ORDER BY created_at ASC",
                    (ticket_id,),
                )

                thread = [
                    {
                        "role": r[0],
                        "message": (r[1] or "")[:2000],
                        "timestamp": r[2],
                    }
                    for r in _comment_rows
                ]

                return {
                    "ticket_id": ticket_id,
                    "message_count": len(thread),
                    "thread": thread[:50],
                }
            except Exception:
                # No role/message columns — return the row we found with empty thread
                return {
                    "ticket_id": ticket_id,
                    "message_count": 0,
                    "thread": [],
                    "note": "Conversation row exists but no thread data.",
                }

        except Exception as e:
            return {"error": f"Failed to retrieve thread: {e}"}

    def _tool_report_progress(self, args):
        """Write progress to scan_progress table for UI polling."""
        scan_id = self._context.get("scan_id", "")
        if not scan_id:
            return {"error": "No scan_id in context"}

        classified = int(args.get("classified", 0))
        total = int(args.get("total", 0))
        message = args.get("message", "")

        try:
            self.conn.execute("""
                INSERT INTO scan_progress
                    (scan_id, classified, total, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scan_id) DO UPDATE SET
                    classified = classified + ?,
                    total = CASE WHEN ? > total THEN ? ELSE total END,
                    updated_at = ?
            """, (
                scan_id, classified, total,
                datetime.utcnow().isoformat(),
                classified, total, total,
                datetime.utcnow().isoformat(),
            ))
            self.conn.commit()

            return {
                "status": "progress_reported",
                "classified": classified,
                "total": total,
            }
        except Exception as e:
            return {"error": f"Failed to report progress: {e}"}

    def _tool_check_cross_trc(self, args):
        """Check if a pattern label or n-gram exists in a different TRC."""
        pattern_label = args.get("pattern_label", "")
        exclude_trc = args.get("exclude_trc", self._context.get("trc", ""))

        if not pattern_label:
            return {"error": "Missing pattern_label"}

        matches = []

        # Check sub_patterns by label similarity
        try:
            patterns = self.conn.execute("""
                SELECT trc, label, friction_type, lifetime_tickets
                FROM sub_patterns
                WHERE label LIKE ?
                  AND trc != ?
                  AND tier IN ('active', 'probationary')
                  AND merged_into IS NULL
                ORDER BY lifetime_tickets DESC
                LIMIT 10
            """, (f"%{pattern_label}%", exclude_trc)).fetchall()

            for p in patterns:
                matches.append({
                    "trc": p["trc"],
                    "label": p["label"],
                    "friction_type": p["friction_type"],
                    "lifetime_tickets": p["lifetime_tickets"],
                    "match_type": "label",
                })
        except Exception:
            pass

        # Check n-grams
        try:
            # Use key words from the pattern label as n-gram search
            words = pattern_label.lower().split()
            for word in words[:3]:
                if len(word) < 4:
                    continue
                ngram_matches = self.conn.execute("""
                    SELECT DISTINCT sp.trc, sp.label, spn.ngram
                    FROM sub_pattern_ngrams spn
                    JOIN sub_patterns sp ON sp.pattern_id = spn.pattern_id
                    WHERE spn.ngram LIKE ?
                      AND sp.trc != ?
                      AND sp.tier IN ('active', 'probationary')
                      AND sp.merged_into IS NULL
                    LIMIT 5
                """, (f"%{word}%", exclude_trc)).fetchall()

                for m in ngram_matches:
                    matches.append({
                        "trc": m["trc"],
                        "label": m["label"],
                        "matching_ngram": m["ngram"],
                        "match_type": "ngram",
                    })
        except Exception:
            pass

        # Deduplicate by (trc, label)
        seen = set()
        unique_matches = []
        for m in matches:
            key = (m["trc"], m["label"])
            if key not in seen:
                seen.add(key)
                unique_matches.append(m)

        return {
            "pattern_label": pattern_label,
            "exclude_trc": exclude_trc,
            "cross_trc_matches": unique_matches,
            "match_count": len(unique_matches),
        }

    # ──────────────────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────────────────

    def get_available_tools(self):
        """Return list of tool names."""
        return list(self._tools.keys())

    def __repr__(self):
        return (
            f"ToolRegistry(tools={len(self._tools)}, "
            f"calls={self._call_count}, "
            f"context={list(self._context.keys())})"
        )
