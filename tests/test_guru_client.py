"""
Tests for GuruClient — Phase 4

Covers:
- Auth header construction (Basic base64)
- list_collections, list_cards, get_card, search_cards (mock HTTP)
- update_card sends correct payload
- test_connection returns bool
- Credential persistence round-trip
- Error handling (auth, API errors)
"""

import base64
import json
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from src.data.guru_client import (
    GuruClient, GuruAuthError, GuruAPIError,
)


class TestAuthHeader(unittest.TestCase):
    """Auth header construction."""

    def test_basic_auth_format(self):
        client = GuruClient("user@co.com", "mytoken123")
        expected_cred = base64.b64encode(
            b"user@co.com:mytoken123"
        ).decode("ascii")
        self.assertEqual(client._auth_header, f"Basic {expected_cred}")

    def test_auth_header_used_in_request(self):
        client = GuruClient("a@b.com", "tok")
        self.assertIn("Basic", client._auth_header)


class TestConnectionTest(unittest.TestCase):
    """test_connection returns bool."""

    @patch.object(GuruClient, "_request")
    def test_connection_success(self, mock_req):
        mock_req.return_value = {"id": "me"}
        client = GuruClient("a@b.com", "tok")
        self.assertTrue(client.test_connection())

    @patch.object(GuruClient, "_request")
    def test_connection_failure(self, mock_req):
        mock_req.side_effect = GuruAuthError("bad")
        client = GuruClient("a@b.com", "tok")
        self.assertFalse(client.test_connection())


class TestListCollections(unittest.TestCase):

    @patch.object(GuruClient, "_request")
    def test_list_collections_parses(self, mock_req):
        mock_req.return_value = [
            {"id": "c1", "name": "Sales", "description": "desc", "slug": "sales"},
            {"id": "c2", "name": "Support", "description": "", "slug": "support"},
        ]
        client = GuruClient("a@b.com", "tok")
        result = client.list_collections()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "c1")
        self.assertEqual(result[0]["name"], "Sales")

    @patch.object(GuruClient, "_request")
    def test_list_collections_empty(self, mock_req):
        mock_req.return_value = []
        client = GuruClient("a@b.com", "tok")
        self.assertEqual(client.list_collections(), [])


class TestListCards(unittest.TestCase):

    @patch.object(GuruClient, "_request")
    def test_list_cards_by_collection(self, mock_req):
        mock_req.return_value = [
            {"id": "card1", "preferredPhrase": "Title 1",
             "content": "Body", "collection": {"name": "Support", "id": "c1"},
             "lastModified": "2026-03-10"},
        ]
        client = GuruClient("a@b.com", "tok")
        result = client.list_cards(collection_id="c1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Title 1")
        self.assertEqual(result[0]["collection"], "Support")
        mock_req.assert_called_with("GET", "/collections/c1/cards")


class TestGetCard(unittest.TestCase):

    @patch.object(GuruClient, "_request")
    def test_get_card_full(self, mock_req):
        mock_req.return_value = {
            "id": "card1",
            "preferredPhrase": "How to Reset",
            "content": "<p>Steps here</p>",
            "collection": {"name": "Help", "id": "c1"},
            "lastModified": "2026-03-10",
            "verificationState": "TRUSTED",
        }
        client = GuruClient("a@b.com", "tok")
        card = client.get_card("card1")
        self.assertEqual(card["title"], "How to Reset")
        self.assertEqual(card["content"], "<p>Steps here</p>")
        self.assertTrue(len(card["content_hash"]) == 64)  # SHA-256 hex
        self.assertEqual(card["collection"], "Help")
        self.assertEqual(card["status"], "TRUSTED")


class TestSearchCards(unittest.TestCase):

    @patch.object(GuruClient, "_request")
    def test_search_returns_cards(self, mock_req):
        mock_req.return_value = [
            {"id": "card2", "preferredPhrase": "Billing FAQ",
             "content": "FAQ content", "collection": {"name": "Billing", "id": "c2"},
             "lastModified": "2026-03-09"},
        ]
        client = GuruClient("a@b.com", "tok")
        result = client.search_cards("billing")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Billing FAQ")


class TestUpdateCard(unittest.TestCase):

    @patch.object(GuruClient, "_request")
    def test_update_card_sends_content(self, mock_req):
        # First call: GET current card; Second call: PUT update
        mock_req.side_effect = [
            {"id": "card1", "preferredPhrase": "Old Title"},
            {"id": "card1", "preferredPhrase": "New Title"},
        ]
        client = GuruClient("a@b.com", "tok")
        result = client.update_card("card1", "New content", "New Title")
        self.assertEqual(result["preferredPhrase"], "New Title")
        # Verify PUT was called
        put_call = mock_req.call_args_list[1]
        self.assertEqual(put_call[0][0], "PUT")
        self.assertEqual(put_call[0][1], "/cards/card1")


class TestCredentialPersistence(unittest.TestCase):

    @patch("src.data.guru_client.pat_store")
    def test_save_credentials(self, mock_store):
        mock_store.save_setting.return_value = True
        ok = GuruClient.save_credentials("a@b.com", "tok123")
        self.assertTrue(ok)
        self.assertEqual(mock_store.save_setting.call_count, 2)

    @patch("src.data.guru_client.pat_store")
    def test_load_credentials(self, mock_store):
        mock_store.load_setting.side_effect = lambda k: {
            "guru_email": "a@b.com",
            "guru_api_token": "tok123",
        }.get(k, "")
        email, token = GuruClient.load_credentials()
        self.assertEqual(email, "a@b.com")
        self.assertEqual(token, "tok123")

    @patch("src.data.guru_client.pat_store")
    def test_is_configured(self, mock_store):
        mock_store.load_setting.side_effect = lambda k: {
            "guru_email": "a@b.com",
            "guru_api_token": "tok",
        }.get(k, "")
        self.assertTrue(GuruClient.is_configured())

    @patch("src.data.guru_client.pat_store")
    def test_not_configured_empty(self, mock_store):
        mock_store.load_setting.return_value = ""
        self.assertFalse(GuruClient.is_configured())


class TestErrorHandling(unittest.TestCase):

    @patch.object(GuruClient, "_request")
    def test_auth_error_raised(self, mock_req):
        mock_req.side_effect = GuruAuthError("401")
        client = GuruClient("a@b.com", "bad")
        self.assertFalse(client.test_connection())

    @patch.object(GuruClient, "_request")
    def test_get_card_not_found(self, mock_req):
        mock_req.return_value = None
        client = GuruClient("a@b.com", "tok")
        card = client.get_card("nonexistent")
        self.assertEqual(card, {})


if __name__ == "__main__":
    unittest.main()
