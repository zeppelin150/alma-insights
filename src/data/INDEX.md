# src/data/

> Data layer for Alma Insights: database management, ingestion pipelines, NLP analysis engines, statistical anomaly detection, Guru KB integration, Zendesk connectivity, report generation, and configuration persistence. This package contains all business logic that does not depend on the UI framework.

## Module Index

### ab_analysis.py
> Statistical comparison of two ticket datasets using chi-squared, Mann-Whitney U, and independent t-tests.

**Public API:**
- `compute_dataset_stats(db, dataset_id, date_start="", date_end="") -> dict` — compute comprehensive statistics (ticket count, TRC distribution, CSAT, sentiment, resolution times, TF-IDF) for a single dataset
- `compare_datasets(stats_a, stats_b) -> dict` — run chi-squared, Mann-Whitney U, t-test, and TF-IDF rank comparisons between two dataset stats dicts
- `build_ab_data_block(stats_a, stats_b, comparison) -> dict` — build a combined data block dict for prompt variable replacement

**Depends on:** `src.data.trending_engine`
**Depended by:** `src.data.ab_report_pipeline`, `src.ui.pages.ab_compare`, `tests.test_feature_integration`

---

### ab_report_pipeline.py
> Multi-phase A/B comparison pipeline: dual data assembly, Gemini narrative generation, and tech summary assembly.

**Public API:**
- `ABReportPipeline.__init__(self, db, gemini_client=None, progress_cb=None)` — initialize with DatabaseManager, optional Gemini client, and progress callback
- `ABReportPipeline.run(self, prompt_data: dict, config: dict) -> dict` — execute the full A/B comparison pipeline returning report_md, tech_summary_md, data_block, stats, comparison, and phases_completed

**Depends on:** `src.data.ab_analysis`, `src.data.report_builder`, `src.data.tech_summary_builder`
**Depended by:** `src.ui.pages.ab_compare`

---

### ai_report_pipeline.py
> Multi-phase AI report generation pipeline: data assembly, Gemini report generation, analyst reports, and tech summary.

**Public API:**
- `AIReportPipeline.__init__(self, db, gemini_client=None, progress_cb=None)` — initialize with DatabaseManager, optional Gemini client, and progress callback
- `AIReportPipeline.run(self, prompt_data: dict, date_start: str, date_end: str, trc_filter: str = None, scan_id: str = None) -> dict` — execute the full pipeline returning report_md, tech_summary_md, analyst_md, data_block, and phases_completed

**Depends on:** `src.data.report_builder`, `src.data.analyst_report_formatter`, `src.data.tech_summary_builder`
**Depended by:** `src.ui.pages.ai_reports`

---

### analyst_report_formatter.py
> Parses JSON content from the analyst_reports table into readable markdown for display across reporting pages.

**Public API:**
- `format_analyst_reports_as_markdown(reports: list[dict]) -> str` — convert a list of analyst_reports rows into a single markdown document
- `get_latest_analyst_summary(db, scan_id: str = None) -> tuple[str, dict]` — get formatted markdown and raw metrics for the latest completed scan

**Depends on:** (none from src/)
**Depended by:** `src.data.ai_report_pipeline`, `src.ui.pages.smart_reporting`, `src.ui.pages.trc_analytics`, `tests.test_reporting_foundation`, `tests.test_reporting_suite`

---

### compound_discovery.py
> Automatically detects statistically significant multi-word terms (bigrams, trigrams) using PMI (Pointwise Mutual Information).

**Public API:**
- `discover_compounds(conversations, existing_compounds=None, min_cooccurrence=5, min_pmi=3.0) -> list` — discover significant multi-word phrases from conversation text, returning dicts with phrase, normalized, frequency, pmi_score
- `persist_discoveries(db, candidates)` — save discovered compounds to the database
- `get_active_compounds(db) -> dict` — return merged dict of hardcoded COMPOUND_TERMS plus user-approved discovered compounds

**Depends on:** `src.data.trending_engine`
**Depended by:** `src.data.trending_engine`, `tests.test_feature_integration`

---

### concept_map.py
> Maps synonymous terms to canonical concept IDs for text normalization before TF-IDF vectorization.

**Public API:**
- `DOMAIN_CONCEPTS` — dict mapping canonical concept names to sets of synonym tokens
- `build_concept_index() -> dict` — build reverse lookup from token to canonical concept
- `apply_concept_normalization(tokens: list, concept_index: dict) -> list` — replace synonym tokens with their canonical concept ID

**Depends on:** (none from src/)
**Depended by:** `src.data.trending_engine`, `tests.test_feature_integration`

---

### connection_factory.py
> Centralized SQLite connection factory. Every database connection in Alma Insights MUST go through `get_connection()`. Direct `sqlite3.connect()` calls are banned.

**Public API:**
- `get_connection(db_path=None, readonly=False) -> sqlite3.Connection` — create properly configured connection (WAL, busy_timeout=30s, FK=ON, row_factory=Row)
- `atomic(conn) -> contextmanager` — transaction context manager with auto-rollback. Cannot nest.

**Depends on:** (none — stdlib only)
**Depended by:** `src.data.db_manager`, `src.services.chat_engine`, `src.services.chat_session`, `src.services.clear_session`, `src.agents.scan_orchestrator`, `src.agents.worker_agent`

---

### conversation_rebuild.py
> Post-ingestion job that reads raw_ingestion_rows, groups by ticket_id, sorts chronologically with NULL timestamp handling, and writes rebuilt conversations.

**Public API:**
- `rebuild_conversations(db, logger: Optional[RunLogger] = None, progress_callback: Optional[Callable] = None) -> dict` — rebuild conversations from raw_ingestion_rows table, returning stats dict

**Depends on:** `src.data.run_logger`, `src.data.rebuild_utils`
**Depended by:** `src.data.lightdash_client`

---

### csv_ingestion.py
> Maps Lightdash CSV exports to internal database schema and handles conversation rebuilding from comment-level rows.

**Public API:**
- `COLUMN_MAP` — dict mapping normalized Lightdash CSV headers to internal field names
- `ingest_csv(file_path, db, progress_callback=None, dataset_id=None, column_override=None) -> dict` — ingest a Lightdash CSV export into the database, returning ingestion stats

