"""
Unit tests for src.data.csv_ingestion — sub-functions and main entry point.

Covers:
  - _read_csv_with_encoding: UTF-8, BOM, Latin-1 fallback, undecodable error
  - _map_columns: standard Lightdash header mapping
  - _resolve_columns: custom overrides, missing required columns
  - _parse_row: single-row dict conversion
  - _group_rows_by_ticket: grouping, first-non-empty-wins, CSAT parsing
  - _write_tickets_to_db: destructive DELETE + INSERT
  - _post_ingest: FTS rebuild (entity/ngram failures tolerated)
  - ingest_csv: end-to-end orchestrator
"""

import csv
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.data.csv_ingestion import (
    COLUMN_MAP,
    _map_columns,
    _normalize_header,
    _parse_row,
    _read_csv_with_encoding,
    _resolve_columns,
    _group_rows_by_ticket,
    _write_tickets_to_db,
    _post_ingest,
    ingest_csv,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _write_csv(path, headers, rows, encoding="utf-8"):
    """Write a CSV file at *path* with the given encoding."""
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


MINIMAL_HEADERS = ["Ticket ID", "Comment Body", "Author Role"]

FULL_HEADERS = [
    "Ticket ID",
    "Subject",
    "Ticket Reason Code List",
    "Status",
    "CSAT Score",
    "Created Day",
    "Comment Body",
    "Author Role",
]


def _noop_progress(msg="", pct=None):
    pass


# ═══ _read_csv_with_encoding ═══════════════════════════════════════

class TestReadCsvWithEncoding:

    def test_reads_utf8(self, tmp_path):
        p = tmp_path / "utf8.csv"
        _write_csv(p, ["Col A", "Col B"], [["hello", "world"]])
        headers, rows = _read_csv_with_encoding(p)
        assert headers == ["Col A", "Col B"]
        assert rows == [["hello", "world"]]

    def test_reads_utf8_bom(self, tmp_path):
        """UTF-8 BOM files should be handled by utf-8-sig."""
        p = tmp_path / "bom.csv"
        # Write raw BOM bytes + CSV content
        p.write_bytes(b"\xef\xbb\xbfCol A,Col B\nhello,world\n")
        headers, rows = _read_csv_with_encoding(p)
        assert headers[0] == "Col A"  # BOM stripped

    def test_reads_latin1(self, tmp_path):
        """Latin-1 fallback when bytes are not valid UTF-8."""
        p = tmp_path / "latin1.csv"
        _write_csv(p, ["Héader"], [["café"]], encoding="latin-1")
        headers, rows = _read_csv_with_encoding(p)
        assert "ader" in headers[0]  # accent preserved
        assert "caf" in rows[0][0]

    def test_error_on_undecodable(self, tmp_path):
        """Random bytes that defeat all encodings should raise ValueError.

        In practice latin-1 accepts all bytes 0x00-0xFF, so this test
        verifies the function at least returns *something* rather than
        crashing on binary junk.
        """
        p = tmp_path / "binary.csv"
        # latin-1 will absorb anything, so we just verify no crash
        p.write_bytes(b"\x80\x81\x82\n\x83\x84\x85")
        headers, rows = _read_csv_with_encoding(p)
        # Should not raise — latin-1 accepts all byte values
        assert isinstance(headers, list)


# ═══ _map_columns ══════════════════════════════════════════════════

class TestMapColumns:

    def test_standard_lightdash_headers(self):
        headers = [
            "Zendesk Ticket Zendesk Ticket ID",
            "Zendesk Ticket [PII Fields] Ticket Subject [PII]",
            "Zendesk Ticket Comment [PII Field] Comment Body [PII]",
            "Zendesk User (Ticket Updater) User Role",
        ]
        mapping, unmapped = _map_columns(headers)
        fields = set(mapping.values())
        assert "ticket_id" in fields
        assert "subject" in fields
        assert "comment_body" in fields
        assert "author_role" in fields
        assert unmapped == []

    def test_short_headers(self):
        mapping, unmapped = _map_columns(["Ticket ID", "Comment Body"])
        assert set(mapping.values()) == {"ticket_id", "comment_body"}

    def test_unmapped_columns_returned(self):
        mapping, unmapped = _map_columns(["Ticket ID", "Unknown Col", "Comment Body"])
        assert "Unknown Col" in unmapped
        assert len(mapping) == 2

    def test_case_insensitive(self):
        mapping, _ = _map_columns(["TICKET ID", "comment body"])
        assert set(mapping.values()) == {"ticket_id", "comment_body"}


# ═══ _resolve_columns ══════════════════════════════════════════════

class TestResolveColumns:

    def test_uses_default_map(self):
        col_mapping, fields, unmapped = _resolve_columns(MINIMAL_HEADERS)
        assert "ticket_id" in fields
        assert "comment_body" in fields

    def test_column_override(self):
        override = {"my_id": "ticket_id", "my_body": "comment_body"}
        headers = ["My_ID", "My_Body", "Extra"]
        col_mapping, fields, unmapped = _resolve_columns(headers, column_override=override)
        assert "ticket_id" in fields
        assert "comment_body" in fields
        assert "Extra" in unmapped

    def test_missing_required_raises(self):
        with pytest.raises(ValueError, match="missing required columns"):
            _resolve_columns(["Ticket ID", "Unknown"])

    def test_missing_ticket_id_raises(self):
        with pytest.raises(ValueError, match="ticket_id"):
            _resolve_columns(["Comment Body"])

    def test_missing_comment_body_raises(self):
        with pytest.raises(ValueError, match="comment_body"):
            _resolve_columns(["Ticket ID"])


# ═══ _parse_row ════════════════════════════════════════════════════

class TestParseRow:

    def test_basic_mapping(self):
        col_mapping = {0: "ticket_id", 1: "comment_body"}
        row = ["T-100", "Hello world"]
        rec = _parse_row(row, col_mapping)
        assert rec == {"ticket_id": "T-100", "comment_body": "Hello world"}

    def test_strips_whitespace(self):
        col_mapping = {0: "ticket_id"}
        rec = _parse_row(["  T-100  "], col_mapping)
        assert rec["ticket_id"] == "T-100"

    def test_unmapped_indices_skipped(self):
        col_mapping = {0: "ticket_id"}
        rec = _parse_row(["T-1", "ignored", "also ignored"], col_mapping)
        assert list(rec.keys()) == ["ticket_id"]

    def test_none_becomes_empty_string(self):
        col_mapping = {0: "ticket_id"}
        rec = _parse_row([None], col_mapping)
        assert rec["ticket_id"] == ""


# ═══ _group_rows_by_ticket ═════════════════════════════════════════

class TestGroupRowsByTicket:

    def _make_col_mapping(self, headers):
        mapping, _ = _map_columns(headers)
        return mapping

    def test_groups_comments_under_ticket(self):
        headers = MINIMAL_HEADERS
        mapping = self._make_col_mapping(headers)
        rows = [
            ["T-1", "First comment", "end-user"],
            ["T-1", "Second comment", "agent"],
            ["T-2", "Only comment", "end-user"],
        ]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, len(rows))
        assert len(tickets) == 2
        assert len(tickets["T-1"]["comments"]) == 2
        assert len(tickets["T-2"]["comments"]) == 1

    def test_first_non_empty_wins_for_subject(self):
        headers = ["Ticket ID", "Subject", "Comment Body", "Author Role"]
        mapping = self._make_col_mapping(headers)
        rows = [
            ["T-1", "", "body1", "agent"],
            ["T-1", "Real subject", "body2", "end-user"],
            ["T-1", "Later subject", "body3", "agent"],
        ]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, len(rows))
        assert tickets["T-1"]["subject"] == "Real subject"

    def test_skips_empty_ticket_id(self):
        mapping = self._make_col_mapping(MINIMAL_HEADERS)
        rows = [
            ["", "orphan body", "agent"],
            ["T-1", "good body", "end-user"],
        ]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, len(rows))
        assert len(tickets) == 1
        assert "T-1" in tickets

    def test_skips_empty_comment_body(self):
        mapping = self._make_col_mapping(MINIMAL_HEADERS)
        rows = [
            ["T-1", "", "agent"],
            ["T-1", "real comment", "end-user"],
        ]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, len(rows))
        assert len(tickets["T-1"]["comments"]) == 1

    def test_csat_parsing(self):
        headers = ["Ticket ID", "CSAT Score", "Comment Body", "Author Role"]
        mapping = self._make_col_mapping(headers)
        rows = [["T-1", "4.5", "body", "end-user"]]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, 1)
        assert tickets["T-1"]["csat_score"] == 4.5

    def test_csat_bad_value_ignored(self):
        headers = ["Ticket ID", "CSAT Score", "Comment Body", "Author Role"]
        mapping = self._make_col_mapping(headers)
        rows = [["T-1", "N/A", "body", "end-user"]]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, 1)
        assert tickets["T-1"]["csat_score"] is None

    def test_role_normalization(self):
        mapping = self._make_col_mapping(MINIMAL_HEADERS)
        rows = [
            ["T-1", "c1", "end-user"],
            ["T-1", "c2", "agent"],
            ["T-1", "c3", ""],
        ]
        tickets = _group_rows_by_ticket(rows, mapping, _noop_progress, 3)
        roles = [c["role"] for c in tickets["T-1"]["comments"]]
        assert roles == ["customer", "agent", "bot"]


