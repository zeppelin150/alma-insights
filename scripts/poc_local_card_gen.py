"""Offline-LLM PoC — generate a Guru card from a doc with a local Qwen3.

Standalone go/no-go harness for docs/OFFLINE_LLM_SCOPE.md. NOT imported
by the app; its only extra dependency is llama-cpp-python, deliberately
absent from requirements.txt:

    pip install llama-cpp-python          # CUDA/Metal wheels available

Usage:
    python scripts/poc_local_card_gen.py --self-test
    python scripts/poc_local_card_gen.py \
        --model-path models/qwen3-8b-instruct-q4_k_m.gguf \
        --doc samples/release_note.txt [--style-guide style.txt]

Uses the REAL card-gen prompt template and the REAL TITLE/---/body parse
contract (enablement_store._parse_card), so a pass here is evidence
about the actual pipeline.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

_TEMPLATE = ROOT / "config" / "prompts" / "enablement_card_from_doc.txt"


def build_prompt(doc_name: str, doc_text: str, style_guide: str) -> str:
    template = _TEMPLATE.read_text(encoding="utf-8")
    style_block = ""
    if style_guide.strip():
        style_block = (
            "\nSTYLE GUIDE — follow it strictly for tone, structure and "
            "formatting:\n--- STYLE GUIDE START ---\n"
            f"{style_guide.strip()}\n--- STYLE GUIDE END ---\n"
        )
    return template.format(
        doc_name=doc_name, doc_text=doc_text,
        collection="Enablement", style_guide=style_block,
    )


def generate_local(model_path: str, prompt: str, max_tokens: int) -> tuple[str, float]:
    from llama_cpp import Llama  # pip install llama-cpp-python
    llm = Llama(
        model_path=model_path, n_ctx=8192, n_gpu_layers=-1, verbose=False,
    )
    t0 = time.time()
    out = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=0.3,
    )
    elapsed = time.time() - t0
    text = out["choices"][0]["message"]["content"]
    n_tokens = out.get("usage", {}).get("completion_tokens", 0) or 1
    return text, n_tokens / max(elapsed, 0.001)


class _StubGenerator:
    """--self-test path: validates prompt assembly + parse contract
    without llama-cpp or a model download."""

    def __call__(self, prompt: str) -> str:
        assert "DOCUMENT START" in prompt, "template did not render"
        return (
            "TITLE: Stub Card From Self-Test\n---\n"
            "One-line summary.\n\n## Steps\n1. First\n2. Second\n\n"
            "## FAQ\n**Q?**  A."
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-path", help="Path to a GGUF model")
    ap.add_argument("--doc", help="Path to a plain-text source document")
    ap.add_argument("--style-guide", help="Optional style-guide text file")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--self-test", action="store_true",
                    help="Validate prompt+parse plumbing with a stub generator")
    args = ap.parse_args()

    if args.self_test:
        doc_name, doc_text = "selftest.txt", "Rollout on Aug 1, 2026. Steps: a, b."
        style = "Use numbered steps."
    else:
        if not (args.model_path and args.doc):
            ap.error("--model-path and --doc are required (or use --self-test)")
        doc_path = Path(args.doc)
        doc_name, doc_text = doc_path.name, doc_path.read_text(encoding="utf-8")
        style = (Path(args.style_guide).read_text(encoding="utf-8")
                 if args.style_guide else "")

    prompt = build_prompt(doc_name, doc_text, style)
    print(f"[poc] prompt: {len(prompt):,} chars "
          f"(style guide: {'yes' if style.strip() else 'no'})")

    if args.self_test:
        text, tps = _StubGenerator()(prompt), 0.0
    else:
        text, tps = generate_local(args.model_path, prompt, args.max_tokens)
        print(f"[poc] throughput: {tps:.1f} tok/s")

    from src.data.enablement_store import _parse_card
    title, body = _parse_card(text, fallback_title=doc_name)
    contract_ok = bool(title) and bool(body) and text.strip().upper().startswith("TITLE:")
    print(f"[poc] contract (TITLE/---/body): {'PASS' if contract_ok else 'FAIL'}")
    print(f"\n===== TITLE =====\n{title}\n===== BODY =====\n{body}\n")

    if not contract_ok:
        print("[poc] NO-GO signal: output did not honor the card contract.")
        return 1
    print("[poc] OK — score structure/faithfulness per docs/OFFLINE_LLM_SCOPE.md §3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
