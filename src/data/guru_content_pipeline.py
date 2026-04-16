"""
Alma Insights — Guru Content Pipeline (Phase 4, T4)

Loop B — LLM content generation (write-gated).  Generates proposed
rewrites and new articles, saves as drafts pending human approval.

Usage:
    pipeline = GuruContentPipeline(db_manager, guru_client)
    draft = pipeline.propose_rewrite(card_id, ["billing_friction"])
    new = pipeline.propose_new_article("login_confusion")
    pipeline.approve_and_push(draft["id"])   # after user approval
"""

import logging
from datetime import datetime, timezone

from src.data.guru_client import GuruClient

logger = logging.getLogger("alma.guru_content")


class GuruContentPipeline:
    """Generates content drafts for Guru — rewrites and new articles."""

    def __init__(self, db_manager, guru_client: GuruClient):
        self.db = db_manager
        self.client = guru_client

    # ── Rewrite Proposal ────────────────────────────────────────

    def propose_rewrite(self, card_id: str,
                        friction_types: list[str],
                        llm_client=None) -> dict:
        """Generate proposed rewrite for existing Guru card.

        LLM prompt: current content + coverage gaps + sample tickets.
        Saves to guru_content_drafts (status='pending',
        draft_type='rewrite').
        """
        # Fetch current article content
        card = self.client.get_card(card_id)
        if not card or not card.get("content"):
            raise ValueError(f"Card {card_id} not found or has no content")

        current_content = card["content"]
        title = card.get("title", "Untitled")

        # Get gap descriptions for the friction types
        gaps = self._get_gap_descriptions(friction_types)

        # Get sample ticket data (redacted) for context
        samples = self._get_sample_tickets(friction_types)

        if llm_client:
            draft_content = self._llm_rewrite(
                llm_client, title, current_content, gaps, samples
            )
        else:
            # Without LLM, produce a structured gap summary
            draft_content = self._fallback_rewrite(
                title, current_content, gaps, samples
            )

        # Save draft
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.conn.execute("""
            INSERT INTO guru_content_drafts
                (card_id, friction_type, draft_type, title, content,
                 source_tickets, status, created_at)
            VALUES (?, ?, 'rewrite', ?, ?, ?, 'pending', ?)
        """, (
            card_id,
            ",".join(friction_types),
            title,
            draft_content,
            ",".join(samples.get("ticket_ids", [])),
            now,
        ))
        self.db.conn.commit()

        draft_id = cursor.lastrowid
        logger.info("Created rewrite draft %d for card %s", draft_id, card_id)

        return {
            "id": draft_id,
            "card_id": card_id,
            "title": title,
            "content": draft_content,
            "draft_type": "rewrite",
            "status": "pending",
        }

    # ── New Article Proposal ────────────────────────────────────

    def propose_new_article(self, friction_type: str,
                            llm_client=None) -> dict:
        """Generate new article for uncovered friction.

        LLM prompt: friction description + sample tickets
        + existing article style.
        Saves to guru_content_drafts (status='pending',
        draft_type='new_article').
        """
        # Get friction context
        friction_info = self._get_friction_info(friction_type)
        samples = self._get_sample_tickets([friction_type])

        # Get example article for style reference
        style_example = self._get_style_example()

        if llm_client:
            title, content = self._llm_new_article(
                llm_client, friction_type, friction_info, samples,
                style_example
            )
        else:
            title = f"Guide: {friction_type.replace('_', ' ').title()}"
            content = self._fallback_new_article(
                friction_type, friction_info, samples
            )

        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.conn.execute("""
            INSERT INTO guru_content_drafts
                (card_id, friction_type, draft_type, title, content,
                 source_tickets, status, created_at)
            VALUES ('', ?, 'new_article', ?, ?, ?, 'pending', ?)
        """, (
            friction_type,
            title,
            content,
            ",".join(samples.get("ticket_ids", [])),
            now,
        ))
        self.db.conn.commit()

        draft_id = cursor.lastrowid
        logger.info("Created new article draft %d for %s",
                     draft_id, friction_type)

        return {
            "id": draft_id,
            "card_id": "",
            "title": title,
            "content": content,
            "draft_type": "new_article",
            "status": "pending",
        }

    # ── Approval / Rejection ────────────────────────────────────

    def approve_and_push(self, draft_id: int,
                         approved_by: str = "user",
                         collection_id: str | None = None) -> bool:
        """Push approved draft to Guru.

        **ONLY after explicit user approval** in UI.
        Rewrites → GuruClient.update_card().
        New articles → GuruClient.create_card(), clipboard fallback.
        Records effectiveness baseline on push.
        """
        draft = self._get_draft(draft_id)
        if not draft:
            logger.warning("Draft %d not found", draft_id)
            return False

        if draft["status"] != "pending":
            logger.info("Draft %d already %s", draft_id, draft["status"])
            return draft["status"] == "pushed"

        now = datetime.now(timezone.utc).isoformat()

        if draft["draft_type"] == "rewrite" and draft["card_id"]:
            try:
                self.client.update_card(
                    draft["card_id"],
                    draft["content"],
                    draft["title"],
                )
            except Exception as exc:
                logger.error("Failed to push draft %d: %s", draft_id, exc)
                return False
        elif draft["draft_type"] == "new_article":
            # Try API create, fall back to clipboard
            created = False
            if collection_id:
                try:
                    self.client.create_card(
                        collection_id,
                        draft["title"],
                        draft["content"],
                    )
                    created = True
                except Exception as exc:
                    logger.warning(
                        "API create failed for draft %d, falling back to "
                        "clipboard: %s", draft_id, exc
                    )
            if not created:
                try:
                    from PySide6.QtWidgets import QApplication
                    clipboard = QApplication.clipboard()
                    clipboard.setText(draft["content"])
                    logger.info("Draft %d copied to clipboard (API create "
                                "unavailable)", draft_id)
                except Exception:
                    logger.warning("Clipboard fallback also failed for "
                                   "draft %d", draft_id)

        # Mark as pushed
        self.db.conn.execute("""
            UPDATE guru_content_drafts
            SET status = 'pushed', approved_by = ?, pushed_at = ?
            WHERE id = ?
        """, (approved_by, now, draft_id))
        self.db.conn.commit()

        # Record effectiveness baseline
        self._record_baseline(draft)

        logger.info("Pushed draft %d to Guru", draft_id)
        return True

    def reject(self, draft_id: int) -> bool:
        """Mark draft as rejected without API call."""
        draft = self._get_draft(draft_id)
        if not draft:
            return False

        self.db.conn.execute(
            "UPDATE guru_content_drafts SET status = 'rejected' WHERE id = ?",
            (draft_id,),
        )
        self.db.conn.commit()
        logger.info("Rejected draft %d", draft_id)
        return True

    def get_pending_drafts(self) -> list[dict]:
        """All drafts with status='pending', most recent first."""
        rows = self.db.conn.execute("""
            SELECT id, card_id, friction_type, draft_type, title,
                   content, source_tickets, status, created_at
            FROM guru_content_drafts
            WHERE status = 'pending'
            ORDER BY created_at DESC
        """).fetchall()
        return [
            {
                "id": r[0], "card_id": r[1], "friction_type": r[2],
                "draft_type": r[3], "title": r[4], "content": r[5],
                "source_tickets": r[6], "status": r[7], "created_at": r[8],
            }
            for r in rows
        ]

    def get_all_drafts(self) -> list[dict]:
        """All drafts, most recent first."""
        rows = self.db.conn.execute("""
            SELECT id, card_id, friction_type, draft_type, title,
                   content, source_tickets, status, approved_by,
                   pushed_at, created_at
            FROM guru_content_drafts
            ORDER BY created_at DESC
        """).fetchall()
        return [
            {
                "id": r[0], "card_id": r[1], "friction_type": r[2],
                "draft_type": r[3], "title": r[4], "content": r[5],
                "source_tickets": r[6], "status": r[7],
                "approved_by": r[8], "pushed_at": r[9], "created_at": r[10],
            }
            for r in rows
        ]

    # ── Internals ───────────────────────────────────────────────

    def _get_draft(self, draft_id: int) -> dict | None:
        """Fetch a single draft by ID."""
        row = self.db.conn.execute(
            "SELECT id, card_id, friction_type, draft_type, title, "
            "content, source_tickets, status, approved_by, pushed_at "
            "FROM guru_content_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "card_id": row[1], "friction_type": row[2],
            "draft_type": row[3], "title": row[4], "content": row[5],
            "source_tickets": row[6], "status": row[7],
            "approved_by": row[8], "pushed_at": row[9],
        }

    def _get_gap_descriptions(self, friction_types: list[str]) -> list[dict]:
        """Get coverage gap descriptions for friction types."""
        if not friction_types:
            return []
        placeholders = ",".join("?" for _ in friction_types)
        rows = self.db.conn.execute(
            f"SELECT friction_type, gap_description, coverage_score "
            f"FROM guru_friction_coverage "
            f"WHERE friction_type IN ({placeholders})",
            friction_types,
        ).fetchall()
        return [
            {"friction_type": r[0], "gap": r[1], "score": r[2]}
            for r in rows
        ]

    def _get_sample_tickets(self, friction_types: list[str]) -> dict:
        """Get redacted sample ticket subjects for context.

        Uses sub_patterns to find relevant tickets from
        the classifications table.
        """
        ticket_ids = []
        subjects = []

        for ft in friction_types[:3]:  # Limit search
            rows = self.db.conn.execute("""
                SELECT c.ticket_id, c.subject
                FROM classifications cl
                JOIN conversations c ON cl.ticket_id = c.ticket_id
                JOIN sub_patterns sp ON cl.trc = sp.trc
                WHERE sp.friction_type = ?
                LIMIT 5
            """, (ft,)).fetchall()

            for r in rows:
                ticket_ids.append(str(r[0]))
                # Truncate subject for context (already redacted by pipeline)
                subj = r[1][:100] if r[1] else ""
                if subj:
                    subjects.append(subj)

        return {"ticket_ids": ticket_ids[:10], "subjects": subjects[:10]}

    def _get_friction_info(self, friction_type: str) -> dict:
        """Get sub_pattern info for a friction type."""
        row = self.db.conn.execute(
            "SELECT friction_type, trc, label, description, "
            "lifetime_tickets FROM sub_patterns "
            "WHERE friction_type = ? AND merged_into IS NULL LIMIT 1",
            (friction_type,),
        ).fetchone()
        if not row:
            return {"friction_type": friction_type}
        return {
            "friction_type": row[0], "trc": row[1], "label": row[2],
            "description": row[3] or "", "ticket_volume": row[4],
        }

    def _get_style_example(self) -> str:
        """Get first article content as style reference."""
        row = self.db.conn.execute(
            "SELECT ga.card_id FROM guru_articles ga "
            "WHERE ga.status = 'active' LIMIT 1"
        ).fetchone()
        if row:
            try:
                card = self.client.get_card(row[0])
                return card.get("content", "")[:500]
            except Exception:
                pass
        return ""

    def _record_baseline(self, draft: dict):
        """Record effectiveness baseline when a draft is pushed."""
        try:
            from src.data.guru_effectiveness import GuruEffectivenessTracker
            tracker = GuruEffectivenessTracker(self.db)
            for ft in draft["friction_type"].split(","):
                ft = ft.strip()
                if ft:
                    tracker.record_baseline(
                        draft.get("card_id", ""), ft
                    )
        except Exception as exc:
            logger.warning("Failed to record baseline: %s", exc)

    # ── LLM Generation ──────────────────────────────────────────

    @staticmethod
    def _llm_rewrite(llm_client, title: str, current_content: str,
                     gaps: list[dict], samples: dict) -> str:
        """Generate rewrite via LLM."""
        gap_text = "\n".join(
            f"- {g['friction_type']}: {g['gap']} (score: {g['score']})"
            for g in gaps
        )
        sample_text = "\n".join(
            f"- {s}" for s in samples.get("subjects", [])[:5]
        )

        prompt = (
            f"You are rewriting a knowledge base article to better address "
            f"customer friction.\n\n"
            f"CURRENT TITLE: {title}\n\n"
            f"CURRENT CONTENT (first 1000 chars):\n"
            f"{current_content[:1000]}\n\n"
            f"COVERAGE GAPS:\n{gap_text or 'None identified'}\n\n"
            f"SAMPLE CUSTOMER QUESTIONS:\n{sample_text or 'None available'}\n\n"
            f"Write an improved version of this article that:\n"
            f"1. Addresses the identified coverage gaps\n"
            f"2. Uses clear, concise language\n"
            f"3. Includes step-by-step instructions where appropriate\n"
            f"4. Maintains the existing article structure where possible\n\n"
            f"Return ONLY the article content (no meta-commentary)."
        )
        try:
            return llm_client.generate(prompt)
        except Exception as exc:
            logger.warning("LLM rewrite failed: %s", exc)
            return current_content

    @staticmethod
    def _llm_new_article(llm_client, friction_type: str,
                         friction_info: dict, samples: dict,
                         style_example: str) -> tuple[str, str]:
        """Generate new article via LLM. Returns (title, content)."""
        sample_text = "\n".join(
            f"- {s}" for s in samples.get("subjects", [])[:5]
        )

        prompt = (
            f"Write a new knowledge base article for the customer friction "
            f'type: "{friction_type}"\n\n'
            f"CONTEXT:\n"
            f"- TRC Code: {friction_info.get('trc', 'unknown')}\n"
            f"- Label: {friction_info.get('label', friction_type)}\n"
            f"- Description: {friction_info.get('description', 'N/A')}\n"
            f"- Ticket volume: {friction_info.get('ticket_volume', 'N/A')}\n\n"
            f"SAMPLE CUSTOMER QUESTIONS:\n{sample_text or 'None available'}\n\n"
        )
        if style_example:
            prompt += (
                f"STYLE REFERENCE (match this format):\n"
                f"{style_example}\n\n"
            )
        prompt += (
            f"Return two lines at the top:\n"
            f"TITLE: <article title>\n"
            f"---\n"
            f"Then the full article content."
        )

        try:
            response = llm_client.generate(prompt)
            return _parse_new_article_response(response, friction_type)
        except Exception as exc:
            logger.warning("LLM new article failed: %s", exc)
            title = f"Guide: {friction_type.replace('_', ' ').title()}"
            return title, ""

    @staticmethod
    def _fallback_rewrite(title: str, current_content: str,
                          gaps: list[dict], samples: dict) -> str:
        """Produce a gap summary when no LLM is available."""
        sections = [f"# {title}\n"]
        if gaps:
            sections.append("## Coverage Gaps Identified\n")
            for g in gaps:
                sections.append(
                    f"- **{g['friction_type']}**: {g['gap']} "
                    f"(score: {g['score']})"
                )
        if samples.get("subjects"):
            sections.append("\n## Sample Customer Questions\n")
            for s in samples["subjects"][:5]:
                sections.append(f"- {s}")
        sections.append("\n## Original Content\n")
        sections.append(current_content[:2000])
        return "\n".join(sections)

    @staticmethod
    def _fallback_new_article(friction_type: str, friction_info: dict,
                              samples: dict) -> str:
        """Produce a template when no LLM is available."""
        sections = [
            f"# {friction_type.replace('_', ' ').title()}\n",
            f"**TRC Code**: {friction_info.get('trc', 'N/A')}\n",
            f"**Description**: {friction_info.get('description', 'N/A')}\n",
        ]
        if samples.get("subjects"):
            sections.append("## Common Customer Questions\n")
            for s in samples["subjects"][:5]:
                sections.append(f"- {s}")
        sections.append("\n## Steps to Resolve\n")
        sections.append("1. [Step 1]\n2. [Step 2]\n3. [Step 3]\n")
        return "\n".join(sections)


def _parse_new_article_response(response: str,
                                friction_type: str) -> tuple[str, str]:
    """Parse LLM new article response into (title, content)."""
    lines = response.strip().split("\n")
    title = f"Guide: {friction_type.replace('_', ' ').title()}"
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("TITLE:"):
            title = stripped.split(":", 1)[1].strip()
            content_start = i + 1
        elif stripped == "---":
            content_start = i + 1
            break

    content = "\n".join(lines[content_start:]).strip()
    return title, content
