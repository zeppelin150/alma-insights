"""Unit tests for the new AsanaClient attachment methods."""

from __future__ import annotations

from src.data.asana_client import AsanaClient


def test_list_attachments_parses(monkeypatch):
    client = AsanaClient("tok")

    # list_attachments pages via _paginate → _get_raw (full payload with
    # next_page) since ff430fe; patching _get here used to leak a LIVE call.
    def fake_get(path, params=None):
        assert path == "/tasks/T1/attachments"
        return {"data": [
            {"gid": "a1", "name": "policy.md", "resource_subtype": "asana"},
            {"gid": "a2", "name": "old.md", "resource_subtype": "asana"}]}

    monkeypatch.setattr(client, "_get_raw", fake_get)
    out = client.list_attachments("T1")
    assert out == [{"gid": "a1", "name": "policy.md", "subtype": "asana"},
                   {"gid": "a2", "name": "old.md", "subtype": "asana"}]


def test_get_attachment_parses(monkeypatch):
    client = AsanaClient("tok")

    def fake_get(path, params=None):
        assert path == "/attachments/a1"
        return {"gid": "a1", "name": "policy.md", "download_url": "https://s3/x",
                "host": "asana", "resource_subtype": "asana"}

    monkeypatch.setattr(client, "_get", fake_get)
    att = client.get_attachment("a1")
    assert att["download_url"] == "https://s3/x"
    assert att["host"] == "asana"
    assert att["name"] == "policy.md"


def test_get_attachment_falls_back_to_permanent_url(monkeypatch):
    client = AsanaClient("tok")
    monkeypatch.setattr(client, "_get", lambda p, params=None: {
        "gid": "a3", "name": "doc", "host": "google_drive",
        "permanent_url": "https://drive.google.com/d/FILEID123/view",
    })
    att = client.get_attachment("a3")
    assert att["view_url"] == "https://drive.google.com/d/FILEID123/view"
    assert att["host"] == "google_drive"
