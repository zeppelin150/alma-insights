"""
Alma Insights — Release Builder
Assembles a standalone distribution zip for Windows or macOS.

Usage:
    python installer/build_release.py --platform windows
    python installer/build_release.py --platform macos
    python installer/build_release.py --platform all

Requirements (build machine only):
    - Python 3.10+
    - Internet access (to download Python embeddable + pip packages)
    - ~2 GB free disk space for staging

Output:
    dist/AlmaInsights-win64.zip
    dist/AlmaInsights-macOS.zip
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


# ═══ Configuration ═══

APP_NAME = "AlmaInsights"

# Single-source version from src/__init__.py
_version_file = Path(__file__).resolve().parent.parent / "src" / "__init__.py"
APP_VERSION = "1.0.0"  # fallback
if _version_file.exists():
    for _line in _version_file.read_text().splitlines():
        if _line.startswith("VERSION"):
            APP_VERSION = _line.split("=", 1)[1].strip().strip("\"'")
            break

# Python version to bundle
PY_VERSION = "3.12.8"
PY_MAJOR_MINOR = "312"  # used in filenames like python312.dll

# python-build-standalone release tag (for macOS)
PBS_RELEASE = "20241219"
PBS_PY_VERSION = "3.12.8"

# Packages installed into the bundled Python.
#
# Exact pins only — this is what actually ships. Must stay in sync with
# requirements.txt. CI enforces parity via tests/test_phase4_build.py.
PACKAGES = [
    "PySide6-Essentials==6.8.1.1",
    "pandas==2.2.3",
    "scikit-learn==1.5.2",
    "nltk==3.9.1",
    "pyyaml==6.0.2",
    "numpy==1.26.4",
    "scipy==1.14.1",
    "vaderSentiment==3.3.2",
    "sentence-transformers==3.3.1",
    "hdbscan==0.8.40",
    "markdown==3.7",
    "keyring==25.5.0",
    "PyJWT[crypto]==2.10.1",
    "google-api-python-client==2.149.0",
    "google-auth==2.36.0",
    "google-auth-oauthlib==1.2.1",
]

# App source files/dirs to include
APP_CONTENTS = [
    "main.py",
    "src",
    "config",
    "assets",
    "migrations",
]

# URLs
WIN_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PY_VERSION}/"
    f"python-{PY_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Node.js standalone version to bundle (LTS)
NODE_VERSION = "22.14.0"
NODE_WIN_URL = (
    f"https://nodejs.org/dist/v{NODE_VERSION}/"
    f"node-v{NODE_VERSION}-win-x64.zip"
)
NODE_MAC_ARM_URL = (
    f"https://nodejs.org/dist/v{NODE_VERSION}/"
    f"node-v{NODE_VERSION}-darwin-arm64.tar.gz"
)
NODE_MAC_X86_URL = (
    f"https://nodejs.org/dist/v{NODE_VERSION}/"
    f"node-v{NODE_VERSION}-darwin-x64.tar.gz"
)

# Gemini CLI npm package — pinned to the bridge-compatible version.
# Bumping requires validating `scan_server/server.js` bridge compatibility.
GEMINI_CLI_PACKAGE = "@google/gemini-cli@0.36.0"

# Sentence-transformers model to pre-download for offline use
ST_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
_LEGACY_MODEL_NAME = "all-MiniLM-L6-v2"  # cleaned up during install

# CPU-only PyTorch index (avoids 2GB CUDA download)
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# python-build-standalone URLs (both architectures for macOS)
PBS_BASE = (
    f"https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PBS_RELEASE}"
)
PBS_MAC_ARM = (
    f"cpython-{PBS_PY_VERSION}+{PBS_RELEASE}-aarch64-apple-darwin-install_only.tar.gz"
)
PBS_MAC_X86 = (
    f"cpython-{PBS_PY_VERSION}+{PBS_RELEASE}-x86_64-apple-darwin-install_only.tar.gz"
)

# ═══ Paths ═══

ROOT = Path(__file__).resolve().parent.parent  # alma-insights/
DIST_DIR = ROOT / "dist"
INSTALLER_DIR = ROOT / "installer"


def log(msg):
    print(f"  {msg}")


def log_step(msg):
    print(f"\n{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}")


def download(url, dest):
    """Download a file with progress indication."""
    filename = url.split("/")[-1]
    log(f"Downloading {filename}...")
    try:
        urllib.request.urlretrieve(url, dest)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        log(f"  Downloaded: {size_mb:.1f} MB")
    except Exception as e:
        print(f"  [ERROR] Download failed: {e}")
        print(f"  URL: {url}")
        sys.exit(1)


def run_cmd(cmd, cwd=None, check=True):
    """Run a command, streaming output."""
    log(f"Running: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0 and check:
        print(f"  [ERROR] Command failed (exit {result.returncode})")
        if result.stdout:
            print(result.stdout[-500:])
        if result.stderr:
            print(result.stderr[-500:])
        sys.exit(1)
    return result


def clean_python_dir(python_dir):
    """Remove unnecessary files from the bundled Python to reduce size."""
    log("Cleaning up unnecessary files...")
    removed = 0

    # Remove __pycache__ directories
    for cache_dir in python_dir.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
            removed += 1

    # Remove .dist-info directories (pip metadata)
    for dist_info in python_dir.rglob("*.dist-info"):
        if dist_info.is_dir():
            shutil.rmtree(dist_info, ignore_errors=True)
            removed += 1

    # Remove test directories from packages
    for test_dir in python_dir.rglob("tests"):
        if test_dir.is_dir() and "site-packages" in str(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
            removed += 1
    for test_dir in python_dir.rglob("test"):
        if test_dir.is_dir() and "site-packages" in str(test_dir):
            shutil.rmtree(test_dir, ignore_errors=True)
            removed += 1

    # Remove pip and its cache (not needed at runtime)
    pip_dir = python_dir / "Lib" / "site-packages" / "pip"
    if pip_dir.exists():
        shutil.rmtree(pip_dir, ignore_errors=True)
        removed += 1
    # Also check unix-style layout
    for sp in python_dir.rglob("site-packages/pip"):
        if sp.is_dir():
            shutil.rmtree(sp, ignore_errors=True)
            removed += 1

    # Remove .pyc files outside __pycache__ (shouldn't exist but just in case)
    for pyc in python_dir.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)

    log(f"  Removed {removed} unnecessary directories")


def copy_app_source(staging_dir):
    """Copy the application source into the staging directory."""
    app_dir = staging_dir / "app"
    app_dir.mkdir(exist_ok=True)

    for item_name in APP_CONTENTS:
        src = ROOT / item_name
        dst = app_dir / item_name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", ".git", ".env",
                "*.db", "exports", "*.log"
            ))
        elif src.is_file():
            shutil.copy2(src, dst)
        else:
            log(f"  [WARN] Source not found, skipping: {item_name}")

    # Ensure data/ directory exists (empty, for runtime)
    (app_dir / "data").mkdir(exist_ok=True)

    # Inject the bundled release-update token. See _inject_release_credentials
    # for the full contract — short version: read ALMA_RELEASE_BOT_PAT from
    # the build environment, obfuscate, and write next to the auto-updater
    # so end users never have to paste a PAT to receive updates.
    _inject_release_credentials(app_dir)

    # Inject the bundled Google OAuth desktop client (client_id/secret) the
    # same way — obfuscated, env-driven, absent-when-unset. Lets users do the
    # "Connect my Google account" flow without provisioning their own GCP
    # client; an admin can still override it in Settings.
    _inject_google_oauth_client(app_dir)

    log(f"  App source copied to {app_dir}")


def _inject_release_credentials(app_dir):
    """Write `src/updater/_release_credentials.py` from a CI env var.

    Read ``ALMA_RELEASE_BOT_PAT`` from the build environment, obfuscate
    it via :mod:`src.updater._token_obfuscation`, and emit a tiny Python
    module containing the obfuscated payload + key. The runtime loader
    at :mod:`src.updater._bundled_token` reads this file and provides
    the token to :func:`update_checker._load_token` as the lowest-
    priority fallback (admin-set keyring tokens still win).

    Behavior matrix
    ---------------

    * Env var present + non-empty  → write the credentials file.
    * Env var present but empty    → fail the build. We never want to
      ship a build that "looks fine" but silently has auto-update
      disabled because someone forgot to set the secret.
    * Env var absent (e.g. dev who runs the script locally without the
      secret) → log a warning and skip. The resulting bundle has no
      bundled token, and end-user installs fall back to the existing
      keyring path. Safe default for `python installer/build_release.py`
      run by hand.

    The obfuscation primitives (`obfuscate` / `generate_key`) are pure
    and import-cheap, so we can call them from the build script
    without spinning up the full app.
    """
    pat = os.environ.get("ALMA_RELEASE_BOT_PAT", "")
    out_path = app_dir / "src" / "updater" / "_release_credentials.py"

    if pat == "":
        if "ALMA_RELEASE_BOT_PAT" in os.environ:
            # Set but empty — that's a CI mistake we should refuse to ship.
            raise RuntimeError(
                "ALMA_RELEASE_BOT_PAT is set but empty. Refusing to build a "
                "release bundle with auto-update auth disabled. Either set "
                "the secret or unset the variable entirely (which produces "
                "a bundle without a bundled token — keyring fallback only)."
            )
        log("  [WARN] ALMA_RELEASE_BOT_PAT not set — bundle will rely on "
            "user-supplied keyring tokens for auto-update auth.")
        return

    # Make sure src/updater/ exists in the staged copy. It always should
    # (we just copied the source tree), but be defensive.
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Pull the obfuscation helpers from the source we just copied. We
    # intentionally import from the staged copy rather than `src.*` so
    # the build script can run from any working directory.
    helper_path = out_path.parent / "_token_obfuscation.py"
    if not helper_path.is_file():
        raise RuntimeError(
            f"Could not find {helper_path} in the staged source. "
            "Did APP_CONTENTS get reorganized?"
        )

    # Tiny isolated module-load — avoids polluting sys.modules.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_token_obfuscation", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {helper_path}")
    obf_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obf_mod)

    key = obf_mod.generate_key()
    payload = obf_mod.obfuscate(pat, key)
    fp = obf_mod.fingerprint(pat)

    contents = (
        '"""\n'
        "Build-injected release update credentials.\n"
        "\n"
        "Generated by installer/build_release.py from the CI secret\n"
        "ALMA_RELEASE_BOT_PAT. Do NOT commit this file. The on-disk\n"
        "value is obfuscated to defeat passive discovery; the actual\n"
        "security boundary is the GitHub permissions on the underlying\n"
        "PAT (Contents: Read-only on a single repo).\n"
        '"""\n'
        "from __future__ import annotations\n"
        "\n"
        "SCHEMA = 1\n"
        f"BUILD_VERSION = {APP_VERSION!r}\n"
        f"TOKEN_KEY = {key!r}\n"
        f"TOKEN_PAYLOAD = {payload!r}\n"
        f"TOKEN_FINGERPRINT = {fp!r}\n"
    )
    out_path.write_text(contents, encoding="utf-8")
    log(f"  Bundled release token injected ({fp}) -> "
        f"{out_path.relative_to(app_dir)}")


