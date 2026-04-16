-- Migration 013: Add provider_id, client_id columns and source_id to ticket_index
-- Stage 2 proactive: prevents Stage 3 migration

-- Add provider_id and client_id to shared tickets table (nullable for existing data)
ALTER TABLE tickets ADD COLUMN provider_id TEXT;
ALTER TABLE tickets ADD COLUMN client_id TEXT;

-- Add source_id to ticket_index (proactive for Stage 3 source-scoped scans)
ALTER TABLE ticket_index ADD COLUMN source_id TEXT DEFAULT 'zendesk_default';
