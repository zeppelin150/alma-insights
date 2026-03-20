#!/usr/bin/env python3
"""
Bootstrap Product Guide Generator

Reads the feature manifest, extracts relevant code context for each feature area,
sends each chunk to Claude for product guide generation, and outputs structured
JSON ready for Guru card creation.

Usage:
    python bootstrap_guides.py \
        --manifest feature-manifest.json \
        --repo-root /path/to/alma-insights \
        --output /tmp/guides \
        --model claude-sonnet-4-20250514 \
        --dry-run  # optional: skip API calls, just show what would be sent
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests


def find_files_for_feature(repo_root: str, patterns: list[str]) -> list[str]:
    """Resolve glob patterns against the repo root, return matching file paths."""
    matched = set()
    for pattern in patterns:
        # Handle exclusion patterns (starting with !)
        if pattern.startswith("!"):
            continue  # We'll handle exclusions in a second pass

        full_pattern = os.path.join(repo_root, pattern)
        for match in glob.glob(full_pattern, recursive=True):
            if os.path.isfile(match):
                matched.add(match)

    # Second pass: remove exclusions
    for pattern in patterns:
        if pattern.startswith("!"):
            exclude_pattern = os.path.join(repo_root, pattern[1:])
            for match in glob.glob(exclude_pattern, recursive=True):
                matched.discard(match)

    return sorted(matched)


def extract_file_context(filepath: str, repo_root: str, max_lines: int = 200) -> dict:
    """
    Extract useful context from a Python file:
    - Relative path
    - Module docstring
    - Class/function signatures with their docstrings
    - First N lines if the file is small enough

    For large files, we prioritize structure over raw content.
    """
    rel_path = os.path.relpath(filepath, repo_root)

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {"path": rel_path, "error": str(e), "lines": 0}

    total_lines = len(lines)
    content = "".join(lines)

    context = {
        "path": rel_path,
        "lines": total_lines,
    }

    # For small files, include the whole thing
    if total_lines <= max_lines:
        context["content"] = content
        context["extraction"] = "full"
    else:
        # For large files, extract structure
        context["extraction"] = "structure"
        extracted_parts = []

        # Module docstring (first triple-quoted string)
        if '"""' in content[:2000] or "'''" in content[:2000]:
            for quote in ['"""', "'''"]:
                start = content.find(quote)
                if start != -1 and start < 500:  # Near top of file
                    end = content.find(quote, start + 3)
                    if end != -1:
                        extracted_parts.append(f"# Module docstring:\n{content[start:end+3]}")
                        break

        # Import block (first 50 lines max)
        import_lines = []
        for line in lines[:50]:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                import_lines.append(stripped)
        if import_lines:
            extracted_parts.append(f"# Imports:\n" + "\n".join(import_lines))

        # Class and function signatures with docstrings
        i = 0
        while i < total_lines:
            stripped = lines[i].strip()
            if stripped.startswith(("class ", "def ", "async def ")):
                sig_block = [lines[i].rstrip()]

                # Grab continuation lines (for multi-line signatures)
                j = i + 1
                while j < total_lines and not lines[j].strip().endswith(":"):
                    if lines[j].strip() == "" or lines[j].strip().startswith(("#", '"""', "'''")):
                        break
                    sig_block.append(lines[j].rstrip())
                    j += 1
                    if j - i > 10:  # Safety valve
                        break

                # Grab docstring if present
                docstring_lines = []
                doc_start = j
                if doc_start < total_lines:
                    doc_line = lines[doc_start].strip()
                    for quote in ['"""', "'''"]:
                        if doc_line.startswith(quote):
                            docstring_lines.append(lines[doc_start].rstrip())
                            if doc_line.endswith(quote) and len(doc_line) > 3:
                                break  # Single-line docstring
                            k = doc_start + 1
                            while k < total_lines:
                                docstring_lines.append(lines[k].rstrip())
                                if quote in lines[k]:
                                    break
                                k += 1
                                if k - doc_start > 20:  # Don't grab huge docstrings
                                    docstring_lines.append("    ...")
                                    break
                            break

                extracted_parts.append("\n".join(sig_block + docstring_lines))
            i += 1

        context["content"] = "\n\n".join(extracted_parts)

    return context


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code."""
    return len(text) // 4


def build_feature_prompt(feature: dict, file_contexts: list[dict], global_ctx: dict) -> str:
    """Build the user message for a single feature area's guide generation."""

    files_block = []
    for ctx in file_contexts:
        if "error" in ctx:
            files_block.append(f"### {ctx['path']} (ERROR: {ctx['error']})")
        else:
            extraction_note = f" [{ctx['extraction']}, {ctx['lines']} lines]"
            files_block.append(f"### {ctx['path']}{extraction_note}\n```python\n{ctx['content']}\n```")

    files_text = "\n\n".join(files_block)

    return f"""Generate a comprehensive product guide for the following feature area of {global_ctx['product_name']}.

## Product Context
- **Product**: {global_ctx['product_name']} — {global_ctx['product_type']}
- **Primary data source**: {global_ctx['primary_data_source']}
- **Tech stack**: {global_ctx['tech_stack']}
- **Deployment**: {global_ctx['deployment']}
- **Primary users**: {global_ctx['primary_users']}

## Feature Area
- **Name**: {feature['name']}
- **Description**: {feature['description']}
- **Target audience**: {feature['audience']}
- **Guide focus**: {feature['guide_focus']}

## Source Code Context

The following files make up this feature area. Use them to understand what the feature does, how it works, and what a user needs to know.

{files_text}

## Output Requirements

Write a product guide as a Guru knowledge base card. The guide should:

1. **Lead with what it does and why it matters** — not how it's built. The audience is {feature['audience']}, not developers.
2. **Cover the workflow** — what triggers this feature, what happens step by step, what the user sees/gets.
3. **Include configuration and settings** — if there are user-configurable options, document them.
4. **Document edge cases and gotchas** — things users commonly run into or need to know.
5. **Be specific** — use actual field names, actual menu paths, actual terminology from the code. Don't be vague.

Format as markdown suitable for a Guru card. Use headers, bullet points, and callout blocks where appropriate.
Do NOT include implementation details like class names, function signatures, or architectural patterns unless they're user-facing.
Do NOT pad with generic filler. If you don't have enough context for a section, say so explicitly so the human reviewer knows to fill it in.
"""


