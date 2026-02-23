/**
 * BUILD SPEC 4.1 — Test Suite
 * 13 test scenarios covering all hardening controls.
 */

'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

let passed = 0;
let failed = 0;

function test(name, fn) {
    try {
        fn();
        console.log('  PASS:', name);
        passed++;
    } catch (e) {
        console.error('  FAIL:', name, '-', e.message);
        failed++;
    }
}

console.log('=== BUILD SPEC 4.1 — Test Suite ===\n');

// ── Test 1: package.json has NO @google/generative-ai ──
test('T1: No banned SDK in package.json', () => {
    const pkg = require('./package.json');
    assert(!pkg.dependencies['@google/generative-ai'],
        '@google/generative-ai found in dependencies');
    assert(pkg.dependencies['better-sqlite3'], 'missing better-sqlite3');
    assert(pkg.dependencies['express'], 'missing express');
    assert(pkg.dependencies['helmet'], 'missing helmet');
    assert(pkg.dependencies['express-rate-limit'], 'missing express-rate-limit');
    assert(pkg.dependencies['node-forge'], 'missing node-forge');
    assert(pkg.dependencies['uuid'], 'missing uuid');
});

// ── Test 2: TLS cert generation ──
test('T2: TLS self-signed cert generation', () => {
    const { generateSelfSignedCert } = require('./tls');
    const { key, cert } = generateSelfSignedCert();
    assert(key.includes('BEGIN RSA PRIVATE KEY'), 'key not PEM format');
    assert(cert.includes('BEGIN CERTIFICATE'), 'cert not PEM format');
});

// ── Test 3: Config parser (H3, H13) ──
test('T3: Config parser — port 0 default, api-key-file', () => {
    const { parseArgs } = require('./config');
    const origArgv = process.argv;
    process.argv = ['node', 'server.js'];
    const cfg = parseArgs();
    process.argv = origArgv;
    assert.strictEqual(cfg.port, 0, 'default port should be 0');
    assert.strictEqual(cfg.geminiModel, 'gemini-2.5-flash', 'default model wrong');
});

// ── Test 4: Redaction parity (HIPAA A1) ──
test('T4: Redaction loads shared patterns, redacts PII', () => {
    const { init, redact, canaryCheck } = require('./redaction');
    init(path.join(__dirname, '..', 'config', 'redaction_patterns.json'));

    const input = 'Call me at 555-123-4567 or email john@example.com, SSN 123-45-6789';
    const redacted = redact(input);
    assert(!redacted.includes('555-123-4567'), 'phone not redacted');
    assert(!redacted.includes('john@example.com'), 'email not redacted');
    assert(!redacted.includes('123-45-6789'), 'SSN not redacted');
    assert(redacted.includes('[PHONE]'), 'phone token missing');
    assert(redacted.includes('[EMAIL]'), 'email token missing');
    assert(redacted.includes('[SSN]'), 'SSN token missing');
});

// ── Test 5: Canary check (HIPAA A3) ──
test('T5: Canary check detects residual PII', () => {
    const { canaryCheck } = require('./redaction');

    const clean = canaryCheck('This is clean text with no PII');
    assert(clean.passed, 'clean text should pass');

    const dirty = canaryCheck('Call 555-123-4567');
    assert(!dirty.passed, 'text with phone should fail');
    assert(dirty.violations.length > 0, 'should have violations');
});

// ── Test 6: Response parser — JSON extraction ──
test('T6: Response parser handles various JSON formats', () => {
    const { extractJsonArray } = require('./response_parser');

    // Clean array
    const clean = extractJsonArray('[{"ticket_id":"T1","sub_cluster":"test"}]');
    assert(Array.isArray(clean), 'clean array failed');
    assert.strictEqual(clean.length, 1);

    // Trailing comma
    const trailing = extractJsonArray('[{"ticket_id":"T1","sub_cluster":"test",}]');
    assert(Array.isArray(trailing), 'trailing comma failed');

    // Single object (not array)
    const single = extractJsonArray('{"ticket_id":"T1","sub_cluster":"test"}');
    assert(Array.isArray(single), 'single object failed');
    assert.strictEqual(single.length, 1);

    // With preamble text
    const preamble = extractJsonArray('Here are the results:\n[{"ticket_id":"T1","sub_cluster":"test"}]');
    assert(Array.isArray(preamble), 'preamble text failed');
    assert.strictEqual(preamble.length, 1);
});

