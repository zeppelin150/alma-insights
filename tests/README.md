# tests/ — Test Suite

> 100 test files, ~38.6K LOC. Unit, integration, E2E, benchmarks, and diagnostics.

## Running Tests

```bash
# Run in groups of 3-4 files (full suite hangs on Windows)
python -m pytest tests/test_X.py tests/test_Y.py tests/test_Z.py -x -v

# Skip slow/live tests
python -m pytest tests/test_pipeline_full.py -m "not slow" -v

# Kill zombie processes before E2E tests
wmic process where "commandline like '%alma_mcp_server%'" call terminate
```

**Known issues:**
- Full `python -m pytest tests/` hangs on Windows
- `test_reporting_foundation::test_bridge_fallback` — pre-existing failure
- `test_feature_integration` — live-DB-dependent failures (expected)

## Fixtures (conftest.py)

| Fixture | Description |
|---------|-------------|
| `empty_db` | DatabaseManager with all tables, zero rows |
| `seeded_db` | 100 tickets across 3 TRCs (TRC-100, TRC-200, TRC-300) |
| `seeded_conn` | Raw SQLite connection from seeded_db |
| `mock_settings` | Temp settings.yaml with safe defaults |
| `mock_gemini_client` | MagicMock Gemini client |
| `mock_claude_client` | MagicMock Claude client |
| `mock_client_factory` | Patches `build_client_for_task` to return mocks |
| `mock_guru_client` | MagicMock Guru client with canned data |

## Markers

`@slow`, `@e2e`, `@ui`, `@live_db`

## Test Files

### Pipeline & Agents

| File | Purpose |
|------|---------|
| `test_pipeline_full.py` | Full agentic NLP pipeline (mocked, no API calls) |
| `test_pipeline_stress.py` | Concurrency, resilience, edge cases |
| `test_pipeline_live.py` | Live integration with real Gemini bridge |
| `test_nlp_full_e2e.py` | Full NLP scanner E2E with 888 tickets |
| `test_acp_bridge.py` | ACPBridge unit tests (subprocess mocked) |
| `test_acp_concurrency_e2e.py` | ACP concurrency (8/16/32 workers) |
| `test_acp_perf.py` | ACP performance and reliability fixes |
| `test_acp_diagnostics.py` | ACP bridge diagnostics |
| `test_report_orchestrator.py` | Report orchestrator resilience |
| `test_scan_report.py` | Post-scan auto-report generation |

### VOC & Reporting

| File | Purpose |
|------|---------|
| `test_voc_builder.py` | VOC pipeline (planning, sampling, NLP) |
| `test_voc_build9.py` | Build 9.0 multi-perspective VOC |
| `test_voc_pipeline_live.py` | Live VOC submission route |
| `test_voc_full_e2e.py` | Full VOC E2E with live Gemini |
| `test_reporting_foundation.py` | MarkdownViewer, formatting |
| `test_reporting_suite.py` | Reporting pipeline integration |
| `test_report_structured.py` | Structured output report builder |
| `test_ai_reports_live.py` | AI reports E2E with real Gemini |

### Chat & MCP

| File | Purpose |
|------|---------|
| `test_chat_engine.py` | ChatEngine send flow, history, tools |
| `test_chat_data_layer.py` | Chat messages, views, FTS5 search |
| `test_chat_tools.py` | Chat tools fast-path validation |
| `test_chat_uplevel_diagnostics.py` | Gemini chat bug reproduction |
| `test_mcp_server.py` | MCP server tool schemas, dispatch |
| `test_mcp_tool_e2e.py` | MCP tool call path isolation |
| `test_chat_mcp_e2e.py` | Full MCP chat round-trip |
| `test_chat_mcp_models.py` | MCP chat across Gemini models |
| `test_chat_mcp_structured.py` | Structured tool calling via ACP+MCP |
| `test_chat_mcp_analysis.py` | MCP chat analysis tests |
| `test_gemini_chat_pipeline.py` | Gemini chat pipeline E2E |

### Data Layer

| File | Purpose |
|------|---------|
| `test_connection_factory.py` | SQLite connection factory |
| `test_settings_manager.py` | Settings migration, load/save |
| `test_csv_reformatter.py` | CSV column mapping + ingest pipeline |
| `test_incremental_import.py` | Incremental import foundation |
| `test_feature_integration.py` | Pre-1.0 feature integration |
| `test_filter_engine.py` | Filter engine query validation |
| `test_entity_normalizer.py` | Entity normalization pipeline |
| `test_integrity_checker.py` | Post-scan data integrity |
| `test_post_nlp.py` | Post-NLP enriched trends |
| `test_embedding_gemma.py` | Embedding model (mocked) |
| `test_embed_live.py` | Live embedding building |
| `test_watchlist_engine.py` | Watchlist rules and alerts |

