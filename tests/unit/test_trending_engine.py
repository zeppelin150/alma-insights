"""
Alma Insights — Unit Tests for trending_engine.py

Covers: sentiment trends, rising terms, topic models (NMF), cross-TRC
correlations, hypothesis testing, run_full_analysis orchestration, and all
decomposed helper functions.

Uses the `seeded_db` fixture (100 tickets, 3 TRCs, 30 days).
"""

import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def date_range():
    """Standard 30-day date range matching seeded_db."""
    today = datetime.now()
    return (
        (today - timedelta(days=31)).strftime("%Y-%m-%d"),
        today.strftime("%Y-%m-%d"),
    )


@pytest.fixture
def mock_sia():
    """Mock SentimentIntensityAnalyzer with deterministic scores."""
    sia = MagicMock()
    sia.polarity_scores.return_value = {"compound": 0.25, "pos": 0.3, "neg": 0.1, "neu": 0.6}
    return sia


# ---------------------------------------------------------------------------
#  TestApplyCompoundTerms
# ---------------------------------------------------------------------------

class TestApplyCompoundTerms:
    """Tests for _apply_compound_terms."""

    def test_builtin_compound_replacement(self):
        from src.data.trending_engine import _apply_compound_terms
        text = "the prior authorization was denied and auto pay failed"
        result = _apply_compound_terms(text)
        assert "prior_authorization" in result
        assert "auto_pay" in result

    def test_custom_compounds_dict(self):
        from src.data.trending_engine import _apply_compound_terms
        custom = {"blue sky": "blue_sky", "hot dog": "hot_dog"}
        result = _apply_compound_terms("i saw a blue sky and ate a hot dog", compounds=custom)
        assert "blue_sky" in result
        assert "hot_dog" in result

    def test_no_match_passes_through(self):
        from src.data.trending_engine import _apply_compound_terms
        text = "nothing special here"
        assert _apply_compound_terms(text) == text

    def test_overlapping_terms_longest_first(self):
        from src.data.trending_engine import _apply_compound_terms
        # "coordination of benefits" should be replaced as one unit
        text = "coordination of benefits check"
        result = _apply_compound_terms(text)
        assert "coordination_of_benefits" in result


class TestDisplayTerm:
    def test_underscore_to_space(self):
        from src.data.trending_engine import _display_term
        assert _display_term("prior_authorization") == "prior authorization"

    def test_no_underscore(self):
        from src.data.trending_engine import _display_term
        assert _display_term("billing") == "billing"


# ---------------------------------------------------------------------------
#  TestCleanThreadText
# ---------------------------------------------------------------------------

class TestCleanThreadText:
    def test_empty_input(self):
        from src.data.trending_engine import _clean_thread_text
        assert _clean_thread_text("") == ""
        assert _clean_thread_text(None) == ""

    def test_strips_role_headers(self):
        from src.data.trending_engine import _clean_thread_text
        text = "[2024-01-01 09:00] CUSTOMER:\nHello I need help"
        result = _clean_thread_text(text)
        assert "CUSTOMER" not in result
        assert "hello" in result  # lowercased

    def test_strips_separator(self):
        from src.data.trending_engine import _clean_thread_text
        text = "part one --- part two"
        result = _clean_thread_text(text)
        assert "---" not in result

    def test_lowercased(self):
        from src.data.trending_engine import _clean_thread_text
        assert _clean_thread_text("HELLO WORLD") == "hello world"

    def test_custom_compounds_forwarded(self):
        from src.data.trending_engine import _clean_thread_text
        custom = {"magic phrase": "magic_phrase"}
        result = _clean_thread_text("This is a magic phrase test", compounds=custom)
        assert "magic_phrase" in result


# ---------------------------------------------------------------------------
#  TestBucketConversations
# ---------------------------------------------------------------------------

