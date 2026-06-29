"""Stage 3 — pull the matched card into a linked, editable draft.

Reuses ``enablement_store.import_guru_card_to_draft`` which converts the card
HTML to markdown AND links the draft to the card id, so the eventual publish
takes the UPDATE branch (never silently creating a duplicate card).
"""

from __future__ import annotations

from typing import Any

from .models import CardMatch, PulledCard


def pull_target_card(conn, guru_client: Any, match: CardMatch) -> PulledCard:
    """Import the matched card and return its current markdown + draft id."""
    from src.data import enablement_store as store
    res = store.import_guru_card_to_draft(conn, guru_client, match.card_id)
    if not res.get("ok"):
        return PulledCard(ok=False, error=res.get("error", "import_failed"))
    return PulledCard(
        ok=True,
        draft_id=res["draft_id"],
        card_id=res["card_id"],
        title=res.get("title", ""),
        current_md=res.get("content", ""),
    )


def fetch_card_markdown(guru_client: Any, card_ref) -> tuple[str, str] | None:
    """Fetch a card's (title, current_markdown) WITHOUT creating a draft.

    Used by fan-out to compare a source against many cards cheaply — a draft is
    only created (via pull_target_card) for the cards that actually change.
    """
    from src.data.enablement_store import parse_guru_card_ref
    from src.data.html_markdown import html_to_markdown
    try:
        card = guru_client.get_card(parse_guru_card_ref(str(card_ref or "")))
    except Exception:  # noqa: BLE001 — unresolved card
        return None
    if not card or not card.get("id"):
        return None
    return card.get("title", ""), html_to_markdown(card.get("content", ""))
