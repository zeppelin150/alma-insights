"""
Alma Insights — Shared Test Fixtures

Provides reusable fixtures for all test modules:
- empty_db: Fully initialized DatabaseManager with all tables, zero rows
- seeded_db: Extends empty_db with 100 deterministic tickets (3 TRCs × ~33)
- mock_settings: Temp settings.yaml with safe defaults
- mock_gemini_client / mock_claude_client: MagicMock LLM clients
"""

import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Markers ───────────────────────────────────────────────────────────
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring live services")
    config.addinivalue_line("markers", "ui: tests requiring QApplication")
    config.addinivalue_line("markers", "live_db: tests using production database")


# ── Database Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def empty_db(tmp_path):
    """Fully initialized DatabaseManager with all tables, zero rows."""
    db_path = tmp_path / "test_alma.db"
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager(db_path=db_path)
    db.initialize()
    yield db
    try:
        db.conn.close()
    except Exception:
        pass


@pytest.fixture
def seeded_db(empty_db):
    """DatabaseManager pre-loaded with 100 deterministic tickets across 3 TRCs.

    TRC distribution:
      - TRC-100 (Billing): 35 tickets
      - TRC-200 (Login):   33 tickets
      - TRC-300 (Claims):  32 tickets

    Date range: 30 days from today-30 to today-1
    CSAT scores: distributed 1.0-5.0
    Resolution times: populated for all tickets
    """
    db = empty_db
    today = datetime.now()

    trcs = [
        ("TRC-100", "Billing Issues", 35),
        ("TRC-200", "Login Problems", 33),
        ("TRC-300", "Claims Friction", 32),
    ]

    ticket_num = 1000
    for trc_code, trc_label, count in trcs:
        for i in range(count):
            tid = f"T-{ticket_num}"
            ticket_num += 1

            days_ago = (i % 30) + 1
            created = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            csat = round(1.0 + (i % 5), 1)  # 1.0, 2.0, 3.0, 4.0, 5.0
            status = "solved" if i % 3 != 0 else "open"
            assign_hours = round(2.0 + (i % 10) * 0.5, 1)
            total_hours = round(assign_hours + 1.0 + (i % 5), 1)
            first_reply = round(0.5 + (i % 8) * 0.3, 1)

            db.upsert_ticket({
                "ticket_id": tid,
                "subject": f"Test ticket {tid} about {trc_label.lower()}",
                "trc_code": trc_code,
                "trc_label": trc_label,
                "status": status,
                "priority": "normal",
                "channel": "email",
                "csat_score": csat,
                "created_at": created,
                "updated_at": "",
                "solved_at": created if status == "solved" else "",
                "requester_name": f"User {i}",
                "requester_email": "",
                "assignee_name": f"Agent {i % 5}",
                "group_name": "Support",
                "tags": [],
                "custom_fields": {},
                "assignment_to_resolution_hours": assign_hours,
                "total_resolution_hours": total_hours,
                "first_reply_hours": first_reply,
            })

            # Build a simple conversation thread
            thread = (
                f"[{created} 09:00] CUSTOMER:\n"
                f"I'm having issues with {trc_label.lower()}. "
                f"Ticket {tid}.\n\n---\n\n"
                f"[{created} 10:00] AGENT:\n"
                f"Thanks for reaching out. Let me look into this."
            )
            preview = thread[:200]

            db.upsert_conversation({
                "ticket_id": tid,
                "subject": f"Test ticket {tid} about {trc_label.lower()}",
                "trc_code": trc_code,
                "trc_label": trc_label,
                "status": status,
                "csat_score": csat,
                "created_at": created,
                "solved_at": created if status == "solved" else "",
                "message_count": 2,
                "client_messages": 1,
                "agent_messages": 1,
                "full_thread": thread,
                "thread_preview": preview,
                "source": "csv",
            })

            # Add comments
            db.upsert_comment({
                "comment_id": f"{tid}-0",
                "ticket_id": tid,
                "author_name": f"User {i}",
                "author_role": "customer",
                "body": f"I'm having issues with {trc_label.lower()}. Ticket {tid}.",
                "is_public": True,
                "created_at": f"{created} 09:00",
            })
            db.upsert_comment({
                "comment_id": f"{tid}-1",
                "ticket_id": tid,
                "author_name": f"Agent {i % 5}",
                "author_role": "agent",
                "body": "Thanks for reaching out. Let me look into this.",
                "is_public": True,
                "created_at": f"{created} 10:00",
            })

    # Seed daily counts for incident detection
    for trc_code, _, count in trcs:
        for d in range(30):
            day = (today - timedelta(days=d + 1)).strftime("%Y-%m-%d")
            daily_count = count // 30 + (1 if d % 3 == 0 else 0)
            try:
                db.conn.execute(
                    "INSERT INTO daily_counts (trc_code, date, ticket_count) "
                    "VALUES (?, ?, ?)",
                    (trc_code, day, daily_count),
                )
            except Exception:
                pass

    db.commit()

    # Build FTS index
    try:
        db.rebuild_fts_index()
    except Exception:
        pass

    yield db