**Depends on:** `src.data.rebuild_utils`, `src.data.entity_extractor`, `src.data.ngram_matcher`
**Depended by:** `src.ui.pages.ab_compare`, `src.ui.pages.conversation_search`, `src.agents.csv_reformatter`, `tests.run_csv_import_integration`, `tests.test_csv_reformatter`, `tests.test_feature_integration`, `tests.trace_memory_import`, `tests.unit.test_csv_ingestion`

---

### db_manager.py
> SQLite backend for ticket storage, conversation rebuilding, full-text search, and all analytical tables.

**Public API:**
- `DatabaseManager.__init__(self, db_path=None)` — initialize with optional custom DB path
- `DatabaseManager.conn` — property returning the SQLite connection (lazy-initialized with WAL mode)
- `DatabaseManager.initialize(self)` — create all tables and indexes
- `DatabaseManager.close(self)` — close the database connection
- `DatabaseManager.upsert_ticket(self, ticket: dict)` — insert or update a ticket record
- `DatabaseManager.upsert_comment(self, comment: dict)` — insert or update a comment record
- `DatabaseManager.upsert_conversation(self, conv: dict)` — insert or update a conversation record
- `DatabaseManager.commit(self)` — commit pending transactions
- `DatabaseManager.rebuild_fts_index(self)` — rebuild the full-text search index
- `DatabaseManager.get_trc_codes(self)` — get list of distinct TRC codes
- `DatabaseManager.search_conversations(self, keyword, trc_code, date_from, date_to, ...)` — search conversations with filtering
- `DatabaseManager.get_conversation(self, ticket_id)` — get a single conversation by ticket ID
- `DatabaseManager.save_report(self, page, parameters, summary, ...)` — persist a generated report
- `DatabaseManager.log_gemini_usage(self, date, source, tokens_in, tokens_out, ...)` — log Gemini API call metrics
- `DatabaseManager.load_demo_data(self)` — populate database with demo data

**Depends on:** `src.data.demo_data`
**Depended by:** `src.ui.main_window`, `src.ui.dialogs.ingestion_dialog`, `src.ui.pages.ab_compare`, `src.ui.pages.ai_reports`, `src.ui.pages.conversation_search`, `src.ui.pages.incidents_page`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `src.ui.widgets.chat_widget`, `src.agents.scan_orchestrator`, `src.data.smart_pipeline`, `tests.conftest`, `tests.run_e2e_full_scan`, `tests.bench_voc`, `tests.run_csv_import_integration`, `tests.test_csv_reformatter`, `tests.test_ai_reports_live`, `tests.test_pipeline_full`, `tests.test_phase5_regression`, `tests.test_reporting_foundation`, `tests.test_feature_integration`, `tests.test_voc_builder`, `tests.trace_memory_import`, `tests.test_voc_full_e2e`

---

### demo_data.py
> Generates ~160 realistic sample ticket conversations for development and demo purposes.

**Public API:**
- `generate_demo_data(db)` — generate and insert demo conversations into the database

**Depends on:** (none from src/)
**Depended by:** `src.data.db_manager`

---

### embedding_engine.py
> Dense vector encoding of conversations for semantic search and clustering using sentence-transformers (all-MiniLM-L6-v2), with HIPAA air-gap environment variables.

**Public API:**
- `is_available() -> bool` — check if sentence-transformers is installed
- `embed_texts(texts: list, batch_size: int = 64) -> np.ndarray` — encode texts into normalized embedding vectors
- `semantic_search(query: str, corpus_embeddings: np.ndarray, corpus_ids: list, top_k: int = 20) -> list` — find the top_k most similar corpus entries to a query
- `compute_embedding_clusters(embeddings: np.ndarray, n_clusters: int = 0) -> dict` — cluster embeddings using KMeans
- `verify_model_bundled() -> bool` — check that the embedding model is available locally

**Depends on:** (none from src/)
**Depended by:** `src.data.trending_engine`, `tests.test_feature_integration`

---

### entity_extractor.py
> Dictionary-based payer and product area extraction from ticket text, with optional Gemini-enhanced batch extraction.

**Public API:**
- `load_entity_dictionaries() -> tuple` — load payer and product area dictionaries from config JSON files
- `extract_entities(text, payer_dict, product_dict) -> dict` — fast dictionary-based entity extraction returning matched payers and product areas
- `hash_email(email) -> str` — one-way SHA-256 hash truncated to 16 chars for repeat contact detection
- `extract_entities_ai_batch(unmatched_tickets, gemini_client) -> list` — Gemini-enhanced extraction for tickets where Layer 1 found no match

**Depends on:** (none from src/)
**Depended by:** `src.data.csv_ingestion`, `src.data.watchlist_engine`, `tests.trace_memory_import`

---

### entity_normalizer.py
> Extracts and normalizes entities from NLP classifications into the `ticket_entities_normalized` table, solving JSON indexing and case inconsistency.

**Public API:**
- `normalize_entities_for_scan(conn, scan_id) -> int` — parse entities_json from classifications, case-normalize, write to normalized table
- `query_by_entity(conn, entity_type, value) -> list` — find tickets by entity type and value
- `get_entity_distribution(conn) -> dict` — return count of each entity value grouped by type

**Depends on:** (none — stdlib only)
**Depended by:** `src.services.post_scan_persist`, `tests.test_entity_normalizer`

---

### gemini_setup.py
> Cross-platform detection, installation, and authentication helpers for the Gemini CLI (Node.js, npm, Gemini CLI).

**Public API:**
- `find_node() -> str | None` — return the full path to the node binary
- `get_node_version(node_path: str) -> str | None` — return node version string
- `node_meets_requirements(node_path: str, min_major: int = 20) -> bool` — check if node version meets minimum
- `find_npm(node_path: str | None = None) -> str | None` — return the full path to the npm binary
- `find_gemini_cli() -> str | None` — return the full path to the gemini binary
- `install_gemini_cli(npm_path: str, progress_callback=None) -> tuple[bool, str]` — run npm install of Gemini CLI
- `launch_gemini_auth(gemini_path: str) -> None` — open terminal for Google OAuth sign-in
- `verify_gemini_auth(gemini_path: str) -> tuple[bool, str]` — verify Gemini CLI is installed and authenticated
- `get_node_download_url() -> str` — return the Node.js download page URL
- `get_gemini_cli_package() -> str` — return the npm package name for the Gemini CLI

**Depends on:** (none from src/)
**Depended by:** `src.ui.pages.settings_page`

---

### guru_client.py
> REST client for the Guru Knowledge Base API v1, supporting article sync, search, and gated content updates.

