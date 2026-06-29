"""Deterministic, offline pre-flight checks for proposed enablement content.

A small model-free check library shared by the content-update critic and the
review UI. Every check is a tiny pure function over the proposed markdown text;
none touch the network, the warehouse, or an LLM. Link checks are FORMAT-only
heuristics (never live HTTP), PII detection reuses the redaction engine as a
defense-in-depth badge, and style conformance only fires when a style block is
supplied.

Public API:

    run_checks(text, *, style_block="", card_id="") -> list[dict]

Each result is ``{"check": str, "status": "ok"|"warn"|"fail", "detail": str}``.
"""

from __future__ import annotations

import re

# Markdown link: [label](target). Bare URL: a token starting with a scheme.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
_BARE_URL = re.compile(r"(?<![(\w])\bhttps?://\S+", re.I)
_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s|$)")
_WORD = re.compile(r"[A-Za-z0-9']+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.M)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)\S", re.M)
_SHOUT = re.compile(r"\b[A-Z]{4,}\b")

_LONG_SENTENCE = 30.0   # mean words/sentence above this reads as dense

# Redaction tokens that signal genuine PII. [URL] is a link (handled by
# broken_links) and [NAME] is the noisy Title-Case heuristic — neither is a
# leak in product docs, so they do not raise the PII badge.
_PII_TOKENS = frozenset({
    "[EMAIL]", "[PHONE]", "[SSN]", "[CARD]", "[MEMBER_ID]", "[DOB]", "[ADDRESS]",
})


def _result(check: str, status: str, detail: str) -> dict:
    """Build one check result row."""
    return {"check": check, "status": status, "detail": detail}


# ── individual checks ────────────────────────────────────────────────

def broken_links(text: str) -> dict:
    """FORMAT-only link sanity: FAIL on malformed, WARN on bare URLs.

    Malformed = a link target with no scheme, embedded whitespace, or
    unbalanced parentheses. Bare (non-markdown) URLs are a soft WARN. Never
    performs any network access.
    """
    targets = [t.strip() for t in _MD_LINK.findall(text or "")]
    malformed = [t for t in targets if _is_malformed_link(t)]
    if malformed:
        return _result("broken_links", "fail",
                       f"malformed link target(s): {malformed}")
    if _BARE_URL.search(text or ""):
        return _result("broken_links", "warn",
                       "bare URL(s) — prefer [label](url) markdown links")
    return _result("broken_links", "ok", f"{len(targets)} markdown link(s) well-formed")


def _is_malformed_link(target: str) -> bool:
    """True when a markdown link target is structurally broken (offline check)."""
    if not target:
        return True
    if any(ch.isspace() for ch in target):
        return True
    if target.count("(") != target.count(")"):
        return True
    if target.startswith(("#", "/", "mailto:")):
        return False
    return "://" not in target


def ai_readability(text: str) -> dict:
    """Deterministic readability: mean words/sentence + structure presence.

    WARN when prose is dense (very long mean sentence) or wholly unstructured
    (no headings on a multi-line doc). The numeric score is returned in detail.
    """
    body = (text or "").strip()
    if not body:
        return _result("ai_readability", "ok", "empty text")
    mean = _mean_sentence_words(body)
    has_heading = bool(_HEADING.search(body))
    has_list = bool(_LIST_ITEM.search(body))
    detail = (f"mean {mean:.1f} words/sentence; "
              f"headings={'yes' if has_heading else 'no'}; "
              f"lists={'yes' if has_list else 'no'}")
    if mean > _LONG_SENTENCE:
        return _result("ai_readability", "warn", f"dense prose — {detail}")
    if not has_heading and body.count("\n") >= 2:
        return _result("ai_readability", "warn", f"no headings — {detail}")
    return _result("ai_readability", "ok", detail)


def _mean_sentence_words(body: str) -> float:
    """Average word count per sentence (>=1 so an unpunctuated blob scores)."""
    sentences = [s for s in _SENTENCE_SPLIT.split(body) if s.strip()]
    words = _WORD.findall(body)
    return len(words) / max(1, len(sentences))


def pii_scan(text: str) -> dict:
    """FAIL when redaction-worthy PII survives in the text (defense-in-depth).

    Reuses ``RedactionEngine`` so the exact config patterns (emails, phones,
    SSNs, member ids) drive detection. Enablement docs should never carry a
    leak; a clean doc is unchanged by scrubbing.
    """
    body = text or ""
    if not body.strip():
        return _result("pii_scan", "ok", "empty text")
    tokens = _redacted_tokens(body)
    if tokens:
        return _result("pii_scan", "fail", f"PII-looking content: {sorted(tokens)}")
    return _result("pii_scan", "ok", "no PII detected")


def _redacted_tokens(body: str) -> set[str]:
    """Return the genuine-PII redaction tokens the engine injects ([EMAIL]…).

    URL/NAME tokens are filtered out — a link or Title-Case pair is not a leak
    in product docs; emails/phones/SSNs/member-ids/etc. are.
    """
    from src.data.redaction_engine import RedactionEngine

    scrubbed = RedactionEngine().scrub(body)
    found = set(re.findall(r"\[[A-Z_]+\]", scrubbed))
    return found & _PII_TOKENS


def style_conformance(text: str, style_block: str) -> dict:
    """Light deterministic style heuristics; skipped when no style block.

    Only meaningful when a style guide is in play. Flags ALL-CAPS shouting,
    a missing heading, and non-markdown (bare) links as soft WARNs.
    """
    if not (style_block or "").strip():
        return _result("style_conformance", "ok", "no style guide configured")
    body = (text or "").strip()
    if not body:
        return _result("style_conformance", "ok", "empty text")
    problems = _style_problems(body)
    if problems:
        return _result("style_conformance", "warn", "; ".join(problems))
    return _result("style_conformance", "ok", "consistent with style heuristics")


def _style_problems(body: str) -> list[str]:
    """Collect style nits (each a short phrase) for a non-empty body."""
    problems: list[str] = []
    if _SHOUT.search(body):
        problems.append("ALL-CAPS shouting")
    if not _HEADING.search(body):
        problems.append("no markdown headings")
    if _BARE_URL.search(body):
        problems.append("bare (non-markdown) links")
    return problems


# ── aggregator ───────────────────────────────────────────────────────

def run_checks(text: str, *, style_block: str = "", card_id: str = "") -> list[dict]:
    """Run every check and return their result rows (order is stable).

    ``card_id`` is accepted for parity with callers that scope a check to a
    card; it is currently unused by the format-only checks. Safe on empty or
    whitespace-only ``text`` — each check guards that case itself.
    """
    body = text or ""
    return [
        broken_links(body),
        ai_readability(body),
        pii_scan(body),
        style_conformance(body, style_block),
    ]
