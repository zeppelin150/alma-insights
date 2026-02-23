"""
Alma Insights — Scan Server Manager
Manages the local Node.js NLP scan server lifecycle from PySide6.
Communicates via HTTPS REST API on localhost.

HARDENING CONTROLS USED:
  H1:  HTTPS with self-signed cert (ssl context, verify_mode=CERT_NONE)
  H3:  Random ephemeral port (reads from .scan_server_port file)
  H4:  Per-run auth token (reads from .scan_server_token file)
  H13: Secure API key handoff via temp file (never CLI arg)
"""

import os
import sys
import ssl
import time
import json
import tempfile
import subprocess
import logging
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger("alma.scan_server")

# Paths relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCAN_SERVER_DIR = _PROJECT_ROOT / "scan_server"
_DATA_DIR = _PROJECT_ROOT / "data"


class ScanServerManager:
    """
    Starts/stops local Node scan server. Communicates via HTTPS REST.
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or str(_DATA_DIR / "local_warehouse.db")
        self.process = None
        self._port = None
        self._auth_token = None
        self._base_url = None

        # H1: SSL context for self-signed cert
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    # ─── Port / Token Discovery ───

    @property
    def _port_file(self):
        return _DATA_DIR / ".scan_server_port"

    @property
    def _token_file(self):
        return _DATA_DIR / ".scan_server_token"

    def _discover_port(self):
        """Read the port from the file written by Node server on startup."""
        try:
            if self._port_file.exists():
                port_str = self._port_file.read_text(encoding="utf-8").strip()
                self._port = int(port_str)
                self._base_url = f"https://127.0.0.1:{self._port}"
                return True
        except (ValueError, OSError) as e:
            logger.warning(f"Could not read port file: {e}")
        return False

    def _load_auth_token(self):
        """Read the per-run auth token from the file written by Node server."""
        try:
            if self._token_file.exists():
                self._auth_token = self._token_file.read_text(
                    encoding="utf-8"
                ).strip()
                return True
        except OSError as e:
            logger.warning(f"Could not read auth token: {e}")
        return False

    # ─── Server Lifecycle ───

    def ensure_running(self) -> bool:
        """Start server if not running. Returns True if healthy."""
        # Try existing server first
        if self._discover_port() and self._load_auth_token():
            if self._health_check():
                return True

        # Start new server
        if not self._start_server():
            return False

        # Wait for server to become healthy (up to 30 seconds)
        for _ in range(60):
            time.sleep(0.5)
            if self._discover_port() and self._load_auth_token():
                if self._health_check():
                    return True

        logger.error("Scan server failed to start within 30 seconds")
        return False

    def _start_server(self) -> bool:
        """Launch Node server as a detached subprocess."""
        server_js = _SCAN_SERVER_DIR / "server.js"
        if not server_js.exists():
            logger.error(f"server.js not found: {server_js}")
            return False

        # Find Node.js
        node_path = self._find_node()
        if not node_path:
            logger.error("Node.js not found on PATH")
            return False

        # H13: Secure API key handoff — write key to temp file
        api_key_file = None
        try:
            api_key = self._get_api_key()
            if api_key:
                fd, api_key_file = tempfile.mkstemp(
                    prefix="alma_apikey_", suffix=".txt"
                )
                os.write(fd, api_key.encode("utf-8"))
                os.close(fd)
        except Exception as e:
            logger.warning(f"Could not write API key file: {e}")

        # Find Gemini CLI path
        gemini_cli = self._find_gemini_cli()

        cmd = [
            node_path,
            str(server_js),
            "--db", str(self.db_path),
            "--port", "0",  # H3: random ephemeral port
            "--data-dir", str(_DATA_DIR),
            "--port-file", str(self._port_file),
            "--token-file", str(self._token_file),
        ]

        if api_key_file:
            cmd.extend(["--api-key-file", api_key_file])

        if gemini_cli:
            cmd.extend(["--gemini-cli", gemini_cli])

        try:
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
            }
            if sys.platform == "win32":
                # DETACHED_PROCESS so server survives app close
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NO_WINDOW
                )
                kwargs.pop("start_new_session", None)

            self.process = subprocess.Popen(cmd, **kwargs)
            logger.info(f"Scan server started (PID {self.process.pid})")
            return True
        except Exception as e:
            logger.error(f"Failed to start scan server: {e}")
            return False

    def _find_node(self) -> str:
        """Find Node.js executable."""
        import shutil
        return shutil.which("node") or ""

    def _find_gemini_cli(self) -> str:
        """Find Gemini CLI path from settings or PATH."""
        import shutil
        config_path = _PROJECT_ROOT / "config" / "settings.yaml"
        if config_path.exists():
            try:
                import yaml
                with open(config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                path = config.get("gemini", {}).get("cli_path", "")
                if path and Path(path).exists():
                    return path
            except Exception:
                pass
        return shutil.which("gemini") or ""

    def _get_api_key(self) -> str:
        """Load the saved Gemini API key."""
        try:
            from src.data import pat_store
            return pat_store.load_setting("gemini_api_key") or ""
        except Exception:
            return ""

    def _health_check(self) -> bool:
        """GET /health over HTTPS, return True if 200."""
        if not self._base_url:
            return False
        try:
            req = Request(f"{self._base_url}/health", method="GET")
            resp = urlopen(req, timeout=3, context=self._ssl_ctx)
            return resp.status == 200
        except (URLError, OSError):
            return False

    # ─── REST API Calls ───

    def _auth_headers(self) -> dict:
        """Return Authorization header dict."""
        if self._auth_token:
            return {"Authorization": f"Bearer {self._auth_token}"}
        return {}

    def _request(self, method, path, body=None) -> dict:
        """Make an authenticated HTTPS request to the scan server."""
        if not self._base_url:
            if not self._discover_port():
                raise RuntimeError("Scan server port not known")

        url = f"{self._base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            **self._auth_headers(),
        }

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = Request(url, data=data, headers=headers, method=method)
        try:
            resp = urlopen(req, timeout=30, context=self._ssl_ctx)
            return json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            if hasattr(e, "read"):
                err_body = e.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(err_body)
                except json.JSONDecodeError:
                    pass
            raise RuntimeError(f"Scan server request failed: {e}")

    def start_scan(self, date_start: str, date_end: str,
                   trc_filter=None, batch_size=700,
                   budget_cap=50.0, mode="full") -> dict:
        """POST /scan/start. Returns {scan_id, total_batches, estimated_cost}."""
        body = {
            "date_start": date_start,
            "date_end": date_end,
            "batch_size": batch_size,
            "budget_cap": budget_cap,
            "mode": mode,
        }
        if trc_filter:
            body["trc_filter"] = trc_filter
        return self._request("POST", "/scan/start", body)

    def get_status(self, scan_id: str) -> dict:
        """GET /scan/{id}/status."""
        return self._request("GET", f"/scan/{scan_id}/status")

    def pause_scan(self, scan_id: str) -> dict:
        """POST /scan/{id}/pause."""
        return self._request("POST", f"/scan/{scan_id}/pause")

    def resume_scan(self, scan_id: str, budget_cap=None) -> dict:
        """POST /scan/{id}/resume."""
        body = {}
        if budget_cap is not None:
            body["budget_cap"] = budget_cap
        return self._request("POST", f"/scan/{scan_id}/resume", body)

    def cancel_scan(self, scan_id: str) -> dict:
        """POST /scan/{id}/cancel."""
        return self._request("POST", f"/scan/{scan_id}/cancel")

    def get_history(self) -> list:
        """GET /scan/history."""
        return self._request("GET", "/scan/history")
