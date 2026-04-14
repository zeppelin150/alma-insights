"""
Phase 1 regression tests — seed_incidents script.

Asserts that scripts/seed_incidents.py correctly:
  - populates the `incidents` table from manifest['incidents'] with
    expected_vernacular synthesized from description + concept_id
  - populates `test_concept_ground_truth` from manifest['cluster_ground_truth']
  - is idempotent (second run doesn't duplicate or clobber)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.data.connection_factory import get_connection


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = PROJECT_ROOT / "scripts" / "seed_incidents.py"


@pytest.fixture
def migrated_db(empty_db):
    """empty_db fixture already runs db_manager.initialize() + all migrations."""
    db_path = empty_db.db_path
    # Close so the subprocess can open it for writes without lock contention
    try:
        empty_db.conn.close()
    except Exception:
        pass
    return Path(db_path)


@pytest.fixture
def fake_manifest(tmp_path):
    manifest = {
        "total_tickets": 3,
        "incidents": {
            "incident_portal_outage_0301": {
                "description": "Portal 500 error outage on March 1",
                "expected_concept": "portal_access_fail",
                "tagged_tickets": 25,
                "correct_tags": 17,
                "mis_tags": 8,
            },
            "incident_small_event": {
                "description": "A minor glitch",
                "expected_concept": "minor_glitch",
                "tagged_tickets": 2,
                "correct_tags": 2,
                "mis_tags": 0,
            },
        },
        "cluster_ground_truth": {
            "portal_access_fail": ["T100", "T101", "T102"],
            "minor_glitch": ["T200"],
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _run_seed(db_path: Path, manifest_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT),
         "--db", str(db_path),
         "--manifest", str(manifest_path)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )


class TestSeedIncidents:
    def test_smoke_run_exits_clean(self, migrated_db, fake_manifest):
        result = _run_seed(migrated_db, fake_manifest)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    def test_incidents_table_populated(self, migrated_db, fake_manifest):
        _run_seed(migrated_db, fake_manifest)
        conn = get_connection(migrated_db, readonly=True)
        try:
            rows = conn.execute(
                "SELECT incident_id, description, expected_vernacular, expected_canonical_cluster "
                "FROM incidents ORDER BY incident_id"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        ids = {r[0] for r in rows}
        assert "incident_portal_outage_0301" in ids
        # expected_canonical_cluster stays NULL until Phase 5 back-fill
        for r in rows:
            assert r[3] is None

    def test_vernacular_includes_mined_terms(self, migrated_db, fake_manifest):
        _run_seed(migrated_db, fake_manifest)
        conn = get_connection(migrated_db, readonly=True)
        try:
            row = conn.execute(
                "SELECT expected_vernacular FROM incidents "
                "WHERE incident_id = 'incident_portal_outage_0301'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        vernacular = json.loads(row[0])
        # Terms from description + concept_id tokens (stopwords stripped)
        assert "portal" in vernacular
        assert "access" in vernacular
        assert "fail" in vernacular
        assert "outage" in vernacular

    def test_ground_truth_populated(self, migrated_db, fake_manifest):
        _run_seed(migrated_db, fake_manifest)
        conn = get_connection(migrated_db, readonly=True)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM test_concept_ground_truth WHERE source = 'manifest'"
            ).fetchone()[0]
            portal_tickets = conn.execute(
                "SELECT ticket_id FROM test_concept_ground_truth "
                "WHERE concept_id = 'portal_access_fail' ORDER BY ticket_id"
            ).fetchall()
        finally:
            conn.close()
        assert count == 4   # 3 + 1
        assert [r[0] for r in portal_tickets] == ["T100", "T101", "T102"]

    def test_idempotent(self, migrated_db, fake_manifest):
        """Re-running should not duplicate rows or error."""
        _run_seed(migrated_db, fake_manifest)
        _run_seed(migrated_db, fake_manifest)
        conn = get_connection(migrated_db, readonly=True)
        try:
            inc_count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            gt_count = conn.execute(
                "SELECT COUNT(*) FROM test_concept_ground_truth"
            ).fetchone()[0]
        finally:
            conn.close()
        assert inc_count == 2
        assert gt_count == 4

    def test_dry_run_writes_nothing(self, migrated_db, fake_manifest):
        result = subprocess.run(
            [sys.executable, str(SEED_SCRIPT),
             "--db", str(migrated_db),
             "--manifest", str(fake_manifest),
             "--dry-run"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 0
        conn = get_connection(migrated_db, readonly=True)
        try:
            count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_missing_manifest_errors(self, migrated_db, tmp_path):
        bogus = tmp_path / "nope.json"
        result = _run_seed(migrated_db, bogus)
        assert result.returncode != 0
        assert "not found" in (result.stderr + result.stdout).lower()