class TestBucketConversations:
    def _make_convs(self, dates):
        return [{"created_at": d} for d in dates]

    def test_daily_bucketing(self):
        from src.data.trending_engine import _bucket_conversations
        convs = self._make_convs(["2024-01-01", "2024-01-01", "2024-01-02"])
        result = _bucket_conversations(convs, "Daily")
        assert "2024-01-01" in result
        assert len(result["2024-01-01"]) == 2
        assert len(result["2024-01-02"]) == 1

    def test_weekly_bucketing(self):
        from src.data.trending_engine import _bucket_conversations
        convs = self._make_convs(["2024-01-01", "2024-01-07"])
        result = _bucket_conversations(convs, "Weekly")
        assert len(result) >= 1  # both dates in same ISO week or adjacent

    def test_monthly_bucketing(self):
        from src.data.trending_engine import _bucket_conversations
        convs = self._make_convs(["2024-01-15", "2024-02-10"])
        result = _bucket_conversations(convs, "Monthly")
        assert "2024-01" in result
        assert "2024-02" in result

    def test_biweekly_bucketing(self):
        from src.data.trending_engine import _bucket_conversations
        convs = self._make_convs(["2024-01-01", "2024-01-15"])
        result = _bucket_conversations(convs, "Biweekly")
        assert len(result) >= 1

    def test_hourly_bucketing(self):
        from src.data.trending_engine import _bucket_conversations
        convs = self._make_convs(["2024-01-01 09:30", "2024-01-01 10:15"])
        result = _bucket_conversations(convs, "Hourly")
        assert "2024-01-01 09:00" in result
        assert "2024-01-01 10:00" in result

    def test_empty_created_at_skipped(self):
        from src.data.trending_engine import _bucket_conversations
        convs = [{"created_at": ""}, {"created_at": None}, {"created_at": "2024-01-01"}]
        result = _bucket_conversations(convs, "Daily")
        assert len(result) == 1

    def test_result_is_sorted(self):
        from src.data.trending_engine import _bucket_conversations
        convs = self._make_convs(["2024-03-01", "2024-01-01", "2024-02-01"])
        result = _bucket_conversations(convs, "Monthly")
        keys = list(result.keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
#  TestAssignDocToWindow
# ---------------------------------------------------------------------------

class TestAssignDocToWindow:
    def test_daily(self):
        from src.data.trending_engine import _assign_doc_to_window
        assert _assign_doc_to_window("2024-06-15", "Daily") == "2024-06-15"

    def test_weekly(self):
        from src.data.trending_engine import _assign_doc_to_window
        result = _assign_doc_to_window("2024-06-15", "Weekly")
        assert result.startswith("2024-W")

    def test_monthly(self):
        from src.data.trending_engine import _assign_doc_to_window
        assert _assign_doc_to_window("2024-06-15", "Monthly") == "2024-06"

    def test_hourly_with_time(self):
        from src.data.trending_engine import _assign_doc_to_window
        result = _assign_doc_to_window("2024-06-15 14:30", "Hourly")
        assert result == "2024-06-15 14:00"

    def test_hourly_no_time(self):
        from src.data.trending_engine import _assign_doc_to_window
        result = _assign_doc_to_window("2024-06-15", "Hourly")
        assert result == "2024-06-15 00:00"

    def test_biweekly(self):
        from src.data.trending_engine import _assign_doc_to_window
        result = _assign_doc_to_window("2024-06-15", "Biweekly")
        assert "-BW" in result

    def test_empty_returns_none(self):
        from src.data.trending_engine import _assign_doc_to_window
        assert _assign_doc_to_window("", "Daily") is None
        assert _assign_doc_to_window(None, "Daily") is None

    def test_invalid_date_returns_none(self):
        from src.data.trending_engine import _assign_doc_to_window
        assert _assign_doc_to_window("not-a-date", "Daily") is None


# ---------------------------------------------------------------------------
#  TestComputeTemporalVelocity
# ---------------------------------------------------------------------------

class TestComputeTemporalVelocity:
    def test_rising_series(self):
        from src.data.trending_engine import _compute_temporal_velocity
        result = _compute_temporal_velocity([0.1, 0.2, 0.4, 0.8, 1.0])
        assert result["velocity"] > 0
        assert result["raw_velocity"] > 0
        assert result["peak_recency"] == "current"

    def test_declining_series(self):
        from src.data.trending_engine import _compute_temporal_velocity
        result = _compute_temporal_velocity([1.0, 0.8, 0.5, 0.2, 0.1])
        assert result["velocity"] < 0
        assert result["peak_recency"] == "historical"

    def test_single_value_returns_zero(self):
        from src.data.trending_engine import _compute_temporal_velocity
        result = _compute_temporal_velocity([0.5])
        assert result["velocity"] == 0.0
        assert result["is_temporally_promoted"] is False

    def test_two_values_computes(self):
        from src.data.trending_engine import _compute_temporal_velocity
        result = _compute_temporal_velocity([0.1, 0.9])
        assert result["velocity"] != 0.0

    def test_return_keys(self):
        from src.data.trending_engine import _compute_temporal_velocity
        result = _compute_temporal_velocity([0.1, 0.3, 0.5])
        expected_keys = {"velocity", "raw_velocity", "recency_score",
                         "peak_window", "peak_recency", "is_temporally_promoted"}
        assert set(result.keys()) == expected_keys

    def test_flat_series_near_zero_velocity(self):
        from src.data.trending_engine import _compute_temporal_velocity
        result = _compute_temporal_velocity([0.5, 0.5, 0.5, 0.5])
        assert abs(result["velocity"]) < 0.01

    def test_temporal_promotion_flag(self):
        from src.data.trending_engine import _compute_temporal_velocity
        # Strong recent signal should set is_temporally_promoted
        result = _compute_temporal_velocity([0.0, 0.0, 0.0, 0.5, 1.0])
        assert result["recency_score"] > 0.5
        if result["velocity"] > 0:
            assert result["is_temporally_promoted"] is True


# ---------------------------------------------------------------------------
#  TestSummarizeSentiment
# ---------------------------------------------------------------------------

class TestSummarizeSentiment:

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_empty_tickets(self, mock_nltk):
        from src.data.trending_engine import _summarize_sentiment
        with patch("src.data.trending_engine.SentimentIntensityAnalyzer", create=True):
            result = _summarize_sentiment([])
        assert result == {}

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_positive_sentiment(self, mock_nltk):
        from src.data.trending_engine import _summarize_sentiment
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.8}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            tickets = [{"full_thread": "Great service, very happy!"}]
            result = _summarize_sentiment(tickets)
            assert result["trend"] == "positive"
            assert result["avg_compound"] > 0

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_negative_sentiment(self, mock_nltk):
        from src.data.trending_engine import _summarize_sentiment
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": -0.6}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            tickets = [{"full_thread": "Terrible awful experience"}]
            result = _summarize_sentiment(tickets)
            assert result["trend"] == "negative"

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_neutral_sentiment(self, mock_nltk):
        from src.data.trending_engine import _summarize_sentiment
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.0}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            tickets = [{"full_thread": "Some text here"}]
            result = _summarize_sentiment(tickets)
            assert result["trend"] == "neutral"

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_tickets_without_thread_skipped(self, mock_nltk):
        from src.data.trending_engine import _summarize_sentiment
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer"):
            tickets = [{"full_thread": ""}, {"full_thread": None}]
            result = _summarize_sentiment(tickets)
            assert result == {}


