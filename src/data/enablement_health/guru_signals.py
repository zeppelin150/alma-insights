"""The ONLY place that touches GuruClient.

``GuruSignals`` wraps the live Guru API surface the health score needs:
the verification queue, per-card view demand (derived from the team analytics
event stream), open-comment counts, and last-modified. A tiny per-instance
cache stops repeated lookups in one ``compute_health`` pass from refetching.

Pure live API — no DB, no settings, no warehouse. Every method guard-clauses an
empty/None client so a partially-configured environment degrades to empty
signals rather than raising.
"""

from __future__ import annotations


class GuruSignals:
    """Live Guru signal facade with a one-pass cache."""

    def __init__(self, guru_client):
        self._guru = guru_client
        self._team_id: str | None = None
        self._views: dict[str, int] | None = None
        self._comments: dict[str, int] = {}
        self._modified: dict[str, str] = {}

    # ── verification freshness ──

    def verification_queue(self) -> list[dict]:
        """The verification-manager queue (cards due / overdue for review)."""
        if self._guru is None:
            return []
        return self._guru.list_unverified_cards()

    # ── demand (per-card views from the analytics event stream) ──

    def card_view_counts(self, from_date=None, to_date=None) -> dict[str, int]:
        """Per-card view counts: ``card-viewed`` events grouped by cardId.

        Cached after the first call — the window is fixed for one pass.
        """
        if self._views is not None:
            return self._views
        if self._guru is None:
            self._views = {}
            return self._views
        self._views = self._aggregate_views(from_date, to_date)
        return self._views

    def _aggregate_views(self, from_date, to_date) -> dict[str, int]:
        team_id = self._get_team_id()
        if not team_id:
            return {}
        events = self._guru.get_analytics(team_id, from_date, to_date) or []
        counts: dict[str, int] = {}
        for ev in events:
            card_id = _viewed_card_id(ev)
            if card_id:
                counts[card_id] = counts.get(card_id, 0) + 1
        return counts

    def _get_team_id(self) -> str:
        if self._team_id is None:
            self._team_id = self._guru.get_team_id() or ""
        return self._team_id

    # ── open comments ──

    def open_comment_count(self, card_id: str) -> int:
        """Number of open comments on a card (cached per card)."""
        if not card_id or self._guru is None:
            return 0
        if card_id in self._comments:
            return self._comments[card_id]
        comments = self._guru.get_card_comments(card_id, status="open") or []
        count = len(comments)
        self._comments[card_id] = count
        return count

    # ── last modified ──

    def last_modified(self, card_id: str) -> str:
        """The card's ``lastModified`` timestamp (cached per card)."""
        if not card_id or self._guru is None:
            return ""
        if card_id in self._modified:
            return self._modified[card_id]
        card = self._guru.get_card(card_id) or {}
        value = card.get("lastModified", "")
        self._modified[card_id] = value
        return value


def _viewed_card_id(event) -> str:
    """Extract the cardId from a ``card-viewed`` analytics event, else ""."""
    if not isinstance(event, dict) or event.get("type") != "card-viewed":
        return ""
    props = event.get("properties") or {}
    return props.get("cardId", "") if isinstance(props, dict) else ""
