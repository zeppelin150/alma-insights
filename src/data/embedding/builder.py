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

_DEFAULT_BATCH_SIZE = 64    # used when no hardware profile is available
_RAW_BODY_MAX_CHARS = 8192  # lean into Qwen3's long context; multi-turn threads
_COMMENTS_LIMIT = 8          # was 3 — allow more back-and-forth per ticket

# Qwen3 supports 32k tokens but attention is quadratic in seq length. 2048 tokens
# holds our 8192-char bodies with headroom (~3.5-4 chars/token) and keeps CPU
# inference tractable. Bump this together with _RAW_BODY_MAX_CHARS when changing.
_DEFAULT_MAX_SEQ_LENGTH = 2048


def _resolve_batch_and_seq() -> tuple[int, int]:
    """Read embedding_batch_size + embedding_max_seq_length from the
    cached hardware profile. Falls back to the documented defaults
    (batch=64, max_seq=2048) if the profile is missing or malformed."""
    try:
        import json
        from pathlib import Path
        path = Path("data") / "hardware_profile.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            batch = int(data.get("embedding_batch_size", _DEFAULT_BATCH_SIZE))
            max_seq = int(data.get("embedding_max_seq_length", _DEFAULT_MAX_SEQ_LENGTH))
            return max(1, batch), max(128, max_seq)
    except Exception:
        pass
    return _DEFAULT_BATCH_SIZE, _DEFAULT_MAX_SEQ_LENGTH

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


def build_embeddings(conn, force: bool = False, composition_mode: str = "raw_body") -> int:
    """Build embeddings for all tickets, skipping unchanged ones.

    Args:
        conn: SQLite connection.
        force: If True, re-embed everything (ignore hashes).
        composition_mode: one of
            - "raw_body" (default, Phase 2): subject + first N comments, PHI-redacted.
              Zero LLM influence. Grounded in user-written content.
            - "composite_A": structured header with Gemini's NLP labels
              (trc / friction_type / sub_cluster) + subject + first 2 comments.
              Uses LLM abstraction as a concept anchor; raw body grounds truth.
            - "composite_C": subject repeated 3× + compact LLM label line + 2 comments.
              Subject-weighted variant; tests whether subject alone carries
              concept signal.
        Hash includes the composition_mode, so switching modes force-re-embeds.

    Returns:
        Count of embeddings written.
    """
    from src.data.embedding.model_loader import is_available
    if not is_available():
        logger.warning("Embedding model not available, skipping build")
        return 0

    tickets = _load_ticket_texts(conn, composition_mode=composition_mode)
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

    # Resolve batch + max_seq from the cached hardware profile so M1/M2,
    # CUDA, and CPU all get appropriate values without code changes.
    batch_size, max_seq_length = _resolve_batch_and_seq()

    # Cap model.max_seq_length before encoding — otherwise sentence-transformers
    # pads each batch to the model's 32k default, killing CPU throughput.
    from src.data.embedding.model_loader import get_model
    _m = get_model()
    if _m is not None and getattr(_m, "max_seq_length", 0) > max_seq_length:
        logger.info("Capping model.max_seq_length %s -> %d",
                     _m.max_seq_length, max_seq_length)
        _m.max_seq_length = max_seq_length
    # Single log line that captures everything support tickets ask for:
    # device, batch size, max seq length. Grep target.
    if _m is not None:
        device = getattr(_m, "device", "unknown")
        logger.info(
            "Embedding pipeline: device=%s  batch=%d  max_seq=%d",
            device, batch_size, max_seq_length,
        )

    # Encode in chunks so progress is visible in the log instead of one
    # 30+ minute black-box call. Chunk size is many batches' worth so the
    # per-chunk encode-call overhead stays negligible.
    from src.data.embedding.encoder import embed_documents
    import time as _time
    texts = [t[1] for t in to_embed]
    chunk_size = max(batch_size * 4, 128)   # ~4 batches per progress tick
    pieces: list = []
    n_total = len(texts)
    n_chunks = (n_total + chunk_size - 1) // chunk_size
    t_start = _time.perf_counter()
    for ci, start in enumerate(range(0, n_total, chunk_size), 1):
        end = min(start + chunk_size, n_total)
        t0 = _time.perf_counter()
        chunk_emb = embed_documents(texts[start:end], batch_size=batch_size)
        pieces.append(chunk_emb)
        elapsed = _time.perf_counter() - t_start
        chunk_dt = _time.perf_counter() - t0
        rate = end / elapsed if elapsed else 0.0
        eta = (n_total - end) / rate if rate else 0.0
        logger.info(
            "Embedding chunk %d/%d  (tickets %d-%d, %.1fs)  total %d/%d  "
            "rate %.1f tk/s  ETA %.0fs",
            ci, n_chunks, start, end, chunk_dt, end, n_total, rate, eta,
        )
    embeddings = np.vstack(pieces)

    written = _write_embeddings(conn, to_embed, embeddings)
    conn.commit()
    logger.info("Wrote %d embeddings", written)
    return written


