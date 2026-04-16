"""
Alma Insights — CSV Reformatter Agent (Build 8.0)

Agentic CSV column mapping: analyzes arbitrary CSV headers + sample data,
proposes a mapping to our target schema (via Gemini or COLUMN_MAP fallback),
and returns a column_override dict that ingest_csv() can use directly.

Two-phase design:
  Phase 1: analyze_csv() → MappingResult (read-only, no side effects)
  Phase 2: Caller passes MappingResult.get_column_override() to ingest_csv()

Fast paths (no Gemini call):
  1. Existing COLUMN_MAP matches both required fields → immediate return
  2. Mapping cache hit (same header fingerprint seen before) → immediate return

Offline fallback:
  If bridge unavailable, returns COLUMN_MAP-only matches. Preview dialog
  lets users manually complete the mapping.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("alma.csv_reformatter")

# ═══════════════════════════════════════════════════════════════
#  TARGET SCHEMA — single source of truth for all 13 fields
# ═══════════════════════════════════════════════════════════════

TARGET_SCHEMA = [
    {"field": "ticket_id",       "required": True,  "description": "Unique ticket/case identifier (numeric or string)"},
    {"field": "comment_body",    "required": True,  "description": "Text body of a single comment or message"},
    {"field": "subject",         "required": False, "description": "Ticket subject line or title"},
    {"field": "trc_code",        "required": False, "description": "Ticket reason code or category label"},
    {"field": "status",          "required": False, "description": "Ticket status (open, solved, closed, pending)"},
    {"field": "csat_score",      "required": False, "description": "Customer satisfaction score (typically 1-5)"},
    {"field": "created_at",      "required": False, "description": "Ticket creation date or timestamp"},
    {"field": "author_role",     "required": False, "description": "Comment author role (end-user, agent, admin)"},
    {"field": "event_timestamp_raw", "required": False, "description": "Raw timestamp of this specific comment event"},
    {"field": "assignment_to_resolution_hours", "required": False, "description": "Hours from ticket assignment to resolution"},
    {"field": "total_resolution_hours",         "required": False, "description": "Total hours from creation to resolution"},
    {"field": "first_reply_hours",              "required": False, "description": "Hours from creation to first agent reply"},
    {"field": "requester_email",                "required": False, "description": "Requester/customer email address"},
]

REQUIRED_FIELDS = {s["field"] for s in TARGET_SCHEMA if s["required"]}
ALL_TARGET_FIELDS = [s["field"] for s in TARGET_SCHEMA]

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
_CACHE_MAX_ENTRIES = 10


# ═══════════════════════════════════════════════════════════════
#  MappingResult — value object
# ═══════════════════════════════════════════════════════════════

@dataclass
class MappingResult:
    """Result of CSV column analysis."""

    mappings: List[Dict] = field(default_factory=list)
    # Each: {"source_column": str, "target_field": str,
    #        "confidence": "high"|"medium"|"low", "reasoning": str}

    unmapped_source: List[str] = field(default_factory=list)
    unmapped_target: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source: str = "column_map"  # "column_map" | "cache" | "gemini" | "offline"
    sample_rows: List[Dict] = field(default_factory=list)  # for preview dialog
    input_headers: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True if both required fields have high-confidence mappings."""
        mapped_targets = {
            m["target_field"] for m in self.mappings
            if m.get("confidence") == "high"
        }
        return REQUIRED_FIELDS.issubset(mapped_targets)

    @property
    def all_high_confidence(self) -> bool:
        """True if every mapping is high-confidence."""
        if not self.mappings:
            return False
        return all(m.get("confidence") == "high" for m in self.mappings)

    def get_column_override(self) -> Dict[str, str]:
        """Return {normalized_source_header: target_field} for ingest_csv().

        Normalizes source column names using the same logic as
        csv_ingestion._normalize_header().
        """
        override = {}
        for m in self.mappings:
            src = m["source_column"]
            tgt = m["target_field"]
            if tgt and tgt != "— Skip —":
                norm = _normalize_header(src)
                override[norm] = tgt
        return override

    def get_mapped_field_count(self) -> int:
        """Number of target fields successfully mapped."""
        return len([m for m in self.mappings if m.get("target_field")])


# ═══════════════════════════════════════════════════════════════
#  CSVReformatter — core agent
# ═══════════════════════════════════════════════════════════════

