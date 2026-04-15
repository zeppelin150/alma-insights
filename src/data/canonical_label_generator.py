"""Canonical label generator — Phase 5 of canonicalization-enrichment.md.

Generates a stable, human-readable `canonical_label` for every row in
`canonical_clusters` using a tiered pipeline:

    1. LLM (batched Gemini call, one per scan) for new / drifted clusters
    2. Extractive fallback (TF-IDF over cluster member bodies + key_phrases)
    3. Medoid fallback (representative ticket's sub_cluster string)

Each tier is guarded by a 4-layer validator:

    L1 — schema: 3-8 words, noun phrase, no trailing punctuation
    L2 — grounding: at least one n-gram overlaps `key_phrases` or snippets
    L3 — PII: label contains no obvious identifiers (emails, IDs, $ amounts)
    L4 — (optional) LLM coherence scoring, skipped by default for cost

On validator failure, the next tier runs. Every final label is written to
`canonical_clusters.canonical_label` with `label_source` and `label_version`
bumped accordingly. Call sites:

    from src.data.canonical_label_generator import generate_labels
    result = generate_labels(conn, scan_id="manual-2026-04-15")

Plan reference: canonicalization-enrichment.md §5 (lines 509–676).
Session kernel reference: phase-5-7-session-kernel.md (Phase 5 deliverables).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Constants & defaults
# ──────────────────────────────────────────────────────────────────────────

_PROMPT_PATH_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "prompts" / "canonical_labeling.txt"

# Schema constraints (plan §5.3 L1)
_WORD_MIN = 3
_WORD_MAX = 8

# PII patterns (plan §5.3 L3) — coarse; full redaction already ran upstream
_PII_PATTERNS = [
    re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),          # SSN-like
    re.compile(r"\b\d{10,}\b"),                      # long ID / phone
    re.compile(r"\$\s?\d+(?:\.\d{2})?\b"),           # dollar amounts
    re.compile(r"@[A-Za-z0-9_.-]+\b"),               # emails / handles
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),            # ISO dates
]

# Snippet sourcing per cluster
_SNIPPETS_PER_CLUSTER = 5
_KEY_PHRASES_PER_CLUSTER = 10
_SNIPPET_MAX_CHARS = 300

# Batched LLM call: cluster count cap per call — stays under ~50 KB input
_LLM_BATCH_MAX_CLUSTERS = 40


# ──────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class ValidatorResult:
    valid: bool
    layer: str = ""
    reason: str = ""

    @classmethod
    def ok(cls) -> "ValidatorResult":
        return cls(True, "", "")

    @classmethod
    def fail(cls, layer: str, reason: str) -> "ValidatorResult":
        return cls(False, layer, reason)


@dataclass
class ClusterContext:
    cluster_id: str
    trc: str
    existing_label: Optional[str]
    existing_source: Optional[str]
    member_count: int
    representative_ticket_id: Optional[str]
    snippets: list[str] = field(default_factory=list)
    key_phrases: list[str] = field(default_factory=list)

    def to_prompt_obj(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "existing_label": self.existing_label,
            "member_count": self.member_count,
            "snippets": self.snippets[:_SNIPPETS_PER_CLUSTER],
            "key_phrases": self.key_phrases[:_KEY_PHRASES_PER_CLUSTER],
        }


@dataclass
class LabelDecision:
    cluster_id: str
    final_label: str
    final_source: str  # 'llm' | 'extractive' | 'medoid' | 'existing_retained' | 'abstain'
    validator_failures: list[str] = field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""


@dataclass
class LabelGenerationResult:
    scan_id: str
    total_clusters: int
    source_counts: dict[str, int]
    decisions: list[LabelDecision] = field(default_factory=list)
    wall_time_ms: int = 0

    def summary(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "total_clusters": self.total_clusters,
            "source_counts": self.source_counts,
            "wall_time_ms": self.wall_time_ms,
        }


# ──────────────────────────────────────────────────────────────────────────
# Validators (plan §5.3)
# ──────────────────────────────────────────────────────────────────────────


def _strip_label(label: str) -> str:
    if label is None:
        return ""
    label = label.strip()
    label = label.rstrip(".!?:;,")
    return label


def validate_label_schema(label: str) -> ValidatorResult:
    """L1 — 3-8 words, starts with letter, no control chars."""
    if not label:
        return ValidatorResult.fail("L1_schema", "empty")
    if label.upper() == "ABSTAIN":
        return ValidatorResult.fail("L1_schema", "abstained")
    stripped = _strip_label(label)
    words = stripped.split()
    n = len(words)
    if n < _WORD_MIN or n > _WORD_MAX:
        return ValidatorResult.fail("L1_schema", f"word_count={n}")
    if not re.match(r"^[A-Za-z]", stripped):
        return ValidatorResult.fail("L1_schema", "bad_prefix")
    # No trailing punctuation beyond what we stripped
    if re.search(r"[?!;:\"']\s*$", label):
        return ValidatorResult.fail("L1_schema", "trailing_punct")
    # No line breaks / control chars
    if any(ch in label for ch in "\n\r\t"):
        return ValidatorResult.fail("L1_schema", "control_char")
    return ValidatorResult.ok()


def _bigrams(text: str) -> set[tuple[str, str]]:
    toks = re.findall(r"[A-Za-z0-9]+", text.lower())
    return {(toks[i], toks[i + 1]) for i in range(len(toks) - 1)}


def validate_label_grounding(label: str, context: ClusterContext) -> ValidatorResult:
    """L2 — label must share an n-gram with key_phrases or snippets."""
    label_lower = label.lower()
    # Direct phrase-in-label or label-in-phrase match (cheap win)
    for phrase in context.key_phrases[:20]:
        p = phrase.lower().strip()
        if not p:
            continue
        if p in label_lower or (len(p) > 4 and label_lower in p):
            return ValidatorResult.ok()

    # Bigram overlap with key_phrases
    label_bigrams = _bigrams(label)
    if label_bigrams:
        for phrase in context.key_phrases[:20]:
            if label_bigrams & _bigrams(phrase):
                return ValidatorResult.ok()
        # Fallback: bigram overlap with any snippet
        for snip in context.snippets[:_SNIPPETS_PER_CLUSTER]:
            if label_bigrams & _bigrams(snip):
                return ValidatorResult.ok()

    # Last resort: single-word overlap with meaningful tokens (>4 chars) in key_phrases
    label_toks = {t for t in re.findall(r"[A-Za-z0-9]+", label_lower) if len(t) > 4}
    phrase_toks: set[str] = set()
    for phrase in context.key_phrases[:20]:
        phrase_toks.update(t for t in re.findall(r"[A-Za-z0-9]+", phrase.lower()) if len(t) > 4)
    if label_toks & phrase_toks:
        return ValidatorResult.ok()

    return ValidatorResult.fail("L2_grounding", "no_ngram_overlap")


def validate_label_pii(label: str) -> ValidatorResult:
    """L3 — coarse PII check. Upstream clients already redact heavily."""
    for pat in _PII_PATTERNS:
        if pat.search(label):
            return ValidatorResult.fail("L3_pii", pat.pattern)
    return ValidatorResult.ok()


def validate_label(label: str, context: ClusterContext) -> ValidatorResult:
    """Run L1→L3 in order. L4 (LLM coherence) is external + optional."""
    r = validate_label_schema(label)
    if not r.valid:
        return r
    r = validate_label_grounding(label, context)
    if not r.valid:
        return r
    r = validate_label_pii(label)
    if not r.valid:
        return r
    return ValidatorResult.ok()


# ──────────────────────────────────────────────────────────────────────────
# Extractive fallback (plan §5.4 middle tier)
# ──────────────────────────────────────────────────────────────────────────


_STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "in", "on", "and", "or", "with",
    "is", "are", "was", "were", "be", "been", "being", "it", "this", "that",
    "their", "they", "them", "at", "as", "by", "from", "not", "no", "so",
    "my", "our", "your", "his", "her", "its", "has", "have", "had", "but",
    "if", "when", "while", "after", "before", "can", "will", "would",
    "please", "thanks", "hi", "hello", "hey",
}


def extractive_label(context: ClusterContext) -> Optional[str]:
    """Produce a 3-6 word noun-phrase label from key_phrases + snippets.

    Scores candidate n-grams by (frequency across key_phrases) × log(word_count).
    Returns the best candidate that passes the schema validator; None otherwise.
    """
    # Candidate pool: every key_phrase trimmed to ≤ _WORD_MAX words
    pool: Counter = Counter()
    for phrase in context.key_phrases[:30]:
        p = phrase.strip()
        words = p.split()
        if _WORD_MIN <= len(words) <= _WORD_MAX:
            # Remove leading stopwords
            while words and words[0].lower() in _STOPWORDS:
                words.pop(0)
            if _WORD_MIN <= len(words) <= _WORD_MAX:
                candidate = " ".join(words)
                pool[candidate] += 1

    if not pool:
        # Fallback: take top bigrams from snippets
        bigram_pool: Counter = Counter()
        for snip in context.snippets[:_SNIPPETS_PER_CLUSTER]:
            toks = [t for t in re.findall(r"[A-Za-z]+", snip.lower()) if t not in _STOPWORDS and len(t) > 2]
            for i in range(len(toks) - 2):
                bigram_pool[" ".join(toks[i:i + 3])] += 1
        if bigram_pool:
            candidate = bigram_pool.most_common(1)[0][0]
            # Capitalize first word
            parts = candidate.split()
            if parts:
                parts[0] = parts[0].capitalize()
            candidate = " ".join(parts)
            pool[candidate] = 1

    if not pool:
        return None

    # Pick the most frequent candidate that passes L1+L3 (grounding is trivial — from key_phrases)
    for candidate, _count in pool.most_common():
        # Capitalize first word for consistency with LLM output
        parts = candidate.split()
        if parts and parts[0][0].islower():
            parts[0] = parts[0].capitalize()
        normalized = " ".join(parts)

        r1 = validate_label_schema(normalized)
        if not r1.valid:
            continue
        r3 = validate_label_pii(normalized)
        if not r3.valid:
            continue
        return normalized

    return None


# ──────────────────────────────────────────────────────────────────────────
# Medoid fallback (plan §5.4 bottom tier)
# ──────────────────────────────────────────────────────────────────────────


def medoid_label(context: ClusterContext) -> Optional[str]:
    """Last-resort label: trim the medoid ticket's sub_cluster / TRC string.

    If the string has 3-8 words, return as-is (capitalized). Otherwise,
    truncate to the first 6 meaningful words.
    """
    src = context.existing_label or context.trc or ""
    src = src.strip()
    if not src:
        return None
    # Strip any leading/trailing punctuation and collapse whitespace
    src = re.sub(r"\s+", " ", src)
    src = src.strip(".,;:!?\"'()[]")
    words = src.split()
    # Drop leading stopwords
    while words and words[0].lower() in _STOPWORDS:
        words.pop(0)
    if _WORD_MIN <= len(words) <= _WORD_MAX:
        candidate = " ".join(words)
    else:
        candidate = " ".join(words[:6]) if len(words) > _WORD_MAX else None
    if not candidate:
        return None
    parts = candidate.split()
    parts[0] = parts[0][0].upper() + parts[0][1:] if parts[0] else parts[0]
    return " ".join(parts)


# ──────────────────────────────────────────────────────────────────────────
# Cluster context loading
# ──────────────────────────────────────────────────────────────────────────


def _safe_json_list(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    except (TypeError, ValueError):
        return []
    if isinstance(v, list):
        return v
    return []


def load_cluster_contexts(
    conn: sqlite3.Connection,
    *,
    trc: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[ClusterContext]:
    """Load canonical_clusters rows + their snippets and aggregated key_phrases.

    Snippets are sourced from `ticket_index.issue_snippet` or, if absent,
    the first 300 chars of the matching classification's `summary`.
    """
    where = ""
    args: list = []
    if trc:
        where = "WHERE trc = ?"
        args.append(trc)
    q = f"""
        SELECT cluster_id, trc, canonical_label, label_source, member_count,
               representative_ticket_id
        FROM canonical_clusters
        {where}
        ORDER BY member_count DESC
    """
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q, args).fetchall()

    contexts: list[ClusterContext] = []
    for r in rows:
        cluster_id = r[0]
        ctx = ClusterContext(
            cluster_id=cluster_id,
            trc=r[1],
            existing_label=r[2],
            existing_source=r[3],
            member_count=r[4] or 0,
            representative_ticket_id=r[5],
        )

        # Aggregate top key_phrases across cluster members (from classifications)
        phrase_counts: Counter = Counter()
        phrase_rows = conn.execute(
            """
            SELECT c.key_phrases
            FROM nlp_ticket_classifications c
            JOIN ticket_index ti ON ti.ticket_id = c.ticket_id
            WHERE ti.canonical_issue_id = ?
            """,
            (cluster_id,),
        ).fetchall()
        for (kp_raw,) in phrase_rows:
            for phrase in _safe_json_list(kp_raw):
                if isinstance(phrase, str) and phrase.strip():
                    phrase_counts[phrase.strip()] += 1
        ctx.key_phrases = [p for p, _ in phrase_counts.most_common(_KEY_PHRASES_PER_CLUSTER)]

        # Pull up to _SNIPPETS_PER_CLUSTER representative snippets
        snippet_rows = conn.execute(
            """
            SELECT COALESCE(ti.issue_snippet, c.summary, '') AS snip
            FROM ticket_index ti
            LEFT JOIN nlp_ticket_classifications c ON c.ticket_id = ti.ticket_id
            WHERE ti.canonical_issue_id = ?
            ORDER BY ti.classification_confidence DESC
            LIMIT ?
            """,
            (cluster_id, _SNIPPETS_PER_CLUSTER),
        ).fetchall()
        snippets: list[str] = []
        for (snip,) in snippet_rows:
            if not snip:
                continue
            s = str(snip).strip()
            if len(s) > _SNIPPET_MAX_CHARS:
                s = s[:_SNIPPET_MAX_CHARS] + "…"
            snippets.append(s)
        ctx.snippets = snippets

        contexts.append(ctx)

    return contexts


# ──────────────────────────────────────────────────────────────────────────
# LLM invocation (plan §5.2)
# ──────────────────────────────────────────────────────────────────────────


def _load_prompt_template(prompt_path: Optional[Path] = None) -> str:
    path = prompt_path or _PROMPT_PATH_DEFAULT
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_json_object(text: str) -> Optional[dict]:
    """Tolerant JSON parser: strips markdown fences, finds first {...}."""
    if not text:
        return None
    # Strip triple-backtick fences
    stripped = re.sub(r"```(?:json)?\s*", "", text)
    stripped = stripped.replace("```", "")
    # Find the first top-level { ... } block
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    candidate = stripped[start:end + 1]
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


def call_llm_for_labels(
    contexts: list[ClusterContext],
    llm_client,
    *,
    prompt_template: Optional[str] = None,
    timeout: int = 180,
) -> dict[str, dict]:
    """Issue ONE batched Gemini call for up to _LLM_BATCH_MAX_CLUSTERS clusters.

    Returns {cluster_id: {canonical_label, confidence, rationale, reused_existing}}.
    Empty dict on any failure (caller falls back to extractive/medoid).
    """
    if not contexts or llm_client is None:
        return {}

    template = prompt_template if prompt_template is not None else _load_prompt_template()

    results: dict[str, dict] = {}
    # Chunk by batch cap
    for batch_start in range(0, len(contexts), _LLM_BATCH_MAX_CLUSTERS):
        batch = contexts[batch_start:batch_start + _LLM_BATCH_MAX_CLUSTERS]
        clusters_payload = json.dumps(
            [c.to_prompt_obj() for c in batch],
            ensure_ascii=False,
            indent=2,
        )
        prompt = template.replace("{clusters_json}", clusters_payload)
        try:
            raw = llm_client.generate(prompt, timeout=timeout)
        except Exception as exc:
            logger.warning("LLM call failed (batch %d): %s", batch_start, exc)
            continue

        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning("LLM returned unparseable JSON (batch %d); raw head=%r", batch_start, (raw or "")[:200])
            continue

        labels_arr = parsed.get("labels") or parsed.get("clusters") or []
        if not isinstance(labels_arr, list):
            logger.warning("LLM output missing 'labels' array (batch %d)", batch_start)
            continue
        for item in labels_arr:
            if not isinstance(item, dict):
                continue
            cid = item.get("cluster_id")
            if not cid:
                continue
            results[cid] = {
                "canonical_label": (item.get("canonical_label") or "").strip(),
                "confidence": float(item.get("confidence") or 0.0),
                "rationale": (item.get("rationale") or "").strip(),
                "reused_existing": bool(item.get("reused_existing", False)),
                "grounded_phrases": item.get("grounded_phrases") or [],
            }

    return results


# ──────────────────────────────────────────────────────────────────────────
# Decision engine
# ──────────────────────────────────────────────────────────────────────────


def decide_label(context: ClusterContext, llm_out: Optional[dict]) -> LabelDecision:
    """Apply LLM → extractive → medoid fallback chain with validators."""
    decision = LabelDecision(cluster_id=context.cluster_id, final_label="", final_source="")

    # Tier 1 — LLM
    if llm_out and llm_out.get("canonical_label"):
        candidate = _strip_label(llm_out["canonical_label"])
        if candidate.upper() == "ABSTAIN":
            decision.validator_failures.append("llm_abstain")
        else:
            v = validate_label(candidate, context)
            if v.valid:
                decision.final_label = candidate
                decision.final_source = "llm" if not llm_out.get("reused_existing") else "existing_retained"
                decision.confidence = llm_out.get("confidence", 0.0)
                decision.rationale = llm_out.get("rationale", "")
                return decision
            decision.validator_failures.append(f"llm:{v.layer}:{v.reason}")

    # Tier 2 — extractive
    ext = extractive_label(context)
    if ext:
        v = validate_label(ext, context)
        if v.valid:
            decision.final_label = ext
            decision.final_source = "extractive"
            decision.confidence = 0.5
            decision.rationale = "extractive top n-gram from key_phrases"
            return decision
        decision.validator_failures.append(f"extractive:{v.layer}:{v.reason}")

    # Tier 3 — medoid
    med = medoid_label(context)
    if med:
        # Medoid is accepted even if it fails grounding (existing label IS
        # the grounding source) — only enforce L1 and L3.
        v1 = validate_label_schema(med)
        v3 = validate_label_pii(med)
        if v1.valid and v3.valid:
            decision.final_label = med
            decision.final_source = "medoid"
            decision.confidence = 0.3
            decision.rationale = "medoid sub_cluster string"
            return decision
        if not v1.valid:
            decision.validator_failures.append(f"medoid:{v1.layer}:{v1.reason}")
        if not v3.valid:
            decision.validator_failures.append(f"medoid:{v3.layer}:{v3.reason}")

    # All tiers failed — use raw existing_label truncated to 8 words as last resort
    raw = _strip_label(context.existing_label or context.trc or "")
    words = raw.split()[:_WORD_MAX]
    if words:
        parts = [words[0][0].upper() + words[0][1:]] + words[1:] if words[0] else words
        decision.final_label = " ".join(parts)
        decision.final_source = "abstain"
        decision.confidence = 0.1
        decision.rationale = "all tiers failed validators"
    else:
        decision.final_label = "Unknown cluster"
        decision.final_source = "abstain"
        decision.confidence = 0.0
        decision.rationale = "no signal available"

    return decision


# ──────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────


def _write_decisions(conn: sqlite3.Connection, decisions: Iterable[LabelDecision]) -> None:
    rows = [
        (d.final_label, d.final_source, d.cluster_id)
        for d in decisions
        if d.final_label
    ]
    if not rows:
        return
    conn.executemany(
        """
        UPDATE canonical_clusters
        SET canonical_label = ?,
            label_source = ?,
            label_version = COALESCE(label_version, 0) + 1
        WHERE cluster_id = ?
        """,
        rows,
    )
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────


def generate_labels(
    conn: sqlite3.Connection,
    scan_id: str,
    *,
    trc: Optional[str] = None,
    llm_client=None,
    prompt_path: Optional[Path] = None,
    force: bool = False,
    persist: bool = True,
) -> LabelGenerationResult:
    """Generate canonical labels for every cluster in `canonical_clusters`.

    Args:
        conn: SQLite connection (factory-managed).
        scan_id: Identifier for audit trail.
        trc: Optional TRC filter.
        llm_client: Pre-built LLM client (Gemini/Claude/None). If None, the
            engine skips the LLM tier and falls straight through to extractive.
        prompt_path: Override the default prompt template path.
        force: If False (default), clusters with non-NULL, validator-passing
            canonical_label + label_source in {'llm','extractive','existing_retained'}
            are skipped. Set True to regenerate everything.
        persist: If False, decisions are computed but not written (useful for
            dry runs / tests).

    Returns:
        LabelGenerationResult with per-cluster decisions + source counts.
    """
    import time

    started = time.perf_counter()

    contexts = load_cluster_contexts(conn, trc=trc)
    all_cluster_ids = [c.cluster_id for c in contexts]

    if not force:
        # Skip clusters already labelled by LLM/extractive/retained.
        needs_relabel = []
        for c in contexts:
            label_ok = (
                c.existing_label
                and c.existing_source in ("llm", "extractive", "existing_retained")
                and validate_label_schema(c.existing_label).valid
            )
            if not label_ok:
                needs_relabel.append(c)
        contexts_for_llm = needs_relabel
    else:
        contexts_for_llm = contexts

    llm_outputs: dict[str, dict] = {}
    if llm_client is not None and contexts_for_llm:
        logger.info("Phase 5: calling LLM for %d cluster labels", len(contexts_for_llm))
        llm_outputs = call_llm_for_labels(contexts_for_llm, llm_client)

    decisions: list[LabelDecision] = []
    source_counts: Counter = Counter()
    for ctx in contexts:
        if not force and ctx.cluster_id not in {c.cluster_id for c in contexts_for_llm}:
            # Skipped — record existing label as 'existing_retained'
            dec = LabelDecision(
                cluster_id=ctx.cluster_id,
                final_label=ctx.existing_label or "",
                final_source="existing_retained",
                confidence=0.0,
                rationale="prior label retained (force=False)",
            )
        else:
            dec = decide_label(ctx, llm_outputs.get(ctx.cluster_id))
        decisions.append(dec)
        source_counts[dec.final_source] += 1

    if persist:
        _write_decisions(conn, decisions)

    wall_ms = int((time.perf_counter() - started) * 1000)
    result = LabelGenerationResult(
        scan_id=scan_id,
        total_clusters=len(all_cluster_ids),
        source_counts=dict(source_counts),
        decisions=decisions,
        wall_time_ms=wall_ms,
    )
    logger.info(
        "Phase 5 label generation: %d clusters, sources=%s (%.1f s)",
        len(all_cluster_ids),
        dict(source_counts),
        wall_ms / 1000.0,
    )
    return result
