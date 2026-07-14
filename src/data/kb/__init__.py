"""Self-building Drive knowledge base (WS2, renn-calendar-kb-studio plan).

Modules:
- drive_kb   — the SINGLE Drive write chokepoint (allowlist-enforced)
- card_format — frontmatter v1 serializer / tolerant parser
- store      — local mirror (kb_cards + FTS5) API
- sync       — pull/push engine (Drive is source of truth for card content)
- worker     — main-process KBWorker (queue drain + sync ticks)
"""
