-- Migration 015: Batch-to-ticket tracking table
-- Enables: direct sweep queries, batch-level coverage metrics, audit trail.
-- Populated at dispatch time in _process_single_batch().

CREATE TABLE IF NOT EXISTS nlp_batch_tickets (
    batch_id    TEXT NOT NULL,
    ticket_id   TEXT NOT NULL,
    scan_id     TEXT NOT NULL,
    PRIMARY KEY (batch_id, ticket_id),
    FOREIGN KEY (batch_id) REFERENCES nlp_batches(batch_id),
    FOREIGN KEY (scan_id) REFERENCES nlp_scan_runs(scan_id)
);

CREATE INDEX IF NOT EXISTS idx_nbt_scan ON nlp_batch_tickets(scan_id);
CREATE INDEX IF NOT EXISTS idx_nbt_ticket ON nlp_batch_tickets(ticket_id);
