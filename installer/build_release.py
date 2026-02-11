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
APP_VERSION = "1.0.0"

# Python version to bundle
PY_VERSION = "3.12.8"
PY_MAJOR_MINOR = "312"  # used in filenames like python312.dll

# python-build-standalone release tag (for macOS)
PBS_RELEASE = "20241219"
PBS_PY_VERSION = "3.12.8"

# Packages to install into the bundled Python
PACKAGES = [
    "PySide6-Essentials>=6.6.0",
    "pandas>=2.0.0",
    "scikit-learn>=1.3.0",
    "nltk>=3.8.0",
    "pyyaml>=6.0",
]

# App source files/dirs to include
APP_CONTENTS = [
    "main.py",
    "src",
    "config",
    "assets",
]

# URLs
WIN_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PY_VERSION}/"
    f"python-{PY_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

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

    log(f"  App source copied to {app_dir}")


def copy_installer_files(staging_dir, plat):
    """Copy the install script and entry point wrappers."""
    installer_dest = staging_dir / "_installer"
    installer_dest.mkdir(exist_ok=True)

    # Copy install.py
    shutil.copy2(INSTALLER_DIR / "install.py", installer_dest / "install.py")

    # Copy platform-specific entry points
    if plat == "windows":
        shutil.copy2(
            INSTALLER_DIR / "install_win.bat",
            staging_dir / "Install Alma Insights.bat",
        )
        shutil.copy2(
            INSTALLER_DIR / "launcher_win.bat",
            staging_dir / "AlmaInsights.bat",
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
        # Ensure executable permissions
        os.chmod(staging_dir / "Install Alma Insights.command", 0o755)
        os.chmod(staging_dir / "AlmaInsights.command", 0o755)

    log("  Installer files copied")


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

        # 4. Install packages
        log_step("Step 4: Install dependencies")
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

        # 6. Clean up
        log_step("Step 6: Clean up bundled Python")
        clean_python_dir(python_dir)

        # 7. Copy app source
        log_step("Step 7: Copy application source")
        copy_app_source(staging)

        # 8. Copy installer files
        log_step("Step 8: Copy installer files")
        copy_installer_files(staging, "windows")

        # 9. Create zip
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

        # 5. Clean up
        log_step("Step 5: Clean up bundled Python")
        clean_python_dir(python_dir)

        # 6. Copy app source
        log_step("Step 6: Copy application source")
        copy_app_source(staging)

        # 7. Copy installer files
        log_step("Step 7: Copy installer files")
        copy_installer_files(staging, "macos")

        # 8. Create zip
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
