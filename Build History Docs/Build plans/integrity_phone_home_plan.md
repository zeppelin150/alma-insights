# Integrity Phone-Home — Implementation Plan

## Context

The integrity checker (`integrity_checker.py`, scan step 8.7) already detects data inconsistencies post-scan. Currently it logs warnings and emits UI events, but the development team has no visibility into issues hitting users in the field. This adds a phone-home component that POSTs integrity failure reports to a managed endpoint when issues are found.

**Design decisions** (from user):
- Endpoint URL lives in a YAML config file, managed via GitHub deployment
- Report payload: integrity issues + scan metadata + environment info
- Always on, no user toggle
- Only fires when issues are found (not on clean scans)

---

## Architecture

```
integrity_checker.py (existing)
    |
    +-- run_post_scan_integrity() returns {"passed": bool, "issues": [...]}
    |
    +-- NEW: if not passed -> phone_home.send_integrity_report(integrity_result)
                                |
                                +-- Reads endpoint from config/phone_home.yaml
                                +-- Enriches with scan metadata + environment
                                +-- POSTs JSON via urllib (non-blocking, fire-and-forget)
                                +-- Never raises -- all failures swallowed + logged
```

---

## Step 1: Config file -- `config/phone_home.yaml`

```yaml
# Managed via GitHub deployment. Do not edit manually.
endpoint: "https://placeholder.example.com/api/v1/integrity-reports"
timeout_seconds: 10
```

Hardcoded path, loaded at report time. No settings_manager integration (this isn't a user-facing setting -- it's deployment infrastructure).

**Critical files:**
- New: `config/phone_home.yaml`

---

## Step 2: Phone-home module -- `src/data/phone_home.py` (~80 LOC)

```python
def send_integrity_report(integrity_result: dict, scan_metadata: dict | None = None) -> None:
```

**Responsibilities:**
1. Load endpoint URL from `config/phone_home.yaml`
2. Build enriched payload:
   - `integrity`: the full integrity result dict (issues, passed, checked_at, scan_id)
   - `scan`: scan_id, ticket_count, scan_duration, app_version
   - `environment`: Python version, PySide6 version, platform, OS version, DB file size, key table row counts (ticket_index, ticket_embeddings, conversations)
3. POST JSON to endpoint via `urllib.request.Request` (matching existing pattern in `guru_client.py`)
4. Run in a daemon thread (fire-and-forget, never blocks scan pipeline)
5. **Never raises** -- all exceptions caught and logged as debug

**Environment collection** (no PHI):
```python
{
    "python_version": sys.version,
    "pyside6_version": PySide6.__version__,
    "platform": sys.platform,
    "os_version": platform.version(),
    "db_size_bytes": os.path.getsize(db_path),
    "table_counts": {
        "ticket_index": N,
        "ticket_embeddings": N,
        "conversations": N,
        "nlp_ticket_classifications": N,
    }
}
```

**Critical files:**
- New: `src/data/phone_home.py`

---

## Step 3: Wire into scan_orchestrator.py (step 8.7)

Modify the existing integrity check block at ~line 910. After detecting issues, call phone_home:

```python
# -- 8.7 Post-scan integrity check --
try:
    from src.data.integrity_checker import run_post_scan_integrity
    integrity_conn = get_connection(self.db_path)
    integrity = run_post_scan_integrity(integrity_conn, scan_id)
    integrity_conn.close()
    if integrity["passed"]:
        self._emit_event(scan_id, 'info', 'running', 'Data integrity verified')
    else:
        issue_count = len(integrity["issues"])
        self._emit_event(scan_id, 'warning', 'running',
                         f'Data integrity: {issue_count} issue(s) found')
        # Phone home with failure report
        try:
            from src.data.phone_home import send_integrity_report
            send_integrity_report(integrity, scan_metadata={
                "ticket_count": self._total_tickets,
                "db_path": str(self.db_path),
            })
        except Exception:
            pass  # phone-home must never affect scan
except Exception as e:
    logger.warning(f"Integrity check failed (non-fatal): {e}")
```

**Critical files:**
- Modified: `src/agents/scan_orchestrator.py` (~line 910-925)

---

## Step 4: Tests -- `tests/test_phone_home.py` (~80 LOC, 6 tests)

- `test_build_payload_structure` -- verify payload has integrity/scan/environment keys
- `test_environment_collection` -- verify python_version, platform, db_size present
- `test_no_phi_in_payload` -- verify no ticket content, conversation text, or PII
- `test_missing_config_does_not_raise` -- graceful no-op if phone_home.yaml missing
- `test_network_failure_does_not_raise` -- mock urllib to raise, verify swallowed
- `test_fires_in_thread` -- verify send runs in daemon thread (non-blocking)

Tests mock `urllib.request.urlopen` -- no real HTTP calls.

**Critical files:**
- New: `tests/test_phone_home.py`

---

## Deliverables

| Type | File | LOC |
|------|------|-----|
| New config | `config/phone_home.yaml` | ~3 |
| New module | `src/data/phone_home.py` | ~80 |
| New tests | `tests/test_phone_home.py` | ~80 |
| Modified | `src/agents/scan_orchestrator.py` | ~6 lines added |

---

## Verification

```bash
# Unit tests
pytest tests/test_phone_home.py -v --tb=short

# Regression (integrity checker + orchestrator still work)
pytest tests/test_integrity_checker.py tests/test_hardening.py -v --tb=short

# Verify no PHI leakage in payload
grep -n "full_thread\|conversation\|comment_body\|customer_name" src/data/phone_home.py
# Should return 0 matches
```
