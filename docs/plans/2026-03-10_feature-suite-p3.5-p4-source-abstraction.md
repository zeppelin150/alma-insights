# Alma Insights Feature Suite — Implementation Plan

## Context

Alma is adding multi-provider LLM support (Claude alongside Gemini), auto-updates, multi-source real-time monitoring with intelligent alerting, and Guru knowledge base integration with friction analysis. Built on a **source-agnostic abstraction layer** so adding new data sources (Intercom, Jira, etc.) requires implementing one client class + one UI tab — not rewriting the pipeline.

**Current state (2026-03-10)**: 65 source monitor tests + ~141 total passing. 9 pages in MainWindow (indices 0-8). Settings centralized in `data/settings.yaml`. Multi-provider LLM (Gemini + Claude). Zendesk Source Monitor live with configurable TRC field mapping.

---

## Execution Order

### Phase 0 — Settings Migration ✅ DONE
### Phase 1 — Claude Client + Model Registry ✅ DONE
### Phase 2 — Auto-Update System ✅ DONE
### Phase 3 — Zendesk Source Monitor ✅ DONE
### Phase 3.5 — Source Abstraction + Data Warehouse + Watchlist Engine ← NEXT
### Phase 4 — Guru Integration (friction analysis + effectiveness tracking)
### Phase 5 — GitHub Wiring + Settings UI
### Phase 6 — CUSUM Corrections (separate spec, deferred)

Each phase gate: run `pytest tests/ -v`, verify 0 regressions + new tests pass.

---

## Phases 0–3 — DONE (Compact Reference)

### Phase 0 — Settings Migration ✅
Centralized `settings_manager.py` (load/save/get_section/set_section). Migrated 46 call sites from `config/` → `data/`. 16 tests.

### Phase 1 — Claude Client + Model Registry ✅
`model_registry.py` (ModelConfig dataclass + singleton + model_changed Signal). `claude_client.py` (Anthropic API, PII redaction, streaming). Multi-provider `client_factory.py` with `build_client_for_model()`. Settings 4-tab restructure (AI Provider/Integrations/Updates/Display). 30 tests.

### Phase 2 — Auto-Update System ✅
`update_checker.py` (GitHub releases API). `updater.py` (stage-and-apply). `schema_migrator.py` (numbered SQL migration runner). Updates tab GitHub config. Unified PII toggle. Smart Reporting Auto-Import card. 30 tests.

### Phase 3 — Zendesk Source Monitor ✅
`zendesk_client.py` (incremental export, view fetch, ticket fields API, configurable TRC extraction with `tag:<prefix>` support). `zendesk_monitor.py` (QTimer polling, TRC spike detection, duplicate prevention). `source_monitor_page.py` (3-tab UI: Live Feed + TRC Spikes + Connection with TRC field mapping). MainWindow `PAGE_SOURCE_MONITOR=8` with LIVE badge. 65 tests.

Smoke-tested against live Zendesk instance (`d3v-acgshelp`, view `43825747769107`, 1,778 tickets).

---

## Phase 3.5 — Source Abstraction + Data Warehouse + Watchlist Engine

**Why this phase exists**: Phase 3 delivers real-time UI monitoring but tickets are transient (in-memory only). We need persistent classified metadata for anomaly detection baselines, a rule engine for outage/single-ticket detection, and — critically — a **source abstraction layer** so the warehouse, watchlist, and downstream Guru integration are source-agnostic from day one. Adding Intercom or Jira later should require implementing one client class + one UI tab, not touching the warehouse or watchlist code.

### Architecture Overview

**Two-Tier Data Model**:
- **Hot Tier** (in-memory): Full records with PHI (subject, description). Powers Live Feed UI. **Never persisted** — PHI stays in memory only.
- **Cold Tier** (SQLite): Classified metadata only. No PHI. Powers anomaly detection, trend analysis, Guru effectiveness.

**Two Separate Data Paths** (no contamination):
```
PATH A (Batch):   Lightdash/CSV → conversations table → ScanOrchestrator → NLP pipeline
PATH B (Live):    Source API → SourceMonitor (hot) → source_events (cold) → Watchlist
```

These paths share `sub_patterns` as a read-only cross-reference (the hybrid classifier reuses NLP scan patterns to classify live tickets). The warehouse **reads** sub_patterns but **never writes** to `conversations`.

**Future bridge**: When Auto-Import activates, it inserts live tickets into `conversations` with `source='zendesk'`, enabling NLP scans on live data. The `source` column on `conversations` prevents data contamination.

---

### P3.5-T1: Source Abstraction Interfaces — `src/data/source_types.py` (~80 lines)

Thin abstraction layer — base classes that define the contract for any data source.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal


@dataclass
class SourceConfig:
    """Describes a configured data source."""
    name: str           # "zendesk", "intercom", "jira"
    display_name: str   # "Zendesk Support"
    icon: str           # emoji or icon path
    is_configured: bool
    is_connected: bool


