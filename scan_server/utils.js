/**
 * Alma Insights — Scan Server Utilities
 * Token estimation, retry helper, structured logging.
 */
'use strict';

/**
 * Estimate token count from text length.
 * Rough heuristic: 1 token ≈ 4 chars, with 30% overhead for JSON/markup.
 */
function estimateTokens(text) {
    if (!text) return 0;
    return Math.ceil(text.length * 1.3 / 4);
}

/**
 * Estimate cost for a batch (Gemini 2.5 Flash pricing).
 * Input: $0.15/1M tokens, Output: $0.60/1M tokens (approximate).
 */
function estimateCost(inputTokens, outputTokens) {
    const inputCost = (inputTokens / 1_000_000) * 0.15;
    const outputCost = (outputTokens / 1_000_000) * 0.60;
    return inputCost + outputCost;
}

/**
 * Retry a function with exponential backoff.
 * @param {Function} fn - Async function to retry
 * @param {Object} opts - {maxRetries, baseDelay, maxDelay, onRetry}
 */
async function withRetry(fn, opts = {}) {
    const maxRetries = opts.maxRetries || 3;
    const baseDelay = opts.baseDelay || 2000;
    const maxDelay = opts.maxDelay || 32000;
    const onRetry = opts.onRetry || (() => {});

    let lastError;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
            return await fn(attempt);
        } catch (err) {
            lastError = err;
            if (attempt >= maxRetries) break;

            const delay = Math.min(baseDelay * Math.pow(2, attempt), maxDelay);
            onRetry(attempt + 1, err, delay);
            await sleep(delay);
        }
    }
    throw lastError;
}

/**
 * Sleep for ms milliseconds.
 */
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Structured log with ISO timestamp.
 */
function log(level, message, data = {}) {
    const entry = {
        ts: new Date().toISOString(),
        level,
        msg: message,
        ...data,
    };
    if (level === 'error') {
        console.error(JSON.stringify(entry));
    } else {
        console.log(JSON.stringify(entry));
    }
}

module.exports = {
    estimateTokens,
    estimateCost,
    withRetry,
    sleep,
    log,
};
