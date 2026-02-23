/**
 * Alma Insights — Batch Worker
 * Orchestrates the NLP scan: processes batches sequentially,
 * calls Gemini CLI (NOT SDK), parses responses, tracks progress.
 *
 * CRITICAL: InfoSec approved ONE path to Gemini — the CLI binary.
 * This file MUST shell out to the CLI, never use @google/generative-ai.
 *
 * HIPAA A3: PII canary on every payload (in payload_builder.js).
 * HIPAA A4: Output redaction before storage (in response_parser.js).
 * HIPAA H12: Temp file cleanup after every CLI call.
 */

'use strict';

const { execSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const dbLayer = require('./db');
const { buildPayload } = require('./payload_builder');
const { parseAndStore } = require('./response_parser');
const { redact } = require('./redaction');
const { withRetry, sleep, estimateCost, log } = require('./utils');

/**
 * BatchWorker — processes all batches for a scan using Gemini CLI.
 */
class BatchWorker {

    /**
     * @param {Object} opts
     * @param {string} opts.geminiCli - Path to Gemini CLI binary
     * @param {string} opts.geminiModel - Model name
     * @param {string} opts.apiKey - Gemini API key
     * @param {number} opts.maxRetries - Max retries per batch
     * @param {number} opts.maxBatchSize - Max tickets per chunk
     * @param {number} opts.batchDelayMs - Delay between batches
     */
    constructor(opts) {
        this.cliPath = opts.geminiCli;
        this.model = opts.geminiModel || 'gemini-2.5-flash';
        this.apiKey = opts.apiKey || '';
        this.maxRetries = opts.maxRetries || 3;
        this.maxBatchSize = opts.maxBatchSize || 700;
        this.batchDelayMs = opts.batchDelayMs || 1000;
    }

    /**
     * Main scan processing loop.
     * Called from server.js when a scan starts or resumes.
     */
    async processScan(db, scanId, scanState) {
        log('info', 'Scan loop started', { scanId });

        while (true) {
            // Check scan status from DB (pause/cancel via REST API)
            const scan = dbLayer.getScan(db, scanId);
            if (!scan) {
                log('error', 'Scan not found', { scanId });
                return;
            }

            if (scan.status === 'cancelled' || scanState.cancelled) {
                log('info', 'Scan cancelled, stopping', { scanId });
                return;
            }
            if (scan.status === 'paused' || scanState.paused) {
                log('info', 'Scan paused, stopping', { scanId });
                return;
            }

            // Budget cap check
            if (scan.actual_cost_usd >= scan.budget_cap_usd) {
                log('warn', 'Budget cap reached, pausing', {
                    scanId,
                    cost: scan.actual_cost_usd,
                    cap: scan.budget_cap_usd,
                });
                scanState.paused = true;
                dbLayer.updateScanStatus(db, scanId, 'paused');
                return;
            }

            // Get next queued batch
            const batch = dbLayer.getNextQueuedBatch(db, scanId);
            if (!batch) {
                log('info', 'All batches processed', { scanId });
                dbLayer.updateScanStatus(db, scanId, 'scan_complete');
                return;
            }

            // Process the batch
            const result = await this._processBatch(db, batch, scanState);

            // Update chunk context for multi-chunk TRCs
            if (result.success && batch.trc_chunk_total > 1) {
                this._updateChunkContext(db, scanId, batch.trc, batch.trc_chunk);
            }

            // Delay between batches
            await sleep(this.batchDelayMs);
        }
    }

    /**
     * Process a single batch: build payload, call CLI, parse response.
     */
    async _processBatch(db, batch, scanState) {
        const batchId = batch.batch_id;
        const scanId = batch.scan_id;

        log('info', 'Processing batch', {
            batchId,
            trc: batch.trc,
            chunk: `${batch.trc_chunk}/${batch.trc_chunk_total}`,
        });

        dbLayer.updateBatchStatus(db, batchId, 'running');
        const startTime = Date.now();

        try {
            // 1. Build payload
            const payload = buildPayload(db, batch, this.maxBatchSize);

            dbLayer.updateBatchStatus(db, batchId, 'running', {
                ticket_count: payload.ticketCount,
                comment_count: payload.commentCount,
                input_tokens: payload.estimatedTokens,
            });

            // 2. Call Gemini CLI with retry
            const responseText = await withRetry(
                (attempt) => {
                    if (scanState.cancelled) throw new Error('Scan cancelled');
                    if (scanState.paused) throw new Error('Scan paused');

                    log('info', 'Calling Gemini CLI', {
                        batchId,
                        attempt,
                        tokens: payload.estimatedTokens,
                    });
                    return this._callGeminiCLI(payload.prompt);
                },
                {
                    maxRetries: this.maxRetries,
                    baseDelay: 2000,
                    maxDelay: 32000,
                    onRetry: (attempt, err, delay) => {
                        log('warn', 'Gemini retry', {
                            batchId,
                            attempt,
                            error: err.message,
                            delayMs: delay,
                        });
                        dbLayer.updateBatchStatus(db, batchId, 'retrying', {
                            retry_count: attempt,
                            error_message: err.message,
                        });
                    },
                }
            );

            const latencyMs = Date.now() - startTime;

            // 3. Parse and store classifications
            const result = parseAndStore(responseText, batchId, scanId, batch.trc, db);

            // 4. Estimate output tokens and cost
            const outputTokens = Math.ceil(responseText.length * 1.3 / 4);
            const batchCost = estimateCost(payload.estimatedTokens, outputTokens);

            // 5. Update batch as completed
            dbLayer.updateBatchStatus(db, batchId, 'completed', {
                output_tokens: outputTokens,
                cost_usd: batchCost,
                latency_ms: latencyMs,
                raw_response: responseText,
                completed_at: new Date().toISOString(),
            });

            // 6. Update scan progress
            const completedCount = dbLayer.getCompletedBatchCount(db, scanId);
            const scan = dbLayer.getScan(db, scanId);
            const newCost = (scan.actual_cost_usd || 0) + batchCost;
            dbLayer.updateScanProgress(db, scanId, {
                completed_batches: completedCount,
                actual_cost_usd: newCost,
                total_input_tokens: (scan.total_input_tokens || 0) + payload.estimatedTokens,
                total_output_tokens: (scan.total_output_tokens || 0) + outputTokens,
            });

            log('info', 'Batch completed', {
                batchId,
                nParsed: result.nParsed,
                nErrors: result.nErrors,
                novelLabels: result.novelLabels,
                costUsd: batchCost.toFixed(4),
                latencyMs,
            });

            return {
                success: true,
                novelLabels: result.novelLabels,
                nParsed: result.nParsed,
                nErrors: result.nErrors,
            };

        } catch (err) {
            const latencyMs = Date.now() - startTime;

            dbLayer.updateBatchStatus(db, batchId, 'failed', {
                error_message: err.message,
                latency_ms: latencyMs,
                completed_at: new Date().toISOString(),
            });

            // Append to scan error log
            const scan = dbLayer.getScan(db, scanId);
            const errors = scan.error_log ? JSON.parse(scan.error_log) : [];
            errors.push({
                batchId,
                trc: batch.trc,
                error: err.message,
                time: new Date().toISOString(),
            });
            dbLayer.updateScanProgress(db, scanId, {
                error_log: JSON.stringify(errors),
            });

            log('error', 'Batch failed', { batchId, error: err.message, latencyMs });

            return { success: false, error: err.message };
        }
    }

    /**
     * Call Gemini CLI binary (mirrors gemini_client.py pattern).
     *
     * CRITICAL: This is the ONLY approved path to Gemini.
     * - Write prompt to temp file
     * - Shell out to CLI with stdin redirect
     * - Clean up temp file (HIPAA H12)
     *
     * @param {string} prompt - The assembled prompt text
     * @returns {string} Raw response text from Gemini
     */
    _callGeminiCLI(prompt) {
        if (!this.cliPath) {
            throw new Error('Gemini CLI path not configured');
        }

        // H12: Write prompt to temp file for stdin redirection
        const tmpDir = os.tmpdir();
        const tmpFile = path.join(
            tmpDir,
            `alma_scan_${Date.now()}_${Math.random().toString(36).slice(2)}.txt`
        );

        try {
            fs.writeFileSync(tmpFile, prompt, 'utf-8');

            // Build environment with API key
            const env = { ...process.env };
            if (this.apiKey) {
                env.GEMINI_API_KEY = this.apiKey;
            }

            // Mirror gemini_client.py exactly:
            //   gemini.CMD --model <model> -p "" < tmpfile
            const shellCmd = `"${this.cliPath}" --model ${this.model} -p "" < "${tmpFile}"`;

            const stdout = execSync(shellCmd, {
                encoding: 'utf-8',
                timeout: 300000,              // 5 min timeout
                maxBuffer: 50 * 1024 * 1024,  // 50 MB
                env,
                shell: true,
                windowsHide: true,
            });

            return stdout.trim();

        } finally {
            // H12: Always clean up temp file
            try {
                if (fs.existsSync(tmpFile)) {
                    fs.unlinkSync(tmpFile);
                }
            } catch (_) {}
        }
    }

    /**
     * Update prior_chunks_context for subsequent chunks of the same TRC.
     */
    _updateChunkContext(db, scanId, trc, chunkNumber) {
        const classifications = db.prepare(`
            SELECT sub_cluster, COUNT(*) AS cnt
            FROM nlp_ticket_classifications
            WHERE scan_id = ? AND trc = ?
            GROUP BY sub_cluster
            ORDER BY cnt DESC
        `).all(scanId, trc);

        if (classifications.length === 0) return;

        const contextLines = classifications.map(
            c => `- "${c.sub_cluster}" (${c.cnt} tickets)`
        ).join('\n');

        db.prepare(`
            UPDATE nlp_batches
            SET prior_chunks_context = ?
            WHERE scan_id = ? AND trc = ? AND status = 'queued' AND trc_chunk > ?
        `).run(contextLines, scanId, trc, chunkNumber);
    }
}

module.exports = { BatchWorker };
