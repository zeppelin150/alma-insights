/**
 * Alma Insights — Scan Server Database Layer
 * SQLite connection (WAL mode) + query helpers for NLP scan tables.
 *
 * HIPAA H15 — WRITE SCOPE ENFORCEMENT:
 *   Node writes ONLY: nlp_scan_runs, nlp_batches, nlp_ticket_classifications
 *   Python writes: sub_patterns, sub_pattern_ngrams, sub_pattern_snapshots,
 *                  provisional_classifications, nlp_findings
 *   Both read everything freely.
 */

'use strict';

const Database = require('better-sqlite3');
const { v4: uuidv4 } = require('uuid');
const { log } = require('./utils');

// H15: Tables the Node server is allowed to write to
const WRITABLE_TABLES = new Set([
    'nlp_scan_runs', 'nlp_batches', 'nlp_ticket_classifications',
]);

let _db = null;

/**
 * Open (or return existing) SQLite connection in WAL mode.
 */
function getDb(dbPath) {
    if (_db) return _db;
    _db = new Database(dbPath);
    _db.pragma('journal_mode = WAL');
    _db.pragma('foreign_keys = ON');
    log('info', 'SQLite opened', { path: dbPath, mode: 'WAL' });
    return _db;
}

/**
 * Close the database connection.
 */
function closeDb() {
    if (_db) {
        _db.close();
        _db = null;
    }
}

// ─── TRC Ticket Counts (for batch planning) ───

function getTrcCounts(db, dateStart, dateEnd) {
    return db.prepare(`
        SELECT trc_code AS trc, COUNT(DISTINCT ticket_id) AS n
        FROM conversations
        WHERE created_at >= ? AND created_at <= ?
          AND trc_code IS NOT NULL AND trc_code != ''
        GROUP BY trc_code
        ORDER BY n DESC
    `).all(dateStart, dateEnd);
}

function getUntaggedCount(db, dateStart, dateEnd) {
    const row = db.prepare(`
        SELECT COUNT(DISTINCT ticket_id) AS n
        FROM conversations
        WHERE created_at >= ? AND created_at <= ?
          AND (trc_code IS NULL OR trc_code = '')
    `).get(dateStart, dateEnd);
    return row ? row.n : 0;
}

// ─── Ticket Data (for payload building) ───

function getTicketsForTrc(db, trc, dateStart, dateEnd, limit) {
    let query = `
        SELECT ticket_id, subject, trc_code, trc_label,
               csat_score, created_at, full_thread,
               message_count, client_messages, agent_messages
        FROM conversations
        WHERE trc_code = ? AND created_at >= ? AND created_at <= ?
        ORDER BY created_at ASC
    `;
    const params = [trc, dateStart, dateEnd];
    if (limit) {
        query += ' LIMIT ?';
        params.push(limit);
    }
    return db.prepare(query).all(...params);
}

function getUntaggedTickets(db, dateStart, dateEnd, limit) {
    let query = `
        SELECT ticket_id, subject, trc_code, trc_label,
               csat_score, created_at, full_thread,
               message_count, client_messages, agent_messages
        FROM conversations
        WHERE (trc_code IS NULL OR trc_code = '')
          AND created_at >= ? AND created_at <= ?
        ORDER BY created_at ASC
    `;
    const params = [dateStart, dateEnd];
    if (limit) {
        query += ' LIMIT ?';
        params.push(limit);
    }
    return db.prepare(query).all(...params);
}

// ─── Scan Run Management ───

function createScanRun(db, opts) {
    const scanId = uuidv4();
    const now = new Date().toISOString();
    db.prepare(`
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             trc_filter, mode, batch_strategy, total_batches, total_tickets,
             total_comments, budget_cap_usd, config_snapshot)
        VALUES (?, ?, 'queued', ?, ?, ?, ?, 'trc', ?, ?, 0, ?, ?)
    `).run(
        scanId, now,
        opts.dateStart, opts.dateEnd,
        opts.trcFilter ? JSON.stringify(opts.trcFilter) : null,
        opts.mode || 'full',
        opts.totalBatches || 0,
        opts.totalTickets || 0,
        opts.budgetCap || 50.0,
        JSON.stringify(opts.configSnapshot || {}),
    );
    return { scanId, createdAt: now };
}

function getScan(db, scanId) {
    return db.prepare('SELECT * FROM nlp_scan_runs WHERE scan_id = ?').get(scanId);
}

function updateScanStatus(db, scanId, status) {
    db.prepare('UPDATE nlp_scan_runs SET status = ? WHERE scan_id = ?').run(status, scanId);
}

function updateScanProgress(db, scanId, updates) {
    const fields = [];
    const values = [];
    for (const [key, val] of Object.entries(updates)) {
        fields.push(`${key} = ?`);
        values.push(val);
    }
    if (fields.length === 0) return;
    values.push(scanId);
    db.prepare(`UPDATE nlp_scan_runs SET ${fields.join(', ')} WHERE scan_id = ?`).run(...values);
}

function getAllScans(db) {
    return db.prepare('SELECT * FROM nlp_scan_runs ORDER BY created_at DESC').all();
}

// ─── Batch Management ───