class SourceClient(ABC):
    """Base class for all source API clients.

    Implementations: ZendeskClient (P3), IntercomClient (future), etc.
    """

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def fetch_incremental(self, cursor: str | None = None
                          ) -> tuple[list[dict], str]:
        """Fetch records since cursor. Returns (records, next_cursor)."""
        ...

    @abstractmethod
    def extract_trc(self, record: dict, trc_field: str = "subject") -> str:
        """Extract TRC code from a record using configured field mapping."""
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the source identifier string (e.g. 'zendesk')."""
        ...


class SourceMonitor(QObject):
    """Base class for real-time polling monitors.

    Subclasses: ZendeskMonitor (P3). Emits source-agnostic signals
    consumed by SourceMonitorPage, warehouse, and watchlist.
    """
    records_received = Signal(list)           # raw record dicts
    spike_detected = Signal(str, int, float)  # (trc, count, delta_pct)
    alert_fired = Signal(dict)                # watchlist alert
    status_changed = Signal(str)              # "live"|"paused"|"error"

    @abstractmethod
    def start(self, interval_seconds: int = 120): ...

    @abstractmethod
    def pause(self): ...

    @abstractmethod
    def resume(self): ...

    @abstractmethod
    def stop(self): ...

    @property
    @abstractmethod
    def source_name(self) -> str: ...
```

**Why this is thin**: No framework, no plugin registry, no config files. Just ABCs that formalize what `ZendeskClient` and `ZendeskMonitor` already do. `ZendeskClient` and `ZendeskMonitor` gain `(SourceClient)` / `(SourceMonitor)` inheritance — their existing methods already satisfy the interface.

### P3.5-T2: Retrofit Zendesk classes to inherit from source abstractions

Minimal changes to existing working code:

**`zendesk_client.py`**: Add `class ZendeskClient(SourceClient):` and `@property source_name` returning `"zendesk"`. All existing methods already match the ABC signatures. Add `@abstractmethod` satisfaction markers.

**`zendesk_monitor.py`**: Add `class ZendeskMonitor(SourceMonitor):` (multi-inherit with QObject already in SourceMonitor). Add `@property source_name` returning `"zendesk"`. Rename `tickets_received` → keep as alias, add `records_received` Signal forwarding.

**Tests**: Extend `test_source_monitor.py` with:
- `isinstance(ZendeskClient(...), SourceClient)` passes
- `isinstance(ZendeskMonitor(...), SourceMonitor)` passes
- `source_name` returns `"zendesk"` for both

### P3.5-T3: Schema migration `migrations/002_source_warehouse.sql`

All warehouse tables use `source` column — not Zendesk-specific names.

```sql
-- ═══ Source-Agnostic Warehouse Tables ═══

-- Core event log — one row per classified record from ANY source
CREATE TABLE IF NOT EXISTS source_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'zendesk',  -- 'zendesk'|'intercom'|'jira'
    ticket_id       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,       -- ISO 8601
    trc_code        TEXT    NOT NULL,       -- from configurable TRC field
    classification  TEXT    DEFAULT '',     -- from hybrid classifier
    sentiment       TEXT    DEFAULT '',     -- pos/neg/neu
    priority        TEXT    DEFAULT '',
    ticket_type     TEXT    DEFAULT '',
    tags            TEXT    DEFAULT '',     -- comma-separated
    flagged         INTEGER DEFAULT 0,
    classified_by   TEXT    DEFAULT '',     -- 'ngram' | 'llm' | 'manual'
    inserted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, ticket_id)              -- same ticket_id can exist in different sources
);
CREATE INDEX IF NOT EXISTS idx_se_source   ON source_events(source);
CREATE INDEX IF NOT EXISTS idx_se_trc      ON source_events(trc_code);
CREATE INDEX IF NOT EXISTS idx_se_created  ON source_events(created_at);
CREATE INDEX IF NOT EXISTS idx_se_class    ON source_events(classification);

-- Hourly rollups for spike detection baselines
CREATE TABLE IF NOT EXISTS source_trc_hourly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    trc_code        TEXT    NOT NULL,
    hour_bucket     TEXT    NOT NULL,      -- "2026-03-10T14:00:00"
    count           INTEGER NOT NULL DEFAULT 0,
    avg_sentiment   REAL    DEFAULT 0.0,
    UNIQUE(source, trc_code, hour_bucket)
);

-- Daily rollups for trend analysis and Guru effectiveness
CREATE TABLE IF NOT EXISTS source_trc_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    trc_code        TEXT    NOT NULL,
    day_bucket      TEXT    NOT NULL,      -- "2026-03-10"
    count           INTEGER NOT NULL DEFAULT 0,
    avg_sentiment   REAL    DEFAULT 0.0,
    UNIQUE(source, trc_code, day_bucket)
);

-- ═══ Watchlist System (Source-Agnostic) ═══

-- Watchlist rules — UI-configurable alert triggers
CREATE TABLE IF NOT EXISTS watchlist_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    rule_type           TEXT    NOT NULL,      -- 'keyword'|'entity'|'volume'|'compound'
    severity            TEXT    NOT NULL DEFAULT 'watch',  -- 'watch'|'incident'
    source_filter       TEXT    DEFAULT '',    -- ''=all sources, 'zendesk'=zendesk only
    is_system           INTEGER NOT NULL DEFAULT 0,  -- 1=prepopulated
    enabled             INTEGER NOT NULL DEFAULT 1,
    -- Keyword matching
    keywords            TEXT    DEFAULT '',    -- comma-separated match terms
    keyword_mode        TEXT    DEFAULT 'any', -- 'any'|'all'
    -- Entity matching
    entity_type         TEXT    DEFAULT '',    -- 'provider'|'payer'|'member'
    entity_filter       TEXT    DEFAULT '',    -- specific entity name, or ''=any
    -- Volume threshold
    volume_threshold    INTEGER DEFAULT 0,
    volume_window_minutes INTEGER DEFAULT 60,
    -- EWMA learning
    ewma_confidence     REAL    DEFAULT 0.5,  -- 0.0-1.0, starts neutral
    ewma_alpha          REAL    DEFAULT 0.3,  -- learning rate
    total_fires         INTEGER DEFAULT 0,
    total_confirmed     INTEGER DEFAULT 0,
    total_dismissed     INTEGER DEFAULT 0,
    -- Cooldown
    cooldown_minutes    INTEGER DEFAULT 120,
    last_fired_at       TEXT    DEFAULT '',
    -- Metadata
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Alert log — every fired alert
CREATE TABLE IF NOT EXISTS watchlist_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER NOT NULL REFERENCES watchlist_rules(id),
    source          TEXT    NOT NULL DEFAULT 'zendesk',
    severity        TEXT    NOT NULL,          -- 'watch'|'incident'
    title           TEXT    NOT NULL,
    summary         TEXT    DEFAULT '',
    ticket_count    INTEGER DEFAULT 0,
    ticket_ids      TEXT    DEFAULT '',        -- comma-separated
    trc_code        TEXT    DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'open',  -- 'open'|'confirmed'|'dismissed'|'expired'
    llm_triage      TEXT    DEFAULT '',        -- LLM analysis result
    llm_confidence  REAL    DEFAULT 0.0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT    DEFAULT '',
    resolved_by     TEXT    DEFAULT ''         -- 'user'|'auto_expire'
);
CREATE INDEX IF NOT EXISTS idx_wa_status ON watchlist_alerts(status);
CREATE INDEX IF NOT EXISTS idx_wa_rule   ON watchlist_alerts(rule_id);

-- Few-shot example bank for LLM triage (sanitized, no PHI)
CREATE TABLE IF NOT EXISTS watchlist_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER REFERENCES watchlist_rules(id),
    example_type    TEXT    NOT NULL,          -- 'confirmed_alert'|'dismissed_alert'|'seed'
    sanitized_text  TEXT    NOT NULL,          -- redacted text used as few-shot context
    outcome         TEXT    NOT NULL,          -- 'true_positive'|'false_positive'
    trc_code        TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ═══ Downstream Compatibility ═══

-- Add source column to conversations table for future Auto-Import
ALTER TABLE conversations ADD COLUMN source TEXT DEFAULT 'csv';
```

**Key design decision**: `ALTER TABLE conversations ADD COLUMN source` uses DEFAULT `'csv'` so all existing batch-imported data is automatically tagged. When Auto-Import inserts Zendesk tickets into `conversations`, it sets `source='zendesk'`. Scan orchestrator can optionally filter by source.

### P3.5-T4: `src/data/source_warehouse.py` (~250 lines)

Source-agnostic warehouse — classifies and persists metadata from ANY source.

```python
class SourceWarehouse:
    """Classifies and persists source record metadata (no PHI)."""

    def __init__(self, db_manager):
        self.db = db_manager

    def ingest_records(self, records: list[dict], source: str,
                       trc_field: str, client: SourceClient) -> list[dict]:
        """Classify and persist a batch of records from any source.

        Args:
            records: Raw record dicts from any SourceClient
            source: Source identifier ('zendesk', 'intercom', etc.)
            trc_field: TRC field mapping string
            client: SourceClient instance for extract_trc()

        Returns enriched record dicts (with classification added).
        Does NOT store PHI — only metadata goes to source_events.
        """
        # 1. Extract TRC via client.extract_trc(record, trc_field)
        # 2. Classify via _classify_hybrid()
        # 3. INSERT INTO source_events (skip dupes via UNIQUE(source, ticket_id))
        # 4. Update hourly/daily rollups
        # 5. Return enriched records

    def _classify_hybrid(self, record: dict, trc: str,
                         source: str) -> tuple[str, str]:
        """N-gram fast path → LLM slow path.

        Returns (classification, classified_by).

        Fast path: Match record tags/text against existing
        sub_pattern_ngrams table (read-only cross-reference from
        NLP scan pipeline). If confidence > 0.7 → use it.

        Slow path: Build LLM prompt with redacted record context,
        call build_client_for_model().generate(), parse classification.
        PII redaction applied before any LLM call.
        """

    def update_rollups(self, source: str, trc_code: str, created_at: str):
        """Upsert hourly and daily rollup counts for source+trc."""

    def get_trc_baseline(self, source: str, trc_code: str,
                         lookback_hours: int = 168) -> dict:
        """Hourly counts for a source+TRC. Used by watchlist volume rules."""

    def get_daily_trend(self, source: str, trc_code: str,
                        lookback_days: int = 30) -> list[dict]:
        """Daily counts for trend charts and Guru effectiveness."""

    def rollup_maintenance(self):
        """Prune hourly rollups > 7 days. Auto-expire open alerts > 24h."""
```

**Accepts any SourceClient** — the `client` parameter is used only for `extract_trc()`. The warehouse doesn't know or care whether the data came from Zendesk, Intercom, or Jira.

### P3.5-T5: `src/data/watchlist_engine.py` (~350 lines)

Source-agnostic watchlist rule engine + EWMA learning loop.

```python
class WatchlistEngine:
    """Evaluates watchlist rules against incoming records from any source."""

    SYSTEM_RULES = [
        {"name": "Site Outage Detection", "rule_type": "compound",
         "severity": "incident",
         "keywords": "site down,can't access,error 500,503,page not loading,outage,service unavailable",
         "keyword_mode": "any", "volume_threshold": 5, "volume_window_minutes": 15},

        {"name": "Payment Processing Failure", "rule_type": "keyword",
         "severity": "incident",
         "keywords": "payment failed,card declined,transaction error,checkout broken",
         "keyword_mode": "any"},

        {"name": "Login/Auth Issues", "rule_type": "keyword",
         "severity": "watch",
         "keywords": "can't log in,password reset,authentication,locked out,SSO",
         "keyword_mode": "any"},

        {"name": "High-Value Provider Alert", "rule_type": "compound",
         "severity": "incident", "entity_type": "provider",
         "keywords": "payout,reimbursement,claim amount,overpayment,underpayment",
         "keyword_mode": "any"},

        {"name": "Data/Privacy Concern", "rule_type": "keyword",
         "severity": "incident",
         "keywords": "data breach,exposed,leaked,unauthorized access,HIPAA",
         "keyword_mode": "any"},
    ]

    def __init__(self, db_manager, warehouse: SourceWarehouse):
        self.db = db_manager
        self.warehouse = warehouse
        self._ensure_system_rules()

    def _ensure_system_rules(self):
        """Insert system rules on first run (is_system=1). Idempotent."""

    def evaluate(self, records: list[dict], source: str,
                 trc_field: str) -> list[dict]:
        """Run all enabled rules against a batch of records.

        Returns list of alert dicts for any rules that fired.

        For each enabled rule (filtered by source_filter if set):
        1. Check cooldown (skip if last_fired_at + cooldown > now)
        2. Evaluate match (keyword/entity/volume/compound)
        3. EWMA confidence gate:
           - n < 5 total fires → ALWAYS proceed (cold start)
           - severity == 'incident' → ALWAYS proceed (critical rules skip gate)
           - ewma_confidence < 0.3 → skip (too many false positives)
           - ewma_confidence >= 0.3 → proceed
        4. Compound rule with entity match → LLM triage
        5. Fire alert → INSERT INTO watchlist_alerts, update rule counters
        """

    def _match_keywords(self, record: dict, rule: dict) -> bool:
        """Check if record subject/description matches rule keywords.
        Works on any record dict that has 'subject' and/or 'description'.
        """

    def _match_entity(self, record: dict, rule: dict) -> bool:
        """Check if record involves the target entity type.
        Uses existing entity extraction from config/entities/
        (payers.json, product_areas.json). Source-agnostic.
        """

    def _match_volume(self, source: str, trc_code: str, rule: dict) -> bool:
        """Check if source+TRC volume exceeds threshold in window.
        Queries source_trc_hourly via warehouse.get_trc_baseline().
        """

    def _llm_triage(self, record: dict, rule: dict) -> dict:
        """Call LLM for single-record anomaly triage.

        1. Load few-shot examples from watchlist_examples
           (up to 5 confirmed + 5 dismissed for this rule)
        2. Build prompt: rule context + few-shots + redacted record
        3. Call build_client_for_model().generate()
        4. Parse response: {should_alert: bool, confidence: float, reason: str}

        Uses existing PII redaction. Works with Gemini or Claude.
        """

    def record_feedback(self, alert_id: int, outcome: str):
        """User confirms or dismisses an alert → update EWMA.

        outcome: 'confirmed' | 'dismissed'

        EWMA update (bounded floor=0.1, ceiling=0.95):
            signal = 1.0 if confirmed else 0.0
            new = alpha * signal + (1 - alpha) * old
            rule.ewma_confidence = max(0.1, min(0.95, new))

        If confirmed → add sanitized text to watchlist_examples
        for future few-shot context. Cap at 5 per outcome per rule.
        """

    # ── CRUD ──

    def list_rules(self, source: str = "") -> list[dict]:
        """All rules, optionally filtered by source_filter."""

    def create_rule(self, **kwargs) -> int:
        """Create user rule (is_system=0). Returns rule ID."""

    def update_rule(self, rule_id: int, **kwargs) -> bool:
        """Update rule. Cannot change is_system flag."""

    def delete_rule(self, rule_id: int) -> bool:
        """Delete user rule. System rules cannot be deleted."""

    def toggle_rule(self, rule_id: int, enabled: bool) -> bool:
        """Enable/disable (system rules CAN be disabled)."""
```

**Source-agnostic**: Rules have an optional `source_filter` column. Empty = applies to all sources. `'zendesk'` = Zendesk only. Rules evaluate against any record dict that has `subject`/`description`/`tags` fields.

### P3.5-T6: Wire warehouse + watchlist into ZendeskMonitor

Update `zendesk_monitor.py` `_do_fetch()`:

```python
# Constructor gains optional warehouse + watchlist params:
def __init__(self, db_manager, warehouse=None, watchlist=None, parent=None):

# In _do_fetch(), after existing fetch + dedup logic:
if new_tickets:
    # ── Persist to warehouse (cold tier, source-agnostic) ──
    if self._warehouse:
        enriched = self._warehouse.ingest_records(
            new_tickets, source=self.source_name,
            trc_field=trc_field, client=client
        )

    # ── Evaluate watchlist rules (source-agnostic) ──
    if self._watchlist:
        alerts = self._watchlist.evaluate(
            new_tickets, source=self.source_name,
            trc_field=trc_field
        )
        for alert in alerts:
            self.alert_fired.emit(alert)

    # ... existing hot-tier TRC spike detection (unchanged) ...
```

New signal: `alert_fired = Signal(dict)` (already defined in `SourceMonitor` base class).

### P3.5-T7: Source Monitor Page — Alerts tab + Watchlist tab

Add two new tabs to `source_monitor_page.py` (5 tabs total):

**Tab 1 — Live Feed** (existing, unchanged)

**Tab 2 — Alerts** (NEW):
- Alert cards with severity badge (WATCH=amber, INCIDENT=red)
- Card content: title, summary, ticket count, timestamp, source badge
- Confirm / Dismiss buttons → calls `WatchlistEngine.record_feedback()`
- Confirmed = green check overlay; Dismissed = strikethrough
- Auto-expire open alerts after 24h (status='expired')
- Alert count badge on tab header
- Filter: severity dropdown, source dropdown

**Tab 3 — TRC Spikes** (existing, unchanged)

**Tab 4 — Connection** (existing, unchanged)

**Tab 5 — Watchlist** (NEW):
- Rules table: Name, Type, Severity, Source, Enabled toggle, EWMA confidence bar, Fires/Confirmed/Dismissed
- System rules: grey background, no delete button, CAN be toggled
- "Add Rule" button → dialog or inline form:
  - Name, Type (keyword/entity/volume/compound), Severity (watch/incident)
  - Source filter (All Sources / Zendesk / etc.)
  - Keywords (comma-separated), keyword mode (any/all)
  - Entity type (provider/payer/member), entity filter
  - Volume threshold + window minutes
  - Cooldown minutes
- Edit / Delete on user rules
- "Reset EWMA" button per rule (sets confidence back to 0.5)

### P3.5-T8: MainWindow wiring

- Initialize `SourceWarehouse(db)` and `WatchlistEngine(db, warehouse)` alongside `ZendeskMonitor`
- Pass `warehouse` and `watchlist` to `ZendeskMonitor.__init__()`
- Connect `ZendeskMonitor.alert_fired` → Source Monitor Page alerts tab
- System tray notification for INCIDENT severity alerts
- Sidebar badge: alert count (red dot for active incidents)
- Call `warehouse.rollup_maintenance()` on app start

### P3.5-T9: Downstream compatibility — `source` column on `conversations`

The migration in T3 adds `ALTER TABLE conversations ADD COLUMN source TEXT DEFAULT 'csv'`. Additional changes needed for forward compatibility:

**`src/data/csv_ingestion.py`** (~2 lines): Add `"source": "csv"` to upsert dict in `_insert_conversations()`.

**`src/data/conversation_rebuild.py`** (~2 lines): Add `"source": "lightdash"` to upsert dict.

**`src/agents/scan_orchestrator.py`** (~5 lines): Add optional `source` parameter to initial TRC distribution query. Default=None (all sources, backward compatible). When set, adds `AND source = ?` filter.

These are safety-net changes — they don't affect current behavior since all existing data gets `source='csv'` via the migration DEFAULT.

### P3.5 Tests

**`tests/test_source_types.py`** (~15 tests):
- ABC contract: ZendeskClient satisfies SourceClient interface
- ABC contract: ZendeskMonitor satisfies SourceMonitor interface
- `source_name` property returns correct string
- SourceConfig dataclass round-trip

**`tests/test_source_warehouse.py`** (~20 tests):
- `ingest_records` persists to source_events with correct source column
- No PHI in source_events (subject/description columns don't exist)
- Duplicate (source, ticket_id) is idempotent
- Hourly/daily rollups update correctly with source filter
- `_classify_hybrid` tries n-gram first, falls back to LLM
- `get_trc_baseline` returns hourly counts filtered by source
- `get_daily_trend` returns daily counts filtered by source
- `rollup_maintenance` prunes old hourly data, expires old alerts

**`tests/test_watchlist_engine.py`** (~25 tests):
- System rules auto-created on first init (5 rules)
- Keyword matching: any mode, all mode
- Entity matching via entity_extractor patterns
- Volume threshold against hourly rollups
- Compound rule requires keyword + entity match
- source_filter respected (rule with source_filter='zendesk' skips intercom records)
- Cooldown prevents re-fire within window
- EWMA update: confirmed → confidence increases, dismissed → decreases
- EWMA bounds: floor 0.1, ceiling 0.95
- Cold start (n<5 fires) always proceeds past EWMA gate
- Incident severity always proceeds (bypasses EWMA gate)
- Low confidence (< 0.3) skips rule evaluation
- LLM triage builds few-shot prompt from watchlist_examples
- Few-shot cap: max 5 confirmed + 5 dismissed per rule
- CRUD: create/update/delete user rules
- System rules can't be deleted but CAN be disabled
- Alert lifecycle: open → confirmed/dismissed/expired

---

## Phase 4 — Guru Integration

**Why**: Close the feedback loop. Zendesk tickets reveal friction points; Guru articles should address those friction points. Phase 4 connects the two: analyze Guru content against ticket friction, propose improvements, draft new content for uncovered friction, and measure whether updated Guru articles actually reduce ticket volume.

**Universal join key**: `friction_type` (derived from `sub_patterns.pattern_name` in the NLP scan pipeline). Links: tickets → classifications → sub_patterns → guru_friction_coverage → guru_articles.

**Source-agnostic by design**: Guru effectiveness tracking queries `source_trc_daily` (built in P3.5) which already has a `source` column. If a Guru article reduces friction, we can measure that across ALL sources, not just Zendesk.

### P4-T1: `src/data/guru_client.py` (~150 lines)

```python
class GuruClient:
    """Guru Knowledge Base API v1 client."""

    BASE = "https://api.getguru.com/api/v1"

    def __init__(self, email: str, api_token: str):
        # Auth: Basic base64(email:api_token)

    def test_connection(self) -> bool:
        """Verify credentials via /members/me."""

    def list_collections(self) -> list[dict]:
        """List all Guru collections (folders)."""

    def list_cards(self, collection_id: str = None) -> list[dict]:
        """List cards, optionally filtered by collection."""

    def get_card(self, card_id: str) -> dict:
        """Fetch a single card with full content."""

    def search_cards(self, query: str) -> list[dict]:
        """Search cards by text query."""

    def update_card(self, card_id: str, content: str,
                    title: str = None) -> dict:
        """Update card content. HUMAN-GATED ONLY.
        Never called without explicit user approval in UI.
        """

    # ── Credential persistence ──
    @staticmethod
    def load_credentials() -> tuple[str, str]:
        """Load (email, api_token) from pat_store."""

    @staticmethod
    def save_credentials(email: str, api_token: str) -> bool:
        """Persist Guru credentials."""
```

### P4-T2: Schema migration `migrations/003_guru_tables.sql`

```sql
-- Guru article cache (content synced from Guru API)
CREATE TABLE IF NOT EXISTS guru_articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         TEXT    NOT NULL UNIQUE,
    collection_id   TEXT    DEFAULT '',
    collection_name TEXT    DEFAULT '',
    title           TEXT    NOT NULL,
    content_hash    TEXT    DEFAULT '',    -- SHA-256 for change detection
    last_synced_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    friction_score  REAL    DEFAULT 0.0,  -- computed by friction pipeline
    status          TEXT    DEFAULT 'active'  -- 'active'|'archived'
);

-- Maps friction types (from sub_patterns) to Guru articles
CREATE TABLE IF NOT EXISTS guru_friction_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    friction_type   TEXT    NOT NULL,      -- from sub_patterns.pattern_name
    card_id         TEXT    NOT NULL REFERENCES guru_articles(card_id),
    coverage_score  REAL    DEFAULT 0.0,  -- 0.0-1.0
    gap_description TEXT    DEFAULT '',   -- what's missing
    analyzed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    scan_id         TEXT    DEFAULT '',
    UNIQUE(friction_type, card_id)
);
CREATE INDEX IF NOT EXISTS idx_gfc_friction ON guru_friction_coverage(friction_type);

-- Effectiveness tracking: does updating Guru reduce ticket volume?
CREATE TABLE IF NOT EXISTS guru_effectiveness (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id          TEXT    NOT NULL REFERENCES guru_articles(card_id),
    friction_type    TEXT    NOT NULL,
    measurement_date TEXT    NOT NULL,
    source           TEXT    DEFAULT '',   -- which source(s) measured against
    pre_volume       REAL    DEFAULT 0.0,  -- avg daily tickets before change
    post_volume      REAL    DEFAULT 0.0,  -- avg daily tickets after change
    pre_window_days  INTEGER DEFAULT 14,
    post_window_days INTEGER DEFAULT 14,
    delta_pct        REAL    DEFAULT 0.0,
    is_significant   INTEGER DEFAULT 0,   -- Poisson test p < 0.05
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- LLM-generated content drafts
CREATE TABLE IF NOT EXISTS guru_content_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         TEXT    DEFAULT '',    -- empty for new article proposals
    friction_type   TEXT    NOT NULL,
    draft_type      TEXT    NOT NULL,      -- 'rewrite'|'new_article'|'supplement'
    title           TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    source_tickets  TEXT    DEFAULT '',    -- comma-separated ticket IDs
    status          TEXT    NOT NULL DEFAULT 'pending',  -- 'pending'|'approved'|'pushed'|'rejected'
    approved_by     TEXT    DEFAULT '',
    pushed_at       TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gcd_status ON guru_content_drafts(status);
```

### P4-T3: `src/data/guru_friction_pipeline.py` (~250 lines)

**Loop A** — Read-only friction analysis.

```python
class GuruFrictionPipeline:
    """Analyzes Guru articles for friction coverage gaps."""

    def __init__(self, db_manager, guru_client: GuruClient):
        self.db = db_manager
        self.client = guru_client

    def sync_articles(self) -> int:
        """Pull all Guru cards → guru_articles table.
        Uses content_hash (SHA-256) for change detection.
        Returns count of new/updated articles.
        """

    def analyze_coverage(self, scan_id: str = "") -> list[dict]:
        """Compare friction types (from sub_patterns) against Guru articles.

        1. Get active friction_types from sub_patterns
        2. For each, search guru_articles for related cards
        3. LLM scores coverage (0.0-1.0) + identifies gaps
        4. Upsert into guru_friction_coverage
        5. Return results sorted by coverage_score ASC (worst gaps first)
        """

    def get_gap_report(self) -> list[dict]:
        """LEFT JOIN sub_patterns against guru_friction_coverage.
        Uncovered friction → gap_score = 1.0.
        Low coverage → gap_score = 1 - coverage_score.
        """

    def compute_friction_scores(self):
        """Update guru_articles.friction_score from coverage analysis.
        Higher score = more friction (worse article quality).
        """
```

### P4-T4: `src/data/guru_content_pipeline.py` (~200 lines)

**Loop B** — LLM content generation (write-gated).

```python
class GuruContentPipeline:
    """Generates content drafts for Guru — rewrites and new articles."""

    def __init__(self, db_manager, guru_client: GuruClient):
        self.db = db_manager
        self.client = guru_client

    def propose_rewrite(self, card_id: str, friction_types: list[str]) -> dict:
        """Generate proposed rewrite for existing Guru card.
        LLM prompt: current content + coverage gaps + sample tickets (redacted).
        Saves to guru_content_drafts (status='pending', draft_type='rewrite').
        """

    def propose_new_article(self, friction_type: str) -> dict:
        """Generate new article for uncovered friction.
        LLM prompt: friction description + sample tickets + existing article style.
        Saves to guru_content_drafts (status='pending', draft_type='new_article').
        """

    def approve_and_push(self, draft_id: int) -> bool:
        """Push approved draft to Guru. ONLY after explicit user approval.
        Rewrites → GuruClient.update_card().
        New articles → copy to clipboard (create_card not implemented yet).
        Records effectiveness baseline on push.
        """

    def reject(self, draft_id: int) -> bool:
        """Mark draft as rejected without API call."""

    def get_pending_drafts(self) -> list[dict]:
        """All drafts with status='pending', most recent first."""
```

### P4-T5: `src/data/guru_effectiveness.py` (~150 lines)

Measures whether Guru changes reduce ticket volume. **Source-agnostic** — queries `source_trc_daily` which has data from all sources.

```python
class GuruEffectivenessTracker:
    """Measures whether Guru changes reduce ticket volume."""

    def __init__(self, db_manager):
        self.db = db_manager

    def record_baseline(self, card_id: str, friction_type: str,
                        pre_window_days: int = 14):
        """Record pre-change volume baseline when a draft is pushed.
        Queries source_trc_daily (all sources) for avg daily count.
        """

    def measure_effectiveness(self, days_since_change: int = 14) -> list[dict]:
        """Measure post-change volume for all tracked changes.
        Computes delta_pct, runs Poisson significance test.
        Reuses _poisson_cdf from incident_engine.py.
        """

    def get_effectiveness_report(self) -> list[dict]:
        """Summary: card_title, friction_type, pre/post volume, delta, significant."""
```

### P4-T6: `src/ui/pages/guru_page.py` (~600 lines)

Extends `AnalysisPageBase`. Five tabs:

**Tab 1 — Cards**: Left panel (card list with friction score progress bar, collection filter, search) + Right panel (card detail, "Analyze Friction" / "Propose Rewrite" buttons).

**Tab 2 — Gap Analysis**: Friction types sorted by gap score. Columns: Friction Type, Ticket Volume (7d), Coverage Score, Covering Article(s), Gap Description. Uncovered friction highlighted red. "Draft New Article" button.

**Tab 3 — Drafts & Diff**: Two-column diff view (CURRENT red vs PROPOSED green). Warning bar. Approve & Push / Reject / Edit Before Pushing buttons. Pending drafts sidebar.

**Tab 4 — Effectiveness**: Pushed changes with pre/post volume, delta %, significance. Before/after bar chart. 14-day minimum post window.

**Tab 5 — Connection**: Guru email + API token, Test Connection, Sync Articles with progress, last sync timestamp.

### P4-T7: Wire into MainWindow

- Add `PAGE_GURU = 9`
- Sidebar button in SOURCES section with "NEW" badge
- Initialize `GuruClient`, `GuruFrictionPipeline`, `GuruContentPipeline`, `GuruEffectivenessTracker`
- Guard: if `sub_patterns` table empty → show "Run NLP Scan first" in Gap Analysis
- Periodic effectiveness measurement on app start

### P4 Tests

**`tests/test_guru_client.py`** (~12 tests):
- Auth header format (Basic base64)
- list_cards, get_card, search_cards (mock HTTP)
- update_card sends correct payload
- test_connection returns bool
- Credential persistence round-trip

**`tests/test_guru_pipeline.py`** (~20 tests):
- sync_articles detects content changes via hash
- analyze_coverage scores articles against friction types
- get_gap_report identifies uncovered friction types
- propose_rewrite generates draft with correct status
- propose_new_article for uncovered friction
- approve_and_push calls GuruClient.update_card
- reject updates status without API call
- double-approve is idempotent
- effectiveness baseline recorded on push
- effectiveness measurement queries source_trc_daily (source-agnostic)
- Poisson significance test matches incident_engine behavior

---

## Phase 5 — Auto-Updater GitHub Wiring

### P5-T1: `scripts/make_release.py` — release packaging

Package `src/` + `config/` into versioned zip with SHA256SUMS.

### P5-T2: Settings page — Updates tab content

Fill in the Updates tab (stubbed in P1-T4):
- Version display + last-checked timestamp
- "Check for Updates" button → UpdateChecker in worker thread
- Update available banner with "Install Now"
- Progress bar during download/install
- "Restart now" button on completion

---

## New Files Inventory

```
src/
  llm/
    __init__.py                    <- P1 ✅
    model_registry.py              <- P1 ✅
    claude_client.py               <- P1 ✅
  data/
    settings_manager.py            <- P0 ✅
    zendesk_client.py              <- P3 ✅ (P3.5: add SourceClient inheritance)
    zendesk_monitor.py             <- P3 ✅ (P3.5: add SourceMonitor inheritance)
    source_types.py                <- P3.5-T1 (ABC interfaces)
    source_warehouse.py            <- P3.5-T4
    watchlist_engine.py            <- P3.5-T5
    guru_client.py                 <- P4-T1
    guru_friction_pipeline.py      <- P4-T3
    guru_content_pipeline.py       <- P4-T4
    guru_effectiveness.py          <- P4-T5
  updater/
    __init__.py                    <- P2 ✅
    update_checker.py              <- P2 ✅
    updater.py                     <- P2 ✅
    schema_migrator.py             <- P2 ✅
  ui/pages/
    source_monitor_page.py         <- P3 ✅ (P3.5: add Alerts + Watchlist tabs)
    guru_page.py                   <- P4-T6
  __init__.py                      <- P2 ✅ (VERSION)

migrations/
  001_initial_baseline.sql         <- P2 ✅
  002_source_warehouse.sql         <- P3.5-T3
  003_guru_tables.sql              <- P4-T2

scripts/
  make_release.py                  <- P5-T1

tests/
  test_settings_manager.py         <- P0 ✅ (16 tests)
  test_model_registry.py           <- P1 ✅ (18 tests)
  test_claude_client.py            <- P1 ✅ (12 tests)
  test_client_factory.py           <- P1 ✅ (extended)
  test_settings_ai_provider.py     <- P1 ✅
  test_updater.py                  <- P2 ✅ (30 tests)
  test_source_monitor.py           <- P3 ✅ (65 tests, extended P3.5)
  test_source_types.py             <- P3.5 (~15 tests)
  test_source_warehouse.py         <- P3.5 (~20 tests)
  test_watchlist_engine.py         <- P3.5 (~25 tests)
  test_guru_client.py              <- P4 (~12 tests)
  test_guru_pipeline.py            <- P4 (~20 tests)
```

## Modified Files

```
src/data/zendesk_client.py         <- P3.5-T2 (add SourceClient inheritance)
src/data/zendesk_monitor.py        <- P3.5-T2, T6 (SourceMonitor + warehouse/watchlist wiring)
src/data/csv_ingestion.py          <- P3.5-T9 (add source='csv' to upsert, ~2 lines)
src/data/conversation_rebuild.py   <- P3.5-T9 (add source='lightdash', ~2 lines)
src/data/db_manager.py             <- P3.5-T3 (migration creates new tables + conversations source column)
src/agents/scan_orchestrator.py    <- P3.5-T9 (optional source filter param, ~5 lines)
src/ui/pages/source_monitor_page.py <- P3.5-T7 (add Alerts + Watchlist tabs)
src/ui/main_window.py              <- P3.5-T8, P4-T7 (warehouse/watchlist init, Guru page)
```

---

## Risk Assessment

### Phase 3.5 — Source Abstraction + Warehouse + Watchlist (MEDIUM-HIGH)

| Risk | Impact | Mitigation |
|------|--------|------------|
| ABC inheritance breaks existing ZendeskClient/Monitor | Source Monitor page stops working | ABCs match existing method signatures exactly; inheritance is additive |
| N-gram classifier insufficient for novel patterns | Tickets classified as "unknown" | LLM slow path catches what n-grams miss; EWMA learning improves over time |
| LLM triage API cost | Budget overrun | EWMA confidence gate; cold start always calls but n<5 is cheap; batch mode |
| False positive alert fatigue | Users stop checking alerts | Tiered severity, cooldowns, EWMA auto-degrades noisy rules, bounded floor/ceiling |
| PHI leaking into cold tier | Compliance violation | Explicit column whitelist in `ingest_records()` — subject/description NEVER stored |
| SQLite write contention (warehouse + monitor) | UI freezes | Short-lived connections + WAL mode + write serialization |
| EWMA divergence | Rule stuck at extremes | Floor 0.1, ceiling 0.95; incident rules bypass EWMA |
| Hourly rollup gaps (app closed) | Anomaly baselines have holes | Backfill from source_events on app start |
| `ALTER TABLE conversations ADD COLUMN` on large DB | Slow migration | SQLite ALTER TABLE ADD COLUMN is O(1) — metadata only, no table rewrite |
| source_filter on watchlist_rules vs no intercom yet | Rules created for nonexistent sources | UI only shows configured sources in dropdown; empty = all sources |

### Phase 4 — Guru Integration (MEDIUM)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Accidental `update_card()` | Live Guru card corrupted | Confirmation dialog + diff view mandatory |
| LLM hallucination in drafts | Bad content pushed | Diff view required; user reviews every character |
| friction_type join key mismatch | Wrong Guru analysis | friction_type derived from sub_patterns.pattern_name, consistent across pipeline |
| Sub-patterns not yet populated | Gap analysis empty | Guard: show "Run NLP Scan first" message |
| Guru API rate limits | Sync fails mid-batch | Paginated sync + retry + rate limit handling |
| Effectiveness measurement too early | Incorrect delta | Minimum 14-day post window; significance test gates measurement |

### Phase 5 — GitHub Wiring (LOW)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Private repo needs auth token | Check fails silently | Handle 403 gracefully |

---

## Phase Gate Protocol

Each phase ends with a **mandatory checkpoint**:

1. **Run full regression**: `python -m pytest tests/ -v`
2. **Run post-phase audit**: grep for missed references or patterns
3. **Summarize results**: test count (old + new), any failures, files changed
4. **PAUSE for review** before starting next phase
5. **Update artifacts**: MEMORY.md, audit files, CLAUDE.md

### Phase Gate Summary Template
```
=== PHASE N COMPLETE ===
Tests: X/X passing (Y new, Z existing)
Files created: [list]
Files modified: [list]
Risks encountered: [any issues hit]
Ready for Phase N+1: [yes/no + blockers]
```

---

## Verification

### Phase 3.5 Acceptance Criteria
- [ ] `isinstance(ZendeskClient(...), SourceClient)` passes
- [ ] `isinstance(ZendeskMonitor(...), SourceMonitor)` passes
- [ ] Records persist to source_events with correct `source` column
- [ ] No PHI in source_events (no subject/description columns in table)
- [ ] UNIQUE(source, ticket_id) prevents cross-source collisions
- [ ] Hourly/daily rollups filtered by source
- [ ] N-gram classifier matches against existing sub_patterns
- [ ] LLM fallback classifies tickets that n-grams miss
- [ ] System watchlist rules auto-created on first launch (5 rules)
- [ ] Keyword rule fires alert on matching text
- [ ] EWMA confidence updates on confirm/dismiss (bounded 0.1-0.95)
- [ ] Cooldown prevents re-fire within window
- [ ] Incident severity always calls LLM triage (bypasses EWMA)
- [ ] Cold start (n<5) always proceeds
- [ ] Alerts tab shows cards with Confirm/Dismiss buttons
- [ ] Watchlist tab shows rules with CRUD (system rules can't be deleted)
- [ ] `conversations` table has `source` column (existing data = 'csv')
- [ ] Scan orchestrator backward compatible (source=None = all sources)

### Phase 4 Acceptance Criteria
- [ ] Guru API connection test succeeds
- [ ] Articles sync with content_hash change detection
- [ ] Gap analysis identifies uncovered friction types
- [ ] Rewrite proposals generate diff view (CURRENT vs PROPOSED)
- [ ] New article drafts generated for uncovered friction
- [ ] Approve pushes to Guru API (with confirmation dialog)
- [ ] Reject marks draft without API call
- [ ] Effectiveness baseline recorded on push
- [ ] Post-change measurement queries source_trc_daily (all sources)
- [ ] Poisson significance test matches incident_engine behavior
- [ ] friction_type correctly joins sub_patterns → guru_friction_coverage

### Data Flow Diagram
```
                        ┌─────────────────────────────────┐
                        │   SOURCE ABSTRACTION LAYER      │
                        │   SourceClient (ABC)             │
                        │   SourceMonitor (ABC)            │
                        └────────┬────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
        ZendeskClient     (IntercomClient)    (JiraClient)
        ZendeskMonitor    (IntercomMonitor)   (JiraMonitor)
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
              HOT TIER (memory)         COLD TIER (SQLite)
              Full records + PHI        source_events (no PHI)
              Live Feed UI cards        source_trc_hourly/daily
                    │                         │
                    │                    ┌────┴────┐
                    │                    │         │
                    │            SourceWarehouse  WatchlistEngine
                    │            _classify_hybrid  evaluate()
                    │            (reads sub_patterns) _llm_triage()
                    │                    │         │
                    │                    │    ┌────┴────┐
                    │                    │    │         │
                    │                    │  Alerts    EWMA
                    │                    │  Tab       Learning
                    │                    │
                    │                    ├──→ source_trc_daily
                    │                    │         │
                    │                    │    GuruEffectiveness
                    │                    │    (pre/post volume)
                    │                    │
                    │              ┌─────┴──────────────┐
                    │              │                     │
                    │     GuruFrictionPipeline   GuruContentPipeline
                    │     analyze_coverage()     propose_rewrite()
                    │     (reads sub_patterns)   propose_new_article()
                    │              │              approve_and_push()
                    │              │                     │
                    │     guru_friction_coverage  guru_content_drafts
                    │              │                     │
                    │              └─────────┬───────────┘
                    │                        │
              ┌─────┴────────────────────────┴────────┐
              │         EXISTING NLP PIPELINE          │
              │  conversations → ScanOrchestrator      │
              │  → WorkerAgent → ToolRegistry          │
              │  → nlp_ticket_classifications          │
              │  → sub_patterns (shared read target)   │
              │  → incident_engine, theta_engine        │
              │  → trending_engine                     │
              └────────────────────────────────────────┘
```

### Adding a New Source (Checklist)
When adding source X (e.g., Intercom), create:
1. `src/data/x_client.py` — implements `SourceClient` ABC (~200 lines)
2. `src/data/x_monitor.py` — extends `SourceMonitor` base (~220 lines)
3. Connection UI section in Source Monitor Connection tab or Settings Integrations tab
4. That's it. Warehouse, watchlist, Guru effectiveness, and all analysis pages work automatically.
