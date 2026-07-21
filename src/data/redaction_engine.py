"""
Alma Insights — Entity-Aware Redaction Engine
PHI/PII scrubbing that preserves business entities (insurance names,
TRC codes, business acronyms).

Order of operations:
1. Identify "keep" regions (allowlisted business entities)
2. Apply PHI detection patterns
3. Skip matches that overlap with keep regions
4. Replace remaining PHI matches with redaction tokens
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("alma.redaction")

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_ENTITIES_DIR = _CONFIG_DIR / "entities"
_DEFAULT_ALLOWLIST = _ENTITIES_DIR / "phi_allowlist.json"
_DEFAULT_PATTERNS = _CONFIG_DIR / "redaction_patterns.json"
_ENTITY_DICTS = (_ENTITIES_DIR / "payers.json", _ENTITIES_DIR / "product_areas.json")

# Name CUE tokens — a Title-Case pair is only treated as a person name when
# one of these immediately precedes it. Keeps clinical/billing Title-Case
# phrases ("Session Note", "Direct Deposit") out of the [NAME] bucket.
_NAME_CUE_WORDS = frozenset({
    "patient", "member", "caller", "contact", "client", "subscriber",
    "mr", "mrs", "ms", "miss", "dr", "per", "from", "for", "by",
})
# Multi-word / punctuation cues matched as a trailing suffix of the pre-context.
_NAME_CUE_PHRASES = ("name:", "spoke with", "spoke to", "talked to", "attn:")
# Titles strong enough to fire even when greedily paired as the FIRST token of
# a Title-Case match ("Patient Jane", "Dr Lee"). Prepositions are excluded —
# they only cue a name when they sit OUTSIDE the pair ("per Kevin Lee").
_NAME_TITLE_WORDS = frozenset({
    "patient", "member", "caller", "contact", "client", "subscriber",
    "mr", "mrs", "ms", "miss", "dr",
})


class RedactionEngine:
    """PHI/PII scrubbing with business entity preservation."""

    def __init__(self, allowlist_path=None, patterns_path=None):
        allowlist_path = Path(allowlist_path) if allowlist_path else _DEFAULT_ALLOWLIST
        patterns_path = Path(patterns_path) if patterns_path else _DEFAULT_PATTERNS

        self._allowlist = self._load_json(allowlist_path)
        self._patterns_cfg = self._load_json(patterns_path)

        # Surgical entity vocab: payer aliases + product/feature terms.
        # Merged with phi_allowlist so classification signal is never scrubbed.
        self._entity_terms = self._load_entity_dicts(_ENTITY_DICTS)

        # Compile PHI patterns
        self._phi_patterns = self._compile_patterns(self._patterns_cfg.get("patterns", []))

        # Build allowlist lookup (case-insensitive set for fast membership)
        self._keep_terms = self._build_keep_terms()

        # Build allowlist regex for region tagging
        self._keep_regex = self._build_keep_regex()

    # ── Public API ────────────────────────────────────────

    def scrub(self, text: str) -> str:
        """Remove PHI/PII while preserving business entities.

        Args:
            text: Raw text that may contain PHI.

        Returns:
            Text with PHI replaced by tokens like [SSN], [EMAIL], etc.
        """
        if not text:
            return text

        # Step 1: Find keep regions
        keep_regions = self._find_keep_regions(text)

        # Step 2: Find PHI matches
        phi_matches = self._find_phi_matches(text)

        # Step 3: Filter out PHI matches that overlap keep regions
        safe_matches = self._filter_overlapping(phi_matches, keep_regions)

        # Step 4: Apply replacements (right-to-left to preserve offsets)
        safe_matches.sort(key=lambda m: m["start"], reverse=True)
        result = text
        for match in safe_matches:
            result = result[:match["start"]] + match["replace"] + result[match["end"]:]

        return result

    def scrub_dict(self, record: dict, fields: list[str] = None) -> dict:
        """Scrub specific text fields in a dict. Returns a new dict."""
        default_fields = ["full_thread", "thread_preview", "body", "subject"]
        fields = fields or default_fields
        out = dict(record)
        for f in fields:
            if f in out and isinstance(out[f], str):
                out[f] = self.scrub(out[f])
        return out

    # ── Keep Region Detection ─────────────────────────────

    def _find_keep_regions(self, text: str) -> list[tuple[int, int]]:
        """Find text spans that should NOT be redacted."""
        regions = []
        if self._keep_regex:
            for m in self._keep_regex.finditer(text):
                regions.append((m.start(), m.end()))

        # Also preserve patterns like TRC-100, policy#12345
        preserve = self._allowlist.get("preserve_patterns", [])
        for pat in preserve:
            escaped = re.escape(pat)
            for m in re.finditer(escaped + r"[\w-]*", text, re.IGNORECASE):
                regions.append((m.start(), m.end()))

        return regions

    def _build_keep_regex(self) -> re.Pattern | None:
        """Build a regex that matches all allowlisted + entity terms."""
        terms = []
        for name in self._allowlist.get("insurance_names", []):
            terms.append(re.escape(name))
        for acro in self._allowlist.get("business_acronyms", []):
            terms.append(r"\b" + re.escape(acro) + r"\b")
        # Payer aliases + product/feature terms (already lowercased).
        for term in self._entity_terms:
            terms.append(r"\b" + re.escape(term) + r"\b")

        if not terms:
            return None

        # Sort by length (longest first) so "Blue Cross Blue Shield" matches before "Blue Cross"
        terms.sort(key=len, reverse=True)
        return re.compile("|".join(terms), re.IGNORECASE)

    def _build_keep_terms(self) -> set[str]:
        """Build lowercase set of all keep terms."""
        terms = set()
        for name in self._allowlist.get("insurance_names", []):
            terms.add(name.lower())
        for acro in self._allowlist.get("business_acronyms", []):
            terms.add(acro.lower())
        terms |= self._entity_terms
        return terms

    @staticmethod
    def _load_entity_dicts(paths) -> set[str]:
        """Merge payer/product entity dicts into one lowercase keep-term set.

        Each dict is ``{canonical: [aliases]}`` (e.g. payers.json,
        product_areas.json). Canonical keys and aliases are both kept.
        Fail-safe: a missing/invalid file contributes nothing.
        """
        terms: set[str] = set()
        for path in paths:
            data = RedactionEngine._load_json(path)
            if not isinstance(data, dict):
                continue
            for canonical, aliases in data.items():
                if isinstance(canonical, str) and canonical.strip():
                    terms.add(canonical.lower())
                for alias in aliases or []:
                    if isinstance(alias, str) and alias.strip():
                        terms.add(alias.lower())
        return terms

    # ── PHI Detection ─────────────────────────────────────

    def _find_phi_matches(self, text: str) -> list[dict]:
        """Find all PHI matches in text."""
        matches = []
        skip_terms = set()
        for s in self._patterns_cfg.get("aggressive_skip_terms", []):
            skip_terms.add(s.lower())

        for pat_info in self._phi_patterns:
            name = pat_info["name"]
            regex = pat_info["compiled"]
            replace = pat_info["replace"]
            conditional = pat_info.get("conditional", False)

            for m in regex.finditer(text):
                matched_text = m.group(0)
                end = m.end()

                # Conditional patterns (name_heuristic): extra checks
                if conditional:
                    if not self._is_likely_name(
                        matched_text, skip_terms, text, m.start()
                    ):
                        continue
                    # The regex only pairs TWO tokens, but a cued/title-paired
                    # name can be 3+ tokens ("Patient Robert Johnson", "member
                    # Mary Jane Watson"). Swallow trailing Title-Case surname
                    # tokens so the surname does not leak.
                    end = self._extend_trailing_names(text, end, skip_terms)

                matches.append({
                    "start": m.start(),
                    "end": end,
                    "text": text[m.start():end],
                    "replace": replace,
                    "pattern": name,
                })

        return matches

    @staticmethod
    def _extend_trailing_names(text: str, end: int, skip_terms: set[str]) -> int:
        """Extend ``end`` over Title-Case surname tokens trailing a name match.

        Walks ` Surname` tokens after a title-paired [NAME] match and absorbs
        them into the redaction span, stopping at the first non-name token,
        a skip term, or end of text. Keeps clinical/billing words out by
        honoring ``skip_terms``.
        """
        surname = re.compile(r"\s+([A-Z][a-z]+)\b")
        while True:
            m = surname.match(text, end)
            if not m or m.group(1).lower() in skip_terms:
                return end
            end = m.end()

    def _is_likely_name(self, matched: str, skip_terms: set[str],
                        full_text: str = "", start: int = 0) -> bool:
        """Decide whether a Title-Case pair is a person name to redact.

        Surgical policy: a pair is only redacted when a name CUE precedes it
        (member/patient/caller/Mr/Dr/"name:"/"spoke with"/"per "). Cue-less
        Title-Case pairs are clinical/billing vocab and are kept — trading
        recall on bare names for precision on classification signal.
        """
        lower = matched.lower()
        parts = lower.split()

        # Never redact known skip terms, allowlisted, or entity vocab.
        if any(p in skip_terms for p in parts):
            return False
        if lower in self._keep_terms:
            return False
        # Single-char tokens are addresses/initials, not names.
        if any(len(p) <= 1 for p in parts):
            return False

        # Context gate: redact when a cue precedes the pair, OR when the
        # pair's own leading token is a title/cue ("Patient Jane", "Dr Lee")
        # — the regex greedily pairs the cue with the first name token.
        if parts and parts[0] in _NAME_TITLE_WORDS:
            return True
        return self._has_name_cue(full_text, start)

    @staticmethod
    def _has_name_cue(full_text: str, start: int) -> bool:
        """True when a name cue token/phrase precedes ``start`` in the text.

        A trailing ``:`` (header cues like ``From:`` / ``Name:`` / ``Attn:``)
        is treated as transparent so the real cue word is the one tested —
        otherwise the colon would be the last token and defeat the cue.
        """
        if not full_text:
            return False
        pre = full_text[:start].lower().rstrip()
        if any(pre.endswith(phrase) for phrase in _NAME_CUE_PHRASES):
            return True
        # Treat a trailing ':' (header cues: 'From:', 'Name:') as transparent
        # so the real cue word, not the colon, is the token under test.
        unwrapped = pre.rstrip(":").rstrip()
        prev_words = re.findall(r"[a-z']+", unwrapped)
        return bool(prev_words) and prev_words[-1] in _NAME_CUE_WORDS

    # ── Overlap Filtering ─────────────────────────────────

    def _filter_overlapping(self, phi_matches: list[dict],
                            keep_regions: list[tuple[int, int]]) -> list[dict]:
        """Remove PHI matches that overlap with keep regions."""
        if not keep_regions:
            return phi_matches

        safe = []
        for match in phi_matches:
            overlaps = False
            for start, end in keep_regions:
                if match["start"] < end and match["end"] > start:
                    overlaps = True
                    break
            if not overlaps:
                safe.append(match)
        return safe

    # ── Helpers ────────────────────────────────────────────

    def _compile_patterns(self, patterns: list[dict]) -> list[dict]:
        """Compile regex patterns from config."""
        compiled = []
        for p in patterns:
            flags = 0
            flag_str = p.get("flags", "")
            if "i" in flag_str:
                flags |= re.IGNORECASE

            try:
                compiled.append({
                    "name": p["name"],
                    "compiled": re.compile(p["regex"], flags),
                    "replace": p["replace"],
                    "conditional": p.get("conditional", False),
                })
            except re.error as e:
                logger.warning("Bad redaction pattern '%s': %s", p.get("name"), e)

        return compiled

    @staticmethod
    def _load_json(path: Path) -> dict:
        """Load a JSON file, returning {} on failure."""
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Could not load %s: %s", path, e)
            return {}
