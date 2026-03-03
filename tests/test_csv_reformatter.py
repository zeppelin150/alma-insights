"""
Build 8.0 — CSV Reformatter Tests

Tests for CSVReformatter, MappingResult, column_override in ingest_csv(),
and the full analysis pipeline.
"""

import csv
import io
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.csv_reformatter import (
    CSVReformatter,
    MappingResult,
    TARGET_SCHEMA,
    REQUIRED_FIELDS,
    ALL_TARGET_FIELDS,
    _normalize_header,
)


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def _make_csv(headers, rows, tmpdir):
    """Write a CSV file and return its path."""
    path = Path(tmpdir) / "test_input.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
    return str(path)


def _lightdash_headers():
    """Standard Lightdash column headers that COLUMN_MAP recognizes."""
    return [
        "Zendesk Ticket Zendesk Ticket Id",
        "Zendesk Ticket [PII Fields] Ticket Subject [PII]",
        "Zendesk Ticket Comment [PII Field] Comment Body [PII]",
        "Zendesk Ticket Ticket Reason Code List",
        "Zendesk Ticket Status",
        "Zendesk Satisfaction (CSAT) Rating Satisfaction (CSAT) Score",
        "Zendesk Ticket Created (EST) Day",
        "Zendesk User (Ticket Updater) User Role",
    ]


def _lightdash_row():
    """Sample row matching Lightdash headers."""
    return [
        "12345", "Help with billing", "I need help with my bill",
        "BILLING_INQUIRY", "open", "3", "2025-10-15", "end-user",
    ]


def _nonstandard_headers():
    """Non-Lightdash headers that need Gemini analysis."""
    return [
        "Case Number",
        "Title",
        "Message Text",
        "Category",
        "Case Status",
        "Rating",
        "Date Opened",
        "Sender Type",
    ]


def _nonstandard_row():
    """Sample row for non-standard headers."""
    return [
        "98765", "Login issue", "I can't log into my account",
        "TECH_SUPPORT", "active", "2", "2025-11-20", "customer",
    ]


# ═══════════════════════════════════════════════════════════════
#  T1: FAST PATH — COLUMN_MAP
# ═══════════════════════════════════════════════════════════════

def test_fast_path_lightdash_headers():
    """Standard Lightdash headers should match via COLUMN_MAP without Gemini."""
    tmpdir = tempfile.mkdtemp()
    try:
        csv_path = _make_csv(
            _lightdash_headers(), [_lightdash_row()], tmpdir
        )
        reformatter = CSVReformatter(cache_dir=tmpdir)
        result = reformatter.analyze_csv(csv_path, bridge_client=None)

        assert isinstance(result, MappingResult)
        assert result.source == "column_map"
        assert result.is_valid is True

        # Both required fields should be mapped
        mapped_targets = {m["target_field"] for m in result.mappings}
        assert "ticket_id" in mapped_targets
        assert "comment_body" in mapped_targets

        # All mappings should be high confidence
        assert result.all_high_confidence is True

        # get_column_override should return a valid dict
        override = result.get_column_override()
        assert isinstance(override, dict)
        assert len(override) > 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  T2: FAST PATH — CACHE HIT
# ═══════════════════════════════════════════════════════════════

def test_fast_path_cache_hit():
    """Second analysis of same headers should return from cache."""
    tmpdir = tempfile.mkdtemp()
    try:
        headers = _nonstandard_headers()
        csv_path = _make_csv(headers, [_nonstandard_row()], tmpdir)

        reformatter = CSVReformatter(cache_dir=tmpdir)

        # Simulate a cached mapping
        fingerprint = reformatter._header_fingerprint(headers)
        fake_mapping = {
            _normalize_header("Case Number"): "ticket_id",
            _normalize_header("Message Text"): "comment_body",
            _normalize_header("Title"): "subject",
            _normalize_header("Category"): "trc_code",
        }
        cache = {
            fingerprint: {
                "mapping": fake_mapping,
                "created": "2026-02-24T10:00:00",
                "source_headers": headers,
            }
        }
        cache_path = Path(tmpdir) / "mapping_cache.json"
        with open(cache_path, "w") as f:
            json.dump(cache, f)

        # Analyze — should hit cache
        result = reformatter.analyze_csv(csv_path, bridge_client=None)
        assert result.source == "cache"
        assert result.is_valid is True

        override = result.get_column_override()
        assert override[_normalize_header("Case Number")] == "ticket_id"
        assert override[_normalize_header("Message Text")] == "comment_body"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  T3: GEMINI ANALYSIS (mocked)
