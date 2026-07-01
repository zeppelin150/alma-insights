"""Per-user Google OAuth — loopback InstalledAppFlow (PKCE), keyring-backed,
disabled on launch.

Individuals authorize their own Google account in-app (Apps-Script style)
instead of relying on the shared service account. Security posture:

* **PKCE + ephemeral port.** ``run_local_server(port=0)`` binds a random
  loopback port; google-auth-oauthlib uses PKCE and a CSRF ``state`` by
  default. No fixed redirect port for another local process to race.
* **Minimal token at rest.** Only the 4-key ``authorized_user`` record
  (client_id, client_secret, refresh_token, token_uri) is stored in the OS
  keyring — never the access/id tokens (a Google id_token JWT alone can
  overflow the Windows Credential Manager ~2.5 KB blob cap).
* **Disabled on launch.** This module has NO import-time side effects and
  ``_active`` starts ``None``. Drive stays disconnected until an explicit
  :func:`reconnect` (silent refresh; browser only if the refresh token is
  missing/revoked). A stolen at-rest record can't be used without the OS
  vault AND a deliberate in-app action.
* **Least privilege.** drive.readonly + drive.file only — never full
  ``drive``.
* **Log hygiene.** Only the client_id fingerprint is ever logged; tokens
  are never logged or written outside the keyring.

The OAuth client (client_id/secret) comes from, in order: an admin-set
``enablement.google.oauth_client_path``; the build-injected obfuscated
bundle (:mod:`src.data._google_oauth_client`); else ``None`` (the UI then
tells the user to supply their own GCP client).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("alma.google_oauth")


def _refuse_in_subprocess(fn_name: str) -> None:
    """Hard-refuse live-credential access from the MCP tool subprocess.

    Invariant 15 — tokens never cross the subprocess boundary. The resolver
    tools run in the MCP subprocess (``ALMA_MCP_MODE`` set by the host); they
    must NEVER load/refresh OAuth credentials. All Google HTTP runs in the main
    process (the controller's worker thread). This guard makes the credential
    setters/getters raise if ever called there, so a future code path can't
    accidentally pull a refresh token into the subprocess.
    """
    if os.environ.get("ALMA_MCP_MODE"):
        raise RuntimeError(
            f"google_oauth.{fn_name}() is refused in the MCP tool subprocess "
            "(ALMA_MCP_MODE set): OAuth tokens never cross the subprocess "
            "boundary. Google access runs in the main process only."
        )

_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

_KEYRING_KEY = "google_oauth_user"
_RECORD_KEYS = ("client_id", "client_secret", "refresh_token", "token_uri")
_MAX_RECORD_CHARS = 1280  # WCM blob cap ~2.5 KB UTF-16

# Session-only live credentials. None until an explicit reconnect() — this
# is the "disabled on launch" guarantee (no import/boot side effects).
_active = None


def scopes() -> list[str]:
    return list(_SCOPES)


# ── client config (admin override > bundled > None) ──────────────────

def _load_bundled_client() -> dict | None:
    """Deobfuscate the build-injected desktop client, mirroring
    src.updater._bundled_token. Absent on dev checkouts → None."""
    try:
        from src.data import _google_oauth_client as bundled  # type: ignore
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 — never fatal
        logger.warning("_google_oauth_client present but unreadable: %s", exc)
        return None
    if getattr(bundled, "SCHEMA", None) != 1:
        logger.warning("bundled OAuth client schema unsupported; ignoring")
        return None
    payload = getattr(bundled, "CLIENT_PAYLOAD", None)
    key = getattr(bundled, "CLIENT_KEY", None)
    if not payload or not key:
        return None
    try:
        from src.updater._token_obfuscation import deobfuscate
        return json.loads(deobfuscate(payload, key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not decode bundled OAuth client: %s", exc)
        return None


def client_config() -> dict | None:
    """The OAuth client config dict ({"installed": {...}}), or None.

    Precedence: admin-provided file > build-injected bundle > None.
    """
    try:
        from src.data.settings_manager import get_section
        path = ((get_section("enablement", {}) or {}).get("google") or {}).get(
            "oauth_client_path", "")
    except Exception:
        path = ""
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            return _validated_client(cfg)
        except Exception as exc:  # noqa: BLE001 — fall through to bundled
            logger.warning("admin OAuth client unreadable (%s); trying bundled", exc)
    return _validated_client(_load_bundled_client())


def _validated_client(cfg) -> dict | None:
    """Accept only a desktop "installed" client. A "web" client's secret IS
    confidential — the "PKCE makes the secret non-secret" rationale only holds
    for installed apps, so refuse to wire one in."""
    if not isinstance(cfg, dict) or "installed" not in cfg:
        if cfg is not None:
            logger.warning("OAuth client is not a desktop 'installed' client; refusing")
        return None
    return cfg


def have_client() -> bool:
    return client_config() is not None


# ── interactive flow (BLOCKING — caller runs on a worker thread) ─────

def run_interactive_flow() -> dict:
    """Run the loopback PKCE consent flow and return the minimal record.

    BLOCKING (opens a browser, serves a localhost callback). Callers MUST
    run this on a worker thread (see google_oauth_worker.GoogleOAuthWorker).
    Raises RuntimeError if no client is configured.
    """
    cfg = client_config()
    if cfg is None:
        raise RuntimeError(
            "No Google OAuth client configured. Supply your own GCP client "
            "in Settings, or use a build with a bundled client."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(cfg, _SCOPES)
    # host="127.0.0.1" pins BOTH the bind and the redirect_uri to the IPv4
    # loopback. The library default host="localhost" resolves to ::1 first on
    # dual-stack hosts, letting a co-resident process shadow the IPv4 callback
    # and intercept the auth code (PKCE still blocks redemption, but the code
    # is burned). port=0 → ephemeral; PKCE + CSRF state on by default.
    # timeout_seconds bounds the wait so an abandoned/intercepted flow surfaces
    # as an error (re-enabling the UI) instead of hanging the worker forever.
    creds = flow.run_local_server(
        host="127.0.0.1", port=0, open_browser=True, timeout_seconds=300,
    )
    return _to_record(creds)


def _to_record(creds) -> dict:
    """Project a Credentials object down to the minimal authorized_user
    record. Never includes access_token / id_token."""
    return {
        "client_id": getattr(creds, "client_id", "") or "",
        "client_secret": getattr(creds, "client_secret", "") or "",
        "refresh_token": getattr(creds, "refresh_token", "") or "",
        "token_uri": getattr(creds, "token_uri", "")
        or "https://oauth2.googleapis.com/token",
    }


# ── keyring persistence ──────────────────────────────────────────────

def store_credentials(record: dict) -> bool:
    """Persist the minimal record to keyring + flip auth_type=oauth_user.

    Rejects oversized or non-minimal records (defends the WCM blob cap and
    ensures no access/id token sneaks in)."""
    minimal = {k: str(record.get(k, "") or "") for k in _RECORD_KEYS}
    if not minimal["refresh_token"]:
        return False
    blob = json.dumps(minimal)
    if len(blob) > _MAX_RECORD_CHARS:
        logger.warning("OAuth record too large (%d chars); refusing", len(blob))
        return False
    from src.data.pat_store import save_setting
    if not save_setting(_KEYRING_KEY, blob):
        return False
    try:
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("enablement", {}) or {})
        drive = dict(cfg.get("drive") or {})
        drive["auth_type"] = "oauth_user"
        drive.setdefault("read_enabled", True)
        cfg["drive"] = drive
        set_section("enablement", cfg)
    except Exception:  # noqa: BLE001 — token stored; settings is best-effort
        pass
    logger.info("stored Google OAuth record for client %s", _record_fingerprint(minimal))
    return True


def has_stored_credentials() -> bool:
    from src.data.pat_store import load_setting
    return bool(load_setting(_KEYRING_KEY, ""))


def is_active() -> bool:
    """True only after an explicit reconnect() this session."""
    _refuse_in_subprocess("is_active")
    return _active is not None


def _stored_record() -> dict | None:
    from src.data.pat_store import load_setting
    raw = load_setting(_KEYRING_KEY, "")
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except (ValueError, TypeError):
        return None
    # A valid-JSON non-object (null / number / string / list) would make
    # rec.get(...) raise AttributeError and escape reconnect() — keep the
    # "any malformed record → None" contract.
    if not isinstance(rec, dict):
        return None
    return rec if rec.get("refresh_token") else None


def load_active_credentials():
    """Return the live Credentials for this session, or None.

    Honors disable-on-launch: returns None unless reconnect() has run this
    session. Never triggers a browser; refreshes silently when expired.
    """
    _refuse_in_subprocess("load_active_credentials")
    return _active


def reconnect():
    """Activate the stored authorization for this session (the ONLY setter
    of the live credentials). Silent — refreshes the access token from the
    stored refresh token; opens NO browser. Returns Credentials or None.
    """
    _refuse_in_subprocess("reconnect")
    global _active
    record = _stored_record()
    if record is None:
        return None
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_info(record, _SCOPES)
        if not creds.valid:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        _active = creds
        return creds
    except Exception as exc:  # noqa: BLE001 — revoked/expired refresh token
        logger.warning("Google OAuth reconnect failed: %s", exc)
        _active = None
        return None


def disconnect() -> None:
    """Drop the live session credentials (keeps the stored record)."""
    global _active
    _active = None


def _revoke_at_google(refresh_token: str) -> None:
    """Best-effort revoke the grant at Google so a leaked refresh token can't
    keep minting access tokens after the user disconnects. Local delete
    proceeds regardless of the outcome."""
    if not refresh_token:
        return
    try:
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({"token": refresh_token}).encode("ascii")
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/revoke", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        urllib.request.urlopen(req, timeout=10)  # nosec B310 — hardcoded https constant
    except Exception as exc:  # noqa: BLE001 — best effort; never raise on disconnect
        logger.warning("Google token revoke failed (deleting local copy anyway): %s", exc)


def forget() -> bool:
    """Revoke at Google (best effort), delete the stored record, and reset
    auth_type to service_account."""
    global _active
    record = _stored_record()
    _active = None
    if record:
        _revoke_at_google(record.get("refresh_token", ""))
    from src.data.pat_store import save_setting
    ok = save_setting(_KEYRING_KEY, "")  # empty → delete
    try:
        from src.data.settings_manager import get_section, set_section
        cfg = dict(get_section("enablement", {}) or {})
        drive = dict(cfg.get("drive") or {})
        if drive.get("auth_type") == "oauth_user":
            drive["auth_type"] = "service_account"
            cfg["drive"] = drive
            set_section("enablement", cfg)
    except Exception:  # noqa: BLE001
        pass
    return ok


# ── logging hygiene ──────────────────────────────────────────────────

def _record_fingerprint(record: dict) -> str:
    """A safe-to-log fingerprint of the client_id only (never tokens)."""
    cid = record.get("client_id", "")
    if not cid:
        return "<unknown client>"
    try:
        from src.updater._token_obfuscation import fingerprint
        return fingerprint(cid)
    except Exception:  # noqa: BLE001
        return cid[:6] + "…"