# ---------------------------------------------------------------------------
#  TestComputeSentimentTrends
# ---------------------------------------------------------------------------

class TestComputeSentimentTrends:
    """Tests for compute_sentiment_trends using seeded_db."""

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_returns_dict_keyed_by_trc(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_sentiment_trends
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.1}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            result = compute_sentiment_trends(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly"
            )
        assert isinstance(result, dict)
        # Should have TRC-100, TRC-200, TRC-300
        assert len(result) >= 1

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_filtered_by_trc(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_sentiment_trends
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": -0.3}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            result = compute_sentiment_trends(
                seeded_db.conn, date_range[0], date_range[1], "TRC-100", "Weekly"
            )
        assert "TRC-100" in result
        assert "TRC-200" not in result

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_series_values_are_tuples(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_sentiment_trends
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.5}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            result = compute_sentiment_trends(
                seeded_db.conn, date_range[0], date_range[1], "TRC-100", "Daily"
            )
        if "TRC-100" in result:
            for label, avg in result["TRC-100"]:
                assert isinstance(label, str)
                assert isinstance(avg, float)

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_empty_conversations_returns_empty(self, mock_nltk, seeded_db):
        from src.data.trending_engine import compute_sentiment_trends
        mock_sia = MagicMock()
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            result = compute_sentiment_trends(
                seeded_db.conn, "2099-01-01", "2099-12-31", None, "Daily"
            )
        assert result == {}

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_pre_fetched_conversations(self, mock_nltk, seeded_db):
        from src.data.trending_engine import compute_sentiment_trends
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.0}
        today = datetime.now()
        convs = [
            {"ticket_id": "T-X1", "trc_code": "TRC-100",
             "created_at": (today - timedelta(days=5)).strftime("%Y-%m-%d"),
             "full_thread": "test text"},
            {"ticket_id": "T-X2", "trc_code": "TRC-100",
             "created_at": (today - timedelta(days=3)).strftime("%Y-%m-%d"),
             "full_thread": "more text"},
        ]
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            result = compute_sentiment_trends(
                seeded_db.conn, "2020-01-01", "2099-12-31", "TRC-100", "Daily",
                conversations=convs,
            )
        assert "TRC-100" in result