# ═══════════════════════════════════════════════════════════════

def test_analyze_nonstandard_headers():
    """Non-standard headers should trigger Gemini analysis."""
    tmpdir = tempfile.mkdtemp()
    try:
        csv_path = _make_csv(
            _nonstandard_headers(), [_nonstandard_row()], tmpdir
        )

        # Mock bridge client
        mock_bridge = MagicMock()
        mock_bridge.generate.return_value = json.dumps({
            "mappings": [
                {"source_column": "Case Number", "target_field": "ticket_id",
                 "confidence": "high", "reasoning": "Numeric case ID"},
                {"source_column": "Message Text", "target_field": "comment_body",
                 "confidence": "high", "reasoning": "Text content of message"},
                {"source_column": "Title", "target_field": "subject",
                 "confidence": "high", "reasoning": "Case title/subject"},
                {"source_column": "Category", "target_field": "trc_code",
                 "confidence": "medium", "reasoning": "Category could map to reason code"},
                {"source_column": "Case Status", "target_field": "status",
                 "confidence": "high", "reasoning": "Status field"},
                {"source_column": "Rating", "target_field": "csat_score",
                 "confidence": "medium", "reasoning": "Numeric rating 1-5"},
                {"source_column": "Date Opened", "target_field": "created_at",
                 "confidence": "high", "reasoning": "Creation date"},
                {"source_column": "Sender Type", "target_field": "author_role",
                 "confidence": "high", "reasoning": "User role"},
            ],
            "unmapped_source": [],
            "unmapped_target": ["event_timestamp_raw", "assignment_to_resolution_hours",
                                "total_resolution_hours", "first_reply_hours",
                                "requester_email"],
            "warnings": [],
        })

        reformatter = CSVReformatter(cache_dir=tmpdir)
        result = reformatter.analyze_csv(csv_path, bridge_client=mock_bridge)

        assert result.source == "gemini"
        assert result.is_valid is True
        assert len(result.mappings) == 8

        # Verify bridge was called
        mock_bridge.generate.assert_called_once()

        # Verify cache was saved
        cache_path = Path(tmpdir) / "mapping_cache.json"
        assert cache_path.exists()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  T4: COLUMN OVERRIDE IN ingest_csv()
# ═══════════════════════════════════════════════════════════════

def test_column_override_ingest():
    """ingest_csv() with column_override should map non-standard headers."""
    tmpdir = tempfile.mkdtemp()
    try:
        csv_path = _make_csv(
            ["Case Number", "Message Text"],
            [
                ["T001", "Hello, I need help"],
                ["T001", "Sure, let me help you"],
                ["T002", "My account is locked"],
            ],
            tmpdir,
        )

        from src.data.db_manager import DatabaseManager
        from src.data.csv_ingestion import ingest_csv

        db_path = Path(tmpdir) / "test_override.db"
        db = DatabaseManager(db_path)
        db.initialize()

        override = {
            "case number": "ticket_id",
            "message text": "comment_body",
        }

        stats = ingest_csv(csv_path, db, column_override=override)

        assert stats["tickets_created"] == 2
        assert stats["comments_stored"] == 3
        assert "ticket_id" in stats["mapped_fields"]
        assert "comment_body" in stats["mapped_fields"]

        db.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  T5: PII MASKING
