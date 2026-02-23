/**
 * Alma Insights — Gemini Payload Builder
 * Assembles classification payloads from SQLite data + statistical context.
 *
 * HIPAA: All ticket text is redacted before inclusion in payload.
 * HIPAA A3: Canary check on every assembled payload.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const dbLayer = require('./db');
const { redactFull, canaryCheck } = require('./redaction');
const { estimateTokens, log } = require('./utils');

// Prompt template cache
let _promptTemplate = null;
let _promptsDir = null;

/**
 * Set the prompts directory path.
 */
function init(promptsDir) {
    _promptsDir = promptsDir;
}

/**
 * Load the classification prompt template.
 */
function getPromptTemplate() {
    if (_promptTemplate) return _promptTemplate;
    const dir = _promptsDir || path.join(__dirname, '..', 'config', 'prompts');
    const templatePath = path.join(dir, 'nlp_classify.txt');
    _promptTemplate = fs.readFileSync(templatePath, 'utf-8');
    return _promptTemplate;
}

/**
 * Build Gemini payload for a batch.
 * @param {Object} db - better-sqlite3 instance
 * @param {Object} batch - batch row from nlp_batches
 * @param {number} maxBatchSize - max tickets per chunk
 * @returns {Object} {prompt, estimatedTokens, ticketCount, commentCount, ticketIds}
 */
function buildPayload(db, batch, maxBatchSize) {
    const trc = batch.trc;
    const scanRow = dbLayer.getScan(db, batch.scan_id);
    const dateStart = scanRow.date_range_start;
    const dateEnd = scanRow.date_range_end;

    // 1. Get tickets for this batch
    let tickets;
    if (trc === '(untagged)') {
        tickets = dbLayer.getUntaggedTickets(db, dateStart, dateEnd);
    } else {
        tickets = dbLayer.getTicketsForTrc(db, trc, dateStart, dateEnd);
    }

    // For multi-chunk TRCs, slice to this chunk's portion
    if (batch.trc_chunk_total > 1 && tickets.length > maxBatchSize) {
        const chunkStart = (batch.trc_chunk - 1) * maxBatchSize;
        const chunkEnd = chunkStart + maxBatchSize;
        tickets = tickets.slice(chunkStart, chunkEnd);
    }

    // 2. Build ticket JSONL (redacted)
    const ticketLines = [];
    let totalComments = 0;
    for (const t of tickets) {
        const thread = t.full_thread || '';
        const redactedSubject = redactFull(t.subject || '');
        const redactedThread = redactFull(thread);

        // Count comments (rough: split by role markers)
        const commentCount = (redactedThread.match(/\[(Client|Agent|System|Bot)\]/gi) || []).length || 1;
        totalComments += commentCount;

        ticketLines.push(JSON.stringify({
            ticket_id: t.ticket_id,
            trc: t.trc_code || trc,
            created_at: t.created_at,
            subject: redactedSubject,
            csat: t.csat_score,
            thread: redactedThread.substring(0, 4000), // cap per-ticket size
        }));
    }
    const ticketJsonl = ticketLines.join('\n');

    // 3. HIPAA A3: Canary check on redacted payload
    const canaryResult = canaryCheck(ticketJsonl);
    if (!canaryResult.passed) {
        log('error', 'HIPAA A3 canary check FAILED — residual PII detected', {
            batchId: batch.batch_id,
            violations: canaryResult.violations,
        });
        throw new Error(
            `PII canary check failed: ${canaryResult.violations.length} violations. ` +
            `Batch aborted. Patterns: ${canaryResult.violations.map(v => v.pattern).join(', ')}`
        );
    }

    // 4. Build statistical context
    const statsContext = buildStatisticalContext(db, trc);

    // 5. Build sub-taxonomy section
    const taxonomySection = buildSubTaxonomySection(db, trc);

    // 6. Build prior chunks context (for multi-chunk TRCs)
    let priorChunksSection = '';
    if (batch.trc_chunk > 1 && batch.prior_chunks_context) {
        priorChunksSection = `\nSUB-PATTERNS FROM PREVIOUS CHUNKS OF THIS TRC:\n${batch.prior_chunks_context}\n\nUse these labels for matching tickets. Create new labels only for genuinely different sub-patterns not covered above.\n`;
    }

    // 7. Assemble prompt from template
    let prompt = getPromptTemplate();
    prompt = prompt.replace('{trc_path}', trc);
    prompt = prompt.replace('{statistical_context}', statsContext || 'No statistical data available for this TRC.');
    prompt = prompt.replace('{sub_taxonomy_section}', taxonomySection + priorChunksSection);
    prompt = prompt.replace('{n_tickets}', String(tickets.length));
    prompt = prompt.replace('{n_comments}', String(totalComments));
    prompt = prompt.replace('{date_start}', dateStart);
    prompt = prompt.replace('{date_end}', dateEnd);
    prompt = prompt.replace('{chunk_n}', String(batch.trc_chunk));
    prompt = prompt.replace('{chunk_total}', String(batch.trc_chunk_total));
    prompt = prompt.replace('{ticket_jsonl}', ticketJsonl);

    const estTokens = estimateTokens(prompt);

    return {
        prompt,
        estimatedTokens: estTokens,
        ticketCount: tickets.length,
        commentCount: totalComments,
        ticketIds: tickets.map(t => t.ticket_id),
    };
}