def _inject_google_oauth_client(app_dir):
    """Write `src/data/_google_oauth_client.py` from a CI env var.

    Read ``ALMA_GOOGLE_OAUTH_CLIENT`` (the JSON string of a Google
    "Desktop app" OAuth client — the ``{"installed": {...}}`` object) from
    the build environment, obfuscate the WHOLE JSON via
    :mod:`src.updater._token_obfuscation`, and emit a tiny module the
    runtime loader (`src.data.google_oauth._load_bundled_client`) reads.

    Behavior matrix mirrors :func:`_inject_release_credentials`:

    * Env var present + non-empty  → write the client module.
    * Env var present but empty    → fail the build (never ship a
      half-configured OAuth bundle).
    * Env var absent               → log a warning and skip. The bundle
      then has no built-in client; the "Connect my Google account" UI
      tells the user to supply their own GCP client in Settings.

    Obfuscating the whole JSON keeps ``client_secret`` out of plaintext so
    `audit_secrets.py` and naive `strings(1)` see only base64. For a
    desktop "installed app" client the secret is not truly confidential
    anyway — PKCE is the real protection (see google_oauth.py).
    """
    client_json = os.environ.get("ALMA_GOOGLE_OAUTH_CLIENT", "")
    out_path = app_dir / "src" / "data" / "_google_oauth_client.py"

    if client_json.strip() == "":
        if "ALMA_GOOGLE_OAUTH_CLIENT" in os.environ:
            raise RuntimeError(
                "ALMA_GOOGLE_OAUTH_CLIENT is set but empty/blank. Refusing to "
                "build with a half-configured OAuth client. Either set valid "
                "JSON or unset the variable entirely (bundle ships without a "
                "built-in client; users supply their own GCP client in Settings)."
            )
        log("  [WARN] ALMA_GOOGLE_OAUTH_CLIENT not set — bundle ships without "
            "a built-in Google OAuth client (admin-supplied client only).")
        return

    # Validate the value is a real Google desktop "installed" client BEFORE
    # writing — a whitespace/garbage value would otherwise pass the empty
    # guard, obfuscate to junk, and ship a bundle that "looks fine" but whose
    # Connect button is silently dead (decode → None at runtime).
    import json as _json
    try:
        parsed = _json.loads(client_json)
        installed = parsed["installed"]
        client_id = installed["client_id"]
        assert installed.get("client_secret"), "missing client_secret"
        assert client_id, "missing client_id"
    except Exception as exc:
        raise RuntimeError(
            "ALMA_GOOGLE_OAUTH_CLIENT is not a valid Google desktop "
            f"'installed' client JSON ({exc}). A 'web' client or malformed "
            "value is rejected so we never ship a broken/confidential client."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Reuse the release-token obfuscation helpers from the staged copy.
    helper_path = app_dir / "src" / "updater" / "_token_obfuscation.py"
    if not helper_path.is_file():
        raise RuntimeError(
            f"Could not find {helper_path} in the staged source. "
            "Did APP_CONTENTS get reorganized?"
        )
    import importlib.util
    spec = importlib.util.spec_from_file_location("_token_obfuscation", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {helper_path}")
    obf_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obf_mod)

    # client_id already validated above; fingerprint it (non-secret) for a
    # safe-to-log build line.
    key = obf_mod.generate_key()
    payload = obf_mod.obfuscate(client_json, key)
    fp = obf_mod.fingerprint(client_id)

    contents = (
        '"""\n'
        "Build-injected Google OAuth desktop client.\n"
        "\n"
        "Generated by installer/build_release.py from the CI secret\n"
        "ALMA_GOOGLE_OAUTH_CLIENT. Do NOT commit this file. The on-disk\n"
        "value is obfuscated to defeat passive discovery; for a desktop\n"
        "'installed app' client the secret is not confidential and PKCE\n"
        "is the real protection.\n"
        '"""\n'
        "from __future__ import annotations\n"
        "\n"
        "SCHEMA = 1\n"
        f"BUILD_VERSION = {APP_VERSION!r}\n"
        f"CLIENT_KEY = {key!r}\n"
        f"CLIENT_PAYLOAD = {payload!r}\n"
        f"CLIENT_FINGERPRINT = {fp!r}\n"
    )
    out_path.write_text(contents, encoding="utf-8")
    log(f"  Bundled Google OAuth client injected ({fp}) -> "
        f"{out_path.relative_to(app_dir)}")


def copy_installer_files(staging_dir, plat):
    """Copy the install + uninstall scripts and entry-point wrappers."""
    installer_dest = staging_dir / "_installer"
    installer_dest.mkdir(exist_ok=True)

    # install.py + uninstall.py both live in _installer/
    shutil.copy2(INSTALLER_DIR / "install.py", installer_dest / "install.py")
    shutil.copy2(INSTALLER_DIR / "uninstall.py", installer_dest / "uninstall.py")

    # Platform-specific top-level wrappers
    if plat == "windows":
        shutil.copy2(
            INSTALLER_DIR / "install_win.bat",
            staging_dir / "Install Alma Insights.bat",
        )
        shutil.copy2(
            INSTALLER_DIR / "launcher_win.bat",
            staging_dir / "AlmaInsights.bat",
        )
        shutil.copy2(
            INSTALLER_DIR / "uninstall_win.bat",
            staging_dir / "Uninstall Alma Insights.bat",
        )
    else:
        shutil.copy2(
            INSTALLER_DIR / "install_mac.command",
            staging_dir / "Install Alma Insights.command",
        )
        shutil.copy2(
            INSTALLER_DIR / "launcher_mac.command",
            staging_dir / "AlmaInsights.command",
        )
        shutil.copy2(
            INSTALLER_DIR / "uninstall_mac.command",
            staging_dir / "Uninstall Alma Insights.command",
        )
        # Ensure executable permissions
        for name in ("Install Alma Insights.command",
                     "AlmaInsights.command",
                     "Uninstall Alma Insights.command"):
            os.chmod(staging_dir / name, 0o755)

    log("  Installer + uninstall files copied")


def download_node_windows(staging_dir, tmp):
    """Download and extract standalone Node.js for Windows."""
    log_step("Download Node.js (Windows)")
    node_zip = tmp / "node-win.zip"
    download(NODE_WIN_URL, node_zip)

    node_dir = staging_dir / "node"
    with zipfile.ZipFile(node_zip) as zf:
        zf.extractall(tmp)

    # Node.js extracts to node-vX.Y.Z-win-x64/ — flatten into staging/node/
    extracted = tmp / f"node-v{NODE_VERSION}-win-x64"
    if extracted.exists():
        shutil.move(str(extracted), str(node_dir))
    else:
        # Fallback: find the extracted directory
        for candidate in tmp.iterdir():
            if candidate.is_dir() and candidate.name.startswith("node-v"):
                shutil.move(str(candidate), str(node_dir))
                break
        else:
            log("  [ERROR] Could not find extracted Node.js directory")
            sys.exit(1)

    log(f"  Extracted to {node_dir}")
    return node_dir


def download_node_macos(staging_dir, tmp):
    """Download and extract standalone Node.js for macOS."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        url = NODE_MAC_ARM_URL
        arch = "arm64"
    else:
        url = NODE_MAC_X86_URL
        arch = "x64"

    log_step(f"Download Node.js (macOS {arch})")
    node_tar = tmp / "node-mac.tar.gz"
    download(url, node_tar)

    with tarfile.open(node_tar, "r:gz") as tf:
        tf.extractall(tmp)

    extracted = tmp / f"node-v{NODE_VERSION}-darwin-{arch}"
    node_dir = staging_dir / "node"
    if extracted.exists():
        shutil.move(str(extracted), str(node_dir))
    else:
        for candidate in tmp.iterdir():
            if candidate.is_dir() and candidate.name.startswith("node-v"):
                shutil.move(str(candidate), str(node_dir))
                break
        else:
            log("  [ERROR] Could not find extracted Node.js directory")
            sys.exit(1)

    log(f"  Extracted to {node_dir}")
    return node_dir


def install_gemini_cli(node_dir):
    """Install Gemini CLI into the bundled Node.js prefix."""
    log_step("Install Gemini CLI")

    if platform.system() == "Windows":
        npm_cmd = str(node_dir / "npm.cmd")
        if not Path(npm_cmd).exists():
            npm_cmd = str(node_dir / "npm")
    else:
        npm_cmd = str(node_dir / "bin" / "npm")

    cmd = [npm_cmd, "install", "-g", GEMINI_CLI_PACKAGE,
           "--prefix", str(node_dir)]

    # On Windows, npm is a .cmd script and needs cmd.exe
    if platform.system() == "Windows":
        cmd = ["cmd.exe", "/c"] + cmd

    result = run_cmd(cmd, check=False)
    if result.returncode == 0:
        log("  Gemini CLI installed successfully")
        return

    # Phase 4: fail-fast — a bundle missing Gemini CLI is non-shippable.
    # The CLI owns Gemini OAuth at runtime; if it's absent the startup
    # splash's Check 4 will block the app from launching.
    log("  [ERROR] Gemini CLI installation failed.")
    if result.stderr:
        log(f"  stderr: {result.stderr[:600]}")
    if result.stdout:
        log(f"  stdout: {result.stdout[:600]}")
    log("  This is a build blocker — the bundled Node.js must be able to")
    log("  install @google/gemini-cli at build time. Check npm registry")
    log("  reachability and the pinned version in GEMINI_CLI_PACKAGE.")
    sys.exit(1)


def download_st_model(python_exe, staging_dir):
    """Pre-download sentence-transformers model for offline use.

    Uses huggingface_hub.snapshot_download with local_dir to produce
    a clean directory layout (no symlinks, no blobs/refs/snapshots
    duplication that cache_folder creates on Windows).
    """
    log_step("Pre-download ML model (sentence-transformers)")

    # Clean directory name for the local model
    model_local_name = ST_MODEL_NAME.split("/")[-1]
    model_dir = staging_dir / "app" / "data" / "models"
    model_path = model_dir / model_local_name
    model_dir.mkdir(parents=True, exist_ok=True)

    result = run_cmd(
        [str(python_exe), "-c",
         f"from huggingface_hub import snapshot_download; "
         f"snapshot_download('{ST_MODEL_NAME}', "
         f"local_dir=r'{model_path}'); "
         f"from sentence_transformers import SentenceTransformer; "
         f"SentenceTransformer(r'{model_path}'); "
         f"print('Model downloaded and verified OK')"],
        check=False,
    )

    if result.returncode == 0:
        model_size = _dir_size_mb(model_dir)
        log(f"  Model pre-downloaded for offline use ({model_size:.0f} MB)")
        # Clean up legacy MiniLM model if present
        _cleanup_legacy_model(model_dir)
    else:
        log("  [WARN] Model download failed — will download on first use")
        if result.stderr:
            log(f"  {result.stderr[:300]}")


def _dir_size_mb(path: Path) -> float:
    """Calculate total size of a directory in MB."""
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def _cleanup_legacy_model(model_dir: Path):
    """Remove old MiniLM model files if present."""
    for d in model_dir.iterdir():
        if d.is_dir() and _LEGACY_MODEL_NAME in d.name:
            log(f"  Cleaning up legacy model: {d.name}")
            shutil.rmtree(d, ignore_errors=True)


def write_sbom(python_exe, staging_dir):
    """Write an SBOM (`pip list --format=json`) into app/sbom.json.

    The SBOM lists every pip-installed package in the bundled Python,
    with exact versions. It's embedded so any installed bundle can be
    audited in isolation (`cat app/sbom.json`) without running pip.
    """
    log_step("Write SBOM")
    sbom_path = staging_dir / "app" / "sbom.json"
    result = run_cmd(
        [str(python_exe), "-m", "pip", "list", "--format", "json",
         "--disable-pip-version-check"],
        check=False,
    )
    if result.returncode != 0:
        log(f"  [ERROR] pip list failed: {result.stderr[:300]}")
        sys.exit(1)

    sbom_path.parent.mkdir(parents=True, exist_ok=True)
    sbom_doc = {
        "schema_version": 1,
        "generated_by": "installer/build_release.py",
        "app": APP_NAME,
        "python_version": PY_VERSION,
        "packages": json.loads(result.stdout or "[]"),
    }
    sbom_path.write_text(json.dumps(sbom_doc, indent=2), encoding="utf-8")
    log(f"  Wrote {sbom_path} ({len(sbom_doc['packages'])} packages)")


def write_checksums(staging_dir):
    """Write SHA-256 for every app/**/*.py file into app/checksums.json.

    Lets the startup splash (Phase 2 Check 1) detect tampering — a
    single modified file will fail the hash comparison on launch.
    Skips bytecode and the sbom/checksums files themselves.
    """
    log_step("Write app/checksums.json")
    import hashlib

    app_dir = staging_dir / "app"
    checksums: dict[str, str] = {}
    skipped = 0

    for f in sorted(app_dir.rglob("*.py")):
        # Skip anything that shouldn't be hashed:
        #   - bytecode caches
        #   - data/ which is runtime-mutable
        rel = f.relative_to(app_dir).as_posix()
        if "__pycache__" in rel or rel.startswith("data/"):
            skipped += 1
            continue
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        checksums[rel] = h.hexdigest()

    out = {
        "schema_version": 1,
        "generated_by": "installer/build_release.py",
        "root": "app",
        "algorithm": "sha256",
        "files": checksums,
    }
    dest = app_dir / "checksums.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log(f"  Wrote {dest} ({len(checksums)} files, skipped {skipped})")


def verify_manifest(staging_dir):
    """Verify all required files/dirs exist before creating the zip."""
    log_step("Verify build manifest")

    system = platform.system()
    if system == "Windows":
        python_bin = "python/python.exe"
        node_bin = "node/node.exe"
    else:
        python_bin = "python/bin/python3"
        node_bin = "node/bin/node"

    required = [
        python_bin,
        node_bin,
        "app/main.py",
        "app/src/__init__.py",
        "app/config",
        "app/migrations",
        "app/assets",
        "_installer/install.py",
    ]

    missing = []
    for rel_path in required:
        full = staging_dir / rel_path
        if not full.exists():
            missing.append(rel_path)

    if missing:
        log("  [ERROR] Missing required paths in build:")
        for m in missing:
            log(f"    - {m}")
        sys.exit(1)

    log(f"  All {len(required)} required paths verified")


def create_zip(staging_dir, output_path):
    """Create a zip archive from the staging directory."""
    log_step(f"Creating {output_path.name}")

    # Calculate total size
    total_size = sum(
        f.stat().st_size for f in staging_dir.rglob("*") if f.is_file()
    )
    log(f"  Total on-disk size: {total_size / (1024 * 1024):.1f} MB")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(staging_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(staging_dir.parent)
                zf.write(file_path, arcname)

    zip_size = os.path.getsize(output_path) / (1024 * 1024)
    log(f"  Zip size: {zip_size:.1f} MB")
    log(f"  Output: {output_path}")


# ═══════════════════════════════════════════
#  WINDOWS BUILD
# ═══════════════════════════════════════════

def build_windows():
    log_step("Building Windows distribution")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        staging = tmp / APP_NAME
        staging.mkdir()
        python_dir = staging / "python"

        # 1. Download and extract Python embeddable
        log_step("Step 1: Download Python embeddable")
        embed_zip = tmp / "python-embed.zip"
        download(WIN_EMBED_URL, embed_zip)

        python_dir.mkdir()
        with zipfile.ZipFile(embed_zip) as zf:
            zf.extractall(python_dir)
        log(f"  Extracted to {python_dir}")

        # 2. Patch ._pth file to enable site-packages and pip
        log_step("Step 2: Configure Python path")
        pth_file = python_dir / f"python{PY_MAJOR_MINOR}._pth"
        if pth_file.exists():
            pth_content = (
                f"python{PY_MAJOR_MINOR}.zip\n"
                ".\n"
                ".\\Lib\\site-packages\n"
                "import site\n"
            )
            pth_file.write_text(pth_content)
            log(f"  Patched {pth_file.name}")
        else:
            log(f"  [WARN] {pth_file.name} not found — listing contents:")
            for f in python_dir.iterdir():
                log(f"    {f.name}")

        # 3. Bootstrap pip
        log_step("Step 3: Bootstrap pip")
        get_pip = tmp / "get-pip.py"
        download(GET_PIP_URL, get_pip)

        python_exe = python_dir / "python.exe"
        run_cmd([str(python_exe), str(get_pip), "--no-warn-script-location"])

        # Create Lib/site-packages if it doesn't exist
        site_packages = python_dir / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)

        # 4a. Install CPU-only PyTorch (avoids 2GB CUDA download)
        log_step("Step 4a: Install CPU-only PyTorch")
        torch_cmd = [
            str(python_exe), "-m", "pip", "install",
            "--no-warn-script-location",
            "--disable-pip-version-check",
            "--index-url", TORCH_CPU_INDEX,
            "torch",
        ]
        result = run_cmd(torch_cmd, check=False)
        if result.returncode == 0:
            log("  CPU-only PyTorch installed")
        else:
            log("  [WARN] CPU-only torch install failed — sentence-transformers may pull CUDA version")

        # 4b. Install packages
        log_step("Step 4b: Install dependencies")
        pip_cmd = [
            str(python_exe), "-m", "pip", "install",
            "--no-warn-script-location",
            "--disable-pip-version-check",
        ]
        for pkg in PACKAGES:
            pip_cmd.append(pkg)
        run_cmd(pip_cmd)

        # 5. Verify PySide6
        log_step("Step 5: Verify PySide6")
        result = run_cmd(
            [str(python_exe), "-c", "from PySide6.QtWidgets import QApplication; print('PySide6 OK')"],
            check=False,
        )
        if result.returncode == 0:
            log("  PySide6 verified successfully")
        else:
            log("  [WARN] PySide6 import check failed — build may still work")
            if result.stderr:
                log(f"  {result.stderr[:200]}")

        # 6. Pre-download sentence-transformers model
        download_st_model(python_exe, staging)

        # SBOM must run before cleanup — clean_python_dir() strips pip below,
        # and write_sbom() shells out to `python -m pip list`.
        write_sbom(python_exe, staging)

        # 7. Clean up
        log_step("Step 7: Clean up bundled Python")
        clean_python_dir(python_dir)

        # 8. Copy app source
        log_step("Step 8: Copy application source")
        copy_app_source(staging)

        # 9. Download and bundle Node.js
        node_dir = download_node_windows(staging, tmp)

        # 10. Install Gemini CLI into bundled Node.js
        install_gemini_cli(node_dir)

        # 11. Copy installer files
        log_step("Step 11: Copy installer files")
        copy_installer_files(staging, "windows")

        # 12. Integrity checksums (SBOM was written above, before cleanup)
        write_checksums(staging)

        # 13. Verify build manifest
        verify_manifest(staging)

        # 14. Create zip
        DIST_DIR.mkdir(exist_ok=True)
        output = DIST_DIR / f"{APP_NAME}-win64.zip"
        create_zip(staging, output)

    return output


# ═══════════════════════════════════════════
#  MACOS BUILD
# ═══════════════════════════════════════════

def build_macos():
    log_step("Building macOS distribution")

    # Determine architecture
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        pbs_filename = PBS_MAC_ARM
        arch_label = "arm64"
    else:
        pbs_filename = PBS_MAC_X86
        arch_label = "x86_64"

    pbs_url = f"{PBS_BASE}/{pbs_filename}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        staging = tmp / APP_NAME
        staging.mkdir()

        # 1. Download python-build-standalone
        log_step(f"Step 1: Download Python ({arch_label})")
        pbs_archive = tmp / pbs_filename
        download(pbs_url, pbs_archive)

        # 2. Extract
        log_step("Step 2: Extract Python")
        with tarfile.open(pbs_archive, "r:gz") as tf:
            tf.extractall(tmp)

        # python-build-standalone extracts to a "python" directory
        extracted = tmp / "python"
        if not extracted.exists():
            # Some releases extract to "install" instead
            for candidate in tmp.iterdir():
                if candidate.is_dir() and candidate.name not in (APP_NAME,):
                    extracted = candidate
                    break

        python_dir = staging / "python"
        shutil.move(str(extracted), str(python_dir))
        log(f"  Extracted to {python_dir}")

        # 3. Install packages
        log_step("Step 3: Install dependencies")
        python_exe = python_dir / "bin" / "python3"
        if not python_exe.exists():
            python_exe = python_dir / "bin" / "python"

        pip_cmd = [
            str(python_exe), "-m", "pip", "install",
            "--no-warn-script-location",
            "--disable-pip-version-check",
        ]
        for pkg in PACKAGES:
            pip_cmd.append(pkg)
        run_cmd(pip_cmd)

        # 4. Verify PySide6
        log_step("Step 4: Verify PySide6")
        result = run_cmd(
            [str(python_exe), "-c", "from PySide6.QtWidgets import QApplication; print('PySide6 OK')"],
            check=False,
        )
        if result.returncode == 0:
            log("  PySide6 verified successfully")
        else:
            log("  [WARN] PySide6 import check failed — build may still work")

        # 5. Pre-download sentence-transformers model
        download_st_model(python_exe, staging)

        # SBOM must run before cleanup — clean_python_dir() strips pip below,
        # and write_sbom() shells out to `python -m pip list`.
        write_sbom(python_exe, staging)

        # 6. Clean up
        log_step("Step 6: Clean up bundled Python")
        clean_python_dir(python_dir)

        # 7. Copy app source
        log_step("Step 7: Copy application source")
        copy_app_source(staging)

        # 8. Download and bundle Node.js
        node_dir = download_node_macos(staging, tmp)

        # 9. Install Gemini CLI into bundled Node.js
        install_gemini_cli(node_dir)

        # 10. Copy installer files
        log_step("Step 10: Copy installer files")
        copy_installer_files(staging, "macos")

        # 11. Integrity checksums (SBOM was written above, before cleanup)
        write_checksums(staging)

        # 12. Verify build manifest
        verify_manifest(staging)

        # 13. Create zip
        DIST_DIR.mkdir(exist_ok=True)
        output = DIST_DIR / f"{APP_NAME}-macOS-{arch_label}.zip"
        create_zip(staging, output)

    return output


# ═══════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description=f"Build {APP_NAME} release packages")
    parser.add_argument(
        "--platform",
        choices=["windows", "macos", "all"],
        default=None,
        help="Target platform (default: auto-detect current OS)",
    )
    args = parser.parse_args()

    plat = args.platform
    if plat is None:
        # Auto-detect
        system = platform.system()
        if system == "Windows":
            plat = "windows"
        elif system == "Darwin":
            plat = "macos"
        else:
            print(f"Unsupported platform: {system}")
            print("Use --platform windows or --platform macos")
            sys.exit(1)

    print()
    print(f"  Alma Insights Release Builder v{APP_VERSION}")
    print(f"  Platform: {plat}")
    print(f"  Python to bundle: {PY_VERSION}")
    print()

    outputs = []

    if plat in ("windows", "all"):
        outputs.append(build_windows())

    if plat in ("macos", "all"):
        outputs.append(build_macos())

    # Summary
    print()
    print("=" * 60)
    print("  Build complete!")
    print()
    for out in outputs:
        size_mb = os.path.getsize(out) / (1024 * 1024)
        print(f"  {out.name}  ({size_mb:.1f} MB)")
    print()
    print(f"  Output directory: {DIST_DIR}")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