# ═══════════════════════════════════════════════════════════════

def test_pii_masking_in_samples():
    """PII in sample data should be masked before Gemini call."""
    reformatter = CSVReformatter()

    # Email
    assert "[EMAIL]" in reformatter._mask_pii_value("user@example.com")
    assert "example.com" not in reformatter._mask_pii_value("user@example.com")

    # Phone
    assert "[PHONE]" in reformatter._mask_pii_value("Call 555-123-4567")
    assert "4567" not in reformatter._mask_pii_value("Call 555-123-4567")

    # SSN
    assert "[SSN]" in reformatter._mask_pii_value("SSN: 123-45-6789")
    assert "6789" not in reformatter._mask_pii_value("SSN: 123-45-6789")

    # Credit card
    assert "[CARD]" in reformatter._mask_pii_value("Card: 4111-1111-1111-1111")

    # Member ID
    assert "[MEMBER_ID]" in reformatter._mask_pii_value("MBR12345678")

    # Clean text unchanged
    assert reformatter._mask_pii_value("Hello world") == "Hello world"

    # Empty/None
    assert reformatter._mask_pii_value("") == ""
    assert reformatter._mask_pii_value(None) is None


# ═══════════════════════════════════════════════════════════════
#  T6: MISSING REQUIRED FIELDS
# ═══════════════════════════════════════════════════════════════

def test_missing_required_fields():
    """CSV without ticket_id → MappingResult.is_valid should be False."""
    result = MappingResult(
        mappings=[
            {"source_column": "Body", "target_field": "comment_body",
             "confidence": "high", "reasoning": "text"},
        ],
        unmapped_source=["Something"],
        unmapped_target=["ticket_id"],
        source="offline",
    )
    assert result.is_valid is False

    # With both required mapped at high confidence
    result2 = MappingResult(
        mappings=[
            {"source_column": "ID", "target_field": "ticket_id",
             "confidence": "high", "reasoning": "id"},
            {"source_column": "Body", "target_field": "comment_body",
             "confidence": "high", "reasoning": "text"},
        ],
        source="gemini",
    )
    assert result2.is_valid is True


# ═══════════════════════════════════════════════════════════════
#  T7: ALL HIGH CONFIDENCE (auto-accept logic)
# ═══════════════════════════════════════════════════════════════

def test_all_high_confidence_auto_accept():
    """All-high-confidence mappings should flag for auto-accept."""
    result = MappingResult(
        mappings=[
            {"source_column": "ID", "target_field": "ticket_id",
             "confidence": "high", "reasoning": "id"},
            {"source_column": "Body", "target_field": "comment_body",
             "confidence": "high", "reasoning": "text"},
        ],
        source="gemini",
    )
    assert result.all_high_confidence is True
    assert result.is_valid is True

    # One medium → not all high
    result2 = MappingResult(
        mappings=[
            {"source_column": "ID", "target_field": "ticket_id",
             "confidence": "high", "reasoning": "id"},
            {"source_column": "Body", "target_field": "comment_body",
             "confidence": "medium", "reasoning": "maybe"},
        ],
        source="gemini",
    )
    assert result2.all_high_confidence is False


# ═══════════════════════════════════════════════════════════════
#  T8: OFFLINE FALLBACK
# ═══════════════════════════════════════════════════════════════

def test_offline_fallback():
    """No bridge → offline fallback with COLUMN_MAP-only matches."""
    tmpdir = tempfile.mkdtemp()
    try:
        csv_path = _make_csv(
            _nonstandard_headers(), [_nonstandard_row()], tmpdir
        )
        reformatter = CSVReformatter(cache_dir=tmpdir)

        # No bridge_client → should fall through to offline
        result = reformatter.analyze_csv(csv_path, bridge_client=None)

        assert result.source == "offline"
        # Non-standard headers shouldn't match COLUMN_MAP
        assert result.is_valid is False
        # Should still have warnings about missing required fields
        assert len(result.warnings) > 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  T9: MAPPING RESULT — get_column_override()
