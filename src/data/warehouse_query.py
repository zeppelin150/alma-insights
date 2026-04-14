"""
Alma Insights — Warehouse Query Layer
Unified query interface across all per-source tables.
Returns data in the same shape as the old `SELECT * FROM conversations` queries
to maintain backward compatibility with analytics engines.
"""

import logging

logger = logging.getLogger("alma.warehouse_query")


class WarehouseQuery:
    """Unified query interface across all registered data sources.

    Falls back to legacy shared tables (tickets, conversations, comments)
    when the source_registry table doesn't exist yet (pre-migration DBs).
    """

    def __init__(self, conn, source_registry):
        self.conn = conn
        self.registry = source_registry
        self._legacy_mode = not self._has_source_registry()

    def _has_source_registry(self) -> bool:
        """Check if source_registry table exists AND per-source tables have data.

        Falls back to legacy mode if per-source tables exist but are empty
        while the shared tables have data (test fixtures, pre-migration state).
        """
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM source_registry").fetchone()
            if not row or row[0] == 0:
                return False
            # Check if any per-source table actually has data
            sources = self.conn.execute("SELECT table_prefix FROM source_registry").fetchall()
            for src in sources:
                prefix = src[0]
                try:
                    cnt = self.conn.execute(f"SELECT COUNT(*) FROM [{prefix}_tickets]").fetchone()
                    if cnt and cnt[0] > 0:
                        return True
                    cnt2 = self.conn.execute(f"SELECT COUNT(*) FROM [{prefix}_conversations]").fetchone()
                    if cnt2 and cnt2[0] > 0:
                        return True
                except Exception:
                    pass
            # Per-source tables exist but are empty — check if shared tables have data
            try:
                shared = self.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()
                if shared and shared[0] > 0:
                    return False  # Use legacy mode — data is in shared tables
            except Exception:
                pass
            try:
                shared_t = self.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()
                if shared_t and shared_t[0] > 0:
                    return False  # Use legacy mode
            except Exception:
                pass
            return True  # Empty DB with source_registry — use new mode
        except Exception:
            return False

    def _get_tables(self, table_type: str, source_id=None) -> list[str]:
        """Get table names, with fallback to legacy shared tables."""
        if self._legacy_mode:
            # Map to old shared table names
            legacy = {"tickets": "tickets", "conversations": "conversations",
                      "comments": "comments", "fts": "conversations_fts"}
            return [legacy.get(table_type, table_type)]

        if source_id:
            return [self.registry.get_table_name(source_id, table_type)]
        return self.registry.get_all_table_names(table_type)

    def get_conversations(self, source_id=None, date_start=None, date_end=None,
                          trc_filter=None, provider_id=None, client_id=None,
                          limit=None):
        """Query conversations from one or all sources.

        Returns list of dicts with same column names as old `conversations` table:
            ticket_id, subject, trc_code, trc_label, status, csat_score,
            created_at, solved_at, message_count, client_messages,
            agent_messages, full_thread, thread_preview, dataset_id

        Args:
            source_id: Query specific source, or None for all sources
            date_start: ISO date string for created_at >= filter
            date_end: ISO date string for created_at <= filter
            trc_filter: TRC code or list of TRC codes to filter
            provider_id: Filter by provider (joins to tickets table)
            client_id: Filter by client (joins to tickets table)
            limit: Max rows to return
        """
        tables = self._get_tables("conversations", source_id)

        if not tables:
            return []

        all_rows = []
        for table in tables:
            rows = self._query_table(table, date_start, date_end, trc_filter, limit)
            all_rows.extend(rows)

        if limit and len(all_rows) > limit:
            all_rows = all_rows[:limit]

        return all_rows

    def get_ticket_count(self, source_id=None):
        """Total ticket count across one or all sources."""
        tables = self._get_tables("tickets", source_id)

        total = 0
        for table in tables:
            try:
                row = self.conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()  # noqa: S608
                total += row[0] if row else 0
            except Exception:
                pass  # Table may not exist yet
        return total

    def get_trc_distribution(self, source_id=None, date_start=None, date_end=None):
        """TRC code -> count mapping for analytics."""
        tables = self._get_tables("conversations", source_id)

        distribution = {}
        for table in tables:
            where, params = self._build_where(date_start, date_end, None)
            sql = f"SELECT trc_code, COUNT(*) FROM [{table}] {where} GROUP BY trc_code"  # noqa: S608
            try:
                for row in self.conn.execute(sql, params).fetchall():
                    code = row[0] or "Unknown"
                    distribution[code] = distribution.get(code, 0) + row[1]
            except Exception:
                pass
        return distribution

    def get_full_threads(self, ticket_ids: list, source_id=None):
        """Fetch full_thread for specific ticket_ids."""
        if not ticket_ids:
            return {}

        tables = self._get_tables("conversations", source_id)

        result = {}
        placeholders = ",".join("?" * len(ticket_ids))
        for table in tables:
            try:
                rows = self.conn.execute(
                    f"SELECT ticket_id, full_thread FROM [{table}] WHERE ticket_id IN ({placeholders})",  # noqa: S608
                    ticket_ids,
                ).fetchall()
                for r in rows:
                    result[r[0]] = r[1]
            except Exception:
                pass
        return result

    def search_fts(self, query: str, source_id=None, limit=50):
        """Full-text search across one or all source FTS tables."""
        if not query or not query.strip():
            return []

        tables = self._get_tables("fts", source_id)

        results = []
        for table in tables:
            try:
                rows = self.conn.execute(
                    f"SELECT ticket_id, subject, trc_label, snippet({table}, 3, '<b>', '</b>', '...', 32) "
                    f"FROM [{table}] WHERE [{table}] MATCH ? LIMIT ?",
                    (query, limit),
                ).fetchall()
                for r in rows:
                    results.append({
                        "ticket_id": r[0],
                        "subject": r[1],
                        "trc_label": r[2],
                        "snippet": r[3],
                    })
            except Exception:
                pass

        return results[:limit]

    # ── Paginated Access (Session 4: Data Warehouse Page) ──

    def get_conversations_paged(self, offset=0, limit=100, source_id=None,
                                date_start=None, date_end=None, trc_filter=None,
                                provider_id=None, client_id=None, keyword=None,
                                insurance_payer=None, service_state=None, tag=None,
                                agent_id=None):
        """Fetch a page of conversations for virtual scroll.

        Returns (rows: list[dict], total_count: int).
        rows contain: ticket_id, subject, trc_code, trc_label, status, csat_score,
                      created_at, solved_at, message_count, source_name
        """
        tables = self._get_tables("conversations", source_id)
        if not tables:
            return [], 0

        # Build source_name lookup
        source_names = {}
        if not self._legacy_mode:
            try:
                for src in self.conn.execute(
                    "SELECT source_id, source_name, table_prefix FROM source_registry"
                ).fetchall():
                    source_names[src[2]] = src[1]
            except Exception:
                pass

        columns = [
            "ticket_id", "subject", "trc_code", "trc_label", "status",
            "csat_score", "created_at", "solved_at", "message_count",
        ]

        all_rows = []
        total_count = 0

        for table in tables:
            conditions, params = self._build_where_extended(
                date_start, date_end, trc_filter, provider_id, client_id, keyword, table,
                insurance_payer=insurance_payer, service_state=service_state,
                tag=tag, agent_id=agent_id,
            )
            where = "WHERE " + " AND ".join(conditions) if conditions else ""

            # Count
            try:
                cnt = self.conn.execute(
                    f"SELECT COUNT(*) FROM [{table}] {where}", params
                ).fetchone()
                total_count += cnt[0] if cnt else 0
            except Exception:
                continue

            # Determine source name for this table
            sname = "Unknown"
            for prefix, name in source_names.items():
                if table.startswith(prefix):
                    sname = name
                    break
            if self._legacy_mode:
                sname = "Default"

            # Fetch rows (we fetch all from each table, then slice globally)
            try:
                sql = (
                    f"SELECT {', '.join(columns)} FROM [{table}] {where} "
                    f"ORDER BY created_at DESC"
                )
                rows = self.conn.execute(sql, params).fetchall()
                for row in rows:
                    d = dict(zip(columns, row))
                    d["source_name"] = sname
                    all_rows.append(d)
            except Exception as e:
                logger.warning("Paged query failed for %s: %s", table, e)

        # Sort combined results by created_at descending
        all_rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)

        # Slice for pagination
        page = all_rows[offset:offset + limit]
        return page, total_count

    def get_trc_history(self, trc_code, source_id=None, days=90):
        """Get TRC history data for the TRC history panel.

        Returns dict with:
            volume_by_day: list of (date, count) tuples
            top_issues: list of (issue_type, count) tuples (from trc_label breakdown)
            total: int
            avg_csat: float or None
            related_trcs: list of (trc_code, co_occurrence_pct) tuples
        """
        if not trc_code:
            return {"volume_by_day": [], "top_issues": [], "total": 0,
                    "avg_csat": None, "related_trcs": []}

        tables = self._get_tables("conversations", source_id)
        if not tables:
            return {"volume_by_day": [], "top_issues": [], "total": 0,
                    "avg_csat": None, "related_trcs": []}

        volume = {}
        total = 0
        csat_sum = 0.0
        csat_count = 0
        issue_counts = {}
        # For co-occurrence: track which ticket_ids have this TRC
        trc_ticket_ids = set()
        # All TRC codes seen on those tickets' neighbors
        neighbor_trcs = {}

        for table in tables:
            try:
                # Volume by day
                rows = self.conn.execute(
                    f"SELECT SUBSTR(created_at, 1, 10) as d, COUNT(*) "
                    f"FROM [{table}] WHERE trc_code = ? "
                    f"AND created_at >= date('now', '-{int(days)} days') "
                    f"GROUP BY d ORDER BY d",
                    (trc_code,),
                ).fetchall()
                for r in rows:
                    volume[r[0]] = volume.get(r[0], 0) + r[1]
                    total += r[1]
            except Exception:
                pass

            try:
                # Average CSAT
                row = self.conn.execute(
                    f"SELECT AVG(csat_score), COUNT(*) FROM [{table}] "
                    f"WHERE trc_code = ? AND csat_score IS NOT NULL "
                    f"AND created_at >= date('now', '-{int(days)} days')",
                    (trc_code,),
                ).fetchone()
                if row and row[1] > 0:
                    csat_sum += (row[0] or 0) * row[1]
                    csat_count += row[1]
            except Exception:
                pass

            try:
                # Issue type breakdown (using trc_label as proxy)
                rows = self.conn.execute(
                    f"SELECT trc_label, COUNT(*) FROM [{table}] "
                    f"WHERE trc_code = ? AND trc_label IS NOT NULL "
                    f"AND created_at >= date('now', '-{int(days)} days') "
                    f"GROUP BY trc_label ORDER BY COUNT(*) DESC LIMIT 10",
                    (trc_code,),
                ).fetchall()
                for r in rows:
                    issue_counts[r[0]] = issue_counts.get(r[0], 0) + r[1]
            except Exception:
                pass

            try:
                # Collect ticket_ids for co-occurrence
                rows = self.conn.execute(
                    f"SELECT ticket_id FROM [{table}] WHERE trc_code = ? "
                    f"AND created_at >= date('now', '-{int(days)} days')",
                    (trc_code,),
                ).fetchall()
                for r in rows:
                    trc_ticket_ids.add(r[0])
            except Exception:
                pass

        # Co-occurrence: find other TRC codes on same client tickets
        # (simplified: look for other TRCs in the same date range)
        if trc_ticket_ids and total > 0:
            for table in tables:
                try:
                    placeholders = ",".join("?" * len(trc_ticket_ids))
                    rows = self.conn.execute(
                        f"SELECT trc_code, COUNT(*) FROM [{table}] "
                        f"WHERE ticket_id IN ({placeholders}) AND trc_code != ? "
                        f"GROUP BY trc_code ORDER BY COUNT(*) DESC LIMIT 5",
                        list(trc_ticket_ids) + [trc_code],
                    ).fetchall()
                    for r in rows:
                        neighbor_trcs[r[0]] = neighbor_trcs.get(r[0], 0) + r[1]
                except Exception:
                    pass

        volume_by_day = sorted(volume.items())
        top_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        avg_csat = round(csat_sum / csat_count, 2) if csat_count > 0 else None

        related_trcs = []
        if total > 0:
            for code, cnt in sorted(neighbor_trcs.items(), key=lambda x: x[1], reverse=True)[:5]:
                pct = round(cnt / total * 100)
                if pct > 0:
                    related_trcs.append((code, pct))

        return {
            "volume_by_day": volume_by_day,
            "top_issues": top_issues,
            "total": total,
            "avg_csat": avg_csat,
            "related_trcs": related_trcs,
        }

    def _build_where_extended(self, date_start=None, date_end=None, trc_filter=None,
                              provider_id=None, client_id=None, keyword=None,
                              table=None, *,
                              insurance_payer=None, service_state=None,
                              tag=None, agent_id=None):
        """Build WHERE conditions for paginated queries with extended filters.

        Phase 1 enrichment filters (insurance_payer, service_state, tag,
        agent_id, provider_id, client_id) piggy-back on ticket_index via
        EXISTS subqueries keyed on ticket_id. This keeps the main SELECT's
        shape unchanged and lets the existing per-source table routing keep
        working. Subqueries are cheap thanks to the new indexes from
        migration 016.
        """
        conditions = []
        params = []

        if date_start:
            conditions.append("created_at >= ?")
            params.append(date_start)
        if date_end:
            conditions.append("created_at <= ?")
            params.append(date_end)
        if trc_filter:
            if isinstance(trc_filter, list):
                placeholders = ",".join("?" * len(trc_filter))
                conditions.append(f"trc_code IN ({placeholders})")
                params.extend(trc_filter)
            else:
                conditions.append("trc_code = ?")
                params.append(trc_filter)
        if keyword:
            conditions.append("(subject LIKE ? OR thread_preview LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw])

        # Enrichment filters against ticket_index (Phase 1). EXISTS avoids
        # changing the SELECT list; ticket_id is the shared join key.
        if insurance_payer:
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_index ti WHERE ti.ticket_id = [{table}].ticket_id "
                f"AND ti.insurance_payer = ?)"
            )
            params.append(insurance_payer)
        if service_state:
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_index ti WHERE ti.ticket_id = [{table}].ticket_id "
                f"AND ti.service_state = ?)"
            )
            params.append(service_state)
        if agent_id:
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_index ti WHERE ti.ticket_id = [{table}].ticket_id "
                f"AND ti.agent_id = ?)"
            )
            params.append(agent_id)
        if provider_id:
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_index ti WHERE ti.ticket_id = [{table}].ticket_id "
                f"AND ti.provider_id = ?)"
            )
            params.append(provider_id)
        if client_id:
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_index ti WHERE ti.ticket_id = [{table}].ticket_id "
                f"AND ti.client_id = ?)"
            )
            params.append(client_id)
        if tag:
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_tags tt WHERE tt.ticket_id = [{table}].ticket_id "
                f"AND tt.tag = ?)"
            )
            params.append(tag)

        return conditions, params

    # ── General-Purpose Table Routing ─────────────────────

    def query_conversations_raw(self, sql_template: str, params=None,
                                source_id=None) -> list:
        """Execute a raw SQL query routed to per-source conversation tables.

        sql_template uses {table} as placeholder for the conversation table name.
        E.g., "SELECT thread_preview FROM {table} WHERE ticket_id IN (?,?)"

        Returns combined results across all sources (or one source).
        """
        return self._query_routed(sql_template, "conversations", params, source_id)

    def query_tickets_raw(self, sql_template: str, params=None,
                          source_id=None) -> list:
        """Same as query_conversations_raw but routes to tickets tables."""
        return self._query_routed(sql_template, "tickets", params, source_id)

    def query_comments_raw(self, sql_template: str, params=None,
                           source_id=None) -> list:
        """Same as query_conversations_raw but routes to comments tables."""
        return self._query_routed(sql_template, "comments", params, source_id)

    def query_fts_raw(self, sql_template: str, params=None,
                      source_id=None) -> list:
        """Same as query_conversations_raw but routes to FTS tables."""
        return self._query_routed(sql_template, "fts", params, source_id)

    def _query_routed(self, sql_template: str, table_type: str,
                      params=None, source_id=None) -> list:
        """Route a SQL template to per-source tables and union results."""
        tables = self._get_tables(table_type, source_id)

        if not tables:
            return []

        all_rows = []
        for table in tables:
            sql = sql_template.replace("{table}", f"[{table}]")
            try:
                rows = self.conn.execute(sql, params or []).fetchall()
                all_rows.extend(rows)
            except Exception as e:
                logger.warning("Routed query failed for %s: %s", table, e)
        return all_rows

    # ── Internal ──────────────────────────────────────────

    def _query_table(self, table: str, date_start=None, date_end=None,
                     trc_filter=None, limit=None):
        """Query a single conversations table and return list of dicts."""
        where, params = self._build_where(date_start, date_end, trc_filter)

        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        sql = (
            f"SELECT ticket_id, subject, trc_code, trc_label, status, csat_score, "
            f"created_at, solved_at, message_count, client_messages, agent_messages, "
            f"full_thread, thread_preview, dataset_id "
            f"FROM [{table}] {where} ORDER BY created_at DESC {limit_clause}"
        )

        try:
            rows = self.conn.execute(sql, params).fetchall()
        except Exception as e:
            logger.warning("Query failed for table %s: %s", table, e)
            return []

        columns = [
            "ticket_id", "subject", "trc_code", "trc_label", "status", "csat_score",
            "created_at", "solved_at", "message_count", "client_messages",
            "agent_messages", "full_thread", "thread_preview", "dataset_id",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def _build_where(self, date_start=None, date_end=None, trc_filter=None):
        """Build WHERE clause and params for date/TRC filtering."""
        conditions = []
        params = []

        if date_start:
            conditions.append("created_at >= ?")
            params.append(date_start)
        if date_end:
            conditions.append("created_at <= ?")
            params.append(date_end)
        if trc_filter:
            if isinstance(trc_filter, list):
                placeholders = ",".join("?" * len(trc_filter))
                conditions.append(f"trc_code IN ({placeholders})")
                params.extend(trc_filter)
            else:
                conditions.append("trc_code = ?")
                params.append(trc_filter)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        return where, params
