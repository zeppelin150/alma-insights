# Alma Insights — Developer Setup & Conventions

## Development Setup

### Prerequisites

- Python 3.10+
- Node.js 20+ (for Gemini bridge in `scan_server/`)
- Git

### First-Time Setup

```bash
# Clone the repo
git clone <repo-url>
cd alma-insights

# Option A: Use the setup script (installs deps, creates shortcut)
python setup_alma_insights.py

# Option B: Manual setup
pip install -r requirements.txt
cd scan_server && npm install && cd ..

# Run the app
python main.py
```

### Optional Dependencies

```bash
# Google Drive export
pip install google-api-python-client google-auth

# Memory profiler diagnostics
pip install mss pytesseract Pillow
# Also requires Tesseract OCR: winget install UB-Mannheim.TesseractOCR
```

### Verify Setup

```bash
# Run a small test batch to verify everything works
python -m pytest tests/test_connection_factory.py tests/test_settings_manager.py -v
```

---

## Code Conventions

### Connection Discipline

ALL database access MUST go through `connection_factory.get_connection()`. Direct `sqlite3.connect()` is banned.

```python
# WRONG:
conn = sqlite3.connect("data/local_warehouse.db")

# RIGHT:
from src.data.connection_factory import get_connection, atomic
conn = get_connection(db_path)

# Multi-step mutations:
with atomic(conn):
    conn.execute("INSERT INTO ...")
    conn.execute("UPDATE ...")
# auto-commits on success, auto-rollbacks on exception
```

`atomic()` cannot nest — raises `RuntimeError` if called inside an existing transaction.

### Settings Access

Always use `settings_manager`. Never read/write `settings.yaml` directly.

```python
from src.data.settings_manager import get_section, set_section
gemini = get_section("gemini", {})
set_section("gemini", {"model": "gemini-2.5-flash"})
```

### LLM Routing

Use `build_client_for_task()` for all LLM access. Never instantiate clients directly.

```python
from src.gemini.client_factory import build_client_for_task
client = build_client_for_task("guru_analysis")  # auto-routes to correct provider
```

### PII Redaction

Mandatory on ALL LLM calls. Both `GeminiClient` and `ClaudeClient` apply redaction automatically. If building new LLM integrations, always apply `RedactionEngine.scrub()` before sending data.

### UI Threading

Never modify Qt widgets from worker threads. Use signals/slots:

```python
# WRONG — crashes:
def worker():
    self.label.setText("done")

# RIGHT — emit signal:
self.data_ready.emit(result)  # from worker thread
# connected to slot that runs in main thread
```

### Logging

Use the `alma.*` namespace for all loggers:
```python
import logging
logger = logging.getLogger("alma.my_module")
```

### Error Handling

- `QtErrorGuard` catches silent Qt failures — don't suppress them
- Use `try/except` around DB operations; always use `atomic()` for multi-step
- Log errors with full context: `logger.error("Failed to X: %s", e)`

---

## Testing

### Test Fixtures

Defined in `tests/conftest.py`:

| Fixture | Description |
|---------|-------------|
| `empty_db` | Fully initialized DatabaseManager, all tables, zero rows |
| `seeded_db` | Extends `empty_db` with 100 tickets across 3 TRCs (TRC-100, TRC-200, TRC-300) |
| `seeded_conn` | Raw SQLite connection from `seeded_db` |
| `mock_settings` | Temp `settings.yaml` with safe defaults |
| `mock_gemini_client` | MagicMock Gemini client |
| `mock_claude_client` | MagicMock Claude client |
| `mock_client_factory` | Patches `build_client_for_task` to return mocks |
| `mock_guru_client` | MagicMock Guru client with canned data |

### Test Markers

```python
@pytest.mark.slow        # long-running tests
@pytest.mark.e2e         # end-to-end requiring live services
@pytest.mark.ui          # requires QApplication
@pytest.mark.live_db     # uses production database
```

### Running Tests

```bash
# Run in groups of 3-4 files (full suite hangs on Windows)
python -m pytest tests/test_connection_factory.py tests/test_settings_manager.py tests/test_model_registry.py -x -v

# Skip slow tests
python -m pytest tests/test_pipeline_full.py -m "not slow" -v

# Kill zombie processes before E2E tests
wmic process where "commandline like '%alma_mcp_server%'" call terminate
```

**Known issues:**
- `test_reporting_foundation::test_bridge_fallback` — pre-existing failure
- `test_feature_integration` — live-DB-dependent failures (expected)
- Full `python -m pytest tests/` hangs on Windows — run in batches

### Writing Tests

```python
def test_my_feature(seeded_db):
    """Test description."""
    # seeded_db has 100 tickets across 3 TRCs
    result = my_function(seeded_db)
    assert result["ticket_count"] == 100

def test_with_mocks(mock_gemini_client, empty_db):
    """Test with mocked LLM."""
    mock_gemini_client.generate.return_value = '{"findings": []}'
    # ... test logic
```

---

## Adding Features

### Adding a New Analysis Engine

1. Create module in `src/data/` (e.g. `my_engine.py`)
2. Follow the interface pattern: `run_analysis(db, date_from, date_to, trc_filter) -> dict`
3. Use `get_connection()` for any DB access
4. Add tests in `tests/test_my_engine.py` using `seeded_db` fixture
5. Wire into a UI page or report pipeline

### Adding a New UI Page

See `docs/UI_GUIDE.md` → "Adding a New Page" section.

### Adding a New LLM Provider

See `docs/LLM_INTEGRATION.md` → "Adding a New Provider" section.

### Adding a New Database Table

1. Create migration file: `migrations/NNN_description.sql`
2. Use `CREATE TABLE IF NOT EXISTS` for idempotency
3. Use `ALTER TABLE ... ADD COLUMN` with `try/except` wrapping in the migrator
4. Update `docs/DATABASE.md` with the new table
5. Test with `test_migration_idempotent.py` pattern

---

## Project Structure

```
alma-insights/
  main.py              — Entry point
  requirements.txt     — Python dependencies
  setup_alma_insights.py — First-run setup
  src/                 — Application source
    agents/            — Agentic NLP pipeline (14 files)
    data/              — Data layer (85 files)
    gemini/            — Gemini integration (4 files)
    llm/               — Claude integration (4 files)
    mcp/               — MCP servers (4 files)
    services/          — Chat, persistence (8 files)
    ui/                — PySide6 UI (75 files)
    updater/           — Auto-update (4 files)
    export/            — Google Drive (2 files)
  config/              — Prompt templates, entity dicts, settings template
  migrations/          — SQL schema migrations (15 files)
  scan_server/         — Node.js Gemini bridge
  installer/           — Build & install scripts
  tests/               — 100 test files
  data/                — Runtime: settings.yaml, local_warehouse.db (gitignored)
  docs/                — Documentation
```

---

## See Also

- `CLAUDE.md` — Quick reference for the codebase
- `docs/ARCHITECTURE.md` — System architecture
- `docs/DATABASE.md` — Schema reference (for new tables/migrations)
- `docs/UI_GUIDE.md` — UI development guide (for new pages/widgets)
- `docs/LLM_INTEGRATION.md` — LLM provider guide (for new providers)
- `docs/DEPLOYMENT.md` — Build and release process
- `docs/TROUBLESHOOTING.md` — Common issues and diagnostics
