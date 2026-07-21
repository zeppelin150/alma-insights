# Qwen3 Embedding Architecture — Technical Handoff

**Audience**: Engineers taking over or extending the semantic layer.
**Last updated**: 2026-06-10.
**Status**: Production (Phases 1–3, 5, 5.5, 7 of the canonicalization-enrichment plan complete).

---

## 1. Why Qwen3-Embedding-0.6B

### 1.1 The constraint stack

The model choice was driven by hard constraints, in priority order:

1. **HIPAA air-gap.** Ticket bodies contain PHI. Our BAA covers the Gemini CLI only —
   no embedding API (OpenAI, Cohere, Vertex, Bedrock embeddings) is permissible for
   raw ticket text. Inference must be fully local with zero outbound connections.
2. **Open weights, no gate.** Apache 2.0, no HF gating, no license click-through —
   required because the installer bundles the model for non-technical end users.
3. **Consumer-hardware inference.** Production target is a 2020 M1 MacBook Pro with
   16 GB unified memory. The model must fit and run at acceptable throughput on CPU/MPS.
4. **Provider agnosticism.** The embedding space is the app's persistent semantic
   memory (canonical clusters survive across scans). It must not depend on whichever
   cloud LLM is active, so the Gemini↔Claude/Bedrock provider swaps don't invalidate
   accumulated state.

### 1.2 Model spec

| Property | Value | Why it matters |
|----------|-------|----------------|
| Model | `Qwen/Qwen3-Embedding-0.6B` | |
| Parameters | 0.6 B (~1.2 GB fp32, ~0.6 GB fp16) | Fits 16 GB unified memory with headroom |
| Output dimensions | 1024 (Matryoshka: 32–1024 usable) | Stored as float32[1024] blobs |
| Max sequence length | 32,768 tokens (we cap at 2,048) | Attention is quadratic; 2,048 tokens holds our 8,192-char bodies at ~3.5–4 chars/token |
| Query encoding | Native `prompt_name="query"` instruction-aware mode | Asymmetric retrieval without manual prefix hacks |
| Runtime | sentence-transformers ≥ 2.7.0 | Already a dependency |
| License | Apache 2.0, ungated | Redistributable in installer |
| On-disk location | `data/models/` | Bundled by `installer/build_release.py` |

### 1.3 What it replaced, and why

The original engine (`src/data/embedding_engine.py`, now a deprecated shim) used
**MiniLM-L6-v2** (384-dim) and — critically — composed its source text from **LLM
classification fields**: `trc_code + friction_type + sub_pattern + issue_snippet`.

That design had a structural flaw: **the embedding space was coupled to the
classifier's label choices.** Tickets the LLM happened to tag identically embedded
nearly identically even when their content diverged, creating a feedback loop where
canonicalization "discovered" the very labels we were trying to validate. It also
made the vector space provider-dependent — re-classify with a different model and
the geometry shifts.

