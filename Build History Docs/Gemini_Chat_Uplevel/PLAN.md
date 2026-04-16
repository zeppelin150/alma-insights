# Gemini Chat Uplevel — Build Plan

**Created**: 2026-04-03
**Mockups**: See `mockup_chat_main.png` and `mockup_explore_dropdown.png` in this directory
**Prior spec**: `C:\Users\Chris\.claude\plans\gemini-chat-redesign.md` (data layer + observability detail)
**Status**: Planning

---

## Overview

Transform Gemini Chats into the premier Alma Insights feature. Three workstreams:

1. **UI Redesign** — Header, bubbles, Explore dropdown, drilldown panels
2. **Data Layer** — `chat_messages` table, `chat_projects`, `chat_tool_executions`, virtual views
3. **Performance** — Warm bridge, priority rate governor, concurrent operation

---

## Mockup Reference (from Figma/Cambric designs)

### Mockup 1: Main Chat View

**Header bar** (left to right):
- "Chat" title (26px bold)
- `[+ New chat]` button (ALMA_GREEN_DARK bg, cream text, rounded)
- **RECENT** label + 4 recent chat chips as rounded pills
  - Active chip: ALMA_GREEN_DARK bg with cream text ("Alma platform bug tickets")
  - Inactive chips: white bg, 1px ALMA_BORDER, dark text ("Billing mismatch root cause", "Provider sync failure analysis", "Session link broken -- Ticket 1...")
- Model selector: pill-style with green dot indicator + "gemini-3-flash-preview" + dropdown chevron
- **[Explore]** button: ALMA_GREEN_DARK bg, cream text, hamburger icon prefix -- opens Explore dropdown