// ── Test 7: Response parser — validation ──
test('T7: Classification validation clamps/validates', () => {
    const { validateClassification } = require('./response_parser');

    const raw = {
        ticket_id: 'T100',
        sub_cluster: 'test pattern',
        sub_cluster_confidence: 1.5,    // should clamp to 1.0
        is_novel: false,
        sentiment_intensity: 10,         // should clamp to 5
        sentiment_polarity: 'garbage',   // should default to neutral
        friction_type: 'invalid',        // should default to other
        anomaly_flag: 'unknown',         // should default to normal
    };
    const c = validateClassification(raw, 'TRC-001');
    assert.strictEqual(c.subClusterConfidence, 1.0, 'confidence not clamped');
    assert.strictEqual(c.sentimentIntensity, 5, 'sentiment not clamped');
    assert.strictEqual(c.sentimentPolarity, 'neutral', 'polarity not defaulted');
    assert.strictEqual(c.frictionType, 'other', 'friction not defaulted');
    assert.strictEqual(c.anomalyFlag, 'normal', 'anomaly not defaulted');
});

// ── Test 8: BatchWorker uses CLI, not SDK ──
test('T8: BatchWorker uses execSync (CLI), not SDK', () => {
    const workerSrc = fs.readFileSync(path.join(__dirname, 'batch_worker.js'), 'utf-8');
    assert(!workerSrc.includes("require('@google/generative-ai')"),
        'batch_worker imports banned SDK');
    assert(!workerSrc.includes('GoogleGenerativeAI'),
        'batch_worker uses GoogleGenerativeAI class');
    assert(workerSrc.includes('execSync'),
        'batch_worker should use execSync for CLI');
    assert(workerSrc.includes('_callGeminiCLI'),
        'batch_worker should have _callGeminiCLI method');
});

// ── Test 9: Server.js uses HTTPS, not HTTP ──
test('T9: Server uses HTTPS with helmet/rate-limit', () => {
    const serverSrc = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf-8');
    assert(serverSrc.includes("require('https')"),
        'server should require https');
    assert(serverSrc.includes("require('helmet')"),
        'server should require helmet');
    assert(serverSrc.includes("require('express-rate-limit')"),
        'server should require rate-limit');
    assert(serverSrc.includes('https.createServer'),
        'server should create HTTPS server');
    assert(!serverSrc.includes("require('cors')"),
        'server should NOT use cors');
    assert(serverSrc.includes('generateSelfSignedCert'),
        'server should use TLS cert');
});

// ── Test 10: H7 Host header validation ──
test('T10: Server has host header validation middleware', () => {
    const serverSrc = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf-8');
    assert(serverSrc.includes('127.0.0.1') && serverSrc.includes('localhost'),
        'host validation should check 127.0.0.1 and localhost');
});

// ── Test 11: H8 Origin/Referer rejection ──
test('T11: Server rejects Origin/Referer headers', () => {
    const serverSrc = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf-8');
    assert(serverSrc.includes('req.headers.origin') &&
           serverSrc.includes('req.headers.referer'),
        'should check for origin and referer headers');
});

// ── Test 12: H9 Error sanitization ──
test('T12: Error responses do not expose internals', () => {
    const serverSrc = fs.readFileSync(path.join(__dirname, 'server.js'), 'utf-8');
    assert(serverSrc.includes('Internal server error'),
        'should have generic error message');
    assert(serverSrc.includes('H9'), 'H9 comment should be present');

    // Count catch blocks that return generic errors
    const matches = serverSrc.match(/res\.status\(500\)\.json\(\{ error: 'Internal server error' \}\)/g);
    assert(matches && matches.length >= 5,
        'at least 5 routes should have error sanitization');
});

// ── Test 13: H15 Write scope enforcement ──
test('T13: db.js exports WRITABLE_TABLES', () => {
    const dbModule = require('./db');
    assert(dbModule.WRITABLE_TABLES, 'WRITABLE_TABLES not exported');
    assert(dbModule.WRITABLE_TABLES instanceof Set, 'WRITABLE_TABLES should be Set');
    assert(dbModule.WRITABLE_TABLES.has('nlp_scan_runs'), 'missing nlp_scan_runs');
    assert(dbModule.WRITABLE_TABLES.has('nlp_batches'), 'missing nlp_batches');
    assert(dbModule.WRITABLE_TABLES.has('nlp_ticket_classifications'),
        'missing nlp_ticket_classifications');
    assert(!dbModule.WRITABLE_TABLES.has('sub_patterns'),
        'sub_patterns should NOT be writable by Node');
    assert(!dbModule.WRITABLE_TABLES.has('nlp_findings'),
        'nlp_findings should NOT be writable by Node');
});

console.log('\n=== Results: ' + passed + ' passed, ' + failed + ' failed ===');
process.exit(failed > 0 ? 1 : 0);
