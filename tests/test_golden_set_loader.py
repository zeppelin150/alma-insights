"""Tests for src/data/canonicalization/golden_set.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.data.canonicalization.golden_set import load_pairs, pair_stats


def _write_csv(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class TestLoadPairs:
    def test_basic_roundtrip(self, tmp_path):
        csv = _write_csv(tmp_path / "g.csv", [
            "pair_id,ticket_a_id,ticket_b_id,same_concept,concept_id",
            "1,101,102,True,foo",
            "2,103,104,False,bar",
        ])
        pairs = load_pairs(csv)
        assert pairs == [("101", "102", True), ("103", "104", False)]

    def test_case_insensitive_header(self, tmp_path):
        csv = _write_csv(tmp_path / "g.csv", [
            "Pair_Id,TICKET_A_ID,Ticket_B_Id,Same_Concept",
            "1,11,12,true",
        ])
        pairs = load_pairs(csv)
        assert pairs == [("11", "12", True)]

    def test_boolean_variants(self, tmp_path):
        csv = _write_csv(tmp_path / "g.csv", [
            "ticket_a_id,ticket_b_id,same_concept",
            "1,2,TRUE",
            "3,4,false",
            "5,6,1",
            "7,8,0",
            "9,10,yes",
            "11,12,no",
        ])
        pairs = load_pairs(csv)
        assert [p[2] for p in pairs] == [True, False, True, False, True, False]

    def test_missing_columns_raises(self, tmp_path):
        csv = _write_csv(tmp_path / "g.csv", [
            "pair_id,ticket_a,ticket_b,same",
            "1,1,2,true",
        ])
        with pytest.raises(ValueError, match="missing required columns"):
            load_pairs(csv)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_pairs(tmp_path / "nope.csv")

    def test_blank_rows_skipped(self, tmp_path):
        csv = _write_csv(tmp_path / "g.csv", [
            "ticket_a_id,ticket_b_id,same_concept",
            "1,2,true",
            ",,",
            "3,,true",
            "4,5,false",
        ])
        pairs = load_pairs(csv)
        assert len(pairs) == 2

    def test_pair_stats(self):
        pairs = [("1", "2", True), ("3", "4", True), ("5", "6", False)]
        stats = pair_stats(pairs)
        assert stats == {"total": 3, "same": 2, "different": 1}

    def test_real_golden_set_loads(self):
        """Smoke test — parse the actual test golden set if present.

        Honours ALMA_GOLDEN_SET_CSV env var; otherwise looks at the
        gitignored data/test_fixtures/ path. Skips cleanly when absent.
        """
        import os
        real = Path(os.environ.get(
            "ALMA_GOLDEN_SET_CSV",
            "data/test_fixtures/alma_test_10000_1_golden_set.csv",
        ))
        if not real.is_file():
            pytest.skip("Real golden set CSV not available")
        pairs = load_pairs(real)
        stats = pair_stats(pairs)
        assert stats["total"] > 400
        assert stats["same"] > 100
        assert stats["different"] > 100
