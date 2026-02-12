# Alma Insights — Build Spec: TRC Analytics + Trending Topics

> This is a structured specification for building two new pages in the
> Alma Insights PySide6 desktop application. Read this document fully
> before writing any code. Follow the conventions and patterns already
> established in the codebase.

---

## 0. Orientation — Read These Files First

Before implementing anything, read these files to understand existing
patterns, schema, theme, and conventions:

```
src/ui/theme.py                      — Color constants (ALMA_GREEN_DARK, etc.)
src/ui/main_window.py                — Page routing, sidebar, signal wiring
src/ui/pages/conversation_search.py  — Reference page (most complete; copy layout patterns)
src/ui/pages/placeholders.py         — Stubs you're replacing (DashboardPage, TrendingPage)
src/data/db_manager.py               — SQLite schema, upsert methods, query patterns
src/data/csv_ingestion.py            — Where resolution times are parsed but NOT stored
src/data/conversation_rebuild.py     — Where resolution times are parsed but NOT stored
requirements.txt                     — Already bundled: PySide6, pandas, scikit-learn, nltk
```

---

## 1. Prerequisites — Schema Migration

### Problem
Three resolution time fields are parsed by both ingestion paths
(csv_ingestion.py lines 209-214, conversation_rebuild.py lines 139-164)
but the `tickets` table has no columns for them. They're computed and
then silently discarded. Fix this first.

### Changes Required

**src/data/db_manager.py — `initialize()` method:**

Add three columns to the `tickets` table:

```sql
assignment_to_resolution_hours  REAL,
total_resolution_hours          REAL,
first_reply_hours               REAL
```

Add them AFTER the `custom_fields` column in the CREATE TABLE statement.

Also add an index:
```sql
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
```

**src/data/db_manager.py — `upsert_ticket()` method:**

Add the three new fields to both the INSERT column list and the VALUES
placeholder. Pull from `ticket.get("assignment_to_resolution_hours")`,
etc.

**src/data/csv_ingestion.py — ticket upsert block (~line 258):**

The dict passed to `db.upsert_ticket()` already has access to
`t["assignment_to_resolution_hours"]`, `t["total_resolution_hours"]`,
and `t["first_reply_hours"]` — they're parsed into the tickets
defaultdict but never passed to the upsert. Add them.

**src/data/conversation_rebuild.py — ticket upsert block (~line 167):**

Same situation. `assign_res`, `total_res`, and `first_reply` are
computed (lines 139-164) but never passed to `upsert_ticket()`. Add them.

### Migration for Existing DBs

Add an `_migrate()` method in db_manager.py called after `initialize()`.
Use `PRAGMA table_info(tickets)` to check if the columns exist. If not,
run `ALTER TABLE tickets ADD COLUMN ...` for each missing column. This
is safe and non-destructive — existing rows get NULL for the new columns.

---

## 2. TRC Analytics Dashboard

**Replace** `DashboardPage` in `src/ui/pages/placeholders.py` with a
full implementation in a new file: `src/ui/pages/trc_analytics.py`.

Update the import in `src/ui/main_window.py` accordingly.

### 2.1 Layout

Top-level structure (vertical):
1. Page header + subtitle (same pattern as ConversationSearchPage)
2. **Filter bar**: date range (From/To QDateEdit), TRC multi-select
   dropdown, status filter, a "Refresh" button
3. **Four metric cards** in a horizontal row (summary KPIs)
4. **Two chart panels** side by side (volume + resolution times)
5. **CSAT heatmap** (full width)
6. **Metrics table** (full width, sortable)

Use QScrollArea as the outer container so everything scrolls if the
window is small (same pattern as SettingsPage).

### 2.2 Filter Bar

- Date From / Date To: QDateEdit with calendar popup, default last 90
  days. Display format "MMM d, yyyy".
- TRC filter: QComboBox with "All TRCs" default. Populated from
  `SELECT DISTINCT trc_code FROM conversations WHERE trc_code != ''`.
- Status filter: QComboBox with "All", "Open", "Pending", "Solved",
  "Closed".
