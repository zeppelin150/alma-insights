"""Operator identity — the single source of truth for "who is using Renn".

Phase 1.5 (M1). Every milestone that needs "who am I" imports from here rather
than re-deriving it, so the precedence rules live in exactly one place.

Precedence for the operator's email:
  1. the explicit Settings override (``enablement.operator_email``), else
  2. the cached email last detected from the connected Google account
     (``enablement.detected_email``), else
  3. a live lookup against the connected Google account (main process only),
     when ``enablement.identity_auto_detect`` is on.

Identity is a *convenience scope* for the "only mine" task filter — NOT an
authorization boundary. It touches only settings + the Guru/Asana/Drive live
APIs; no PHI/ticket/warehouse reads (fully decoupled).
"""
from __future__ import annotations

from src.data.settings_manager import get_section, update_section


def _cfg() -> dict:
    return get_section("enablement", {}) or {}


def operator_email(*, resolve: bool = True) -> str | None:
    """The operator's email, or None if unknown.

    ``resolve=False`` skips the (network) Google fallback — use it on hot paths
    like per-turn chat context where a cached/settings value is enough.
    """
    cfg = _cfg()
    override = (cfg.get("operator_email") or "").strip()
    if override:
        return override
    cached = (cfg.get("detected_email") or "").strip()
    if cached:
        return cached
    if resolve and cfg.get("identity_auto_detect", True):
        return _detect_and_cache()
    return None


def _detect_and_cache() -> str | None:
    """Resolve the email from the connected Google account and cache it.

    Main process only (``google_oauth`` refuses in the subprocess). Returns None
    when Google isn't connected this session or the lookup fails.
    """
    try:
        from src.data import google_oauth
        email = google_oauth.fetch_account_email()
    except Exception:  # noqa: BLE001 — subprocess guard / lib missing → unknown
        return None
    if email:
        set_detected_email(email)
    return email or None


def operator_name() -> str:
    return (_cfg().get("operator_name") or "").strip()


def operator_asana_gid() -> str:
    return (_cfg().get("operator_asana_gid") or "").strip()


def operator_identity() -> dict:
    """``{email, name, asana_gid, source}`` for display/logging (no network)."""
    cfg = _cfg()
    override = (cfg.get("operator_email") or "").strip()
    if override:
        email, source = override, "settings"
    else:
        email = (cfg.get("detected_email") or "").strip()
        source = "google" if email else "unset"
    return {
        "email": email,
        "name": (cfg.get("operator_name") or "").strip(),
        "asana_gid": (cfg.get("operator_asana_gid") or "").strip(),
        "source": source,
    }


def operator_context_line() -> str:
    """A one-line, per-turn context string naming the operator (or '' if unknown)
    so ANY Renn surface can answer 'who am I'. Read-only, no network."""
    idn = operator_identity()
    email = (idn.get("email") or "").strip()
    name = (idn.get("name") or "").strip()
    if not email and not name:
        return ""
    who = email or name
    tail = f" ({name})" if (name and email) else ""
    return (f"[OPERATOR] You are assisting {who}{tail}. If they ask who they are or "
            f"to confirm their identity, answer with this.")


def set_operator_email(email: str) -> None:
    update_section("enablement", {"operator_email": (email or "").strip()})


def set_operator_name(name: str) -> None:
    update_section("enablement", {"operator_name": (name or "").strip()})


def set_operator_asana_gid(gid: str) -> None:
    update_section("enablement", {"operator_asana_gid": (gid or "").strip()})


def set_detected_email(email: str) -> None:
    update_section("enablement", {"detected_email": (email or "").strip()})


def is_mine(assignee: str | None, *, assignee_gid: str | None = None) -> bool:
    """True when a task's assignee is the operator.

    Matches on the Asana GID first (unambiguous), then falls back to a
    case-insensitive match of the operator's email OR display name against the
    assignee TEXT (Asana ingestion stores the display NAME, not an email/gid).
    Returns False when the operator identity is unset — the UI filter treats an
    unknown identity as "show all" rather than calling is_mine at all.
    """
    gid = operator_asana_gid()
    if gid and assignee_gid and gid == assignee_gid:
        return True
    ident = operator_identity()
    needles = {v.strip().lower() for v in (ident["email"], ident["name"]) if v.strip()}
    if not needles or not assignee:
        return False
    return assignee.strip().lower() in needles
