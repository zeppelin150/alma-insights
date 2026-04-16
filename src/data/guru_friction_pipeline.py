"""
Alma Insights — Guru Friction Pipeline (Phase 4, T3)

Loop A — Read-only friction analysis.  Syncs Guru articles, compares
them against friction types from sub_patterns, and identifies
coverage gaps.

Usage:
    pipeline = GuruFrictionPipeline(db_manager, guru_client)
    n_updated = pipeline.sync_articles()
    gaps = pipeline.analyze_coverage(scan_id="scan_123")
    report = pipeline.get_gap_report()
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from src.data.guru_client import GuruClient

logger = logging.getLogger("alma.guru_friction")


class GuruFrictionPipeline:
    """Analyzes Guru articles for friction coverage gaps."""

    def __init__(self, db_manager: object, guru_client: GuruClient) -> None:
        self.db = db_manager
        self.client = guru_client

    # ── Article Sync ────────────────────────────────────────────

    def sync_articles(self) -> int:
        """Pull all Guru cards into guru_articles table.

        Uses content_hash (SHA-256) for change detection.
        Returns count of new or updated articles.
        """
        cards = self.client.list_cards()
        if not cards:
            logger.info("No Guru cards found")
            return 0

        count = 0
        now = datetime.now(timezone.utc).isoformat()

        for card in cards:
            card_id = card.get("id", "")
            if not card_id:
                continue

            # Fetch full card content for hashing
            try:
                full = self.client.get_card(card_id)
            except Exception as exc:
                logger.warning("Failed to fetch card %s: %s", card_id, exc)
                continue

            content = full.get("content", "")
            content_hash = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()

            # Check if article already exists with same hash
            existing = self.db.conn.execute(
                "SELECT content_hash FROM guru_articles WHERE card_id = ?",
                (card_id,),
            ).fetchone()

            if existing and existing[0] == content_hash:
                # No content change — just update last_synced_at
                self.db.conn.execute(
                    "UPDATE guru_articles SET last_synced_at = ? "
                    "WHERE card_id = ?",
                    (now, card_id),
                )
                continue

            # Insert or update
            self.db.conn.execute("""
                INSERT INTO guru_articles
                    (card_id, collection_id, collection_name, title,
                     content_hash, last_synced_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                ON CONFLICT(card_id) DO UPDATE SET
                    collection_id   = excluded.collection_id,
                    collection_name = excluded.collection_name,
                    title           = excluded.title,
                    content_hash    = excluded.content_hash,
                    last_synced_at  = excluded.last_synced_at
            """, (
                card_id,
                full.get("collection_id", ""),
                full.get("collection", ""),
                full.get("title", ""),
                content_hash,
                now,
            ))
            count += 1

        self.db.conn.commit()
        logger.info("Synced %d new/updated Guru articles", count)
        return count

    # ── Friction Coverage Analysis ──────────────────────────────

    def analyze_coverage(self, scan_id: str = "",
                         llm_client: object | None = None,
                         source_id: str | None = None) -> list[dict]:
        """Compare friction types against Guru articles.

        1. Get active friction_types from sub_patterns
        2. For each, search guru_articles for related cards
        3. LLM scores coverage (0.0-1.0) + identifies gaps
        4. Upsert into guru_friction_coverage
        5. Return results sorted by coverage_score ASC (worst first)

        Args:
            source_id: Optional — scope friction types to a specific data source.
                       Default (None) = all sources combined (KB quality view).
        """
        friction_types = self._get_active_friction_types(source_id=source_id)
        if not friction_types:
            logger.info("No friction types found — run NLP scan first")
            return []

        articles = self.db.conn.execute(
            "SELECT card_id, title, content_hash FROM guru_articles "
            "WHERE status = 'active'"
        ).fetchall()

        if not articles:
            logger.info("No Guru articles synced — sync first")
            return []

        results = []
        now = datetime.now(timezone.utc).isoformat()

        for ft in friction_types:
            friction_type = ft["friction_type"]
            trc = ft["trc"]
            label = ft["label"]

            # Find potentially relevant articles by keyword matching
            matching = self._find_relevant_articles(
                friction_type, label, trc
            )

            if not matching:
                # No coverage at all
                results.append({
                    "friction_type": friction_type,
                    "trc": trc,
                    "label": label,
                    "coverage_score": 0.0,
                    "gap_description": "No Guru article covers this friction type",
                    "card_id": "",
                })
                continue

            # Score each match
            for article_row in matching:
                card_id = article_row[0]
                title = article_row[1]

                # Use LLM for detailed scoring if available
                if llm_client:
                    score, gap_desc = self._llm_score_coverage(
                        llm_client, friction_type, label, title
                    )
                else:
                    # Keyword-based scoring fallback
                    score, gap_desc = self._keyword_score(
                        friction_type, label, title
                    )

                # Upsert coverage record
                self.db.conn.execute("""
                    INSERT INTO guru_friction_coverage
                        (friction_type, card_id, coverage_score,
                         gap_description, analyzed_at, scan_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(friction_type, card_id) DO UPDATE SET
                        coverage_score  = excluded.coverage_score,
                        gap_description = excluded.gap_description,
                        analyzed_at     = excluded.analyzed_at,
                        scan_id         = excluded.scan_id
                """, (friction_type, card_id, score, gap_desc, now, scan_id))

                results.append({
                    "friction_type": friction_type,
                    "trc": trc,
                    "label": label,
                    "coverage_score": score,
                    "gap_description": gap_desc,
                    "card_id": card_id,
                    "card_title": title,
                })

        self.db.conn.commit()

        # Sort by coverage (worst gaps first)
        results.sort(key=lambda r: r["coverage_score"])
        return results

    # ── Gap Report ──────────────────────────────────────────────

    def get_gap_report(self) -> list[dict]:
        """LEFT JOIN sub_patterns against guru_friction_coverage.

        Uncovered friction → gap_score = 1.0.
        Low coverage → gap_score = 1 - coverage_score.
        Returns sorted by gap_score DESC (worst gaps first).
        """
        rows = self.db.conn.execute("""
            SELECT
                sp.friction_type,
                sp.trc,
                sp.label,
                sp.lifetime_tickets,
                COALESCE(gfc.coverage_score, 0.0) AS coverage,
                COALESCE(gfc.gap_description, 'No coverage') AS gap_desc,
                COALESCE(ga.title, '') AS card_title,
                COALESCE(gfc.card_id, '') AS card_id
            FROM sub_patterns sp
            LEFT JOIN guru_friction_coverage gfc
                ON sp.friction_type = gfc.friction_type
            LEFT JOIN guru_articles ga
                ON gfc.card_id = ga.card_id
            WHERE sp.merged_into IS NULL
              AND sp.tier != 'retired'
              AND sp.friction_type IS NOT NULL
              AND sp.friction_type != ''
            ORDER BY COALESCE(gfc.coverage_score, 0.0) ASC,
                     sp.lifetime_tickets DESC
        """).fetchall()

        return [
            {
                "friction_type": r[0],
                "trc": r[1],
                "label": r[2],
                "ticket_volume": r[3],
                "coverage_score": r[4],
                "gap_score": round(1.0 - r[4], 3),
                "gap_description": r[5],
                "card_title": r[6],
                "card_id": r[7],
            }
            for r in rows
        ]

    # ── Deep Friction Analysis (H6) ────────────────────────────

    def analyze_friction_deep(self, friction_type: str,
                              scan_id: str = "",
                              llm_client: object | None = None,
                              progress_cb: Callable[[str], None] | None = None) -> dict:
        """Multi-agent deep analysis of a single friction type.

        Three-phase pipeline:
          1. Search agent — find related cards via graph constellation
          2. Per-card analysis — compare each card's metadata vs friction data
          3. Synthesis — build friction flow map + prioritized rewrite list

        Args:
            friction_type: The friction type to analyze (e.g. 'login_loop').
            scan_id: Optional scan ID for context.
            llm_client: Optional LLM client for enhanced analysis.
            progress_cb: Optional callable(str) for status updates.

        Returns:
            dict with keys: friction_type, cards_analyzed, flow_map_md,
            rewrite_priorities, constellation, phases_completed, error.
        """
        _progress = progress_cb or (lambda msg: None)
        result = {
            "friction_type": friction_type,
            "cards_analyzed": 0,
            "flow_map_md": "",
            "rewrite_priorities": [],
            "constellation": [],
            "phases_completed": [],
            "error": None,
        }

        # ── Phase 1: Search Agent — find relevant cards ──
        _progress("Phase 1: Searching card constellation...")
        try:
            constellation, seed_card_id = self._search_agent(friction_type)
            result["constellation"] = constellation
            result["phases_completed"].append("search")
        except Exception as exc:
            logger.error("Deep analysis Phase 1 failed: %s", exc)
            result["error"] = f"Search failed: {exc}"
            return result

        if not seed_card_id:
            result["flow_map_md"] = (
                f"## Friction Analysis: {friction_type}\n\n"
                f"No Guru cards found covering this friction type.\n"
                f"**Recommendation**: Create a new KB article."
            )
            result["phases_completed"].append("no_cards")
            return result

        # ── Phase 2: Per-card Analysis Agent ──
        _progress(f"Phase 2: Analyzing {len(constellation) + 1} cards...")
        try:
            card_analyses = self._per_card_analysis_agent(
                friction_type, seed_card_id, constellation, llm_client
            )
            result["cards_analyzed"] = len(card_analyses)
            result["phases_completed"].append("per_card_analysis")
        except Exception as exc:
            logger.error("Deep analysis Phase 2 failed: %s", exc)
            result["error"] = f"Card analysis failed: {exc}"
            card_analyses = []

        # ── Phase 3: Synthesis Agent ──
        _progress("Phase 3: Synthesizing friction flow map...")
        try:
            flow_map, priorities = self._synthesis_agent(
                friction_type, card_analyses, constellation, llm_client
            )
            result["flow_map_md"] = flow_map
            result["rewrite_priorities"] = priorities
            result["phases_completed"].append("synthesis")
        except Exception as exc:
            logger.error("Deep analysis Phase 3 failed: %s", exc)
            result["error"] = f"Synthesis failed: {exc}"
            # Fallback flow map
            result["flow_map_md"] = self._fallback_flow_map(
                friction_type, card_analyses
            )
            result["phases_completed"].append("synthesis_fallback")

        logger.info(
            "Deep analysis of '%s': %d cards, phases=%s",
            friction_type, result["cards_analyzed"],
            result["phases_completed"],
        )
        return result

    def _search_agent(self, friction_type: str) -> tuple[list, str]:
        """Phase 1: Find seed card and constellation for a friction type.

        Returns:
            (constellation_list, seed_card_id) or ([], "") if no card found.
        """
        # Find the primary card covering this friction type
        row = self.db.conn.execute("""
            SELECT gfc.card_id, ga.title, ga.collection_name
            FROM guru_friction_coverage gfc
            JOIN guru_articles ga ON gfc.card_id = ga.card_id
            WHERE gfc.friction_type = ?
            ORDER BY gfc.coverage_score DESC
            LIMIT 1
        """, (friction_type,)).fetchone()

        if not row:
            return [], ""

        seed_card_id = row[0]

        # Get constellation from graph builder
        try:
            from src.data.guru_graph_builder import GuruGraphBuilder
            builder = GuruGraphBuilder(self.db)
            constellation = builder.get_constellation(seed_card_id, max_hops=2)
        except Exception:
            constellation = []

        return constellation, seed_card_id

    def _per_card_analysis_agent(self, friction_type: str,
                                  seed_card_id: str,
                                  constellation: list,
                                  llm_client) -> list[dict]:
        """Phase 2: Analyze each card's relevance to the friction type.

        Returns list of dicts with card_id, title, relevance_score,
        coverage_score, gap, recommendation.
        """
        # Collect all card IDs to analyze (seed + constellation)
        card_ids = [seed_card_id]
        card_ids.extend(c["card_id"] for c in constellation[:3])  # max 4 total

        analyses = []
        for card_id in card_ids:
            card_row = self.db.conn.execute(
                "SELECT card_id, title, collection_name FROM guru_articles "
                "WHERE card_id = ?",
                (card_id,),
            ).fetchone()
            if not card_row:
                continue

            # Get existing coverage data
            cov_row = self.db.conn.execute(
                "SELECT coverage_score, gap_description "
                "FROM guru_friction_coverage "
                "WHERE friction_type = ? AND card_id = ?",
                (friction_type, card_id),
            ).fetchone()

            coverage = cov_row[0] if cov_row else 0.0
            gap = cov_row[1] if cov_row else "Not analyzed"

            # Determine recommendation
            if coverage >= 0.8:
                rec = "adequate"
            elif coverage >= 0.5:
                rec = "needs_update"
            elif coverage > 0.0:
                rec = "major_rewrite"
            else:
                rec = "not_relevant"

            analysis = {
                "card_id": card_id,
                "title": card_row[1],
                "collection": card_row[2],
                "coverage_score": coverage,
                "gap": gap,
                "recommendation": rec,
                "is_seed": card_id == seed_card_id,
            }

            # Optional LLM enhancement
            if llm_client and coverage < 0.8:
                try:
                    enhanced = self._llm_analyze_card(
                        llm_client, friction_type, card_row[1], gap
                    )
                    analysis["gap"] = enhanced.get("gap", gap)
                    analysis["recommendation"] = enhanced.get("rec", rec)
                except Exception:
                    pass  # Keep heuristic values

            analyses.append(analysis)

        return analyses

    def _synthesis_agent(self, friction_type: str,
                          card_analyses: list[dict],
                          constellation: list,
                          llm_client) -> tuple[str, list]:
        """Phase 3: Build friction flow map and rewrite priority list.

        Returns:
            (flow_map_markdown, rewrite_priorities)
        """
        if llm_client and card_analyses:
            return self._llm_synthesis(
                llm_client, friction_type, card_analyses, constellation
            )
        return self._heuristic_synthesis(
            friction_type, card_analyses, constellation
        )

    def _heuristic_synthesis(self, friction_type: str,
                              card_analyses: list[dict],
                              constellation: list) -> tuple[str, list]:
        """Build flow map and priorities without LLM."""
        lines = [f"## Friction Flow: {friction_type}\n"]

        # Flow map
        seed = next((a for a in card_analyses if a.get("is_seed")), None)
        if seed:
            lines.append(f"**Primary Card**: {seed['title']}")
            lines.append(f"  Coverage: {seed['coverage_score']:.2f}")
            if seed["gap"]:
                lines.append(f"  Gap: {seed['gap']}")
            lines.append("")

        related = [a for a in card_analyses if not a.get("is_seed")]
        if related:
            lines.append("### Related Cards\n")
            for a in related:
                lines.append(
                    f"- **{a['title']}** (coverage: {a['coverage_score']:.2f}, "
                    f"rec: {a['recommendation']})"
                )
            lines.append("")

        if constellation:
            lines.append(f"### Constellation ({len(constellation)} cards)\n")
            for c in constellation[:5]:
                rels = ", ".join(c.get("rel_types", []))
                lines.append(
                    f"- {c['title']} (hop {c['hop_distance']}, {rels})"
                )
            lines.append("")

        # Priorities
        priorities = []
        for a in sorted(card_analyses, key=lambda x: x["coverage_score"]):
            if a["recommendation"] in ("major_rewrite", "needs_update"):
                priorities.append({
                    "card_id": a["card_id"],
                    "title": a["title"],
                    "action": a["recommendation"],
                    "coverage": a["coverage_score"],
                    "gap": a["gap"],
                })

        if priorities:
            lines.append("### Rewrite Priorities\n")
            for i, p in enumerate(priorities, 1):
                action = "Major rewrite" if p["action"] == "major_rewrite" else "Update"
                lines.append(
                    f"{i}. **{p['title']}** — {action} "
                    f"(coverage: {p['coverage']:.2f})"
                )
            lines.append("")
        else:
            lines.append("*All analyzed cards have adequate coverage.*\n")

        return "\n".join(lines), priorities

    def _llm_synthesis(self, llm_client, friction_type: str,
                        card_analyses: list[dict],
                        constellation: list) -> tuple[str, list]:
        """LLM-enhanced synthesis of friction flow map."""
        # Build context for LLM
        card_summary = []
        for a in card_analyses:
            card_summary.append(
                f"- {a['title']} (coverage: {a['coverage_score']:.2f}, "
                f"gap: {a['gap']}, rec: {a['recommendation']})"
            )

        prompt = (
            f"Analyze the Guru KB coverage for friction type '{friction_type}'.\n\n"
            f"Cards analyzed:\n" + "\n".join(card_summary) + "\n\n"
            f"Constellation has {len(constellation)} additional related cards.\n\n"
            f"Produce:\n"
            f"1. A brief friction flow map (how does this friction manifest, "
            f"which cards help, which have gaps)\n"
            f"2. A prioritized list of rewrite recommendations\n\n"
            f"Use markdown formatting."
        )

        try:
            response = llm_client.generate(prompt)
            # Extract priorities from card analyses (LLM text is the flow map)
            priorities = []
            for a in sorted(card_analyses, key=lambda x: x["coverage_score"]):
                if a["recommendation"] in ("major_rewrite", "needs_update"):
                    priorities.append({
                        "card_id": a["card_id"],
                        "title": a["title"],
                        "action": a["recommendation"],
                        "coverage": a["coverage_score"],
                        "gap": a["gap"],
                    })
            return response, priorities
        except Exception as exc:
            logger.warning("LLM synthesis failed, using heuristic: %s", exc)
            return self._heuristic_synthesis(
                friction_type, card_analyses, constellation
            )

    @staticmethod
    def _llm_analyze_card(llm_client, friction_type: str,
                           title: str, current_gap: str) -> dict:
        """LLM-enhanced per-card analysis."""
        prompt = (
            f"A Guru KB article titled \"{title}\" covers friction type "
            f"\"{friction_type}\".\n"
            f"Current gap assessment: {current_gap}\n\n"
            f"Provide:\n"
            f"GAP: <updated gap description>\n"
            f"REC: <one of: adequate, needs_update, major_rewrite>"
        )
        response = llm_client.generate(prompt)
        result = {}
        for line in response.strip().split("\n"):
            line = line.strip()
            if line.upper().startswith("GAP:"):
                result["gap"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("REC:"):
                rec = line.split(":", 1)[1].strip().lower()
                if rec in ("adequate", "needs_update", "major_rewrite"):
                    result["rec"] = rec
        return result

    def _fallback_flow_map(self, friction_type: str,
                            card_analyses: list[dict]) -> str:
        """Minimal flow map when synthesis fails."""
        lines = [
            f"## Friction Analysis: {friction_type}\n",
            f"Analyzed {len(card_analyses)} cards.\n",
        ]
        for a in card_analyses:
            lines.append(
                f"- **{a['title']}**: coverage {a['coverage_score']:.2f} "
                f"({a['recommendation']})"
            )
        return "\n".join(lines)

    # ── Friction Scores ─────────────────────────────────────────

    def compute_friction_scores(self) -> None:
        """Update guru_articles.friction_score from coverage analysis.

        Higher score = more friction (worse article quality).
        Score = avg(1 - coverage_score) across all friction types
        mapped to this article.
        """
        self.db.conn.execute("""
            UPDATE guru_articles
            SET friction_score = COALESCE((
                SELECT AVG(1.0 - gfc.coverage_score)
                FROM guru_friction_coverage gfc
                WHERE gfc.card_id = guru_articles.card_id
            ), 0.0)
        """)
        self.db.conn.commit()

    # ── Internals ───────────────────────────────────────────────

    def _get_active_friction_types(self, source_id: str | None = None) -> list[dict]:
        """Get distinct active friction types from sub_patterns.

        When source_id is provided, only returns friction types from that source.
        Default (None) returns all — combined KB quality view.
        """
        sql = """
            SELECT DISTINCT friction_type, trc, label
            FROM sub_patterns
            WHERE merged_into IS NULL
              AND tier != 'retired'
              AND friction_type IS NOT NULL
              AND friction_type != ''"""
        params = []
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        sql += " ORDER BY friction_type"
        rows = self.db.conn.execute(sql, params).fetchall()
        return [
            {"friction_type": r[0], "trc": r[1], "label": r[2]}
            for r in rows
        ]

    def _find_relevant_articles(self, friction_type: str,
                                label: str, trc: str) -> list:
        """Find Guru articles that might cover this friction type.

        Uses keyword matching against article titles.
        """
        # Build search terms from friction type and label
        keywords = set()
        for text in [friction_type, label]:
            for word in text.lower().replace("_", " ").replace("-", " ").split():
                if len(word) > 2:
                    keywords.add(word)

        if not keywords:
            return []

        # Build WHERE clause for keyword matching
        conditions = []
        params = []
        for kw in list(keywords)[:5]:  # Limit to avoid huge queries
            conditions.append("LOWER(title) LIKE ?")
            params.append(f"%{kw}%")

        where = " OR ".join(conditions)
        return self.db.conn.execute(
            f"SELECT card_id, title FROM guru_articles "
            f"WHERE status = 'active' AND ({where})",
            params,
        ).fetchall()

    @staticmethod
    def _keyword_score(friction_type: str, label: str,
                       title: str) -> tuple[float, str]:
        """Simple keyword-based coverage scoring (fallback).

        Counts keyword overlap between friction type/label and title.
        """
        ft_words = set(
            friction_type.lower().replace("_", " ").replace("-", " ").split()
        )
        label_words = set(
            label.lower().replace("_", " ").replace("-", " ").split()
        )
        all_words = ft_words | label_words
        all_words = {w for w in all_words if len(w) > 2}

        if not all_words:
            return 0.5, "Unable to assess coverage"

        title_lower = title.lower()
        matches = sum(1 for w in all_words if w in title_lower)
        score = min(1.0, matches / max(len(all_words), 1))

        if score < 0.3:
            gap = "Article title has low relevance to this friction type"
        elif score < 0.7:
            gap = "Partial coverage — article may not fully address friction"
        else:
            gap = ""

        return round(score, 3), gap

    @staticmethod
    def _llm_score_coverage(llm_client, friction_type: str,
                            label: str, title: str) -> tuple[float, str]:
        """Use LLM to score coverage quality."""
        prompt = (
            f"Rate how well a knowledge base article titled "
            f'"{title}" would cover the customer friction type '
            f'"{friction_type}" (label: "{label}").\n\n'
            f"Respond with exactly two lines:\n"
            f"SCORE: <number 0.0 to 1.0>\n"
            f"GAP: <brief description of what's missing, or empty if "
            f"fully covered>"
        )
        try:
            response = llm_client.generate(prompt)
            return _parse_coverage_response(response)
        except Exception as exc:
            logger.warning("LLM coverage scoring failed: %s", exc)
            return GuruFrictionPipeline._keyword_score(
                friction_type, label, title
            )


def _parse_coverage_response(response: str) -> tuple[float, str]:
    """Parse LLM coverage response into (score, gap_description)."""
    score = 0.5
    gap = ""
    for line in response.strip().split("\n"):
        line = line.strip()
        if line.upper().startswith("SCORE:"):
            try:
                val = float(line.split(":", 1)[1].strip())
                score = max(0.0, min(1.0, val))
            except (ValueError, IndexError):
                pass
        elif line.upper().startswith("GAP:"):
            gap = line.split(":", 1)[1].strip()
    return round(score, 3), gap
