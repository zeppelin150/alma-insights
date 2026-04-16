-- ═══════════════════════════════════════════════════════════════
--  Migration 005 — Persistence Layer (Build 11.0)
--  All tables here are PERSISTENT (survive Clear & Close).
--  PHI-free: no raw ticket text, no customer PII.
-- ═══════════════════════════════════════════════════════════════

-- ═══ TICKET INDEX — core persistent ticket-level record ═══
CREATE TABLE IF NOT EXISTS ticket_index (
    ticket_id               TEXT PRIMARY KEY,
    first_seen_scan_id      TEXT NOT NULL,
    last_seen_scan_id       TEXT NOT NULL,
    first_seen_date         DATETIME NOT NULL,
    ticket_created_date     DATE,
    trc_code                TEXT,
    trc_label               TEXT,
    subject_sanitized       TEXT,
    issue_snippet           TEXT,
    friction_type           TEXT,
    sub_pattern             TEXT,
    sub_pattern_id          INTEGER,
    sentiment_polarity      TEXT,
    sentiment_intensity     REAL,
    anomaly_flag            TEXT,
    anomaly_reason          TEXT,
    root_cause_hint         TEXT,
    csat_score              REAL,
    message_count           INTEGER,
    resolution_hours        REAL,
    classification_confidence REAL,
    classification_method   TEXT,
    entities_json           TEXT,
    key_phrases             TEXT,
    is_novel                BOOLEAN DEFAULT 0,
    dataset_id              TEXT
);
CREATE INDEX IF NOT EXISTS idx_ti_trc ON ticket_index(trc_code);
CREATE INDEX IF NOT EXISTS idx_ti_friction ON ticket_index(friction_type);
CREATE INDEX IF NOT EXISTS idx_ti_sub_pattern ON ticket_index(sub_pattern);
CREATE INDEX IF NOT EXISTS idx_ti_anomaly ON ticket_index(anomaly_flag);
CREATE INDEX IF NOT EXISTS idx_ti_created ON ticket_index(ticket_created_date);
CREATE INDEX IF NOT EXISTS idx_ti_sentiment ON ticket_index(sentiment_polarity);
CREATE INDEX IF NOT EXISTS idx_ti_last_scan ON ticket_index(last_seen_scan_id);

-- ═══ SCAN CATEGORY SNAPSHOTS — post-scan distributions ═══
CREATE TABLE IF NOT EXISTS scan_category_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id             TEXT NOT NULL,
    scan_date           DATETIME NOT NULL,
    trc                 TEXT NOT NULL,
    friction_type       TEXT,
    sub_pattern         TEXT,
    ticket_count        INTEGER DEFAULT 0,
    avg_sentiment       REAL,
    avg_csat            REAL,
    anomaly_count       INTEGER DEFAULT 0,
    sample_ticket_ids   TEXT
);
CREATE INDEX IF NOT EXISTS idx_scs_scan_date ON scan_category_snapshots(scan_date);
CREATE INDEX IF NOT EXISTS idx_scs_friction ON scan_category_snapshots(friction_type);

-- ═══ ANALYSIS RUNS — every AI report generation ═══
CREATE TABLE IF NOT EXISTS analysis_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT UNIQUE NOT NULL,
    run_date            DATETIME NOT NULL,
    prompt_template     TEXT,
    prompt_text         TEXT,
    trc_filter          TEXT,
    date_start          DATE,
    date_end            DATE,
    ticket_count        INTEGER,
    model_used          TEXT,
    output_text         TEXT,
    output_structured   TEXT,
    token_count         INTEGER,
    cost_usd            REAL,
    duration_sec        REAL,
    definition_id       TEXT,
    source              TEXT DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_ar_date ON analysis_runs(run_date);
CREATE INDEX IF NOT EXISTS idx_ar_template ON analysis_runs(prompt_template);

-- ═══ TREND SNAPSHOTS — statistical engine outputs ═══
CREATE TABLE IF NOT EXISTS trend_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date       DATETIME NOT NULL,
    engine              TEXT NOT NULL,
    metric_key          TEXT NOT NULL,
    metric_value        REAL,
    direction           TEXT,
    severity            TEXT,
    pct_change          REAL,
    baseline_value      REAL,
    context             TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts_date ON trend_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ts_engine ON trend_snapshots(engine);

-- ═══ INSIGHT LEDGER — significant findings log ═══
CREATE TABLE IF NOT EXISTS insight_ledger (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id              TEXT UNIQUE NOT NULL,
    date_identified         DATETIME NOT NULL,
    source_run_id           TEXT,
    insight_type            TEXT,
    title                   TEXT,
    description             TEXT,
    severity                TEXT,
    supporting_ticket_ids   TEXT,
    supporting_data         TEXT,
    status                  TEXT DEFAULT 'new',
    resolved_date           DATETIME,
    notes                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_il_status ON insight_ledger(status);
CREATE INDEX IF NOT EXISTS idx_il_severity ON insight_ledger(severity);

-- ═══ CHAT SESSIONS — conversation persistence ═══
CREATE TABLE IF NOT EXISTS chat_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT UNIQUE NOT NULL,
    created_at          DATETIME NOT NULL,
    updated_at          DATETIME NOT NULL,
    source_page         TEXT,
    source_context      TEXT,
    trc_filter          TEXT,
    date_start          DATE,
    date_end            DATE,
    messages            TEXT,
    title               TEXT
);
CREATE INDEX IF NOT EXISTS idx_cs_updated ON chat_sessions(updated_at);

-- ═══ REPORT DEFINITIONS — Smart Report pipeline configs ═══
CREATE TABLE IF NOT EXISTS report_definitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    definition_id       TEXT UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT,
    created_by          TEXT,
    created_at          DATETIME NOT NULL,
    updated_at          DATETIME NOT NULL,
    config              TEXT NOT NULL,
    schedule            TEXT,
    last_run_id         TEXT,
    last_run_date       DATETIME,
    is_active           BOOLEAN DEFAULT 1
);