# ═══════════════════════════════════════════════════════════════

def test_mapping_result_get_column_override():
    """get_column_override() should return normalized keys."""
    result = MappingResult(
        mappings=[
            {"source_column": "Case Number", "target_field": "ticket_id",
             "confidence": "high", "reasoning": ""},
            {"source_column": "Message Text", "target_field": "comment_body",
             "confidence": "high", "reasoning": ""},
            {"source_column": "Extra Col", "target_field": "— Skip —",
             "confidence": "low", "reasoning": ""},
        ],
        source="gemini",
    )

    override = result.get_column_override()

    # Normalized keys
    assert override["case number"] == "ticket_id"
    assert override["message text"] == "comment_body"

    # Skipped columns should not be in override
    assert "extra col" not in override
    assert len(override) == 2


# ═══════════════════════════════════════════════════════════════
#  T10: JSON PARSING — edge cases
# ═══════════════════════════════════════════════════════════════

def test_parse_json_response_markdown_fences():
    """JSON wrapped in markdown fences should parse correctly."""
    text = '```json\n{"mappings": [], "unmapped_source": []}\n```'
    result = CSVReformatter._parse_json_response(text)
    assert result is not None
    assert "mappings" in result


def test_parse_json_response_trailing_commas():
    """JSON with trailing commas should be fixed and parsed."""
    text = '{"mappings": [{"a": 1,},], "extra": "x",}'
    result = CSVReformatter._parse_json_response(text)
    assert result is not None


def test_parse_json_response_embedded():
    """JSON embedded in prose should be extracted."""
    text = 'Here is the mapping:\n{"mappings": [], "warnings": []}\nDone.'
    result = CSVReformatter._parse_json_response(text)
    assert result is not None
    assert "mappings" in result


def test_parse_json_response_empty():
    """Empty/None input should return None gracefully."""
    assert CSVReformatter._parse_json_response("") is None
    assert CSVReformatter._parse_json_response(None) is None


# ═══════════════════════════════════════════════════════════════
#  T11: HEADER FINGERPRINT — determinism
# ═══════════════════════════════════════════════════════════════

def test_header_fingerprint_deterministic():
    """Same headers in different order should produce same fingerprint."""
    h1 = ["Column A", "Column B", "Column C"]
    h2 = ["Column C", "Column A", "Column B"]

    fp1 = CSVReformatter._header_fingerprint(h1)
    fp2 = CSVReformatter._header_fingerprint(h2)

    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex


def test_header_fingerprint_case_insensitive():
    """Headers with different casing should produce same fingerprint."""
    h1 = ["Ticket ID", "Comment Body"]
    h2 = ["ticket id", "comment body"]

    fp1 = CSVReformatter._header_fingerprint(h1)
    fp2 = CSVReformatter._header_fingerprint(h2)

    assert fp1 == fp2


# ═══════════════════════════════════════════════════════════════
#  T12: INGEST_CSV — backward compatibility
# ═══════════════════════════════════════════════════════════════

def test_ingest_csv_no_override_backward_compat():
    """ingest_csv() with no column_override should behave identically to before."""
    tmpdir = tempfile.mkdtemp()
    try:
        csv_path = _make_csv(
            _lightdash_headers(), [_lightdash_row()], tmpdir
        )

        from src.data.db_manager import DatabaseManager
        from src.data.csv_ingestion import ingest_csv

        db_path = Path(tmpdir) / "test_compat.db"
        db = DatabaseManager(db_path)
        db.initialize()

        # No override — standard COLUMN_MAP path
        stats = ingest_csv(csv_path, db)

        assert stats["tickets_created"] == 1
        assert "ticket_id" in stats["mapped_fields"]
        assert "comment_body" in stats["mapped_fields"]

        db.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
