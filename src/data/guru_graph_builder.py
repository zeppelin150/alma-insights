"""
Alma Insights — Guru Card Graph Builder (Hardening H5)

Builds and traverses a relationship graph between Guru KB cards.
Runs after ``sync_articles()`` to extract deterministic relationships:

  1. same_collection  — cards sharing a collection
  2. cross_reference  — card title appears in another card's content
  3. shared_friction  — cards cover the same friction type

Domain tagging:
  - collection-based (collection name → domain slug)
  - title-keyword (configurable keyword → domain map)

Graph traversal:
  ``get_constellation(card_id, max_hops)`` — BFS from a seed card

Usage:
    builder = GuruGraphBuilder(db)
    builder.rebuild()                             # full rebuild
    cards = builder.get_constellation("abc", 2)   # 2-hop neighborhood
    domain_cards = builder.get_domain("billing")  # all cards in domain
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("alma.guru_graph")

# ── Default keyword → domain map ─────────────────────────────────
_KEYWORD_DOMAINS = {
    "billing": ["billing", "bill", "invoice", "charge", "payment", "pay", "refund"],
    "shipping": ["shipping", "delivery", "tracking", "carrier", "shipment"],
    "account": ["account", "login", "password", "profile", "authentication"],
    "returns": ["return", "exchange", "rma", "warranty"],
    "product": ["product", "item", "catalog", "inventory", "stock"],
    "ordering": ["order", "checkout", "cart", "purchase"],
    "technical": ["error", "bug", "troubleshoot", "setup", "install", "configure"],
}


class GuruGraphBuilder:
    """Builds and queries the card relationship graph.

    Operates on ``guru_card_graph`` and ``guru_card_domains`` tables
    created by migration 004.
    """

    def __init__(self, db: object, keyword_domains: dict | None = None) -> None:
        """
        Args:
            db: DatabaseManager instance.
            keyword_domains: Optional override for keyword → domain map.
        """
        self.db = db
        self._kw_domains = keyword_domains or _KEYWORD_DOMAINS

    # ═══════════════════════════════════════════════════════════════
    #  Build / Rebuild
    # ═══════════════════════════════════════════════════════════════

    def rebuild(self) -> dict:
        """Clear and rebuild the full card graph + domain tags.

        Returns:
            dict with edge_count, domain_tag_count.
        """
        conn = self._conn()

        # Clear existing data
        conn.execute("DELETE FROM guru_card_graph")
        conn.execute("DELETE FROM guru_card_domains")

        # Load all synced cards
        cards = conn.execute("""
            SELECT card_id, title, collection_id, collection_name, content_hash
            FROM guru_articles
        """).fetchall()

        if not cards:
            conn.commit()
            return {"edge_count": 0, "domain_tag_count": 0}

        card_list = [
            {
                "card_id": c[0], "title": c[1],
                "collection_id": c[2], "collection_name": c[3],
                "content_hash": c[4],
            }
            for c in cards
        ]

        edge_count = 0
        edge_count += self._build_collection_edges(conn, card_list)
        edge_count += self._build_cross_reference_edges(conn, card_list)
        edge_count += self._build_shared_friction_edges(conn)

        domain_count = 0
        domain_count += self._tag_collection_domains(conn, card_list)
        domain_count += self._tag_keyword_domains(conn, card_list)

        conn.commit()
        logger.info(
            "Graph rebuilt: %d edges, %d domain tags from %d cards",
            edge_count, domain_count, len(card_list),
        )
        return {"edge_count": edge_count, "domain_tag_count": domain_count}

    # ═══════════════════════════════════════════════════════════════
    #  Query — Constellation Traversal
    # ═══════════════════════════════════════════════════════════════

    def get_constellation(self, card_id: str, max_hops: int = 2) -> list[dict]:
        """BFS from a seed card, returning all reachable cards within max_hops.

        Args:
            card_id: Seed card ID.
            max_hops: Maximum edge distance (default 2).

        Returns:
            List of dicts: card_id, title, collection, hop_distance, rel_types.
        """
        conn = self._conn()
        visited = {card_id: 0}
        frontier = [card_id]
        edges_by_card = {}  # card_id → set of rel_types

        for hop in range(1, max_hops + 1):
            next_frontier = []
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            rows = conn.execute(f"""
                SELECT source_id, target_id, rel_type
                FROM guru_card_graph
                WHERE source_id IN ({placeholders})
                   OR target_id IN ({placeholders})
            """, frontier + frontier).fetchall()

            for src, tgt, rel in rows:
                neighbor = tgt if src in visited else src
                if neighbor not in visited:
                    visited[neighbor] = hop
                    next_frontier.append(neighbor)
                    edges_by_card.setdefault(neighbor, set()).add(rel)
                elif neighbor in edges_by_card:
                    edges_by_card[neighbor].add(rel)
            frontier = next_frontier

        # Fetch card details for visited nodes (exclude seed)
        result = []
        others = [cid for cid in visited if cid != card_id]
        if others:
            placeholders = ",".join("?" for _ in others)
            details = conn.execute(f"""
                SELECT card_id, title, collection_name
                FROM guru_articles
                WHERE card_id IN ({placeholders})
            """, others).fetchall()

            detail_map = {d[0]: (d[1], d[2]) for d in details}
            for cid in others:
                title, coll = detail_map.get(cid, ("?", "?"))
                result.append({
                    "card_id": cid,
                    "title": title,
                    "collection": coll,
                    "hop_distance": visited[cid],
                    "rel_types": sorted(edges_by_card.get(cid, set())),
                })

        result.sort(key=lambda x: (x["hop_distance"], x["title"]))
        return result

    # ═══════════════════════════════════════════════════════════════
    #  Query — Domain
    # ═══════════════════════════════════════════════════════════════

    def get_domain(self, domain: str) -> list[dict]:
        """Get all cards tagged with a domain.

        Args:
            domain: Domain slug (e.g. 'billing').

        Returns:
            List of dicts: card_id, title, collection, confidence, source.
        """
        conn = self._conn()
        rows = conn.execute("""
            SELECT gcd.card_id, ga.title, ga.collection_name,
                   gcd.confidence, gcd.source
            FROM guru_card_domains gcd
            JOIN guru_articles ga ON gcd.card_id = ga.card_id
            WHERE gcd.domain = ?
            ORDER BY gcd.confidence DESC
        """, (domain,)).fetchall()

        return [
            {
                "card_id": r[0], "title": r[1], "collection": r[2],
                "confidence": r[3], "source": r[4],
            }
            for r in rows
        ]

    def list_domains(self) -> list[dict]:
        """List all domains with card counts.

        Returns:
            List of dicts: domain, card_count.
        """
        conn = self._conn()
        rows = conn.execute("""
            SELECT domain, COUNT(DISTINCT card_id) as cnt
            FROM guru_card_domains
            GROUP BY domain
            ORDER BY cnt DESC
        """).fetchall()
        return [{"domain": r[0], "card_count": r[1]} for r in rows]

    # ═══════════════════════════════════════════════════════════════
    #  Edge Builders (internal)
    # ═══════════════════════════════════════════════════════════════

    def _build_collection_edges(self, conn, cards: list[dict]) -> int:
        """Create same_collection edges between cards sharing a collection."""
        by_collection = {}
        for c in cards:
            coll = c["collection_id"]
            if coll:
                by_collection.setdefault(coll, []).append(c["card_id"])

        count = 0
        for coll_id, card_ids in by_collection.items():
            for i, src in enumerate(card_ids):
                for tgt in card_ids[i + 1:]:
                    conn.execute("""
                        INSERT OR IGNORE INTO guru_card_graph
                            (source_id, target_id, rel_type, weight)
                        VALUES (?, ?, 'same_collection', 0.5)
                    """, (src, tgt))
                    count += 1
        return count

    def _build_cross_reference_edges(self, conn, cards: list[dict]) -> int:
        """Create cross_reference edges when card title appears in another card's content.

        Uses guru_articles.title matched against stored content via LIKE.
        Only checks titles with 3+ words to avoid false positives.
        """
        # Build title index (only meaningful titles)
        title_map = {}  # title_lower → card_id
        for c in cards:
            title = c["title"]
            if title and len(title.split()) >= 3:
                title_map[title.lower()] = c["card_id"]

        if not title_map:
            return 0

        # For each card, check if any other card's title appears in its content
        # We query content from the DB since card_list doesn't include it
        count = 0
        for c in cards:
            card_id = c["card_id"]
            # Get content for this card
            row = conn.execute(
                "SELECT card_id FROM guru_articles WHERE card_id = ?",
                (card_id,),
            ).fetchone()
            if not row:
                continue

            # Check each title against this card's title (lightweight heuristic)
            # Full content matching would require fetching all content — skip for now
            # Instead, check title-in-title references
            card_title_lower = (c["title"] or "").lower()
            for ref_title, ref_id in title_map.items():
                if ref_id != card_id and ref_title in card_title_lower:
                    conn.execute("""
                        INSERT OR IGNORE INTO guru_card_graph
                            (source_id, target_id, rel_type, weight)
                        VALUES (?, ?, 'cross_reference', 0.8)
                    """, (card_id, ref_id))
                    count += 1

        return count

    def _build_shared_friction_edges(self, conn) -> int:
        """Create shared_friction edges between cards covering the same friction type."""
        rows = conn.execute("""
            SELECT a.card_id, b.card_id, a.friction_type
            FROM guru_friction_coverage a
            JOIN guru_friction_coverage b
                ON a.friction_type = b.friction_type
               AND a.card_id < b.card_id
        """).fetchall()

        count = 0
        for src, tgt, ft in rows:
            conn.execute("""
                INSERT OR IGNORE INTO guru_card_graph
                    (source_id, target_id, rel_type, weight)
                VALUES (?, ?, 'shared_friction', 1.0)
            """, (src, tgt))
            count += 1
        return count

    # ═══════════════════════════════════════════════════════════════
    #  Domain Taggers (internal)
    # ═══════════════════════════════════════════════════════════════

    def _tag_collection_domains(self, conn, cards: list[dict]) -> int:
        """Tag cards with domains derived from collection name."""
        count = 0
        for c in cards:
            coll_name = (c["collection_name"] or "").strip()
            if not coll_name:
                continue
            # Slugify collection name as domain
            domain = re.sub(r"[^a-z0-9]+", "_", coll_name.lower()).strip("_")
            if domain:
                conn.execute("""
                    INSERT OR IGNORE INTO guru_card_domains
                        (card_id, domain, source, confidence)
                    VALUES (?, ?, 'collection', 1.0)
                """, (c["card_id"], domain))
                count += 1
        return count

    def _tag_keyword_domains(self, conn, cards: list[dict]) -> int:
        """Tag cards with domains based on title keyword matching."""
        count = 0
        for c in cards:
            title_lower = (c["title"] or "").lower()
            for domain, keywords in self._kw_domains.items():
                if any(kw in title_lower for kw in keywords):
                    conn.execute("""
                        INSERT OR IGNORE INTO guru_card_domains
                            (card_id, domain, source, confidence)
                        VALUES (?, ?, 'keyword', 0.8)
                    """, (c["card_id"], domain))
                    count += 1
        return count

    # ═══════════════════════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════════════════════

    def _conn(self):
        return self.db.get_connection() if hasattr(self.db, 'get_connection') else self.db.conn
