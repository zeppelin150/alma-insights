"""LLM-built summary for a catalog item, with a deterministic fallback.

The summary is what makes look-alike items distinguishable by CONTENT and what
keeps the search surface tiny. Built by the active model (Haiku) when a client
is supplied; otherwise a deterministic excerpt is used (no LLM, for tests).
"""

from __future__ import annotations

_SYSTEM = "You summarize knowledge content for a search catalog. Output only JSON."


def summarize_item(llm_client, title: str, text: str, *, max_chars: int = 4000) -> dict:
    """Return ``{"summary": str, "topics": [str]}``."""
    body = (text or "")[:max_chars]
    if llm_client is None:
        return _fallback(body)
    from src.data.content_update.llm_json import call_llm_json
    prompt = (
        "Summarize this content for a SEARCH CATALOG in 1-2 sentences that capture what "
        "makes it DISTINCT from similarly-titled items, then list 3-6 lowercase topic "
        "keywords.\nReturn ONLY JSON: {\"summary\": \"...\", \"topics\": [\"...\"]}\n\n"
        f"TITLE: {title}\n---\n{body}\n"
    )
    res = call_llm_json(llm_client, _SYSTEM, prompt, _validate)
    if not res["ok"]:
        return _fallback(body)
    d = res["data"]
    topics = [str(t).lower().strip() for t in d.get("topics", []) if str(t).strip()][:8]
    summary = str(d.get("summary", "")).strip() or _fallback(body)["summary"]
    return {"summary": summary, "topics": topics}


def _validate(d: dict) -> str:
    return "" if "summary" in d else "missing 'summary' key"


def _fallback(body: str) -> dict:
    return {"summary": " ".join((body or "").split())[:240], "topics": []}