def call_claude(system_prompt: str, user_message: str, model: str, api_key: str) -> str:
    """Call the Anthropic API and return the text response."""
    payload = {
        "model": model,
        "max_tokens": 8192,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json=payload,
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text}")

    data = resp.json()
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]

    return text


def main():
    parser = argparse.ArgumentParser(description="Bootstrap product guides from codebase")
    parser.add_argument("--manifest", required=True, help="Path to feature-manifest.json")
    parser.add_argument("--repo-root", required=True, help="Path to the repo root")
    parser.add_argument("--output", required=True, help="Output directory for generated guides")
    parser.add_argument("--model", default="claude-sonnet-4-20250514", help="Claude model to use")
    parser.add_argument("--dry-run", action="store_true", help="Skip API calls, output prompts only")
    parser.add_argument("--features", nargs="*", help="Only process these feature IDs (space-separated)")
    parser.add_argument("--max-tokens-per-feature", type=int, default=120000,
                        help="Max input tokens per feature chunk")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: ANTHROPIC_API_KEY environment variable required (or use --dry-run)")
        sys.exit(1)

    # Load manifest
    with open(args.manifest) as f:
        manifest = json.load(f)

    global_ctx = manifest["global_context"]
    features = manifest["features"]

    # Filter features if specified
    if args.features:
        features = [f for f in features if f["id"] in args.features]
        if not features:
            print(f"ERROR: No features matched {args.features}")
            sys.exit(1)

    os.makedirs(args.output, exist_ok=True)

    system_prompt = (
        f"You are a product documentation writer for {global_ctx['product_name']}, "
        f"a {global_ctx['product_type']}. "
        f"You write clear, specific, actionable product guides for {global_ctx['primary_users']}. "
        f"You focus on what features do and how to use them, not how they're built. "
        f"You are honest about gaps — if the code doesn't give you enough context to document "
        f"something confidently, you flag it with [NEEDS REVIEW] rather than guessing."
    )

    results = []

    for feature in features:
        feature_id = feature["id"]
        print(f"\n{'='*60}")
        print(f"Processing: {feature['name']} ({feature_id})")
        print(f"{'='*60}")

        # Find matching files
        files = find_files_for_feature(args.repo_root, feature["file_patterns"])
        print(f"  Found {len(files)} files matching patterns")

        if not files:
            print(f"  ⚠️  No files found — check your glob patterns")
            results.append({
                "feature_id": feature_id,
                "feature_name": feature["name"],
                "status": "no_files",
                "files_found": 0,
            })
            continue

        # Extract context from each file
        file_contexts = []
        total_tokens = 0
        for filepath in files:
            ctx = extract_file_context(filepath, args.repo_root)
            ctx_tokens = estimate_tokens(ctx.get("content", ""))

            # Check if adding this file would bust the token budget
            if total_tokens + ctx_tokens > args.max_tokens_per_feature:
                print(f"  ⚠️  Token budget reached at {total_tokens} tokens, "
                      f"skipping remaining {len(files) - len(file_contexts)} files")
                # Still include the file but with truncated content
                ctx["content"] = ctx.get("content", "")[:1000] + "\n\n[TRUNCATED — token budget]"
                file_contexts.append(ctx)
                break

            file_contexts.append(ctx)
            total_tokens += ctx_tokens

        print(f"  Extracted context from {len(file_contexts)} files (~{total_tokens} tokens)")

        # Build the prompt
        user_message = build_feature_prompt(feature, file_contexts, global_ctx)

        if args.dry_run:
            # Write the prompt to disk for inspection
            prompt_path = os.path.join(args.output, f"{feature_id}_prompt.txt")
            with open(prompt_path, "w") as f:
                f.write(f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER MESSAGE ===\n{user_message}")
            print(f"  [DRY RUN] Prompt written to {prompt_path}")
            print(f"  [DRY RUN] Estimated input tokens: ~{estimate_tokens(system_prompt + user_message)}")
            results.append({
                "feature_id": feature_id,
                "feature_name": feature["name"],
                "status": "dry_run",
                "files_found": len(files),
                "files_extracted": len(file_contexts),
                "estimated_tokens": estimate_tokens(system_prompt + user_message),
                "prompt_path": prompt_path,
            })
            continue

        # Call Claude
        print(f"  Calling Claude ({args.model})...")
        try:
            guide_content = call_claude(system_prompt, user_message, args.model, api_key)

            # Write the guide
            guide_path = os.path.join(args.output, f"{feature_id}_guide.md")
            with open(guide_path, "w") as f:
                f.write(guide_content)

            # Write the full output record
            record = {
                "feature_id": feature_id,
                "feature_name": feature["name"],
                "status": "success",
                "files_found": len(files),
                "files_extracted": len(file_contexts),
                "guide_path": guide_path,
                "guru_collection": feature.get("guru_collection"),
                "guru_card_id": feature.get("guru_card_id"),
                "guide_content": guide_content,
            }
            record_path = os.path.join(args.output, f"{feature_id}_record.json")
            with open(record_path, "w") as f:
                json.dump(record, f, indent=2)

            results.append(record)
            print(f"  ✅ Guide written to {guide_path}")

            # Rate limit courtesy — don't hammer the API
            time.sleep(2)

        except Exception as e:
            print(f"  ❌ Error: {e}")
            results.append({
                "feature_id": feature_id,
                "feature_name": feature["name"],
                "status": "error",
                "error": str(e),
            })

    # Write summary
    summary_path = os.path.join(args.output, "bootstrap_summary.json")
    with open(summary_path, "w") as f:
        json.dump({
            "model": args.model,
            "repo_root": args.repo_root,
            "features_processed": len(results),
            "results": results,
        }, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Bootstrap complete. Summary: {summary_path}")
    print(f"{'='*60}")

    # Print status table
    for r in results:
        status_icon = {"success": "✅", "dry_run": "🔍", "error": "❌", "no_files": "⚠️"}.get(r["status"], "?")
        print(f"  {status_icon} {r['feature_name']}: {r['status']}")


if __name__ == "__main__":
    main()
