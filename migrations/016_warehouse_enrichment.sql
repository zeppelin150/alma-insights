-- Migration 016: Data Warehouse Enrichment Foundation
--
-- Phase 1 of the canonicalization-enrichment build. Adds enrichment columns to
-- ticket_index (insurance, agent, state, channel, session_date, dispute amount),
-- a many-to-many ticket_tags table with an incident registry, entity registries
-- for audit/canonical naming, a ground-truth concept table for regression
-- testing, and a backfill checkpoint table for the resumeable backfill script.
--
-- Note: ticket_index already has provider_id and client_id added later via
-- ingestion writers; migration 013 added them to the tickets table only. We add
-- them here so the warehouse can filter on them without joining.

-- ═══ ENRICHMENT COLUMNS ON ticket_index ═══════════════════════════════════════
ALTER TABLE ticket_index ADD COLUMN insurance_payer TEXT;
ALTER TABLE ticket_index ADD COLUMN client_id TEXT;
ALTER TABLE ticket_index ADD COLUMN provider_id TEXT;
ALTER TABLE ticket_index ADD COLUMN agent_id TEXT;
ALTER TABLE ticket_index ADD COLUMN service_state TEXT;       -- 2-char US state code
ALTER TABLE ticket_index ADD COLUMN channel TEXT DEFAULT 'email';
ALTER TABLE ticket_index ADD COLUMN session_date DATE;        -- nullable, date of clinical session if relevant
ALTER TABLE ticket_index ADD COLUMN dispute_amount_usd REAL;  -- nullable, extracted from body in later phases

CREATE INDEX IF NOT EXISTS idx_ti_insurance    ON ticket_index(insurance_payer);
CREATE INDEX IF NOT EXISTS idx_ti_client       ON ticket_index(client_id);
CREATE INDEX IF NOT EXISTS idx_ti_provider     ON ticket_index(provider_id);
CREATE INDEX IF NOT EXISTS idx_ti_agent        ON ticket_index(agent_id);
CREATE INDEX IF NOT EXISTS idx_ti_state        ON ticket_index(service_state);
CREATE INDEX IF NOT EXISTS idx_ti_channel      ON ticket_index(channel);
CREATE INDEX IF NOT EXISTS idx_ti_session_date ON ticket_index(session_date);

-- ═══ TAG SYSTEM (M:N) ════════════════════════════════════════════════════════
-- A ticket can have 0+ tags; a tag can apply to many tickets. Sources distinguish
-- ingestion-time tags from manual/inferred so audit can scope by provenance.
CREATE TABLE IF NOT EXISTS ticket_tags (
    ticket_id   TEXT NOT NULL,
    tag         TEXT NOT NULL,
    source      TEXT DEFAULT 'ingestion',           -- 'ingestion' | 'manual' | 'inferred'
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticket_id, tag),
    FOREIGN KEY (ticket_id) REFERENCES ticket_index(ticket_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tt_tag    ON ticket_tags(tag);
CREATE INDEX IF NOT EXISTS idx_tt_ticket ON ticket_tags(ticket_id);
CREATE INDEX IF NOT EXISTS idx_tt_source ON ticket_tags(source);

-- ═══ INCIDENT REGISTRY ═══════════════════════════════════════════════════════
-- Source of truth for what each operational tag means. expected_vernacular is a
-- JSON array of expected terms / phrases; expected_canonical_cluster is filled
-- in post-Phase 5 once cluster IDs exist (NULL until then).
CREATE TABLE IF NOT EXISTS incidents (
    incident_id                 TEXT PRIMARY KEY,
    description                 TEXT NOT NULL,
    incident_date               DATE,
    expected_vernacular         TEXT,                          -- JSON array
    expected_canonical_cluster  TEXT,                          -- back-filled post-Phase 5
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes                       TEXT
);

-- ═══ ENTITY REGISTRIES ═══════════════════════════════════════════════════════
-- One row per distinct entity. Populated lazily by ticket_index_writer on first
-- encounter; ticket_count is maintained by triggers / batched recompute (we use
-- batched recompute to avoid hot-path overhead).
CREATE TABLE IF NOT EXISTS insurance_payers (
    payer_id        TEXT PRIMARY KEY,                          -- canonical name, e.g. "Thunderbird Insurance"
    aliases_json    TEXT,                                      -- JSON array of aliases
    first_seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clients (
    client_id               TEXT PRIMARY KEY,                  -- c-xxxxxx (Alma internal UUID, non-PHI)
    first_seen_ticket_id    TEXT,
    first_seen_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_count            INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS providers (
    provider_id             TEXT PRIMARY KEY,                  -- p-xxxxxxx (Alma internal UUID, non-PHI)
    first_seen_ticket_id    TEXT,
    first_seen_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_count            INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id                TEXT PRIMARY KEY,                  -- a-xxxxx (Alma internal CS agent ID)
    first_seen_ticket_id    TEXT,
    first_seen_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ticket_count            INTEGER DEFAULT 0
);

-- ═══ TEST GROUND TRUTH (drift fix #1) ════════════════════════════════════════
-- Per-ticket concept labels loaded from alma_test_10000_1_manifest.json by
-- scripts/seed_incidents.py. Used by Phase 3/5/9 regression tests as the truth
-- side of precision/recall computation. Keeping this in the main DB (not a
-- separate test DB) lets golden-set comparison run against the same connection
-- the canonicalization engine uses, avoiding cross-DB attach overhead.
CREATE TABLE IF NOT EXISTS test_concept_ground_truth (
    ticket_id   TEXT NOT NULL,
    concept_id  TEXT NOT NULL,
    source      TEXT DEFAULT 'manifest',                       -- 'manifest' | 'golden_set' | 'hitl'
    loaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticket_id, concept_id, source)
);
CREATE INDEX IF NOT EXISTS idx_tcgt_concept ON test_concept_ground_truth(concept_id);
CREATE INDEX IF NOT EXISTS idx_tcgt_source  ON test_concept_ground_truth(source);

-- ═══ BACKFILL CHECKPOINT (drift fix #8) ══════════════════════════════════════
-- scripts/backfill_enrichment.py writes a row per processed import_run so the
-- script can resume from the last completed run on re-invocation. Single-row
-- table by convention (id=1); backfill_enrichment.py upserts it transactionally
-- after each import_run completes.
CREATE TABLE IF NOT EXISTS backfill_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    last_import_run_id      TEXT,
    rows_processed          INTEGER DEFAULT 0,
    runs_processed          INTEGER DEFAULT 0,
    started_at              TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status                  TEXT DEFAULT 'idle'                -- 'idle' | 'running' | 'completed' | 'failed'
);
