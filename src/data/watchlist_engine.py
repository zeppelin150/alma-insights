"""
Alma Insights — Watchlist Engine (Phase 3.5)

UI-configurable rule engine for real-time alert detection.
Source-agnostic — evaluates rules against records from any source.

Rule types:
    keyword   — match terms in subject/description
    entity    — match entity type (provider/payer/member)
    volume    — TRC count exceeds threshold in time window
    compound  — keyword + entity + optional volume (most sophisticated)

EWMA Learning Loop:
    - Each rule has ewma_confidence (0.1 to 0.95)
    - User feedback (confirm/dismiss) updates confidence
    - Low-confidence rules are skipped (< 0.3)
    - Incident severity always fires (bypasses EWMA)
    - Cold start (< 5 fires) always fires

LLM Triage:
    - Compound rules with entity match trigger LLM for single-ticket analysis
    - Few-shot examples from watchlist_examples table
    - Calls build_client_for_task("watchlist_triage").generate()
"""

import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger("alma.watchlist")


class WatchlistEngine:
    """Evaluates watchlist rules against incoming records from any source."""

    SYSTEM_RULES = [
        {
            "name": "Site Outage Detection",
            "rule_type": "compound",
            "severity": "incident",
            "keywords": "site down,can't access,error 500,503,page not loading,outage,service unavailable",
            "keyword_mode": "any",
            "volume_threshold": 5,
            "volume_window_minutes": 15,
            "cooldown_minutes": 60,
        },
        {
            "name": "Payment Processing Failure",
            "rule_type": "keyword",
            "severity": "incident",
            "keywords": "payment failed,card declined,transaction error,checkout broken",
            "keyword_mode": "any",
            "cooldown_minutes": 120,
        },
        {
            "name": "Login/Auth Issues",
            "rule_type": "keyword",
            "severity": "watch",
            "keywords": "can't log in,password reset,authentication,locked out,SSO",
            "keyword_mode": "any",
            "cooldown_minutes": 180,
        },
        {
            "name": "High-Value Provider Alert",
            "rule_type": "compound",
            "severity": "incident",
            "entity_type": "provider",
            "keywords": "payout,reimbursement,claim amount,overpayment,underpayment",
            "keyword_mode": "any",
            "cooldown_minutes": 240,
        },
        {
            "name": "Data/Privacy Concern",
            "rule_type": "keyword",
            "severity": "incident",
            "keywords": "data breach,exposed,leaked,unauthorized access,HIPAA",
            "keyword_mode": "any",
            "cooldown_minutes": 60,
        },
    ]

    def __init__(self, db_manager, warehouse=None):
        self.db = db_manager
        self.warehouse = warehouse
        self._ensure_system_rules()

    def _ensure_system_rules(self):
        """Insert system rules on first run (is_system=1). Idempotent."""
        try:
            conn = self.db.get_connection()
            existing = conn.execute(
                "SELECT COUNT(*) FROM watchlist_rules WHERE is_system = 1"
            ).fetchone()[0]

            if existing > 0:
                return  # Already initialized

            for rule in self.SYSTEM_RULES:
                conn.execute("""
                    INSERT INTO watchlist_rules
                        (name, rule_type, severity, is_system, enabled,
                         keywords, keyword_mode, entity_type, entity_filter,
                         volume_threshold, volume_window_minutes,
                         cooldown_minutes)
                    VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rule["name"], rule["rule_type"], rule["severity"],
                    rule.get("keywords", ""), rule.get("keyword_mode", "any"),
                    rule.get("entity_type", ""), rule.get("entity_filter", ""),
                    rule.get("volume_threshold", 0),
                    rule.get("volume_window_minutes", 60),
                    rule.get("cooldown_minutes", 120),
                ))
            conn.commit()
            logger.info("Initialized %d system watchlist rules",
                        len(self.SYSTEM_RULES))

        except Exception as exc:
            logger.warning("Failed to ensure system rules: %s", exc)

    def evaluate(self, records: list[dict], source: str,
                 trc_field: str = "subject",
                 client=None) -> list[dict]:
        """Run all enabled rules against a batch of records.

        Args:
            records: Raw record dicts from any SourceClient.
            source: Source identifier ('zendesk', 'intercom', etc.).
            trc_field: TRC field mapping string.
            client: Optional SourceClient instance for extract_trc().
                    If None, falls back to record['_trc_code'] or 'unknown'.

        Returns list of alert dicts for any rules that fired.
        """
        alerts = []
        try:
            rules = self._get_enabled_rules(source)
        except Exception as exc:
            logger.warning("Failed to load watchlist rules: %s", exc)
            return alerts

        now = time.time()

        for rule in rules:
            rule_id = rule["id"]

            # 1. Check cooldown
            if self._is_in_cooldown(rule, now):
                continue

            # 2. Find matching records for this rule
            matching = []
            for record in records:
                if self._record_matches_rule(record, rule):
                    matching.append(record)

            if not matching:
                continue

            # 3. Volume check (if rule has volume_threshold)
            if rule["volume_threshold"] > 0 and self.warehouse:
                trc = self._extract_trc(matching[0], trc_field, client)
                if not self._check_volume(source, trc, rule):
                    continue

            # 4. EWMA confidence gate
            if not self._passes_ewma_gate(rule):
                continue

            # 5. LLM triage for compound rules with entity match
            llm_result = None
            if (rule["rule_type"] == "compound"
                    and rule.get("entity_type")
                    and rule["severity"] == "incident"):
                llm_result = self._llm_triage(matching[0], rule)
                if llm_result and not llm_result.get("should_alert", True):
                    continue

            # 6. Fire alert
            trc = self._extract_trc(matching[0], trc_field, client)
            alert = self._fire_alert(rule, matching, source, trc, llm_result)
            if alert:
                alerts.append(alert)

        return alerts

    @staticmethod
    def _extract_trc(record: dict, trc_field: str,
                     client=None) -> str:
        """Extract TRC from a record using client or fallback.

        Source-agnostic — works with any SourceClient implementation.
        """
        # If warehouse already enriched this record, use cached value
        if record.get("_trc_code"):
            return record["_trc_code"]

        # Use client if available
        if client is not None:
            try:
                return client.extract_trc(record, trc_field)
            except Exception:
                pass

        return "unknown"

    def _get_enabled_rules(self, source: str) -> list[dict]:
        """Load all enabled rules, filtered by source if applicable."""
        conn = self.db.get_connection()
        rows = conn.execute("""
            SELECT id, name, rule_type, severity, source_filter,
                   is_system, keywords, keyword_mode,
                   entity_type, entity_filter,
                   volume_threshold, volume_window_minutes,
                   ewma_confidence, ewma_alpha,
                   total_fires, total_confirmed, total_dismissed,
                   cooldown_minutes, last_fired_at
            FROM watchlist_rules
            WHERE enabled = 1
              AND (source_filter = '' OR source_filter = ?)
        """, (source,)).fetchall()

        columns = [
            "id", "name", "rule_type", "severity", "source_filter",
            "is_system", "keywords", "keyword_mode",
            "entity_type", "entity_filter",
            "volume_threshold", "volume_window_minutes",
            "ewma_confidence", "ewma_alpha",
            "total_fires", "total_confirmed", "total_dismissed",
            "cooldown_minutes", "last_fired_at",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def _is_in_cooldown(self, rule: dict, now: float) -> bool:
        """Check if rule is in cooldown period."""
        last_fired = rule.get("last_fired_at", "")
        if not last_fired:
            return False
        try:
            last_dt = datetime.fromisoformat(last_fired.replace("Z", "+00:00"))
            elapsed_minutes = (now - last_dt.timestamp()) / 60
            return elapsed_minutes < rule.get("cooldown_minutes", 120)
        except (ValueError, TypeError):
            return False

    def _record_matches_rule(self, record: dict, rule: dict) -> bool:
        """Check if a single record matches a rule's criteria."""
        rule_type = rule["rule_type"]

        if rule_type == "keyword":
            return self._match_keywords(record, rule)
        elif rule_type == "entity":
            return self._match_entity(record, rule)
        elif rule_type == "compound":
            # Compound: must match keywords AND (optionally) entity
            kw_match = self._match_keywords(record, rule)
            if not kw_match:
                return False
            if rule.get("entity_type"):
                return self._match_entity(record, rule)
            return True
        elif rule_type == "volume":
            # Volume rules match all records (volume checked separately)
            return True

        return False

    def _match_keywords(self, record: dict, rule: dict) -> bool:
        """Check if record text matches rule keywords."""
        keywords_str = rule.get("keywords", "")
        if not keywords_str:
            return False

        keywords = [k.strip().lower() for k in keywords_str.split(",")
                    if k.strip()]
        if not keywords:
            return False

        # Build text to search
        text_parts = []
        for field in ("subject", "description"):
            val = record.get(field, "")
            if val:
                text_parts.append(str(val))
        text = " ".join(text_parts).lower()

        mode = rule.get("keyword_mode", "any")
        if mode == "all":
            return all(kw in text for kw in keywords)
        else:  # "any"
            return any(kw in text for kw in keywords)

    def _match_entity(self, record: dict, rule: dict) -> bool:
        """Check if record involves the target entity type.

        Uses existing entity extraction from config/entities/.
        """
        entity_type = rule.get("entity_type", "")
        if not entity_type:
            return False

        # Build text to check
        text = " ".join(str(record.get(f, ""))
                        for f in ("subject", "description", "tags")
                        if record.get(f))

        if not text:
            return False

        try:
            from src.data.entity_extractor import (
                load_entity_dictionaries, extract_entities
            )
            payer_dict, product_dict = load_entity_dictionaries()
            entities = extract_entities(text, payer_dict, product_dict)

            if entity_type == "provider":
                # Provider detection: check for provider-related terms
                provider_terms = [
                    "provider", "physician", "doctor", "clinician",
                    "practitioner", "npi", "payout"
                ]
                return any(t in text.lower() for t in provider_terms)
            elif entity_type == "payer":
                return bool(entities.get("payers"))
            elif entity_type == "member":
                member_terms = ["member", "patient", "enrollee", "subscriber"]
                return any(t in text.lower() for t in member_terms)

        except ImportError:
            logger.debug("Entity extractor not available")

        return False

    def _check_volume(self, source: str, trc_code: str, rule: dict) -> bool:
        """Check if TRC volume exceeds threshold in window."""
        if not self.warehouse:
            return False
        threshold = rule.get("volume_threshold", 0)
        window = rule.get("volume_window_minutes", 60)
        count = self.warehouse.get_hourly_count(source, trc_code, window)
        return count >= threshold

    def _passes_ewma_gate(self, rule: dict) -> bool:
        """Check if rule passes EWMA confidence gate.

        Returns True if rule should proceed:
        - Incident severity: ALWAYS (bypasses gate)
        - Cold start (< 5 fires): ALWAYS
        - ewma_confidence >= 0.3: proceed
        - ewma_confidence < 0.3: skip (too many false positives)
        """
        # Incident severity always fires
        if rule.get("severity") == "incident":
            return True

        # Cold start — always fire until we have enough data
        total_fires = rule.get("total_fires", 0)
        if total_fires < 5:
            return True

        # EWMA gate
        confidence = rule.get("ewma_confidence", 0.5)
        return confidence >= 0.3

    def _llm_triage(self, record: dict, rule: dict) -> dict | None:
        """Call LLM for single-record anomaly triage.

        Returns {should_alert: bool, confidence: float, reason: str}
        or None on failure.
        """
        try:
            from src.gemini.client_factory import build_client_for_task

            # Load few-shot examples
            examples = self._load_examples(rule["id"])
            examples_text = ""
            if examples:
                examples_text = "\n\nPast examples:\n"
                for ex in examples:
                    examples_text += (
                        f"- [{ex['outcome']}] {ex['sanitized_text'][:200]}\n"
                    )

            # Build redacted text
            subject = record.get("subject", "")
            description = record.get("description", "")
            try:
                from src.gemini.gemini_client import _redact_base
                subject = _redact_base(subject)
                description = _redact_base(description[:500])
            except ImportError:
                subject = re.sub(r'\S+@\S+', '[EMAIL]', subject)
                description = re.sub(r'\S+@\S+', '[EMAIL]', description[:500])

            prompt = (
                f"You are an alert triage system. Evaluate whether this "
                f"support ticket warrants a '{rule['severity']}' alert "
                f"for rule '{rule['name']}'.\n\n"
                f"Rule keywords: {rule.get('keywords', '')}\n"
                f"Entity type: {rule.get('entity_type', 'any')}\n\n"
                f"Ticket subject: {subject}\n"
                f"Ticket description: {description}\n"
                f"{examples_text}\n"
                f"Respond in this exact format:\n"
                f"ALERT: yes/no\n"
                f"CONFIDENCE: 0.0-1.0\n"
                f"REASON: brief explanation"
            )

            client = build_client_for_task("watchlist_triage")
            response = client.generate(prompt, timeout=30, max_tokens=200)

            # Parse response
            should_alert = "alert: yes" in response.lower()
            confidence = 0.5
            reason = ""
            for line in response.split("\n"):
                line_lower = line.strip().lower()
                if line_lower.startswith("confidence:"):
                    try:
                        confidence = float(
                            line_lower.replace("confidence:", "").strip()
                        )
                    except ValueError:
                        pass
                elif line_lower.startswith("reason:"):
                    reason = line.split(":", 1)[1].strip()

            return {
                "should_alert": should_alert,
                "confidence": confidence,
                "reason": reason,
            }

        except Exception as exc:
            logger.debug("LLM triage failed: %s", exc)
            return None

    def _load_examples(self, rule_id: int, limit: int = 5) -> list[dict]:
        """Load few-shot examples for a rule (up to limit per outcome)."""
        try:
            conn = self.db.get_connection()
            rows = conn.execute("""
                SELECT sanitized_text, outcome
                FROM watchlist_examples
                WHERE rule_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (rule_id, limit * 2)).fetchall()
            return [{"sanitized_text": r[0], "outcome": r[1]} for r in rows]
        except Exception:
            return []

    def _fire_alert(self, rule: dict, matching: list[dict],
                    source: str, trc_code: str,
                    llm_result: dict | None) -> dict | None:
        """Create and persist an alert."""
        try:
            rule_id = rule["id"]
            ticket_ids = [str(r.get("id", r.get("ticket_id", "")))
                          for r in matching]

            title = f"{rule['name']}"
            summary = (
                f"Rule '{rule['name']}' matched {len(matching)} "
                f"record(s) from {source}"
            )
            if llm_result and llm_result.get("reason"):
                summary += f". LLM: {llm_result['reason']}"

            conn = self.db.get_connection()

            # Insert alert
            cursor = conn.execute("""
                INSERT INTO watchlist_alerts
                    (rule_id, source, severity, title, summary,
                     ticket_count, ticket_ids, trc_code, status,
                     llm_triage, llm_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """, (
                rule_id, source, rule["severity"], title, summary,
                len(matching), ",".join(ticket_ids), trc_code,
                str(llm_result) if llm_result else "",
                llm_result.get("confidence", 0.0) if llm_result else 0.0,
            ))
            alert_id = cursor.lastrowid

            # Update rule counters
            conn.execute("""
                UPDATE watchlist_rules
                SET total_fires = total_fires + 1,
                    last_fired_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
            """, (rule_id,))

            conn.commit()

            alert = {
                "id": alert_id,
                "rule_id": rule_id,
                "rule_name": rule["name"],
                "source": source,
                "severity": rule["severity"],
                "title": title,
                "summary": summary,
                "ticket_count": len(matching),
                "ticket_ids": ticket_ids,
                "trc_code": trc_code,
                "status": "open",
                "llm_result": llm_result,
            }
            logger.info("Alert fired: %s (%s) — %d records",
                        title, rule["severity"], len(matching))
            return alert

        except Exception as exc:
            logger.warning("Failed to fire alert: %s", exc)
            return None

    def record_feedback(self, alert_id: int, outcome: str):
        """User confirms or dismisses an alert -> update EWMA.

        outcome: 'confirmed' | 'dismissed'
        """
        try:
            conn = self.db.get_connection()

            # Get the alert and its rule
            alert = conn.execute("""
                SELECT rule_id, source, trc_code
                FROM watchlist_alerts
                WHERE id = ?
            """, (alert_id,)).fetchone()

            if not alert:
                logger.warning("Alert %d not found", alert_id)
                return

            rule_id = alert[0]

            # Update alert status
            status = "confirmed" if outcome == "confirmed" else "dismissed"
            conn.execute("""
                UPDATE watchlist_alerts
                SET status = ?, resolved_at = datetime('now'),
                    resolved_by = 'user'
                WHERE id = ?
            """, (status, alert_id))

            # Update rule EWMA + counters
            rule = conn.execute("""
                SELECT ewma_confidence, ewma_alpha,
                       total_confirmed, total_dismissed
                FROM watchlist_rules WHERE id = ?
            """, (rule_id,)).fetchone()

            if rule:
                old_conf, alpha = rule[0], rule[1]
                signal = 1.0 if outcome == "confirmed" else 0.0
                new_conf = alpha * signal + (1.0 - alpha) * old_conf
                # Bound: floor 0.1, ceiling 0.95
                new_conf = max(0.1, min(0.95, new_conf))

                confirmed_delta = 1 if outcome == "confirmed" else 0
                dismissed_delta = 1 if outcome == "dismissed" else 0

                conn.execute("""
                    UPDATE watchlist_rules
                    SET ewma_confidence = ?,
                        total_confirmed = total_confirmed + ?,
                        total_dismissed = total_dismissed + ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                """, (new_conf, confirmed_delta, dismissed_delta, rule_id))

            # If confirmed, add sanitized example for future few-shot
            if outcome == "confirmed":
                self._add_example(conn, rule_id, alert_id)

            conn.commit()
            logger.info("Alert %d %s (EWMA updated)", alert_id, status)

        except Exception as exc:
            logger.warning("Failed to record feedback: %s", exc)

    def _add_example(self, conn, rule_id: int, alert_id: int):
        """Add sanitized alert text to few-shot example bank.

        Caps at 10 examples per rule (5 confirmed + 5 dismissed).
        """
        try:
            alert_data = conn.execute("""
                SELECT summary, trc_code
                FROM watchlist_alerts WHERE id = ?
            """, (alert_id,)).fetchone()

            if not alert_data:
                return

            # Check cap
            count = conn.execute("""
                SELECT COUNT(*) FROM watchlist_examples
                WHERE rule_id = ? AND outcome = 'true_positive'
            """, (rule_id,)).fetchone()[0]

            if count >= 5:
                # Remove oldest
                conn.execute("""
                    DELETE FROM watchlist_examples
                    WHERE id = (
                        SELECT id FROM watchlist_examples
                        WHERE rule_id = ? AND outcome = 'true_positive'
                        ORDER BY created_at ASC LIMIT 1
                    )
                """, (rule_id,))

            conn.execute("""
                INSERT INTO watchlist_examples
                    (rule_id, example_type, sanitized_text, outcome, trc_code)
                VALUES (?, 'confirmed_alert', ?, 'true_positive', ?)
            """, (rule_id, alert_data[0], alert_data[1] or ""))

        except Exception as exc:
            logger.debug("Failed to add example: %s", exc)

    # ── CRUD ──────────────────────────────────────────────────

    def list_rules(self, source: str = "") -> list[dict]:
        """All rules, optionally filtered by source_filter."""
        try:
            conn = self.db.get_connection()
            if source:
                rows = conn.execute("""
                    SELECT * FROM watchlist_rules
                    WHERE source_filter = '' OR source_filter = ?
                    ORDER BY is_system DESC, name ASC
                """, (source,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM watchlist_rules
                    ORDER BY is_system DESC, name ASC
                """).fetchall()

            columns = [desc[0] for desc in conn.execute(
                "SELECT * FROM watchlist_rules LIMIT 0"
            ).description]
            return [dict(zip(columns, row)) for row in rows]

        except Exception as exc:
            logger.warning("Failed to list rules: %s", exc)
            return []

    def create_rule(self, **kwargs) -> int:
        """Create a user rule (is_system=0). Returns rule ID."""
        try:
            conn = self.db.get_connection()
            kwargs["is_system"] = 0
            kwargs.setdefault("enabled", 1)
            kwargs.setdefault("severity", "watch")
            kwargs.setdefault("keyword_mode", "any")
            kwargs.setdefault("ewma_confidence", 0.5)
            kwargs.setdefault("ewma_alpha", 0.3)
            kwargs.setdefault("cooldown_minutes", 120)

            cursor = conn.execute("""
                INSERT INTO watchlist_rules
                    (name, rule_type, severity, source_filter, is_system,
                     enabled, keywords, keyword_mode,
                     entity_type, entity_filter,
                     volume_threshold, volume_window_minutes,
                     ewma_confidence, ewma_alpha, cooldown_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get("name", "Untitled Rule"),
                kwargs.get("rule_type", "keyword"),
                kwargs["severity"],
                kwargs.get("source_filter", ""),
                0,  # is_system
                kwargs["enabled"],
                kwargs.get("keywords", ""),
                kwargs["keyword_mode"],
                kwargs.get("entity_type", ""),
                kwargs.get("entity_filter", ""),
                kwargs.get("volume_threshold", 0),
                kwargs.get("volume_window_minutes", 60),
                kwargs["ewma_confidence"],
                kwargs["ewma_alpha"],
                kwargs["cooldown_minutes"],
            ))
            conn.commit()
            return cursor.lastrowid

        except Exception as exc:
            logger.warning("Failed to create rule: %s", exc)
            return -1

    def update_rule(self, rule_id: int, **kwargs) -> bool:
        """Update a rule. Cannot change is_system flag."""
        try:
            conn = self.db.get_connection()
            kwargs.pop("is_system", None)  # Cannot change
            kwargs.pop("id", None)

            if not kwargs:
                return True

            set_clauses = []
            values = []
            for key, value in kwargs.items():
                set_clauses.append(f"{key} = ?")
                values.append(value)

            set_clauses.append("updated_at = datetime('now')")
            values.append(rule_id)

            conn.execute(
                f"UPDATE watchlist_rules SET {', '.join(set_clauses)} "
                f"WHERE id = ?",
                values
            )
            conn.commit()
            return True

        except Exception as exc:
            logger.warning("Failed to update rule: %s", exc)
            return False

    def delete_rule(self, rule_id: int) -> bool:
        """Delete a user rule. System rules cannot be deleted."""
        try:
            conn = self.db.get_connection()
            result = conn.execute("""
                DELETE FROM watchlist_rules
                WHERE id = ? AND is_system = 0
            """, (rule_id,))
            conn.commit()
            return result.rowcount > 0

        except Exception as exc:
            logger.warning("Failed to delete rule: %s", exc)
            return False

    def toggle_rule(self, rule_id: int, enabled: bool) -> bool:
        """Enable/disable a rule (system rules CAN be disabled)."""
        try:
            conn = self.db.get_connection()
            conn.execute("""
                UPDATE watchlist_rules
                SET enabled = ?, updated_at = datetime('now')
                WHERE id = ?
            """, (1 if enabled else 0, rule_id))
            conn.commit()
            return True

        except Exception as exc:
            logger.warning("Failed to toggle rule: %s", exc)
            return False

    def get_open_alerts(self, source: str = "") -> list[dict]:
        """Get all open alerts, optionally filtered by source."""
        try:
            conn = self.db.get_connection()
            if source:
                rows = conn.execute("""
                    SELECT wa.*, wr.name as rule_name
                    FROM watchlist_alerts wa
                    JOIN watchlist_rules wr ON wa.rule_id = wr.id
                    WHERE wa.status = 'open' AND wa.source = ?
                    ORDER BY wa.created_at DESC
                """, (source,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT wa.*, wr.name as rule_name
                    FROM watchlist_alerts wa
                    JOIN watchlist_rules wr ON wa.rule_id = wr.id
                    WHERE wa.status = 'open'
                    ORDER BY wa.created_at DESC
                """).fetchall()

            columns = [desc[0] for desc in conn.execute(
                "SELECT wa.*, wr.name as rule_name "
                "FROM watchlist_alerts wa "
                "JOIN watchlist_rules wr ON wa.rule_id = wr.id LIMIT 0"
            ).description]
            return [dict(zip(columns, row)) for row in rows]

        except Exception as exc:
            logger.warning("Failed to get open alerts: %s", exc)
            return []

    def get_all_alerts(self, limit: int = 50) -> list[dict]:
        """Get recent alerts (all statuses) for the Alerts tab."""
        try:
            conn = self.db.get_connection()
            rows = conn.execute("""
                SELECT wa.*, wr.name as rule_name
                FROM watchlist_alerts wa
                JOIN watchlist_rules wr ON wa.rule_id = wr.id
                ORDER BY wa.created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

            columns = [desc[0] for desc in conn.execute(
                "SELECT wa.*, wr.name as rule_name "
                "FROM watchlist_alerts wa "
                "JOIN watchlist_rules wr ON wa.rule_id = wr.id LIMIT 0"
            ).description]
            return [dict(zip(columns, row)) for row in rows]

        except Exception as exc:
            logger.warning("Failed to get alerts: %s", exc)
            return []