class CSVReformatter:
    """Agentic CSV column mapper using Gemini + COLUMN_MAP fallback."""

    def __init__(self, cache_dir=None):
        """
        Args:
            cache_dir: Directory for mapping_cache.json. Defaults to data/.
        """
        self._cache_dir = Path(cache_dir) if cache_dir else (
            Path(__file__).resolve().parent.parent.parent / "data"
        )

    def analyze_csv(self, file_path, bridge_client=None) -> MappingResult:
        """Analyze CSV headers and return a column mapping proposal.

        Args:
            file_path: Path to CSV file.
            bridge_client: Optional ReportBridgeClient or any object with
                          .generate(prompt, system_prompt, timeout) method.

        Returns:
            MappingResult with proposed mappings and metadata.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        # ── Read headers + sample rows ──
        headers, sample_rows = self._read_csv_sample(file_path, max_rows=5)

        if not headers:
            raise ValueError("CSV file has no headers")

        logger.info(
            "CSVReformatter: analyzing %d headers, %d sample rows from %s",
            len(headers), len(sample_rows), file_path.name,
        )

        # ── Fast path 1: COLUMN_MAP ──
        result = self._try_column_map(headers, sample_rows)
        if result is not None:
            logger.info("CSVReformatter: COLUMN_MAP fast path — both required fields matched")
            return result

        # ── Fast path 2: Cache ──
        fingerprint = self._header_fingerprint(headers)
        cached = self._load_cached_mapping(fingerprint)
        if cached is not None:
            cached.sample_rows = sample_rows
            cached.input_headers = headers
            logger.info("CSVReformatter: cache hit for fingerprint %s", fingerprint[:12])
            return cached

        # ── Gemini path ──
        if bridge_client:
            try:
                result = self._analyze_via_gemini(
                    headers, sample_rows, bridge_client
                )
                if result:
                    # Cache on success
                    self._save_cached_mapping(fingerprint, result)
                    logger.info(
                        "CSVReformatter: Gemini analysis complete — "
                        "%d mappings, source=%s",
                        len(result.mappings), result.source,
                    )
                    return result
            except Exception as e:
                logger.warning("CSVReformatter: Gemini analysis failed: %s", e)
                # Fall through to offline

        # ── Offline fallback ──
        logger.info("CSVReformatter: offline fallback — COLUMN_MAP partial matches only")
        return self._build_offline_result(headers, sample_rows)

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL — CSV reading
    # ═══════════════════════════════════════════════════════════

    def _read_csv_sample(self, file_path, max_rows=5):
        """Read headers + first N data rows from a CSV file.

        Returns:
            (headers: list[str], sample_rows: list[dict])
        """
        raw = Path(file_path).read_bytes()
        text = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("Could not decode CSV file")

        reader = csv.reader(io.StringIO(text))
        headers = next(reader)
        headers = [h.strip() for h in headers]

        sample_rows = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            row_dict = {}
            for j, val in enumerate(row):
                if j < len(headers):
                    row_dict[headers[j]] = val.strip() if val else ""
            sample_rows.append(row_dict)

        return headers, sample_rows

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL — Fast path: COLUMN_MAP
    # ═══════════════════════════════════════════════════════════

    def _try_column_map(self, headers, sample_rows) -> Optional[MappingResult]:
        """Try existing COLUMN_MAP. Returns MappingResult if both required
        fields matched, else None."""
        from src.data.csv_ingestion import COLUMN_MAP

        mappings = []
        matched_targets = set()
        unmapped_source = []

        for h in headers:
            norm = _normalize_header(h)
            if norm in COLUMN_MAP:
                target = COLUMN_MAP[norm]
                if target not in matched_targets:
                    mappings.append({
                        "source_column": h,
                        "target_field": target,
                        "confidence": "high",
                        "reasoning": "Matched via COLUMN_MAP (known header variant)",
                    })
                    matched_targets.add(target)
                else:
                    unmapped_source.append(h)
            else:
                unmapped_source.append(h)

        # Only return if both required fields matched
        if not REQUIRED_FIELDS.issubset(matched_targets):
            return None

        unmapped_target = [
            f["field"] for f in TARGET_SCHEMA
            if f["field"] not in matched_targets
        ]

        return MappingResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=unmapped_target,
            warnings=[],
            source="column_map",
            sample_rows=sample_rows,
            input_headers=headers,
        )

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL — Gemini analysis
    # ═══════════════════════════════════════════════════════════

    def _analyze_via_gemini(self, headers, sample_rows, bridge_client):
        """Call Gemini to analyze CSV columns and return MappingResult."""
        prompt = self._build_analysis_prompt(headers, sample_rows)

        logger.info(
            "CSVReformatter: calling Gemini (%d char prompt)",
            len(prompt),
        )

        t0 = time.time()
        response = bridge_client.generate(
            prompt, system_prompt="", timeout=60
        )
        elapsed = time.time() - t0

        logger.info(
            "CSVReformatter: Gemini responded in %.1fs (%d chars)",
            elapsed, len(response or ""),
        )

        if not response:
            return None

        parsed = self._parse_json_response(response)
        if not parsed:
            logger.warning("CSVReformatter: failed to parse Gemini JSON response")
            return None

        return self._build_gemini_result(parsed, headers, sample_rows)

    def _build_analysis_prompt(self, headers, sample_rows):
        """Load template and inject schema + CSV data."""
        template_path = _PROMPTS_DIR / "csv_reformat_schema.txt"
        template = template_path.read_text(encoding="utf-8")

        # Format schema definition
        schema_lines = []
        for s in TARGET_SCHEMA:
            req = "REQUIRED" if s["required"] else "optional"
            schema_lines.append(
                f"  - {s['field']} ({req}): {s['description']}"
            )
        schema_text = "\n".join(schema_lines)

        # Format headers
        headers_text = json.dumps(headers, indent=2)

        # PII-mask sample data
        masked_samples = self._mask_pii_in_samples(sample_rows)
        samples_text = json.dumps(masked_samples, indent=2, ensure_ascii=False)

        prompt = template.replace("{schema_definition}", schema_text)
        prompt = prompt.replace("{input_headers}", headers_text)
        prompt = prompt.replace("{sample_count}", str(len(masked_samples)))
        prompt = prompt.replace("{sample_data}", samples_text)

        return prompt

    def _mask_pii_in_samples(self, sample_rows):
        """Mask PII patterns in sample data before sending to Gemini."""
        masked = []
        for row in sample_rows:
            masked_row = {}
            for k, v in row.items():
                masked_row[k] = self._mask_pii_value(v)
            masked.append(masked_row)
        return masked

    @staticmethod
    def _mask_pii_value(text):
        """Mask common PII patterns in a single value."""
        if not text:
            return text
        # Email
        text = re.sub(
            r'[\w.+-]+@[\w-]+\.[\w.-]+',
            '[EMAIL]', text
        )
        # Phone (US formats)
        text = re.sub(
            r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b',
            '[PHONE]', text
        )
        # SSN
        text = re.sub(
            r'\b\d{3}-\d{2}-\d{4}\b',
            '[SSN]', text
        )
        # Credit card
        text = re.sub(
            r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
            '[CARD]', text
        )
        # Member/account IDs
        text = re.sub(
            r'\b(?:MBR|MEM|ID|MEMBER|ACCT)[-#]?\d{4,10}\b',
            '[MEMBER_ID]', text, flags=re.IGNORECASE
        )
        return text

    def _build_gemini_result(self, parsed, headers, sample_rows):
        """Convert parsed Gemini JSON into a MappingResult."""
        raw_mappings = parsed.get("mappings", [])
        mappings = []
        mapped_targets = set()

        for m in raw_mappings:
            src = m.get("source_column", "")
            tgt = m.get("target_field", "")
            conf = m.get("confidence", "low")
            reason = m.get("reasoning", "")

            # Validate target field
            if tgt not in ALL_TARGET_FIELDS:
                continue
            # Deduplicate
            if tgt in mapped_targets:
                continue

            mappings.append({
                "source_column": src,
                "target_field": tgt,
                "confidence": conf,
                "reasoning": reason,
            })
            mapped_targets.add(tgt)

        unmapped_source = parsed.get("unmapped_source", [])
        unmapped_target = parsed.get("unmapped_target", [])
        warnings = parsed.get("warnings", [])

        # Ensure unmapped_target is accurate
        actual_unmapped = [
            f["field"] for f in TARGET_SCHEMA
            if f["field"] not in mapped_targets
        ]

        return MappingResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=actual_unmapped,
            warnings=warnings,
            source="gemini",
            sample_rows=sample_rows,
            input_headers=headers,
        )

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL — Offline fallback
    # ═══════════════════════════════════════════════════════════

    def _build_offline_result(self, headers, sample_rows):
        """Build MappingResult with COLUMN_MAP-only matches (no Gemini)."""
        from src.data.csv_ingestion import COLUMN_MAP

        mappings = []
        matched_targets = set()
        unmapped_source = []

        for h in headers:
            norm = _normalize_header(h)
            if norm in COLUMN_MAP:
                target = COLUMN_MAP[norm]
                if target not in matched_targets:
                    mappings.append({
                        "source_column": h,
                        "target_field": target,
                        "confidence": "high",
                        "reasoning": "Matched via COLUMN_MAP",
                    })
                    matched_targets.add(target)
                else:
                    unmapped_source.append(h)
            else:
                unmapped_source.append(h)

        unmapped_target = [
            f["field"] for f in TARGET_SCHEMA
            if f["field"] not in matched_targets
        ]

        warnings = []
        missing_required = REQUIRED_FIELDS - matched_targets
        if missing_required:
            warnings.append(
                f"Required fields not auto-mapped: {sorted(missing_required)}. "
                "Please assign them manually."
            )

        return MappingResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=unmapped_target,
            warnings=warnings,
            source="offline",
            sample_rows=sample_rows,
            input_headers=headers,
        )

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL — JSON parsing (analyst_agent pattern)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _parse_json_response(text):
        """Parse a JSON response from Gemini, with fallbacks.

        Handles markdown fences, trailing commas, and embedded JSON.
        Pattern from analyst_agent._parse_json_response().
        """
        if not text:
            return None

        cleaned = text.strip()

        # Strip markdown fences
        if cleaned.startswith("```"):
            cleaned = cleaned.lstrip("`").lstrip("json").lstrip("\n")
            cleaned = cleaned.rstrip("`").rstrip("\n")

        # Fix trailing commas
        cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try regex extraction
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(
                    re.sub(r",\s*([\]}])", r"\1", match.group())
                )
            except json.JSONDecodeError:
                pass

        logger.warning(
            "CSVReformatter: failed to parse JSON response (%d chars)",
            len(text),
        )
        return None

    # ═══════════════════════════════════════════════════════════
    #  INTERNAL — Mapping cache
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _header_fingerprint(headers):
        """Deterministic hash of sorted normalized headers."""
        normalized = sorted(_normalize_header(h) for h in headers)
        blob = json.dumps(normalized, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def _cache_path(self):
        return self._cache_dir / "mapping_cache.json"

    def _load_cache(self):
        """Load the mapping cache from disk."""
        path = self._cache_path()
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self, cache):
        """Save the mapping cache to disk, enforcing max entries."""
        # LRU eviction: keep most recent N entries
        if len(cache) > _CACHE_MAX_ENTRIES:
            # Sort by created timestamp, keep newest
            entries = sorted(
                cache.items(),
                key=lambda kv: kv[1].get("created", ""),
                reverse=True,
            )
            cache = dict(entries[:_CACHE_MAX_ENTRIES])

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._cache_path(), "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except OSError as e:
            logger.debug("CSVReformatter: cache save failed: %s", e)

    def _load_cached_mapping(self, fingerprint):
        """Look up a cached mapping by header fingerprint.

        Returns MappingResult or None.
        """
        cache = self._load_cache()
        entry = cache.get(fingerprint)
        if not entry:
            return None

        mappings = []
        override = entry.get("mapping", {})
        source_headers = entry.get("source_headers", [])

        # Rebuild mappings from the cached override dict
        for src_header in source_headers:
            norm = _normalize_header(src_header)
            if norm in override:
                mappings.append({
                    "source_column": src_header,
                    "target_field": override[norm],
                    "confidence": "high",
                    "reasoning": "Loaded from mapping cache",
                })

        matched_targets = {m["target_field"] for m in mappings}
        unmapped_source = [h for h in source_headers
                          if _normalize_header(h) not in override]
        unmapped_target = [f["field"] for f in TARGET_SCHEMA
                          if f["field"] not in matched_targets]

        return MappingResult(
            mappings=mappings,
            unmapped_source=unmapped_source,
            unmapped_target=unmapped_target,
            warnings=[],
            source="cache",
            sample_rows=[],  # will be populated by caller
            input_headers=source_headers,
        )

    def _save_cached_mapping(self, fingerprint, result):
        """Save a successful mapping to the cache."""
        from datetime import datetime, timezone

        cache = self._load_cache()
        cache[fingerprint] = {
            "mapping": result.get_column_override(),
            "created": datetime.now(timezone.utc).isoformat(),
            "source_headers": result.input_headers,
        }
        self._save_cache(cache)


# ═══════════════════════════════════════════════════════════════
#  MODULE-LEVEL HELPERS
# ═══════════════════════════════════════════════════════════════

def _normalize_header(h):
    """Normalize a CSV header for matching.

    Same logic as csv_ingestion._normalize_header().
    """
    return h.strip().lower().replace("\n", " ").replace("  ", " ")