# ── Raw Connection Fixture ────────────────────────────────────────────

@pytest.fixture
def seeded_conn(seeded_db):
    """Raw SQLite connection from seeded_db (for functions that take conn)."""
    yield seeded_db.conn


# ── Settings Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def mock_settings(tmp_path):
    """Temporary settings.yaml with safe defaults."""
    import yaml

    settings = {
        "gemini": {
            "model": "gemini-2.5-flash",
            "cli_path": "/usr/bin/gemini",
            "pii_redaction": True,
            "temperature": 0.2,
        },
        "ai": {
            "task_routing": {
                "override_all": None,
                "routes": {
                    "nlp_classification": "gemini",
                    "voc_analysis": "gemini",
                    "report_generation": "gemini",
                    "guru_analysis": "claude",
                    "guru_content_generation": "claude",
                    "watchlist_triage": "claude",
                    "meta_analytics": "claude",
                    "ab_comparison": "gemini",
                },
            },
        },
        "nlp_scan": {
            "batch_size": 25,
            "budget_cap": 50.0,
            "parallel_workers": 3,
            "mode": "agentic",
        },
        "display": {
            "default_date_range_days": 90,
            "layman_mode": True,
        },
    }

    settings_path = tmp_path / "settings.yaml"
    with open(settings_path, "w") as f:
        yaml.dump(settings, f)

    with patch("src.data.settings_manager.get_settings_path", return_value=settings_path):
        yield settings


# ── LLM Client Mocks ─────────────────────────────────────────────────

@pytest.fixture
def mock_gemini_client():
    """MagicMock Gemini client with standard response patterns."""
    client = MagicMock()
    client.model = "gemini-2.5-flash"
    client.is_alive.return_value = True
    client.generate.return_value = '{"summary": "Test summary", "findings": []}'
    return client


@pytest.fixture
def mock_claude_client():
    """MagicMock Claude client with standard response patterns."""
    client = MagicMock()
    client.model_id = "claude-sonnet-4-6"
    client.generate.return_value = "Test analysis result"
    client.generate_streaming.return_value = iter(["Test ", "streaming ", "result"])
    return client


@pytest.fixture
def mock_client_factory(mock_gemini_client, mock_claude_client):
    """Patches build_client_for_task to return mock clients by provider."""
    def _factory(task_type, use_bridge=False):
        claude_tasks = {"guru_analysis", "guru_content_generation",
                       "watchlist_triage", "meta_analytics"}
        if task_type in claude_tasks:
            return mock_claude_client
        return mock_gemini_client

    with patch("src.gemini.client_factory.build_client_for_task", side_effect=_factory):
        yield _factory


# ── Guru Mocks ────────────────────────────────────────────────────────

@pytest.fixture
def mock_guru_client():
    """MagicMock Guru client with canned collection/card data."""
    client = MagicMock()
    client.list_collections.return_value = [
        {"id": "coll-1", "name": "Billing", "slug": "billing"},
        {"id": "coll-2", "name": "Claims", "slug": "claims"},
    ]
    client.list_cards.return_value = [
        {"id": "card-1", "preferredPhrase": "How to fix billing",
         "collection": {"id": "coll-1"}, "content": "Steps to fix billing..."},
        {"id": "card-2", "preferredPhrase": "Claims process guide",
         "collection": {"id": "coll-2"}, "content": "Guide for claims..."},
    ]
    client.get_card.return_value = {
        "id": "card-1", "preferredPhrase": "How to fix billing",
        "content": "Steps to fix billing issues.",
    }
    client.search_cards.return_value = client.list_cards.return_value
    client.update_card.return_value = {"id": "card-1", "preferredPhrase": "Updated"}
    client.create_card.return_value = {"id": "card-new", "preferredPhrase": "New card"}
    return client
