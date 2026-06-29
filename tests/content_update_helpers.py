"""Shared fakes for the content_update pipeline tests (not collected by pytest)."""

from __future__ import annotations

import json


class StubLLM:
    """Routes by prompt content: identify -> JSON plan, write -> TITLE/---/body."""

    def __init__(self, plan=None, title="New Title", body="New body line one\nNew body line two"):
        self.plan = plan if plan is not None else {
            "summary": "update intro",
            "changes": [{"type": "update", "section": "Intro",
                         "reason": "new policy", "evidence": "X"}],
        }
        self.title = title
        self.body = body
        self.calls: list[str] = []

    def generate(self, prompt, system_prompt="", timeout=120):
        self.calls.append(prompt)
        if "JSON object" in prompt:
            return "```json\n" + json.dumps(self.plan) + "\n```"
        if "COMPLETE revised card" in prompt:
            return f"TITLE: {self.title}\n---\n{self.body}"
        return ""


class SequencedLLM:
    """Returns queued responses in order (last one repeats)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.i = 0
        self.calls: list[str] = []

    def generate(self, prompt, system_prompt="", timeout=120):
        self.calls.append(prompt)
        r = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return r


class FakeGuru:
    """Mimics GuruClient's NORMALIZED shape (id/title/content)."""

    def __init__(self, cards=None):
        self.cards = cards or []
        self.updated: list[dict] = []
        self.created: list[dict] = []

    def get_card(self, card_id):
        for c in self.cards:
            if c["id"] == card_id:
                return {"id": c["id"], "title": c.get("title", ""),
                        "content": c.get("content", ""),
                        "collection": c.get("collection", ""),
                        "collection_id": c.get("collection_id", "")}
        return {}

    def search_cards(self, query):
        return [dict(c) for c in self.cards]

    def update_card(self, card_id, content, title=None):
        self.updated.append({"card_id": card_id, "content": content, "title": title})
        return {"id": card_id, "title": title}

    def create_card(self, collection_id, title, content, folder_ids=None):
        self.created.append({"id": "new-card", "title": title})
        return {"id": "new-card"}

    def list_collections(self):
        return [{"id": "col-1", "name": "Billing"}]


class FakeAsanaAttachments:
    """Just the attachment surface used by load_source."""

    def __init__(self, attachments=None, detail=None):
        self._attachments = attachments or []
        self._detail = detail or {}

    def list_attachments(self, task_gid, limit=100):
        return list(self._attachments)

    def get_attachment(self, attachment_gid):
        return dict(self._detail.get(attachment_gid, {}))


class FakeDrive:
    """Supports both the attachment path (export_text) and import_drive_doc (get_file)."""

    def __init__(self, files=None, texts=None):
        self._files = files or {}   # file_id -> {name, mime_type, url, modified_time}
        self._texts = texts or {}   # file_id -> text

    def get_file(self, file_id):
        meta = self._files.get(file_id)
        if not meta:
            return {}
        return {"id": file_id, "name": meta.get("name", ""),
                "mime_type": meta.get("mime_type", ""), "url": meta.get("url", ""),
                "modified_time": meta.get("modified_time", "")}

    def export_text(self, file_id, mime_type):
        return self._texts.get(file_id, "")