- Refresh button: Triggers full recompute of all panels below.

All charts and tables update when Refresh is clicked (not live on every
filter change — these computations are heavier than conversation search).

### 2.3 Summary KPI Cards

Four cards in a horizontal QHBoxLayout. Each card is a QFrame styled
like the settings cards (white background, rounded border, subtle shadow
via border color).

| Card | Value | Subtitle |
|------|-------|----------|
| Total Tickets | count | "in selected range" |
| Avg Resolution | hours (1 decimal) | "assignment → resolution" |
| Avg First Reply | hours (1 decimal) | "first agent response" |
| Avg CSAT | score (1 decimal) + /5 | "satisfaction score" |

If no data exists for a metric, show "—" rather than 0.

### 2.4 Volume by TRC Chart

A **horizontal bar chart** rendered with QPainter on a custom QWidget.
Each bar = one TRC code, length = ticket count. Sorted descending by
count. Color: ALMA_GREEN_LIGHT. Show count labels at the end of each
bar. Cap at top 20 TRCs if there are more (with a "and N more..." label).

Implementation: Create a reusable `BarChartWidget(QWidget)` class in
`src/ui/widgets/charts.py` that accepts a list of (label, value) tuples
and paints them. This widget will be reusable.

### 2.5 Resolution Time Distribution Chart

A **box plot** (or simplified box plot) per TRC showing the distribution
of `total_resolution_hours`. Show median line, Q1-Q3 box, and
min/max whiskers (capped at 1.5×IQR).

Implementation: Create a `BoxPlotWidget(QWidget)` class in
`src/ui/widgets/charts.py` that accepts a dict of
`{label: [values]}` and paints horizontal box plots. Use
ALMA_INFO for the box fill, ALMA_GREEN_DARK for the median line.

Cap at top 10 TRCs by volume. Only show TRCs with ≥5 tickets
(box plots are meaningless with tiny samples).

### 2.6 CSAT Heatmap

A **grid** with TRC codes on the Y axis and time periods (weeks or
months, auto-chosen based on date range) on the X axis. Cell color
= average CSAT for that TRC×period, on a red-yellow-green gradient:

- 1.0-2.0: ALMA_ERROR (red)
- 2.0-3.0: ALMA_WARNING (amber)
- 3.0-4.0: #B8A000 (yellow-ish)
- 4.0-5.0: ALMA_SUCCESS (green)
- No data: ALMA_BORDER_LIGHT (light gray)

Show the numeric value inside each cell. Implementation: custom
`HeatmapWidget(QWidget)` in `src/ui/widgets/charts.py`.

Auto-choose granularity: ≤60 days → weekly, >60 days → monthly.
Only show TRCs with ≥3 rated tickets in the range.

### 2.7 Metrics Table

A QTableWidget (not QTableView — simpler for this use case) with
sortable columns. One row per TRC code.

| Column | Computation |
|--------|------------|
| TRC Code | group key |
| Ticket Count | COUNT(*) |
| Avg Resolution (hrs) | AVG(total_resolution_hours) |
| Median Resolution (hrs) | computed in Python via pandas |
| P95 Resolution (hrs) | computed in Python via pandas |
| Avg First Reply (hrs) | AVG(first_reply_hours) |
| Avg CSAT | AVG(csat_score) WHERE csat_score IS NOT NULL |
| % Solved | COUNT(status='solved') / COUNT(*) × 100 |
| Avg Messages | AVG(message_count) |
| Agent:Customer Ratio | SUM(agent_messages) / NULLIF(SUM(client_messages), 0) |

Click column headers to sort. Format numbers to 1 decimal place.
Highlight cells: CSAT < 3.0 in light red background, resolution P95 >
48 hours in light amber background.

### 2.8 Data Layer

Create `src/data/trc_analytics.py` with a function:

```python
def compute_trc_analytics(db, date_start, date_end, trc_filter=None, status_filter=None) -> dict:
```

This queries the DB, computes all metrics using pandas, and returns a
dict with all the data the UI needs. The page calls this once on
Refresh, then distributes the results to all widgets. Keep the
computation in a worker thread (QThread) with a progress signal so the
UI doesn't freeze on large datasets.

