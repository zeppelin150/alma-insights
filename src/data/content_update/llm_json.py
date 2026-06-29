"""Call an LLM and parse a validated JSON object, with bounded retry.

Small models (Haiku) occasionally fence JSON wrong or omit a field. This helper
extracts the JSON object, validates its shape, and — on failure — re-asks ONCE
per remaining attempt with the specific error appended, then gives up cleanly.
No infinite loop; the caller turns a failure into a staged-with-issues result.
"""

from __future__ import annotations

import json
from typing import Any, Callable


def call_llm_json(client: Any, system: str, prompt: str,
                  validate: Callable[[dict], str], *, retries: int = 2) -> dict:
    """Return ``{"ok": True, "data": {...}}`` or ``{"ok": False, "error": "..."}``.

    ``validate(data)`` returns "" when the parsed object is acceptable, else a
    short error string used both to reject and to coach the retry.
    """
    attempt_prompt = prompt
    last_err = "llm_json_failed"
    for _ in range(max(1, retries + 1)):
        text = client.generate(attempt_prompt, system) if system else client.generate(attempt_prompt)
        data, parse_err = _extract_json(text)
        if parse_err:
            last_err = parse_err
        else:
            verr = validate(data)
            if not verr:
                return {"ok": True, "data": data}
            last_err = verr
        attempt_prompt = (
            f"{prompt}\n\nYour previous reply was rejected: {last_err}\n"
            "Return ONLY a single valid JSON object, fenced in ```json."
        )
    return {"ok": False, "error": last_err}


def _extract_json(text: str) -> tuple[dict, str]:
    """Pull the first JSON OBJECT out of ``text`` (fenced or bare).

    Tries the fenced block, then a whole-string parse, then a brace scan that is
    string-literal aware (via ``json.raw_decode``) so a ``}`` inside a quoted
    value doesn't truncate the object. Top-level arrays are rejected (not an
    object), never silently sliced to their first element.
    """
    raw = (text or "").strip()
    if not raw:
        return {}, "empty_response"
    for candidate in (_between(raw, "```json", "```"), _between(raw, "```", "```"), raw):
        if not candidate:
            continue
        obj = _loads_dict(candidate.strip())
        if obj is not None:
            return obj, ""
    # A whole-string parse to a non-dict (e.g. a top-level array) is NOT the
    # object we asked for — reject it rather than slicing its first element out.
    if _parses_to_nondict(raw):
        return {}, "json_not_an_object"
    obj = _scan_object(raw)
    if obj is not None:
        return obj, ""
    return {}, ("invalid_json" if "{" in raw else "no_json_object_found")


def _between(text: str, open_tok: str, close_tok: str) -> str:
    start = text.find(open_tok)
    if start == -1:
        return ""
    start += len(open_tok)
    end = text.find(close_tok, start)
    return text[start:end].strip() if end != -1 else ""


def _loads_dict(s: str) -> dict | None:
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parses_to_nondict(s: str) -> bool:
    """True if the whole string parses as valid JSON that is not an object."""
    try:
        return not isinstance(json.loads(s), dict)
    except json.JSONDecodeError:
        return False


def _scan_object(text: str) -> dict | None:
    """First substring that decodes to a JSON object, ``}``-in-string safe."""
    decoder = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        i = text.find("{", i + 1)
    return None
