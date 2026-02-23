"""
Alma Insights — Gemini Setup Utilities
Cross-platform detection, installation, and authentication helpers for the Gemini CLI.

All functions are pure (no UI imports) so they can be used from worker threads
and tested independently of the UI.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


# ─── Node.js detection ────────────────────────────────────────────────────────

def find_node() -> str | None:
    """
    Return the full path to the node binary, or None if not found.
    Checks PATH first, then common platform-specific install locations.
    """
    # PATH lookup (works on both Windows and macOS/Linux)
    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return found

    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "node.exe",
        ]
        # nvm for Windows stores versions under %APPDATA%\nvm\v*\node.exe
        nvm_root = Path(os.environ.get("APPDATA", "")) / "nvm"
        if nvm_root.exists():
            for version_dir in sorted(nvm_root.glob("v*"), reverse=True):
                candidate = version_dir / "node.exe"
                if candidate.exists():
                    candidates.insert(0, candidate)
    else:
        candidates = [
            Path("/usr/local/bin/node"),
            Path("/usr/bin/node"),
            Path.home() / ".nvm" / "versions" / "node",  # checked below for latest
        ]
        # nvm on macOS/Linux
        nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
        versions_dir = nvm_dir / "versions" / "node"
        if versions_dir.exists():
            for version_dir in sorted(versions_dir.glob("v*"), reverse=True):
                candidate = version_dir / "bin" / "node"
                if candidate.exists():
                    candidates.insert(0, candidate)

    for c in candidates:
        if Path(c).exists():
            return str(c)
    return None


def get_node_version(node_path: str) -> str | None:
    """Return node version string (e.g. 'v22.1.0') or None on failure."""
    try:
        result = subprocess.run(
            [node_path, "--version"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def node_meets_requirements(node_path: str, min_major: int = 20) -> bool:
    """Return True if the node version is >= min_major."""
    version = get_node_version(node_path)
    if not version:
        return False
    try:
        major = int(version.lstrip("v").split(".")[0])
        return major >= min_major
    except (ValueError, IndexError):
        return False


# ─── npm detection ────────────────────────────────────────────────────────────

def find_npm(node_path: str | None = None) -> str | None:
    """
    Return the full path to the npm binary, or None.
    If node_path is given, checks the same directory first (most reliable).

    On Windows, npm is a .cmd script — we prefer npm.cmd over the bare
    'npm' file (which is a Unix shell script that cmd.exe cannot run).
    """
    if node_path:
        node_dir = Path(node_path).parent
        if sys.platform == "win32":
            # Prefer .cmd > .bat > bare (bare is a Unix shell script on Windows)
            candidates = [
                node_dir / "npm.cmd",
                node_dir / "npm.bat",
                node_dir / "npm",
            ]
        else:
            candidates = [
                node_dir / "npm",
                node_dir / "npm.cmd",
                node_dir / "npm.bat",
            ]
        for c in candidates:
            if c.exists():
                return str(c)

    # PATH fallback — on Windows prefer .cmd variant
    if sys.platform == "win32":
        found = shutil.which("npm.cmd") or shutil.which("npm")
    else:
        found = shutil.which("npm")
    return found


# ─── Gemini CLI detection ─────────────────────────────────────────────────────

def find_gemini_cli() -> str | None:
    """
    Return the full path to the gemini binary, or None.
    Covers npm global install locations on Windows and macOS.
    """
    found = shutil.which("gemini") or shutil.which("gemini.cmd")
    if found:
        return found

    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("APPDATA", "")) / "npm" / "gemini.cmd",
            Path(os.environ.get("APPDATA", "")) / "npm" / "gemini",
        ]
    else:
        candidates = [
            Path("/usr/local/bin/gemini"),
            Path.home() / ".npm-global" / "bin" / "gemini",
            Path.home() / "node_modules" / ".bin" / "gemini",
        ]
        # Also check nvm-managed npm global bins
        nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
        versions_dir = nvm_dir / "versions" / "node"
        if versions_dir.exists():
            for version_dir in sorted(versions_dir.glob("v*"), reverse=True):
                candidate = version_dir / "bin" / "gemini"
                if candidate.exists():
                    candidates.insert(0, candidate)

    for c in candidates:
        if Path(c).exists():
            return str(c)
    return None


# ─── Installation ─────────────────────────────────────────────────────────────

def install_gemini_cli(npm_path: str, progress_callback=None) -> tuple[bool, str]:
    """
    Run `npm install -g @google/gemini-cli` and stream output.

    Args:
        npm_path: Full path to the npm binary.
        progress_callback: Optional callable(line: str) called for each stdout line.

    Returns:
        (success, error_message)
    """
    # On Windows, npm is a .cmd script — it must always be invoked through
    # cmd.exe /c, even when the path doesn't end in .cmd (shutil.which can
    # return the extension-less name on some PATH setups).
    if sys.platform == "win32":
        cmd = ["cmd.exe", "/c", npm_path, "install", "-g", "@google/gemini-cli"]
    else:
        cmd = [npm_path, "install", "-g", "@google/gemini-cli"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line and progress_callback:
                progress_callback(line)

        proc.wait(timeout=300)  # 5 minute hard timeout

        if proc.returncode == 0:
            return True, ""
        else:
            return False, f"npm exited with code {proc.returncode}"

    except subprocess.TimeoutExpired:
        proc.kill()
        return False, "Installation timed out (5 minutes)"
    except FileNotFoundError:
        return False, f"npm not found at: {npm_path}"
    except Exception as e:
        return False, str(e)


# ─── Authentication ───────────────────────────────────────────────────────────

def launch_gemini_auth(gemini_path: str) -> None:
    """
    Open a new terminal window running `gemini` so the user can complete
    Google OAuth sign-in. The terminal stays open after auth completes so
    the user can see success/failure messages before closing it.

    We don't wait for this process — the user will click "Verify Sign-in"
    in Alma after completing auth in the terminal.
    """
    if sys.platform == "win32":
        # Start a new cmd.exe window that runs gemini then stays open
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "cmd.exe", "/k", gemini_path],
            shell=False,
        )
    elif sys.platform == "darwin":
        # Use osascript to open a new Terminal.app window running gemini
        script = f'tell application "Terminal" to do script "{gemini_path}"'
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # Linux fallback — try common terminal emulators
        for terminal in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
            if shutil.which(terminal):
                subprocess.Popen([terminal, "--", "bash", "-c",
                                  f"{gemini_path}; read -p 'Press Enter to close'"])
                return
        # Last resort: run in background (user won't see it)
        subprocess.Popen([gemini_path])


def verify_gemini_auth(gemini_path: str) -> tuple[bool, str]:
    """
    Verify that the Gemini CLI is installed and authenticated.

    Uses --version which exits cleanly without requiring a prompt,
    meaning any exit code 0 indicates the binary is runnable (and
    for the Gemini CLI, auth is cached at this point if it succeeded).

    Returns:
        (success: bool, version_string_or_error: str)
    """
    try:
        result = subprocess.run(
            [gemini_path, "--version"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        output = (result.stdout or result.stderr).strip()
        if result.returncode == 0:
            return True, output or "OK"
        else:
            return False, output or f"Exit code {result.returncode}"
    except FileNotFoundError:
        return False, "Binary not found"
    except subprocess.TimeoutExpired:
        return False, "Timed out (15s)"
    except Exception as e:
        return False, str(e)


# ─── Utilities ────────────────────────────────────────────────────────────────

def get_node_download_url() -> str:
    """Return the Node.js download page URL."""
    return "https://nodejs.org/en/download/"


def get_gemini_cli_package() -> str:
    """Return the npm package name for the Gemini CLI."""
    return "@google/gemini-cli"