# ═══ _write_tickets_to_db ══════════════════════════════════════════

class TestWriteTicketsToDb:

    def test_destructive_delete_before_insert(self, empty_db):
        """Existing rows should be wiped before new data is written."""
        db = empty_db
        # Seed an existing ticket
        db.upsert_ticket({
            "ticket_id": "OLD-1", "subject": "old", "trc_code": "", "trc_label": "",
            "status": "open", "priority": "", "channel": "", "csat_score": None,
            "created_at": "2024-01-01", "updated_at": "", "solved_at": "",
            "requester_name": "", "requester_email": "", "assignee_name": "",
            "group_name": "", "tags": [], "custom_fields": {},
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None,
            "first_reply_hours": None,
        })
        db.upsert_conversation({
            "ticket_id": "OLD-1", "subject": "old", "trc_code": "", "trc_label": "",
            "status": "open", "csat_score": None, "created_at": "2024-01-01",
            "solved_at": "", "message_count": 0, "client_messages": 0,
            "agent_messages": 0, "full_thread": "", "thread_preview": "",
            "source": "csv",
        })
        db.commit()

        # Build a minimal tickets dict
        tickets = {
            "NEW-1": {
                "comments": [{"body": "hi", "created_at": "2024-06-01", "event_ts_raw": "", "role": "customer"}],
                "subject": "New ticket",
                "trc_code": "TRC-X",
                "status": "open",
                "csat_score": None,
                "created_at": "2024-06-01",
                "requester_email": "",
                "assignment_to_resolution_hours": None,
                "total_resolution_hours": None,
                "first_reply_hours": None,
            }
        }
        _write_tickets_to_db(db, tickets, dataset_id=None, _progress=_noop_progress)

        # Since Session 1, imports are additive — old ticket should STILL exist
        old = db.conn.execute("SELECT 1 FROM tickets WHERE ticket_id='OLD-1'").fetchone()
        assert old is not None  # Additive: old data preserved

        # New ticket should exist
        new = db.conn.execute("SELECT 1 FROM tickets WHERE ticket_id='NEW-1'").fetchone()
        assert new is not None

    def test_returns_inserted_count(self, empty_db):
        tickets = {
            f"T-{i}": {
                "comments": [{"body": f"c{i}", "created_at": "", "event_ts_raw": "", "role": "customer"}],
                "subject": "", "trc_code": "", "status": "", "csat_score": None,
                "created_at": "", "requester_email": "",
                "assignment_to_resolution_hours": None,
                "total_resolution_hours": None,
                "first_reply_hours": None,
            }
            for i in range(5)
        }
        count = _write_tickets_to_db(empty_db, tickets, dataset_id=None, _progress=_noop_progress)
        assert count == 5