/**
 * Build statistical context block for a TRC.
 * Queries incident engine, theta engine, and rising terms.
 */
function buildStatisticalContext(db, trc) {
    const lines = [];

    // Poisson/CUSUM from incident engine
    try {
        const baseline = dbLayer.getTrcBaseline(db, trc);
        if (baseline) {
            lines.push(`- Normal daily volume: ${Number(baseline.lambda_daily || 0).toFixed(1)} tickets/day`);
            if (baseline.cusum_value > 0) {
                lines.push(`- CUSUM accumulated: ${Number(baseline.cusum_value).toFixed(1)} (threshold: ${Number(baseline.cusum_threshold).toFixed(1)})`);
            }
        }
    } catch (_) { /* table may not exist */ }

    // Incident flags
    try {
        const incFlags = dbLayer.getIncidentFlags(db, trc);
        for (const f of incFlags.slice(0, 3)) {
            lines.push(`- Incident flag (${f.flag_type}): ${f.observed_value} observed vs ${Number(f.expected_lambda || 0).toFixed(1)} expected on ${f.triggered_date}`);
        }
    } catch (_) {}

    // Theta anomaly flags
    try {
        const thetaFlags = dbLayer.getAnomalyFlags(db, trc);
        for (const f of thetaFlags.slice(0, 3)) {
            lines.push(`- theta-EWMA alert: ${f.metric_type} = ${Number(f.observed_value).toFixed(2)} (expected: ${Number(f.expected_mean).toFixed(2)}, z=${Number(f.z_score).toFixed(1)})`);
        }
    } catch (_) {}

    // Rising terms
    try {
        const rising = dbLayer.getRisingTerms(db, trc, 5);
        if (rising.length > 0) {
            const termList = rising.map(r => `"${r.term}" (v=${Number(r.velocity).toFixed(2)})`).join(', ');
            lines.push(`- Rising terms: ${termList}`);
        }
    } catch (_) {}

    return lines.length > 0 ? lines.join('\n') : '';
}

/**
 * Build sub-taxonomy section for established sub-patterns.
 */
function buildSubTaxonomySection(db, trc) {
    let patterns;
    try {
        patterns = dbLayer.getActiveSubPatterns(db, trc);
    } catch (_) {
        return '';
    }
    if (!patterns || patterns.length === 0) return '';

    const lines = [`EXISTING SUB-PATTERNS FOR THIS TRC (${patterns.length} active):\n`];

    for (let i = 0; i < patterns.length; i++) {
        const p = patterns[i];
        let ngramList = '';
        try {
            const ngrams = dbLayer.getSubPatternNgrams(db, p.pattern_id, 0.1);
            ngramList = ngrams.map(n => n.ngram).join(', ');
        } catch (_) {}

        lines.push(`${i + 1}. "${p.label}" — ${p.description || 'No description'}`);
        if (ngramList) {
            lines.push(`   N-grams: [${ngramList}]`);
        }
        lines.push(`   Friction: ${p.friction_type || 'unknown'} | Lifetime: ${p.lifetime_tickets} tickets`);
        lines.push('');
    }

    lines.push('If a ticket clearly matches an existing sub-pattern, use that exact label. If a ticket does NOT match — even partially — mark is_novel = true with justification. DO NOT force-fit. A genuine novel discovery is MORE valuable than an incorrect match.');

    return lines.join('\n');
}

module.exports = {
    init,
    buildPayload,
    buildStatisticalContext,
    buildSubTaxonomySection,
};