---

## 3. Trending Topics

**Replace** `TrendingPage` in `src/ui/pages/placeholders.py` with a
full implementation in a new file: `src/ui/pages/trending_topics.py`.

Update the import in `src/ui/main_window.py` accordingly.

### 3.1 Layout

Top-level structure (vertical):
1. Page header + subtitle
2. **Filter bar**: date range, TRC filter, time window granularity
   (weekly/biweekly/monthly), "Analyze" button
3. **Sentiment trend panel** (full width)
4. **Rising terms panel** (full width)
5. **Topic clusters panel** (full width)
6. **Drill-down panel** at the bottom (shows matching tickets when you
   click a term or cluster)

### 3.2 Filter Bar

- Date From / Date To: QDateEdit, default last 90 days
- TRC filter: QComboBox, "All TRCs" or specific TRC
- Window size: QComboBox with "Weekly", "Biweekly", "Monthly"
- Analyze button: Triggers the full NLP pipeline. This is expensive, so
  it's manual (not auto-refresh). Show a progress dialog while running.

### 3.3 Sentiment Trend Panel

**What it measures:** Average sentiment polarity per time window, broken
out by TRC (or overall if "All TRCs" selected).

**Implementation:**
- Use `nltk.sentiment.vader.SentimentIntensityAnalyzer` (already bundled
  with nltk). VADER is purpose-built for short social/support text.
- For each conversation, score the `full_thread` text and extract the
  `compound` score (-1 to +1).
- Group by time window, compute mean compound score per window (and
  per TRC if filtering).
- Display as a **line chart** with time on X axis, sentiment on Y axis.
  One line per TRC (limit to top 5 TRCs by volume if "All TRCs").
  Color-code lines distinctly.

Create a `LineChartWidget(QWidget)` in `src/ui/widgets/charts.py` that
accepts `{series_label: [(x_label, y_value), ...]}` and paints a
multi-line chart with legend.

Y axis range: -1.0 to +1.0. Draw a horizontal reference line at 0
(neutral). Color the background subtly: green tint above 0, red tint
below 0.

**Below the chart**, show a summary table:
| TRC | Current Window | Previous Window | Δ Change | Trend |
|-----|---------------|-----------------|----------|-------|
| COB | -0.12 | +0.05 | -0.17 ↓ | Declining |

Use arrows and color (green ↑ / red ↓) for the trend column.

### 3.4 Rising Terms Panel

**What it measures:** Terms whose TF-IDF frequency is increasing across
time windows — i.e., language that's becoming more common in tickets.

**Implementation:**

1. **Preprocessing** (in `src/data/trending_engine.py`):
   - Take `full_thread` text from conversations in the date range
   - Strip timestamp lines and role labels (`[...] CUSTOMER:`, etc.)
     using regex: `r'\[.*?\]\s*(CUSTOMER|AGENT|BOT).*?:\n'`
   - Lowercase
   - Tokenize with `nltk.word_tokenize`
   - Remove English stopwords (`nltk.corpus.stopwords`) PLUS a custom
     domain stopword list: `{"ticket", "zendesk", "please", "thank",
     "thanks", "hi", "hello", "would", "could", "also", "like",
     "know", "need", "get", "one", "us", "see", "let"}`
   - Rejoin tokens into cleaned text per conversation

2. **Time-windowed TF-IDF** (in `src/data/trending_engine.py`):
   - Bucket conversations by time window (week/biweek/month) based on
     `created_at`
   - For each window, concatenate all cleaned text (optionally per-TRC)
     into one "document"
   - Fit `sklearn.feature_extraction.text.TfidfVectorizer` across ALL
     windows so the vocabulary is consistent
   - Extract the TF-IDF matrix: rows = windows, columns = terms

3. **Velocity scoring**:
   - For each term, compute the slope of its TF-IDF score over the last
     N windows using `numpy.polyfit(x, y, 1)` (linear regression)
   - Terms with the steepest positive slopes = "rising"
   - Terms with the steepest negative slopes = "cooling"
   - Terms with near-zero slope = "baseline" (ignore these)

