"""Tonight's harness for the Renn content-update loop.

Runs the pipeline end-to-end against LIVE clients (Haiku via the local `claude`
CLI, live Guru, live Asana), stages a draft, prints the change plan + a unified
diff + any validator issues, and — only with --publish and an explicit y —
pushes the update back to Guru.

Examples:
  # point directly at a card, source = an uploaded text/md attachment on a task
  python -m scripts.run_content_update_demo --asana-task 12345 --card https://app.getguru.com/card/abcd --publish

  # let Renn search within targeted collections
  python -m scripts.run_content_update_demo --doc ./new_policy.md --search "Aetna copay" --collection Billing

  # one-time: enable Haiku via the CLI + route enablement to Claude
  python -m scripts.run_content_update_demo --setup-haiku
"""

from __future__ import annotations

import argparse
import sys
import urllib.request

from src.data.connection_factory import get_connection
from src.data.content_update import (
    ContentUpdateRequest, Deps, approve_and_publish, run_content_update,
)


def _http_get(url: str, timeout: int = 30, max_bytes: int = 8_000_000) -> str:
    # The url comes from an Asana attachment's download_url (attacker-influenced:
    # anyone who can attach a file to the watched task controls it). Restrict to
    # http(s) so a file:// / internal URL can't read local files or SSRF, and
    # cap the read so a huge/streaming URL can't OOM the harness.
    from urllib.parse import urlparse
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) attachment url: {url}")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — scheme checked above
        return resp.read(max_bytes).decode("utf-8", errors="replace")


def _guru_client():
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        print("WARNING: Guru not configured (set guru_email / guru_api_token).")
        return None
    return GuruClient(email, token)


def _asana_client():
    from src.data.asana_client import AsanaClient
    client = AsanaClient.from_store()
    return client if client.api_key else None


def _drive_reader():
    try:
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
        return reader if reader.is_configured() else None
    except Exception:  # noqa: BLE001 — Drive is optional for uploaded attachments
        return None


def _llm_client():
    from src.gemini.client_factory import build_client_for_task
    client = build_client_for_task("enablement_card_update")
    print(f"LLM client: {type(client).__name__} "
          f"(model={getattr(client, 'model', getattr(client, 'model_id', '?'))})")
    return client


def _setup_haiku() -> None:
    from src.data.settings_manager import get_section, set_section
    from src.llm.model_registry import ModelRegistry
    reg = ModelRegistry.instance()
    reg.enable("claude-haiku-4-5")
    reg.set_active("claude-haiku-4-5")
    enablement = get_section("enablement", {}) or {}
    enablement["provider"] = "claude"
    set_section("enablement", enablement)
    print("Enabled claude-haiku-4-5, set active, routed enablement -> claude (CLI).")


def _build_request(args) -> ContentUpdateRequest:
    return ContentUpdateRequest(
        asana_task_gid=args.asana_task,
        attachment_selector=args.attachment,
        source_doc_ref=args.doc,
        source_text=args.text,
        source_title=args.title,
        target_card_ref=args.card,
        target_card_name=args.card_name,
        search_query=args.search,
        collections=args.collection or [],
        reference_refs=args.reference or [],
        approved_by=args.approved_by,
    )


def _print_result(res) -> None:
    print(f"\n=== status: {res.status} (stage: {res.stage}) ===")
    if res.status == "ambiguous_card":
        print("Renn isn't sure which card to update. Candidates:")
        for c in res.candidates:
            print(f"  - {c.card_id}  {c.title!r}  (score {c.score})")
        print("Re-run with --card <id/url> or --card-name \"<exact title>\".")
        return
    if not res.ok:
        print(f"FAILED: {res.error}")
        return
    if res.plan:
        print(f"\nSummary: {res.plan.summary}")
        for ch in res.plan.changes:
            print(f"  [{ch.type}] {ch.section}: {ch.reason}")
    print("\n--- DIFF (current -> proposed) ---")
    print(res.diff or "(no textual diff)")
    if res.issues:
        print("\n--- VALIDATOR ISSUES (review before publish) ---")
        for issue in res.issues:
            print(f"  ! {issue}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Renn content-update demo")
    ap.add_argument("--setup-haiku", action="store_true", help="enable Haiku via CLI then exit")
    ap.add_argument("--asana-task", help="Asana task GID whose attachment is the source")
    ap.add_argument("--attachment", help="substring to pick the attachment by name")
    ap.add_argument("--doc", help="source doc: local path / stored doc_id / Drive ref")
    ap.add_argument("--text", help="raw source text (skips Asana/Drive)")
    ap.add_argument("--title", help="source title override")
    ap.add_argument("--card", help="target Guru card id or app.getguru.com URL")
    ap.add_argument("--card-name", help="exact target card title")
    ap.add_argument("--search", help="search query if no card given")
    ap.add_argument("--collection", action="append", help="scope search to collection (repeatable)")
    ap.add_argument("--reference", action="append", help="extra reference ref (repeatable)")
    ap.add_argument("--approved-by", default="demo")
    ap.add_argument("--publish", action="store_true", help="offer to push the update to Guru")
    args = ap.parse_args(argv)

    if args.setup_haiku:
        _setup_haiku()
        return 0

    deps = Deps(
        llm_client=_llm_client(),
        guru_client=_guru_client(),
        asana_client=_asana_client(),
        http_get=_http_get,
        drive_reader=_drive_reader(),
    )
    if deps.llm_client is None:
        print("ERROR: no LLM client could be built.")
        return 2

    conn = get_connection()
    res = run_content_update(conn, _build_request(args), deps)
    _print_result(res)

    if res.ok and res.status == "staged" and args.publish:
        answer = input("\nPublish this update to Guru? [y/N] ").strip().lower()
        if answer == "y":
            out = approve_and_publish(conn, deps.guru_client, res.draft_id,
                                      approved_by=args.approved_by)
            print(f"publish: {out.get('status', out.get('error'))} "
                  f"(card_id={out.get('card_id')})")
        else:
            print("Left staged as a pending draft — not published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
