"""Drive-search evaluation PREFLIGHT — read-only machine truth.

Answers one question: *can a live Drive search evaluation even run on this
box right now, and which of the three funnel stages are reachable?*

Makes NO writes, NO LLM calls, and NO network calls. It only reads
``data/settings.yaml`` (via settings_manager), stats a couple of paths, and
asks ``google_oauth`` whether a session is active. Nothing here connects to
Google — that is deliberate: preflight must be safe to run at any time,
including before the owner has connected an account.

Secrets are NEVER printed. Paths that may embed an account identifier are
reported as exists/missing plus a length-masked fingerprint, following the
idiom in ``scripts/validate_live_integrations.py`` (_mask).

The three stages this reports on (see docs/DRIVE_SEARCH_EVAL.md):

    Stage 1  FIND A FOLDER    picker / by-id  -> enablement.drive.active_folders
    Stage 2  LIVE DRIVE SEARCH DriveReader.search_files (currently unreachable
                              in production — no caller injects live_client)
    Stage 3  MIRROR SEARCH    kb_search FTS5 + enablement_documents LIKE floor

Usage
-----
    python scripts/drive_eval_preflight.py
    python scripts/drive_eval_preflight.py --json     # machine-readable

NOTE: ``google_oauth`` hard-raises under ALMA_MCP_MODE (google_oauth.py:39-54)
because OAuth tokens must never cross the MCP subprocess boundary. This script
refuses to run with that variable set rather than reporting a misleading error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — older Python / redirected stream
        pass


def _mask(secret: str) -> str:
    """Length-masked fingerprint — proves a value is present without leaking it.

    Copied deliberately from scripts/validate_live_integrations.py:50-56 so the
    two scripts share one masking idiom.
    """
    if not secret:
        return "<empty>"
    n = len(secret)
    return f"{secret[:3]}...{secret[-2:]} (len {n})" if n > 6 else f"<{n} chars>"


def _mask_path(p: str) -> str:
    """A service-account credentials PATH is itself sensitive: the generated
    filename embeds the GCP project name and a private-key-id prefix
    (``<project>-<keyid>.json``). So report ONLY the extension and the total
    length — never the stem, never the directory."""
    if not p:
        return "<unset>"
    return f"<{Path(p).suffix or 'no-ext'} file, path len {len(p)}>"


# ── probes (each returns a plain dict; none of them touch the network) ──────

def probe_settings() -> dict:
    """Read the enablement settings section. Never raises."""
    try:
        from src.data.settings_manager import get_section
        en = get_section("enablement", {}) or {}
    except Exception as exc:  # noqa: BLE001 — a broken settings file must report, not crash
        return {"ok": False, "error": str(exc)}
    drive = en.get("drive") or {}
    kb = en.get("kb")
    return {
        "ok": True,
        # auth_type ABSENT is meaningful: DriveReader.from_settings (drive_reader.py:48-49)
        # defaults it to "service_account", which is a different code path from oauth_user.
        "auth_type_present": "auth_type" in drive,
        "auth_type": drive.get("auth_type", "service_account"),
        "read_enabled": bool(drive.get("read_enabled")),
        "credentials_path": drive.get("credentials_path", "") or "",
        "active_folders": list(drive.get("active_folders") or []),
        "kb_section_present": kb is not None,
        "kb_enabled": bool((kb or {}).get("enabled")),
        "kb_ec_folder_id": (kb or {}).get("ec_folder_id", "") or "",
        "demo_mode": bool(en.get("demo_mode", True)),
        "web_tabs": str(en.get("web_tabs", "off")),
    }


def probe_credentials_file(path: str) -> dict:
    """Stat the service-account credentials file. Does NOT open or parse it —
    we never read key material."""
    if not path:
        return {"configured": False, "exists": False, "detail": "no credentials_path set"}
    p = Path(path)
    try:
        exists = p.is_file()
        size = p.stat().st_size if exists else 0
    except OSError as exc:
        return {"configured": True, "exists": False, "detail": f"stat failed: {exc}"}
    return {"configured": True, "exists": exists,
            "detail": f"{size} bytes" if exists else "file not found"}


def probe_oauth() -> dict:
    """google_oauth.is_active() — expected False at boot (disable-on-launch,
    google_oauth.py:66-67, 220-223). A True here means the owner connected
    Google in-app in THIS process, which a standalone script cannot inherit."""
    if os.environ.get("ALMA_MCP_MODE"):
        return {"active": None, "detail": "refused: ALMA_MCP_MODE is set"}
    try:
        from src.data import google_oauth
        return {"active": bool(google_oauth.is_active()),
                "detail": "session credentials present" if google_oauth.is_active()
                          else "not connected this session (expected for a fresh process)"}
    except Exception as exc:  # noqa: BLE001
        return {"active": None, "detail": f"probe failed: {exc}"}


def probe_web_bundle() -> dict:
    p = _ROOT / "src" / "ui" / "web" / "dist" / "index.html"
    try:
        exists = p.is_file()
        size = p.stat().st_size if exists else 0
    except OSError as exc:
        return {"exists": False, "detail": f"stat failed: {exc}"}
    return {"exists": exists, "detail": f"{size} bytes" if exists else "not built"}


def probe_google_libs() -> dict:
    """Are the optional Google client libs importable? DriveReader._build_service
    raises ImportError without them (drive_reader.py:68-71)."""
    missing = []
    for mod in ("googleapiclient.discovery", "google.oauth2.service_account"):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod.split(".")[0])
    return {"available": not missing, "missing": sorted(set(missing))}


# ── GO / NO-GO per funnel stage ────────────────────────────────────────────

def evaluate_stages(s: dict, creds: dict, oauth: dict, libs: dict) -> list[dict]:
    """One verdict row per funnel stage. Verdicts are GO / NO-GO / BLOCKED-BY-DESIGN."""
    stages: list[dict] = []

    # Stage 1 — find a folder.
    if not s.get("ok"):
        stages.append({"stage": "1 find-folder", "verdict": "NO-GO",
                       "why": "settings unreadable"})
    elif s["active_folders"]:
        stages.append({"stage": "1 find-folder", "verdict": "GO",
                       "why": f"{len(s['active_folders'])} active folder(s) configured"})
    else:
        stages.append({"stage": "1 find-folder", "verdict": "NO-GO",
                       "why": "enablement.drive.active_folders is empty — pick a "
                              "folder in-app, or pass --folder-id to the runner"})

    # Stage 2 — live Drive search.
    auth_ok = (oauth.get("active") is True) if s.get("auth_type") == "oauth_user" \
        else creds.get("exists", False)
    if not libs["available"]:
        stages.append({"stage": "2 live-drive-search", "verdict": "NO-GO",
                       "why": f"missing libs: {', '.join(libs['missing'])}"})
    elif not s.get("read_enabled"):
        stages.append({"stage": "2 live-drive-search", "verdict": "NO-GO",
                       "why": "enablement.drive.read_enabled is false — "
                              "DriveReader.is_configured() returns False"})
    elif not auth_ok:
        stages.append({"stage": "2 live-drive-search", "verdict": "NO-GO",
                       "why": f"auth_type={s['auth_type']} but no usable credential "
                              "(see the auth rows above)"})
    else:
        stages.append({"stage": "2 live-drive-search", "verdict": "GO*",
                       "why": "credentials are in place and DriveReader can BUILD "
                              "a service. *This is not proof the API works — "
                              "preflight makes no network call, so it cannot see "
                              "a disabled Drive API, a revoked key, or an unshared "
                              "folder. Confirm with: python scripts/run_drive_eval.py "
                              "--list-drives (read-only) and check the [proof] block."})

    # Stage 2 has a second, permanent blocker that no amount of config fixes:
    # NO production caller injects live_client into query_business_drive.
    stages.append({
        "stage": "2b live-via-chat-tool", "verdict": "BLOCKED-BY-DESIGN",
        "why": "query_business_drive's live_client seam is never populated "
               "(enablement_tools.py:28-31, claude_tools.py:1213-1216) — the "
               "chat tool ALWAYS takes the local_index branch. Measured by "
               "tests/test_drive_query.py, not fixed here."})

    # Stage 3 — mirror search.
    if not s.get("ok"):
        stages.append({"stage": "3 mirror-search", "verdict": "NO-GO",
                       "why": "settings unreadable"})
    elif s["demo_mode"]:
        stages.append({"stage": "3 mirror-search", "verdict": "NO-GO",
                       "why": "enablement.demo_mode is true — _kb_precheck refuses "
                              "index_drive_folder (kb_tools.py:44-47)"})
    elif not s["kb_section_present"]:
        stages.append({"stage": "3 mirror-search", "verdict": "NO-GO",
                       "why": "no enablement.kb section at all — KB was never "
                              "bootstrapped; kb_search will return only the "
                              "enablement_documents LIKE floor"})
    elif not s["kb_enabled"]:
        stages.append({"stage": "3 mirror-search", "verdict": "NO-GO",
                       "why": "enablement.kb.enabled is false"})
    elif not s["kb_ec_folder_id"]:
        stages.append({"stage": "3 mirror-search", "verdict": "NO-GO",
                       "why": "enablement.kb.ec_folder_id unset — EC folder not "
                              "bootstrapped (kb_tools.py:53-56)"})
    else:
        stages.append({"stage": "3 mirror-search", "verdict": "GO",
                       "why": "KB enabled and bootstrapped"})
    return stages


# ── rendering ──────────────────────────────────────────────────────────────

def _table(rows: list[tuple[str, str, str]]) -> str:
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    out = []
    for label, value, note in rows:
        out.append(f"  {label:<{w0}}  {value:<{w1}}  {note}".rstrip())
    return "\n".join(out)


def build_report() -> dict:
    s = probe_settings()
    creds = probe_credentials_file(s.get("credentials_path", ""))
    oauth = probe_oauth()
    libs = probe_google_libs()
    bundle = probe_web_bundle()
    stages = evaluate_stages(s, creds, oauth, libs)
    return {"settings": s, "credentials": creds, "oauth": oauth,
            "google_libs": libs, "web_bundle": bundle, "stages": stages}


def render(rep: dict) -> str:
    s, creds, oauth = rep["settings"], rep["credentials"], rep["oauth"]
    libs, bundle = rep["google_libs"], rep["web_bundle"]
    lines = ["", "=" * 78,
             " DRIVE SEARCH EVAL — PREFLIGHT (read-only; no network, no writes)",
             "=" * 78, "", " Machine truth", " " + "-" * 76]

    if not s.get("ok"):
        lines.append(f"  settings unreadable: {s.get('error')}")
        return "\n".join(lines)

    rows = [
        ("enablement.drive.auth_type", s["auth_type"],
         "" if s["auth_type_present"] else "(ABSENT -> default)"),
        ("enablement.drive.read_enabled", str(s["read_enabled"]), ""),
        ("  credentials_path", _mask_path(s["credentials_path"]),
         "EXISTS" if creds["exists"] else f"MISSING — {creds['detail']}"),
        ("google_oauth.is_active()", str(oauth["active"]), oauth["detail"]),
        ("google client libs", "ok" if libs["available"] else "MISSING",
         ", ".join(libs["missing"])),
        ("enablement.kb section", "present" if s["kb_section_present"] else "ABSENT",
         f"enabled={s['kb_enabled']} ec_folder_id="
         f"{_mask(s['kb_ec_folder_id']) if s['kb_ec_folder_id'] else '<unset>'}"),
        ("enablement.demo_mode", str(s["demo_mode"]),
         "blocks KB indexing" if s["demo_mode"] else ""),
        ("enablement.drive.active_folders", str(len(s["active_folders"])),
         ", ".join(_mask(str((f or {}).get("id", ""))) for f in s["active_folders"])
         or "none configured"),
        ("enablement.web_tabs", s["web_tabs"], ""),
        ("src/ui/web/dist/index.html", "present" if bundle["exists"] else "ABSENT",
         bundle["detail"]),
    ]
    lines.append(_table(rows))
    lines += ["", " Funnel readiness", " " + "-" * 76]
    for st in rep["stages"]:
        lines.append(f"  {st['verdict']:<20} {st['stage']}")
        lines.append(f"  {'':<20} └─ {st['why']}")
    gos = sum(1 for st in rep["stages"] if st["verdict"].startswith("GO"))
    lines += ["", f"  {gos}/{len(rep['stages'])} stages GO", "",
              "  Secrets are never printed by this script — only presence and "
              "masked lengths.", "=" * 78, ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    if os.environ.get("ALMA_MCP_MODE"):
        print("REFUSED: ALMA_MCP_MODE is set. google_oauth hard-raises in the MCP "
              "subprocess (google_oauth.py:39-54). Run this from a normal shell.",
              file=sys.stderr)
        return 2

    rep = build_report()
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(render(rep))
    # Exit 0 always: preflight REPORTS, it does not gate. A non-zero here would
    # make it useless in the exact situation it exists for (nothing configured).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
