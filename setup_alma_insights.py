"""
Alma Insights — First-Run Setup & Installer
Checks prerequisites, installs dependencies, creates desktop shortcut.

Run with:  python setup_alma_insights.py
"""

import subprocess
import sys
import os
import platform
import shutil
from pathlib import Path


# ═══ Configuration ═══
APP_NAME = "Alma Insights"
APP_VERSION = "1.0.0"
MIN_PYTHON = (3, 10)
REQUIRED_PACKAGES = [
    "PySide6>=6.6.0",
    "pandas>=2.0.0",
    "scikit-learn>=1.3.0",
    "pyyaml>=6.0",
]


def print_header():
    print()
    print("=" * 60)
    print(f"  {APP_NAME} — Setup & Installation")
    print(f"  Version {APP_VERSION}")
    print("=" * 60)
    print()


def check_python():
    """Verify Python version meets minimum requirements."""
    ver = sys.version_info
    print(f"[CHECK] Python version: {ver.major}.{ver.minor}.{ver.micro}")

    if (ver.major, ver.minor) < MIN_PYTHON:
        print(f"[ERROR] Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.")
        print(f"        Current version: {ver.major}.{ver.minor}")
        print()
        print("  Download Python from: https://www.python.org/downloads/")
        print("  Make sure to check 'Add Python to PATH' during installation.")
        return False

    print(f"[  OK ] Python {ver.major}.{ver.minor} meets minimum requirement ({MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)")
    return True


def check_pip():
    """Verify pip is available."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            pip_ver = result.stdout.strip().split()[1]
            print(f"[  OK ] pip {pip_ver} is available")
            return True
    except Exception:
        pass

    print("[ERROR] pip is not available.")
    print("        Run: python -m ensurepip --upgrade")
    return False


def install_dependencies():
    """Install all required Python packages using pip."""
    print()
    print("[SETUP] Installing Python dependencies...")
    print("        (This may take a few minutes on first run)")
    print()

    # Use --user flag to install without admin rights (safe for corporate environments)
    # If running in a venv, --user is not needed/wanted
    in_venv = hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )

    install_args = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if not in_venv:
        install_args.append("--user")

    # Install from requirements.txt if available, otherwise install individually
    req_file = Path(__file__).parent / "requirements.txt"
    if req_file.exists():
        install_args.extend(["-r", str(req_file)])
        cmd_display = f"pip install -r requirements.txt {'(user)' if not in_venv else '(venv)'}"
    else:
        install_args.extend(REQUIRED_PACKAGES)
        cmd_display = f"pip install {' '.join(REQUIRED_PACKAGES)}"

    print(f"  Running: {cmd_display}")
    print()

    try:
        result = subprocess.run(install_args, timeout=300)
        if result.returncode == 0:
            print()
            print("[  OK ] All dependencies installed successfully")
            return True
        else:
            print()
            print("[ERROR] Some dependencies failed to install.")
            print("        Try running manually: pip install -r requirements.txt")
            return False
    except subprocess.TimeoutExpired:
        print("[ERROR] Installation timed out. Check your network connection.")
        return False
    except Exception as e:
        print(f"[ERROR] Installation failed: {e}")
        return False


def verify_pyside6():
    """Verify PySide6 can be imported."""
    try:
        import importlib
        pyside = importlib.import_module("PySide6")
        from PySide6.QtCore import qVersion
        print(f"[  OK ] PySide6 (Qt {qVersion()}) verified")
        return True
    except ImportError:
        print("[ERROR] PySide6 could not be imported after installation.")
        print("        This may be a platform compatibility issue.")
        print("        Try: pip install PySide6 --force-reinstall")
        return False


def create_desktop_shortcut():
    """Create a desktop shortcut to launch the application."""
    app_dir = Path(__file__).parent.resolve()
    main_script = app_dir / "main.py"
    desktop = Path.home() / "Desktop"

    system = platform.system()
    print()
    print("[SETUP] Creating desktop shortcut...")

    if system == "Windows":
        return _create_windows_shortcut(app_dir, main_script, desktop)
    elif system == "Darwin":
        return _create_mac_shortcut(app_dir, main_script, desktop)
    else:
        return _create_linux_shortcut(app_dir, main_script, desktop)


def _create_windows_shortcut(app_dir, main_script, desktop):
    """Create a .bat launcher and optionally a .lnk shortcut on Windows."""
    # Create a .bat launcher (always works, no extra dependencies)
    bat_path = app_dir / "Alma Insights.bat"
    bat_content = f"""@echo off
