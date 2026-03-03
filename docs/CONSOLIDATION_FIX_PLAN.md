# Immediate Fix: Single-Fetch Consolidation for trending_engine.py

## Problem Statement

`run_full_analysis()` fetches the same conversation dataset **3-4 times** per analysis run. Each call to `_fetch_conversations()` executes `SELECT ... full_thread ... fetchall()`, materializing the entire result set into Python memory. With ~20K conversations (each ~2-5 KB of `full_thread` text), this means:

- **Fetch 1** (line 1377): `run_full_analysis` Step 3 — main fetch, shared for Steps 4-5
- **Fetch 2** (line 1439 → line 296): `compute_sentiment_trends()` — re-fetches everything
- **Fetch 3** (line 1443 → line 518): `compute_rising_terms()` — re-fetches everything
- **Fetch 4** (line 1457 → line 624): `compute_topic_clusters()` — re-fetches everything (only when `topic_method="kmeans"`, which is the non-default path)
- **Fetch 5** (line 1463): `run_full_analysis` Step 9 — cross-TRC fetch (only when `trc_filter` is set, fetches ALL TRCs)

Fetches 2 and 3 **always fire**. Fetch 4 is conditional (kmeans only). Fetch 5 is conditional (trc_filter only).

Each copy: ~20K dicts × ~3 KB avg = **~60 MB per fetch** at moderate scale. At 4 copies that's 240 MB just from conversation dicts — and that balloons with `full_thread` text, `defaultdict` TRC groupings, and intermediate VADER scoring objects held concurrently.

## The Fix: Pass Pre-Fetched Conversations

### Change 1: `compute_sentiment_trends()` — add `conversations=None` param

**Current signature (line 288):**
```python
def compute_sentiment_trends(conn, date_start, date_end, trc_filter, window_size):
```

**New signature:**
```python
def compute_sentiment_trends(conn, date_start, date_end, trc_filter, window_size,
                             conversations=None):
```

**Body change (line 296):**
```python
# BEFORE:
conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)

# AFTER:
if conversations is None:
    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
```

**Why this is safe:**
- The function only reads `full_thread`, `trc_code`, `created_at`, `csat_score` from each conversation dict
- `run_full_analysis` Step 4 already computes `conv["_sentiment"]` for every conversation — but `compute_sentiment_trends` re-computes it anyway (line 303-310). When pre-fetched conversations arrive with `_sentiment` already set, the function **overwrites it** identically (same VADER scoring). This is redundant but harmless — we keep the overwrite for now to minimize diff surface.
- When called standalone (no `conversations` param), behavior is identical to today
- Two external callers (`ab_analysis.py`, `report_builder.py`) call `run_full_analysis()`, not this function directly — zero external impact

**Risk: NONE** — additive parameter with default None, existing behavior preserved.

---

### Change 2: `compute_rising_terms()` — add `conversations=None` param

**Current signature (line 507-508):**
```python
def compute_rising_terms(conn, date_start, date_end, trc_filter, window_size,
                         conn_for_feedback=None):
```

**New signature:**
```python
def compute_rising_terms(conn, date_start, date_end, trc_filter, window_size,
                         conn_for_feedback=None, conversations=None):
```

**Body change (line 518):**
```python
# BEFORE:
conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)

# AFTER:
if conversations is None:
    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
```

**Why this is safe:**
- The function reads `full_thread` and `created_at` — both present in the pre-fetched data
- It does NOT mutate the conversation dicts (only reads `conv.get("full_thread", "")`)
- Same standalone fallback as Change 1
- Zero external callers — only called from `run_full_analysis()` and hypothesis prompt builder (line 1951, which passes its own terms, not conversations)

**Risk: NONE** — same pattern as Change 1.

---

### Change 3: `compute_topic_clusters()` — add `conversations=None` param

**Current signature (line 615):**
```python
def compute_topic_clusters(conn, date_start, date_end, trc_filter):
```

**New signature:**
```python
def compute_topic_clusters(conn, date_start, date_end, trc_filter,
                           conversations=None):
```

**Body change (line 624):**
```python
# BEFORE:
conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)

# AFTER:
if conversations is None:
    conversations = _fetch_conversations(conn, date_start, date_end, trc_filter)
```

**Why this is safe:**
- Only called from `run_full_analysis()` line 1457 when `topic_method="kmeans"` (non-default)
- Reads `full_thread`, `ticket_id`, `csat_score` — all present in pre-fetched data
- Does NOT mutate conversation dicts

**Risk: MINIMAL** — this path is rarely exercised (NMF is default) but should still be fixed for completeness.

---

### Change 4: Wire pre-fetched conversations in `run_full_analysis()`

**Step 6 (line 1439):**
```python
# BEFORE:
sentiment_result = compute_sentiment_trends(conn, date_start, date_end, trc_filter, window_size)

# AFTER:
sentiment_result = compute_sentiment_trends(
    conn, date_start, date_end, trc_filter, window_size,
    conversations=conversations,
)
```