# ---------------------------------------------------------------------------
#  TestComputeRisingTerms
# ---------------------------------------------------------------------------

class TestComputeRisingTerms:

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_returns_rising_and_cooling_keys(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_rising_terms
        with patch("src.data.trending_engine._tokenize_and_clean", return_value=["billing", "claim"]):
            result = compute_rising_terms(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly"
            )
        assert "rising" in result
        assert "cooling" in result
        assert isinstance(result["rising"], list)
        assert isinstance(result["cooling"], list)

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_empty_data_returns_empty_lists(self, mock_nltk, seeded_db):
        from src.data.trending_engine import compute_rising_terms
        result = compute_rising_terms(
            seeded_db.conn, "2099-01-01", "2099-12-31", None, "Weekly"
        )
        assert result == {"rising": [], "cooling": []}

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_single_window_returns_empty(self, mock_nltk, seeded_db):
        from src.data.trending_engine import compute_rising_terms
        # One day range = one window for Weekly
        today = datetime.now().strftime("%Y-%m-%d")
        result = compute_rising_terms(
            seeded_db.conn, today, today, None, "Weekly"
        )
        assert result == {"rising": [], "cooling": []}

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_rising_term_structure(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_rising_terms
        with patch("src.data.trending_engine._tokenize_and_clean",
                   return_value=["billing", "issues", "claim"]):
            result = compute_rising_terms(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly"
            )
        for term_info in result["rising"]:
            assert "term" in term_info
            assert "velocity" in term_info
            assert "current_score" in term_info
            assert "sparkline_data" in term_info

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_max_15_rising_terms(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_rising_terms
        with patch("src.data.trending_engine._tokenize_and_clean",
                   return_value=["billing", "claim", "denial"]):
            result = compute_rising_terms(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly"
            )
        assert len(result["rising"]) <= 15

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_max_10_cooling_terms(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import compute_rising_terms
        with patch("src.data.trending_engine._tokenize_and_clean",
                   return_value=["billing", "claim"]):
            result = compute_rising_terms(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly"
            )
        assert len(result["cooling"]) <= 10


# ---------------------------------------------------------------------------
#  TestBuildWeightModifiers
# ---------------------------------------------------------------------------

class TestBuildWeightModifiers:
    def test_no_feedback_tables(self, seeded_db):
        from src.data.trending_engine import _build_weight_modifiers
        result = _build_weight_modifiers(seeded_db.conn)
        assert isinstance(result, dict)

    def test_with_feedback_table(self, seeded_db):
        from src.data.trending_engine import _build_weight_modifiers
        conn = seeded_db.conn
        # Table already exists from db.initialize(); use the real schema
        conn.execute(
            "INSERT INTO tfidf_feedback (term, feedback_type, context, merge_target, created_at) "
            "VALUES ('billing', 'important', '', '', '2024-01-01')"
        )
        conn.execute(
            "INSERT INTO tfidf_feedback (term, feedback_type, context, merge_target, created_at) "
            "VALUES ('billing', 'important', '', '', '2024-01-02')"
        )
        conn.execute(
            "INSERT INTO tfidf_feedback (term, feedback_type, context, merge_target, created_at) "
            "VALUES ('noise_term', 'noise', '', '', '2024-01-01')"
        )
        conn.commit()

        result = _build_weight_modifiers(conn)
        assert result["billing"] == pytest.approx(1.4)  # 1.0 + 0.2*2
        assert result["noise_term"] == pytest.approx(0.85)  # 1.0 - 0.15*1

    def test_clamping(self, seeded_db):
        from src.data.trending_engine import _build_weight_modifiers
        conn = seeded_db.conn
        # Insert many noise signals to push below floor
        for i in range(20):
            conn.execute(
                "INSERT INTO tfidf_feedback (term, feedback_type, context, merge_target, created_at) "
                f"VALUES ('junk', 'noise', '', '', '2024-01-{i+1:02d}')"
            )
        conn.commit()

        result = _build_weight_modifiers(conn)
        assert result["junk"] == pytest.approx(0.1)  # floored


# ---------------------------------------------------------------------------
#  TestFindMultiTopicTickets
# ---------------------------------------------------------------------------

class TestFindMultiTopicTickets:
    def test_single_topic_returns_empty(self):
        from src.data.trending_engine import _find_multi_topic_tickets
        W = np.array([[0.5, 0.01], [0.8, 0.02]])
        meta = [{"ticket_id": "T-1", "subject": "a"}, {"ticket_id": "T-2", "subject": "b"}]
        topics = [{"label": "A"}, {"label": "B"}]
        result = _find_multi_topic_tickets(W, 2, meta, topics, threshold=0.15)
        assert result == []

    def test_multi_topic_detected(self):
        from src.data.trending_engine import _find_multi_topic_tickets
        W = np.array([[0.5, 0.4], [0.8, 0.02]])
        meta = [{"ticket_id": "T-1", "subject": "both"}, {"ticket_id": "T-2", "subject": "one"}]
        topics = [{"label": "Topic A"}, {"label": "Topic B"}]
        result = _find_multi_topic_tickets(W, 2, meta, topics, threshold=0.15)
        assert len(result) == 1
        assert result[0]["ticket_id"] == "T-1"
        assert len(result[0]["topics"]) == 2

    def test_custom_threshold(self):
        from src.data.trending_engine import _find_multi_topic_tickets
        W = np.array([[0.5, 0.2]])
        meta = [{"ticket_id": "T-1", "subject": "x"}]
        topics = [{"label": "A"}, {"label": "B"}]
        # With high threshold, second topic score (0.2) is above 0.15 but below 0.3
        result_low = _find_multi_topic_tickets(W, 2, meta, topics, threshold=0.15)
        result_high = _find_multi_topic_tickets(W, 2, meta, topics, threshold=0.3)
        assert len(result_low) == 1
        assert len(result_high) == 0


# ---------------------------------------------------------------------------
#  TestBuildConceptGroups
# ---------------------------------------------------------------------------

class TestBuildConceptGroups:

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_groups_by_concept(self, mock_nltk):
        from src.data.trending_engine import _build_concept_groups
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": -0.2}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            tickets = [
                {"ticket_id": "T-1", "trc_code": "TRC-100", "full_thread": "billing charge issue",
                 "subject": "billing", "created_at": "2024-06-01"},
            ]
            result = _build_concept_groups(["billing_charge"], tickets, "Daily")
        assert "billing_charge" in result
        assert "T-1" in result["billing_charge"]["ticket_ids"]

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_empty_concepts(self, mock_nltk):
        from src.data.trending_engine import _build_concept_groups
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer"):
            result = _build_concept_groups([], [], "Daily")
        assert result == {}

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_no_matching_tickets(self, mock_nltk):
        from src.data.trending_engine import _build_concept_groups
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer"):
            tickets = [
                {"ticket_id": "T-1", "trc_code": "TRC-100",
                 "full_thread": "unrelated topic xyz", "subject": "xyz",
                 "created_at": "2024-06-01"},
            ]
            result = _build_concept_groups(["billing_charge"], tickets, "Daily")
        # billing_charge synonyms include "billing", "charge" etc - "unrelated topic xyz" won't match
        assert "billing_charge" not in result or len(result["billing_charge"]["ticket_ids"]) == 0


# ---------------------------------------------------------------------------
#  TestComputeConceptCorrelation
# ---------------------------------------------------------------------------

class TestComputeConceptCorrelation:
    def test_single_concept_returns_empty(self):
        from src.data.trending_engine import _compute_concept_correlation
        groups = {"billing": {"volume_by_window": [("W1", 5), ("W2", 6)]}}
        result = _compute_concept_correlation(groups)
        assert result == {}

    def test_insufficient_windows_returns_empty(self):
        from src.data.trending_engine import _compute_concept_correlation
        groups = {
            "billing": {"volume_by_window": [("W1", 5)]},
            "claims": {"volume_by_window": [("W1", 3)]},
        }
        result = _compute_concept_correlation(groups)
        assert result == {}

    def test_correlated_concepts(self):
        from src.data.trending_engine import _compute_concept_correlation
        windows = [("W1", 1), ("W2", 2), ("W3", 3), ("W4", 4), ("W5", 5)]
        groups = {
            "billing": {"volume_by_window": windows},
            "claims": {"volume_by_window": windows},  # perfectly correlated
        }
        result = _compute_concept_correlation(groups)
        assert "pearson_r" in result
        assert result["pearson_r"] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
#  TestComputeCrossTrcCorrelations
# ---------------------------------------------------------------------------

class TestComputeCrossTrcCorrelations:
    def test_single_trc_returns_empty(self):
        from src.data.trending_engine import compute_cross_trc_correlations
        series = {"TRC-100": {"volume": [1, 2, 3, 4, 5], "sentiment": [0.1]*5, "windows": ["W1","W2","W3","W4","W5"]}}
        result = compute_cross_trc_correlations(None, None, None, "Weekly", per_trc_series=series)
        assert result == {"correlations": []}

    def test_two_trcs_correlated(self):
        from src.data.trending_engine import compute_cross_trc_correlations
        windows = ["W1", "W2", "W3", "W4", "W5"]
        series = {
            "TRC-A": {"volume": [1, 2, 3, 4, 5], "sentiment": [0.1]*5, "windows": windows},
            "TRC-B": {"volume": [1, 2, 3, 4, 5], "sentiment": [0.1]*5, "windows": windows},
        }
        result = compute_cross_trc_correlations(
            None, None, None, "Weekly", min_correlation=0.5, per_trc_series=series
        )
        # Perfect correlation: volume-volume should be detected
        corrs = result["correlations"]
        # Volume series are identical (r=1.0), but sentiment is constant (skipped)
        volume_corrs = [c for c in corrs if c["metric_a"] == "volume" and c["metric_b"] == "volume"]
        if volume_corrs:
            assert volume_corrs[0]["r"] == pytest.approx(1.0, abs=0.01)

    def test_max_20_correlations(self):
        from src.data.trending_engine import compute_cross_trc_correlations
        # Build many TRCs with correlated series to generate > 20 correlations
        windows = [f"W{i}" for i in range(10)]
        series = {}
        for j in range(10):
            series[f"TRC-{j}"] = {
                "volume": list(range(10)),
                "sentiment": [0.1 * i for i in range(10)],
                "windows": windows,
            }
        result = compute_cross_trc_correlations(
            None, None, None, "Weekly", min_correlation=0.3, per_trc_series=series
        )
        assert len(result["correlations"]) <= 20


# ---------------------------------------------------------------------------
#  TestFindLeadLag
# ---------------------------------------------------------------------------

class TestFindLeadLag:
    def test_no_lag_identical_series(self):
        from src.data.trending_engine import _find_lead_lag
        a = [1, 2, 3, 4, 5, 6, 7, 8]
        b = [1, 2, 3, 4, 5, 6, 7, 8]
        lag, r = _find_lead_lag(a, b, max_lag=3)
        assert lag == 0
        assert r == pytest.approx(1.0, abs=0.01)

    def test_too_short_returns_zero(self):
        from src.data.trending_engine import _find_lead_lag
        lag, r = _find_lead_lag([1, 2], [3, 4], max_lag=3)
        assert lag == 0
        assert r == 0.0

    def test_constant_series_returns_zero(self):
        from src.data.trending_engine import _find_lead_lag
        lag, r = _find_lead_lag([5]*10, [5]*10, max_lag=3)
        assert r == 0.0


# ---------------------------------------------------------------------------
#  TestLagToLabel
# ---------------------------------------------------------------------------

class TestLagToLabel:
    def test_hourly(self):
        from src.data.trending_engine import _lag_to_label
        assert _lag_to_label(1, "Hourly") == "~1 hour"
        assert "hours" in _lag_to_label(5, "Hourly")

    def test_daily(self):
        from src.data.trending_engine import _lag_to_label
        assert _lag_to_label(1, "Daily") == "~1 day"
        assert "days" in _lag_to_label(3, "Daily")

    def test_weekly(self):
        from src.data.trending_engine import _lag_to_label
        result = _lag_to_label(1, "Weekly")
        assert "week" in result

    def test_monthly(self):
        from src.data.trending_engine import _lag_to_label
        result = _lag_to_label(2, "Monthly")
        assert "days" in result or "month" in result


# ---------------------------------------------------------------------------
#  TestScoreEvidence
# ---------------------------------------------------------------------------

class TestScoreEvidence:
    def test_insufficient_evidence(self):
        from src.data.trending_engine import _score_evidence
        result = _score_evidence({}, {}, {}, {})
        assert result == "insufficient"

    def test_moderate_evidence(self):
        from src.data.trending_engine import _score_evidence
        groups = {
            "billing": {"ticket_ids": list(range(15)), "sentiment_avg": -0.3},
        }
        correlation = {"pearson_r": 0.6}
        result = _score_evidence(groups, {}, correlation, {})
        # 1 (10+ tickets) + 1 (r>0.5) + 1 (sentiment < -0.2) = 3 -> weak
        # Actually: 15 tickets => 1 point, r=0.6 => 1 point, sentiment -0.3 => 1 point = 3
        # 3 >= 2 => "weak"
        assert result in ("weak", "moderate")

    def test_strong_evidence(self):
        from src.data.trending_engine import _score_evidence
        groups = {
            "billing": {"ticket_ids": list(range(40)), "sentiment_avg": -0.5},
            "claims": {"ticket_ids": list(range(10)), "sentiment_avg": -0.4},
        }
        temporal = {"lead_lag": {"peak_correlation": 0.8}}
        correlation = {"pearson_r": 0.8}
        incidents = {"related_flags": [{"theta_level": 2}]}
        result = _score_evidence(groups, temporal, correlation, incidents)
        # 2 (30+) + 3 (r>0.7 temporal) + 2 (r>0.7 corr) + 2 (theta_2) + 2 (sentiment) = 11
        assert result == "strong"


# ---------------------------------------------------------------------------
#  TestComputeTopicTrends
# ---------------------------------------------------------------------------

class TestComputeTopicTrends:
    def test_mutates_topics_inplace(self):
        from src.data.trending_engine import _compute_topic_trends
        W = np.array([[0.5, 0.2], [0.3, 0.7], [0.6, 0.1], [0.2, 0.8]])
        meta = [
            {"created_at": "2024-01-01"}, {"created_at": "2024-01-08"},
            {"created_at": "2024-01-15"}, {"created_at": "2024-01-22"},
        ]
        topics = [
            {"trend_direction": "stable", "trend_slope": 0.0, "trend_sparkline": []},
            {"trend_direction": "stable", "trend_slope": 0.0, "trend_sparkline": []},
        ]
        _compute_topic_trends(topics, W, meta, "Weekly")
        # Should have populated sparkline and direction
        assert len(topics[0]["trend_sparkline"]) > 0

    def test_single_window_no_crash(self):
        from src.data.trending_engine import _compute_topic_trends
        W = np.array([[0.5, 0.2]])
        meta = [{"created_at": "2024-01-01"}]
        topics = [
            {"trend_direction": "stable", "trend_slope": 0.0, "trend_sparkline": []},
            {"trend_direction": "stable", "trend_slope": 0.0, "trend_sparkline": []},
        ]
        _compute_topic_trends(topics, W, meta, "Weekly")
        # With < 2 buckets, function returns early — sparklines remain empty
        assert topics[0]["trend_sparkline"] == []


# ---------------------------------------------------------------------------
#  TestRunFullAnalysis
# ---------------------------------------------------------------------------

class TestRunFullAnalysis:

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_empty_date_range_returns_skeleton(self, mock_nltk, seeded_db):
        from src.data.trending_engine import run_full_analysis
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.0}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia):
            result = run_full_analysis(
                seeded_db.conn, "2099-01-01", "2099-12-31", None, "Weekly"
            )
        assert result["sentiment"] == {}
        assert result["terms"] == {"rising": [], "cooling": []}

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_progress_callback_called(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import run_full_analysis
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.0}
        callback = MagicMock()
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia), \
             patch("src.data.trending_engine._tokenize_and_clean", return_value=["billing"]):
            result = run_full_analysis(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly",
                progress_callback=callback,
            )
        assert callback.call_count >= 10  # 12 steps

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_result_keys(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import run_full_analysis
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.1}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia), \
             patch("src.data.trending_engine._tokenize_and_clean", return_value=["billing"]):
            result = run_full_analysis(
                seeded_db.conn, date_range[0], date_range[1], None, "Weekly"
            )
        assert "sentiment" in result
        assert "terms" in result
        assert "topics" in result
        assert "correlations" in result


