"""
Alma Insights — Source Warehouse (Phase 3.5)

Classifies and persists source record metadata (no PHI) into the
cold tier.  Uses a hybrid classification pipeline:
  1. N-gram fast path — match against existing sub_pattern_ngrams
  2. LLM slow path — call build_client_for_task("nlp_classification").generate()

All tables are source-agnostic (keyed by ``source`` column).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone

logger = logging.getLogger("alma.warehouse")

# Minimum n-gram specificity to use fast-path classification
_NGRAM_MIN_SPECIFICITY = 0.4
# Minimum number of matching n-grams to accept fast-path result
_NGRAM_MIN_MATCHES = 2
# N-gram confidence threshold (ratio of matched n-grams to total for pattern)
_NGRAM_CONFIDENCE_THRESHOLD = 0.7


class SourceWarehouse:
    """Classifies and persists source record metadata (no PHI).

    Cold tier only — subject, description, requester are NEVER stored.
    Only metadata fields go to source_events.
    """

    def __init__(self, db_manager: object) -> None:
        self.db = db_manager

    def ingest_records(self, records: list[dict], source: str,
                       trc_field: str, client: object) -> list[dict]:
        """Classify and persist a batch of records from any source.

        Args:
            records: Raw record dicts from any SourceClient.
            source: Source identifier ('zendesk', 'intercom', etc.).
            trc_field: TRC field mapping string.
            client: SourceClient instance for extract_trc().

        Returns:
            Enriched record dicts (with classification added).
            Does NOT store PHI — only metadata goes to source_events.
        """
        enriched = []
        for record in records:
            ticket_id = str(record.get("id", record.get("ticket_id", "")))
            if not ticket_id:
                continue

            trc = client.extract_trc(record, trc_field)
            classification, classified_by = self._classify_hybrid(
                record, trc, source
            )

            # Build cold-tier row — NO PHI fields
            event = {
                "source": source,
                "ticket_id": ticket_id,
                "created_at": record.get("created_at", ""),
                "trc_code": trc,
                "classification": classification,
                "sentiment": record.get("sentiment", ""),
                "priority": record.get("priority", ""),
                "ticket_type": record.get("type", ""),
                "tags": ",".join(record.get("tags", []))
                        if isinstance(record.get("tags"), list) else "",
                "flagged": 1 if record.get("flagged") else 0,
                "classified_by": classified_by,
            }

            # Persist to source_events (skip duplicates)
            self._upsert_event(event)

            # Update rollups
            created_at = record.get("created_at", "")
            if created_at:
                self.update_rollups(source, trc, created_at)

            # Enrich the original record for downstream use
            enriched_record = dict(record)
            enriched_record["_classification"] = classification
            enriched_record["_classified_by"] = classified_by
            enriched_record["_trc_code"] = trc
            enriched.append(enriched_record)

        logger.info(
            "Warehouse ingested %d records from %s (%d enriched)",
            len(records), source, len(enriched)
        )
        return enriched

    def _classify_hybrid(self, record: dict, trc: str,
                         source: str) -> tuple[str, str]:
        """N-gram fast path -> LLM slow path.

        Returns (classification, classified_by).

        Fast path: Match record text against existing sub_pattern_ngrams.
        If confidence > threshold -> use the matched pattern's friction_type.

        Slow path: Build LLM prompt with redacted record context,
        call build_client_for_task("nlp_classification").generate(), parse classification.
        """
        # Try n-gram fast path first
        classification = self._classify_ngram(record, trc)
        if classification:
            return classification, "ngram"

        # LLM slow path — only if we have a client configured
        classification = self._classify_llm(record, trc)
        if classification:
            return classification, "llm"

        return "", "none"

    def _classify_ngram(self, record: dict, trc: str) -> str:
        """Match record text against sub_pattern_ngrams.

        Returns friction_type if confident match, empty string otherwise.
        """
        # Build text to match against
        text_parts = []
        for field in ("subject", "description", "tags"):
            val = record.get(field)
            if val:
                if isinstance(val, list):
                    text_parts.append(" ".join(str(v) for v in val))
                else:
                    text_parts.append(str(val))
        text = " ".join(text_parts).lower()

        if not text.strip():
            return ""

        try:
            conn = self.db.get_connection()
            # Get all n-grams for this TRC (or all TRCs if trc is generic)
            if trc and trc != "unknown":
                rows = conn.execute("""
                    SELECT spn.ngram, spn.pattern_id, spn.specificity,
                           sp.friction_type, sp.label
                    FROM sub_pattern_ngrams spn
                    JOIN sub_patterns sp ON spn.pattern_id = sp.pattern_id
                    WHERE spn.trc = ?
                      AND sp.tier IN ('active', 'learning')
                      AND sp.merged_into IS NULL
                      AND spn.specificity >= ?
                    ORDER BY spn.specificity DESC
                """, (trc, _NGRAM_MIN_SPECIFICITY)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT spn.ngram, spn.pattern_id, spn.specificity,
                           sp.friction_type, sp.label
                    FROM sub_pattern_ngrams spn
                    JOIN sub_patterns sp ON spn.pattern_id = sp.pattern_id
                    WHERE sp.tier IN ('active', 'learning')
                      AND sp.merged_into IS NULL
                      AND spn.specificity >= ?
                    ORDER BY spn.specificity DESC
                    LIMIT 500
                """, (_NGRAM_MIN_SPECIFICITY,)).fetchall()

            if not rows:
                return ""

            # Score each pattern by number of matching n-grams
            pattern_scores: dict[str, dict] = {}
            for ngram, pattern_id, specificity, friction_type, label in rows:
                if ngram.lower() in text:
                    if pattern_id not in pattern_scores:
                        pattern_scores[pattern_id] = {
                            "matches": 0,
                            "total_specificity": 0.0,
                            "friction_type": friction_type or label,
                        }
                    pattern_scores[pattern_id]["matches"] += 1
                    pattern_scores[pattern_id]["total_specificity"] += specificity

            if not pattern_scores:
                return ""

            # Find best matching pattern
            best_pattern = max(
                pattern_scores.items(),
                key=lambda x: (x[1]["matches"], x[1]["total_specificity"])
            )
            pattern_id, scores = best_pattern

            if scores["matches"] >= _NGRAM_MIN_MATCHES:
                return scores["friction_type"]

            return ""

        except Exception as exc:
            logger.debug("N-gram classification failed: %s", exc)
            return ""

    def _classify_llm(self, record: dict, trc: str) -> str:
        """Call LLM for classification. Returns friction_type or empty string.

        Uses PII redaction before sending to LLM.
        """
        try:
            from src.gemini.client_factory import build_client_for_task

            # Build redacted text for classification
            subject = record.get("subject", "")
            description = record.get("description", "")
            if not subject and not description:
                return ""

            # Apply PII redaction
            try:
                from src.gemini.gemini_client import _redact_base
                subject = _redact_base(subject)
                if description:
                    description = _redact_base(description[:500])
            except ImportError:
                # Fallback: basic email/phone redaction
                subject = re.sub(r'\S+@\S+', '[EMAIL]', subject)
                description = re.sub(r'\S+@\S+', '[EMAIL]', description[:500])

            prompt = (
                f"Classify this support ticket into a friction category.\n\n"
                f"TRC Code: {trc}\n"
                f"Subject: {subject}\n"
                f"Description: {description}\n\n"
                f"Respond with ONLY the friction category name "
                f"(e.g., 'billing_dispute', 'login_failure', 'feature_request'). "
                f"If you cannot determine the category, respond with 'unknown'."
            )

            client = build_client_for_task("nlp_classification")
            response = client.generate(prompt, timeout=30, max_tokens=100)
            classification = response.strip().strip('"').strip("'").lower()

            # Sanitize — only allow alphanumeric + underscore
            classification = re.sub(r'[^a-z0-9_]', '_', classification)
            classification = re.sub(r'_+', '_', classification).strip('_')

            if classification and classification != "unknown":
                return classification

        except Exception as exc:
            logger.debug("LLM classification failed: %s", exc)

        return ""

    def _upsert_event(self, event: dict):
        """Insert or ignore a source event (dedup by source+ticket_id)."""
        try:
            conn = self.db.get_connection()
            conn.execute("""
                INSERT OR IGNORE INTO source_events
                    (source, ticket_id, created_at, trc_code, classification,
                     sentiment, priority, ticket_type, tags, flagged,
                     classified_by, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                event["source"], event["ticket_id"], event["created_at"],
                event["trc_code"], event["classification"],
                event["sentiment"], event["priority"], event["ticket_type"],
                event["tags"], event["flagged"], event["classified_by"],
            ))
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to upsert source event: %s", exc)

    def update_rollups(self, source: str, trc_code: str, created_at: str) -> None:
        """Upsert hourly and daily rollup counts."""
        try:
            # Parse created_at to get hour and day buckets
            if "T" in created_at:
                dt_str = created_at[:19]  # "2026-03-10T14:30:00"
            else:
                dt_str = created_at[:19]

            # Hour bucket: "2026-03-10T14:00:00"
            hour_bucket = dt_str[:13] + ":00:00"
            # Day bucket: "2026-03-10"
            day_bucket = dt_str[:10]

            conn = self.db.get_connection()

            # Upsert hourly
            conn.execute("""
                INSERT INTO source_trc_hourly (source, trc_code, hour_bucket, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(source, trc_code, hour_bucket)
                DO UPDATE SET count = count + 1
            """, (source, trc_code, hour_bucket))

            # Upsert daily
            conn.execute("""
                INSERT INTO source_trc_daily (source, trc_code, day_bucket, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(source, trc_code, day_bucket)
                DO UPDATE SET count = count + 1
            """, (source, trc_code, day_bucket))

            conn.commit()

        except Exception as exc:
            logger.debug("Rollup update failed: %s", exc)

    def get_trc_baseline(self, source: str, trc_code: str,
                         lookback_hours: int = 168) -> list[dict]:
        """Hourly counts for a source+TRC over lookback window."""
        try:
            conn = self.db.get_connection()
            cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00")
            rows = conn.execute("""
                SELECT hour_bucket, count
                FROM source_trc_hourly
                WHERE source = ? AND trc_code = ?
                  AND hour_bucket >= datetime(?, '-' || ? || ' hours')
                ORDER BY hour_bucket
            """, (source, trc_code, cutoff, lookback_hours)).fetchall()
            return [{"hour": r[0], "count": r[1]} for r in rows]
        except Exception as exc:
            logger.debug("get_trc_baseline failed: %s", exc)
            return []

    def get_daily_trend(self, source: str, trc_code: str,
                        lookback_days: int = 30) -> list[dict]:
        """Daily counts for trend charts and Guru effectiveness."""
        try:
            conn = self.db.get_connection()
            cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            rows = conn.execute("""
                SELECT day_bucket, count
                FROM source_trc_daily
                WHERE source = ? AND trc_code = ?
                  AND day_bucket >= date(?, '-' || ? || ' days')
                ORDER BY day_bucket
            """, (source, trc_code, cutoff, lookback_days)).fetchall()
            return [{"day": r[0], "count": r[1]} for r in rows]
        except Exception as exc:
            logger.debug("get_daily_trend failed: %s", exc)
            return []

    def get_hourly_count(self, source: str, trc_code: str,
                         window_minutes: int = 60) -> int:
        """Total count for source+TRC in the last N minutes.

        Used by watchlist volume rules.
        """
        try:
            conn = self.db.get_connection()
            rows = conn.execute("""
                SELECT COALESCE(SUM(count), 0)
                FROM source_trc_hourly
                WHERE source = ? AND trc_code = ?
                  AND hour_bucket >= datetime('now', '-' || ? || ' minutes')
            """, (source, trc_code, window_minutes)).fetchone()
            return rows[0] if rows else 0
        except Exception:
            return 0

    def rollup_maintenance(self) -> None:
        """Prune hourly rollups > 7 days. Auto-expire open alerts > 24h."""
        try:
            conn = self.db.get_connection()

            # Prune old hourly rollups
            deleted = conn.execute("""
                DELETE FROM source_trc_hourly
                WHERE hour_bucket < datetime('now', '-7 days')
            """).rowcount
            if deleted:
                logger.info("Pruned %d old hourly rollup rows", deleted)

            # Auto-expire old open alerts
            expired = conn.execute("""
                UPDATE watchlist_alerts
                SET status = 'expired',
                    resolved_at = datetime('now'),
                    resolved_by = 'auto_expire'
                WHERE status = 'open'
                  AND created_at < datetime('now', '-24 hours')
            """).rowcount
            if expired:
                logger.info("Auto-expired %d old alerts", expired)

            conn.commit()
        except Exception as exc:
            logger.warning("Rollup maintenance failed: %s", exc)
