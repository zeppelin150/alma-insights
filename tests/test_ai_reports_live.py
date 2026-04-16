"""
Alma Insights — AI Reports Live E2E Test

Exercises the full report generation pipeline against the production DB
with real Gemini API calls:
  1. build_data_block() — pre-compute analytics from real data
  2. format_data_block_for_prompt() — structured text formatting
  3. replace_prompt_variables() — prompt template expansion
  4. GeminiClient.generate() — real Gemini API call
  5. Report history save/load round-trip

GATED: Only runs when invoked with --live flag or ALMA_LIVE_TEST=1 env var.

Usage:
  ALMA_LIVE_TEST=1 python -m pytest tests/test_ai_reports_live.py -v
  python tests/test_ai_reports_live.py --live -v
"""

import os
import sys
import time
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Production DB with real data (888 tickets). The worktree DB is empty schema-only,
# so we use the main repo's local_warehouse.db which has the actual imported data.
_PROD_DB = Path("C:/alma-insights/data/local_warehouse.db")
if not _PROD_DB.exists():
    # Fallback: try worktree location
    _PROD_DB = _PROJECT_ROOT / "data" / "alma_insights.db"

# ── Gate: skip unless --live or ALMA_LIVE_TEST=1 ──
LIVE_MODE = (
    "--live" in sys.argv
    or os.environ.get("ALMA_LIVE_TEST", "") == "1"
)
if "--live" in sys.argv:
    sys.argv.remove("--live")


def skip_unless_live(cls):
    """Class decorator: skip entire test class unless live mode."""
    if not LIVE_MODE:
        return unittest.skip("Live tests require --live flag")(cls)
    return cls


# ── Settings loader ──
def _get_settings():
    import yaml
    cfg_path = _PROJECT_ROOT / "config" / "settings.yaml"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ── Prompt definitions (same as ai_reports.py) ──
REPORT_TYPES = [
    {
        "name": "General Trend Analysis",
        "prompt_file": "general_trend.txt",
        "system_prompt": "You are a Support Analytics engine. Data is provided "
                         "as pre-computed statistics. You do NOT load files or access URLs.",
    },
    {
        "name": "Executive Summary",
        "prompt_file": "executive_summary.txt",
        "system_prompt": "You are a Support Analytics engine producing executive summaries.",
    },
    {
        "name": "Incident Summary",
        "prompt_file": "incident_summary.txt",
        "system_prompt": "You are a Support Analytics engine specializing in incident detection.",
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# TEST CLASS: Data Block (no Gemini calls)
# ═══════════════════════════════════════════════════════════════════════════

@skip_unless_live
class TestDataBlock(unittest.TestCase):
    """Test data block construction against production DB."""

    @classmethod
    def setUpClass(cls):
        from src.data.db_manager import DatabaseManager
        cls.db_path = _PROD_DB
        cls.db = DatabaseManager(cls.db_path)
        cls.db.initialize()

        # Find valid date range from actual data
        row = cls.db.conn.execute(
            "SELECT MIN(created_at) as mn, MAX(created_at) as mx FROM conversations"
        ).fetchone()
        cls.date_start = row["mn"][:10] if row["mn"] else "2024-01-01"
        cls.date_end = row["mx"][:10] if row["mx"] else "2025-12-31"

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_01_build_data_block(self):
        """build_data_block returns dict with ticket_count > 0."""
        from src.data.report_builder import build_data_block
        t0 = time.time()
        block = build_data_block(self.db, self.date_start, self.date_end)
        elapsed = time.time() - t0

        self.assertIsInstance(block, dict)
        self.assertGreater(block.get("ticket_count", 0), 0,
                           "Expected tickets in production DB")
        self.assertIn("trc_distribution", block)
        self.assertIn("csat_summary", block)
        self.assertIn("top_terms", block)

        print(f"\n  Data block built: {block['ticket_count']} tickets, "
              f"{len(block.get('trc_distribution', []))} TRCs, "
              f"{elapsed:.1f}s")

        # Store for dependent tests
        self.__class__._block = block

    def test_02_format_data_block(self):
        """format_data_block_for_prompt returns substantial text."""
        from src.data.report_builder import format_data_block_for_prompt
        block = getattr(self.__class__, '_block', None)
        if not block:
            self.skipTest("test_01 did not produce a data block")

        text = format_data_block_for_prompt(block)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 200, "Data block text too short")
        self.assertIn("TOPLINE", text)

        print(f"\n  Formatted data block: {len(text)} chars")
        self.__class__._data_text = text

    def test_03_prompt_variable_replacement(self):
        """replace_prompt_variables fills all template tokens."""
        from src.data.report_builder import replace_prompt_variables
        block = getattr(self.__class__, '_block', None)
        if not block:
            self.skipTest("test_01 did not produce a data block")

        # Load the general_trend prompt
        prompt_path = _PROJECT_ROOT / "config" / "prompts" / "general_trend.txt"
        if not prompt_path.exists():
            self.skipTest("general_trend.txt not found")
        prompt_text = prompt_path.read_text(encoding="utf-8")

        result = replace_prompt_variables(prompt_text, block)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 500)

        # Verify no unreplaced tokens remain
        import re
        unreplaced = re.findall(r'\{[a-z_]+\}', result)
        self.assertEqual(len(unreplaced), 0,
                         f"Unreplaced tokens: {unreplaced}")

        print(f"\n  Prompt after variable replacement: {len(result)} chars, "
              f"0 unreplaced tokens")
        self.__class__._general_prompt = result


# ═══════════════════════════════════════════════════════════════════════════
# TEST CLASS: Live Gemini Report Generation
# ═══════════════════════════════════════════════════════════════════════════

