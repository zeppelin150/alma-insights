/**
 * Alma Insights — PII/PHI Redaction (Node.js)
 * HIPAA A1: Loads patterns from shared config/redaction_patterns.json
 * HIPAA A3: Canary check for residual PII after redaction
 * HIPAA A4: Redact Gemini output before storage
 *
 * Single source of truth — Python gemini_client.py loads the same file.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { log } = require('./utils');

let _patterns = null;
let _skipTerms = null;
let _patternsPath = null;

/**
 * Initialize redaction with path to shared config.
 */
function init(redactionPatternsPath) {
    _patternsPath = redactionPatternsPath;
}

/**
 * Load redaction patterns from shared JSON config. Cached after first call.
 */
function _loadPatterns() {
    if (_patterns) return;

    // Resolve path — either set via init() or default
    const configPath = _patternsPath || path.join(
        __dirname, '..', 'config', 'redaction_patterns.json'
    );

    try {
        const raw = fs.readFileSync(configPath, 'utf-8');
        const cfg = JSON.parse(raw);
        _patterns = cfg.patterns || [];
        _skipTerms = new Set(cfg.aggressive_skip_terms || []);
        log('info', 'Redaction patterns loaded', { count: _patterns.length });
    } catch (err) {
        log('error', 'Failed to load redaction patterns', { error: err.message });
        _patterns = [];
        _skipTerms = new Set();
    }
}

/**
 * Apply base PII redaction to text.
 * Applies all non-conditional patterns from the shared config.
 */
function redact(text) {
    if (!text) return text;
    _loadPatterns();

    for (const pat of _patterns) {
        if (pat.conditional) continue; // skip name_heuristic
        const flags = (pat.flags === 'i') ? 'gi' : 'g';
        const re = new RegExp(pat.regex, flags);
        text = text.replace(re, pat.replace);
    }
    return text;
}

/**
 * Apply aggressive redaction (name heuristic) to text.
 * Skips RCM domain terms that look like names but aren't.
 */
function redactAggressive(text) {
    if (!text) return text;
    _loadPatterns();

    const namePat = _patterns.find(p => p.conditional && p.name === 'name_heuristic');
    if (!namePat) return text;

    const re = new RegExp(namePat.regex, 'g');
    text = text.replace(re, (match, word1, word2) => {
        if (_skipTerms.has(word1) || _skipTerms.has(word2)) {
            return match; // don't redact RCM terms
        }
        return namePat.replace;
    });
    return text;
}

/**
 * Full redaction: base + aggressive.
 */
function redactFull(text) {
    text = redact(text);
    text = redactAggressive(text);
    return text;
}

/**
 * HIPAA A3: Pre-flight PII canary check.
 * Scans redacted text for patterns that should NOT survive redaction.
 * Returns {passed: bool, violations: [{pattern, match}]}
 */
function canaryCheck(text) {
    if (!text) return { passed: true, violations: [] };

    const canaryPatterns = [
        { name: 'SSN', regex: /\b\d{3}-\d{2}-\d{4}\b/g },
        { name: 'Phone', regex: /\b\d{3}[-.]?\d{3}[-.]?\d{4}\b/g },
        { name: 'Email', regex: /\b[\w.-]+@[\w.-]+\.\w{2,}\b/g },
        { name: 'DOB', regex: /\b\d{2}\/\d{2}\/\d{4}\b/g },
    ];

    const violations = [];
    for (const cp of canaryPatterns) {
        const matches = text.match(cp.regex);
        if (matches) {
            for (const m of matches) {
                // Ignore redaction tokens themselves
                if (m.startsWith('[') && m.endsWith(']')) continue;
                violations.push({ pattern: cp.name, match: m });
            }
        }
    }

    return {
        passed: violations.length === 0,
        violations,
    };
}

module.exports = {
    init,
    redact,
    redactAggressive,
    redactFull,
    canaryCheck,
};