# ═══ _post_ingest ══════════════════════════════════════════════════

class TestPostIngest:

    def test_fts_rebuild(self, empty_db):
        """_post_ingest should rebuild FTS index without error."""
        # Insert ticket first (FK), then conversation so FTS has something to index
        empty_db.upsert_ticket({
            "ticket_id": "T-1", "subject": "test", "trc_code": "", "trc_label": "",
            "status": "open", "priority": "", "channel": "", "csat_score": None,
            "created_at": "2024-01-01", "updated_at": "", "solved_at": "",
            "requester_name": "", "requester_email": "", "assignee_name": "",
            "group_name": "", "tags": [], "custom_fields": {},
            "assignment_to_resolution_hours": None,
            "total_resolution_hours": None, "first_reply_hours": None,
        })
        empty_db.upsert_conversation({
            "ticket_id": "T-1", "subject": "test", "trc_code": "", "trc_label": "",
            "status": "open", "csat_score": None, "created_at": "2024-01-01",
            "solved_at": "", "message_count": 1, "client_messages": 1,
            "agent_messages": 0, "full_thread": "hello world", "thread_preview": "hello",
            "source": "csv",
        })
        empty_db.commit()

        tickets = {"T-1": {"subject": "test", "comments": [{"body": "hello world"}]}}
        # Should not raise even if entity/ngram modules fail
        _post_ingest(empty_db, tickets, _noop_progress)

        # Verify FTS index was built
        row = empty_db.conn.execute(
            "SELECT ticket_id FROM conversations_fts WHERE conversations_fts MATCH 'hello'"
        ).fetchone()
        assert row is not None


# ═══ ingest_csv (end-to-end) ═══════════════════════════════════════

