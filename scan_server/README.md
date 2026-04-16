# scan_server/ — Node.js Gemini Bridge

> Express HTTPS REST API for orchestrating Gemini batch classification. Persistent subprocess managed by the Python app via `ACPBridge`.

## Setup

```bash
cd scan_server
npm install
```

**Auth:** Gemini CLI uses OAuth — run `gemini auth login` first. Credentials cached at `~/.gemini/google_accounts.json`.

**Critical:** No `@google/generative-ai` SDK. CLI-only Gemini access (HIPAA BAA requirement).

## Module Index

| File | LOC | Purpose |
|------|-----|---------|
| `server.js` | ~400 | Express app: TLS, auth, routing, idle shutdown. Entry point. |
| `config.js` | ~50 | CLI argument parsing (port, DB path, API key file) |
| `batch_worker.js` | ~300 | Batch processor: Gemini CLI invocation, progress tracking |
| `db.js` | ~250 | SQLite layer (WAL mode): scan/batch CRUD, classification writes |
| `payload_builder.js` | ~200 | Prompt assembly from SQLite data + statistical context |
| `response_parser.js` | ~200 | Parse Gemini JSON output, validate, store classifications |
| `redaction.js` | ~150 | PII/PHI scrubbing (mirrors Python `redaction_engine.py`) |
| `tls.js` | ~50 | Self-signed certificate generation (in-memory, per-run) |
| `utils.js` | ~80 | Token estimation, retry helper, structured logging |
| `test_spec_41.js` | ~100 | Pipeline 4.1 specification test |

## Hardening Controls (15 layers)

| # | Control | Purpose |
|---|---------|---------|
| H1 | TLS (self-signed, in-memory) | Encrypt loopback traffic |
| H2 | Loopback binding (127.0.0.1) | No network exposure |
| H3 | Random ephemeral port (--port 0) | No predictable endpoint |
| H4 | Per-run auth token (file handoff) | Session authentication |
| H5 | helmet() security headers | Standard HTTP hardening |
| H6 | express-rate-limit (60 req/min) | Abuse prevention |
| H7 | Host header validation | Only 127.0.0.1/localhost |
| H8 | Origin/Referer rejection | No browser requests |
| H9 | Error sanitization | No stack traces in responses |
| H10 | Body size limit (50 MB) | Prevent memory exhaustion |
| H11 | Idle shutdown (30 min) | Auto-cleanup |
| H12 | Temp file cleanup | In batch_worker.js |
| H13 | API key file handoff | Read + delete, never CLI arg |
| H14 | Orphan temp file cleanup | On startup |
| H15 | SQLite write scope | Only nlp_scan_runs, nlp_batches, nlp_ticket_classifications |

## HIPAA Controls

- **A1**: PII patterns loaded from shared `config/redaction_patterns.json`
- **A3**: Canary check for residual PII after redaction
- **A4**: Redact all Gemini output before storage

## See Also

- `docs/AGENTS.md` — Python-side pipeline that manages this server
- `docs/LLM_INTEGRATION.md` — Full LLM integration guide
