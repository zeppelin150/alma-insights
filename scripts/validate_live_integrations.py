"""Live integration validator for the Guru + Asana enablement back-end.

Runs the same calls the enablement UI makes — against real Guru/Asana — and
reports what populated, with counts, distinguishing four outcomes per provider:

    NOT-CONFIGURED  no credentials in pat_store
    AUTH-FAILED     credentials present but rejected (e.g. Guru 401)
    OK-EMPTY        authenticated, but the account/board has no data yet
    OK-POPULATED    authenticated and data flowed into the local warehouse

Usage
-----
    # validate using creds already saved in pat_store (encrypted keyring):
    python scripts/validate_live_integrations.py

    # persist creds from env into pat_store first, then validate
    # (keeps secrets out of argv / shell history / process list):
    #   PowerShell:  $env:GURU_EMAIL=...; $env:GURU_API_TOKEN=...; $env:ASANA_API_KEY=...
    python scripts/validate_live_integrations.py --save

Guru auth is Basic base64(email:token); a User API token is generated under the
signed-in account at Guru → profile → Apps & Integrations → API Access. (If the
token is a Collection token, pass the Collection ID as GURU_EMAIL.)
Asana auth is a Personal Access Token (Bearer); validated via /users/me.

Secrets are never printed (only a length-masked fingerprint) and only ever read
from / written to pat_store (OS keyring) — nothing is committed or logged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python scripts/validate_live_integrations.py` from the project root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Live Guru/Asana data (card titles, user names) may contain non-ASCII; the
# Windows console defaults to cp1252. Force UTF-8 so output never crashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _mask(secret: str) -> str:
    """Length-masked fingerprint — proves a value is present without leaking it."""
    if not secret:
        return "<empty>"
    n = len(secret)
    return f"{secret[:3]}...{secret[-2:]} (len {n})" if n > 6 else f"<{n} chars>"


def _line(label: str, value: str = "") -> None:
    print(f"    {label:<10} {value}".rstrip())


# ── credential persistence (optional --save) ─────────────────────────────────

def _save_from_env() -> None:
    """Persist creds from env vars into pat_store; skip any that are unset."""
    from src.data import pat_store
    from src.data.guru_client import GuruClient

    g_email = os.environ.get("GURU_EMAIL", "").strip()
    g_token = os.environ.get("GURU_API_TOKEN", "").strip()
    a_key = os.environ.get("ASANA_API_KEY", "").strip()

    print("Persisting credentials from environment into pat_store...")
    if g_email and g_token:
        ok = GuruClient.save_credentials(g_email, g_token)
        print(f"  Guru   -> {'saved' if ok else 'FAILED to save'} "
              f"(email={g_email}, token={_mask(g_token)})")
    else:
        print("  Guru   -> skipped (set GURU_EMAIL and GURU_API_TOKEN to save)")
    if a_key:
        ok = pat_store.save_setting("asana_api_key", a_key)
        print(f"  Asana  -> {'saved' if ok else 'FAILED to save'} "
              f"(key={_mask(a_key)})")
    else:
        print("  Asana  -> skipped (set ASANA_API_KEY to save)")
    print()


# ── Guru ─────────────────────────────────────────────────────────────────────

def validate_guru(conn) -> str:
    print("=== Guru ===")
    from src.data.guru_client import GuruClient, GuruAuthError, GuruAPIError
    from src.data import guru_analytics

    email, token = GuruClient.load_credentials()
    if not (email and token):
        _line("[creds]", "none in pat_store (guru_email / guru_api_token)")
        _line("verdict", "NOT-CONFIGURED")
        return "NOT-CONFIGURED"
    _line("[creds]", f"email={email}  token={_mask(token)}")

    client = GuruClient(email, token)
    try:
        team_id = client.get_team_id()
    except GuruAuthError:
        _line("[auth]", "REJECTED — Invalid Guru credentials (401)")
        _line("verdict", "AUTH-FAILED")
        return "AUTH-FAILED"
    except GuruAPIError as exc:
        _line("[auth]", f"API error: {exc}")
        _line("verdict", "AUTH-FAILED")
        return "AUTH-FAILED"
    except Exception as exc:  # noqa: BLE001
        _line("[auth]", f"unexpected error: {exc}")
        _line("verdict", "ERROR")
        return "ERROR"

    _line("[auth]", "OK")
    _line("[team]", team_id or "<none returned>")

    try:
        summary = guru_analytics.sync(conn, client, days_back=30)
        conn.commit()
        sections = summary.get("sections", {}) if isinstance(summary, dict) else {}
        _line("[sync]", f"ok={summary.get('ok')}  sections={sections}")
    except Exception as exc:  # noqa: BLE001
        _line("[sync]", f"FAILED: {exc}  (has the app initialised the DB schema?)")
        _line("verdict", "ERROR")
        return "ERROR"

    top = guru_analytics.top_cards(conn, days=30)
    kpis = guru_analytics.verification_kpis(conn)
    comments = guru_analytics.open_comments(conn, limit=50)
    due = guru_analytics.cards_due_for_update(conn, days=30)
    _line("[read]", f"top_cards={len(top)}  open_comments={len(comments)}  "
                    f"cards_due={len(due)}")
    _line("", f"verification_kpis={kpis}")

    # Base the verdict on content the Analytics page actually renders — not on
    # always-present scaffolding (last_sync_at / empty team_stats), which would
    # mislabel a card-less account as populated.
    populated = bool(top or comments or due or (kpis or {}).get("queue_total"))
    verdict = "OK-POPULATED" if populated else "OK-EMPTY"
    _line("verdict", verdict + ("  (empty dev account — expected)"
                                if verdict == "OK-EMPTY" else ""))
    return verdict


# ── Asana ────────────────────────────────────────────────────────────────────

def validate_asana(conn) -> str:
    print("=== Asana ===")
    from src.data.asana_client import AsanaClient
    from src.data import asana_setup, asana_monitor, enablement_tasks

    client = AsanaClient.from_store()
    if not client.api_key:
        _line("[creds]", "none in pat_store (asana_api_key)")
        _line("verdict", "NOT-CONFIGURED")
        return "NOT-CONFIGURED"
    _line("[creds]", f"key={_mask(client.api_key)}")

    ok, name = client.test_connection()
    if not ok:
        _line("[auth]", f"REJECTED — {name}")
        _line("verdict", "AUTH-FAILED")
        return "AUTH-FAILED"
    _line("[auth]", f"OK — authenticated as {name!r}")

    try:
        workspaces = client.list_workspaces()
        projects = client.list_projects(workspaces[0]["gid"]) if workspaces else []
        _line("[read]", f"workspaces={len(workspaces)}  "
                        f"projects(first ws)={len(projects)}")
    except Exception as exc:  # noqa: BLE001
        _line("[read]", f"list failed: {exc}")
        workspaces, projects = [], []

    boards = asana_setup.get_asana_config(conn)
    _line("[boards]", f"configured={len(boards)}"
                      + ("" if boards else "  (no board set up — configure one in"
                                           " enablement Settings to auto-create tasks)"))

    created = asana_monitor.poll_once(conn, client=client)
    conn.commit()
    _line("[poll]", f"poll_once created/seen={len(created)}")

    asana_tasks = enablement_tasks.list_tasks(conn, source="asana")
    all_tasks = enablement_tasks.list_tasks(conn)
    _line("[tasks]", f"enablement_tasks(source=asana)={len(asana_tasks)}  "
                     f"total={len(all_tasks)}")

    # Auth is the gate; tasks only flow once a board with indicators is configured.
    if boards and (created or asana_tasks):
        verdict = "OK-POPULATED"
    elif not boards:
        verdict = "OK-EMPTY"  # authenticated but nothing to poll yet
    else:
        verdict = "OK-EMPTY"  # board configured but no indicator matches yet
    _line("verdict", verdict + ("  (auth OK; no board/indicator data yet)"
                                if verdict == "OK-EMPTY" else ""))
    return verdict


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    if "--save" in sys.argv[1:]:
        _save_from_env()

    from src.data.connection_factory import get_connection

    conn = get_connection()
    try:
        guru_verdict = validate_guru(conn)
        print()
        asana_verdict = validate_asana(conn)
    finally:
        conn.close()

    print("\n=== Summary ===")
    print(f"  Guru :  {guru_verdict}")
    print(f"  Asana:  {asana_verdict}")

    # Exit non-zero only when something is genuinely broken (missing creds or
    # rejected auth). Empty dev accounts are the expected state and pass.
    bad = {"NOT-CONFIGURED", "AUTH-FAILED", "ERROR"}
    return 1 if (guru_verdict in bad or asana_verdict in bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
