"""KB index-card format v1 (WS2-M2, renn-calendar-kb-studio plan).

A card is a Markdown file with YAML frontmatter living in the operator's EC
Drive folder — the HUMAN-READABLE cloud index. Humans may hand-edit cards, so
the parser is TOLERANT by contract: broken/missing YAML never raises (the card
degrades to body-only + a ``needs_repair`` issue), unknown keys are preserved
verbatim on round-trip, and a YAML bomb is stopped by a size cap.

Frontmatter v1 keys (all optional at parse; serialize writes them all):
  card_id (kb-<uuid8>, stable), schema_version (1), title,
  type (source_summary|research_note|decision|glossary|faq|update_diff|
        published_card),
  topics [slugs], source_id (Drive fileId | guru:<id> | asana:<gid> | ''),
  source_url, source_mime, source_modified (RFC3339 of the SOURCE at summarize
  time — the staleness key), summary (<=2 sentences), key_facts [str],
  entities [str], provenance {created_by, job_id, model, created_at, updated_at}

Filename convention: ``<slug(title)>--<card_id>.md`` — the card_id half
survives a human renaming the title half.
"""

from __future__ import annotations

import re
import uuid

from src.data.artifact_store import slugify

SCHEMA_VERSION = 1
CARD_TYPES = frozenset({
    "source_summary", "research_note", "decision", "glossary", "faq",
    "update_diff", "published_card",
})

_MAX_CARD_CHARS = 1_000_000          # YAML-bomb / runaway-file guard
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_FILENAME_ID_RE = re.compile(r"--(kb-[0-9a-f]{8})\.md\Z")

_V1_KEYS = ("card_id", "schema_version", "title", "type", "topics",
            "source_id", "source_url", "source_mime", "source_modified",
            "summary", "key_facts", "entities", "provenance")


def new_card_id() -> str:
    return f"kb-{uuid.uuid4().hex[:8]}"


def card_filename(title: str, card_id: str) -> str:
    return f"{slugify(title or 'card')}--{card_id}.md"


def card_id_from_filename(name: str) -> str | None:
    m = _FILENAME_ID_RE.search(name or "")
    return m.group(1) if m else None


def extract_extra(meta: dict) -> dict:
    """The frontmatter keys that are NOT part of the v1 schema — a human's own
    additions. These are stored separately (kb_cards.extra_json) so they
    survive the DB round-trip and are merged back on the next Drive write."""
    known = set(_V1_KEYS)
    return {k: v for k, v in (meta or {}).items() if k not in known}


def serialize_card(meta: dict, body: str) -> str:
    """Frontmatter + body → the canonical card text. Unknown meta keys are
    written after the v1 keys so a human's additions round-trip."""
    import yaml
    out: dict = {}
    for key in _V1_KEYS:
        out[key] = meta.get(key)
    out["schema_version"] = SCHEMA_VERSION
    out.setdefault("topics", [])
    out.setdefault("key_facts", [])
    out.setdefault("entities", [])
    for key, value in (meta or {}).items():       # preserve unknown keys
        if key not in out:
            out[key] = value
    fm = yaml.safe_dump(out, sort_keys=False, allow_unicode=True,
                        default_flow_style=False)
    return f"---\n{fm}---\n\n{(body or '').strip()}\n"


def parse_card(text: str) -> tuple[dict, str, list[str]]:
    """Card text → (meta, body, issues). NEVER raises for content trouble.

    issues: 'too_large' (truncated), 'no_frontmatter', 'broken_frontmatter',
    'frontmatter_not_mapping' — any of which means the caller should flag the
    mirror row ``needs_repair`` (repair is FLAG-ONLY for human-created files).
    """
    import yaml
    issues: list[str] = []
    raw = text or ""
    if len(raw) > _MAX_CARD_CHARS:
        raw = raw[:_MAX_CARD_CHARS]
        issues.append("too_large")
    m = _FM_RE.match(raw)
    if not m:
        issues.append("no_frontmatter")
        return {}, raw.strip(), issues
    try:
        meta = yaml.safe_load(m.group(1))
    except Exception:  # noqa: BLE001 — hand-edited YAML
        issues.append("broken_frontmatter")
        return {}, raw[m.end():].strip(), issues
    if not isinstance(meta, dict):
        issues.append("frontmatter_not_mapping")
        return {}, raw[m.end():].strip(), issues
    # normalize the list-ish keys defensively (a human may write a string)
    for key in ("topics", "key_facts", "entities"):
        v = meta.get(key)
        if v is None:
            meta[key] = []
        elif isinstance(v, str):
            meta[key] = [v]
        elif not isinstance(v, list):
            meta[key] = [str(v)]
    return meta, raw[m.end():].strip(), issues
