"""The KB's SINGLE Drive write chokepoint (WS2-M1, renn-calendar-kb-studio).

Gate-exemption enforcement (ratified D-GATE): writes inside the app-created EC
folder skip the human Confirm gate, and this module is what makes that safe —
IN CODE, not prompt:

* ``write_card_file`` raises :class:`KBWriteDenied` unless the parent folder
  id is a non-quarantined row in ``kb_folders`` (the allowlist). The model
  never supplies folder ids; tools pass TOPIC NAMES that code resolves.
* Topic-folder CREATION is code-gated (the pre-mortem exemption-hole fix):
  Haiku-emitted topics are slug-normalized and fuzzy-matched against existing
  folders; a genuinely NEW folder must be in the curated seed list (product
  areas) or fit the budget caps — overflow lands in ``misc``.
* Bootstrap runs a WRITABILITY PROBE (create+delete-less marker update), not a
  bare existence check: ``drive.file`` grants are per-OAuth-client-ID, so an
  EC tree created by the dev build is READABLE but NOT WRITABLE by the release
  build — the probe surfaces "re-bootstrap required" instead of eternal 403s.
* Verification requests ``trashed`` + ``capabilities/canAddChildren``: a
  trashed EC folder still ACCEPTS writes (into the trash!), so trashed ⇒
  quarantined ⇒ refuse + re-bootstrap.
* Write idempotency: every card file carries ``appProperties.kb_card_id``;
  before any create we probe for it, so a crash between Drive-create and
  mirror-commit cannot duplicate the card on retry.

All Drive writes route through GoogleDriveExporter's throttled surface.
DB access: plain execute+commit, NEVER atomic() (worker threads/MCP subprocess).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.data.artifact_store import slugify

logger = logging.getLogger("alma.kb.drive")

EC_ROOT_NAME = "EC"
MISC_TOPIC = "misc"
MARKER_NAME = ".kb_marker"

# Topic-minting budget (pre-mortem caps): per-job new-folder budget is passed
# by callers; this is the global ceiling.
MAX_TOPIC_FOLDERS = 40
_FUZZY_THRESHOLD = 0.8

_ENTITIES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "entities"


class KBWriteDenied(RuntimeError):
    """A Drive write outside the EC allowlist was attempted (or a quarantined
    folder). This is the exemption's hard edge — never catch-and-proceed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exporter():
    from src.export.gdrive_export import GoogleDriveExporter
    return GoogleDriveExporter.from_settings()


def _log(conn, action: str, *, card_id=None, drive_file_id=None, detail="") -> None:
    try:
        conn.execute(
            "INSERT INTO kb_sync_log (action, card_id, drive_file_id, detail, created_at) "
            "VALUES (?,?,?,?,?)",
            (action, card_id, drive_file_id, str(detail)[:500], _now()))
        conn.commit()
    except Exception:  # noqa: BLE001 — audit is best-effort
        pass


# ── allowlist reads ──────────────────────────────────────────────────

def allowed_folder(conn, folder_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM kb_folders WHERE folder_id=? AND status='ok'",
        (folder_id,)).fetchone()
    return row is not None


def ec_root_id(conn) -> str | None:
    row = conn.execute(
        "SELECT folder_id FROM kb_folders WHERE role='ec_root' AND status='ok' "
        "LIMIT 1").fetchone()
    return row[0] if row else None


def topic_folders(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT folder_id, topic, status FROM kb_folders WHERE role='topic'"
    ).fetchall()
    return [{"folder_id": r[0], "topic": r[1], "status": r[2]} for r in rows]


# ── bootstrap ────────────────────────────────────────────────────────