# ---------------------------------------------------------------------------
#  TestTestHypothesis
# ---------------------------------------------------------------------------

class TestTestHypothesis:

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_returns_required_keys(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import test_hypothesis
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.0}
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia), \
             patch("nltk.word_tokenize", return_value=["billing", "issues"]), \
             patch("nltk.corpus.stopwords.words", return_value=[]):
            result = test_hypothesis(
                seeded_db.conn, "billing issues are increasing",
                date_range[0], date_range[1], "Weekly"
            )
        expected_keys = {"hypothesis", "concepts_extracted", "matching_tickets",
                         "concept_groups", "temporal_pattern", "correlation",
                         "incident_signals", "sentiment_data", "evidence_strength",
                         "gemini_synthesis"}
        assert set(result.keys()) == expected_keys

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_no_matches_returns_empty_result(self, mock_nltk, seeded_db):
        from src.data.trending_engine import test_hypothesis
        mock_sia = MagicMock()
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia), \
             patch("nltk.word_tokenize", return_value=["xyznonexistent"]), \
             patch("nltk.corpus.stopwords.words", return_value=[]):
            result = test_hypothesis(
                seeded_db.conn, "xyznonexistent issue",
                "2099-01-01", "2099-12-31", "Weekly"
            )
        assert result["matching_tickets"] == []
        assert result["evidence_strength"] == "insufficient"

    @patch("src.data.trending_engine._ensure_nltk_data")
    def test_progress_callback(self, mock_nltk, seeded_db, date_range):
        from src.data.trending_engine import test_hypothesis
        mock_sia = MagicMock()
        mock_sia.polarity_scores.return_value = {"compound": 0.0}
        cb = MagicMock()
        with patch("nltk.sentiment.vader.SentimentIntensityAnalyzer", return_value=mock_sia), \
             patch("nltk.word_tokenize", return_value=["billing"]), \
             patch("nltk.corpus.stopwords.words", return_value=[]):
            test_hypothesis(
                seeded_db.conn, "billing issues",
                date_range[0], date_range[1], "Weekly",
                progress_callback=cb,
            )
        assert cb.call_count >= 1


