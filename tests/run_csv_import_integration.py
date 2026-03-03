"""
Build 8.0 — CSV Import Integration Test
End-to-end test of the full pipeline: CSV → analyze → override → ingest → verify DB.
"""

import sys
import tempfile
import shutil
import json
import csv
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.csv_reformatter import CSVReformatter, MappingResult, _normalize_header
from src.data.db_manager import DatabaseManager
from src.data.csv_ingestion import ingest_csv


def main():
    tmpdir = tempfile.mkdtemp()
    passed = 0
    failed = 0

    print("=" * 70)
    print("BUILD 8.0 — CSV IMPORT INTEGRATION TEST")
    print("=" * 70)

    # ═══════════════════════════════════════════════════════════
    # TEST 1: Lightdash CSV → COLUMN_MAP fast path
    # ═══════════════════════════════════════════════════════════
    print()
    print("TEST 1: Lightdash CSV (COLUMN_MAP fast path)")
    print("-" * 50)

    lightdash_csv = Path(tmpdir) / "lightdash_export.csv"
    with open(lightdash_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Zendesk Ticket Zendesk Ticket Id",
            "Zendesk Ticket [PII Fields] Ticket Subject [PII]",
            "Zendesk Ticket Comment [PII Field] Comment Body [PII]",
            "Zendesk Ticket Ticket Reason Code List",
            "Zendesk Ticket Status",
            "Zendesk Satisfaction (CSAT) Rating Satisfaction (CSAT) Score",
            "Zendesk Ticket Created (EST) Day",
            "Zendesk User (Ticket Updater) User Role",
        ])
        # Ticket T001: 3 comments
        w.writerow(["T001", "Billing issue", "I was charged twice for my subscription",
                     "BILLING_INQUIRY", "open", "2", "2025-10-15", "end-user"])
        w.writerow(["T001", "Billing issue", "Let me look into that for you",
                     "BILLING_INQUIRY", "open", "2", "2025-10-15", "agent"])
        w.writerow(["T001", "Billing issue", "Thank you, still waiting",
                     "BILLING_INQUIRY", "open", "2", "2025-10-15", "end-user"])
        # Ticket T002: 2 comments
        w.writerow(["T002", "Login problems", "I cannot access my account since yesterday",
                     "TECH_SUPPORT", "solved", "4", "2025-10-16", "end-user"])
        w.writerow(["T002", "Login problems", "We have reset your password, please try again",
                     "TECH_SUPPORT", "solved", "4", "2025-10-16", "agent"])
        # Ticket T003: 1 comment
        w.writerow(["T003", "Feature request", "Can you add dark mode to the app?",
                     "FEATURE_REQUEST", "closed", "5", "2025-10-17", "end-user"])

    try:
        reformatter = CSVReformatter(cache_dir=tmpdir)
        result = reformatter.analyze_csv(str(lightdash_csv), bridge_client=None)

        print(f"  Source:           {result.source}")
        print(f"  Is valid:         {result.is_valid}")
        print(f"  All high conf:    {result.all_high_confidence}")
        print(f"  Mapped fields:    {result.get_mapped_field_count()}")

        mapped_targets = {m["target_field"] for m in result.mappings}
        print(f"  Required mapped:  ticket_id={'ticket_id' in mapped_targets}, "
              f"comment_body={'comment_body' in mapped_targets}")

        # Ingest with override
        db_path = Path(tmpdir) / "test1.db"
        db = DatabaseManager(db_path)
        db.initialize()
        stats = ingest_csv(str(lightdash_csv), db,
                          column_override=result.get_column_override())

        print(f"  CSV rows:         {stats['total_csv_rows']}")
        print(f"  Tickets created:  {stats['tickets_created']}")
        print(f"  Comments stored:  {stats['comments_stored']}")
        print(f"  Mapped fields:    {stats['mapped_fields']}")

        # Verify DB
        convos = db.conn.execute(
            "SELECT ticket_id, subject, trc_code, status, csat_score "
            "FROM conversations ORDER BY ticket_id"
        ).fetchall()
        print(f"  DB conversations: {len(convos)}")
        for c in convos:
            print(f"    {c[0]}: subject={c[1]!r}, trc={c[2]}, status={c[3]}, csat={c[4]}")

        comments = db.conn.execute(
            "SELECT ticket_id, author_role, body "
            "FROM comments ORDER BY ticket_id, comment_id"
        ).fetchall()
        print(f"  DB comments:      {len(comments)}")

        db.close()

        assert result.source == "column_map"
        assert result.is_valid is True
        assert stats["tickets_created"] == 3
        assert stats["comments_stored"] == 6
        assert len(convos) == 3
        assert len(comments) == 6
        print("  [OK] TEST 1 PASSED")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] TEST 1 FAILED: {e}")
        failed += 1

    # ═══════════════════════════════════════════════════════════
    # TEST 2: Non-standard CSV → column_override
    # ═══════════════════════════════════════════════════════════
    print()
    print("TEST 2: Non-standard CSV (manual column_override)")
    print("-" * 50)

    custom_csv = Path(tmpdir) / "custom_export.csv"
    with open(custom_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Case Number", "Title", "Message Text", "Category",
                     "Case Status", "Rating", "Date Opened", "Sender Type"])
        w.writerow(["C100", "Claim denied",
                     "My claim was denied without explanation",
                     "CLAIMS", "open", "1", "2025-11-01", "customer"])
        w.writerow(["C100", "Claim denied",
                     "We are reviewing your claim and will update shortly",
                     "CLAIMS", "open", "1", "2025-11-01", "agent"])
        w.writerow(["C101", "Payment not posted",
                     "Payment sent 2 weeks ago still not showing",
                     "PAYMENTS", "pending", "2", "2025-11-02", "customer"])
        w.writerow(["C101", "Payment not posted",
                     "We found the payment, it will be posted today",
                     "PAYMENTS", "pending", "2", "2025-11-02", "agent"])
        w.writerow(["C102", "Enrollment question",
                     "When does open enrollment start?",
                     "ENROLLMENT", "solved", "5", "2025-11-03", "customer"])

    try:
        manual_override = {
            "case number": "ticket_id",
            "title": "subject",
            "message text": "comment_body",
            "category": "trc_code",
            "case status": "status",
            "rating": "csat_score",
            "date opened": "created_at",
            "sender type": "author_role",
        }

        db_path2 = Path(tmpdir) / "test2.db"
        db2 = DatabaseManager(db_path2)
        db2.initialize()
        stats2 = ingest_csv(str(custom_csv), db2, column_override=manual_override)

        print(f"  CSV rows:         {stats2['total_csv_rows']}")
        print(f"  Tickets created:  {stats2['tickets_created']}")
        print(f"  Comments stored:  {stats2['comments_stored']}")
        print(f"  Mapped fields:    {stats2['mapped_fields']}")

        convos2 = db2.conn.execute(
            "SELECT ticket_id, subject, trc_code, status, csat_score "
            "FROM conversations ORDER BY ticket_id"
        ).fetchall()
        print(f"  DB conversations: {len(convos2)}")
        for c in convos2:
            print(f"    {c[0]}: subject={c[1]!r}, trc={c[2]}, status={c[3]}, csat={c[4]}")

        # FTS search
        fts_results = db2.search_conversations(keyword="denied")
        print(f"  FTS 'denied':     {len(fts_results)} result(s)")
        for r in fts_results:
            print(f"    -> {r['ticket_id']}: {r['subject']}")

        db2.close()

        assert stats2["tickets_created"] == 3
        assert stats2["comments_stored"] == 5
        assert len(fts_results) >= 1
        print("  [OK] TEST 2 PASSED")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] TEST 2 FAILED: {e}")
        failed += 1

    # ═══════════════════════════════════════════════════════════
    # TEST 3: Offline fallback
    # ═══════════════════════════════════════════════════════════
    print()
    print("TEST 3: Offline fallback (non-standard CSV, no bridge)")
    print("-" * 50)

    try:
        reformatter = CSVReformatter(cache_dir=Path(tmpdir) / "no_cache")
        result3 = reformatter.analyze_csv(str(custom_csv), bridge_client=None)

        print(f"  Source:           {result3.source}")
        print(f"  Is valid:         {result3.is_valid}")
        print(f"  Mapped count:     {result3.get_mapped_field_count()}")
        print(f"  Unmapped source:  {result3.unmapped_source}")
        print(f"  Warnings:         {result3.warnings}")

        assert result3.source == "offline"
        assert result3.is_valid is False
        print("  [OK] TEST 3 PASSED")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] TEST 3 FAILED: {e}")
        failed += 1

    # ═══════════════════════════════════════════════════════════
    # TEST 4: Mapping cache write + read
    # ═══════════════════════════════════════════════════════════
    print()
    print("TEST 4: Mapping cache (Gemini call -> cache hit)")
    print("-" * 50)

    try:
        cache_dir = Path(tmpdir) / "cache_test"
        cache_dir.mkdir()
        reformatter4 = CSVReformatter(cache_dir=cache_dir)

        mock_bridge = MagicMock()
        mock_bridge.generate.return_value = json.dumps({
            "mappings": [
                {"source_column": "Case Number", "target_field": "ticket_id",
                 "confidence": "high", "reasoning": "ID field"},
                {"source_column": "Message Text", "target_field": "comment_body",
                 "confidence": "high", "reasoning": "Text body"},
                {"source_column": "Title", "target_field": "subject",
                 "confidence": "high", "reasoning": "Subject line"},
            ],
            "unmapped_source": ["Category", "Case Status", "Rating",
                                "Date Opened", "Sender Type"],
            "unmapped_target": [],
            "warnings": [],
        })

        # First call: Gemini
        result4a = reformatter4.analyze_csv(str(custom_csv),
                                            bridge_client=mock_bridge)
        print(f"  First call source:  {result4a.source}")
        print(f"  Gemini calls:       {mock_bridge.generate.call_count}")
        assert result4a.source == "gemini"
        assert mock_bridge.generate.call_count == 1

        cache_path = cache_dir / "mapping_cache.json"
        print(f"  Cache file exists:  {cache_path.exists()}")
        assert cache_path.exists()

        # Second call: should use cache
        mock_bridge.generate.reset_mock()
        result4b = reformatter4.analyze_csv(str(custom_csv),
                                            bridge_client=mock_bridge)
        print(f"  Second call source: {result4b.source}")
        print(f"  Gemini calls:       {mock_bridge.generate.call_count}")
        assert result4b.source == "cache"
        assert mock_bridge.generate.call_count == 0

        override = result4b.get_column_override()
        print(f"  Cached override:    {dict(list(override.items())[:3])}...")
        assert override[_normalize_header("Case Number")] == "ticket_id"
        assert override[_normalize_header("Message Text")] == "comment_body"
        print("  [OK] TEST 4 PASSED")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] TEST 4 FAILED: {e}")
        failed += 1

    # ═══════════════════════════════════════════════════════════
    # TEST 5: PII masking in samples
    # ═══════════════════════════════════════════════════════════
    print()
    print("TEST 5: PII masking verification")
    print("-" * 50)

    try:
        pii_csv = Path(tmpdir) / "pii_test.csv"
        with open(pii_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ID", "Body", "Email", "Phone"])
            w.writerow(["1", "Call john@patient.com about claim",
                         "john@patient.com", "555-123-4567"])
            w.writerow(["2", "SSN 123-45-6789 on file, card 4111-1111-1111-1111",
                         "admin@health.org", "800-555-0100"])

        reformatter5 = CSVReformatter(cache_dir=tmpdir)
        headers, samples = reformatter5._read_csv_sample(str(pii_csv))
        masked = reformatter5._mask_pii_in_samples(samples)

        print(f"  Original email:    {samples[0]['Email']}")
        print(f"  Masked email:      {masked[0]['Email']}")
        print(f"  Original phone:    {samples[0]['Phone']}")
        print(f"  Masked phone:      {masked[0]['Phone']}")
        print(f"  Original body[1]:  {samples[1]['Body'][:50]}")
        print(f"  Masked body[1]:    {masked[1]['Body']}")

        dump = json.dumps(masked)
        assert "john@patient.com" not in dump
        assert "555-123-4567" not in dump
        assert "123-45-6789" not in dump
        assert "4111-1111-1111-1111" not in dump
        assert "[EMAIL]" in masked[0]["Email"]
        assert "[PHONE]" in masked[0]["Phone"]
        assert "[SSN]" in masked[1]["Body"]
        assert "[CARD]" in masked[1]["Body"]
        print("  [OK] TEST 5 PASSED")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] TEST 5 FAILED: {e}")
        failed += 1

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    print("=" * 70)
    if failed == 0:
        print(f"ALL {passed} INTEGRATION TESTS PASSED")
    else:
        print(f"{passed} PASSED, {failed} FAILED")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
