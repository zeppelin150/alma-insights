# Source Monitor — Architecture & Algorithm Reference

**Last updated**: 2026-05-07 (rate-chart redesign)
**Owner**: ops engineering
**Code**: `src/ui/pages/source_monitor/`, `src/data/source_baseline.py`, `src/data/source_warehouse.py`, `src/data/watchlist_engine.py`

This document is the reference for everything the Source Monitor page does. If you're touching the rate chart, watchlist rules, or alerts UI, read this first.

---

## 1. Overview

The Source Monitor is a real-time view onto live ticket streams from Zendesk (and, eventually, Intercom, Jira, …). It serves three purposes:

1. **Show arrival-rate trends** so an ops lead can see at a glance whether ticket volume is unusual.
2. **Fire alerts** when configurable rules detect outages, abuse patterns, or topic spikes.
3. **Learn from feedback** — the watchlist EWMA loop demotes rules that fire on false positives.

Critically, this page never persists ticket bodies or PHI. Live ticket dicts move through the page in memory only; only **classified metadata** (TRC code, severity, classification, timestamps) goes to disk.

---

## 2. Data Flow

### Two-tier model (Phase 3.5)

```
┌─────────────────────────────────────────────────────────────┐
│  HOT TIER (in-memory)                                       │
│  ZendeskMonitor → records_received → SourceMonitorPage      │
│  PHI-bearing dicts (subject, description, requester)        │
│  Lifetime: until next QObject GC                            │
└──────────────────────────────┬──────────────────────────────┘
                               │ classify (n-gram → LLM)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  COLD TIER (SQLite, no PHI)                                 │
│  source_events  ──┐                                         │
│                   ├──> source_trc_hourly  (rollup)          │
│                   ├──> source_trc_daily   (rollup)          │
│                   └──> watchlist_alerts   (rule fires)      │
└─────────────────────────────────────────────────────────────┘
                               │ read
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  UI RENDER                                                  │
│  RateTab           ← compute_rate_baseline()                │
│  AlertsTab         ← WatchlistEngine.get_all_alerts()       │
│  WatchlistTab      ← WatchlistEngine.list_rules()           │
└─────────────────────────────────────────────────────────────┘
```

### End-to-end sequence

```
Zendesk API → ZendeskClient.fetch_incremental()
            → ZendeskMonitor._do_fetch()
              ├─→ records_received signal       (hot, page UI)
              ├─→ SourceWarehouse.ingest_records()
              │     ├─→ source_events           (cold, no PHI)
              │     └─→ source_trc_hourly +     (cold, rollups)
              │         source_trc_daily
              └─→ WatchlistEngine.evaluate(records)
                    └─→ watchlist_alerts        (cold, fired alerts)
```

---

## 3. Rate-per-hour Chart

The Live Feed tab shows a control-chart-style view of ticket arrival rate. Replaces the old ticket-card scroll and the separate TRC Spikes tab.

### 3.1 Algorithm

For each foreground bucket B at hour-of-day h on date D:

```
baseline_samples = counts in source_trc_hourly for the same (source,
                   trc_code, hour-of-day h) on dates {D-7 … D-1}
baseline_mean    = mean(baseline_samples)
baseline_std     = stdev(baseline_samples)  with Bessel correction,
                   floored at 1.0 to prevent zero-width bands
upper_band       = baseline_mean + 2 * baseline_std
is_spike         = rate[B] > upper_band[B]
```

The 2σ multiplier is a flat 2.0; we deliberately don't tune it per TRC because the user feedback loop on alerts (Confirm/Dismiss) provides a coarser-grained but more robust learning signal.

### 3.2 Cold start

When fewer than 5 distinct days of history exist for the (source, trc) pair, the algorithm switches to a permissive flat band:

```
upper_band = max(rate_so_far) * 1.5      # for all foreground buckets
```

This deliberately under-fires — better to miss day-2 spikes than to fire spurious alerts before the baseline has settled.

The UI surfaces this state with a "Building baseline (day N of 5)" badge.

### 3.3 Settings

| Key | Default | Range | Purpose |
|---|---|---|---|
| `source_monitor.rate_chart.window_hours` | 24 | 24 \| 48 | Foreground window length (user toggle in tab header) |
| `source_monitor.rate_chart.baseline_days` | 7 | 7 (fixed) | Trailing window for baseline statistics. Reserved for future tuning |
| `source_monitor.rate_chart.default_view` | "aggregate" | "aggregate" \| "per_trc" | Initial selection in the TRC dropdown |

### 3.4 Why per-hour-of-day, not flat baseline?

Ticket volume has strong diurnal structure: Monday 9 AM ≫ Sunday 3 AM. A flat 7-day baseline would treat 9 AM Monday as suspicious every week.

By comparing 9 AM today against 9 AM on the previous 7 days, we control for time-of-day automatically. This is the same trick traditional process-control charts use when production schedules vary across the day.

### 3.5 Source code

