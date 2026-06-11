"""Configure + verify the LIVE Enablement demo: real Google Drive in, real Guru out.

Idempotent, arg-driven (no interactive prompts). Every run ends with a status
report. Typical sequence:

    python scripts/setup_live_demo.py --status
    python scripts/setup_live_demo.py --guru-token <TOKEN>
    python scripts/setup_live_demo.py --list-collections
    python scripts/setup_live_demo.py --collection "Provider Enablement"
    python scripts/setup_live_demo.py --drive-creds C:/path/sa.json --folder-id <ID> --folder-name "Demo Docs"
    python scripts/setup_live_demo.py --go-live
    python scripts/setup_live_demo.py --scan          # real Drive -> index -> Gemini draft -> task
    python scripts/setup_live_demo.py --push-test     # REALLY posts a test card to Guru

Notes:
  - The Drive folder must be shared (Viewer) with the service-account email the
    status report prints. The GCP project needs the Drive API enabled.
  - --scan drafts via the live enablement LLM lane (real Gemini, ~30s/doc).
  - --push-test creates a real Guru card in the configured collection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.data.pat_store import load_setting, save_setting          # noqa: E402
from src.data.settings_manager import get_section, set_section     # noqa: E402


def _warehouse():
    from src.data.db_manager import DatabaseManager
    db = DatabaseManager()          # default data/local_warehouse.db
    db.initialize()                 # idempotent; applies migration 027 if missing
    return db


def _merge_enablement(**patch) -> dict:
    cfg = dict(get_section("enablement", {}) or {})
    for key, value in patch.items():
        if isinstance(value, dict):
            sub = dict(cfg.get(key) or {})
            sub.update(value)
            cfg[key] = sub
        else:
            cfg[key] = value
    set_section("enablement", cfg)
    return cfg


def _guru_client():
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return None
    return GuruClient(email, token)


def cmd_list_collections() -> int:
    client = _guru_client()
    if client is None:
        print("[guru] credentials incomplete - set --guru-email/--guru-token first")
        return 1
    cols = client.list_collections()
    print(f"[guru] {len(cols)} collections:")
    for c in cols:
        print(f"   {c.get('id')}  -  {c.get('name')}")
    return 0


def cmd_set_collection(name_or_id: str) -> int:
    client = _guru_client()
    if client is None:
        print("[guru] credentials incomplete - set the token first")
        return 1
    cols = client.list_collections()
    match = next((c for c in cols if c.get("id") == name_or_id), None) or next(
        (c for c in cols if (c.get("name") or "").lower() == name_or_id.lower()), None)
    if not match:
        print(f"[guru] no collection matching {name_or_id!r}; use --list-collections")
        return 1
    _merge_enablement(guru={"publish_collection_id": match["id"]})
    print(f"[guru] publish collection -> {match['name']} ({match['id']})")
    return 0


def cmd_add_folder(folder_id: str, name: str) -> int:
    from src.data import enablement_sources as sources
    db = _warehouse()
    sources.add_source(db.conn, source_type="drive", source_id=f"drive:{folder_id}",
                       display_name=name, config={"folder_id": folder_id, "recursive": True})
    print(f"[drive] watching folder {folder_id} ({name}) in monitor_sources")
    return 0


def cmd_scan() -> int:
    from src.data import drive_monitor
    db = _warehouse()
    print("[scan] polling the real Drive (drafting via live LLM - ~30s per new doc)...")
    out = drive_monitor.poll_once(db.conn)
    if out.get("skipped"):
        print("[scan] SKIPPED - Drive read not configured (creds/read_enabled).")
        return 1
    print(f"[scan] documents={len(out['documents'])} drafts={len(out['drafts'])} "
          f"tasks={len(out['tasks'])}")
    from src.data import enablement_store as store
    for d in store.list_drafts(db.conn, status="pending", limit=10):
        print(f"   draft {d['id']}: {d['title']}")
    return 0


def cmd_push_test() -> int:
    from src.data import enablement_store as store
    client = _guru_client()
    if client is None:
        print("[guru] credentials incomplete")
        return 1
    collection = (get_section("enablement", {}).get("guru") or {}).get("publish_collection_id")
    if not collection:
        print("[guru] no publish_collection_id configured - use --collection")
        return 1
    db = _warehouse()
    did = store.save_card_draft(
        db.conn, title="Alma Enablement - connectivity test card",
        content="This card verifies the live Enablement -> Guru publish path. Safe to delete.")
    res = store.publish_draft(db.conn, did, guru_client=client, collection_id=collection)
    print(f"[guru] push-test -> {res}")
    return 0 if res.get("ok") else 1


def cmd_status() -> int:
    print("=" * 64)
    print("LIVE DEMO STATUS")
    print("=" * 64)
    cfg = get_section("enablement", {}) or {}
    drive_cfg = cfg.get("drive") or {}
    print(f"demo_mode:               {cfg.get('demo_mode', True)}   (live demo needs False)")
    print(f"provider:                {cfg.get('provider', 'gemini')}")
    print(f"publish_collection_id:   {(cfg.get('guru') or {}).get('publish_collection_id') or '(unset)'}")
    print(f"drive.credentials_path:  {drive_cfg.get('credentials_path') or '(unset)'}")
    print(f"drive.read_enabled:      {drive_cfg.get('read_enabled', False)}")

    # creds presence (never echo secrets)
    print(f"guru_email set:          {bool(load_setting('guru_email', ''))}")
    print(f"guru_api_token set:      {bool(load_setting('guru_api_token', ''))}")
    print(f"asana_api_key set:       {bool(load_setting('asana_api_key', ''))}")

    # watched folders
    try:
        from src.data import enablement_sources as sources
        db = _warehouse()
        folders = sources.list_sources(db.conn, "drive")
        print(f"watched Drive folders:   {len(folders)}")
        for f in folders:
            print(f"   {f['source_id']}  ({f['display_name']})  last={f['last_status'] or 'never'}")
    except Exception as exc:  # noqa: BLE001
        print(f"watched Drive folders:   error - {exc}")

    # live connection probes
    print("-" * 64)
    creds_path = drive_cfg.get("credentials_path") or ""
    if creds_path and Path(creds_path).is_file():
        try:
            sa = json.load(open(creds_path, encoding="utf-8"))
            print(f"service account:         {sa.get('client_email')}")
            print("   -> share the demo Drive folder with this email (Viewer).")
        except Exception:
            pass
        from src.data.drive_reader import DriveReader
        ok, msg = DriveReader(creds_path).test_connection()
        print(f"Drive API:               {'OK' if ok else 'FAIL'} - {msg}")
    else:
        print("Drive API:               not configured (--drive-creds)")

    client = _guru_client()
    if client is not None:
        try:
            ok = bool(client.test_connection())
            print(f"Guru API:                {'OK' if ok else 'FAIL'}")
        except Exception as exc:  # noqa: BLE001
            print(f"Guru API:                FAIL - {exc}")
    else:
        print("Guru API:                credentials incomplete (--guru-token)")

    try:
        from src.gemini.client_factory import build_client_for_task
        llm = build_client_for_task("enablement_card_gen")
        print(f"LLM lane:                {type(llm).__name__} ({getattr(llm, 'model', '?')})")
    except Exception as exc:  # noqa: BLE001
        print(f"LLM lane:                FAIL - {exc}")
    print("=" * 64)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--guru-email")
    ap.add_argument("--guru-token")
    ap.add_argument("--asana-pat")
    ap.add_argument("--drive-creds", help="path to the service-account JSON")
    ap.add_argument("--folder-id", help="Drive folder ID to watch")
    ap.add_argument("--folder-name", default="Demo Docs")
    ap.add_argument("--collection", help="Guru collection (name or id) to publish to")
    ap.add_argument("--list-collections", action="store_true")
    ap.add_argument("--go-live", action="store_true", help="set enablement.demo_mode=false")
    ap.add_argument("--demo-mode", action="store_true", help="set enablement.demo_mode=true")
    ap.add_argument("--scan", action="store_true", help="run a real Drive poll now")
    ap.add_argument("--push-test", action="store_true", help="publish a test card to Guru (REAL write)")
    args = ap.parse_args()

    rc = 0
    if args.guru_email:
        save_setting("guru_email", args.guru_email)
        print("[guru] email stored")
    if args.guru_token:
        save_setting("guru_api_token", args.guru_token)
        print("[guru] token stored (keyring)")
    if args.asana_pat:
        save_setting("asana_api_key", args.asana_pat)
        print("[asana] PAT stored (keyring)")
    if args.drive_creds:
        path = str(Path(args.drive_creds).resolve())
        _merge_enablement(drive={"credentials_path": path, "read_enabled": True})
        print(f"[drive] credentials_path -> {path}; read_enabled -> True")
    if args.folder_id:
        rc |= cmd_add_folder(args.folder_id, args.folder_name)
    if args.list_collections:
        rc |= cmd_list_collections()
    if args.collection:
        rc |= cmd_set_collection(args.collection)
    if args.go_live:
        _merge_enablement(demo_mode=False)
        print("[mode] demo_mode -> False (LIVE)")
    if args.demo_mode:
        _merge_enablement(demo_mode=True)
        print("[mode] demo_mode -> True")
    if args.scan:
        rc |= cmd_scan()
    if args.push_test:
        rc |= cmd_push_test()

    cmd_status()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