**Public API:**
- `GuruClient.__init__(self, email: str, api_token: str)` — initialize with Guru credentials
- `GuruClient.test_connection(self) -> bool` — verify credentials via GET /whoami
- `GuruClient.list_collections(self) -> list[dict]` — list all Guru collections
- `GuruClient.list_cards(self, collection_id: str | None = None) -> list[dict]` — list cards, optionally filtered by collection
- `GuruClient.get_card(self, card_id: str) -> dict` — fetch a single card with full content
- `GuruClient.search_cards(self, query: str) -> list[dict]` — search cards by text query
- `GuruClient.update_card(self, card_id: str, content: str, title: str | None = None) -> dict` — update card content (human-gated only)
- `GuruClient.create_card(self, collection_id: str, title: str, content: str) -> dict` — create a new card (human-gated only)
- `GuruClient.load_credentials() -> tuple[str, str]` — load credentials from pat_store
- `GuruClient.save_credentials(email: str, api_token: str) -> bool` — persist credentials
- `GuruClient.is_configured() -> bool` — check if credentials are saved
- `GuruAuthError` — raised on 401 Unauthorized
- `GuruAPIError` — raised for non-auth API errors

**Depends on:** `src.data.pat_store`
**Depended by:** `src.data.guru_content_pipeline`, `src.data.guru_friction_pipeline`, `src.ui.main_window`, `src.ui.pages.guru_page`, `tests.test_guru_client`, `tests.test_guru_pipeline`

---

### guru_content_pipeline.py
> Loop B of Guru integration: LLM content generation (write-gated) for proposed rewrites and new articles, saved as drafts pending human approval.

**Public API:**
- `GuruContentPipeline.__init__(self, db_manager, guru_client: GuruClient)` — initialize with DatabaseManager and GuruClient
- `GuruContentPipeline.propose_rewrite(self, card_id: str, friction_types: list[str], llm_client=None) -> dict` — generate proposed rewrite for existing card
- `GuruContentPipeline.propose_new_article(self, friction_type: str, llm_client=None) -> dict` — generate new article for uncovered friction
- `GuruContentPipeline.approve_and_push(self, draft_id: int, approved_by: str = "user", collection_id: str | None = None) -> bool` — push approved draft to Guru (human-gated)
- `GuruContentPipeline.reject(self, draft_id: int) -> bool` — mark draft as rejected
- `GuruContentPipeline.get_pending_drafts(self) -> list[dict]` — all drafts with status='pending'
- `GuruContentPipeline.get_all_drafts(self) -> list[dict]` — all drafts, most recent first

**Depends on:** `src.data.guru_client`, `src.data.guru_effectiveness`
**Depended by:** `src.ui.main_window`, `tests.test_guru_pipeline`

---

### guru_effectiveness.py
> Measures whether Guru article changes actually reduce ticket volume using Poisson significance testing.

**Public API:**
- `GuruEffectivenessTracker.__init__(self, db_manager)` — initialize with DatabaseManager
- `GuruEffectivenessTracker.record_baseline(self, card_id: str, friction_type: str, pre_window_days: int = 14)` — record pre-change volume baseline when a draft is pushed
- `GuruEffectivenessTracker.measure_effectiveness(self, days_since_change: int = 14) -> list[dict]` — measure post-change volume for all tracked changes
- `GuruEffectivenessTracker.get_effectiveness_report(self) -> list[dict]` — summary of all effectiveness measurements

**Depends on:** (none from src/)
**Depended by:** `src.data.guru_content_pipeline`, `src.ui.main_window`, `src.llm.claude_tools`, `tests.test_guru_pipeline`

---

### guru_friction_pipeline.py
> Loop A of Guru integration: read-only friction analysis that syncs articles, compares against friction types, and identifies coverage gaps.

**Public API:**
- `GuruFrictionPipeline.__init__(self, db_manager, guru_client: GuruClient)` — initialize with DatabaseManager and GuruClient
- `GuruFrictionPipeline.sync_articles(self) -> int` — pull all Guru cards into guru_articles table
- `GuruFrictionPipeline.analyze_coverage(self, scan_id: str = "", llm_client=None) -> list[dict]` — compare friction types against Guru articles and score coverage
- `GuruFrictionPipeline.get_gap_report(self) -> list[dict]` — LEFT JOIN sub_patterns against coverage, sorted by gap score
- `GuruFrictionPipeline.analyze_friction_deep(self, friction_type: str, scan_id: str = "", llm_client=None, progress_cb=None) -> dict` — multi-agent deep analysis of a single friction type
- `GuruFrictionPipeline.compute_friction_scores(self)` — update guru_articles.friction_score from coverage analysis

**Depends on:** `src.data.guru_client`, `src.data.guru_graph_builder`
**Depended by:** `src.ui.main_window`, `src.llm.claude_tools`, `tests.test_guru_pipeline`, `tests.test_hardening`

---

### guru_graph_builder.py
> Builds and traverses a relationship graph between Guru KB cards using collection, cross-reference, and shared-friction edges.

**Public API:**
- `GuruGraphBuilder.__init__(self, db, keyword_domains: dict | None = None)` — initialize with DatabaseManager and optional keyword-domain map
- `GuruGraphBuilder.rebuild(self) -> dict` — clear and rebuild the full card graph and domain tags
- `GuruGraphBuilder.get_constellation(self, card_id: str, max_hops: int = 2) -> list[dict]` — BFS from a seed card returning reachable cards within max_hops
- `GuruGraphBuilder.get_domain(self, domain: str) -> list[dict]` — get all cards tagged with a domain
- `GuruGraphBuilder.list_domains(self) -> list[dict]` — list all domains with card counts

**Depends on:** (none from src/)
**Depended by:** `src.data.guru_friction_pipeline`, `tests.test_hardening`

---

### incident_engine.py
> Two-tier Poisson statistical process control for TRC ticket rates with CUSUM drift detection.

**Public API:**
- `run_incident_scan(db, target_date: str = None, date_from: str = None, progress_callback=None) -> dict` — full incident scan across the date range, returning trc_results, new_flags, and open_flags_total
- `get_open_flags(db, theta_level=None, flag_type=None, limit=100) -> list` — get open incident flags, optionally filtered
- `get_flag_history(db, trc_code=None, days=90, limit=300) -> list` — get incident flag history
- `acknowledge_flag(db, flag_id, notes="")` — mark a flag as acknowledged
- `resolve_flag(db, flag_id, notes="")` — mark a flag as resolved
- `mark_false_positive(db, flag_id, notes="")` — mark a flag as false positive
- `correlate_flag_with_interventions(db, flag, lookback_days=14) -> list` — check if a flag correlates with a recent intervention

