/**
 * Alma Insights — Scan Server Config
 * Parses CLI arguments. No defaults that could leak secrets.
 *
 * HIPAA H3:  port 0 = OS auto-assign (random ephemeral)
 * HIPAA H13: API key read from file, never passed as CLI arg.
 */

'use strict';

const fs = require('fs');
const path = require('path');

function parseArgs() {
    const args = process.argv.slice(2);
    const config = {
        port: 0,                          // H3: 0 = auto-assign
        dbPath: '',
        dataDir: '',
        apiKeyFile: '',                   // H13: path to temp file containing key
        geminiCli: '',
        geminiModel: 'gemini-2.5-flash',
        portFile: '',                     // where to write the assigned port
        tokenFile: '',                    // where to write the auth token

        // Static config
        maxBatchSize: 700,
        minTrcTickets: 5,
        maxRetries: 3,
        idleShutdownMs: 1800000,          // 30 minutes
        defaultBudgetCap: 50.0,
        batchDelayMs: 1000,
    };

    for (let i = 0; i < args.length; i++) {
        switch (args[i]) {
            case '--port':         config.port = parseInt(args[++i] || '0', 10); break;
            case '--db':           config.dbPath = args[++i]; break;
            case '--data-dir':     config.dataDir = args[++i]; break;
            case '--api-key-file': config.apiKeyFile = args[++i]; break;
            case '--gemini-cli':   config.geminiCli = args[++i]; break;
            case '--gemini-model': config.geminiModel = args[++i]; break;
            case '--port-file':    config.portFile = args[++i]; break;
            case '--token-file':   config.tokenFile = args[++i]; break;
        }
    }

    // H13: Load API key from file, then delete the file
    if (config.apiKeyFile && fs.existsSync(config.apiKeyFile)) {
        config.apiKey = fs.readFileSync(config.apiKeyFile, 'utf-8').trim();
        try { fs.unlinkSync(config.apiKeyFile); } catch (_) {}
    }

    // Default paths
    if (!config.dataDir) {
        config.dataDir = path.join(__dirname, '..', 'data');
    }
    if (!config.dbPath) {
        config.dbPath = path.join(config.dataDir, 'local_warehouse.db');
    }

    // Derived paths
    config.redactionPatternsPath = path.join(
        __dirname, '..', 'config', 'redaction_patterns.json'
    );
    config.promptsDir = path.join(__dirname, '..', 'config', 'prompts');

    return config;
}

module.exports = { parseArgs };
