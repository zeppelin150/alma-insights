"""Drive search evaluation RUNNER — points at a real folder, indexes, searches, scores.

This is the LIVE half of the harness. It is a script, not a test: pytest's
``testpaths = tests`` (pytest.ini) means it is never collected, so the default
``-m "not live"`` filter is irrelevant to it — it simply cannot run under
pytest. It only ever runs when the owner invokes it by hand.

It drives the real funnel, in order:

    list_drives  ->  list_folders  ->  set_drive_folder  ->  index  ->  search

PROOF-OF-CALL, not proof-of-no-exception
----------------------------------------
``list_drives`` (drive_reader.py:111) and ``list_folders`` (drive_reader.py:137)
return ``[]`` WITHOUT building a service when Drive is not configured. A run
that raises nothing can therefore mean "no Drive call ever happened". Every
stage below is recorded through :class:`ProbedReader`, which reports whether a
service object was actually constructed. A stage with ``service_built=False``
is printed as UNPROVEN and never counted as a success.

WRITES
------
``--index-mode kb`` is NOT read-only. It runs the real KB ingest, which:
  * sends extracted document text to Haiku via the local ``claude`` CLI
    (kb/ingest.py:71-82) — the enablement lane disables aggressive PII
    redaction, so the target Drive MUST be synthetic. No PHI. Ever.
  * creates folders and uploads a ``.kb_marker`` file into Drive
    (kb/drive_kb.py:42, 164-171).
It therefore requires --yes-i-understand-this-writes and prints a loud banner.

``--index-mode mirror`` (the default) exports text into the LOCAL warehouse
only: no LLM, no Drive writes, no cards. It still writes to
``data/local_warehouse.db``. That is enough to exercise Stage 3's full-text
floor, which is the stage that actually runs in production today.

Usage
-----
    python scripts/drive_eval_preflight.py                  # check readiness first

    # enumerate what the connected account can see (read-only)
    python scripts/run_drive_eval.py --list-drives

    # mirror a folder locally, then run queries and score them
    python scripts/run_drive_eval.py --folder-id <ID> --index-mode mirror \
        --queries evals/drive/gold.yaml --report evals/drive/report.md

    # search only, no indexing
    python scripts/run_drive_eval.py --queries evals/drive/queries.txt \
        --search-mode kb --report evals/drive/report.md

    # full KB build (LLM + Drive writes)
    python scripts/run_drive_eval.py --folder-id <ID> --index-mode kb \
        --yes-i-understand-this-writes

Queries file may be either a gold.yaml (query + ground truth) or a .txt with
one query per line. With a .txt there is no ground truth, so the runner emits
``<report>.judgments.csv`` for the owner to mark up; feed that back with
--judgments to get scores. See docs/DRIVE_SEARCH_EVAL.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from tests.drive_eval import scorer as SC  # noqa: E402


# ── call proof ─────────────────────────────────────────────────────────────

class ProbedReader:
    """Delegating wrapper that records whether each Drive call really happened.

    ``service_built`` is read off the underlying DriveReader's ``_service``
    attribute AFTER the call. Because ``_build_service`` caches into
    ``_service`` (drive_reader.py:64-84), a non-None value is positive evidence
    that the client was constructed — i.e. that the unconfigured early-return
    path at drive_reader.py:111/137 was NOT taken.
    """

    def __init__(self, reader):
        self._reader = reader
        self.calls: list[dict] = []

    @property
    def configured(self) -> bool:
        try:
            return bool(self._reader.is_configured())
        except Exception:  # noqa: BLE001
            return False

    def _invoke(self, name: str, *args, **kwargs):
        started = time.monotonic()
        rec: dict = {"call": name, "service_built": False, "ok": False,
                     "count": 0, "error": "", "seconds": 0.0}
        try:
            result = getattr(self._reader, name)(*args, **kwargs)
            rec["ok"] = True
            rec["count"] = len(result) if isinstance(result, (list, tuple)) else 1
            return result
        except Exception as exc:  # noqa: BLE001 — a failed stage must still report
            rec["error"] = f"{type(exc).__name__}: {exc}"
            return [] if name.startswith("list_") else None
        finally:
            rec["service_built"] = getattr(self._reader, "_service", None) is not None
            rec["seconds"] = round(time.monotonic() - started, 2)
            self.calls.append(rec)

    def list_drives(self):
        return self._invoke("list_drives")

    def list_folders(self, parent_id):
        return self._invoke("list_folders", parent_id)

    def list_changed_files(self, folder_id, **kw):
        return self._invoke("list_changed_files", folder_id, **kw)

    def export_text(self, file_id, mime_type):
        return self._invoke("export_text", file_id, mime_type)

    def search_files(self, query, limit=20):
        return self._invoke("search_files", query, limit=limit) or []

    def is_configured(self):
        return self._reader.is_configured()

    # ---- reporting -------------------------------------------------------

    def proof_table(self) -> str:
        if not self.calls:
            return "  (no Drive calls attempted)"
        w = max(len(c["call"]) for c in self.calls)
        lines = []
        for c in self.calls:
            verdict = ("PROVEN " if c["service_built"] and c["ok"]
                       else "FAILED " if c["error"]
                       else "UNPROVEN")
            # Google HttpError bodies are enormous and echo the full request
            # URL (query string included). Truncate: the reason code is what
            # matters, and the full text stays in the .run.json.
            note = (c["error"][:180] + "…" if len(c["error"]) > 180
                    else c["error"]) or f"{c['count']} result(s), {c['seconds']}s"
            lines.append(f"  {verdict}  {c['call']:<{w}}  {note}")
        return "\n".join(lines)

    @property
    def any_proven(self) -> bool:
        return any(c["service_built"] and c["ok"] for c in self.calls)

    def verdict(self) -> str:
        """Why nothing was proven — the three causes need different fixes."""
        if not self.calls or self.any_proven:
            return ""
        if any(c["error"] for c in self.calls):
            return ("*** No Drive call SUCCEEDED. A client WAS constructed and "
                    "the request WAS sent, but the API rejected it (see the "
                    "errors above). This is a credentials/scope/API-enablement "
                    "problem, not an empty Drive. ***")
        if not any(c["service_built"] for c in self.calls):
            return ("*** NO Drive call was PROVEN. Every list_* returned "
                    "WITHOUT constructing a service — i.e. is_configured() was "
                    "False and the calls short-circuited to [] "
                    "(drive_reader.py:111,137). The empty results above reflect "
                    "an unconfigured client, NOT an empty Drive. ***")
        return "*** No Drive call was proven successful. ***"


# ── banner ─────────────────────────────────────────────────────────────────

_WRITE_BANNER = """
################################################################################
#                                                                              #
#   WRITE MODE — THIS IS NOT A READ-ONLY RUN                                   #
#                                                                              #
#   --index-mode kb will:                                                      #
#     * send extracted DOCUMENT TEXT to Haiku via the local `claude` CLI       #
#       (src/data/kb/ingest.py:71-82). The enablement lane disables            #
#       aggressive PII redaction. The target Drive MUST be synthetic.          #
#       DO NOT POINT THIS AT REAL PATIENT OR CUSTOMER DATA.                    #
#     * CREATE FOLDERS in Drive and upload a `.kb_marker` file                 #
#       (src/data/kb/drive_kb.py:42, 164-171).                                 #
#     * write cards + queue rows into data/local_warehouse.db.                 #
#                                                                              #
################################################################################
"""


def _confirm_writes(args) -> bool:
    print(_WRITE_BANNER)
    if not args.yes_i_understand_this_writes:
        print("REFUSED: --index-mode kb requires --yes-i-understand-this-writes\n",
              file=sys.stderr)
        return False
    print("  --yes-i-understand-this-writes supplied; proceeding.\n")
    return True


# ── stages ─────────────────────────────────────────────────────────────────

def stage_enumerate(probed: ProbedReader, parent: str = "root") -> dict:
    """list_drives -> list_folders. Read-only."""
    print("\n[stage 1] enumerate drives + folders")
    if not probed.configured:
        print("  Drive is NOT configured/connected this session — list_drives "
              "would return [] without building a service.\n"
              "  Run scripts/drive_eval_preflight.py to see why.")
    drives = probed.list_drives() or []
    for d in drives:
        print(f"    drive  {d.get('id'):<20} {d.get('name')}")
    folders = probed.list_folders(parent) or []
    for f in folders:
        print(f"    folder {f.get('id'):<20} {f.get('name')}")
    return {"drives": drives, "folders": folders}


def stage_set_folder(conn, folder_id: str, folder_name: str = "") -> dict:
    """Persist the folder into enablement.drive.active_folders (settings write)."""
    print("\n[stage 2] set_drive_folder (writes data/settings.yaml)")
    from src.data.chat_tools.enablement_tools import _set_drive_folder_impl
    out = _set_drive_folder_impl(conn, folder_id, folder_name or None, None)
    print(f"    -> {out}")
    return out


def stage_index_mirror(conn, probed: ProbedReader, folder_id: str,
                       max_docs: int) -> dict:
    """Enumerate the folder tree and store FULL TEXT into enablement_documents.

    Deliberately reuses kb.ingest.enumerate_folder so the eval walks the tree
    the same way production does (explicit BFS; it passes recursive=False
    because DriveReader ignores that parameter — see tests/test_drive_query.py).
    No LLM, no Drive writes, no cards.
    """
    print(f"\n[stage 3] index (mirror mode) folder={folder_id}")
    from src.data import enablement_store
    from src.data.kb.ingest import MAX_DEPTH, enumerate_folder

    files, truncated = enumerate_folder(probed, folder_id, cap=max_docs)
    print(f"    enumerated {len(files)} file(s) (depth<={MAX_DEPTH}, cap={max_docs})"
          f"{' TRUNCATED' if truncated else ''}")

    stored = failed = 0
    for f in files:
        fid = f.get("id") or ""
        name = f.get("name") or "document"
        mime = f.get("mimeType") or f.get("mime_type") or ""
        text = probed.export_text(fid, mime) or ""
        if not text:
            failed += 1
            continue
        try:
            enablement_store.save_document(
                conn, source="drive", doc_id=fid, name=name, source_ref=fid,
                mime_type=mime, web_url=f.get("webViewLink") or f.get("url") or "",
                modified_time=f.get("modifiedTime") or f.get("modified_time") or "",
                full_text=text)
            stored += 1
        except Exception as exc:  # noqa: BLE001
            print(f"    save failed for {name}: {exc}")
            failed += 1
    print(f"    stored {stored}, no-text/failed {failed}")
    return {"enumerated": len(files), "stored": stored, "failed": failed,
            "truncated": truncated}


def stage_index_kb(conn, probed: ProbedReader, folder_id: str, topic: str) -> dict:
    """Full KB ingest: LLM summaries + Drive writes. Gated by the banner."""
    print(f"\n[stage 3] index (KB mode) folder={folder_id}")
    from src.data.kb.ingest import index_folder
    out = index_folder(conn, folder_id, reader=probed, topic_hint=topic)
    print(f"    -> {out}")
    return out


def stage_search(conn, queries: list[SC.Query], mode: str, limit: int,
                 probed: ProbedReader | None) -> list[SC.RunResult]:
    """Run each query through the chosen search path."""
    print(f"\n[stage 4] search (mode={mode}, limit={limit})")
    runs: list[SC.RunResult] = []
    for q in queries:
        titles: list[str] = []
        keys: list[str] = []
        aliases: list[set[str]] = []
        err = ""

        def _add(primary: str, title: str) -> None:
            """Record one result under BOTH its id and its title, so a gold
            entry written either way matches (see RunResult.aliases)."""
            key = SC.normalize_key(primary or title)
            titles.append(title)
            keys.append(key)
            aliases.append({k for k in (key, SC.normalize_key(title)) if k})

        try:
            if mode == "kb":
                from src.data.kb.search import kb_search
                for r in kb_search(conn, q.query, limit=limit):
                    _add(r.get("doc_id") or r.get("card_id") or "",
                         r.get("title") or "")
            elif mode == "local":
                from src.data.drive_query import query_business_drive
                out = query_business_drive(conn, q.query, limit=limit)
                err = out.get("error", "")
                for r in out.get("results", []):
                    _add(r.get("doc_id") or "", r.get("name") or r.get("title") or "")
            elif mode == "live":
                # The seam no production caller uses (drive_query.py:40-46).
                # Injecting it here is the ONLY way to exercise Stage 2.
                from src.data.drive_query import query_business_drive
                out = query_business_drive(conn, q.query, limit=limit,
                                           live_client=probed)
                err = out.get("error", "")
                for r in out.get("results", []):
                    _add(r.get("id") or "", r.get("name") or "")
            else:
                raise ValueError(f"unknown search mode {mode!r}")
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        print(f"    {q.id:<8} {len(keys):>3} hit(s)  {q.query[:56]}"
              + (f"   ERROR {err}" if err else ""))
        runs.append(SC.RunResult(query_id=q.id, retrieved=keys, titles=titles,
                                 aliases=aliases, error=err))
    return runs


# ── query loading ──────────────────────────────────────────────────────────

def load_queries(path: str) -> tuple[list[SC.Query], str]:
    """Returns (queries, ground_truth_mode)."""
    p = Path(path)
    if p.suffix.lower() in (".yaml", ".yml"):
        qs = SC.load_gold_yaml(p)
        has_gold = any(q.has_ground_truth for q in qs)
        return qs, ("gold" if has_gold else "none")
    lines = [ln.strip() for ln in p.read_text("utf-8").splitlines()]
    qs = [SC.Query(id=f"q{i}", query=ln)
          for i, ln in enumerate((x for x in lines if x and not x.startswith("#")), 1)]
    if not qs:
        raise ValueError(f"{path}: no queries found")
    return qs, "none"


# ── main ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder-id", default="", help="Drive folder id to index")
    ap.add_argument("--folder-name", default="", help="display name for the folder")
    ap.add_argument("--list-drives", action="store_true",
                    help="enumerate drives/folders and exit (read-only)")
    ap.add_argument("--parent", default="root",
                    help="parent id for --list-drives folder listing (default: root)")
    ap.add_argument("--index-mode", choices=("none", "mirror", "kb"), default="mirror",
                    help="none | mirror (local full text, no LLM/no Drive writes) | "
                         "kb (full ingest: LLM + Drive writes)")
    ap.add_argument("--max-docs", type=int, default=50,
                    help="cap for mirror-mode enumeration (default 50, matching "
                         "kb.ingest.MAX_DOCS_PER_JOB)")
    ap.add_argument("--topic", default="", help="topic hint for --index-mode kb")
    ap.add_argument("--set-folder", action="store_true",
                    help="persist --folder-id into enablement.drive.active_folders")
    ap.add_argument("--queries", default="", help="gold.yaml or one-per-line .txt")
    ap.add_argument("--judgments", default="",
                    help="score a previously-judged CSV instead of running queries")
    ap.add_argument("--search-mode", choices=("kb", "local", "live"), default="kb",
                    help="kb = kb_search FTS5 + LIKE floor (production path); "
                         "local = query_business_drive local branch; "
                         "live = query_business_drive with an INJECTED live client")
    ap.add_argument("--limit", type=int, default=10, help="results per query")
    ap.add_argument("--report", default="", help="markdown report path (also writes .json)")
    ap.add_argument("--db", default="", help="warehouse path (default: data/local_warehouse.db)")
    ap.add_argument("--yes-i-understand-this-writes", action="store_true",
                    help="required for --index-mode kb")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if os.environ.get("ALMA_MCP_MODE"):
        print("REFUSED: ALMA_MCP_MODE is set — Google access never runs in the "
              "MCP subprocess (google_oauth.py:39-54).", file=sys.stderr)
        return 2

    # --judgments is a pure offline scoring path: no Drive, no DB.
    if args.judgments:
        queries, runs = SC.load_judgments_csv(args.judgments)
        report = SC.score(queries, runs, ground_truth_mode="judgments",
                          run_label=f"judgments:{Path(args.judgments).name}",
                          generated_at=datetime.now(timezone.utc).isoformat())
        print(SC.to_markdown(report))
        if args.report:
            md, js = SC.write_report(report, args.report)
            print(f"\nwrote {md}\nwrote {js}")
        return 0

    if args.index_mode == "kb" and not _confirm_writes(args):
        return 2

    from src.data.connection_factory import get_connection
    from src.data.drive_reader import DriveReader

    db_path = args.db or str(_ROOT / "data" / "local_warehouse.db")
    conn = get_connection(db_path)
    probed = ProbedReader(DriveReader.from_settings())
    summary: dict = {"db": db_path, "configured": probed.configured}

    try:
        needs_drive = bool(args.list_drives or args.folder_id
                           or args.search_mode == "live")
        if needs_drive and not probed.configured:
            print("\nWARNING: DriveReader.is_configured() is False. Drive calls "
                  "will short-circuit to [] WITHOUT building a service — any "
                  "'success' below would be meaningless. Run "
                  "scripts/drive_eval_preflight.py.")

        if args.list_drives:
            summary["enumerate"] = stage_enumerate(probed, args.parent)
        if args.folder_id and args.set_folder:
            summary["set_folder"] = stage_set_folder(conn, args.folder_id,
                                                     args.folder_name)
        if args.folder_id and args.index_mode == "mirror":
            summary["index"] = stage_index_mirror(conn, probed, args.folder_id,
                                                  args.max_docs)
        elif args.folder_id and args.index_mode == "kb":
            summary["index"] = stage_index_kb(conn, probed, args.folder_id,
                                              args.topic)

        report = None
        if args.queries:
            queries, gt_mode = load_queries(args.queries)
            runs = stage_search(conn, queries, args.search_mode, args.limit,
                                probed)
            report = SC.score(
                queries, runs, ground_truth_mode=gt_mode,
                run_label=f"{args.search_mode} / {Path(args.queries).name}",
                generated_at=datetime.now(timezone.utc).isoformat())

            if gt_mode == "none":
                # No ground truth -> emit the judgment sheet instead of
                # pretending the zeros mean anything.
                base = Path(args.report or (_ROOT / "evals" / "drive" / "report.md"))
                csv_path = base.with_suffix(".judgments.csv")
                SC.write_judgment_csv(queries, runs, csv_path)
                print(f"\nNo ground truth supplied — wrote {csv_path}\n"
                      f"Mark the 'relevant' column (1/0) and re-run:\n"
                      f"    python scripts/run_drive_eval.py --judgments "
                      f"{csv_path} --report {base}")
            else:
                print()
                print(SC.to_markdown(report))
    finally:
        # ── proof of call ────────────────────────────────────────────────
        print("\n[proof] Drive calls actually made")
        print(probed.proof_table())
        verdict = probed.verdict()
        if verdict:
            print(f"\n  {verdict}")
        summary["drive_calls"] = probed.calls
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if args.report and report is not None:
        md, js = SC.write_report(report, args.report)
        print(f"\nwrote {md}\nwrote {js}")
    if args.report:
        Path(args.report).with_suffix(".run.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