**Depends on:** (none from src/)
**Depended by:** `src.data.report_builder`, `src.data.smart_pipeline`, `src.data.trending_engine`, `src.ui.pages.incidents_page`, `tests.test_feature_integration`

---

### import_mode.py
> Enum and converter for CSV/Lightdash import modes.

**Public API:**
- `ImportMode` — enum with `INCREMENTAL`, `FULL_REFRESH` values
- `mode_from_ui_text(text) -> ImportMode` — convert UI display text to enum

**Depends on:** (none — stdlib only)
**Depended by:** `src.data.csv_ingestion`, `src.data.lightdash_client`

---

### import_tracker.py
> Logs import runs and provides dedup gate for incremental imports via the `import_runs` table.

**Public API:**
- `start_import_run(conn, source, mode, file_name) -> str` — create import_runs record, return run_id
- `complete_import_run(conn, run_id, stats)` — mark run completed with metrics
- `fail_import_run(conn, run_id, error_msg)` — mark run failed
- `get_existing_ticket_ids(conn, table_prefix=None) -> set` — fetch existing ticket IDs for dedup
- `filter_new_tickets(existing_ids, incoming_rows, id_column) -> list` — filter to new tickets only

**Depends on:** (none — stdlib only)
**Depended by:** `src.data.csv_ingestion`, `src.data.conversation_rebuild`, `tests.test_incremental_import`

---

### integrity_checker.py
> Post-scan data integrity verification detecting silent inconsistencies in embeddings, counts, and schema.

**Public API:**
- `IntegrityIssue` — dataclass with `severity`, `check_name`, `message`, `expected`, `actual`
- `check_data_integrity(conn) -> list[IntegrityIssue]` — run all integrity checks (conv vs index count, embedding freshness, FTS row count, orphaned classifications, entity case consistency)
- `run_post_scan_integrity(conn, scan_id) -> dict` — post-scan verification for a specific scan

**Depends on:** (none — stdlib only)
**Depended by:** `tests.test_integrity_checker`

---

### job_queue.py
> Sequential job executor using QThread that runs one worker at a time, preventing concurrent DB access and UI freezes.

**Public API:**
- `JobDescriptor` — dataclass describing a unit of work (job_id, name, description, create_worker, state, error_msg)
- `CallableWorker` — generic QThread wrapper for plain callables with finished_result, error, and progress signals
- `JobQueue.__init__(self, parent=None)` — initialize the central sequential job queue
- `JobQueue.submit(self, job: JobDescriptor) -> None` — add a single job to the queue
- `JobQueue.submit_batch(self, jobs: list[JobDescriptor]) -> None` — add multiple jobs
- `JobQueue.cancel_pending(self, job_id: str) -> None` — remove a queued job by ID
- `JobQueue.cancel_all(self) -> None` — cancel everything and clear queue
- `JobQueue.is_running(self) -> bool` — check if a job is currently running
- `JobQueue.pending_count(self) -> int` — count of pending jobs
- `JobQueue.get_job_list(self) -> list[dict]` — return combined history + current + pending for overlay
- `JobQueue.mark_job_failed(self, job_id: str, error_msg: str) -> None` — mark current job as failed from error callback

**Depends on:** (none from src/)
**Depended by:** `src.ui.main_window`, `src.ui.pages.conversation_search`

---

### lightdash_client.py
> Saved-chart ingestion from Lightdash: URL parsing, preflight checks, chunked data pull with retry/backoff, and SQLite persistence.

**Public API:**
- `ParsedChartURL` — result of parsing a Lightdash saved-chart URL
- `PreflightResult` — result of a single preflight check
- `run_preflight(pat, chart_url, ...) -> list[PreflightResult]` — run all preflight checks
- `run_ingestion(pat, chart_url, date_start, date_end, db, ...) -> dict` — full ingestion pipeline

**Depends on:** `src.data.run_logger`, `src.data.pat_store`, `src.data.conversation_rebuild`
**Depended by:** `src.data.lightdash_mock`, `src.ui.dialogs.ingestion_dialog`

---

### lightdash_mock.py
> Spoofs all Lightdash API interactions for test/debug mode with realistic fake data matching the real column schema.

**Public API:**
- `generate_mock_rows(date_start: str, date_end: str, max_rows: int = 500) -> list` — generate mock Lightdash API rows
- `MockHTTPClient` — drop-in replacement for the real HTTP client with configurable failure modes
- `run_mock_preflight(logger=None) -> list` — run a spoofed preflight with simulated delays
- `run_mock_ingestion(db, date_start, date_end, logger=None, progress_callback=None) -> dict` — run spoofed ingestion using MockHTTPClient

**Depends on:** `src.data.run_logger`, `src.data.lightdash_client`
**Depended by:** `src.ui.dialogs.ingestion_dialog`

---

### memory_profiler.py
> Lightweight diagnostic profiler using tracemalloc and gc for diagnosing memory bloat.

**Public API:**
- `MemoryProfiler.start()` — start tracemalloc tracking (call once at app startup)
- `MemoryProfiler.is_started() -> bool` — check if profiler is running
- `MemoryProfiler.snapshot() -> dict` — take a memory snapshot and return a structured report
- `MemoryProfiler.format_report(report=None) -> str` — return a human-readable text report

**Depends on:** (none from src/)
**Depended by:** `src.ui.pages.settings_page`

---

### ngram_matcher.py
> Provisional sub-pattern classification using stored n-gram fingerprints for near-real-time sub-pattern tracking between Gemini scans.

**Public API:**
- `NgramMatcher.__init__(self, db, match_threshold=None)` — initialize with DatabaseManager and optional match threshold
- `NgramMatcher.classify_ticket(self, ticket_id, trc, text) -> tuple` — score ticket text against active sub-patterns, returning (matched_pattern_id, match_score)
- `NgramMatcher.classify_batch(self, ticket_ids)` — classify multiple tickets and store to provisional_classifications
- `NgramMatcher.clear_cache(self)` — clear the TRC n-gram cache

**Depends on:** (none from src/)
**Depended by:** `src.data.csv_ingestion`, `tests.trace_memory_import`

