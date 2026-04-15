"""Incremental embedding builder (Phase 2 — raw-body composition).

Computes Qwen3 embeddings for tickets that are new or changed,
persists to ticket_embeddings table with hash-based skip.

Phase 2 change (2026-04-14, canonicalization-enrichment plan):

Source text was previously a composite of PHI-free classification fields
(`trc_code + friction_type + sub_pattern + issue_snippet`). That coupled the
embedding space to the LLM's label choice — a cluster of tickets the classifier
happened to tag identically would embed identically, even if the underlying
content was divergent. This biases canonicalization toward the labels we're
trying to canonicalize.

The new composition reads the raw conversation body (subject + first 3
comments, PHI-redacted via `RedactionEngine.scrub`), truncated to 2048 chars.
Falls back to the old classification-field composition when no conversation
body is available (e.g. for tickets visible only via ticket_index without a
matching conversations row yet).

Module deps: numpy, stdlib (hashlib, logging, datetime); RedactionEngine
Module dependents: canonicalization engine (Phase 3+), report generation
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64           # doubled — CPU inference amortizes well up to 64-128
_RAW_BODY_MAX_CHARS = 8192  # lean into Qwen3's long context; multi-turn threads
_COMMENTS_LIMIT = 8          # was 3 — allow more back-and-forth per ticket

# Qwen3 supports 32k tokens but attention is quadratic in seq length. 2048 tokens
# holds our 8192-char bodies with headroom (~3.5-4 chars/token) and keeps CPU
# inference tractable. Bump this together with _RAW_BODY_MAX_CHARS when changing.
_MAX_SEQ_LENGTH = 2048

# Cache redaction engine at module level — construction parses JSON files.
_redactor = None


def _get_redactor():
    global _redactor
    if _redactor is None:
        try:
            from src.data.redaction_engine import RedactionEngine
            _redactor = RedactionEngine()
        except Exception as exc:   # noqa: BLE001 — non-fatal fallback
            logger.warning("RedactionEngine unavailable (%s); raw bodies will "
                           "be passed through WITHOUT redaction. This is a "
                           "HIPAA risk outside the Gemini CLI boundary.", exc)
            _redactor = False  # sentinel: tried and failed
    return _redactor


def build_embeddings(conn, force: bool = False) -> int:
    """Build embeddings for all tickets, skipping unchanged ones.

    Args:
        conn: SQLite connection.
        force: If True, re-embed everything (ignore hashes).

    Returns:
        Count of embeddings written.
    """
    from src.data.embedding.model_loader import is_available
    if not is_available():
        logger.warning("Embedding model not available, skipping build")
        return 0

    tickets = _load_ticket_texts(conn)
    if not tickets:
        logger.info("No tickets to embed")
        return 0

    existing_hashes = _load_existing_hashes(conn) if not force else {}

    to_embed = []
    for tid, text, text_hash in tickets:
        if not force and existing_hashes.get(tid) == text_hash:
            continue
        to_embed.append((tid, text, text_hash))

    if not to_embed:
        logger.info("All embeddings up to date, nothing to embed")
        return 0

    logger.info("Embedding %d tickets (of %d total)", len(to_embed), len(tickets))

    # Cap model.max_seq_length before encoding — otherwise sentence-transformers
    # pads each batch to the model's 32k default, killing CPU throughput.
    from src.data.embedding.model_loader import get_model
    _m = get_model()
    if _m is not None and getattr(_m, "max_seq_length", 0) > _MAX_SEQ_LENGTH:
        logger.info("Capping model.max_seq_length %s -> %d",
                     _m.max_seq_length, _MAX_SEQ_LENGTH)
        _m.max_seq_length = _MAX_SEQ_LENGTH

    texts = [t[1] for t in to_embed]
    from src.data.embedding.encoder import embed_documents
    embeddings = embed_documents(texts, batch_size=_BATCH_SIZE)

    written = _write_embeddings(conn, to_embed, embeddings)
    conn.commit()
    logger.info("Wrote %d embeddings", written)
    return written


# ──────────────────────────────────────────────────────────────────────
# source text loading (Phase 2: raw body primary, composed text fallback)
# ──────────────────────────────────────────────────────────────────────

def _load_ticket_texts(conn) -> list[tuple[str, str, str]]:
    """Load (ticket_id, source_text, source_text_hash) tuples for every ticket.

    Prefers raw body (subject + first 3 comment bodies, PHI-redacted,
    truncated). Falls back to composed classification text when no
    conversation body is available.
    """
    # Pull classification fields + conversation body in one query — LEFT JOIN
    # so tickets without conversation rows still appear (and fall back).
    # We try the shared `conversations` table first; per-source tables are
    # searched only if the shared table has no matches (covered by the
    # multi-source registry lookup elsewhere in the pipeline).
    rows = conn.execute(
        """
        SELECT ti.ticket_id,
               ti.trc_code, ti.friction_type, ti.sub_pattern, ti.issue_snippet,
               c.subject, c.full_thread, c.thread_preview
          FROM ticket_index ti
     LEFT JOIN conversations c ON c.ticket_id = ti.ticket_id
        """
    ).fetchall()

    # If the shared conversations lookup is empty (legacy / per-source only),
    # augment with a best-effort sweep of per-source conversation tables.
    per_source_bodies = _load_per_source_bodies(conn) if rows else {}

    result: list[tuple[str, str, str]] = []
    for r in rows:
        tid = r[0]
        trc, friction, sub, snippet = r[1], r[2], r[3], r[4]
        subject = r[5]
        full_thread = r[6]
        thread_preview = r[7]

        # Try per-source override if shared is empty
        if not full_thread and tid in per_source_bodies:
            subject, full_thread = per_source_bodies[tid]

        raw_text = _compose_raw_body(subject, full_thread, thread_preview)
        if raw_text:
            source_text = raw_text
            source_kind = "raw_body"
        else:
            source_text = _compose_classification_text(trc, friction, sub, snippet)
            source_kind = "composed_fallback"

        text_hash = hashlib.sha256(
            (source_kind + "|" + source_text).encode("utf-8")
        ).hexdigest()[:16]
        result.append((tid, source_text, text_hash))
    return result


def _load_per_source_bodies(conn) -> dict[str, tuple[str, str]]:
    """Best-effort sweep of per-source `{prefix}_conversations` tables.

    Returns {ticket_id: (subject, full_thread)} for tickets only present in
    per-source tables (not the shared `conversations`).
    """
    out: dict[str, tuple[str, str]] = {}
    try:
        prefixes = [
            r[0] for r in conn.execute(
                "SELECT table_prefix FROM source_registry"
            ).fetchall()
        ]
    except Exception:
        return out

    for prefix in prefixes:
        table = f"{prefix}_conversations"
        try:
            rows = conn.execute(
                f"SELECT ticket_id, subject, full_thread FROM [{table}]"
            ).fetchall()
        except Exception:
            continue
        for r in rows:
            tid = r[0]
            if tid not in out and r[2]:
                out[tid] = (r[1] or "", r[2])
    return out


def _compose_raw_body(subject, full_thread, thread_preview) -> str:
    """Assemble PHI-redacted raw body text, truncated to 2048 chars.

    Returns empty string when no usable body is available.
    """
    parts: list[str] = []
    if subject:
        s = str(subject).strip()
        if s:
            parts.append(s)

    body = full_thread or thread_preview
    if not body:
        if not parts:
            return ""
        text = parts[0]
    else:
        text = str(body).strip()
        # Extract first N comment blocks. Threads are separated by "---" per
        # csv_ingestion convention; split and take first N non-empty.
        blocks = [b.strip() for b in text.split("\n\n---\n\n") if b.strip()]
        if blocks:
            text = "\n\n".join(blocks[:_COMMENTS_LIMIT])
        if parts:
            text = parts[0] + "\n\n" + text

    if not text:
        return ""

    # PHI scrub before truncation so we don't chop mid-token.
    redactor = _get_redactor()
    if redactor:
        try:
            text = redactor.scrub(text)
        except Exception as exc:   # noqa: BLE001 — safer to emit empty than raw PHI
            logger.error("Redaction failed: %s; skipping ticket body", exc)
            return ""

    if len(text) > _RAW_BODY_MAX_CHARS:
        text = text[:_RAW_BODY_MAX_CHARS]

    return text


def _compose_classification_text(trc, friction, sub, snippet) -> str:
    """Legacy fallback composition (kept for tickets without body content)."""
    parts = []
    if trc:
        parts.append(f"Category: {trc}")
    if friction:
        parts.append(f"Type: {friction}")
    if sub:
        parts.append(f"Pattern: {sub}")
    if snippet:
        parts.append(str(snippet))
    return ". ".join(parts) if parts else "No description"


# Deprecated alias — external callers may still import it. Keep but route.
def _compose_text(trc: str, friction: str, sub: str, snippet: str) -> str:
    """DEPRECATED: classification-only composition. Use raw body path instead."""
    return _compose_classification_text(trc, friction, sub, snippet)


# ──────────────────────────────────────────────────────────────────────
# persistence
# ──────────────────────────────────────────────────────────────────────

def _load_existing_hashes(conn) -> dict[str, str]:
    """Load existing ticket_id → source_text_hash mapping."""
    rows = conn.execute(
        "SELECT ticket_id, source_text_hash FROM ticket_embeddings"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _write_embeddings(
    conn, tickets: list[tuple[str, str, str]], embeddings: np.ndarray,
) -> int:
    """Write embeddings to ticket_embeddings table."""
    from src.data.embedding.model_loader import EMBEDDING_DIM, MODEL_NAME
    now = datetime.now(timezone.utc).isoformat()
    written = 0

    for i, (tid, _text, text_hash) in enumerate(tickets):
        vec = embeddings[i]
        blob = vec.astype(np.float32).tobytes()
        conn.execute(
            "INSERT OR REPLACE INTO ticket_embeddings "
            "(ticket_id, embedding_blob, source_text_hash, "
            "model_name, dim_size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, blob, text_hash, MODEL_NAME, EMBEDDING_DIM, now),
        )
        written += 1

    return written
