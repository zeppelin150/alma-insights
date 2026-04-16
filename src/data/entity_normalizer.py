"""Extract and normalize entities from NLP classifications.

Solves two problems:
1. entities_json is not indexable (requires json_extract)
2. Case inconsistency ("Billing" vs "billing" = tickets split)
"""

import json
import logging

logger = logging.getLogger("alma.entity_normalizer")

# Canonical case mappings for known entity values.
# Keys are lowercase; values are the canonical form.
CASE_NORMALIZATION: dict[str, dict[str, str]] = {
    "product_area": {
        "billing": "Billing",
        "billing & refunds": "Billing",
        "claims": "Claims",
        "eligibility": "Eligibility",
        "enrollment": "Enrollment",
        "provider": "Provider",
        "pharmacy": "Pharmacy",
        "appeals": "Appeals",
        "authorizations": "Authorizations",
    },
}


def _normalize_value(entity_type: str, raw_value: str) -> str:
    """Case-normalize an entity value using canonical mappings."""
    canonical = CASE_NORMALIZATION.get(entity_type, {})
    return canonical.get(raw_value.lower(), raw_value.title())


def normalize_entities_for_scan(conn, scan_id: str) -> int:
    """Extract entities_json -> ticket_entities_normalized for a scan.

    Reads from nlp_ticket_classifications WHERE scan_id = ?,
    parses entities_json, case-normalizes, writes to normalized table.
    Returns count of entity rows written.
    """
    rows = conn.execute(
        "SELECT ticket_id, entities_json FROM nlp_ticket_classifications "
        "WHERE scan_id = ? AND entities_json IS NOT NULL",
        (scan_id,),
    ).fetchall()

    count = 0
    for row in rows:
        ticket_id = row[0]
        try:
            entities = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            if not isinstance(entities, dict):
                continue
        except (json.JSONDecodeError, TypeError):
            continue

        for entity_type, raw_value in entities.items():
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            normalized = _normalize_value(entity_type, raw_value.strip())
            conn.execute(
                "INSERT OR REPLACE INTO ticket_entities_normalized "
                "(ticket_id, entity_type, entity_value, scan_id) "
                "VALUES (?, ?, ?, ?)",
                (ticket_id, entity_type, normalized, scan_id),
            )
            count += 1

    conn.commit()
    logger.info("Normalized %d entity rows for scan %s", count, scan_id[:8])
    return count


def query_by_entity(
    conn, entity_type: str, entity_value: str,
) -> list[str]:
    """Fast indexed entity lookup. Returns ticket_ids.

    Case-insensitive: normalizes the query value before lookup.
    """
    normalized = _normalize_value(entity_type, entity_value)
    rows = conn.execute(
        "SELECT ticket_id FROM ticket_entities_normalized "
        "WHERE entity_type = ? AND entity_value = ?",
        (entity_type, normalized),
    ).fetchall()
    return [r[0] for r in rows]


def get_entity_distribution(
    conn, entity_type: str,
) -> list[tuple[str, int]]:
    """Return (value, count) pairs sorted by count desc."""
    rows = conn.execute(
        "SELECT entity_value, COUNT(*) as cnt "
        "FROM ticket_entities_normalized "
        "WHERE entity_type = ? "
        "GROUP BY entity_value ORDER BY cnt DESC",
        (entity_type,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]