---

### nlp_meta_analyzer.py
> NLP Layer 2 meta-analyzer that aggregates classifications, manages the learning sub-taxonomy, extracts n-grams, cross-references statistical engines, and generates ranked findings.

**Public API:**
- `NLPMetaAnalyzer.__init__(self, db)` — initialize with DatabaseManager
- `NLPMetaAnalyzer.run_analysis(self, scan_id)` — main entry point called when scan status is 'scan_complete'

**Depends on:** (none from src/)
**Depended by:** `src.agents.scan_orchestrator`, `src.data.smart_pipeline`, `src.ui.pages.trc_analytics`, `tests.unit.test_nlp_meta_analyzer`

---

### nlp_synthesis.py
> NLP Layer 3 synthesizer that produces qualia-rich narrative from NLP scan findings via Gemini.

**Public API:**
- `NLPSynthesizer.__init__(self, db, gemini_client)` — initialize with DatabaseManager and GeminiClient
- `NLPSynthesizer.synthesize_findings(self, scan_id, max_findings=10) -> str` — synthesize top N findings into a narrative report
- `NLPSynthesizer.synthesize_single_finding(self, finding_id, user_question=None) -> str` — deep-dive on a single finding for chat drilldown

**Depends on:** `src.gemini.gemini_client`
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.widgets.chat_widget`

---

### pat_store.py
> Persists Lightdash PAT and other credentials securely in the user's home directory (~/.alma-insights/credentials.json).

**Public API:**
- `save_pat(pat: str) -> bool` — save PAT to disk
- `load_pat() -> str` — load PAT from disk
- `has_pat() -> bool` — check if a PAT is saved
- `delete_pat() -> bool` — remove saved PAT
- `redact_pat(pat: str) -> str` — redact PAT for display
- `save_setting(key: str, value) -> bool` — save a non-sensitive setting to the config file
- `load_setting(key: str, default=None)` — load a non-sensitive setting

**Depends on:** (none from src/)
**Depended by:** `src.data.guru_client`, `src.data.lightdash_client`, `src.data.scan_server_manager`, `src.data.smart_pipeline`, `src.data.zendesk_client`, `src.gemini.gemini_client`, `src.gemini.client_factory`, `src.agents.gemini_bridge_wrapper`, `src.ui.pages.settings_page`, `src.ui.pages.source_monitor_page`

---

### product_gap_engine.py
> Detects product areas with cross-cutting issues: high TRC spread, rising volume, declining sentiment, and repeat contacts.

**Public API:**
- `detect_product_gaps(db, date_start, date_end) -> list` — detect product gaps by cross-cutting product area analysis, returning flagged product areas with gap scores

**Depends on:** (none from src/)
**Depended by:** `src.data.report_builder`, `tests.test_feature_integration`

---

### rebuild_utils.py
> Shared logic for timestamp parsing, NULL placement, chronological sorting, and role normalization used by both CSV and API ingestion paths.

**Public API:**
- `parse_timestamp(ts_raw) -> Optional[datetime]` — parse a raw timestamp string into datetime
- `normalize_role(raw_role: str) -> str` — normalize author role string to 'customer', 'agent', or 'bot'
- `sort_events_chronologically(events: List[dict]) -> List[dict]` — sort events chronologically with NULL timestamp handling, assigning synthetic timestamps

**Depends on:** (none from src/)
**Depended by:** `src.data.csv_ingestion`, `src.data.conversation_rebuild`, `tests.test_feature_integration`, `tests.trace_memory_import`

---

### redaction_engine.py
> PHI/PII scrubbing that preserves business entities (insurance names, TRC codes, acronyms). Identifies "keep" regions from allowlist, applies detection patterns, skips overlapping matches.

**Public API:**
- `RedactionEngine(allowlist_path=None, patterns_path=None)` — initialize with config files (defaults to `config/entities/phi_allowlist.json` and `config/redaction_patterns.json`)
- `RedactionEngine.scrub(text) -> str` — remove PHI/PII, replace with tokens like `[SSN]`, `[EMAIL]`, `[PHONE]`
- `RedactionEngine.scrub_dict(record, fields) -> dict` — scrub specific fields in a dict

**Depends on:** (none — stdlib only)
**Depended by:** `src.gemini.gemini_client`, `src.llm.claude_client`, `tests.test_stage3_redaction_analytics`

---

### report_builder.py
> Pre-computes all analytics into a data block for prompt variable replacement so Gemini receives results, not raw data.

**Public API:**
- `build_data_block(db, date_start, date_end, trc_filter=None, dataset_id=None) -> dict` — pre-compute all analytics into a dict mapping to prompt variables
- `format_data_block_for_prompt(block) -> str` — format data block dict as human-readable text for prompt injection
- `replace_prompt_variables(prompt_text, block, db=None) -> str` — replace {variable} placeholders in prompt text with data block values
- `build_windowed_data_blocks(db, date_start, date_end, ...) -> list` — build data blocks for multiple time windows
- `format_temporal_context(windowed_blocks) -> str` — format windowed data blocks as temporal context text

**Depends on:** `src.data.trending_engine`, `src.data.incident_engine`, `src.data.product_gap_engine`
**Depended by:** `src.data.ab_report_pipeline`, `src.data.ai_report_pipeline`, `src.data.smart_pipeline`, `src.data.voc_builder`, `src.ui.pages.ab_compare`, `src.ui.pages.ai_reports`, `src.ui.widgets.chat_widget`, `tests.test_ai_reports_live`, `tests.test_feature_integration`, `tests.unit.test_report_builder`

---

### run_logger.py
> Timestamped, structured logging for preflight and ingestion runs with JSONL file output, TXT summary, and Qt signal for in-app live view.

**Public API:**
- `RunLogger.__init__(self, dataset_name: str = "", run_type: str = "ingestion")` — initialize a new run logger
- `RunLogger.log(self, phase, action, status, detail, ...)` — log a structured event
- `RunLogger.start(self, detail="")` — log run start
- `RunLogger.end(self, status="OK", detail="")` — log run end and write summary
- `RunLogger.preflight(self, action, ...)` — log a preflight event
- `RunLogger.request(self, action, ...)` — log an HTTP request event
- `RunLogger.backoff(self, action, attempt, backoff_ms, ...)` — log a retry/backoff event
- `RunLogger.page(self, page_num, rows, ...)` — log a pagination event
- `RunLogger.error(self, action, detail, ...)` — log an error
- `RunLogger.get_display_lines(self) -> List[str]` — return all formatted display lines

**Depends on:** (none from src/)
**Depended by:** `src.data.lightdash_client`, `src.data.lightdash_mock`, `src.data.conversation_rebuild`, `src.ui.dialogs.ingestion_dialog`

---

### scan_ledger.py
> Builds structured markdown summaries from PHI-free tables for use as context when routing tasks to Claude.

**Public API:**
- `build_current_ledger(db, scan_id: str) -> str` — build a structured markdown ledger for a single scan
- `build_historical_ledger(db, num_scans: int = 5) -> str` — build a multi-scan trend view from PHI-free tables

**Depends on:** (none from src/)
**Depended by:** `src.llm.claude_tools`, `tests.test_hardening`

---

### scan_report_builder.py
> Assembles a structured markdown report from DB data after an NLP scan completes (pure Python, no LLM calls).

**Public API:**
- `ScanReportBuilder.__init__(self, db_path: str)` — initialize with database path
- `ScanReportBuilder.build_and_save(self, scan_id: str) -> None` — build report markdown and persist to analysis_reports

**Depends on:** (none from src/)
**Depended by:** `tests.test_scan_report`

---

### scan_server_manager.py
> Manages the local Node.js NLP scan server lifecycle with HTTPS REST API communication, ephemeral ports, and per-run auth tokens.

**Public API:**
- `ScanServerManager.__init__(self, db_path=None)` — initialize with optional database path
- `ScanServerManager.ensure_running(self) -> bool` — start server if not running, return True if healthy
- `ScanServerManager.start_scan(self, date_start, date_end, trc_filter=None, batch_size=700, budget_cap=50.0, mode="full") -> dict` — POST /scan/start
- `ScanServerManager.get_status(self, scan_id: str) -> dict` — GET /scan/{id}/status
- `ScanServerManager.pause_scan(self, scan_id: str) -> dict` — POST /scan/{id}/pause
- `ScanServerManager.resume_scan(self, scan_id: str, budget_cap=None) -> dict` — POST /scan/{id}/resume
- `ScanServerManager.cancel_scan(self, scan_id: str) -> dict` — POST /scan/{id}/cancel
- `ScanServerManager.get_history(self) -> list` — GET /scan/history

**Depends on:** `src.data.settings_manager`, `src.data.pat_store`
**Depended by:** (none found)

---

### scan_worker.py
> Detached subprocess for batch NLP classification via Gemini CLI, communicating exclusively via SQLite.

**Public API:**
- `ScanWorker.__init__(self, db_path, scan_id, worker_id=0)` — initialize with database path, scan ID, and worker ID

**Depends on:** `src.gemini.gemini_client`
**Depended by:** (none found — invoked as `python -m src.data.scan_worker`)

---

### scan_worker_manager.py
> Manages NLP scan worker subprocess(es), creating scan records, partitioning batches by TRC, and launching detached workers.

**Public API:**
- `ScanWorkerManager.__init__(self, db_path=None)` — initialize with optional database path
- `ScanWorkerManager.start_scan(self, date_start, date_end, trc_filter=None, batch_size=700, budget_cap=50.0, parallel_workers=1, mode='full') -> dict` — create scan record, partition batches, and launch workers

**Depends on:** `src.data.settings_manager`
**Depended by:** `src.ui.pages.trc_analytics`

---

### schema_builder.py
> Dynamic DDL generator for per-source tables. Creates `{prefix}_tickets`, `{prefix}_conversations`, `{prefix}_comments`, and `{prefix}_fts` tables for each registered data source. All statements use `IF NOT EXISTS` for idempotency.

**Public API:**
- `create_source_tables(conn, table_prefix)` — create the standard 4-table set for a data source

**Depends on:** (none — logging only)
**Depended by:** `src.data.source_registry`, `tests.test_stage2_source_registry`

---

### schedule_manager.py
> Polls the report_schedules DB table every 60 seconds and emits run_triggered when a schedule is due, with timezone support for daily/weekly/biweekly/monthly recurrence.

**Public API:**
- `SUPPORTED_TIMEZONES` — list of supported timezone names
- `TIMEZONE_LABELS` — dict mapping timezone names to human-readable labels
- `compute_next_run(repeat_type: str, repeat_day: int, repeat_time: str, tz_name: str = "UTC", after: datetime = None) -> str` — compute the next run datetime as ISO string
- `format_next_run(iso_str: str) -> str` — format an ISO datetime for display
- `ScheduleManager.__init__(self, db, parent=None)` — initialize with DatabaseManager
- `ScheduleManager.start(self)` — begin polling
- `ScheduleManager.stop(self)` — pause polling

**Depends on:** (none from src/)
**Depended by:** `src.ui.main_window`, `src.ui.pages.smart_reporting`, `tests.test_reporting_suite`

---

### settings_manager.py
> Centralized access for config/settings.yaml with automatic migration from config/ to data/ so settings survive auto-updates.

**Public API:**
- `get_settings_path() -> Path` — return the active settings.yaml path
- `load_settings() -> dict` — load the full settings dict
- `save_settings(cfg: dict) -> bool` — save the full settings dict
- `get_section(section: str, default=None) -> dict` — load a single section from settings
- `set_section(section: str, value: dict) -> bool` — write a single section to settings
- `update_section(section: str, updates: dict) -> bool` — merge updates into a section
- `reset_migration_flag()` — reset the one-time migration flag (for testing)

**Depends on:** (none from src/)
**Depended by:** `src.data.scan_server_manager`, `src.data.scan_worker_manager`, `src.data.smart_pipeline`, `src.data.voc_builder`, `src.ui.main_window`, `src.ui.layman_mode`, `src.ui.pages.ai_reports`, `src.ui.pages.settings_page`, `src.ui.pages.smart_reporting`, `src.ui.pages.trending_topics`, `src.ui.pages.trc_analytics`, `src.ui.widgets.collapsible_section`, `src.gemini.gemini_client`, `src.gemini.client_factory`, `src.llm.model_registry`, `src.agents.scan_orchestrator`, `tests.test_hardening`, `tests.test_updater`, `tests.test_feature_integration`

---

### smart_pipeline.py
> End-to-end reporting pipeline: data pull, analytics, optional NLP scan, meta-analysis, AI report generation, save, and Drive export.

**Public API:**
- `run_pipeline(config=None, progress_cb=None) -> dict` — run the full smart reporting pipeline returning success, report_text, report_id, duration_ms, steps_completed, error, and nlp_scan_id

**Depends on:** `src.data.settings_manager`, `src.data.db_manager`, `src.data.incident_engine`, `src.data.report_builder`, `src.data.nlp_meta_analyzer`, `src.data.pat_store`
**Depended by:** `src.ui.pages.smart_reporting`

---

### source_types.py
> Thin ABC interfaces defining the contract for any data source (SourceClient, SourceMonitor, SourceConfig).

**Public API:**
- `SourceConfig` — dataclass describing a configured data source instance (name, display_name, icon, is_configured, is_connected, extra)
- `SourceClient` — abstract base class for source API clients with test_connection, fetch_incremental, extract_trc, source_name, is_configured
- `SourceMonitor` — abstract base class for real-time polling monitors with signals (records_received, spike_detected, alert_fired, status_changed) and start/pause/resume/stop lifecycle

**Depends on:** (none from src/)
**Depended by:** `src.data.zendesk_client`, `src.data.zendesk_monitor`, `tests.test_source_types`

---

### source_registry.py
> CRUD for registering data sources. Each source gets its own table_prefix and per-source tables plus column mappings.

**Public API:**
- `get_source_template(source_type) -> dict` — return default config template for source type (zendesk, kodif, custom)
- `SourceRegistry(conn)` — initialize registry
  - `create_source(source_id, source_name, source_type, table_prefix, column_mapping) -> dict` — register new data source
  - `get_source(source_id) -> dict | None` — look up source by ID
  - `get_default_source() -> dict | None` — get the default source
  - `list_sources() -> list[dict]` — list all registered sources
  - `update_source(source_id, **kwargs)` — update source fields
  - `delete_source(source_id)` — remove a source registration

**Depends on:** `src.data.schema_builder`
**Depended by:** `src.data.warehouse_query`, `src.data.filter_engine.fts_handler`, `src.ui.pages.source_monitor_page`, `tests.test_stage2_source_registry`

---

### source_warehouse.py
> Classifies and persists source record metadata (no PHI) into the cold tier using a hybrid n-gram fast path / LLM slow path classification pipeline.

**Public API:**
- `SourceWarehouse.__init__(self, db_manager)` — initialize with DatabaseManager
- `SourceWarehouse.ingest_records(self, records: list[dict], source: str, trc_field: str, client) -> list[dict]` — classify and persist a batch of records from any source

**Depends on:** (none from src/)
**Depended by:** `src.ui.main_window`, `tests.test_source_warehouse`

---

### tech_summary_builder.py
> Aggregates existing DB metrics (gemini_usage, nlp_batches, scan_events, probe_history) into structured summaries for display across reporting pages.

**Public API:**
- `build_tech_summary(db, scan_id: str = None, report_run_id: int = None) -> dict` — aggregate all DB metrics for a given scan or report run
- `format_tech_summary_as_markdown(summary) -> str` — format the summary dict as markdown text

**Depends on:** `src.data.usage_tracker`
**Depended by:** `src.data.ab_report_pipeline`, `src.data.ai_report_pipeline`, `src.ui.pages.smart_reporting`, `tests.test_reporting_foundation`

---

### theta_engine.py
> Statistical process control for RCM ticket metrics: computes EWMA rolling baselines and flags day-over-day variance exceeding 1-sigma (watch) or 2-sigma (incident) for sentiment and term frequency.

**Public API:**
- `run_theta_scan(conn, target_date=None, progress_callback=None) -> dict` — run the theta anomaly detection scan for a specific date
- `run_theta_scan_range(conn, date_from, date_to, progress_callback=None) -> dict` — run theta scan across a date range
- `acknowledge_flag(conn, flag_id)` — mark a theta flag as acknowledged
- `mark_false_positive(conn, flag_id)` — mark a theta flag as false positive
- `get_flag_history(conn, trc_code=None, days=90)` — get theta flag history

**Depends on:** (none from src/)
**Depended by:** `src.ui.pages.incidents_page`, `tests.test_feature_integration`

---

### trc_analytics.py
> Queries the database and computes all metrics needed by the TRC Analytics page (summary KPIs, volume by TRC, resolution times, CSAT heatmap).

**Public API:**
- `compute_trc_analytics(conn, date_start, date_end, trc_filter=None, status_filter=None) -> dict` — compute all TRC analytics metrics returning summary, volume_by_trc, resolution_by_trc, csat_heatmap, and metrics_table

**Depends on:** (none from src/)
**Depended by:** `src.ui.pages.trc_analytics`, `tests.test_feature_integration`

---

### trending_engine.py
> Sentiment analysis, TF-IDF rising terms, topic clustering (NMF), cross-TRC correlations, and hypothesis testing for the Trending Topics page.

**Public API:**
- `DOMAIN_STOPWORDS` — comprehensive set of domain-specific stopwords
- `COMPOUND_TERMS` — dict of known compound phrases and their replacements
- `compute_sentiment_trends(conn, date_start, date_end, trc_filter, window_size, ...) -> dict` — compute windowed sentiment trends
- `compute_rising_terms(conn, date_start, date_end, trc_filter, window_size, ...) -> dict` — compute TF-IDF rising terms with temporal velocity
- `compute_topic_clusters(conn, date_start, date_end, trc_filter, ...) -> dict` — compute NMF topic clusters
- `compute_topic_model(conn, date_start, date_end, trc_filter, window_size, ...) -> dict` — compute standalone TF-IDF topic model
- `compute_cross_trc_correlations(conn, date_start, date_end, window_size, ...) -> dict` — compute cross-TRC volume correlations
- `compute_temporal_leads(correlations_result, window_size, max_lag=4) -> list` — compute lead-lag relationships
- `run_full_analysis(conn, date_start, date_end, trc_filter, window_size, ...) -> dict` — run the complete analysis pipeline
- `get_tickets_for_term(db, term, date_start, date_end, trc_filter, limit=50) -> list` — get tickets containing a specific term
- `get_tickets_by_ids(db, ticket_ids, limit=50) -> list` — get tickets by IDs
- `test_hypothesis(conn, hypothesis, date_start, date_end, ...) -> dict` — test a user hypothesis against the data
- `smooth_clusters_with_ai(topics, gemini_client) -> list` — use AI to improve topic cluster labels
- `suggest_keyword_improvements(terms, gemini_client) -> list` — use AI to suggest keyword improvements

**Depends on:** `src.data.concept_map`, `src.data.compound_discovery`, `src.data.embedding_engine`, `src.data.incident_engine`
**Depended by:** `src.data.ab_analysis`, `src.data.compound_discovery`, `src.data.report_builder`, `src.ui.pages.trending_topics`, `src.ui.dialogs.term_manager_dialog`, `src.ui.widgets.term_manager_panel`, `tests.test_phase5_regression`, `tests.test_feature_integration`

---

### usage_tracker.py
> Centralized Gemini token/cost tracking and aggregation with plan utilization monitoring.

**Public API:**
- `GEMINI_PLANS` — dict of Gemini plan pricing configurations
- `UsageTracker.__init__(self, db_manager)` — initialize with DatabaseManager
- `UsageTracker.estimate_tokens(text: str) -> int` — estimate token count from text length
- `UsageTracker.estimate_cost(tokens_in: int, tokens_out: int, model: str = "gemini-2.5-flash") -> float` — calculate estimated cost in USD
- `UsageTracker.log_call(self, source: str, tokens_in: int, tokens_out: int, model: str = "gemini-2.5-flash", scan_id: str = None)` — log a Gemini API call
- `UsageTracker.get_daily_totals(self, date: str = None) -> dict` — get token/cost totals for a specific day
- `UsageTracker.get_weekly_totals(self) -> dict` — get totals for the current week
- `UsageTracker.get_monthly_totals(self) -> dict` — get totals for the current month
- `UsageTracker.get_period_totals(self, period_type: str = "quarterly") -> dict` — get totals for current billing period
- `UsageTracker.get_plan_utilization(self, plan_key: str = "flash_2.5") -> dict` — get current usage as percentage of plan limits
- `UsageTracker.get_cost_history_weekly(self, weeks: int = 12) -> list` — return weekly cost data for bar chart
- `UsageTracker.get_scan_cost(self, scan_id: str) -> dict` — get total cost for a specific scan

**Depends on:** (none from src/)
**Depended by:** `src.data.tech_summary_builder`, `src.data.voc_builder`, `src.agents.scan_orchestrator`, `src.ui.widgets.cost_dashboard`, `tests.test_feature_integration`

---

### voc_builder.py
> Multi-perspective VOC root cause analysis pipeline with batched TRC analysis, pipelined accumulator, specialist analysts, and convergence into executive reports.

**Public API:**
- `VOCBuilder.__init__(self, db, gemini_client=None, progress_cb=None, cost_cb=None)` — initialize with DatabaseManager, optional Gemini client, and callbacks
- `VOCBuilder.run(self, date_start, date_end, trc_filter=None, ...) -> dict` — run the full VOC pipeline returning report text and metadata

**Depends on:** `src.data.settings_manager`, `src.data.report_builder`, `src.data.usage_tracker`
**Depended by:** `src.ui.pages.ai_reports`, `tests.bench_voc`, `tests.test_voc_builder`, `tests.test_voc_full_e2e`

---

### warehouse_query.py
> Unified query interface across all per-source tables. Maintains backward compatibility with legacy shared tables (tickets, conversations, comments) for pre-migration databases.

**Public API:**
- `WarehouseQuery(conn, source_registry)` — initialize with connection and SourceRegistry
  - `get_conversations(source_id=None, date_start=None, date_end=None, trc_filter=None, provider_id=None, client_id=None, limit=None) -> list` — query conversations from one or all sources
  - `get_tickets(source_id=None, ...) -> list` — query tickets
  - `get_ticket_count(source_id=None, ...) -> int` — count tickets matching filter
  - `get_trc_distribution(source_id=None, ...) -> list[dict]` — TRC code distribution

**Depends on:** `src.data.source_registry`
**Depended by:** `src.data.db_manager`, `src.data.filter_engine.fts_handler`, `src.ui.pages.data_warehouse_page`

---

### watchlist_engine.py
> UI-configurable rule engine for real-time alert detection with keyword, entity, volume, and compound rule types, plus EWMA learning loop and LLM triage.

**Public API:**
- `WatchlistEngine.__init__(self, db, warehouse=None, llm_client=None)` — initialize with DatabaseManager, optional SourceWarehouse, and LLM client
- `WatchlistEngine.evaluate(self, records: list[dict], source: str) -> list[dict]` — evaluate watchlist rules against incoming records

**Depends on:** `src.data.entity_extractor`
**Depended by:** `src.ui.main_window`, `tests.test_watchlist_engine`

---

### zendesk_client.py
> Zendesk Support API v2 client for incremental ticket export with cursor-based pagination.

**Public API:**
- `ZendeskClient.__init__(self, subdomain: str, email: str, api_key: str, view_id: str = "")` — initialize with Zendesk credentials
- `ZendeskClient.test_connection(self) -> bool` — verify credentials
- `ZendeskClient.fetch_incremental(self, cursor=None, start_time=None) -> tuple[list[dict], str]` — fetch records since cursor
- `ZendeskClient.extract_trc(self, record: dict, trc_field: str = "subject") -> str` — extract TRC code from a record
- `ZendeskClient.source_name -> str` — returns 'zendesk'
- `ZendeskClient.is_configured -> bool` — check if credentials are present
- `ZendeskAuthError` — raised on 401 Unauthorized
- `ZendeskRateLimitError` — raised on 429 Too Many Requests

**Depends on:** `src.data.source_types`, `src.data.pat_store`
**Depended by:** `src.data.zendesk_monitor`, `src.ui.main_window`, `src.ui.pages.source_monitor_page`, `tests.test_source_monitor`

---

### zendesk_monitor.py
> Background polling service that fetches new tickets from Zendesk at a configurable interval and computes TRC spike alerts.

**Public API:**
- `ZendeskMonitor.__init__(self, db, warehouse=None, watchlist=None, parent=None)` — initialize with DatabaseManager and optional warehouse/watchlist
- `ZendeskMonitor.start(self, interval_seconds: int = 120)` — begin polling
- `ZendeskMonitor.pause(self)` — pause polling
- `ZendeskMonitor.resume(self)` — resume polling
- `ZendeskMonitor.stop(self)` — stop polling and clear state

**Depends on:** `src.data.source_types`, `src.data.zendesk_client`
**Depended by:** `src.ui.main_window`, `tests.test_source_monitor`
