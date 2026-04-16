# Post-NLP Processing

Enrichment modules that run **after** the NLP meta-analyzer and **before/during** post-scan persistence. These populate the tables created by migration 006 that the chat tools read.

## Pipeline Position

```
NLP Scan Pipeline:
  scan_orchestrator → worker_agents → nlp_meta_analyzer
                                           ↓
                                    Stage 7: _rank_findings
                                           ↓
                                    Stage 8: _select_exemplars
                                           ↓
                                  ★ ticket_theme_tagger ★  (Session 3)
                                           ↓
                                    post_scan_persist
                                           ↓
                                  ★ enriched_trends ★     (Session 3)
```

## Module Files

| File | Purpose |
|------|---------|
| `enriched_trends.py` | `compute_enriched_trends(conn, scan_id)` — aggregates ticket_index into enriched_trends table |
| `ticket_theme_tagger.py` | `tag_tickets_to_findings(conn, scan_id)` — links tickets to NLP findings via ticket_theme_tags |

## Key Design Decisions

- **Monday-anchored periods** (not `strftime('%Y-%W')`) for year-boundary safety
- **REPLACE INTO** for idempotent re-runs
- **Confidence scoring**: 1.0 for exact sub_pattern match, 0.8 for friction_type-only
