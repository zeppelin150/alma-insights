"""Effectiveness feedback for a card: its update history + Guru-native impact.

FULLY DECOUPLED: the measured impact is a Guru-native proxy (per-card view +
open-comment deltas captured at publish and re-pulled after a window — see
``effectiveness_proxy``), NOT ticket volume. We never read a ticket / RCM /
warehouse table here. Until a baseline accrues a followup, the push is simply
*anchored* (via the provenance pushed_at) and reported as pending. All
de-identified: counts and deltas only, no ticket text.
"""

from __future__ import annotations

from . import provenance


def card_effectiveness(conn, card_id: str, *, limit: int = 20) -> dict:
    updates = provenance.history(conn, card_id, limit=limit)
    proxy = _proxy(conn, card_id)
    # ``measured`` retained as a stable (always-empty) key for tolerant callers
    # that predate the decoupling: the old ticket-volume measure is gone — impact
    # now lives under ``proxy`` (Guru-native, no warehouse read).
    return {"ok": True, "card_id": card_id, "updates": updates,
            "proxy": proxy, "measured": [], "note": _note(updates, proxy)}


def _proxy(conn, card_id: str) -> dict | None:
    """Latest captured proxy deltas for the card (Guru-native, no tickets).

    Reads the baseline + newest followup snapshots straight from
    ``card_signal_snapshot`` — no live Guru call, no warehouse. Returns None
    when there is no baseline or no followup yet (impact still pending).
    """
    try:
        base = conn.execute(
            "SELECT view_count, open_comment_count, captured_at FROM card_signal_snapshot "
            "WHERE card_id=? AND phase='baseline' ORDER BY captured_at DESC LIMIT 1",
            (card_id,)).fetchone()
        follow = conn.execute(
            "SELECT view_count, open_comment_count, captured_at FROM card_signal_snapshot "
            "WHERE card_id=? AND phase='followup' ORDER BY captured_at DESC LIMIT 1",
            (card_id,)).fetchone()
    except Exception:  # noqa: BLE001 — table absent / not yet computed
        return None
    if not base or not follow:
        return None
    return {"views_delta": int(follow["view_count"]) - int(base["view_count"]),
            "comments_resolved": int(base["open_comment_count"]) - int(follow["open_comment_count"]),
            "baseline_at": base["captured_at"], "followup_at": follow["captured_at"]}


def _note(updates: list[dict], proxy: dict | None) -> str:
    if not updates:
        return "No recorded updates for this card."
    published = [u for u in updates if u.get("status") == "published"]
    if proxy:
        return (f"Guru-native impact: views {_signed(proxy['views_delta'])}, "
                f"{proxy['comments_resolved']} comment(s) resolved since publish. "
                f"{len(published)} published update(s).")
    if published:
        return (f"{len(published)} published update(s); Guru-native impact is measured once "
                f"a post-publish window accrues (anchored {published[0].get('pushed_at')}).")
    return f"{len(updates)} staged update(s), not yet published."


def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)
