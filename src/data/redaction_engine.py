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
_DEFAULT_ALLOWLIST = _CONFIG_DIR / "entities" / "phi_allowlist.json"
_DEFAULT_PATTERNS = _CONFIG_DIR / "redaction_patterns.json"


class RedactionEngine:
    """PHI/PII scrubbing with business entity preservation."""

    def __init__(self, allowlist_path=None, patterns_path=None):
        allowlist_path = Path(allowlist_path) if allowlist_path else _DEFAULT_ALLOWLIST
        patterns_path = Path(patterns_path) if patterns_path else _DEFAULT_PATTERNS

        self._allowlist = self._load_json(allowlist_path)
        self._patterns_cfg = self._load_json(patterns_path)

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
        """Build a regex that matches all allowlisted terms."""
        terms = []
        for name in self._allowlist.get("insurance_names", []):
            terms.append(re.escape(name))
        for acro in self._allowlist.get("business_acronyms", []):
            terms.append(r"\b" + re.escape(acro) + r"\b")

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

                # Conditional patterns (name_heuristic): extra checks
                if conditional and not self._is_likely_name(matched_text, skip_terms):
                    continue

                matches.append({
                    "start": m.start(),
                    "end": m.end(),
                    "text": matched_text,
                    "replace": replace,
                    "pattern": name,
                })

        return matches

    def _is_likely_name(self, text: str, skip_terms: set[str]) -> bool:
        """Check if a title-case pair is likely a person name, not a business term."""
        lower = text.lower()

        # Check against skip terms
        parts = lower.split()
        for part in parts:
            if part in skip_terms:
                return False

        # Check against allowlist
        if lower in self._keep_terms:
            return False

        # Very short words are likely not names (e.g. "St Louis" → address)
        if any(len(p) <= 1 for p in parts):
            return False

        return True

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