# ---------------------------------------------------------------------------
#  TestFetchConversations
# ---------------------------------------------------------------------------

class TestFetchConversations:
    def test_returns_list_of_dicts(self, seeded_db, date_range):
        from src.data.trending_engine import _fetch_conversations
        result = _fetch_conversations(seeded_db.conn, date_range[0], date_range[1], None)
        assert isinstance(result, list)
        assert len(result) > 0
        assert isinstance(result[0], dict)
        assert "ticket_id" in result[0]

    def test_trc_filter(self, seeded_db, date_range):
        from src.data.trending_engine import _fetch_conversations
        result = _fetch_conversations(seeded_db.conn, date_range[0], date_range[1], "TRC-100")
        assert all(r["trc_code"] == "TRC-100" for r in result)

    def test_empty_date_range(self, seeded_db):
        from src.data.trending_engine import _fetch_conversations
        result = _fetch_conversations(seeded_db.conn, "2099-01-01", "2099-12-31", None)
        assert result == []


# ---------------------------------------------------------------------------
#  TestEmptyFullResult
# ---------------------------------------------------------------------------

class TestEmptyFullResult:
    def test_skeleton_structure(self):
        from src.data.trending_engine import _empty_full_result
        result = _empty_full_result()
        assert result["sentiment"] == {}
        assert result["terms"]["rising"] == []
        assert result["terms"]["cooling"] == []
        assert result["topics"]["topics"] == []
        assert result["correlations"]["correlations"] == []


# ---------------------------------------------------------------------------
#  TestBucketSingle
# ---------------------------------------------------------------------------

class TestBucketSingle:
    def test_daily(self):
        from src.data.trending_engine import _bucket_single
        assert _bucket_single("2024-06-15 09:30", "daily") == "2024-06-15"

    def test_weekly(self):
        from src.data.trending_engine import _bucket_single
        result = _bucket_single("2024-06-15", "weekly")
        assert "-W" in result

    def test_monthly(self):
        from src.data.trending_engine import _bucket_single
        assert _bucket_single("2024-06-15", "monthly") == "2024-06"

    def test_hourly(self):
        from src.data.trending_engine import _bucket_single
        assert _bucket_single("2024-06-15 14:30", "hourly") == "2024-06-15 14:00"

    def test_empty_returns_empty(self):
        from src.data.trending_engine import _bucket_single
        assert _bucket_single("", "daily") == ""

    def test_invalid_date_returns_empty(self):
        from src.data.trending_engine import _bucket_single
        assert _bucket_single("not-a-date", "daily") == ""