# ──────────────────────────────────────────────────────────────────────
# source text loading (Phase 2: raw body primary, composed text fallback)
# ──────────────────────────────────────────────────────────────────────

def _load_ticket_texts(conn, composition_mode: str = "raw_body") -> list[tuple[str, str, str]]:
    """Load (ticket_id, source_text, source_text_hash) tuples for every ticket.

    Three composition modes:
    - "raw_body" (default): subject + first N comments, PHI-redacted.
    - "composite_A": structured [trc][friction][sub_cluster] header + raw body.
    - "composite_C": subject×3 + compact label line + 2 comments.

    For composite modes, pulls the latest sub_cluster from
    nlp_ticket_classifications per ticket.
    """
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

    # For composite modes, fetch latest sub_cluster per ticket from NLP output.
    # Falls back gracefully if the classification table is empty.
    nlp_labels = _load_nlp_labels(conn) if composition_mode != "raw_body" else {}

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

        # Prefer NLP-table sub_cluster (richer than ticket_index.sub_pattern);
        # fall back to ticket_index columns.
        nlp_sub = nlp_labels.get(tid, {}).get("sub_cluster") or sub
        nlp_friction = nlp_labels.get(tid, {}).get("friction_type") or friction

        if composition_mode == "composite_A":
            source_text = _compose_composite_A(
                subject=subject, full_thread=full_thread,
                thread_preview=thread_preview, trc=trc,
                friction_type=nlp_friction, sub_cluster=nlp_sub,
            )
            source_kind = "composite_A"
        elif composition_mode == "composite_C":
            source_text = _compose_composite_C(
                subject=subject, full_thread=full_thread,
                thread_preview=thread_preview, trc=trc,
                friction_type=nlp_friction, sub_cluster=nlp_sub,
            )
            source_kind = "composite_C"
        else:
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


def _load_nlp_labels(conn) -> dict[str, dict]:
    """Return {ticket_id: {sub_cluster, friction_type}} from most recent NLP row."""
    try:
        rows = conn.execute(
            """SELECT ticket_id, sub_cluster, friction_type
                 FROM nlp_ticket_classifications
                ORDER BY rowid DESC"""
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        tid = r[0]
        if tid in out:
            continue   # keep first (most recent by rowid DESC)
        out[tid] = {"sub_cluster": r[1], "friction_type": r[2]}
    return out


def _compose_composite_A(*, subject, full_thread, thread_preview,
                          trc, friction_type, sub_cluster) -> str:
    """Variant A: structured LLM header + raw body."""
    header_parts = []
    if trc: header_parts.append(f"[trc: {trc}]")
    if friction_type: header_parts.append(f"[friction: {friction_type}]")
    if sub_cluster: header_parts.append(f"[issue: {sub_cluster}]")
    header = " ".join(header_parts)

    # Use the existing raw-body composer but with a tighter cap (leave room for header)
    raw = _compose_raw_body(subject, full_thread, thread_preview)
    if not raw:
        # If no body at all, use header alone
        return header or ""
    # Shorter body in composite mode so header has proportional weight
    if len(raw) > 1500:
        raw = raw[:1500]
    if header:
        return f"{header}\n\n{raw}"
    return raw


def _compose_composite_C(*, subject, full_thread, thread_preview,
                          trc, friction_type, sub_cluster) -> str:
    """Variant C: subject repeated 3x + compact LLM line + 2 comments."""
    subject_s = (subject or "").strip()
    label_bits = [x for x in (trc, friction_type, sub_cluster) if x]
    label_line = " / ".join(label_bits) if label_bits else ""

    parts: list[str] = []
    if subject_s:
        parts.append(subject_s)
        parts.append(subject_s)
        parts.append(subject_s)
    if label_line:
        parts.append(f"[{label_line}]")
    # Short body: first 2 comments, ~1200 chars, redacted
    body_text = _compose_raw_body(subject=None, full_thread=full_thread,
                                   thread_preview=thread_preview)
    if body_text:
        if len(body_text) > 1200:
            body_text = body_text[:1200]
        parts.append(body_text)
    return "\n\n".join(parts)


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
