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
    # Small models (Haiku) sometimes return `topics` as a comma-joined string or
    # omit/null it. Coerce to a list before iterating, else a string would be
    # walked character-by-character (garbage topics) and a scalar (int) would
    # raise TypeError and abort the whole index run.
    raw_topics = d.get("topics")
    if isinstance(raw_topics, str):
        raw_topics = raw_topics.split(",")
    elif not isinstance(raw_topics, list):
        raw_topics = []
    topics = [str(t).lower().strip() for t in raw_topics if str(t).strip()][:8]
    # `summary` may be null/non-string; str(None) is the truthy literal "None"
    # which would defeat the fallback, so reject anything but a real string.
    raw_summary = d.get("summary")
    summary = (raw_summary.strip() if isinstance(raw_summary, str) else "") or _fallback(body)["summary"]
    return {"summary": summary, "topics": topics}


def _validate(d: dict) -> str:
    return "" if "summary" in d else "missing 'summary' key"


def _fallback(body: str) -> dict:
    return {"summary": " ".join((body or "").split())[:240], "topics": []}