- `src/data/source_baseline.py` — `compute_rate_baseline()` (pure function, no Qt)
- `src/ui/widgets/rate_chart.py` — `RateChartWidget` (QPainter)
- `src/ui/pages/source_monitor/rate_tab.py` — Tab wrapper, debouncing, TRC dropdown, 24/48 toggle

---

## 4. Watchlist Rules

The watchlist is a configurable rule engine that fires alerts on incoming ticket streams.

### 4.1 Rule types

| Type | Matches when | Fields used |
|---|---|---|
| `keyword` | ticket subject/description contains any/all of keywords | `keywords`, `keyword_mode` |
| `entity` | ticket has a specific entity type (provider, payer, member) | `entity_type`, `entity_filter` |
| `volume` | TRC count in the last `volume_window_minutes` exceeds threshold | `volume_threshold`, `volume_window_minutes` |
| `compound` | keyword AND entity AND optional volume — all must match | combination of the above |

### 4.2 Severity gating

- `incident`: high-priority alerts. Fire even when `ewma_confidence < 0.3` (cold-start floor).
- `watch`: lower-priority alerts. Skipped when `ewma_confidence < 0.3`.

### 4.3 EWMA learning loop

Each rule has a confidence score in [0.1, 0.95] that updates from user feedback:

```
α = ewma_alpha  (default 0.3)

# Confirm:
new_confidence = α * 1.0 + (1 - α) * old_confidence

# Dismiss:
new_confidence = α * 0.0 + (1 - α) * old_confidence
```

