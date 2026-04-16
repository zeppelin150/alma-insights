# Embedding Engine (EmbeddingGemma-300M)

Air-gapped local embedding engine for semantic ticket search.
Replaces the old `embedding_engine.py` (MiniLM-L6-v2).

## Model Spec

| Property | Value |
|----------|-------|
| Model | `Qwen/Qwen3-Embedding-0.6B` |
| Dimensions | 1024 (supports 32-1024 via Matryoshka) |
| Max sequence length | 32,768 tokens |
| Parameters | 0.6B |
| Framework | sentence-transformers >= 2.7.0 |
| License | Apache 2.0 (open weights, no gate) |
| Location | `data/models/` |

## HIPAA Air-Gap Architecture

All inference is local. Six environment variables are set BEFORE any
Hugging Face imports to guarantee zero outbound connections:

```
HF_HUB_DISABLE_TELEMETRY=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1
SENTENCE_TRANSFORMERS_HOME=<local_path>
NO_PROXY=*
```

`verify_air_gap()` performs runtime checks: env vars, file existence,
socket test to confirm no outbound connections succeed.

## Public API

```python
from src.data.embedding import embed_documents, embed_query, semantic_search, is_available

if is_available():
    embeddings = embed_documents(["ticket text 1", "ticket text 2"])
    query_vec = embed_query("billing dispute")
    results = semantic_search(query_vec, embeddings, ticket_ids, top_k=10)
```

## Module Files

| File | Purpose |
|------|---------|
| `air_gap.py` | Environment enforcement + runtime verification |
| `model_loader.py` | Singleton model load with air-gap check |
| `encoder.py` | `embed_documents()`, `embed_query()` |
| `search.py` | `semantic_search()`, `load_embedding_cache()` |
| `builder.py` | `build_embeddings()` — incremental pipeline with hash-based skip |
| `compat.py` | Backward-compat shims for old `embedding_engine.py` API |
