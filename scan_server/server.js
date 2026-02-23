/**
 * Alma Insights — NLP Scan Server
 * Express HTTPS REST API for orchestrating Gemini batch classification.
 *
 * HARDENING CONTROLS:
 *   H1:  TLS (self-signed, in-memory cert via node-forge)
 *   H2:  Loopback binding (127.0.0.1 only)
 *   H3:  Random ephemeral port (--port 0)
 *   H4:  Per-run auth token (written to file for Python to read)
 *   H5:  helmet() security headers
 *   H6:  express-rate-limit (60 req/min)
 *   H7:  Host header validation (127.0.0.1 / localhost only)
 *   H8:  Origin/Referer rejection (no browser requests)
 *   H9:  Error sanitization (no stack traces in responses)
 *   H10: Body size limit (50 MB)
 *   H11: Idle shutdown (30 min)
 *   H12: Temp file cleanup (in batch_worker.js)
 *   H13: API key file handoff (read + delete, never CLI arg)
 *   H14: Clean orphan temp files on startup
 *   H15: SQLite write scope enforcement (in db.js)
 *
 * CRITICAL: NO @google/generative-ai. CLI-only Gemini access.
 */

'use strict';

const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const express = require('express');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');
const { v4: uuidv4 } = require('uuid');

const { parseArgs } = require('./config');
const { generateSelfSignedCert } = require('./tls');
const dbLayer = require('./db');
const { init: initRedaction } = require('./redaction');
const { init: initPayloadBuilder } = require('./payload_builder');
const { BatchWorker } = require('./batch_worker');
const { estimateTokens, estimateCost, log } = require('./utils');

// ═══════════════════════════════════════════
//  PARSE CONFIG
// ═══════════════════════════════════════════

const config = parseArgs();

// ═══════════════════════════════════════════
//  EXPRESS APP + MIDDLEWARE (order matters)
// ═══════════════════════════════════════════

const app = express();

// H5: Security headers
app.use(helmet());

// H10: Body size limit
app.use(express.json({ limit: '50mb' }));

// H6: Rate limiting
app.use(rateLimit({
    windowMs: 60 * 1000,
    max: 60,
    standardHeaders: true,
    legacyHeaders: false,
}));

// H7: Host header validation
app.use((req, res, next) => {
    const host = (req.headers.host || '').split(':')[0];
    if (host !== '127.0.0.1' && host !== 'localhost') {
        return res.status(403).json({ error: 'forbidden' });
    }
    next();
});

// H8: Origin/Referer rejection (block browser requests)
app.use((req, res, next) => {
    if (req.headers.origin || req.headers.referer) {
        return res.status(403).json({ error: 'browser requests not allowed' });
    }
    next();
});

// H4: Per-run auth token — all routes except /health
let authToken;

app.use((req, res, next) => {
    if (req.path === '/health') return next();
    const token = req.headers.authorization?.replace('Bearer ', '');
    if (!token || token !== authToken) {
        return res.status(401).json({ error: 'unauthorized' });
    }
    next();
});

// ═══════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════

let db;
let batchWorker;
let activeScanId = null;
let scanPaused = false;
let scanCancelled = false;
let idleTimer = null;

// ═══════════════════════════════════════════
//  IDLE SHUTDOWN (H11)
// ═══════════════════════════════════════════

function resetIdleTimer() {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
        if (!activeScanId) {
            log('info', 'Idle shutdown — no active scans for 30 minutes');
            shutdown();
        } else {
            resetIdleTimer();
        }
    }, config.idleShutdownMs);
}

function shutdown() {
    dbLayer.closeDb();
    process.exit(0);
}

// ═══════════════════════════════════════════
//  REST ROUTES
// ═══════════════════════════════════════════

// ── Health Check (public, no auth) ──

app.get('/health', (_req, res) => {
    res.json({
        status: 'ok',
        active_scans: activeScanId ? 1 : 0,
    });
});

// ── Start Scan ──