def ensure_ec_root(conn, *, exporter=None) -> dict:
    """Create/verify the EC root folder. Idempotent; probes WRITABILITY.

    Returns {"ok", "folder_id"} or {"ok": False, "error", "needs_rebootstrap"?}.
    """
    exporter = exporter or _exporter()
    existing = ec_root_id(conn)
    if existing:
        verdict = _verify_folder(conn, existing, exporter)
        if verdict.get("ok"):
            return {"ok": True, "folder_id": existing}
        return verdict

    from src.data.settings_manager import get_section, set_section
    kb_cfg = (get_section("enablement", {}) or {}).get("kb") or {}
    parent = kb_cfg.get("ec_parent_id") or None

    created = exporter.create_folder(EC_ROOT_NAME, parent,
                                     app_properties={"kb_role": "ec_root"})
    folder_id = created.get("id") or ""
    if not folder_id:
        return {"ok": False, "error": "create_failed"}
    probe = _probe_writable(exporter, folder_id)
    if not probe.get("ok"):
        return probe

    conn.execute(
        "INSERT OR REPLACE INTO kb_folders (folder_id, topic, parent_id, role, "
        "status, created_at) VALUES (?, '', ?, 'ec_root', 'ok', ?)",
        (folder_id, parent, _now()))
    conn.commit()
    cfg = dict(get_section("enablement", {}) or {})
    kb = dict(cfg.get("kb") or {})
    kb["ec_folder_id"] = folder_id
    cfg["kb"] = kb
    set_section("enablement", cfg)
    _log(conn, "create", drive_file_id=folder_id, detail="EC root bootstrapped")
    return {"ok": True, "folder_id": folder_id}


def _verify_folder(conn, folder_id: str, exporter) -> dict:
    """Re-verify a known folder: trashed or unwritable ⇒ quarantine + refuse."""
    try:
        meta = exporter.get_file_meta(folder_id)
    except Exception as exc:  # noqa: BLE001 — 404 under a different client id etc.
        _quarantine(conn, folder_id, f"unreadable: {exc}")
        return {"ok": False, "error": "ec_folder_unreachable",
                "needs_rebootstrap": True,
                "message": "The EC folder can't be read (deleted, or created by "
                           "a different app build) — re-bootstrap the KB."}
    caps = meta.get("capabilities") or {}
    if meta.get("trashed") or caps.get("canAddChildren") is False:
        _quarantine(conn, folder_id,
                    "trashed" if meta.get("trashed") else "not writable")
        return {"ok": False, "error": "ec_folder_quarantined",
                "needs_rebootstrap": True,
                "message": "The EC folder was trashed or is no longer writable "
                           "— re-bootstrap the KB (cards are regenerable)."}
    return {"ok": True, "folder_id": folder_id}


def _probe_writable(exporter, folder_id: str) -> dict:
    """The writability probe: create+update a marker file INSIDE the folder.
    drive.file visibility ≠ writability across OAuth client ids."""
    try:
        marker = exporter.upload_file(MARKER_NAME, b"kb", "text/plain",
                                      folder_id,
                                      app_properties={"kb_role": "marker"})
        exporter.update_file(marker.get("id"), "kb", "text/plain")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "ec_folder_not_writable",
                "needs_rebootstrap": True,
                "message": f"EC folder exists but this build cannot write to it "
                           f"({str(exc)[:120]}) — it was likely created by a "
                           f"different OAuth client; re-bootstrap to recreate it."}


def _quarantine(conn, folder_id: str, reason: str) -> None:
    try:
        conn.execute("UPDATE kb_folders SET status='quarantined' WHERE folder_id=?",
                     (folder_id,))
        conn.commit()
        _log(conn, "repair_flag", drive_file_id=folder_id,
             detail=f"quarantined: {reason}")
    except Exception:  # noqa: BLE001
        pass


# ── code-gated topic minting ─────────────────────────────────────────

def _seed_topics() -> set[str]:
    """Curated allowed-new-topic slugs from the product-area dictionary
    (payer names are entities, not topics). PHI-free by construction."""
    seeds: set[str] = set()
    try:
        data = json.loads((_ENTITIES_DIR / "product_areas.json").read_text("utf-8"))
        values = data if isinstance(data, list) else (
            data.get("product_areas") or data.get("areas") or list(data.keys()))
        for v in values:
            name = v if isinstance(v, str) else (v.get("name") if isinstance(v, dict) else "")
            if name:
                seeds.add(slugify(str(name)))
    except Exception:  # noqa: BLE001 — no seeds is fine, budget still applies
        pass
    return seeds