**User bubble**:
- Right-aligned, ALMA_GREEN_DARK background
- "You" label in cream/gold, 12px semi-bold
- Message text in ALMA_CREAM (#F3F1EC), 14px regular
- Rounded corners (12px)
- Max width ~70% of content area

**Gemini bubble**:
- Left-aligned, white (#FFFFFF) background with subtle 1px ALMA_BORDER_LIGHT border
- "Gemini" label in green (#14573F), 12px semi-bold
- Intro text as normal prose (13px, ALMA_TEXT_DARK)
- **Finding sections** rendered as cards with left green border:
  - Section header bold ("1. Broken Session & Portal Links")
  - Description text as normal prose
  - **Ticket references as inline code chips**: `#10246`, `#10145`, `#10108, 10023, 10228, 10089`
    - Monospace font, light gray bg, rounded, clickable
    - These should be interactive — click opens ticket in drilldown
- Rounded corners (12px) on outer bubble

**Input bar**:
- Full-width text field with placeholder "Ask anything about your ticket data..."
- "Send" button: ALMA_GREEN_DARK bg, cream text, rounded (larger than current)

**Status bar**:
- "Ready" left, "888 conversations in database" right

### Mockup 2: Explore Dropdown

When user clicks **[Explore]** button, a dropdown menu appears below it with these options:

| Icon | Label | Badge | Drilldown Panel |
|------|-------|-------|-----------------|
| Chat bubble icon | **All Chats** | `888` (total chat count) | Opens drilldown to full chat history list |
| Folder icon | **Projects** | `12` (project count) | Opens drilldown to project browser |
| Chart/report icon | **Reports** | `24` (report count) | Opens drilldown showing all reports from across the app (AI Reports, Smart Reports, VOC) for chat-through |
| Alert triangle icon | **Incidents** | `3` (active incidents) | Opens drilldown showing active incidents for investigation |

**Removed from original**: "Schedule & Dispatch" — not relevant to chat experience.

**Added** (not in mockup, to be designed): **Monitor** panel — observability mode showing tool calls, tokens, cost, latency. This gets its own entry in the Explore dropdown or a separate toggle.

**Behavior**: Each option forces the drilldown panel to open and navigate directly to the corresponding panel/tab. The Explore button acts as a quick-jump to any drilldown section.

---

## Drilldown Panel Modes (420px, slides from right)

### Mode 1: All Chats
- Search bar (FTS5 on `chat_messages.content`)
- Session list ordered by `last_message_at DESC`
- Each card: title, message count, cost badge, relative time
- Click to load session in main chat area
- Infinite scroll or paginated

### Mode 2: Projects
- Project list with session counts
- Expandable — click project to see sessions within
- Drag sessions between projects
- `[+ New Project]` button at bottom
- Each project card: name, session count, last active

### Mode 3: Reports (Cross-App Report Browser)
- **Pulls reports from ALL pages**: AI Reports, Smart Reports, VOC, A/B Compare
- Source: `analysis_reports` + `analysis_runs` tables
- Each report card: report type badge, date, TRC filter, ticket count, cost
- Click to load report into chat context — starts a follow-up chat grounded in that report
- Filter by report type, date range
- This unifies the "Follow-Up Chat" experience — any report becomes chattable from Gemini Chats

### Mode 4: Incidents
- Active incidents from `incident_flags` + `watchlist_alerts`
- Each card: severity badge, TRC, description, ticket count
- Click to start incident investigation chat
- Injects incident context into chat system prompt

### Mode 5: Monitor (Observability)
- Live feed of current session's tool calls, context injection, token usage
- Event timeline with color-coded cards:
  - USER (green border): message preview
  - CONTEXT (blue border): system prompt size, history tokens, data scope
  - TOOL (amber border): tool name, args, result rows, elapsed ms, tables touched
  - RESPONSE (teal border): tokens in/out, cost, latency, model
- Session totals footer: cumulative tokens, cost, tool calls, avg latency
- Data source: `chat_messages` + `chat_tool_executions` tables

---

## Data Layer (Migration 009)

### New Tables

```sql
CREATE TABLE IF NOT EXISTS chat_projects (
  project_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  sort_order INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(session_id),
  ordinal INTEGER NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool_result')),
  content TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  model_used TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd REAL,
  latency_ms INTEGER,
  tool_calls TEXT,        -- JSON summary array
  tool_round INTEGER DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  metadata TEXT,          -- JSON extensible
  UNIQUE(session_id, ordinal)
);
CREATE INDEX idx_chat_msg_session ON chat_messages(session_id, ordinal);
CREATE INDEX idx_chat_msg_created ON chat_messages(created_at);

CREATE TABLE IF NOT EXISTS chat_tool_executions (
  execution_id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES chat_messages(message_id),
  session_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  args_json TEXT,
  result_json TEXT,
  result_rows INTEGER,
  tables_touched TEXT,    -- JSON array
  elapsed_ms INTEGER,
  error TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_tool_exec_msg ON chat_tool_executions(message_id);
CREATE INDEX idx_tool_exec_session ON chat_tool_executions(session_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
  content, session_id UNINDEXED, message_id UNINDEXED,
  content=chat_messages, content_rowid=rowid
);

ALTER TABLE chat_sessions ADD COLUMN project_id TEXT REFERENCES chat_projects(project_id);
```

### Virtual Views

```sql
CREATE VIEW v_session_summary AS
SELECT
  s.session_id, s.title, s.source_page, s.project_id,
  s.created_at, s.updated_at, s.filter_json, s.ticket_count,
  COUNT(m.message_id) as message_count,
  SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) as user_messages,
  SUM(CASE WHEN m.role='assistant' THEN 1 ELSE 0 END) as assistant_messages,
  SUM(COALESCE(m.tokens_in,0)) as total_tokens_in,
  SUM(COALESCE(m.tokens_out,0)) as total_tokens_out,
  SUM(COALESCE(m.cost_usd,0)) as total_cost,
  MAX(m.created_at) as last_message_at,
  (SELECT content FROM chat_messages
   WHERE session_id=s.session_id AND role='user'
   ORDER BY ordinal LIMIT 1) as first_question
FROM chat_sessions s
LEFT JOIN chat_messages m ON m.session_id = s.session_id
GROUP BY s.session_id;

CREATE VIEW v_session_tools AS
SELECT
  e.session_id, e.tool_name,
  COUNT(*) as call_count,
  SUM(e.elapsed_ms) as total_ms,
  AVG(e.elapsed_ms) as avg_ms,
  SUM(e.result_rows) as total_rows
FROM chat_tool_executions e
GROUP BY e.session_id, e.tool_name;

CREATE VIEW v_chat_cost_daily AS
SELECT
  SUBSTR(m.created_at,1,10) as date,
  m.model_used,
  COUNT(DISTINCT m.session_id) as sessions,
  SUM(m.tokens_in) as tokens_in,
  SUM(m.tokens_out) as tokens_out,
  SUM(m.cost_usd) as total_cost
FROM chat_messages m
WHERE m.role = 'assistant'
GROUP BY SUBSTR(m.created_at,1,10), m.model_used;
```

### Migration Data Path

1. Create new tables + views
2. For each existing `chat_sessions` row:
   - `json.loads(messages)`
   - INSERT each message as `chat_messages` row with ordinal
3. Rebuild `chat_sessions` without `messages` blob column
4. Drop legacy columns: `trc_filter`, `date_start`, `date_end` (duplicated in `filter_json`)

---

## UI Component Mapping

### Header Bar

| Element | Qt Widget | Properties |
|---------|-----------|------------|
| "Chat" title | `QLabel` | 26px Inter Bold, `ALMA_TEXT_DARK` |
| + New chat | `QPushButton` | `ALMA_GREEN_DARK` bg, `ALMA_TEXT_ON_DARK`, 6px radius |
| RECENT label | `QLabel` | 11px Inter Medium, `ALMA_TEXT_LIGHT`, uppercase |
| Recent chips | `QHBoxLayout` of `QPushButton` | Checkable, 15px border-radius, active = GREEN_DARK bg |
| Model selector | `QPushButton` (custom) | Green dot + model name + chevron, dropdown on click |
| Explore button | `QPushButton` | `ALMA_GREEN_DARK` bg, hamburger icon + "Explore" text |

### Explore Dropdown

| Element | Qt Widget | Properties |
|---------|-----------|------------|
| Dropdown container | `QMenu` or `QFrame` popup | White bg, 8px radius, shadow, 1px border |
| Menu items | Custom `QWidgetAction` | Icon + label + count badge (right-aligned) |
| Count badges | `QLabel` | Light gray bg, rounded pill, 11px bold |
| Icons | `QLabel` with emoji or SVG | Chat bubble, folder, chart, alert triangle |

### Chat Bubbles

| Element | Qt Widget | Properties |
|---------|-----------|------------|
| User bubble | `QFrame` (MessageBubble) | `ALMA_GREEN_DARK` bg, 12px radius, right-aligned |
| User "You" label | `QLabel` | 12px Semi Bold, `ALMA_TEXT_ON_DARK` |
| User text | `QLabel` (word-wrap) | 14px Regular, `ALMA_CREAM` |
| Gemini bubble | `QFrame` (MessageBubble) | `#FFFFFF` bg, 1px `ALMA_BORDER_LIGHT`, 12px radius |
| Gemini "Gemini" label | `QLabel` | 12px Semi Bold, `ALMA_GREEN_LIGHT` (#14573F) |
| Finding sections | `QFrame` with left border | 3px left border `ALMA_GREEN_LIGHT`, light cream bg |
| Section headers | `QLabel` | 14px Bold, `ALMA_TEXT_DARK` |
| Ticket chips | `QLabel` or `QPushButton` | Monospace, light gray bg (#F0EDE8), rounded, clickable |
| Prose text | `QLabel` (word-wrap) | 13px Regular, `ALMA_TEXT_DARK`, line-height 22px |

### Drilldown Panel

| Element | Qt Widget | Properties |
|---------|-----------|------------|
| Panel container | `DrilldownPanel` (existing) | 420px width, slide-from-right animation |
| Mode tabs | Internal state, no visible tabs | Driven by Explore dropdown selection |
| Search bar | `QLineEdit` | Queries `chat_messages_fts` |
| Session cards | `QFrame` in `QScrollArea` | Title, message count, cost, time |
| Project groups | `QTreeWidget` or collapsible `QFrame` | Expandable project folders |
| Monitor timeline | `QScrollArea` of event `QFrame`s | Color-coded left border per event type |
| Report cards | `QFrame` | Report type badge, date, TRC, cost |
| Incident cards | `QFrame` | Severity badge, TRC, description |

---

## Performance Architecture

### Warm Bridge (eliminates ~1.5s cold start per message)

```python
# GeminiChatsPage adopts warm bridge pattern
class GeminiChatsPage:
    def _init_bridge(self):
        self._warm_bridge = GeminiBridgeWrapper()
        self._warm_bridge.boot()  # 1.5s cold start once

    def _on_send(self, text):
        # Reuse warm bridge instead of build_client_for_task()
        self._engine.send(text, client=self._warm_bridge)
```

### Priority Rate Governor (chat never starved by scans)

```python
class PriorityRateGovernor(RateGovernor):
    def acquire(self, priority="normal", timeout=60):
        if priority == "chat":
            return True  # Chat always gets through
        return super().acquire(timeout=timeout)
```

### Concurrency Matrix

| Scenario | Bridges | Memory | API slots | Confirmed safe? |
|----------|---------|--------|-----------|-----------------|
| Chat only | 1 warm | ~100MB | 1 | Yes |
| Chat + NLP Scan | 1 + 3 | ~400MB | 4 | Yes (separate pools) |
| Chat + Report gen | 1 + 4 | ~500MB | 5 | Yes (separate pools) |
| Chat + Scan + Report | 1 + 3 + 4 | ~800MB | 8 | Yes (no global mutex) |

---

## Explore Dropdown → Drilldown Panel Routing

```python
EXPLORE_PANELS = {
    "all_chats": {
        "label": "All Chats",
        "icon": "chat_bubble",
        "badge_query": "SELECT COUNT(*) FROM chat_sessions",
        "drilldown_mode": "chat_history",
    },
    "projects": {
        "label": "Projects",
        "icon": "folder",
        "badge_query": "SELECT COUNT(*) FROM chat_projects",
        "drilldown_mode": "projects",
    },
    "reports": {
        "label": "Reports",
        "icon": "chart",
        "badge_query": "SELECT COUNT(*) FROM analysis_reports",
        "drilldown_mode": "report_browser",
    },
    "incidents": {
        "label": "Incidents",
        "icon": "alert",
        "badge_query": "SELECT COUNT(*) FROM incident_flags WHERE active=1",
        "drilldown_mode": "incidents",
    },
    "monitor": {
        "label": "Monitor",
        "icon": "eye",
        "badge_query": None,  # No count badge
        "drilldown_mode": "observability",
    },
}
```

On click: `drilldown_panel.show_mode(mode)` — opens panel and switches to correct view.

---

## Reports Panel (Cross-App Report Browser)

This is the key integration piece. The Reports panel in the drilldown aggregates reports from:

| Source Table | Report Types | Fields Shown |
|-------------|-------------|-------------|
| `analysis_reports` (page='ai_reports') | General trend, TRC deep dive, friction, anomaly, cross-TRC | Date, TRC filter, ticket count |
| `analysis_reports` (page='smart_reporting') | Smart pipeline reports | Date, config, cost |
| `analysis_reports` (report_type='voc_rca') | VOC Root Cause Analysis | Date, TRC, ticket count |
| `analysis_runs` | AI report metadata | Prompt template, model, tokens, cost, duration |

**Click behavior**: Loads report context into chat, starts a new session with:
- `source_page = "report_drilldown"`
- `source_context = {"report_id": X, "report_type": "ai_reports"}`
- System prompt includes report summary as context
- User can ask follow-up questions grounded in the report data

This replaces the current ephemeral ReportChatWidget with a persistent, first-class experience.

---

## Build Sequence (Recommended)

### Session 1: Data Layer Foundation
- Migration 009 (new tables, views, data extraction)
- Update `chat_session.py` CRUD to write `chat_messages` rows
- Update `chat_engine.py` to capture tool execution telemetry
- Tests for new CRUD layer

### Session 2: UI Redesign
- Header bar: recent chips, model selector, Explore button
- User bubble: ALMA_GREEN_DARK + cream text
- Gemini bubble: finding sections with left-border cards, ticket chips
- Markdown rendering refinement (prose, not JSON)

### Session 3: Explore Dropdown + Drilldown Panels
- Explore dropdown menu with badge counts
- Drilldown panel modes: All Chats, Projects, Reports, Incidents
- Report browser pulling from analysis_reports + analysis_runs
- Monitor panel (observability timeline)

### Session 4: Performance + Polish
- Warm bridge for GeminiChatsPage
- Priority rate governor
- FTS5 chat search
- Follow-up chat unification (ephemeral → persistent)

---

## Files Modified/Created

### New Files
- `migrations/009_chat_data_layer.sql`
- `src/ui/widgets/explore_menu.py` — Explore dropdown + routing
- `src/ui/widgets/chat_drilldown.py` — Multi-mode drilldown (chats, projects, reports, incidents, monitor)
- `src/ui/widgets/message_bubble.py` — Redesigned bubble with finding cards + ticket chips
- `src/ui/widgets/monitor_panel.py` — Observability timeline

### Modified Files
- `src/services/chat_session.py` — CRUD now writes `chat_messages` rows, not JSON blob
- `src/services/chat_engine.py` — Captures token/cost/latency per message, tool execution detail
- `src/data/chat_tools/registry.py` — Writes `chat_tool_executions` on every dispatch
- `src/ui/pages/gemini_chats_page.py` — Header redesign, warm bridge, Explore integration
- `src/data/db_manager.py` — New table methods, virtual view registration
- `src/ui/widgets/drilldown_panel.py` — Multi-mode support

### Estimated Scope
- ~1,200 new LOC (data layer + new widgets)
- ~400 modified LOC (existing files)
- ~80 new tests
- 4 build sessions