The replacement (locked decision #2, `canonicalization_decisions.md`, 2026-04-14):
**Qwen3 on raw conversation bodies only.** Zero LLM influence on the vector space.
Side benefits: 1024-dim vs 384-dim representation, 32k vs 512 token context, and
the MiniLM compat layer was dropped rather than maintained.

---

## 2. How Qwen3 Embeddings Work (primer)

For readers new to embedding models:

- Qwen3-Embedding is a **decoder-based text encoder**: text in, a single dense
  vector out. Semantically similar texts map to nearby vectors.
- We **L2-normalize** every vector at encode time (`normalize_embeddings=True`),
  so **cosine similarity reduces to a dot product**, and **Euclidean distance is
  monotonic in cosine distance** — both facts are load-bearing (see §4.2).
- It is an **asymmetric retrieval** model: queries and documents are encoded
  differently. Documents get no prompt; queries are encoded with
  `prompt_name="query"`, which prepends Qwen3's instruction template for
  retrieval-optimized encoding ([encoder.py](../src/data/embedding/encoder.py)).
- **Matryoshka representation**: the 1024-dim output is trained so prefixes
  (e.g. first 256 dims) remain usable embeddings. We store full 1024 today; this
  is the escape hatch if M1 memory/throughput ever forces truncation.

---

## 3. Integration Architecture

### 3.1 Module layout — `src/data/embedding/`

| File | Responsibility |
|------|----------------|
| `air_gap.py` | Env-var enforcement + runtime verification (§3.2) |
| `model_loader.py` | Singleton load, device resolution, CPU fallback (§3.3) |
| `encoder.py` | `embed_documents()`, `embed_query()` — thin, normalized encode |
| `search.py` | `semantic_search()`, embedding-cache load |
| `builder.py` | `build_embeddings()` — incremental pipeline, composition modes (§3.4) |
| `compat.py` | Backward-compat shims for the old `embedding_engine.py` API |

### 3.2 Air gap enforcement

[air_gap.py](../src/data/embedding/air_gap.py) sets six env vars **before any
Hugging Face import**:

```
HF_HUB_DISABLE_TELEMETRY=1   HF_HUB_OFFLINE=1      TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1        SENTENCE_TRANSFORMERS_HOME=data/models   NO_PROXY=*
```

`verify_air_gap()` runtime-checks all six vars, model-dir existence, and runs a
**live socket test** against `huggingface.co:443` — a *successful* connection fails
the check. Connection failure (or inability to test) passes conservatively.

### 3.3 Model loading and device resolution

[model_loader.py](../src/data/embedding/model_loader.py) is a module-level
singleton. Load sequence:

1. `is_available()` — sentence-transformers importable AND model files on disk.
   Returns bool; never raises. The startup check (`src/startup/checks/model.py`)
   warns-but-continues if the model is missing (semantic features disable, app runs).
2. `enforce_air_gap()` before the HF import.
3. **Device resolution**: reads `embedding_device` from `data/hardware_profile.json`
   (written by the startup hardware profiler), then *validates against torch's
   runtime view* — a profile claiming `cuda` on a box where
   `torch.cuda.is_available()` is False silently downgrades to `cpu`. Same for `mps`.
4. **First-load fallback**: some PyTorch builds report `mps.is_available() == True`
   but throw on first encode. If the accelerator load throws, we retry on CPU and
   **rewrite the profile** (`embedding_device_fallback_reason`) so subsequent
   launches don't repeat the failing attempt.
5. Model path discovery handles both clean `snapshot_download` layouts and HF cache
   structure (`models--Qwen--Qwen3-Embedding-0.6B/snapshots/<hash>/`).

### 3.4 The builder — incremental pipeline + composition modes

[builder.py](../src/data/embedding/builder.py) `build_embeddings(conn, force,
composition_mode)` is the only writer to `ticket_embeddings`.

**Source-text composition** (the Phase 2 decision, §1.3) — three modes exist:

| Mode | Composition | Status |
|------|-------------|--------|
| `raw_body` | Subject + first **8** comments, **PHI-redacted** via `RedactionEngine.scrub`, truncated to **8,192 chars** | **Production default** |
| `composite_A` | `[trc: X] [friction: Y] [issue: Z]` header + body (1,500-char cap) | Experimental, retained for A/B |
| `composite_C` | Subject ×3 + compact label line + first 2 comments (1,200-char cap) | Experimental, retained for A/B |

Fallback: tickets with no conversation body (visible only via `ticket_index`) use
the legacy classification-field composition.

**Hash-based skip**: each ticket's composed text is hashed **with the
composition_mode as a prefix** (`raw_body|…`). Unchanged tickets are skipped;
switching modes force-re-embeds everything. This makes the 50K-ticket onboarding
embed a one-time cost — subsequent scans embed only new/changed tickets
(~500–2,000 typical).

**PHI redaction caveat**: if `RedactionEngine` fails to construct, raw bodies pass
through **unredacted** with a logged HIPAA warning. The vectors themselves never
leave the machine, but redaction is defense-in-depth — treat that warning as
actionable.

**Hardware-adaptive sizing**: batch size and `max_seq_length` come from
`data/hardware_profile.json` (`embedding_batch_size`, `embedding_max_seq_length`);
defaults are batch=64, max_seq=2048. Known gap: see §7.

### 3.5 Persistence

Embeddings live in `ticket_embeddings` (ticket_id, embedding_blob = float32[1024]
little-endian, content hash, timestamps). Cluster/concept state (§4) lives in
`canonical_clusters` (migration 017) and `canonical_concepts` (migration 020), with
assignment columns added to `ticket_index` (`canonical_issue_id`,
`canonical_confidence`, `assignment_method`, `hdbscan_membership_prob`,
`canonicalized_at`).

---

## 4. How It Works Within the System

### 4.1 Consumer map

```
                       ┌──────────────────────────────┐
   ticket bodies ──►   │  build_embeddings()          │
   (PHI-redacted)      │  Qwen3 → float32[1024], L2   │
                       └───────────┬──────────────────┘
                                   │ ticket_embeddings
        ┌──────────────────┬───────┴────────┬───────────────────┐
        ▼                  ▼                ▼                   ▼
  canonicalization    semantic_search   audit_tag_         drift / fission /
  (HDBSCAN+snap)      (chat MCP tool)   correlation        dormancy telemetry
        │                                                  (Phase 6, default off)
        ▼ canonical_clusters
  concept linking (Phase 7 — one LLM call/scan)
        ▼ canonical_concepts
  query_issues (chat), reports, trends
```

### 4.2 Canonicalization ([canonicalization_engine.py](../src/data/canonicalization_engine.py))

Per-TRC pipeline (TRC scoping is locked decision #1 — KODIF owns TRC definitions,
so clustering never crosses TRC boundaries at L1):

1. **Centroid snapping** — new tickets are first compared against *existing*
   cluster centroids; cosine > **0.75** snaps the ticket into the existing cluster.
   This is what makes clusters **stable across scans** (locked decision #6) — a
   re-run doesn't re-derive the taxonomy.
2. **HDBSCAN** on the remaining (unsnapped) embeddings. We run it with
   **Euclidean distance on L2-normalized vectors** rather than hdbscan's pairwise-
   cosine path — mathematically equivalent ordering (Euclidean is monotonic in
   cosine on the unit sphere) and ~3–4× faster.
3. **KNN fallback** — HDBSCAN noise points are assigned to the nearest centroid if
   cosine > 0.75; otherwise marked `unclustered`. Every ticket gets an auditable
   `assignment_method` enum: `hdbscan_core | hdbscan_border | knn_fallback |
   snapped_existing | unclustered`.
4. **Centroids** are confidence-weighted means; the **medoid** (member maximizing
   similarity to centroid) becomes the `representative_ticket_id`.
5. **Labeling** is the Option D hybrid (locked decision #5): medoid → extractive
   TF-IDF → LLM, with a validator chain — the only place an LLM touches cluster
   metadata, and it names clusters, never shapes them.

### 4.3 Concept layer (Phase 7) — where pure embeddings stopped being enough

The honest result from the Phase 3 gate (§5): cluster-level recall **caps at ~0.65**
because Qwen3 embeds same-concept tickets into distinct sub-groups on surface
features — claim IDs, dates, payer-specific phrasing. Tightening HDBSCAN params
trades precision for recall without fixing the cause.

The fix ([concept_linker.py](../src/data/concept_linker.py)): one **batched LLM
call per scan** groups `canonical_clusters` into higher-level `canonical_concepts`
(≤80 clusters per call, cross-batch reconciliation). Concepts are the **recall
layer** — the granularity the product actually asks at ("among 47 Thunderbird
tickets, top concept is payment-responsibility inaccuracy, 35 tickets").

Division of labor, by design:

| Layer | Engine | Provider-dependent? |
|-------|--------|---------------------|
| ticket → vector | Qwen3, local | No |
| vector → cluster | HDBSCAN + cosine, local | No |
| cluster → concept | 1 LLM call/scan, task-routed | Yes — but swappable, and operates *above* the stable vector space |
| cluster → label | Hybrid w/ LLM final step | Yes — cosmetic only |

### 4.4 Semantic search (chat)

The `semantic_search` MCP tool ([chat_mcp_server.py](../src/mcp/chat_mcp_server.py))
embeds the user's natural-language query via `embed_query()` (instruction-aware
`prompt_name="query"`) and ranks tickets by dot product against the cached document
matrix. Identical behavior under Gemini or Claude chat providers.

### 4.5 Tag auditing

`audit_tag_correlation` checks event-applied incident tags against embedding
geometry: tickets whose vectors sit far (cosine distance) from the tag's expected
cluster centroid are ranked as likely mis-tagged. A pure-vector integrity check on
human/event labeling — no LLM in the loop.

### 4.6 Phase 6 telemetry (built, default OFF)

Drift detection (per-scan centroid-delta snapshots, 2-scan confirmation gate),
cluster **fission** (triggers: intra-cluster variance > 0.35 OR silhouette < 0.15;
sub-HDBSCAN split proposed, LLM commit gate ≥ 4.0, observational only — splits are
not committed), and **dormancy/resurrection** (3-scan dormancy, 10-scan retirement,
2-scan resurrection gate requiring ≥3 new members or ≥2 sustained). All
feature-gated off in production; tests exist (§5.4).

---

## 5. Testing & Validation History

### 5.1 The Phase 3 gate — methodology

The gate (`scripts/run_phase3_gate.py`; `run_phase3_mini_gate.py` is a 200-ticket
subsample for ~2–5 min iteration) is a full-stack validation against a fresh DB:

1. Apply migrations 001–018 to a temp database.
2. Ingest the gate fixture: `alma_test_10000_1_enriched.csv` — 9,467 rows,
   **1,788 unique tickets** (synthetic, PHI-free, realistic per-TRC distributions).
3. Build Qwen3 raw-body embeddings.
4. Run canonicalization with default params.
5. Score against the **golden set**: 423 hand-labeled ticket pairs
   (`same_issue` bool, stratified across TRCs). Pairwise precision/recall/F1.

Gate thresholds: precision ≥ 0.80 AND recall ≥ 0.80 to pass (exit code 0/1).
A cached gate DB (`data/phase3_gate_test.db`: 1,780 classifications, 1,788
embeddings, 19 clusters) is kept because a rebuild costs ~35 min + Gemini spend.

Additionally, every tuning run is persisted to `canonicalization_tuning_runs`
(silhouette, Davies-Bouldin, golden P/R/F1, noise %, n_clusters, wall time) via a
32-cell grid-search harness (`scripts/tune_canonicalization.py`) — the parameter
space is fully auditable in-DB.

### 5.2 Results — what worked and what didn't

| Experiment | Result | Verdict |
|------------|--------|---------|
| **Phase 3 baseline** — HDBSCAN on raw-body Qwen3 embeddings, 19 clusters from 1,788 tickets | **P 0.93 / R 0.65 / F1 0.77** | Precision excellent; **recall failed the 0.80 gate**. Root cause: Qwen3 separates same-concept tickets on surface wording. Not fixable by parameter tuning. |
| **Phase 7 concept re-scoring** — clusters grouped into concepts via one batched Gemini call, recall re-scored at concept level (validated 2026-04-15 on the cached gate DB) | **P 0.88–0.93 / R 0.76–0.93 / F1 0.82–0.86** | **Adopted.** The range (not a point) is LLM nondeterminism — Gemini's grouping granularity varies run to run. Architecturally correct; accept ≥0.76 recall as passing. |
| **MiniLM + classification-field composition** (legacy) | Label-bias feedback loop (§1.3) | **Rejected & removed.** Shimmed for compat only. |
| **composite_A / composite_C compositions** | Built and switchable; no comparative metrics published | **Not adopted.** `raw_body` was locked on architectural grounds (decoupled vector space) rather than benchmark superiority. The A/B harness remains if someone wants to settle it empirically. |
| **Pairwise-cosine HDBSCAN** | ~3–4× slower than Euclidean-on-normalized | Rejected; mathematically equivalent ordering. |
| **K-means / spectral clustering** | Non-deterministic, non-auditable assignment | Rejected (locked decision #3) in favor of HDBSCAN + enum-audited assignment methods. |
| **Re-cluster every scan** | Cluster identities churn between runs | Rejected (locked decision #6) in favor of centroid snapping at cos > 0.75. |

Multi-label assignment tiers (primary/secondary/tertiary at cosine 0.75/0.65/0.55,
M:N table) are designed, unit-tested (19 tests), and **deferred** — production is
single-label via `ticket_index.canonical_issue_id`.

### 5.3 Key takeaway for the next engineer

**Pure embedding clustering gives you precision; it does not give you recall at
product granularity.** The 0.65 recall ceiling is a property of how embedding
models encode surface variation, not a bug in our pipeline. The two-level design
(local vectors for precision + one cheap LLM call for concept-level recall) is the
load-bearing architectural answer — don't try to tune HDBSCAN past it.

### 5.4 Test suite

~136 test methods / 2,156 lines across the canonicalization stack, all green at
last full run (0 regressions across a 13-file, 293-test sweep):

| File | Tests | Covers |
|------|-------|--------|
| `test_canonicalization_engine.py` | 41 | HDBSCAN, snapping, KNN fallback, confidence-weighted centroids, golden-set tp/fp/fn logic, E2E, re-run stability |
| `test_multilabel_assignments.py` | 19 | Tier thresholds, rank computation, boundaries |
| `test_drift_detection.py` | 19 | Snapshot deltas, stability bands, centroid drift |
| `test_dormancy.py` | 15 | Tier state machine, 2-scan resurrection gate |
| `test_fission.py` | 21 | Variance/silhouette triggers, sub-HDBSCAN, LLM commit gate |
| `test_tag_audit.py` | 21 | Correlation scoring, mismatch ranking |

Patterns: fresh-DB fixtures per test; synthetic cluster vectors with controlled
cosine separation (~0.85–0.95 intra-cluster); golden-set scoring tested in
isolation. Run in groups of 3–4 files (full `tests/` hangs on Windows).

### 5.5 Performance

| Environment | Throughput / cost |
|-------------|-------------------|
| Dev — i7-12700K + RTX 4070 Ti SUPER (CUDA, cu124) | ~500–2,000 tickets/min |
| Dev CPU | ~30 tickets/min (the reason CUDA was set up) |
| Production — M1, 16 GB (MPS) | Expected 5–15× over M1 CPU; **not yet validated on the work machine** (see hardware-profiler M1 plan) |
| CoreML / ANE (future option) | Potential 20–50× over M1 CPU via coremltools ONNX→CoreML conversion — unexplored |
| Full re-embed, 1,788-ticket fixture | ~5 min (dev) |
| Full gate run | ~35 min + Gemini spend (hence the cached gate DB) |

`scripts/bench_embedding.py` is a parameterized throughput harness (batch 32–128 ×
max_seq 256–512 × truncation variants) over 100 real tickets; configs exist but no
canonical published table — run it on new hardware before changing profile defaults.

---

## 6. Schema Quick Reference

```sql
-- migration 017
canonical_clusters(
  cluster_id TEXT PK,            -- "{trc_slug}-{uuid4_hex_12}"
  trc TEXT NOT NULL,
  canonical_label TEXT, label_source TEXT,   -- medoid|extractive|llm|existing_retained
  centroid_blob BLOB NOT NULL,   -- float32[1024], L2-normalized
  representative_ticket_id TEXT, -- medoid
  member_count INT, lifetime_tickets INT, lifetime_scans INT,
  tier TEXT DEFAULT 'probationary',  -- probationary|active|stable|dormant|retired|split
  merged_into TEXT, split_into_json TEXT,
  concept_id TEXT)               -- FK → canonical_concepts (Phase 7)

ticket_index + (canonical_issue_id, canonical_confidence,
                assignment_method, hdbscan_membership_prob, canonicalized_at)

canonicalization_tuning_runs(params_json, silhouette, davies_bouldin,
  golden_precision/recall/f1, noise_pct, n_clusters, wall_time_ms, ...)

-- migration 020
canonical_concepts(concept_id TEXT PK, concept_label, centroid_blob,
  member_cluster_count, trcs_touched_json, source DEFAULT 'llm',
  llm_confidence, rationale, ...)
```

---

## 7. Known Limitations & Open Work

1. **Recall variance is LLM-bound.** Concept-level recall 0.76–0.93 across runs is
   Gemini granularity nondeterminism, not pipeline flakiness. A deterministic
   concept layer (e.g. agglomerative over cluster centroids with an LLM naming
   pass) was considered and set aside; revisit only if the variance bites.
2. **M1/MPS path unvalidated.** The hardware profiler writes `embedding_device`,
   the loader honors it with CPU fallback — but there's a known gap where the
   embedding pipeline historically didn't read the profile, dropping M1 users to
   CPU. Plan: `~/.claude/plans/hardware-profiler-m1.md`. Validate on the work
   machine before trusting M1 throughput estimates.
3. **Redaction soft-fails open.** Builder logs a warning and embeds unredacted
   bodies if `RedactionEngine` construction fails (§3.4). Vectors stay local, but
   consider hard-failing in a future hardening pass.
4. **Composition modes never benchmarked head-to-head.** `raw_body` won on
   architecture, not measurement. The harness (mode-prefixed hashes force clean
   re-embeds) makes the experiment cheap if it ever matters.
5. **Matryoshka truncation unexploited.** Storing 256-dim prefixes would cut
   blob size and similarity-math cost 4× at modest quality loss — relevant only
   if the 50K-ticket M1 target strains memory.
6. **Phase 6 (drift/fission/dormancy) is telemetry-only.** Built and tested,
   feature-gated off. Phase 8 (HITL) and Phase 9 (MCP tooling for tag audit) are
   designed but not built.

## 8. File Index

| Concern | Files |
|---------|-------|
| Embedding engine | `src/data/embedding/{air_gap,model_loader,encoder,search,builder,compat}.py` |
| Clustering | `src/data/canonicalization_engine.py` |
| Concept layer | `src/data/concept_linker.py`, `config/prompts/concept_linking.txt` |
| Gates & tuning | `scripts/run_phase3_gate.py`, `scripts/run_phase3_mini_gate.py`, `scripts/tune_canonicalization.py`, `scripts/bench_embedding.py` |
| Schema | `migrations/017_canonical_clusters.sql`, `migrations/020_canonical_concepts.sql` |
| Startup integration | `src/startup/checks/model.py`, `data/hardware_profile.json` |
| Tests | `tests/test_canonicalization_engine.py` + 5 siblings (§5.4) |
| Plan & decisions | `~/.claude/plans/canonicalization-enrichment.md`, memory `canonicalization_decisions.md` |