function createBatches(db, scanId, batchList) {
    const stmt = db.prepare(`
        INSERT INTO nlp_batches
            (batch_id, scan_id, batch_number, trc, trc_chunk, trc_chunk_total,
             status, ticket_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
    `);
    const now = new Date().toISOString();
    const insertAll = db.transaction(() => {
        for (const b of batchList) {
            stmt.run(
                uuidv4(), scanId, b.batchNumber,
                b.trc, b.chunk || 1, b.chunkTotal || 1,
                b.ticketCount || 0, now,
            );
        }
    });
    insertAll();
}

function getNextQueuedBatch(db, scanId) {
    return db.prepare(`
        SELECT * FROM nlp_batches
        WHERE scan_id = ? AND status = 'queued'
        ORDER BY batch_number ASC
        LIMIT 1
    `).get(scanId);
}

function updateBatchStatus(db, batchId, status, updates = {}) {
    const fields = ['status = ?'];
    const values = [status];
    for (const [key, val] of Object.entries(updates)) {
        fields.push(`${key} = ?`);
        values.push(val);
    }
    values.push(batchId);
    db.prepare(`UPDATE nlp_batches SET ${fields.join(', ')} WHERE batch_id = ?`).run(...values);
}

function getBatchesForScan(db, scanId) {
    return db.prepare(
        'SELECT * FROM nlp_batches WHERE scan_id = ? ORDER BY batch_number'
    ).all(scanId);
}

function getCompletedBatchCount(db, scanId) {
    const row = db.prepare(
        "SELECT COUNT(*) AS cnt FROM nlp_batches WHERE scan_id = ? AND status = 'completed'"
    ).get(scanId);
    return row ? row.cnt : 0;
}

// ─── Classification Storage ───

function insertClassifications(db, classifications) {
    const stmt = db.prepare(`
        INSERT INTO nlp_ticket_classifications
            (classification_id, batch_id, scan_id, ticket_id, trc,
             sub_cluster, sub_cluster_confidence, is_novel,
             sentiment_intensity, sentiment_polarity, friction_type,
             anomaly_flag, anomaly_reason, entities_json, key_phrases,
             root_cause_hint, summary, raw_classification, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const now = new Date().toISOString();
    const insertAll = db.transaction(() => {
        for (const c of classifications) {
            stmt.run(
                uuidv4(), c.batchId, c.scanId, c.ticketId, c.trc,
                c.subCluster || null, c.subClusterConfidence || null,
                c.isNovel ? 1 : 0,
                c.sentimentIntensity || null, c.sentimentPolarity || null,
                c.frictionType || 'other',
                c.anomalyFlag || 'normal', c.anomalyReason || null,
                c.entitiesJson || null, c.keyPhrases || null,
                c.rootCauseHint || null, c.summary || null,
                c.rawClassification || null, now,
            );
        }
    });
    insertAll();
}

// ─── Sub-Pattern Queries (read-only from Node) ───

function getActiveSubPatterns(db, trc) {
    return db.prepare(`
        SELECT * FROM sub_patterns
        WHERE trc = ? AND tier IN ('active', 'probationary')
          AND merged_into IS NULL
        ORDER BY lifetime_tickets DESC
    `).all(trc);
}

function getSubPatternNgrams(db, patternId, minSpecificity = 0.0) {
    return db.prepare(`
        SELECT * FROM sub_pattern_ngrams
        WHERE pattern_id = ? AND specificity >= ?
        ORDER BY (frequency * specificity) DESC
        LIMIT 8
    `).all(patternId, minSpecificity);
}

// ─── Statistical Engine Queries (read-only from Node) ───

function getIncidentFlags(db, trc) {
    return db.prepare(`
        SELECT * FROM incident_flags
        WHERE trc_code = ? AND status = 'open'
        ORDER BY triggered_date DESC
        LIMIT 5
    `).all(trc);
}

function getTrcBaseline(db, trc) {
    return db.prepare('SELECT * FROM trc_baselines WHERE trc_code = ?').get(trc);
}

function getAnomalyFlags(db, trc) {
    return db.prepare(`
        SELECT * FROM anomaly_flags
        WHERE trc_code = ? AND status = 'open'
        ORDER BY date DESC
        LIMIT 5
    `).all(trc);
}

// ─── Rising Terms (for statistical context injection) ───

function getRisingTerms(db, trc, limit = 10) {
    try {
        return db.prepare(`
            SELECT term, velocity, trend
            FROM rising_terms
            WHERE trc = ?
            ORDER BY velocity DESC
            LIMIT ?
        `).all(trc, limit);
    } catch (_) {
        // Table may not exist yet
        return [];
    }
}

module.exports = {
    getDb,
    closeDb,
    WRITABLE_TABLES,
    // TRC counts
    getTrcCounts,
    getUntaggedCount,
    // Ticket data
    getTicketsForTrc,
    getUntaggedTickets,
    // Scan runs
    createScanRun,
    getScan,
    updateScanStatus,
    updateScanProgress,
    getAllScans,
    // Batches
    createBatches,
    getNextQueuedBatch,
    updateBatchStatus,
    getBatchesForScan,
    getCompletedBatchCount,
    // Classifications
    insertClassifications,
    // Sub-patterns (read-only)
    getActiveSubPatterns,
    getSubPatternNgrams,
    // Statistical engines (read-only)
    getIncidentFlags,
    getTrcBaseline,
    getAnomalyFlags,
    getRisingTerms,
};
