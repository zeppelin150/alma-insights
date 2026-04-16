# Frontend Design Queue — UI Mockup Specifications

## Purpose

This document describes every UI change across Stages 1-3 for mockup generation. Each section provides the layout, components, states, and visual specifications needed to create design mockups in Figma or similar tools. These mockups will be used as implementation references.

**Output directory**: `C:\alma-insights\Build History Docs\Multi-source database plan\`

**Design system**: Alma Insights uses a cream/green color palette with the following tokens:
- `ALMA_GREEN_DARK`: `#1B4332` (sidebar, primary buttons, headers)
- `ALMA_GREEN_MID`: `#2D6A4F` (hover states, accents)
- `ALMA_CREAM`: `#F5F1EB` (page backgrounds)
- `ALMA_WHITE`: `#FFFFFF` (cards, panels)
- Danger/destructive: `#D32F2F` (red buttons, warnings)
- Text primary: `#1A1A1A`
- Text secondary: `#666666`
- Border: `#E0D8CC`
- Font: System default (Segoe UI on Windows)

---

## Stage 1 Mockups

### 1.1 — Updated "Clear & Close" Dialog

**Location**: Appears when user closes the app (main_window.py closeEvent)

**Current state**: Dialog says "Clear session data?" with text about removing imported ticket data.

**New design**:
```
┌─────────────────────────────────────────────────────┐
│  Close Alma Insights                                │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Clear staging data?                                │
│                                                      │
│  Staging data (raw import rows) will be cleared.    │
│  All tickets, enrichments, and reports are           │
│  preserved in the database.                          │
│                                                      │
│  Choose "Keep & Close" to retain everything as-is.  │
│                                                      │
│  ┌──────────────┐ ┌──────────────┐ ┌────────┐      │
│  │ Clear & Close│ │ Keep & Close │ │ Cancel │      │
│  └──────────────┘ └──────────────┘ └────────┘      │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**Key differences from current**:
- Title changed from "Clear session data?" to "Clear staging data?"
- Body text emphasizes that tickets/enrichments/reports are PRESERVED
- "Clear & Close" only clears staging (raw_ingestion_rows), not tickets
- No mention of "data privacy" — data stays in warehouse by design

---

### 1.2 — "Full Database Reset" in Settings Page

**Location**: Settings page, new "Data Management" section (bottom of Display tab or new Data tab)

**Layout**:
```
┌─────────────────────────────────────────────────────────────┐
│  Data Management                                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Database Status                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  Tickets: 2,847  │  Sources: 1  │  Last Import: 04/03  ││
│  │  NLP Scans: 12   │  Reports: 8  │  DB Size: 142 MB     ││
│  └─────────────────────────────────────────────────────────┘│
│                                                              │
│  ⚠ Danger Zone                                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                                                          ││
│  │  Full Database Reset                                     ││
│  │  Permanently delete ALL data including tickets,          ││
│  │  enrichments, reports, and chat history.                  ││
│  │  This cannot be undone.                                  ││
│  │                                                          ││
│  │  ┌──────────────────────────┐                            ││
│  │  │  🔴 Full Database Reset  │                            ││
│  │  └──────────────────────────┘                            ││
│  │                                                          ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

**Confirmation dialog** (appears when button clicked):
```
┌─────────────────────────────────────────────────────┐
│  Confirm Full Database Reset                        │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ⚠ This will permanently delete:                    │
│  • 2,847 tickets and conversations                  │
│  • 12 NLP scan results                              │
│  • 8 analysis reports                               │
│  • All chat history                                 │
│                                                      │
│  Type DELETE to confirm:                            │
│  ┌─────────────────────────────────────┐            │
│  │                                     │            │
│  └─────────────────────────────────────┘            │
│                                                      │
│  ┌──────────────┐         ┌──────────┐              │
│  │  🔴 Reset    │         │  Cancel  │              │
│  └──────────────┘         └──────────┘              │
│                                                      │
└─────────────────────────────────────────────────────┘
```

