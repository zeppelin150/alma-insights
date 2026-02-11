"""
Alma Insights — PAT Store
Persists Lightdash PAT securely in user's home directory.
Never stored in the SQLite DB or the project repo.
"""

import json
import os
from pathlib import Path


_CONFIG_DIR = Path.home() / ".alma-insights"
_CREDS_FILE = _CONFIG_DIR / "credentials.json"


def _ensure_dir():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Restrict permissions on Unix
    if os.name != "nt":
        try:
            os.chmod(_CONFIG_DIR, 0o700)
        except OSError:
            pass


def save_pat(pat: str) -> bool:
    """Save PAT to disk. Returns True on success."""
    try:
        _ensure_dir()
        data = _load_all()
        data["lightdash_pat"] = pat
        _CREDS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(_CREDS_FILE, 0o600)
            except OSError:
                pass
        return True
    except Exception:
        return False


def load_pat() -> str:
    """Load PAT from disk. Returns empty string if not found."""
    data = _load_all()
    return data.get("lightdash_pat", "")


def has_pat() -> bool:
    """Check if a PAT is saved."""
    return bool(load_pat())


def delete_pat() -> bool:
    """Remove saved PAT."""
    try:
        data = _load_all()
        data.pop("lightdash_pat", None)
        _ensure_dir()
        _CREDS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def redact_pat(pat: str) -> str:
    """Redact PAT for display: ldpat_…XXXX or ***…XXXX."""
    if not pat:
        return "(none)"
    if len(pat) <= 4:
        return "***" + pat[-2:]
    prefix = pat[:6] if pat.startswith("ldpat_") else "***"
    return f"{prefix}…{pat[-4:]}"


def save_setting(key: str, value) -> bool:
    """Save a non-sensitive setting to the config file."""
    try:
        _ensure_dir()
        data = _load_all()
        data[key] = value
        _CREDS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_setting(key: str, default=None):
    """Load a non-sensitive setting."""
    return _load_all().get(key, default)


def _load_all() -> dict:
    """Load the entire config file."""
    try:
        if _CREDS_FILE.exists():
            return json.loads(_CREDS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}