4. **Display**:
   - Show top 15 rising terms and top 10 cooling terms in two columns
   - Each term row shows: the term, current TF-IDF score, velocity
     (slope), and a **sparkline** (tiny inline line chart showing the
     term's TF-IDF score across all windows)
   - Rising terms: green accent, sorted by velocity descending
   - Cooling terms: red accent, sorted by velocity ascending
   - Clicking a term populates the drill-down panel with matching tickets

For sparklines, create a small `SparklineWidget(QWidget)` in
`src/ui/widgets/charts.py` — fixed size ~120×30px, just draws a simple
polyline with QPainter.

### 3.5 Topic Clusters Panel

**What it measures:** Groups of tickets with similar language within a
TRC, revealing sub-themes that don't have their own ticket reason code.

**Implementation:**

1. **Vectorize** all conversations in the date range using
   `TfidfVectorizer` (can reuse the one from 3.4, or fit separately
   on individual conversations instead of window-concatenated text)

2. **Cluster** using `sklearn.cluster.KMeans`:
   - Auto-select K using a simple heuristic: `K = min(8, n_tickets // 20)`
     with a floor of 2 and ceiling of 8
   - If there are < 20 tickets, skip clustering and show a message
   - Fit KMeans on the TF-IDF matrix

3. **Label clusters** by extracting the top 5 terms (highest centroid
   values) from each cluster. The cluster label = those terms joined,
   e.g., "modifier, denial, timely, resubmit, filing"

4. **Display** as cards in a flow layout or grid (2-3 columns):
   - Each card shows: auto-generated label (top terms), ticket count,
     avg CSAT for tickets in that cluster, avg sentiment
   - Cards are color-coded by sentiment (green/amber/red border)
   - Clicking a card populates the drill-down panel with the tickets in
     that cluster

5. **Optional enhancement**: Show a simplified scatter plot using the
   first 2 PCA components of the TF-IDF matrix, with points colored by
   cluster. Use `sklearn.decomposition.PCA` (already available via
   scikit-learn). This is the "nice to have" visual. If time is tight,
   skip it and just show the cards.

### 3.6 Drill-Down Panel

A panel at the bottom of the page (collapsible or in a QSplitter) that
shows the actual tickets matching the selected term or cluster. Reuse
the conversation card style from ConversationSearchPage — show ticket
ID, subject, TRC, CSAT, preview text. Clicking a card opens the full
thread in a dialog (same FullThreadDialog pattern from conversation
search).

Limit to 50 tickets in the drill-down. Show "Showing 50 of N matches"
if truncated.

### 3.7 Data / Compute Layer

Create `src/data/trending_engine.py` with these functions:

```python
def compute_sentiment_trends(db, date_start, date_end, trc_filter, window_size) -> dict:
    """Returns {trc: [(window_label, avg_compound), ...]}"""

def compute_rising_terms(db, date_start, date_end, trc_filter, window_size) -> dict:
    """Returns {rising: [...], cooling: [...]} with term, velocity, sparkline data"""

def compute_topic_clusters(db, date_start, date_end, trc_filter) -> dict:
    """Returns {clusters: [{label, terms, ticket_ids, count, avg_csat, avg_sentiment}, ...]}"""

def get_tickets_for_term(db, term, date_start, date_end, trc_filter) -> list:
    """Returns list of conversation dicts containing the term"""
```

**IMPORTANT:** All three compute functions are expensive (TF-IDF on
potentially thousands of conversations). Run them in a QThread worker,
NOT on the main thread. Show a QProgressDialog during computation.
The Trending Topics page should have a single worker that runs all three
in sequence when "Analyze" is clicked.

### 3.8 NLTK Data Bootstrapping

VADER and stopwords require nltk data files. Add a utility function
that checks for and downloads them on first use:

```python
import nltk
for resource in ['vader_lexicon', 'punkt', 'punkt_tab', 'stopwords']:
    try:
        nltk.data.find(f'corpora/{resource}' if resource == 'stopwords'
                       else f'sentiment/{resource}' if 'vader' in resource
                       else f'tokenizers/{resource}')
    except LookupError:
        nltk.download(resource, quiet=True)
```

Call this at app startup (main.py) or lazily on first use in
trending_engine.py. Prefer lazy (on first Analyze click) with a
"Downloading language data..." progress message, since not everyone
will use this feature.

---

## 4. Conventions to Follow

### UI Patterns
- All pages use the same header pattern: QLabel with objectName
  "PageHeader" + QLabel with objectName "PageSubheader"
- Cards use QFrame with `background: ALMA_WHITE; border: 1px solid
  ALMA_BORDER_LIGHT; border-radius: 10px`
- Buttons follow the existing stylesheet patterns in conversation_search
  and ingestion_dialog (green primary, gray secondary)
- Use the ALMA_ color constants from theme.py everywhere, never
  hardcode hex values inline

### Architecture
- Data computation lives in `src/data/`, UI lives in `src/ui/pages/`
- Chart widgets live in `src/ui/widgets/charts.py` (new file)
- Heavy computation runs in QThread workers, never on the main thread
- Pages receive `db_manager` in their constructor (same as existing)

### What NOT to Do
- Do NOT add new pip dependencies. Everything needed (pandas,
  scikit-learn, nltk, numpy) is already in requirements.txt.
- Do NOT use matplotlib, plotly, or any external charting library.
  Charts are rendered with QPainter on custom QWidgets.
- Do NOT modify the conversations table schema. The new columns go in
  `tickets` only.
- Do NOT change existing page behavior. ConversationSearchPage,
  SettingsPage, and the ingestion pipeline should be untouched except
  for the resolution time upsert fix.
- Do NOT remove the ReportsPage placeholder. Only DashboardPage and
  TrendingPage are being replaced.

---

## 5. Files to Create

```
src/ui/widgets/charts.py         — BarChartWidget, BoxPlotWidget, HeatmapWidget,
                                    LineChartWidget, SparklineWidget
src/ui/pages/trc_analytics.py    — TRCAnalyticsPage (replaces DashboardPage)
src/ui/pages/trending_topics.py  — TrendingTopicsPage (replaces TrendingPage)
src/data/trc_analytics.py        — compute_trc_analytics()
src/data/trending_engine.py      — sentiment, TF-IDF, clustering functions
```

## 6. Files to Modify

```
src/data/db_manager.py           — Add 3 columns to tickets, upsert, migration
src/data/csv_ingestion.py        — Pass resolution times to upsert_ticket()
src/data/conversation_rebuild.py — Pass resolution times to upsert_ticket()
src/ui/main_window.py            — Update imports (DashboardPage → TRCAnalyticsPage,
                                    TrendingPage → TrendingTopicsPage)
src/ui/pages/placeholders.py     — Remove DashboardPage and TrendingPage classes
                                    (keep ReportsPage)
```

---

## 7. Testing Checklist

After building, verify:

- [ ] App launches without errors (`python main.py` via `run_clean.bat`)
- [ ] TRC Analytics page loads and shows empty state gracefully
- [ ] Import the test CSV (`lightdash_test_export.csv`, 502 rows) via
      CSV import on Conversations page
- [ ] TRC Analytics: Refresh shows populated charts and table
- [ ] TRC Analytics: Resolution time columns have data (not all NULL)
- [ ] TRC Analytics: CSAT heatmap renders with color gradient
- [ ] TRC Analytics: Metrics table sorts by clicking column headers
- [ ] Trending Topics: Analyze button shows progress dialog
- [ ] Trending Topics: Sentiment chart renders with lines per TRC
- [ ] Trending Topics: Rising/cooling terms populate with sparklines
- [ ] Trending Topics: Topic clusters show labeled cards
- [ ] Trending Topics: Clicking a term shows matching tickets in drill-down
- [ ] Trending Topics: Clicking a cluster shows its tickets in drill-down
- [ ] Conversations page still works correctly (no regressions)
- [ ] Settings page still works correctly (no regressions)
