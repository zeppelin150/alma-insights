# Alma Insights — Troubleshooting Guide

## Startup Issues

### App doesn't launch / crashes immediately

**Symptoms:** Window never appears, Python traceback in console

**Check:**
1. Python version: `python --version` (need 3.10+)
2. Dependencies: `pip install -r requirements.txt`
3. PySide6 install: `python -c "from PySide6.QtWidgets import QApplication; print('OK')"`
4. Check console output for the specific error

**Common causes:**
- Missing PySide6 or wrong version (need 6.6.0+)
- Corrupted `data/local_warehouse.db` — delete it, app will recreate on launch
- `__pycache__` conflicts — cleared automatically on launch (`main.py:13-18`), but if launch crashes before that, manually delete `src/**/__pycache__`

### Settings not loading

**Symptoms:** App resets to defaults on every launch

**Check:**
- `data/settings.yaml` exists and is valid YAML
- If missing, check `config/settings.yaml.migrated` — may need to copy back to `data/`
- Settings migration runs once on first launch via `get_settings_path()`

---

## Database Issues

### "database is locked" errors

**Symptoms:** `sqlite3.OperationalError: database is locked`

**Causes:**
- Another process has a write lock (zombie Gemini/MCP process)
- `atomic()` nested inside another `atomic()` — raises `RuntimeError`

**Fix:**
```bash
# Kill zombie processes
wmic process where "commandline like '%alma_mcp_server%'" call terminate
wmic process where "commandline like '%gemini%'" call terminate

# Verify no other Python processes hold the DB
# On Windows: Task Manager → look for extra python.exe processes
```

### Schema corruption / missing tables

**Symptoms:** `no such table: ...` errors

**Fix:**
1. Let `db_manager.initialize()` recreate — it uses `CREATE TABLE IF NOT EXISTS`
2. Run migrations: they're applied automatically on every launch
3. Nuclear option: delete `data/local_warehouse.db`, restart app

### Data integrity issues

**Symptoms:** Wrong counts, stale embeddings, orphaned records

**Diagnostic:**
```python
from src.data.integrity_checker import check_data_integrity
from src.data.connection_factory import get_connection

conn = get_connection()
issues = check_data_integrity(conn)
for issue in issues:
    print(f"[{issue.severity}] {issue.check_name}: {issue.message}")
```

**Checks performed:**
1. `conversations` vs `ticket_index` count divergence
2. `ticket_index` vs `ticket_embeddings` count divergence
3. Embedding freshness (created_at vs latest scan)
4. FTS5 row count vs conversations count
5. Orphaned `nlp_ticket_classifications` (not in `ticket_index`)
6. Entity case consistency

---

## LLM Issues

### Gemini not responding

**Symptoms:** Scan hangs, reports fail with timeout

**Check:**
1. Gemini CLI authenticated: `gemini auth status`
2. If expired: `gemini auth login`
3. API quota: check Google AI Studio dashboard
4. CLI path correct in settings: Settings → AI tab → Gemini CLI Path

### Claude API errors

**Symptoms:** `ClaudeAuthError` or `ClaudeRateLimitError`

**Fix:**
- `ClaudeAuthError` (401): Check API key in Settings → AI tab
- `ClaudeRateLimitError` (429): Wait `retry_after` seconds, or reduce concurrent calls
- Verify key: Settings → AI tab → "Test Connection"

### PII redaction stripping too much

**Symptoms:** LLM responses reference `[REDACTED]` or `[EMAIL]` instead of expected content

**Check:**
- `config/entities/phi_allowlist.json` — add business terms that should be preserved
- `config/redaction_patterns.json` — check if a pattern is too aggressive

---

## Scan Pipeline Issues

### Scan hangs / no progress

**Symptoms:** Progress bar stuck, no new classifications appearing

**Diagnostic:**
1. Check `scan_progress` table: `SELECT * FROM scan_progress ORDER BY updated_at DESC LIMIT 1`
2. Check `agent_health` table: `SELECT * FROM agent_health WHERE scan_id = '<id>'`
3. Check for zombie processes (see Database Issues above)

**Common causes:**
- Bridge subprocess crashed — Supervisor should auto-restart, but check logs
- Rate limiting (429) — RateGovernor backs off exponentially. Check `gemini_usage` table.
- Budget cap reached — scan stops when `actual_cost_usd >= budget_cap_usd`
- All workers stalled — after 3 consecutive stalls, bridge restarts. If still stuck, cancel and restart scan.

### Classifications missing / low quality

**Symptoms:** Tickets not classified, wrong sub-patterns

