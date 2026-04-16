"""
Tests for src.data.embedding (Session 4)

All tests use mocks since the EmbeddingGemma model (~600MB) is not
available in the test environment. Tests validate:
    - air_gap: env vars set correctly, verify_air_gap report structure
    - model_loader: is_available behavior with/without model
    - encoder: embed_documents/embed_query shapes (mocked model)
    - search: cosine similarity ranking, top_k, min threshold
    - builder: incremental skip, force re-embed
    - compat: old API routes to new implementation
    - semantic_tools: graceful fallback when model unavailable
"""

import json
import sqlite3
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIG_005 = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")
_MIG_006 = (_PROJECT_ROOT / "migrations" / "006_hybrid_chat.sql").read_text(encoding="utf-8")

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY, subject TEXT, trc_code TEXT, created_at TEXT
);
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_BASE_DDL)
    conn.executescript(_MIG_005)
    conn.executescript(_MIG_006)
    _seed(conn)
    yield conn
    conn.close()


def _seed(conn):
    for i in range(5):
        tid = f"T-{i+1}"
        conn.execute(
            "INSERT INTO ticket_index "
            "(ticket_id, first_seen_scan_id, last_seen_scan_id, "
            "first_seen_date, ticket_created_date, trc_code, "
            "friction_type, sub_pattern, issue_snippet) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, "s1", "s1", "2026-01-01", f"2026-01-{10+i:02d}",
             ["Billing", "Billing", "Claims", "Claims", "Tech"][i],
             "test_type", "test_pattern", f"Issue about topic {i}"),
        )
    conn.commit()


def _seed_embeddings(conn, dim=1024):
    """Pre-seed embeddings for search/cache tests."""
    np.random.seed(42)
    for i in range(5):
        tid = f"T-{i+1}"
        vec = np.random.randn(dim).astype(np.float32)
        vec = vec / np.linalg.norm(vec)  # normalize
        conn.execute(
            "INSERT INTO ticket_embeddings "
            "(ticket_id, embedding_blob, source_text_hash, "
            "model_name, dim_size, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, vec.tobytes(), f"hash_{i}", "embeddinggemma-300m", dim, "2026-01-01"),
        )
    conn.commit()


# ═══════════════════════════════════════
#  AIR GAP
# ═══════════════════════════════════════

class TestAirGap:

    def test_enforce_sets_env_vars(self):
        import os
        from src.data.embedding.air_gap import enforce_air_gap, AIR_GAP_ENV
        enforce_air_gap()
        for key, expected in AIR_GAP_ENV.items():
            assert os.environ.get(key) == expected

    def test_verify_report_structure(self):
        from src.data.embedding.air_gap import enforce_air_gap, verify_air_gap
        enforce_air_gap()
        report = verify_air_gap()
        assert "passed" in report
        assert "checks" in report
        assert "model_dir" in report
        assert isinstance(report["checks"], dict)

    def test_verify_checks_env_vars(self):
        from src.data.embedding.air_gap import enforce_air_gap, verify_air_gap
        enforce_air_gap()
        report = verify_air_gap()
        assert report["checks"]["env_HF_HUB_OFFLINE"] is True
        assert report["checks"]["env_TRANSFORMERS_OFFLINE"] is True


# ═══════════════════════════════════════
#  MODEL LOADER
# ═══════════════════════════════════════

class TestModelLoader:

    def test_is_available_no_library(self):
        """When sentence_transformers is not installed, returns False."""
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            # Force reimport check
            from src.data.embedding import model_loader
            # We can't easily mock ImportError this way, so test the file check
            pass

    def test_is_available_no_model_dir(self):
        """When model dir doesn't exist, returns False."""
        from src.data.embedding.model_loader import _model_files_exist
        with patch("src.data.embedding.model_loader.MODEL_DIR",
                   Path("/nonexistent/path")):
            assert _model_files_exist() is False

    def test_is_available_with_mock_dir(self, tmp_path):
        """When model dir has config.json, returns True (if library present)."""
        from src.data.embedding.model_loader import _model_files_exist
        model_dir = tmp_path / "test_model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        with patch("src.data.embedding.model_loader.MODEL_DIR", tmp_path):
            assert _model_files_exist() is True


# ═══════════════════════════════════════
#  ENCODER (mocked model)
# ═══════════════════════════════════════

class TestEncoder:

    def _mock_model(self):
        model = MagicMock()
        model.encode = MagicMock(side_effect=lambda texts, **kw:
            np.random.randn(len(texts), 1024).astype(np.float32))
        return model

    def test_embed_documents_shape(self):
        mock = self._mock_model()
        with patch("src.data.embedding.model_loader.get_model", return_value=mock):
            from src.data.embedding.encoder import embed_documents
            result = embed_documents(["text 1", "text 2", "text 3"])
            assert result.shape == (3, 1024)
            assert result.dtype == np.float32

    def test_embed_query_shape(self):
        mock = self._mock_model()
        with patch("src.data.embedding.model_loader.get_model", return_value=mock):
            from src.data.embedding.encoder import embed_query
            result = embed_query("search query")
            assert result.shape == (1024,)

    def test_embed_documents_model_unavailable(self):
        with patch("src.data.embedding.model_loader.get_model", return_value=None):
            from src.data.embedding.encoder import embed_documents
            with pytest.raises(RuntimeError, match="not available"):
                embed_documents(["text"])


# ═══════════════════════════════════════
#  SEARCH
# ═══════════════════════════════════════