@skip_unless_live
class TestLiveReportGeneration(unittest.TestCase):
    """Test actual report generation via Gemini CLI."""

    @classmethod
    def setUpClass(cls):
        from src.data.db_manager import DatabaseManager
        from src.gemini.gemini_client import GeminiClient

        cls.db_path = _PROD_DB
        cls.db = DatabaseManager(cls.db_path)
        cls.db.initialize()

        # Build Gemini client from config
        cfg = _get_settings()
        gemini_cfg = cfg.get("gemini", {})
        cls.client = GeminiClient(
            cli_path=gemini_cfg.get("cli_path", ""),
            model=gemini_cfg.get("model", "gemini-2.5-flash"),
            temperature=gemini_cfg.get("temperature", 0.2),
            pii_redaction=gemini_cfg.get("pii_redaction", True),
        )

        # Verify Gemini is available
        if not cls.client.is_available():
            raise unittest.SkipTest("Gemini CLI not available")

        # Date range
        row = cls.db.conn.execute(
            "SELECT MIN(created_at) as mn, MAX(created_at) as mx FROM conversations"
        ).fetchone()
        cls.date_start = row["mn"][:10] if row["mn"] else "2024-01-01"
        cls.date_end = row["mx"][:10] if row["mx"] else "2025-12-31"

        # Pre-build data block (shared across all report tests)
        from src.data.report_builder import build_data_block
        cls.block = build_data_block(cls.db, cls.date_start, cls.date_end)

        # Track results for dependent tests
        cls._results = {}

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def _generate_report(self, prompt_file, system_prompt):
        """Helper: load prompt, replace vars, generate via Gemini."""
        from src.data.report_builder import replace_prompt_variables

        prompt_path = _PROJECT_ROOT / "config" / "prompts" / prompt_file
        if not prompt_path.exists():
            self.skipTest(f"{prompt_file} not found")
        prompt_text = prompt_path.read_text(encoding="utf-8")

        prompt = replace_prompt_variables(prompt_text, self.block)
        t0 = time.time()
        result = self.client.generate(prompt, system_prompt=system_prompt,
                                      timeout=300)
        elapsed = time.time() - t0
        return result, elapsed

    def test_01_general_trend_report(self):
        """General Trend Analysis produces substantive output."""
        rt = REPORT_TYPES[0]
        result, elapsed = self._generate_report(
            rt["prompt_file"], rt["system_prompt"])

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 200,
                           f"Report too short ({len(result)} chars)")

        print(f"\n  General Trend: {len(result)} chars, {elapsed:.1f}s")
        self.__class__._results["general_trend"] = result

    def test_02_executive_summary_report(self):
        """Executive Summary produces substantive output."""
        rt = REPORT_TYPES[1]
        result, elapsed = self._generate_report(
            rt["prompt_file"], rt["system_prompt"])

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 300,
                           f"Report too short ({len(result)} chars)")

        print(f"\n  Executive Summary: {len(result)} chars, {elapsed:.1f}s")
        self.__class__._results["executive_summary"] = result

    def test_03_incident_summary_report(self):
        """Incident Summary produces substantive output."""
        rt = REPORT_TYPES[2]
        result, elapsed = self._generate_report(
            rt["prompt_file"], rt["system_prompt"])

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 300,
                           f"Report too short ({len(result)} chars)")

        print(f"\n  Incident Summary: {len(result)} chars, {elapsed:.1f}s")
        self.__class__._results["incident_summary"] = result

    def test_04_report_save_and_load(self):
        """Save a report to DB and load it back — round-trip check."""
        results = getattr(self.__class__, '_results', {})
        report_text = results.get("general_trend", "")
        if not report_text:
            self.skipTest("test_01 did not produce a report")

        # Save using actual DB API: save_report(page, parameters, summary, full_results, ...)
        self.db.save_report(
            page="ai_reports_e2e_test",
            parameters={"prompt": "General Trend Analysis",
                        "date_start": self.date_start,
                        "date_end": self.date_end},
            summary=report_text[:500],
            full_results=report_text,
            ticket_count=self.block.get("ticket_count", 0),
            notes="E2E live test",
        )

        # Load the most recent report for this page
        reports = self.db.get_reports("ai_reports_e2e_test", limit=1)
        self.assertGreater(len(reports), 0, "Should have saved report")

        report_id = reports[0]["report_id"]
        loaded = self.db.get_full_report(report_id)
        self.assertIsNotNone(loaded, "get_full_report should return data")
        self.assertEqual(loaded["full_results"], report_text,
                         "Report text should round-trip exactly")

        print(f"\n  Report saved (id={report_id}), "
              f"loaded back ({len(loaded['full_results'])} chars) — match OK")

    def test_05_report_history_list(self):
        """Report history lists reports for this page."""
        history = self.db.get_reports("ai_reports_e2e_test", limit=5)
        self.assertIsInstance(history, list)
        # Only assert > 0 if test_04 ran successfully
        if getattr(self.__class__, '_results', {}).get("general_trend"):
            self.assertGreater(len(history), 0,
                               "Should have at least 1 report after test_04")
        print(f"\n  Report history: {len(history)} reports for e2e test page")


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not LIVE_MODE:
        print(
            "\n  This test requires a live Gemini connection.\n"
            "  Run with: python tests/test_ai_reports_live.py --live\n"
            "  Or set:   ALMA_LIVE_TEST=1\n"
        )
        sys.exit(0)

    print("\n  ALMA INSIGHTS — AI REPORTS LIVE E2E TEST")
    print("  " + "=" * 50)
    unittest.main(verbosity=2)
