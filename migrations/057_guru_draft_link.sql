-- ─────────────────────────────────────────────────────────────────────
-- Migration 057 — link a CCC draft to the Guru draft it was pushed to.
--
-- WS-B (pilot feedback 2026-08-07, owner-approved 2026-08-09): "push the
-- Renn updates into a Guru draft to view in Guru before publishing." The
-- push composes the same publish_body bytes as a publish, creates a draft
-- via Guru's drafts API (POST /api/v1/drafts — official-but-undocumented,
-- proven via Guru's own CLI), aims it at the target collection via
-- /drafts/{id}/context, and records the Guru draft id here.
--
-- guru_draft_id          the Guru draft's id; NULL = never pushed to a draft.
-- guru_draft_created_at  when the push happened (ISO-8601 UTC).
--
-- The CCC row's status STAYS 'pending' while a Guru draft exists — the
-- specialist publishes natively in Guru, then closes out with the
-- "Published in Guru" action (status → 'pushed', a local mark; there is no
-- API detection of native publishes in v1).
--
-- Idempotent: single-line ALTER ADD COLUMNs (the schema migrator's PRAGMA
-- table_info guard skips them when present).
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE guru_content_drafts ADD COLUMN guru_draft_id TEXT;
ALTER TABLE guru_content_drafts ADD COLUMN guru_draft_created_at TEXT;
