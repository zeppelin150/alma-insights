# Offline LLM for Enablement Content — Scoping (P8, 2026-06-11)

**Question:** since enablement content is product documentation (no PHI),
can a locally-run Qwen3 instruct model subsidize the cloud LLM lanes for
card generation/revision — a third `enablement.provider="local"` — at
Haiku-comparable quality, fully offline?

**Status: scoped + PoC harness shipped. NOT wired into the app** (out of
scope per the approved plan). Go/no-go evidence comes from running
`scripts/poc_local_card_gen.py` against a downloaded GGUF.

---

## 1. Model matrix (Qwen3 instruct, GGUF quants)

| Model | Quant | File size | RAM in use (approx) | M1 16GB fit | Expected quality |
|-------|-------|-----------|---------------------|-------------|------------------|
| Qwen3-4B-Instruct | Q4_K_M | ~2.5 GB | ~3.5 GB | Comfortable | Below the bar for faithful card structure; fine for revision-style edits |
| Qwen3-8B-Instruct | Q4_K_M | ~5.0 GB | ~6.5 GB | **Comfortable** (≈9 GB headroom) | The sweet spot — strong instruction following, good markdown discipline |
| Qwen3-8B-Instruct | Q5_K_M | ~5.8 GB | ~7.5 GB | OK | Marginal quality gain over Q4_K_M |
| Qwen3-14B-Instruct | Q4_K_M | ~8.8 GB | ~10.5 GB | Tight but workable (app + model + OS ≈ 14 GB) | Closest to Haiku; risky alongside the full app |

Recommendation: **Qwen3-8B-Instruct Q4_K_M** as the default candidate;
14B-Q4 only if the 8B fails the rubric, and then only in enablement mode
(where the analytics/torch stack is suppressed — the mode split from P1
is exactly what makes the 14B thinkable).

Note the distinction from the existing **Qwen3-Embedding-0.6B**
(`src/data/embedding/`) — that is an embedding model; this scope is
about the *generative* instruct family. They share nothing but the name.

## 2. Runtime

**Recommendation: llama-cpp-python.** One stack covers both targets:
- Dev box (i7-12700K + RTX 4070 Ti SUPER): wheel with CUDA
  (`pip install llama-cpp-python` with prebuilt cu124 wheel, or
  `CMAKE_ARGS="-DGGML_CUDA=on"`), `n_gpu_layers=-1` → 60–110 tok/s on 8B-Q4.
- M1 16GB (production): Metal is on by default in macOS wheels,
  `n_gpu_layers=-1` → ~18–30 tok/s on 8B-Q4. A 600-token card ≈ 25–35 s —
  acceptable for a background draft lane, marginal for interactive revision.

Alternative (M1-only): **MLX** (`mlx-lm`) — typically 10–25% faster than
llama.cpp Metal on the same quant, but it is a second stack, macOS-only,
and the GGUF ecosystem (quants, downloads) is broader. Not worth the
split unless llama.cpp Metal misses the latency bar.

CUDA/CPU/MPS auto-selection mirrors the embedding loader's
`hardware_profile.json` pattern (`src/data/embedding/model_loader.py`)
when this gets wired.

## 3. Quality bar (vs Haiku) — the rubric the PoC measures

Run `scripts/poc_local_card_gen.py` over ~10 archetype docs (release
note, process change, pricing update, FAQ-ish doc, policy doc…) and
score each output:

1. **Contract adherence** — parses via `enablement_store._parse_card`
   (TITLE / `---` / body); the PoC validates this automatically.
2. **Structure** — summary line first, rollout/due-date callout when the
   doc has one, numbered steps for processes, short FAQ. (The card-gen
   prompt demands these; Haiku scores ~10/10.)
3. **Style-guide compliance** — pass `--style-guide`; check tone/format
   rules are followed (the proven quality lever from the live demo).
4. **Faithfulness** — no invented facts/dates/names on a spot-check.
   This is the likely failure mode for ≤8B models; weight it highest.
5. **Throughput** — tok/s printed by the PoC; M1 bar: a full card in
   under ~45 s.

**Go** = 8B-Q4 scores ≥4/5 on structure+faithfulness across the doc set
and meets the latency bar on M1. **No-go** = hallucinated specifics on
>1/10 docs (a wrong rollout date in a published card is worse than no
card).

## 4. Integration design (future work, NOT in this build)

The contract is tiny: `draft_card_from_document` and `_revise_draft_impl`
only need `.generate(prompt) -> str`.

- `src/llm/local_llm_client.py` — `LocalLlmClient(model_path, n_ctx=8192)`
  wrapping llama-cpp-python; lazy model load on first `.generate()`
  (mirrors the embedding loader's deferral so boot stays fast); single
  in-process instance (the model is ~6 GB — never per-call).
- `src/gemini/client_factory.py` — third branch in the existing
  `enablement_*` short-circuit: `enablement.provider="local"` →
  `LocalLlmClient` from `enablement.local_model_path`.
- Settings: provider combo gains "Local (offline)"; a model-path field.
- **Scope guard:** local lane is for `enablement_*` tasks ONLY — PHI
  tasks stay on the Gemini CLI (BAA), and the chat tool-loop stays on
  its existing providers. Redaction stance unchanged (enablement lanes
  already run base-only redaction).
- The tool-calling Renn chat is NOT a target — small local models are
  unreliable tool callers; the local lane is generate-only.

## 5. Risks

| Risk | Mitigation |
|------|------------|
| Hallucinated specifics in published cards | Faithfulness rubric is the go/no-go gate; publish stays human-gated regardless |
| 14B + full product mode OOMs the M1 16GB | Local lane recommended only in enablement mode; default to 8B |
| llama-cpp-python wheel friction on M1 (build from source) | Pin a version with prebuilt metal wheels; document in install notes |
| A second model artifact to distribute (~5 GB) | Out-of-band download (like the embedding model pre-download in the installer), never bundled |
| Sequential generations block each other | Single-flight queue in LocalLlmClient; the UI already treats drafting as background work |

## 6. PoC harness

`scripts/poc_local_card_gen.py` — standalone; **deps deliberately absent
from requirements.txt** (`pip install llama-cpp-python` to run live).
Uses the real `config/prompts/enablement_card_from_doc.txt` template and
the real `_parse_card` contract check, so a PoC pass is evidence about
the actual pipeline, not a toy prompt.

```
python scripts/poc_local_card_gen.py --self-test          # no model needed
python scripts/poc_local_card_gen.py --model-path qwen3-8b-instruct-q4_k_m.gguf \
    --doc release_note.txt --style-guide style.txt
```