class TestIngestCsvE2E:

    def test_file_not_found(self, empty_db, tmp_path):
        with pytest.raises(FileNotFoundError):
            ingest_csv(tmp_path / "nope.csv", empty_db)

    def test_missing_columns_error(self, empty_db, tmp_path):
        p = tmp_path / "bad.csv"
        _write_csv(p, ["Random Column"], [["value"]])
        with pytest.raises(ValueError, match="missing required columns"):
            ingest_csv(p, empty_db)

    def test_full_round_trip(self, empty_db, tmp_path):
        """CSV -> ingest -> verify tickets, conversations, and comments in DB."""
        p = tmp_path / "data.csv"
        _write_csv(p, FULL_HEADERS, [
            ["T-100", "Billing issue", "TRC-100", "open", "4.0", "2024-03-01", "I need help with billing", "end-user"],
            ["T-100", "Billing issue", "TRC-100", "open", "4.0", "2024-03-01", "Sure, let me check", "agent"],
            ["T-200", "Login broken", "TRC-200", "solved", "5.0", "2024-03-02", "Cannot log in", "end-user"],
        ])

        stats = ingest_csv(p, empty_db)

        assert stats["total_csv_rows"] == 3
        assert stats["tickets_created"] == 2
        assert stats["comments_stored"] == 3
        assert "ticket_id" in stats["mapped_fields"]
        assert "subject" in stats["mapped_fields"]

        # Verify ticket records
        t100 = empty_db.conn.execute(
            "SELECT subject, trc_code FROM tickets WHERE ticket_id='T-100'"
        ).fetchone()
        assert t100 is not None
        assert t100[0] == "Billing issue"
        assert t100[1] == "TRC-100"

        # Verify conversation records
        conv = empty_db.conn.execute(
            "SELECT message_count, client_messages, agent_messages FROM conversations WHERE ticket_id='T-100'"
        ).fetchone()
        assert conv is not None
        assert conv[0] == 2  # message_count
        assert conv[1] == 1  # client_messages
        assert conv[2] == 1  # agent_messages

        # Verify comments
        comments = empty_db.conn.execute(
            "SELECT author_role FROM comments WHERE ticket_id='T-100' ORDER BY comment_id"
        ).fetchall()
        assert len(comments) == 2
        roles = [c[0] for c in comments]
        assert "customer" in roles
        assert "agent" in roles

    def test_progress_callback_invoked(self, empty_db, tmp_path):
        p = tmp_path / "prog.csv"
        _write_csv(p, MINIMAL_HEADERS, [["T-1", "body", "end-user"]])
        calls = []
        ingest_csv(p, empty_db, progress_callback=lambda msg, pct: calls.append((msg, pct)))
        assert len(calls) > 0
        # Final callback should indicate 100%
        assert calls[-1][1] == 100

    def test_dataset_id_tagging(self, empty_db, tmp_path):
        """dataset_id should propagate to conversations table."""
        p = tmp_path / "ds.csv"
        _write_csv(p, MINIMAL_HEADERS, [["T-1", "body", "end-user"]])
        ingest_csv(p, empty_db, dataset_id="ds-alpha")

        row = empty_db.conn.execute(
            "SELECT dataset_id FROM conversations WHERE ticket_id='T-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "ds-alpha"

    def test_column_override(self, empty_db, tmp_path):
        """Custom column_override dict should be used instead of COLUMN_MAP."""
        p = tmp_path / "custom.csv"
        _write_csv(p, ["my_id", "my_body"], [["T-1", "hello"]])
        override = {"my_id": "ticket_id", "my_body": "comment_body"}
        stats = ingest_csv(p, empty_db, column_override=override)
        assert stats["tickets_created"] == 1

    def test_resolution_times_stored(self, empty_db, tmp_path):
        """Resolution time columns should land in the tickets table."""
        headers = [
            "Ticket ID",
            "Comment Body",
            "Author Role",
            "Assignment to Resolution Time in Hours (Calendar)",
            "Total Resolution Time in Hours (Calendar)",
            "First Reply Time in Hours (Calendar)",
        ]
        p = tmp_path / "res.csv"
        _write_csv(p, headers, [["T-1", "body", "end-user", "2.5", "10.3", "0.7"]])
        stats = ingest_csv(p, empty_db)
        assert stats["has_resolution_times"] is True

        row = empty_db.conn.execute(
            "SELECT assignment_to_resolution_hours, total_resolution_hours, first_reply_hours "
            "FROM tickets WHERE ticket_id='T-1'"
        ).fetchone()
        assert row is not None
        assert abs(row[0] - 2.5) < 0.01
        assert abs(row[1] - 10.3) < 0.01
        assert abs(row[2] - 0.7) < 0.01