**Check:**
1. `nlp_batches` table: look for `status = 'failed'` batches and check `error_message`
2. `review_flags` table: tickets the LLM flagged as ambiguous
3. `analyst_reports` table: quality audit results from `AnalystAgent`
4. Prompt template: `config/prompts/nlp_classify.txt` — ensure it matches current schema

### Bridge zombie processes

**Symptoms:** Multiple `gemini` or `node` processes accumulating, DB locks

**Fix:**
```bash
# Windows
wmic process where "commandline like '%alma_mcp_server%'" call terminate
wmic process where "commandline like '%gemini --acp%'" call terminate
taskkill /f /im node.exe  # nuclear — kills ALL node processes

# macOS/Linux
pkill -f alma_mcp_server
pkill -f "gemini --acp"
```

**Prevention:** ACPBridge registers atexit cleanup, but crashes can orphan processes. Always kill before running E2E tests.

---

## Memory Issues

### App using excessive memory

**Symptoms:** Slow performance, OS warnings, eventual crash

**Diagnostic:**
```bash
# Launch with memory profiler
python main.py --profile
```

Then in the app or via code:
```python
from src.data.memory_profiler import MemoryProfiler
report = MemoryProfiler.snapshot()
print(MemoryProfiler.format_report(report))
```

**Known memory-intensive operations:**
- `trending_engine.py` — holds 3-4 copies of full dataset during 12-step pipeline. Large datasets (>50K tickets) can cause OOM.
- `voc_builder.py` — accumulates evidence ledger across multiple LLM rounds
- Embedding computation — `sentence-transformers` model loads ~90 MB into memory

**Mitigation:**
- Reduce date range for trending analysis
- Use smaller batch sizes for VOC
- Close unused pages (some pages hold data references)

### Qt memory leak detection

`QtErrorGuard` monitors RSS growth every 5 seconds. If it detects rapid growth, it emits a `memory_warning` signal displayed in the status bar.

---

## UI Issues

### Silent widget crashes

**Symptoms:** Parts of the UI stop updating, charts don't render, no visible error

**Check:** Status bar — `QtErrorGuard` surfaces captured errors as a clickable label.

```python
# Programmatic access:
guard.get_error_summary()  # formatted string of all errors
guard.get_error_count()    # total count
```

**Common causes:**
- Exception in a `paintEvent` or delegate — Qt swallows these silently
- Widget modified from worker thread (see Threading rules in `docs/CONTRIBUTING.md`)
- Infinite recursion in a custom delegate

### Charts not rendering

**Symptoms:** Blank chart area, empty state shown when data exists

**Check:**
1. Is data loaded? Check the underlying DB query
2. Is the page's auto-analysis enabled? Settings → General → Section toggles
3. Date range: ensure it overlaps with available data
4. Layman mode: some labels change, but shouldn't affect rendering

### Drilldown panel not opening

**Symptoms:** Clicking a ticket/row does nothing

**Check:** The page must have `set_drilldown_panel(drilldown)` wired in `MainWindow.__init__()`.

---

## Test Issues

### Full test suite hangs on Windows

**Known issue.** Run tests in groups of 3-4 files:
```bash
python -m pytest tests/test_X.py tests/test_Y.py tests/test_Z.py -x -v
```

### Tests fail with "database is locked"

Kill zombie processes before running tests:
```bash
wmic process where "commandline like '%alma_mcp_server%'" call terminate
```

### Mock client not being used

Ensure you're using the `mock_client_factory` fixture, which patches `build_client_for_task`:
```python
def test_my_feature(seeded_db, mock_client_factory):
    # mock_client_factory patches the factory globally
    client = build_client_for_task("guru_analysis")
    # → returns mock_claude_client automatically
```

---

## Diagnostic Tools Summary

| Tool | Purpose | Usage |
|------|---------|-------|
| Memory Profiler | tracemalloc + gc analysis | `python main.py --profile` |
| QtErrorGuard | Silent Qt exception capture | Automatic — check status bar |
| Integrity Checker | DB consistency validation | `check_data_integrity(conn)` |
| Bridge Health | ACPBridge subprocess status | `bridge.get_stats()` / `bridge.ping()` |
| Scan Events | Scan pipeline event log | Query `scan_events` table |
| Gemini Usage | API cost tracking | Query `gemini_usage` table |
| Probe History | Canary probe results | Query `probe_history` table |
| Agent Health | Worker health metrics | Query `agent_health` table |

---

## See Also

- `CLAUDE.md` — Known Gotchas section
- `docs/CONTRIBUTING.md` — Developer setup and test patterns
- `docs/DEPLOYMENT.md` — Build and update system
- `docs/DATABASE.md` — Schema reference for diagnostic queries
- `docs/AGENTS.md` — Pipeline error handling and resilience