Bounds: confidence floor of 0.1 (always allow the rule a chance to recover); ceiling of 0.95 (don't let confirmed rules become ungated).

Cold start: rules with `total_fires < 5` always fire, regardless of confidence — we need data points to learn from.

### 4.4 Cooldown

A rule won't re-fire within `cooldown_minutes` (default 120) of its `last_fired_at`. Prevents alert spam during ongoing incidents.

### 4.5 System rules

5 hardcoded rules are auto-inserted on first run. They have `is_system=1` and cannot be deleted — but can be toggled on/off:

- **Site Outage Detection** — compound: keywords + volume threshold
- **Payment Processing Failure** — keyword
- **Login/Auth Issues** — keyword
- **High-Value Provider Alert** — compound: entity + keyword
- **Data/Privacy Concern** — keyword

See `src/data/watchlist_engine.py` for keyword lists.

### 4.6 LLM triage

When a rule fires with severity=`incident`, the engine calls `build_client_for_task("watchlist_triage")` (Claude) with a redacted summary of the matching tickets, and stores the result in `watchlist_alerts.llm_triage`. The Alerts tab displays this as a brief context blurb on the alert card.

LLM triage is **optional** — if the call fails or no client is configured, the alert still fires with empty triage text.

---

## 5. UI Tabs

### 5.1 Live Feed (formerly the ticket-card scroll)

Renders a `RateChartWidget` showing the current rate vs. trailing baseline. Header controls:

- **TRC dropdown** — aggregate or specific TRC. Drives the `trc_code` parameter to `compute_rate_baseline()`.
- **24h / 48h toggle** — drives `window_hours`.
- **Spike sidebar** — small list of currently-spiking buckets (timestamp + count).

The chart auto-refreshes when `monitor.records_received` fires, debounced to once every 2 seconds.

### 5.2 Alerts

List of fired alerts (open and recently-resolved). Each card shows:

- Severity badge (red incident / amber watch)
- Title (rule name)
- Triage summary (from LLM, if available)
- TRC code + ticket count
- Confirm / Dismiss buttons (writes to `WatchlistEngine.record_feedback()`)

Filter dropdown: All / Incident / Watch / Open only.

### 5.3 Watchlist

Card-based rule manager. Cards grouped under collapsible "System Rules" and "Custom Rules" headers. Each card shows:

- Severity badge + rule name
- Type, source filter, and matching parameters preview
- EWMA confidence bar (color-tiered: green ≥0.7 / amber ≥0.3 / red <0.3) and number
- Fires / Confirmed / Dismissed counts
- Action buttons: ON/OFF toggle, Edit, Delete (custom rules only)

`+ Add Rule` button opens `RuleDialog` (`src/ui/pages/source_monitor/rule_dialog.py`).

### 5.4 Connection

Zendesk credentials form (subdomain, email, API token, view ID, poll interval) plus the TRC field mapping subform with a "Fetch Fields" button that pulls the live custom-field list from the user's Zendesk instance.

Save action writes to `data/settings.yaml` via `settings_manager.save_settings()`. Test Connection runs `ZendeskClient.test_connection()` and shows pass/fail.

---

## 6. DB Schema Reference

All Source Monitor tables live in [`migrations/002_source_warehouse.sql`](../migrations/002_source_warehouse.sql).

### `source_events`

One row per classified record from any source. **No PHI.**

| Column | Type | Purpose |
|---|---|---|
| `id` | int PK | autoincrement |
| `source` | text | 'zendesk' \| 'intercom' \| 'jira' |
| `ticket_id` | text | source-side ID |
| `created_at` | text | ISO 8601 |
| `trc_code` | text | from configured TRC field |
| `classification` | text | from hybrid classifier |
| `sentiment`, `priority`, `ticket_type`, `tags`, `flagged` | various | metadata |
| `classified_by` | text | 'ngram' \| 'llm' \| 'manual' |
| `inserted_at` | text | server-side timestamp |

UNIQUE: `(source, ticket_id)` — same ticket can exist in different sources.

### `source_trc_hourly`

Spike-detection baselines. Pruned > 7 days by `rollup_maintenance()`.

| Column | Type | Purpose |
|---|---|---|
| `source` | text | source identifier |
| `trc_code` | text | TRC bucket |
| `hour_bucket` | text | "YYYY-MM-DDTHH:00:00" |
| `count` | int | tickets in bucket |
| `avg_sentiment` | real | reserved |

UNIQUE: `(source, trc_code, hour_bucket)`.

### `source_trc_daily`

Trend analysis + Guru effectiveness. Kept indefinitely (small).

Same shape as hourly with `day_bucket` ("YYYY-MM-DD") instead of `hour_bucket`.

### `watchlist_rules`

UI-configurable rules with EWMA confidence.

Key columns: `name`, `rule_type`, `severity`, `is_system`, `enabled`, rule-type-specific fields, `ewma_confidence`, `ewma_alpha`, `total_fires`, `total_confirmed`, `total_dismissed`, `cooldown_minutes`, `last_fired_at`.

### `watchlist_alerts`

Every fired alert.

Key columns: `rule_id` (FK), `severity`, `title`, `summary`, `ticket_count`, `ticket_ids`, `trc_code`, `status` ('open' \| 'confirmed' \| 'dismissed' \| 'expired'), `llm_triage`, `llm_confidence`.

### `watchlist_examples`

Sanitized few-shot examples for LLM triage prompts. Populated by user feedback events.

---

## 7. Adding a New Data Source

The Source Monitor is source-agnostic by design. Adding Intercom requires only:

1. **Implement `IntercomClient(SourceClient)`** in `src/data/intercom_client.py` — subclass of the abstract `SourceClient` defined in `src/data/source_types.py`. Implement `test_connection`, `fetch_incremental`, `extract_trc`, `source_name`.

2. **Implement `IntercomMonitor(SourceMonitor)`** in `src/data/intercom_monitor.py` — subclass `SourceMonitor`, emit `records_received` and `status_changed`.

3. **Add a Connection-tab subform** for Intercom credentials. Reuse `ConnectionTab` styling.

4. **Wire in `MainWindow._setup_source_monitor()`** — instantiate the new client + monitor.

The warehouse, watchlist, baseline algorithm, and rate chart all consume `SourceMonitor` records uniformly via the `source` column.

---

## 8. Settings Keys (full)

```yaml
source_monitor:
  rate_chart:
    window_hours: 24            # 24 | 48
    baseline_days: 7            # fixed (reserved)
    default_view: "aggregate"   # "aggregate" | "per_trc"

zendesk:
  subdomain: ""
  email: ""
  api_key: ""                   # stored in PAT store, not yaml
  view_id: ""
  poll_interval: 120
  trc_field: "subject"          # custom_field id or "subject" | "description"

watchlist:
  llm_triage_enabled: true      # disable to skip Claude triage on incident alerts

guru:
  experimental_ui_enabled: false  # gate the legacy Guru workbench
```

---

## 9. Operational Notes

- **Polling cadence**: default 120 s. Faster polling (30 s) is supported but Zendesk API rate limits will start to bite below 60 s.
- **Backpressure**: if the watchlist evaluation falls behind incoming records (rare), `WatchlistEngine.evaluate()` may drop records to a debug log. The rate chart is unaffected — it always reads the rollup.
- **Time zone**: all hour buckets are UTC. The UI displays hours in the user's local TZ via Qt's locale. The bucket math itself never converts.
- **Pruning**: `SourceWarehouse.rollup_maintenance()` runs nightly via a `QTimer` in `MainWindow`. It deletes `source_trc_hourly` rows older than 7 days and expires open alerts older than 24 hours.
- **Cold-start banner**: visible until `days_with_data >= 5` for the selected (source, trc). Each TRC accrues independently.

---

## 10. References

- Phase 3.5 design: [`docs/plans/2026-03-10_feature-suite-p3.5-p4-source-abstraction.md`](plans/2026-03-10_feature-suite-p3.5-p4-source-abstraction.md)
- Rate-chart redesign plan: `~/.claude/plans/source-monitor-redesign.md`
- Migration source of truth: [`migrations/002_source_warehouse.sql`](../migrations/002_source_warehouse.sql)
- Public API reference: [`src/data/INDEX.md`](../src/data/INDEX.md)
