"""Post-scan data integrity verification.

Detects silent data inconsistencies that would otherwise
surface as wrong results in chat, search, or reports.
"""

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger("alma.integrity")


@dataclass
class IntegrityIssue:
    """Single data integrity finding from check_data_integrity().

    Attributes:
        severity: "error" (blocking) or "warning" (degraded).
        check_name: Short identifier like "embedding_freshness".
        message: Human-readable description of the issue.
        expected: Expected value or count.
        actual: Actual observed value or count.
    """

    severity: str          # "error" | "warning"
    check_name: str        # e.g. "embedding_freshness"
    message: str           # human-readable description
    expected: int | str
    actual: int | str


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _count(conn, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def check_data_integrity(conn) -> list[IntegrityIssue]:
    """Run all integrity checks. Returns list of issues found.

    Checks performed:
    1. conversations vs ticket_index count divergence
    2. ticket_index vs ticket_embeddings count divergence
    3. embedding freshness (created_at vs latest scan)
    4. FTS5 row count vs conversations count
    5. orphaned nlp_ticket_classifications (not in ticket_index)
    6. entity case consistency (top N values per entity_type)
    """
    issues = []

    # 1. conversations vs ticket_index
    conv_count = _count(conn, "conversations")
    idx_count = _count(conn, "ticket_index")
    if idx_count == 0 and conv_count > 0:
        issues.append(IntegrityIssue(
            severity="error",
            check_name="ticket_index_empty",
            message="ticket_index is empty but conversations has data",
            expected=f">0", actual=0,
        ))
    elif idx_count == 0:
        issues.append(IntegrityIssue(
            severity="error",
            check_name="ticket_index_empty",
            message="ticket_index is empty — no classified data available",
            expected=">0", actual=0,
        ))

    # 2. ticket_index vs ticket_embeddings
    embed_count = _count(conn, "ticket_embeddings")
    if idx_count > 0 and abs(idx_count - embed_count) > 2:
        issues.append(IntegrityIssue(
            severity="warning",
            check_name="embedding_count_divergence",
            message=(
                f"ticket_index ({idx_count}) vs ticket_embeddings ({embed_count}) "
                f"diverge by {abs(idx_count - embed_count)}"
            ),
            expected=idx_count, actual=embed_count,
        ))

    # 3. embedding freshness
    if _table_exists(conn, "ticket_embeddings") and _table_exists(conn, "nlp_scan_runs"):
        latest_scan = conn.execute(
            "SELECT MAX(started_at) FROM nlp_scan_runs WHERE status = 'complete'"
        ).fetchone()
        latest_embed = conn.execute(
            "SELECT MAX(created_at) FROM ticket_embeddings"
        ).fetchone()

        if latest_scan and latest_scan[0] and latest_embed and latest_embed[0]:
            scan_ts = latest_scan[0][:19]
            embed_ts = latest_embed[0][:19]
            if scan_ts > embed_ts:
                issues.append(IntegrityIssue(
                    severity="warning",
                    check_name="embedding_freshness",
                    message=(
                        f"Embeddings ({embed_ts}) are older than "
                        f"latest scan ({scan_ts})"
                    ),
                    expected=scan_ts, actual=embed_ts,
                ))

    # 4. FTS5 vs conversations
    fts_count = _count(conn, "conversations_fts")
    if conv_count > 0 and abs(conv_count - fts_count) > 2:
        issues.append(IntegrityIssue(
            severity="warning",
            check_name="fts_count_mismatch",
            message=(
                f"conversations ({conv_count}) vs conversations_fts ({fts_count}) "
                f"diverge by {abs(conv_count - fts_count)}"
            ),
            expected=conv_count, actual=fts_count,
        ))

    # 5. orphaned nlp_ticket_classifications
    if _table_exists(conn, "nlp_ticket_classifications") and idx_count > 0:
        orphan_count = conn.execute(
            "SELECT COUNT(*) FROM nlp_ticket_classifications c "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM ticket_index t WHERE t.ticket_id = c.ticket_id"
            ")"
        ).fetchone()[0]
        if orphan_count > 0:
            issues.append(IntegrityIssue(
                severity="warning",
                check_name="orphaned_classifications",
                message=f"{orphan_count} classifications have no matching ticket_index row",
                expected=0, actual=orphan_count,
            ))

    # 6. entity case consistency
    if _table_exists(conn, "nlp_ticket_classifications"):
        try:
            rows = conn.execute(
                "SELECT entities_json FROM nlp_ticket_classifications "
                "WHERE entities_json IS NOT NULL LIMIT 500"
            ).fetchall()

            import json
            entity_values: dict[str, dict[str, int]] = {}
            for row in rows:
                try:
                    entities = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    if not isinstance(entities, dict):
                        continue
                    for etype, evalue in entities.items():
                        if not isinstance(evalue, str):
                            continue
                        entity_values.setdefault(etype, {})
                        lower = evalue.lower()
                        entity_values[etype].setdefault(lower, set())
                        entity_values[etype][lower].add(evalue)
                except (json.JSONDecodeError, TypeError):
                    continue

            for etype, lower_map in entity_values.items():
                for lower_val, variants in lower_map.items():
                    if len(variants) > 1:
                        issues.append(IntegrityIssue(
                            severity="warning",
                            check_name="entity_case_inconsistency",
                            message=(
                                f"Entity '{etype}' has case variants: "
                                f"{sorted(variants)}"
                            ),
                            expected=1, actual=len(variants),
                        ))
        except Exception as e:
            logger.debug("Entity case check skipped: %s", e)

    return issues


def run_post_scan_integrity(conn, scan_id: str) -> dict:
    """Called after scan completes. Returns pass/fail + issues.

    Emits warnings to logger. Does NOT block scan completion.
    Returns: {"passed": bool, "issues": [...], "checked_at": str}
    """
    issues = check_data_integrity(conn)

    for issue in issues:
        if issue.severity == "error":
            logger.error("Integrity [%s]: %s", issue.check_name, issue.message)
        else:
            logger.warning("Integrity [%s]: %s", issue.check_name, issue.message)

    return {
        "passed": not any(i.severity == "error" for i in issues),
        "issues": [asdict(i) for i in issues],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scan_id": scan_id,
    }
