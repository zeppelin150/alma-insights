-- Migration 008: Entity normalization table
-- Extracts entities from nlp_ticket_classifications.entities_json
-- into an indexed, case-normalized lookup table.

CREATE TABLE IF NOT EXISTS ticket_entities_normalized (
    ticket_id    TEXT NOT NULL,
    entity_type  TEXT NOT NULL,   -- 'payer', 'product_area', 'feature'
    entity_value TEXT NOT NULL,   -- case-normalized
    scan_id      TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (ticket_id, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_tent_type_value
    ON ticket_entities_normalized(entity_type, entity_value);

CREATE INDEX IF NOT EXISTS idx_tent_ticket
    ON ticket_entities_normalized(ticket_id);