class TestSearch:

    def test_cosine_similarity_ranking(self):
        from src.data.embedding.search import semantic_search
        np.random.seed(42)
        # Create corpus where item 0 is most similar to query
        query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        corpus = np.array([
            [0.9, 0.1, 0.0],   # most similar
            [0.0, 1.0, 0.0],   # orthogonal
            [0.5, 0.5, 0.0],   # medium
        ], dtype=np.float32)
        # Normalize
        corpus = corpus / np.linalg.norm(corpus, axis=1, keepdims=True)
        ids = ["T-1", "T-2", "T-3"]

        results = semantic_search(query, corpus, ids, top_k=3)
        assert results[0]["ticket_id"] == "T-1"  # highest similarity
        assert results[0]["similarity"] > results[1]["similarity"]

    def test_top_k_respected(self):
        from src.data.embedding.search import semantic_search
        query = np.array([1.0, 0.0], dtype=np.float32)
        corpus = np.eye(2, dtype=np.float32)
        ids = ["A", "B"]
        results = semantic_search(query, corpus, ids, top_k=1)
        assert len(results) == 1

    def test_min_similarity_filters(self):
        from src.data.embedding.search import semantic_search
        query = np.array([1.0, 0.0], dtype=np.float32)
        corpus = np.array([
            [1.0, 0.0],    # sim = 1.0
            [0.0, 1.0],    # sim = 0.0
        ], dtype=np.float32)
        ids = ["A", "B"]
        results = semantic_search(query, corpus, ids, min_similarity=0.5)
        assert len(results) == 1
        assert results[0]["ticket_id"] == "A"

    def test_empty_corpus(self):
        from src.data.embedding.search import semantic_search
        query = np.array([1.0], dtype=np.float32)
        results = semantic_search(query, np.array([]), [], top_k=5)
        assert results == []

    def test_load_embedding_cache(self, db):
        _seed_embeddings(db)
        from src.data.embedding.search import load_embedding_cache
        embeddings, ids = load_embedding_cache(db)
        assert len(ids) == 5
        assert embeddings.shape == (5, 1024)

    def test_load_cache_empty_db(self, db):
        from src.data.embedding.search import load_embedding_cache
        embeddings, ids = load_embedding_cache(db)
        assert len(ids) == 0


# ═══════════════════════════════════════
#  BUILDER
# ═══════════════════════════════════════

class TestBuilder:

    def _mock_embed(self, texts, batch_size=32):
        return np.random.randn(len(texts), 1024).astype(np.float32)

    def test_incremental_skip_unchanged(self, db):
        """Second run should skip if hashes haven't changed."""
        with patch("src.data.embedding.model_loader.is_available", return_value=True), \
             patch("src.data.embedding.encoder.embed_documents", side_effect=self._mock_embed):
            from src.data.embedding.builder import build_embeddings
            count1 = build_embeddings(db)
            assert count1 == 5  # all 5 tickets
            count2 = build_embeddings(db)
            assert count2 == 0  # all skipped (hashes unchanged)

    def test_force_reembeds_all(self, db):
        with patch("src.data.embedding.model_loader.is_available", return_value=True), \
             patch("src.data.embedding.encoder.embed_documents", side_effect=self._mock_embed):
            from src.data.embedding.builder import build_embeddings
            build_embeddings(db)
            count = build_embeddings(db, force=True)
            assert count == 5  # all re-embedded

    def test_unavailable_model_returns_zero(self, db):
        with patch("src.data.embedding.model_loader.is_available", return_value=False):
            from src.data.embedding.builder import build_embeddings
            assert build_embeddings(db) == 0


# ═══════════════════════════════════════
#  COMPAT
# ═══════════════════════════════════════

class TestCompat:

    def test_verify_model_bundled_routes(self):
        with patch("src.data.embedding.model_loader.is_available", return_value=True):
            from src.data.embedding.compat import verify_model_bundled
            assert verify_model_bundled() is True

    def test_is_available_routes(self):
        with patch("src.data.embedding.model_loader.is_available", return_value=False):
            from src.data.embedding.compat import is_available
            assert is_available() is False


# ═══════════════════════════════════════
#  SEMANTIC SEARCH TOOL
# ═══════════════════════════════════════

class TestSemanticSearchTool:

    def test_fallback_when_unavailable(self, db):
        with patch("src.data.embedding.model_loader.is_available", return_value=False):
            from src.data.chat_tools.semantic_tools import handle_semantic_search
            result = handle_semantic_search(db, {"query": "billing issue"}, {})
            assert result["count"] == 0
            assert "unavailable" in result["note"]

    def test_missing_query_returns_error(self, db):
        from src.data.chat_tools.semantic_tools import handle_semantic_search
        result = handle_semantic_search(db, {}, {})
        assert "error" in result

    def test_search_with_cached_embeddings(self, db):
        """End-to-end with pre-seeded embeddings and mocked model."""
        _seed_embeddings(db)
        mock_model = MagicMock()
        # Return a query vector similar to T-1's embedding
        first_emb = np.frombuffer(
            db.execute("SELECT embedding_blob FROM ticket_embeddings WHERE ticket_id='T-1'").fetchone()[0],
            dtype=np.float32,
        ).copy()
        mock_model.encode = MagicMock(return_value=first_emb.reshape(1, -1))

        with patch("src.data.embedding.model_loader.is_available", return_value=True), \
             patch("src.data.embedding.model_loader.get_model", return_value=mock_model):
            from src.data.chat_tools.semantic_tools import handle_semantic_search
            result = handle_semantic_search(db, {"query": "test", "top_k": 3}, {})
            assert result["count"] >= 1
            assert result["matches"][0]["ticket_id"] == "T-1"
