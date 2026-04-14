"""
Alma Insights — Ticket Index Writer (Build 12.0 / Phase 1 enrichment)

Dedup gate + upsert logic for the persistent ticket_index table.
Called from the NLP scan pipeline (worker_agent / stream_parser)
to maintain a PHI-free, deduplicated ticket-level record.

Phase 1 (2026-04-14) adds 8 enrichment columns on ticket_index (insurance_payer,
client_id, provider_id, agent_id, service_state, channel, session_date,
dispute_amount_usd) plus ticket_tags M:N and entity-registry upserts
(insurance_payers / clients / providers / agents). Tag writes and registry
upserts happen in the same transaction as the ticket upsert when the caller
supplies an atomic() context; otherwise they're per-statement (caller's
responsibility to batch).

Public API:
    should_classify_ticket(ticket_id, scan_id, conn) -> str
    update_scan_reference(ticket_id, scan_id, conn)
    upsert_ticket_index(ticket_id, scan_id, classification, ticket_meta, conn)
    upsert_entity_registries(ticket_id, meta, conn)
    write_ticket_tags(ticket_id, tags, conn, source='ingestion')

Module deps: json, logging, datetime
Module dependents: worker_agent, stream_parser, csv_ingestion, lightdash_client,
    backfill_enrichment script
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable

logger = logging.getLogger("alma.ticket_index")

# Settings keys that callers can pass in `ticket_meta` to populate the new
# enrichment columns. Centralized so ingestion + backfill agree on the names.
ENRICHMENT_META_KEYS: tuple[str, ...] = (
    "insurance_payer",
    "client_id",
    "provider_id",
    "agent_id",
    "service_state",
    "channel",
    "session_date",
    "dispute_amount_usd",
)


def should_classify_ticket(ticket_id: str, scan_id: str, conn) -> str:
    """Decide whether a ticket needs LLM classification.

    Args:
        ticket_id: Ticket identifier.
        scan_id: Current NLP scan ID.
        conn: SQLite connection.

    Returns:
        'classify'       — new ticket, needs full classification
        'skip'           — already processed in this scan (restart recovery)
        'update_scan_id' — good existing classification, just update reference
    """
    row = conn.execute(
        "SELECT last_seen_scan_id, classification_confidence "
        "FROM ticket_index WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchone()

    if row is None:
        return "classify"

    last_scan_id = row[0] if isinstance(row, tuple) else row["last_seen_scan_id"]
    confidence = row[1] if isinstance(row, tuple) else row["classification_confidence"]

    if last_scan_id == scan_id:
        return "skip"

    if confidence is not None and confidence >= 0.7:
        return "update_scan_id"

    return "classify"


def update_scan_reference(ticket_id: str, scan_id: str, conn):
    """Mark an existing ticket as seen in the current scan."""
    conn.execute(
        "UPDATE ticket_index SET last_seen_scan_id = ? WHERE ticket_id = ?",
        (scan_id, ticket_id),
    )


def _filter_non_pii_entities(entities_json: str | None) -> str | None:
    """Keep only non-PII entity types (payer, product_area, feature)."""
    if not entities_json:
        return None
    try:
        entities = json.loads(entities_json) if isinstance(entities_json, str) else entities_json
        safe_types = {"payer", "product_area", "feature", "system", "process"}
        if isinstance(entities, list):
            filtered = [e for e in entities if e.get("type", "").lower() in safe_types]
        elif isinstance(entities, dict):
            filtered = {k: v for k, v in entities.items() if k.lower() in safe_types}
        else:
            return None
        return json.dumps(filtered)
    except (json.JSONDecodeError, TypeError):
        return None


def _normalize_enrichment(meta: dict) -> dict[str, Any]:
    """Pull enrichment fields from meta, normalizing blank strings to None.

    Centralizes the "blank → NULL" rule so INSERT and UPDATE share it.
    """
    out: dict[str, Any] = {}
    for key in ENRICHMENT_META_KEYS:
        val = meta.get(key)
        if isinstance(val, str):
            val = val.strip() or None
        out[key] = val
    return out


_INGESTION_SENTINEL_SCAN_ID = "ingestion"


def ensure_ticket_index_row(ticket_id: str, meta: dict, conn) -> None:
    """Create a ticket_index row at ingestion time with enrichment only.

    Used by CSV ingestion and Lightdash ingestion to pre-populate enrichment
    fields before any NLP classification has run. When a scan later calls
    upsert_ticket_index, the existing row is updated with classification data
    while enrichment columns survive via the COALESCE clause on conflict.

    Uses the sentinel scan_id 'ingestion' for first_seen_scan_id /
    last_seen_scan_id to satisfy the NOT NULL constraint on migration 005.
    Downstream code that filters by real scan_ids will simply not see these
    rows until a real scan updates them — which is the correct behavior.

    Args:
        ticket_id: Ticket identifier.
        meta: dict of enrichment fields. Recognized keys are
            ENRICHMENT_META_KEYS plus 'subject', 'trc_code', 'trc_label',
            'created_at' / 'ticket_created_date', 'csat_score',
            'message_count', 'resolution_hours', 'dataset_id', 'tags'.
        conn: SQLite connection (caller manages transaction).
    """
    now = datetime.utcnow().isoformat()
    enrich = _normalize_enrichment(meta)

    conn.execute(
        """
        INSERT INTO ticket_index (
            ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
            ticket_created_date, trc_code, trc_label, subject_sanitized,
            csat_score, message_count, resolution_hours, dataset_id,
            insurance_payer, client_id, provider_id, agent_id,
            service_state, channel, session_date, dispute_amount_usd
        ) VALUES (?, ?, ?, ?,
                  ?, ?, ?, ?,
                  ?, ?, ?, ?,
                  ?, ?, ?, ?,
                  ?, ?, ?, ?)
        ON CONFLICT(ticket_id) DO UPDATE SET
            insurance_payer    = COALESCE(excluded.insurance_payer,    ticket_index.insurance_payer),
            client_id          = COALESCE(excluded.client_id,          ticket_index.client_id),
            provider_id        = COALESCE(excluded.provider_id,        ticket_index.provider_id),
            agent_id           = COALESCE(excluded.agent_id,           ticket_index.agent_id),
            service_state      = COALESCE(excluded.service_state,      ticket_index.service_state),
            channel            = COALESCE(excluded.channel,            ticket_index.channel),
            session_date       = COALESCE(excluded.session_date,       ticket_index.session_date),
            dispute_amount_usd = COALESCE(excluded.dispute_amount_usd, ticket_index.dispute_amount_usd),
            ticket_created_date = COALESCE(excluded.ticket_created_date, ticket_index.ticket_created_date),
            subject_sanitized  = COALESCE(excluded.subject_sanitized,  ticket_index.subject_sanitized),
            csat_score         = COALESCE(excluded.csat_score,         ticket_index.csat_score)
        """,
        (
            ticket_id,
            _INGESTION_SENTINEL_SCAN_ID,
            _INGESTION_SENTINEL_SCAN_ID,
            now,
            meta.get("created_at") or meta.get("ticket_created_date"),
            meta.get("trc") or meta.get("trc_code"),
            meta.get("trc_label"),
            meta.get("subject_sanitized") or meta.get("subject"),
            meta.get("csat") or meta.get("csat_score"),
            meta.get("message_count"),
            meta.get("resolution_hours"),
            meta.get("dataset_id"),
            enrich["insurance_payer"],
            enrich["client_id"],
            enrich["provider_id"],
            enrich["agent_id"],
            enrich["service_state"],
            enrich["channel"],
            enrich["session_date"],
            enrich["dispute_amount_usd"],
        ),
    )

    upsert_entity_registries(ticket_id, meta, conn)
    if "tags" in meta and meta["tags"]:
        write_ticket_tags(ticket_id, meta["tags"], conn, source="ingestion")


def upsert_entity_registries(ticket_id: str, meta: dict, conn) -> None:
    """Upsert insurance_payers / clients / providers / agents on first encounter.

    Uses INSERT OR IGNORE to avoid clobbering existing rows. ticket_count is
    NOT incremented here — that's a batched recompute (see
    scripts/backfill_enrichment.py or a later maintenance task) to avoid
    serializing on a hot row during parallel ingestion.

    Args:
        ticket_id: Ticket this encounter belongs to; stored as
            first_seen_ticket_id only when the registry row is newly created.
        meta: Dict containing any of ENRICHMENT_META_KEYS.
        conn: SQLite connection (caller manages transaction).
    """
    fields = _normalize_enrichment(meta)

    payer = fields["insurance_payer"]
    if payer:
        conn.execute(
            "INSERT OR IGNORE INTO insurance_payers (payer_id, aliases_json) "
            "VALUES (?, ?)",
            (payer, json.dumps([])),
        )

    client_id = fields["client_id"]
    if client_id:
        conn.execute(
            "INSERT OR IGNORE INTO clients (client_id, first_seen_ticket_id) "
            "VALUES (?, ?)",
            (client_id, ticket_id),
        )

    provider_id = fields["provider_id"]
    if provider_id:
        conn.execute(
            "INSERT OR IGNORE INTO providers (provider_id, first_seen_ticket_id) "
            "VALUES (?, ?)",
            (provider_id, ticket_id),
        )

    agent_id = fields["agent_id"]
    if agent_id:
        conn.execute(
            "INSERT OR IGNORE INTO agents (agent_id, first_seen_ticket_id) "
            "VALUES (?, ?)",
            (agent_id, ticket_id),
        )


def write_ticket_tags(
    ticket_id: str,
    tags: Iterable[str] | str | None,
    conn,
    *,
    source: str = "ingestion",
) -> int:
    """Write M:N tag rows into ticket_tags. Idempotent via INSERT OR IGNORE.

    Args:
        ticket_id: Ticket to tag.
        tags: Either a list/tuple of tag strings, a JSON array string, or a
            semicolon-delimited string. None or empty → no-op.
        conn: SQLite connection.
        source: Provenance. 'ingestion' (default), 'manual', or 'inferred'.

    Returns:
        Count of rows inserted (after dedup).

    Raises:
        ValueError: if `source` is not one of the allowed values.
    """
    if source not in {"ingestion", "manual", "inferred"}:
        raise ValueError(f"invalid tag source {source!r}")

    normalized = _split_tags(tags)
    if not normalized:
        return 0

    inserted = 0
    for tag in normalized:
        cur = conn.execute(
            "INSERT OR IGNORE INTO ticket_tags (ticket_id, tag, source) "
            "VALUES (?, ?, ?)",
            (ticket_id, tag, source),
        )
        inserted += cur.rowcount or 0
    return inserted


def _split_tags(raw: Iterable[str] | str | None) -> list[str]:
    """Normalize tags input to a de-duped, lowercased list of non-empty strings.

    Accepts: list/tuple, JSON array string, semicolon or comma-delimited string.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    items = parsed
                else:
                    items = [s]
            except json.JSONDecodeError:
                items = s.replace(",", ";").split(";")
        else:
            items = s.replace(",", ";").split(";")
    else:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for t in items:
        if not isinstance(t, str):
            continue
        norm = t.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def upsert_ticket_index(
    ticket_id: str,
    scan_id: str,
    classification: dict,
    ticket_meta: dict | None = None,
    conn=None,
):
    """Insert or update the ticket_index row from a fresh classification.

    Also upserts entity registries (insurance_payers / clients / providers /
    agents) and, if `ticket_meta['tags']` is present, writes ticket_tags rows
    with source='ingestion'. Caller is responsible for the transaction.

    Args:
        ticket_id:      Ticket identifier.
        scan_id:        Current NLP scan ID.
        classification: Dict from Gemini classification output.
        ticket_meta:    Optional dict with ticket-level metadata. Recognized
            keys: created_at, trc, trc_label, subject, csat, message_count,
            resolution_hours, dataset_id, plus all of ENRICHMENT_META_KEYS
            (insurance_payer, client_id, provider_id, agent_id, service_state,
            channel, session_date, dispute_amount_usd), plus 'tags'.
        conn:           SQLite connection.

    Raises:
        TypeError: if conn is None.
    """
    if conn is None:
        raise TypeError("upsert_ticket_index requires conn")

    meta = ticket_meta or {}
    now = datetime.utcnow().isoformat()

    key_phrases = classification.get("key_phrases")
    if isinstance(key_phrases, list):
        key_phrases = ", ".join(key_phrases)

    entities_raw = classification.get("entities") or classification.get("entities_json")
    if isinstance(entities_raw, (dict, list)):
        entities_raw = json.dumps(entities_raw)
    entities_safe = _filter_non_pii_entities(entities_raw)

    enrich = _normalize_enrichment(meta)

    conn.execute(
        """
        INSERT INTO ticket_index (
            ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
            ticket_created_date, trc_code, trc_label, subject_sanitized,
            issue_snippet, friction_type, sub_pattern, sub_pattern_id,
            sentiment_polarity, sentiment_intensity, anomaly_flag, anomaly_reason,
            root_cause_hint, csat_score, message_count, resolution_hours,
            classification_confidence, classification_method, entities_json,
            key_phrases, is_novel, dataset_id,
            insurance_payer, client_id, provider_id, agent_id,
            service_state, channel, session_date, dispute_amount_usd
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        ON CONFLICT(ticket_id) DO UPDATE SET
            last_seen_scan_id       = excluded.last_seen_scan_id,
            friction_type           = excluded.friction_type,
            sub_pattern             = excluded.sub_pattern,
            sub_pattern_id          = excluded.sub_pattern_id,
            sentiment_polarity      = excluded.sentiment_polarity,
            sentiment_intensity     = excluded.sentiment_intensity,
            anomaly_flag            = excluded.anomaly_flag,
            anomaly_reason          = excluded.anomaly_reason,
            root_cause_hint         = excluded.root_cause_hint,
            issue_snippet           = excluded.issue_snippet,
            classification_confidence = excluded.classification_confidence,
            classification_method   = excluded.classification_method,
            entities_json           = excluded.entities_json,
            key_phrases             = excluded.key_phrases,
            is_novel                = excluded.is_novel,
            insurance_payer         = COALESCE(excluded.insurance_payer, ticket_index.insurance_payer),
            client_id               = COALESCE(excluded.client_id,       ticket_index.client_id),
            provider_id             = COALESCE(excluded.provider_id,     ticket_index.provider_id),
            agent_id                = COALESCE(excluded.agent_id,        ticket_index.agent_id),
            service_state           = COALESCE(excluded.service_state,   ticket_index.service_state),
            channel                 = COALESCE(excluded.channel,         ticket_index.channel),
            session_date            = COALESCE(excluded.session_date,    ticket_index.session_date),
            dispute_amount_usd      = COALESCE(excluded.dispute_amount_usd, ticket_index.dispute_amount_usd)
        """,
        (
            ticket_id,
            scan_id,                                              # first_seen_scan_id
            scan_id,                                              # last_seen_scan_id
            now,                                                  # first_seen_date
            meta.get("created_at") or meta.get("ticket_created_date"),
            meta.get("trc") or meta.get("trc_code"),
            meta.get("trc_label"),
            meta.get("subject_sanitized") or meta.get("subject"),
            classification.get("summary") or classification.get("issue_snippet"),
            classification.get("friction_type"),
            classification.get("sub_cluster") or classification.get("sub_pattern"),
            classification.get("sub_pattern_id"),
            classification.get("sentiment_polarity"),
            classification.get("sentiment_intensity"),
            classification.get("anomaly_flag"),
            classification.get("anomaly_reason"),
            classification.get("root_cause_hint"),
            meta.get("csat") or meta.get("csat_score"),
            meta.get("message_count"),
            meta.get("resolution_hours"),
            classification.get("sub_cluster_confidence")
            or classification.get("classification_confidence"),
            classification.get("classification_method", "llm"),
            entities_safe,
            key_phrases,
            1 if classification.get("is_novel") else 0,
            meta.get("dataset_id"),
            enrich["insurance_payer"],
            enrich["client_id"],
            enrich["provider_id"],
            enrich["agent_id"],
            enrich["service_state"],
            enrich["channel"],
            enrich["session_date"],
            enrich["dispute_amount_usd"],
        ),
    )

    # Entity registries + tags share the caller's transaction.
    upsert_entity_registries(ticket_id, meta, conn)
    if "tags" in meta and meta["tags"]:
        write_ticket_tags(ticket_id, meta["tags"], conn, source="ingestion")

    logger.debug("Upserted ticket_index: %s (scan=%s)", ticket_id, scan_id)