**States**:
- Reset button DISABLED until user types "DELETE" exactly
- Button is red (#D32F2F) with white text
- Danger zone bordered with red/orange dashed border

---

### 1.3 — Import Status Banner (Conversation Search)

**Location**: Conversation Search page, below the filter bar

**After a successful additive import, show a status banner**:
```
┌─────────────────────────────────────────────────────────────────┐
│  ✓ Import complete: 5 new tickets added (1,995 skipped — already│
│    in database). Total: 2,847 tickets in warehouse.            │
│                                                     [Dismiss ×] │
└─────────────────────────────────────────────────────────────────┘
```

**Color**: Light green background (#E8F5E9), dark green text, dismiss button.

**Variants**:
- All new: "✓ Import complete: 2,000 new tickets added."
- All skipped: "ℹ Import complete: 0 new tickets (all 2,000 already in database)."
- Error: Red background, "✗ Import failed: [error message]"

---

## Stage 2 Mockups

### 2.1 — Source Management Section in Settings

**Location**: Settings page, new "Sources" section or tab

**Layout**:
```
┌─────────────────────────────────────────────────────────────────┐
│  Data Sources                                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Registered Sources                                  [+ New Source]│
│  ┌───────────────────────────────────────────────────────────┐   │
│  │  ✓ Zendesk - RCM Support (default)                       │   │
│  │    Type: Zendesk │ Tickets: 2,847 │ Last import: 04/03   │   │
│  │    [Edit Mapping]  [View Data]                            │   │
│  ├───────────────────────────────────────────────────────────┤   │
│  │  ○ Zendesk - Provider Group                               │   │
│  │    Type: Zendesk │ Tickets: 0 │ Not yet imported          │   │
│  │    [Edit Mapping]  [Delete]                               │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 — Create New Source Dialog

**Triggered by**: "+ New Source" button in Settings

```
┌─────────────────────────────────────────────────────────────────┐
│  Create New Data Source                                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Source Name:                                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Zendesk - Provider Group                                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Source Type:                                                    │
│  ┌──────────────────────┐                                       │
│  │ Zendesk          ▼   │                                       │
│  └──────────────────────┘                                       │
│  Options: Zendesk, Kodif (Stage 3), Custom                      │
│                                                                  │
│  Column Mapping:                                                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Internal Field    │  CSV Column Name                      │ │
│  ├────────────────────┼───────────────────────────────────────┤ │
│  │  Ticket ID         │  ┌─────────────────────┐             │ │
│  │                    │  │ ticket_id        ▼   │             │ │
│  │  Subject           │  │ subject          ▼   │             │ │
│  │  TRC Code          │  │ trc_code         ▼   │             │ │
│  │  Created At        │  │ created_at       ▼   │             │ │
│  │  Conversation Body │  │ description      ▼   │             │ │
│  │  Provider ID (opt) │  │ ─ not mapped ─   ▼   │             │ │
│  │  Client ID (opt)   │  │ ─ not mapped ─   ▼   │             │ │
│  └────────────────────┴───────────────────────────────────────┘ │
│                                                                  │
│  Conversation Structure:                                         │
│  ○ Row per comment (needs rebuild)    ← Zendesk default         │
│  ○ Self-contained (single column)     ← Kodif default           │
│                                                                  │
│  ┌────────────┐         ┌──────────┐                            │
│  │   Create   │         │  Cancel  │                            │
│  └────────────┘         └──────────┘                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 — Source Selector on Conversation Search (Import)

**Location**: Conversation Search page, in the import dropdown area

**Current**: `[Import CSV... ▼]  [Pull Data]`

**New**: Add source indicator showing which source this import targets:

```
┌─────────────────────────────────────────────────────────────────┐
│  Conversation Search                                             │
│  Search and browse rebuilt ticket conversations                  │
│                                                                  │
│  Importing to: ┌────────────────────────┐                       │
│                │ Zendesk - RCM Support ▼│  [Import CSV... ▼] [Pull Data]│
│                └────────────────────────┘                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Keyword [________]  TRC Code [All TRCs ▼]              │   │
│  │  From [Jan 1, 2020 ▼]  To [Apr 5, 2026 ▼]  CSAT [Any]│   │
│  │                                     [Search]  [Clear]   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.4 — Source Selector on Analytics Pages

**Location**: Each analytics page (Incidents, Trending Topics, Smart Reporting, AI Reports)

**Reusable widget**: Appears in the filter bar area of each page.

```
┌──────────────────────────────────────────────────────────────┐
│  Source: ┌──────────────────────────┐                        │
│          │ Zendesk - RCM Support  ▼ │                        │
│          └──────────────────────────┘                        │
│  Options:                                                     │
│    • All Sources                                              │
│    • Zendesk - RCM Support                                    │
│    • Zendesk - Provider Group                                 │
│    • (future Kodif sources)                                   │
└──────────────────────────────────────────────────────────────┘
```

**Placement**: Left-aligned in filter bar, before date range pickers. Consistent across all pages.

---

## Stage 3 Mockups

### 3.1 — Data Warehouse Page (NEW PAGE)

**Location**: Sidebar position 5 (between Smart Reporting and Settings)

**Sidebar icon**: Database/warehouse icon

**Full page layout**:
```
┌──────────────────────────────────────────────────────────────────────────┐
│  Data Warehouse                                          [2,847 tickets]│
│  Browse all stored data across all sources                               │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ Source: [All Sources ▼]  From: [Jan 1, 2020 ▼]  To: [Apr 5 ▼]   │  │
│  │ TRC: [All TRCs ▼]  Client ID: [________]  Provider ID: [________]│  │
│  │ Insurance: [All ▼]  Keyword: [________]            [🔍 Search]  │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  Results: 2,847 tickets across 2 sources         [Export CSV ▼]         │
│  ┌──────┬──────────┬─────────┬──────────────────┬────────┬──────────┐   │
│  │  ID  │   Date   │   TRC   │    Subject       │ Source │ Client   │   │
│  ├──────┼──────────┼─────────┼──────────────────┼────────┼──────────┤   │
│  │12345 │ 03/15/26 │ AUTH-01 │ Prior auth delay │Zendesk │ CLT-001  │   │
│  │12346 │ 03/15/26 │ BIL-03  │ Claim denied EOB │Zendesk │ CLT-002  │   │
│  │12347 │ 03/14/26 │ AUTH-01 │ Auth turnaround  │Zendesk │ CLT-001  │   │
│  │12348 │ 03/14/26 │ ELG-02  │ Eligibility chk  │Zendesk │ CLT-045  │   │
│  │  ⋮   │    ⋮     │    ⋮    │       ⋮          │   ⋮    │    ⋮     │   │
│  │      │          │         │ (virtual scroll)  │        │          │   │
│  └──────┴──────────┴─────────┴──────────────────┴────────┴──────────┘   │
│                                                                          │
│  ── Ticket Detail: #12345 ──────────────────────────────────────────    │
│  ┌──────────┬───────────┬───────────┬──────────┐                        │
│  │ Overview │  NLP Data │ Timeline  │ Related  │                        │
│  └──────────┴───────────┴───────────┴──────────┘                        │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  TRC: AUTH-01         │  Client: CLT-001    │  Provider: PRV-042│   │
│  │  Classification: Prior Authorization Issue                       │   │
│  │  Sentiment: Negative (-0.72)  │  Friction: High                 │   │
│  │  Ngrams: "prior auth", "peer review", "turnaround time"         │   │
│  │  Issue Types: [Authorization Delay] [Peer Review Required]       │   │
│  │  CSAT: 2.1  │  Priority: High  │  Status: Closed               │   │
│  │  Insurance: UHC  │  Policy: UHC-987654                          │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ── TRC History: AUTH-01 ───────────────────────────────────────────    │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Volume (last 90 days):  ▁▂▃▅▇▅▃▂▁                              │   │
│  │  Total tickets: 342  │  Avg CSAT: 2.8  │  Trend: ↓ declining    │   │
│  │                                                                   │   │
│  │  Top Issues:                                                      │   │
│  │  ██████████ Authorization Delay (42%)                             │   │
│  │  ██████     Peer Review Required (28%)                            │   │
│  │  ████       Turnaround Time (18%)                                 │   │
│  │  ██         Missing Documentation (12%)                           │   │
│  │                                                                   │   │
│  │  Ngram Trends:                                                    │   │
│  │  "prior auth"  ↑ 15% (rising)    "peer review"  → stable         │   │
│  │  "turnaround"  ↓ 8% (cooling)    "expedite"     ↑ 22% (rising)  │   │
│  │                                                                   │   │
│  │  Related TRCs: BIL-03 (co-occurrence: 34%), ELG-02 (18%)        │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**States to mockup**:
1. **Empty state**: No data in warehouse → "Import data via Conversation Search to get started"
2. **Loading state**: Virtual scroll loading indicator at bottom of table
3. **Filtered state**: Filter applied, showing subset with "Showing 342 of 2,847 tickets"
4. **Detail expanded**: Ticket selected, detail panel visible below table
5. **TRC history expanded**: TRC history panel visible below detail

### 3.2 — Sidebar with Data Warehouse

**Current sidebar** (9 pages):
```
┌──────────────────┐
│ 🔍 Conversations │
│ 📊 TRC Analytics │
│ 📈 Trending      │
│ ⚠  Incidents     │
│ 📋 Smart Report  │
│ ⚙  Settings      │
│ 🤖 AI Reports    │
│ 📡 Source Monitor │
│ 📚 Guru          │
└──────────────────┘
```

**New sidebar** (10 pages):
```
┌──────────────────┐
│ 🔍 Conversations │
│ 📊 TRC Analytics │
│ 📈 Trending      │
│ ⚠  Incidents     │
│ 📋 Smart Report  │
│ 🗄  Data Warehouse│  ← NEW
│ ⚙  Settings      │
│ 🤖 AI Reports    │
│ 📡 Source Monitor │
│ 📚 Guru          │
└──────────────────┘
```

### 3.3 — Updated Conversation Search with Source Type Selector

**Location**: Conversation Search page, import area

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Conversation Search                                                     │
│  Search and browse rebuilt ticket conversations                          │
│                                                                          │
│  ┌────────────────────────────┐                                         │
│  │ Importing to:              │  ┌─────────────┐ ┌──────────┐          │
│  │ ┌────────────────────────┐ │  │Import CSV...▼│ │Pull Data │          │
│  │ │Zendesk - RCM Support ▼ │ │  └─────────────┘ └──────────┘          │
│  │ └────────────────────────┘ │                                         │
│  │  ○ Zendesk - RCM Support  │                                         │
│  │  ○ Zendesk - Provider Grp │                                         │
│  │  ○ Kodif - Member Chat    │                                         │
│  │  + Create New Source...   │                                         │
│  └────────────────────────────┘                                         │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Viewing: All Sources (2,847 tickets)                            │   │
│  │  Keyword [________]  TRC [All TRCs ▼]  Source [All ▼]           │   │
│  │  From [Jan 1 ▼]  To [Apr 5 ▼]  CSAT [Any]  [Search]  [Clear]  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.4 — Analytics Page with Combined Mode Toggle

**Location**: Each analytics page (showing Incidents as example)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Incidents Analysis                                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Source: ┌───────────────────────┐  Mode: ○ Per-Source  ● Combined      │
│          │ All Sources         ▼ │                                       │
│          └───────────────────────┘                                       │
│  From: [Mar 1 ▼]  To: [Apr 5 ▼]  TRC: [All ▼]     [🔄 Refresh]      │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  When "Combined" is selected:                                    │   │
│  │  Results aggregate across all sources.                           │   │
│  │  Source column appears in results table.                         │   │
│  │  Charts show stacked bars by source.                             │   │
│  │                                                                   │   │
│  │  When "Per-Source" is selected:                                   │   │
│  │  Only the selected source's data is analyzed.                    │   │
│  │  No source column in results.                                    │   │
│  │  Charts show single-source data.                                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.5 — Guru Page with Source Selector

**Location**: Guru page, Gap Analysis tab

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Guru Knowledge Base                                                     │
│  ┌────────┬──────────────┬────────┬───────────────┬────────────┐        │
│  │ Cards  │ Gap Analysis │ Drafts │ Effectiveness │ Connection │        │
│  └────────┴──────────────┴────────┴───────────────┴────────────┘        │
│                                                                          │
│  Friction Analysis                                                       │
│  Analyze friction for: ┌────────────────────────┐                       │
│                         │ All Sources (default) ▼│                       │
│                         └────────────────────────┘                       │
│  Scope: Friction scores reflect ticket volume from selected source(s).  │
│                                                                          │
│  [Analyze Friction]                                                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Design Tokens Reference

For mockup consistency:

```
Colors:
  Primary background:    #F5F1EB (cream)
  Card background:       #FFFFFF
  Sidebar background:    #1B4332 (dark green)
  Sidebar text:          #FFFFFF
  Sidebar hover:         #2D6A4F
  Primary button:        #1B4332 bg, #FFFFFF text
  Danger button:         #D32F2F bg, #FFFFFF text
  Success banner:        #E8F5E9 bg, #1B5E20 text
  Error banner:          #FFEBEE bg, #C62828 text
  Info banner:           #E3F2FD bg, #1565C0 text
  Border:                #E0D8CC
  Table header bg:       #F5F1EB
  Table row hover:       #EDE8E0
  Table row selected:    #D4E6DC (light green tint)

Typography:
  Page title:            18px, bold, #1A1A1A
  Section header:        14px, bold, #1A1A1A
  Body text:             13px, regular, #1A1A1A
  Secondary text:        12px, regular, #666666
  Table header:          12px, bold, #1A1A1A
  Table cell:            12px, regular, #1A1A1A

Spacing:
  Page padding:          24px
  Section gap:           16px
  Card padding:          16px
  Filter bar gap:        8px between controls

Components:
  Buttons:               8px 16px padding, 4px border-radius
  Dropdowns:             Full-width or auto, 4px border-radius, #E0D8CC border
  Text inputs:           Full-width, 8px padding, 4px border-radius, #E0D8CC border
  Cards:                 #FFFFFF bg, 1px #E0D8CC border, 8px border-radius, 2px shadow
  Filter bar:            #FFFFFF bg card with 16px padding
  Status badges:         Pill shape, 4px 8px padding, colored bg
```

---

## Mockup Checklist

| # | Mockup | Stage | Priority | States Needed |
|---|--------|-------|----------|---------------|
| 1 | Updated Clear & Close dialog | 1 | HIGH | Default |
| 2 | Full Database Reset (Settings) | 1 | HIGH | Default, Confirmation dialog, Button disabled/enabled |
| 3 | Import status banner | 1 | MEDIUM | Success (new), Success (all skipped), Error |
| 4 | Source Management in Settings | 2 | HIGH | With sources listed, Empty state |
| 5 | Create New Source dialog | 2 | HIGH | Zendesk template, Column mapping section |
| 6 | Source selector on Conversations | 2 | HIGH | Dropdown open, Source selected |
| 7 | Source selector on analytics | 2 | MEDIUM | Per-source and All Sources states |
| 8 | Data Warehouse page (full) | 3 | HIGH | Empty, Populated, Filtered, Detail expanded, TRC history |
| 9 | Updated sidebar with Warehouse | 3 | HIGH | Default |
| 10 | Conversations with multi-source | 3 | MEDIUM | Import source dropdown, Create New |
| 11 | Analytics combined mode toggle | 3 | MEDIUM | Per-source vs Combined |
| 12 | Guru source selector | 3 | LOW | Default, Filtered by source |