**Step 7 (lines 1443-1446):**
```python
# BEFORE:
terms = compute_rising_terms(
    conn, date_start, date_end, trc_filter, window_size,
    conn_for_feedback=conn if db is not None else None,
)

# AFTER:
terms = compute_rising_terms(
    conn, date_start, date_end, trc_filter, window_size,
    conn_for_feedback=conn if db is not None else None,
    conversations=conversations,
)
```

**Step 8 (line 1457):**
```python
# BEFORE:
topics = compute_topic_clusters(conn, date_start, date_end, trc_filter)

# AFTER:
topics = compute_topic_clusters(conn, date_start, date_end, trc_filter,
                                conversations=conversations)
```

---

### Change 5: Strip `per_trc_series` from returned correlations

`compute_cross_trc_correlations()` returns `{"correlations": [...], "per_trc_series": {...}}`. The `per_trc_series` dict contains volume + sentiment arrays for every TRC — potentially large. The UI (`_populate_correlations()` at trending_topics.py:1157) **never reads it** — it only reads `data.get("correlations", [])`.

**In `run_full_analysis()`, after Step 10 (line 1476-1478):**
```python
# BEFORE:
correlations = compute_cross_trc_correlations(
    conn, date_start, date_end, window_size,
    per_trc_series=per_trc_series,
)

# AFTER:
correlations = compute_cross_trc_correlations(
    conn, date_start, date_end, window_size,
    per_trc_series=per_trc_series,
)
# Strip internal data not needed by UI or downstream
correlations.pop("per_trc_series", None)
```

**Risk: NONE** — no consumer reads `per_trc_series` from the returned result dict. The temporal lead-lag function (Step 11, line 1483) reads `correlations["correlations"]`, not `per_trc_series`. AI enhancement worker reads only `topics` and `terms`. The `_empty_full_result()` default already ships `"per_trc_series": {}` — we'll update that too.

---

### Change 6: Strip `full_thread` after Step 12

After compound discovery (Step 12, line 1492) — the last consumer of `full_thread` — strip it from the conversation list to release memory before the result dict is returned and cached in the UI.

**After Step 12 block (before the return statement at line 1504):**
```python
# ── Memory cleanup: full_thread no longer needed ──
for conv in conversations:
    conv.pop("full_thread", None)
if trc_filter and 'all_conversations' in dir():
    for conv in all_conversations:
        conv.pop("full_thread", None)
```

**Wait — the conversations list is NOT returned.** The return dict contains `sentiment` (aggregated averages), `terms` (term scores), `topics` (cluster labels), `correlations` (correlation coefficients), `discovery` (candidate counts). None of these contain raw `full_thread` text.

So this change is about releasing memory sooner — the GC would eventually collect the conversations list when the function returns, but if the function is being called from a QThread worker that holds a reference to intermediate locals, those can persist longer than expected.

**Risk: LOW** — defensive cleanup, no functional change.

---

## What This Does NOT Fix

1. **The Step 9 cross-TRC re-fetch** (line 1463): When `trc_filter` is set, `run_full_analysis` fetches ALL conversations a second time to compute cross-TRC correlations. This is semantically different data (unfiltered vs. filtered) and cannot be eliminated without changing Step 3 to always fetch unfiltered data — which would be a larger change. This is a Phase 2 fix.

2. **`_last_analysis_result` caching in the UI**: `trending_topics.py` stores the entire result dict at line 866. This is needed for AI smoothing application (line 1660). However, after AI enhancements complete, the result could be trimmed. This is a Phase 2 fix.

3. **500K-row scaling**: The fundamental `fetchall()` → Python memory architecture doesn't change. That requires SQL-level aggregation (Phase 3).

---

## Memory Impact Estimate

At 20K conversations, ~3 KB avg per conversation:
- **Eliminating Fetch 2+3**: Saves ~120 MB (two full copies)
- **Stripping per_trc_series from return**: Saves ~5-20 MB
- **full_thread cleanup**: Saves ~60 MB (one copy's text)
- **Total immediate savings**: ~185-200 MB

At 100K conversations, this scales linearly to ~900 MB - 1 GB saved.

---

## Testing Strategy

1. `python -m pytest tests/ -x -q` — all 274 tests must pass
2. Manual: Run full analysis from trending_topics with default settings (NMF, no TRC filter) — verify sentiment chart, rising terms, topics, correlations all populate
3. Manual: Run with TRC filter enabled — verify cross-TRC correlations still work
4. Manual: Run with kmeans topic method — verify clusters populate
5. Memory: Check Task Manager before/after — should see ~150-200 MB reduction

---

## Files Modified

| File | Lines Changed | Risk |
|------|--------------|------|
| `src/data/trending_engine.py` | ~20 lines across 6 locations | LOW — all additive params with None defaults |
| No other files | — | — |