app.post('/scan/start', (req, res) => {
    try {
        const { date_start, date_end, trc_filter, batch_size, budget_cap, mode } = req.body;

        if (!date_start || !date_end) {
            return res.status(400).json({ error: 'date_start and date_end required' });
        }

        if (activeScanId) {
            return res.status(409).json({ error: 'A scan is already active', scan_id: activeScanId });
        }

        const maxBatch = batch_size || config.maxBatchSize;
        const budgetLimit = budget_cap || config.defaultBudgetCap;

        // 1. Get TRC ticket counts
        const trcCounts = dbLayer.getTrcCounts(db, date_start, date_end);
        const untaggedCount = dbLayer.getUntaggedCount(db, date_start, date_end);

        // 2. Build batch plan — TRC partitioning
        const batchPlan = [];
        let batchNumber = 0;
        let totalTickets = 0;

        for (const { trc, n } of trcCounts) {
            if (n < config.minTrcTickets) continue;

            if (trc_filter && Array.isArray(trc_filter) && !trc_filter.includes(trc)) {
                continue;
            }

            if (n <= maxBatch) {
                batchNumber++;
                batchPlan.push({
                    batchNumber,
                    trc,
                    chunk: 1,
                    chunkTotal: 1,
                    ticketCount: n,
                });
                totalTickets += n;
            } else {
                const chunkTotal = Math.ceil(n / maxBatch);
                for (let c = 1; c <= chunkTotal; c++) {
                    batchNumber++;
                    const chunkSize = c < chunkTotal
                        ? maxBatch
                        : n - (maxBatch * (chunkTotal - 1));
                    batchPlan.push({
                        batchNumber,
                        trc,
                        chunk: c,
                        chunkTotal,
                        ticketCount: chunkSize,
                    });
                    totalTickets += chunkSize;
                }
            }
        }

        // Add untagged tickets
        if (untaggedCount >= config.minTrcTickets) {
            const untaggedChunks = Math.ceil(untaggedCount / maxBatch);
            for (let c = 1; c <= untaggedChunks; c++) {
                batchNumber++;
                const chunkSize = c < untaggedChunks
                    ? maxBatch
                    : untaggedCount - (maxBatch * (untaggedChunks - 1));
                batchPlan.push({
                    batchNumber,
                    trc: '(untagged)',
                    chunk: c,
                    chunkTotal: untaggedChunks,
                    ticketCount: chunkSize,
                });
                totalTickets += chunkSize;
            }
        }

        if (batchPlan.length === 0) {
            return res.status(400).json({
                error: 'No batches to process',
                detail: `No TRCs with >= ${config.minTrcTickets} tickets in date range`,
            });
        }

        // 3. Estimate cost
        const estInputTokens = totalTickets * 600;
        const estOutputTokens = totalTickets * 200;
        const estimatedCostUsd = estimateCost(estInputTokens, estOutputTokens);

        // 4. Create scan run
        const { scanId, createdAt } = dbLayer.createScanRun(db, {
            dateStart: date_start,
            dateEnd: date_end,
            trcFilter: trc_filter || null,
            mode: mode || 'full',
            totalBatches: batchPlan.length,
            totalTickets,
            budgetCap: budgetLimit,
            configSnapshot: {
                model: config.geminiModel,
                maxBatchSize: maxBatch,
                budgetCap: budgetLimit,
            },
        });

        // 5. Create batch records
        dbLayer.createBatches(db, scanId, batchPlan);

        // 6. Update scan status
        dbLayer.updateScanProgress(db, scanId, {
            estimated_cost_usd: estimatedCostUsd,
            total_input_tokens: estInputTokens,
            total_output_tokens: estOutputTokens,
            status: 'running',
        });

        // 7. Prepare for processing
        activeScanId = scanId;
        scanPaused = false;
        scanCancelled = false;
        resetIdleTimer();

        log('info', 'Scan started', {
            scanId,
            batches: batchPlan.length,
            tickets: totalTickets,
            estimatedCost: estimatedCostUsd.toFixed(2),
        });

        // Send response BEFORE starting batch processing.
        // execSync in _callGeminiCLI blocks the event loop,
        // so we must flush the HTTP response first.
        res.json({
            scan_id: scanId,
            total_batches: batchPlan.length,
            total_tickets: totalTickets,
            estimated_cost: estimatedCostUsd,
        });

        // Start processing on next tick so response is fully sent
        setImmediate(() => startBatchProcessing(scanId));
    } catch (err) {
        log('error', 'Scan start failed', { error: err.message });
        // H9: Error sanitization — no stack traces
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── Scan Status ──

app.get('/scan/:id/status', (req, res) => {
    try {
        const scan = dbLayer.getScan(db, req.params.id);
        if (!scan) return res.status(404).json({ error: 'scan not found' });

        const completedBatches = dbLayer.getCompletedBatchCount(db, req.params.id);
        const currentBatch = dbLayer.getNextQueuedBatch(db, req.params.id);

        res.json({
            scan_id: scan.scan_id,
            status: scan.status,
            completed_batches: completedBatches,
            total_batches: scan.total_batches,
            total_tickets: scan.total_tickets,
            cost_so_far: scan.actual_cost_usd || 0,
            budget_cap: scan.budget_cap_usd,
            estimated_cost: scan.estimated_cost_usd,
            current_batch_trc: currentBatch ? currentBatch.trc : null,
            errors: scan.error_log ? JSON.parse(scan.error_log) : [],
        });
    } catch (err) {
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── Pause Scan ──

app.post('/scan/:id/pause', (req, res) => {
    try {
        if (activeScanId !== req.params.id) {
            return res.status(404).json({ error: 'scan not active' });
        }
        scanPaused = true;
        dbLayer.updateScanStatus(db, req.params.id, 'paused');
        log('info', 'Scan paused', { scanId: req.params.id });
        res.json({ status: 'paused' });
    } catch (err) {
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── Resume Scan ──

app.post('/scan/:id/resume', (req, res) => {
    try {
        const scan = dbLayer.getScan(db, req.params.id);
        if (!scan) return res.status(404).json({ error: 'scan not found' });

        if (scan.status !== 'paused') {
            return res.status(400).json({ error: 'scan is not paused' });
        }

        if (req.body && req.body.budget_cap) {
            dbLayer.updateScanProgress(db, req.params.id, {
                budget_cap_usd: req.body.budget_cap,
            });
        }

        activeScanId = req.params.id;
        scanPaused = false;
        scanCancelled = false;
        dbLayer.updateScanStatus(db, req.params.id, 'running');
        log('info', 'Scan resumed', { scanId: req.params.id });
        res.json({ status: 'running' });

        // Start processing on next tick so response is fully sent
        setImmediate(() => startBatchProcessing(req.params.id));
    } catch (err) {
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── Cancel Scan ──

app.post('/scan/:id/cancel', (req, res) => {
    try {
        const scan = dbLayer.getScan(db, req.params.id);
        if (!scan) return res.status(404).json({ error: 'scan not found' });

        scanCancelled = true;
        if (activeScanId === req.params.id) {
            activeScanId = null;
        }
        dbLayer.updateScanStatus(db, req.params.id, 'cancelled');
        log('info', 'Scan cancelled', { scanId: req.params.id });
        resetIdleTimer();
        res.json({ status: 'cancelled' });
    } catch (err) {
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ── Scan History ──

app.get('/scan/history', (_req, res) => {
    try {
        const scans = dbLayer.getAllScans(db);
        res.json(scans.map(s => ({
            scan_id: s.scan_id,
            date_range: `${s.date_range_start} to ${s.date_range_end}`,
            status: s.status,
            total_batches: s.total_batches,
            completed_batches: s.completed_batches,
            total_tickets: s.total_tickets,
            cost: s.actual_cost_usd || 0,
            created_at: s.created_at,
        })));
    } catch (err) {
        res.status(500).json({ error: 'Internal server error' });
    }
});

// H9: Global error handler — no stack traces
app.use((err, _req, res, _next) => {
    log('error', 'Unhandled error', { error: err.message });
    res.status(500).json({ error: 'Internal server error' });
});

// ═══════════════════════════════════════════
//  BATCH PROCESSING
// ═══════════════════════════════════════════

function startBatchProcessing(scanId) {
    log('info', 'Starting batch processing', { scanId });

    const scanState = {
        get paused() { return scanPaused; },
        set paused(v) { scanPaused = v; },
        get cancelled() { return scanCancelled; },
        set cancelled(v) { scanCancelled = v; },
    };

    batchWorker.processScan(db, scanId, scanState)
        .then(() => {
            if (!scanCancelled) {
                log('info', 'Scan loop completed', { scanId });
            }
            if (activeScanId === scanId && !scanPaused) {
                activeScanId = null;
            }
            resetIdleTimer();
        })
        .catch(err => {
            log('error', 'Scan loop failed', { scanId, error: err.message });
            dbLayer.updateScanStatus(db, scanId, 'failed');
            activeScanId = null;
            resetIdleTimer();
        });
}

// ═══════════════════════════════════════════
//  H14: CLEAN ORPHAN TEMP FILES
// ═══════════════════════════════════════════

function cleanOrphanTempFiles() {
    const tmpDir = os.tmpdir();
    try {
        const files = fs.readdirSync(tmpDir);
        let cleaned = 0;
        for (const f of files) {
            if (f.startsWith('alma_scan_') && f.endsWith('.txt')) {
                try {
                    fs.unlinkSync(path.join(tmpDir, f));
                    cleaned++;
                } catch (_) {}
            }
        }
        if (cleaned > 0) {
            log('info', 'Cleaned orphan temp files', { count: cleaned });
        }
    } catch (_) {}
}

// ═══════════════════════════════════════════
//  STARTUP
// ═══════════════════════════════════════════

function start() {
    log('info', 'Starting scan server', { db: config.dbPath });

    // H14: Clean orphan temp files from previous crashes
    cleanOrphanTempFiles();

    // H1: Generate per-run TLS certificate (in-memory)
    const { key, cert } = generateSelfSignedCert();

    // H4: Generate per-run auth token
    authToken = crypto.randomBytes(32).toString('hex');

    // Init database
    db = dbLayer.getDb(config.dbPath);

    // Init redaction with shared config path
    initRedaction(config.redactionPatternsPath);

    // Init payload builder with prompts dir
    initPayloadBuilder(config.promptsDir);

    // Init batch worker (CLI-only, no SDK)
    batchWorker = new BatchWorker({
        geminiCli: config.geminiCli,
        geminiModel: config.geminiModel,
        apiKey: config.apiKey || '',
        maxRetries: config.maxRetries,
        maxBatchSize: config.maxBatchSize,
        batchDelayMs: config.batchDelayMs,
    });

    // Check for interrupted scans from previous session
    const allScans = dbLayer.getAllScans(db);
    const interrupted = allScans.find(s => s.status === 'running');
    if (interrupted) {
        log('info', 'Found interrupted scan, marking as paused', {
            scanId: interrupted.scan_id,
        });
        dbLayer.updateScanStatus(db, interrupted.scan_id, 'paused');
    }

    // H1 + H2 + H3: HTTPS server on loopback with auto-assigned port
    const httpsServer = https.createServer({ key, cert }, app);

    httpsServer.listen(config.port, '127.0.0.1', () => {
        const assignedPort = httpsServer.address().port;
        log('info', `Scan server listening on https://127.0.0.1:${assignedPort}`);

        // Write port file for Python to discover
        if (config.portFile) {
            fs.writeFileSync(config.portFile, String(assignedPort), 'utf-8');
        } else {
            const defaultPortFile = path.join(config.dataDir, '.scan_server_port');
            fs.writeFileSync(defaultPortFile, String(assignedPort), 'utf-8');
        }

        // Write token file for Python to read
        if (config.tokenFile) {
            fs.writeFileSync(config.tokenFile, authToken, 'utf-8');
        } else {
            const defaultTokenFile = path.join(config.dataDir, '.scan_server_token');
            fs.writeFileSync(defaultTokenFile, authToken, 'utf-8');
        }

        resetIdleTimer();
    });

    // Graceful shutdown
    process.on('SIGTERM', () => {
        log('info', 'SIGTERM received, shutting down');
        httpsServer.close(() => shutdown());
    });
    process.on('SIGINT', () => {
        log('info', 'SIGINT received, shutting down');
        httpsServer.close(() => shutdown());
    });
}

// Run if executed directly
if (require.main === module) {
    start();
}

module.exports = { app, start };