### Integrations

| File | Purpose |
|------|---------|
| `test_guru_client.py` | Guru API auth and integration |
| `test_guru_pipeline.py` | Guru friction pipeline (sync, coverage, gaps) |
| `test_guru_card_viewer.py` | GuruCardViewer widget |
| `test_guru_workbench.py` | GuruWorkbenchPanel helpers |
| `test_source_monitor.py` | Zendesk source monitor |
| `test_source_types.py` | Source abstraction interface |
| `test_source_warehouse.py` | Source warehouse ingestion |
| `test_claude_client.py` | Claude client (generate, auth, streaming) |
| `test_model_registry.py` | Model registry singleton |
| `test_hardening.py` | Task routing and factory tests |

### Migrations & Schema

| File | Purpose |
|------|---------|
| `test_migration_006.py` | Migration 006 (hybrid chat tables) |
| `test_migration_idempotent.py` | Idempotent migration on existing DBs |
| `test_updater.py` | Auto-update version parsing |

### UI & Regression

| File | Purpose |
|------|---------|
| `test_qt_regression.py` | Qt pages, widgets, error handling |
| `test_build11_regression.py` | Build 11.0 regression |
| `test_phase5_regression.py` | Phase 5 cross-page consolidation |
| `test_phase5_release.py` | Release packaging, updates tab |
| `test_bug_clear_close.py` | Clear & close behavior |
| `test_bug_sidebar_layout.py` | Sidebar layout/collapse |
| `test_bug_warehouse_filters.py` | Warehouse filter wiring |
| `test_bug_warehouse_ui.py` | Warehouse page UI |

### Multi-Source Architecture

| File | Purpose |
|------|---------|
| `test_stage2_source_registry.py` | Source registry + schema builder |
| `test_stage3_redaction_analytics.py` | Redaction engine + analytics |
| `test_stage4_data_warehouse.py` | Data warehouse page, virtual scroll |
| `test_stage5_multi_source.py` | Multi-source, Kodif import |
| `test_s6a_dual_write.py` | Dual-write gap fix |
| `test_s6b_selector_wiring.py` | Source selector wiring |
| `test_s6c_guru_clear_e2e.py` | Guru source tagging + clear |
| `test_s7b_default_dual_write.py` | Default import dual-write |
| `test_integration_hybrid.py` | Hybrid chat + DB integration |

### Benchmarks & Diagnostics

| File | Purpose |
|------|---------|
| `bench_53.py` | Build 5.3 scan benchmark (3 workers) |
| `bench_scan.py` | Build 6.1 scanner benchmark |
| `bench_voc.py` | Build 6.3 VOC benchmark |
| `debug_classify.py` | Batch classification output diagnostic |
| `debug_live_scan.py` | Real scan diagnostic |
| `debug_mixed_batch.py` | Mixed-TRC batch output |
| `debug_truncation.py` | Output truncation threshold |
| `run_csv_import_integration.py` | CSV import E2E pipeline |
| `run_e2e_full_scan.py` | Production E2E scan with coverage |
| `trace_memory_import.py` | Import memory bloat isolation |
| `trace_memory_app.py` | Full app memory trace |
| `diag_mcp_call.py` | Minimal MCP tool call diagnostic |
| `diag_mcp_store.py` | MCP store_classification diagnostic |
| `acp_protocol_probe.py` | ACP protocol probe |
| `acp_prompt_probe.py` | ACP prompt response capture |

### Unit Tests (tests/unit/)

| File | Purpose |
|------|---------|
| `test_report_builder.py` | Report builder formatters |
| `test_nlp_meta_analyzer.py` | NLP meta-analysis |
| `test_incident_engine.py` | Poisson parameter computation |
| `test_trending_engine.py` | Trending engine analysis |
| `test_ticket_index_writer.py` | Ticket index dedup + upsert |
| `test_post_scan_persist.py` | Post-scan snapshot/trend/insight |
| `test_post_report_persist.py` | Post-report CRUD |
| `test_chat_session.py` | Chat session CRUD |
| `test_context_injector.py` | Context injector integration |
| `test_alma_query.py` | Alma query CLI tool |
| `test_csv_ingestion.py` | CSV encoding + entry point |
| `test_clear_session.py` | Session clear + data survival |