def _token_set_ratio(a: str, b: str) -> float:
    ta, tb = set(a.split("-")), set(b.split("-"))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def resolve_topic_folder(conn, topic: str, *, exporter=None,
                         new_folder_budget: int = 3) -> dict:
    """Resolve a (possibly Haiku-emitted) topic to an allowlisted folder,
    creating it only within the code-gated rules. Returns {"ok", "folder_id",
    "topic", "created"?, "overflow"?}."""
    root = ec_root_id(conn)
    if not root:
        return {"ok": False, "error": "ec_not_bootstrapped",
                "message": "Bootstrap the EC folder first (KB settings)."}
    slug = slugify(topic or MISC_TOPIC)

    existing = topic_folders(conn)
    live = [f for f in existing if f["status"] == "ok"]
    for f in live:                                   # exact slug reuse
        if f["topic"] == slug:
            return {"ok": True, "folder_id": f["folder_id"], "topic": slug}
    best, best_ratio = None, 0.0
    for f in live:                                   # fuzzy reuse (plural/reorder)
        r = _token_set_ratio(slug, f["topic"])
        if r > best_ratio:
            best, best_ratio = f, r
    if best is not None and best_ratio >= _FUZZY_THRESHOLD:
        return {"ok": True, "folder_id": best["folder_id"], "topic": best["topic"]}

    # A genuinely NEW folder: seed-list membership OR budget, global cap.
    allowed_new = (slug in _seed_topics()) or new_folder_budget > 0
    if len(live) >= MAX_TOPIC_FOLDERS or not allowed_new:
        misc = next((f for f in live if f["topic"] == MISC_TOPIC), None)
        if misc:
            return {"ok": True, "folder_id": misc["folder_id"],
                    "topic": MISC_TOPIC, "overflow": True}
        slug = MISC_TOPIC                             # create misc below

    exporter = exporter or _exporter()
    created = exporter.create_folder(slug, root,
                                     app_properties={"kb_role": "topic",
                                                     "kb_topic": slug})
    folder_id = created.get("id") or ""
    if not folder_id:
        return {"ok": False, "error": "create_failed"}
    conn.execute(
        "INSERT OR REPLACE INTO kb_folders (folder_id, topic, parent_id, role, "
        "status, created_at) VALUES (?, ?, ?, 'topic', 'ok', ?)",
        (folder_id, slug, root, _now()))
    conn.commit()
    _log(conn, "create", drive_file_id=folder_id, detail=f"topic folder {slug}")
    return {"ok": True, "folder_id": folder_id, "topic": slug, "created": True}


# ── the write chokepoint ─────────────────────────────────────────────

def write_card_file(conn, parent_folder_id: str, filename: str, content: str,
                    *, card_id: str, exporter=None) -> dict:
    """Create-or-update a card .md inside an ALLOWLISTED EC folder.

    Raises :class:`KBWriteDenied` for any parent not in kb_folders (status ok).
    Idempotent across crash windows via the appProperties kb_card_id probe.
    Returns {"ok", "drive_file_id", "modified_time", "updated": bool}.
    """
    if not allowed_folder(conn, parent_folder_id):
        raise KBWriteDenied(
            f"refusing Drive write: folder {parent_folder_id!r} is not in the "
            f"EC allowlist (writes outside EC stay human-gated)")
    exporter = exporter or _exporter()

    existing = None
    try:
        existing = exporter.find_child_by_app_property(
            parent_folder_id, "kb_card_id", card_id)
    except Exception as exc:  # noqa: BLE001 — probe is safety, not a gate
        logger.debug("card idempotency probe failed: %s", exc)
    if existing:
        res = exporter.update_file(existing["id"], content)
        _log(conn, "update", card_id=card_id, drive_file_id=existing["id"])
        return {"ok": True, "drive_file_id": existing["id"],
                "modified_time": res.get("modifiedTime") or "", "updated": True}
    created = exporter.upload_file(filename, content.encode("utf-8"),
                                   "text/markdown", parent_folder_id,
                                   app_properties={"kb_card_id": card_id})
    file_id = created.get("id") or ""
    meta = {}
    try:
        meta = exporter.get_file_meta(file_id, fields="id, modifiedTime")
    except Exception:  # noqa: BLE001
        pass
    _log(conn, "create", card_id=card_id, drive_file_id=file_id)
    return {"ok": True, "drive_file_id": file_id,
            "modified_time": meta.get("modifiedTime") or "", "updated": False}