title Alma Insights
cd /d "{app_dir}"
"{sys.executable}" "{main_script}"
if errorlevel 1 pause
"""
    bat_path.write_text(bat_content)
    print(f"[  OK ] Launcher created: {bat_path}")

    # Copy to desktop
    desktop_bat = desktop / "Alma Insights.bat"
    try:
        shutil.copy2(bat_path, desktop_bat)
        print(f"[  OK ] Desktop shortcut: {desktop_bat}")
    except Exception as e:
        print(f"[ WARN] Could not copy to desktop: {e}")
        print(f"        You can manually copy '{bat_path}' to your desktop.")

    # Try to create a proper .lnk shortcut using PowerShell
    lnk_path = desktop / "Alma Insights.lnk"
    try:
        ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{lnk_path}")
$Shortcut.TargetPath = "{sys.executable}"
$Shortcut.Arguments = '"{main_script}"'
$Shortcut.WorkingDirectory = "{app_dir}"
$Shortcut.Description = "Alma Insights — RCM Issue Analysis"
$Shortcut.Save()
"""
        subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, timeout=10
        )
        if lnk_path.exists():
            print(f"[  OK ] Windows shortcut: {lnk_path}")
            # Remove the .bat from desktop if .lnk succeeded
            if desktop_bat.exists():
                desktop_bat.unlink()
    except Exception:
        print("[ INFO] .lnk shortcut creation skipped (PowerShell unavailable)")
        print(f"        Using .bat launcher on desktop instead.")

    return True


def _create_mac_shortcut(app_dir, main_script, desktop):
    """Create a .command launcher on macOS."""
    command_path = desktop / "Alma Insights.command"
    content = f"""#!/bin/bash
cd "{app_dir}"
"{sys.executable}" "{main_script}"
"""
    command_path.write_text(content)
    os.chmod(command_path, 0o755)
    print(f"[  OK ] Desktop launcher: {command_path}")
    return True


def _create_linux_shortcut(app_dir, main_script, desktop):
    """Create a .desktop launcher on Linux."""
    desktop_file = desktop / "alma-insights.desktop"
    content = f"""[Desktop Entry]
Type=Application
Name=Alma Insights
Comment=RCM Issue Analysis & AI-Assisted Reporting
Exec={sys.executable} {main_script}
Path={app_dir}
Terminal=false
Categories=Office;Development;
"""
    desktop_file.write_text(content)
    os.chmod(desktop_file, 0o755)
    print(f"[  OK ] Desktop launcher: {desktop_file}")
    return True


def run_setup():
    """Main setup sequence."""
    print_header()

    # Step 1: Check Python
    if not check_python():
        print()
        print("Setup cannot continue. Please install the required Python version.")
        input("Press Enter to exit...")
        sys.exit(1)

    # Step 2: Check pip
    if not check_pip():
        print()
        print("Attempting to install pip...")
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"])
        if not check_pip():
            print("Setup cannot continue without pip.")
            input("Press Enter to exit...")
            sys.exit(1)

    # Step 3: Install dependencies
    if not install_dependencies():
        print()
        print("Setup encountered errors during dependency installation.")
        print("You may need to resolve these manually before running the app.")
        input("Press Enter to continue anyway...")

    # Step 4: Verify PySide6
    if not verify_pyside6():
        print()
        print("PySide6 verification failed. The app may not launch correctly.")
        input("Press Enter to continue anyway...")

    # Step 5: Create shortcut
    create_desktop_shortcut()

    # Step 6: Initialize database
    print()
    print("[SETUP] Initializing database...")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from src.data.db_manager import DatabaseManager
        db = DatabaseManager()
        db.initialize()
        db.close()
        print("[  OK ] Database initialized")
    except Exception as e:
        print(f"[ WARN] Database init skipped: {e}")
        print("        Database will be created on first launch.")

    # Done
    print()
    print("=" * 60)
    print(f"  {APP_NAME} setup complete!")
    print()
    print("  To launch:")
    print(f"    python main.py")
    print("    — or use the desktop shortcut")
    print("=" * 60)
    print()

    # Offer to launch
    try:
        response = input("Launch Alma Insights now? (y/n): ").strip().lower()
        if response in ("y", "yes", ""):
            print("Launching...")
            subprocess.Popen([sys.executable, str(Path(__file__).parent / "main.py")])
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    run_setup()
