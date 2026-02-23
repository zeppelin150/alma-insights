/**
 * Alma Insights — Gemini Response Parser
 * Parses JSON classification array from Gemini, validates fields,
 * stores to nlp_ticket_classifications.
 *
 * HIPAA A4: Redacts all Gemini output fields before storage.
 */

'use strict';

const dbLayer = require('./db');
const { redact } = require('./redaction');
const { log } = require('./utils');

// Valid friction_type enum values
const VALID_FRICTION_TYPES = new Set([
    'access_blocked', 'self_serve_failure', 'automation_loop',
    'incorrect_charge', 'missing_information', 'policy_confusion',
    'feature_broken', 'feature_missing', 'process_delay',
    'communication_gap', 'escalation_demand', 'repeat_contact',
    'positive_feedback', 'other',
]);

const VALID_ANOMALY_FLAGS = new Set(['normal', 'unusual', 'critical']);
const VALID_POLARITIES = new Set(['positive', 'negative', 'mixed', 'neutral']);

/**
 * Parse raw Gemini response text into validated classifications.
 * @param {string} rawResponse - Raw text from Gemini
 * @param {string} batchId - Batch UUID
 * @param {string} scanId - Scan UUID
 * @param {string} trc - TRC code for this batch
 * @param {Object} db - better-sqlite3 instance
 * @returns {Object} {nParsed, nErrors, novelLabels, classifications}
 */
function parseAndStore(rawResponse, batchId, scanId, trc, db) {
    // 1. Extract JSON array from response
    let parsed;
    try {
        parsed = extractJsonArray(rawResponse);
    } catch (err) {
        log('error', 'JSON parse failed', { batchId, error: err.message });
        return { nParsed: 0, nErrors: 1, novelLabels: [], error: err.message };
    }

    if (!Array.isArray(parsed)) {
        log('error', 'Response is not a JSON array', { batchId });
        return { nParsed: 0, nErrors: 1, novelLabels: [], error: 'Not a JSON array' };
    }

    // 2. Validate and normalize each classification
    const classifications = [];
    const novelLabels = [];
    let nErrors = 0;

    for (const item of parsed) {
        try {
            const c = validateClassification(item, trc);

            // HIPAA A4: Redact Gemini output before storage
            c.summary = redact(c.summary || '');
            c.rootCauseHint = redact(c.rootCauseHint || '');
            c.anomalyReason = redact(c.anomalyReason || '');
            if (c.keyPhrases) {
                try {
                    const phrases = JSON.parse(c.keyPhrases);
                    c.keyPhrases = JSON.stringify(phrases.map(p => redact(p)));
                } catch (_) {
                    c.keyPhrases = redact(c.keyPhrases);
                }
            }

            c.batchId = batchId;
            c.scanId = scanId;
            c.trc = trc;

            classifications.push(c);

            if (c.isNovel) {
                novelLabels.push(c.subCluster);
            }
        } catch (err) {
            nErrors++;
            log('warn', 'Classification validation failed', {
                batchId,
                ticketId: item?.ticket_id,
                error: err.message,
            });
        }
    }

    // 3. Store to SQLite
    if (classifications.length > 0) {
        dbLayer.insertClassifications(db, classifications);
    }

    log('info', 'Batch parsed', {
        batchId,
        nParsed: classifications.length,
        nErrors,
        novelLabels,
    });

    return {
        nParsed: classifications.length,
        nErrors,
        novelLabels,
        classifications,
    };
}

/**
 * Extract JSON array from raw Gemini response text.
 * Handles: clean arrays, markdown-wrapped, trailing commas,
 * single objects, partial responses.
 */
function extractJsonArray(text) {
    if (!text || !text.trim()) {
        throw new Error('Empty response');
    }

    let cleaned = text.trim();

    // Strip markdown fences
    cleaned = cleaned.replace(/^```(?:json)?\s*/i, '');
    cleaned = cleaned.replace(/\s*```\s*$/, '');

    // Try direct parse first (clean array)
    try {
        const direct = JSON.parse(cleaned);
        if (Array.isArray(direct)) return direct;
        // Single object — wrap in array
        if (direct && typeof direct === 'object' && direct.ticket_id) {
            return [direct];
        }
    } catch (_) {}

    // Find the first [ and last ]
    const firstBracket = cleaned.indexOf('[');
    const lastBracket = cleaned.lastIndexOf(']');
    if (firstBracket !== -1 && lastBracket > firstBracket) {
        let arrayText = cleaned.substring(firstBracket, lastBracket + 1);

        // Fix trailing commas before ] or }
        arrayText = arrayText.replace(/,\s*([}\]])/g, '$1');

        try {
            return JSON.parse(arrayText);
        } catch (_) {}
    }

    // Fallback: extract individual JSON objects
    return extractObjectsFallback(cleaned);
}

/**
 * Fallback: extract individual JSON objects from malformed response.
 */
function extractObjectsFallback(text) {
    const objects = [];
    const regex = /\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g;
    let match;
    while ((match = regex.exec(text)) !== null) {
        try {
            const obj = JSON.parse(match[0]);
            if (obj.ticket_id) {
                objects.push(obj);
            }
        } catch (_) {
            // skip invalid objects
        }
    }

    if (objects.length === 0) {
        throw new Error('No valid JSON classifications found in response');
    }

    log('warn', 'Used fallback object extraction', { objectsFound: objects.length });
    return objects;
}

/**
 * Validate and normalize a single classification object.
 */
function validateClassification(item, trc) {
    if (!item.ticket_id) {
        throw new Error('Missing ticket_id');
    }

    return {
        ticketId: String(item.ticket_id),
        subCluster: item.sub_cluster || null,
        subClusterConfidence: clamp(item.sub_cluster_confidence, 0, 1),
        isNovel: Boolean(item.is_novel),
        sentimentIntensity: clamp(item.sentiment_intensity, 1, 5),
        sentimentPolarity: VALID_POLARITIES.has(item.sentiment_polarity)
            ? item.sentiment_polarity : 'neutral',
        frictionType: VALID_FRICTION_TYPES.has(item.friction_type)
            ? item.friction_type : 'other',
        anomalyFlag: VALID_ANOMALY_FLAGS.has(item.anomaly_flag)
            ? item.anomaly_flag : 'normal',
        anomalyReason: item.anomaly_reason || null,
        entitiesJson: item.entities ? JSON.stringify(item.entities) : null,
        keyPhrases: item.key_phrases ? JSON.stringify(item.key_phrases) : null,
        rootCauseHint: item.root_cause_hint || null,
        summary: item.summary || null,
        rawClassification: JSON.stringify(item),
    };
}

function clamp(val, min, max) {
    if (val == null || isNaN(val)) return null;
    return Math.max(min, Math.min(max, Number(val)));
}

module.exports = {
    parseAndStore,
    extractJsonArray,
    validateClassification,
};
